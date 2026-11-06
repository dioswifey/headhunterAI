"""기존 공고 가져오기 (PRD v0.8 JOB-09) — 기업이 이미 쓴 공고 본문(붙여넣기) 또는 링크를 공고 입력 칸으로 나눈다.

원칙: 공고 내용은 기업이 정한다. 이 기능은 **원문을 칸에 옮겨 담을 뿐 내용을 지어내지 않는다.**
    ① 규칙 분리: '주요 업무 / 자격 요건 / 우대 사항 / 회사 소개 / 근무지' 같은 제목 줄로 나눈다(모델 호출 없음, 빠름)
    ② 규칙으로 업무·자격 칸을 못 찾으면 로컬 모델(`llm.chat_json`)에 '원문 문장을 그대로 옮겨 담기'를 시킨다
       → 결과의 각 줄이 원문에 있는지 코드가 대조하고, 원문에 없는 줄은 버린다
    ③ 링크: http/https만, 사설·내부 주소 차단(SSRF 방지), 10초·1MB 제한. 화면이 자바스크립트로만 그려지는 사이트는
       본문이 비어 있을 수 있다 → '본문을 붙여 넣어 주세요'
담당자가 결과를 검토·수정한 뒤 저장·게시한다(JOB-10).
"""
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

from . import llm

FIELDS = ("title", "company_intro", "duties", "requirements", "preferred", "location")
HEADINGS = [
    ("duties", r"주요\s*업무|담당\s*업무|업무\s*내용|하는\s*일|이런\s*일을\s*해요|what\s+you('|’)?ll\s+do|responsibilities"),
    ("requirements", r"자격\s*요건|지원\s*자격|필수\s*(요건|조건|역량)|이런\s*분을\s*찾아요|requirements|qualifications"),
    ("preferred", r"우대\s*(사항|조건|요건)|이런\s*분이면\s*더\s*좋아요|preferred|nice\s+to\s+have|plus"),
    ("company_intro", r"회사\s*소개|기업\s*소개|팀\s*소개|about\s+(us|the\s+company)"),
    ("_benefits", r"복지|혜택|채용\s*절차|전형\s*절차|근무\s*조건|기타|benefits|hiring\s+process"),
]
LOCATION = re.compile(r"(근무\s*(지|위치|장소)|location)\s*[:：]\s*(.+)", re.I)
MAX_TEXT = 20000


def _is_heading(line):
    s = re.sub(r"^[\s#\-*•·■□▶▷◆◇【\[(]+|[\s:：\]】)]+$", "", line).strip()
    if not s or len(s) > 40:
        return None
    for key, rx in HEADINGS:
        if re.fullmatch(rf"(?i)(?:{rx})", s):
            return key
    return None


def _bullets(lines):
    out = []
    for ln in lines:
        s = re.sub(r"^[\s\-*•·▶▷◆◇○●■□✔✓]+|^\d+[.)]\s*", "", ln).strip()
        if s:
            out.append(f"- {s}")
    return "\n".join(out)


def rule_split(text):
    lines = [l.rstrip() for l in text.replace("\r", "").split("\n")]
    out = {k: "" for k in FIELDS}
    sections, cur, head = {}, None, []
    for ln in lines:
        m = LOCATION.search(ln)
        if m:
            if not out["location"]:
                out["location"] = m.group(3).strip()[:80]
            continue                                   # 근무지 줄은 다른 칸에 넣지 않는다
        key = _is_heading(ln)
        if key:
            cur = key
            sections.setdefault(cur, [])
            continue
        if cur:
            sections[cur].append(ln)
        elif ln.strip():
            head.append(ln.strip())
    if head and len(head[0]) <= 60:
        out["title"] = head[0]
        head = head[1:]
    out["company_intro"] = "\n".join(sections.get("company_intro") or head).strip()[:2000]
    for k in ("duties", "requirements", "preferred"):
        out[k] = _bullets(sections.get(k, []))[:3000]
    return out


SCHEMA = {"type": "object", "properties": {k: {"type": "string"} for k in FIELDS}, "required": list(FIELDS)}
SYSTEM = ("너는 채용 공고 원문을 입력 칸으로 옮겨 담는 도우미다. 원문에 있는 문장만 그대로 옮긴다. "
          "새 내용·조건·수치를 지어내거나 바꾸거나 요약하지 않는다. 원문에 없는 칸은 빈 문자열로 둔다. "
          "duties·requirements·preferred는 원문 항목을 줄마다 '- '로 시작해 담는다.")


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def llm_split(text):
    out = llm.chat_json(SYSTEM, text[:8000], SCHEMA, timeout=180, temperature=0.0)
    src, dropped = _norm(text), 0
    for k in ("duties", "requirements", "preferred"):
        keep = []
        for ln in (out.get(k) or "").split("\n"):
            body = re.sub(r"^\s*-\s*", "", ln).strip()
            if not body:
                continue
            if _norm(body) in src:                          # 원문 대조: 원문에 없는 줄은 버린다
                keep.append(f"- {body}")
            else:
                dropped += 1
        out[k] = "\n".join(keep)
    for k in ("title", "company_intro", "location"):
        if out.get(k) and _norm(out[k]) not in src:
            out[k], dropped = "", dropped + 1
    return out, dropped


def _safe_url(url):
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("http(s) 주소만 가져올 수 있습니다.")
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("내부·사설 주소는 가져올 수 없습니다.")
    return u.geturl()


def fetch_text(url):
    from bs4 import BeautifulSoup
    url = _safe_url(url)
    with httpx.Client(timeout=10, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (compatible; headhunterAI-job-import/0.1)"}) as c:
        r = c.get(url)
        hops = 0
        while r.is_redirect and hops < 3:                   # 리다이렉트도 매번 주소를 다시 검사
            url = _safe_url(str(r.next_request.url))
            r = c.get(url)
            hops += 1
        r.raise_for_status()
        raw = r.content[:1_000_000].decode(r.encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        t.decompose()
    return soup.get_text("\n", strip=True)[:MAX_TEXT]


def import_job(text="", url=""):
    """반환: {fields, method: rule|ai, source: paste|url, warnings[]}"""
    warnings, source = [], "paste"
    if url and not text.strip():
        source = "url"
        try:
            text = fetch_text(url)
        except Exception as e:
            raise ValueError(f"링크에서 공고를 가져오지 못했습니다({type(e).__name__}). 공고 본문을 복사해 붙여 넣어 주세요.") from e
        if len(text) < 100:
            raise ValueError("링크에서 공고 본문을 찾지 못했습니다(화면이 스크립트로 그려지는 사이트일 수 있음). 본문을 붙여 넣어 주세요.")
    text = (text or "").strip()[:MAX_TEXT]
    if len(text) < 30:
        raise ValueError("공고 본문이 너무 짧습니다.")
    fields, method = rule_split(text), "rule"
    if not fields["duties"] or not fields["requirements"]:
        try:
            ai, dropped = llm_split(text)
            for k in FIELDS:
                if not fields[k] and ai.get(k):
                    fields[k] = ai[k]
            method = "rule+ai"
            if dropped:
                warnings.append(f"원문에 없는 내용 {dropped}줄은 넣지 않았습니다.")
        except llm.LLMError:
            warnings.append("AI 보조 분리에 실패해 제목 줄로만 나눴습니다. 빈 칸은 직접 채워 주세요.")
    for k, label in (("duties", "주요 업무"), ("requirements", "자격 요건")):
        if not fields[k]:
            warnings.append(f"'{label}' 칸을 찾지 못했습니다. 직접 채워 주세요.")
    warnings.append("가져온 내용은 원문 그대로입니다. 차별 소지 표현·사실 여부를 확인한 뒤 저장·게시하세요.")
    return {"fields": fields, "method": method, "source": source, "warnings": warnings}
