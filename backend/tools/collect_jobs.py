"""공고 원문 수집기 (사람인·원티드) — RAG 재생성용 참고 자료, 개발 전용.

2026-11-12 사용자 지시: 사람인 50개 + 원티드 150개, DB job_roles 7개 직무에 고르게.
원문은 저장소 루트 job_posting_resources/<site>/에 둔다(2026-11-12 사용자 지시). DB에 넣지 않는다.
다음 단계(임베딩 → Chroma → 재생성)는 LangGraph `app.agents.job_posting_graph`가 맡는다.

접속 예절
- 정직한 User-Agent, 브라우저 위장(impersonate)·stealthy_headers 끔, 프록시·우회 없음
- 요청 간격 4초 이상, 동시 1건
- 403·429·CAPTCHA·로그인 요구가 나오면 즉시 중단
- 사람인: 시작 때 robots.txt(User-agent: *)를 읽어 쓰는 경로가 Disallow면 중단
- 원티드: robots.txt가 403(CloudFront 차단)이라 읽을 수 없음 → RFC 9309상 4xx는 제한 없음으로 간주.
  공개 JSON API(/api/chaos/navigation/v1/results, /api/v4/jobs/{id})만 쓴다

실행 (Scrapling 가상환경):
  C:\\Users\\user\\.venvs\\scrapling\\Scripts\\python.exe -I backend/tools/collect_jobs.py --site saramin --total 50
  C:\\Users\\user\\.venvs\\scrapling\\Scripts\\python.exe -I backend/tools/collect_jobs.py --site wanted --total 150
이어하기: 이미 있는 파일(<site>_<id>.json)은 건너뛴다.
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.robotparser import RobotFileParser

from scrapling.fetchers import Fetcher

sys.stdout.reconfigure(encoding="utf-8")

UA = "headhunterAI-dev-research/0.1 (personal project; low-rate; respects robots.txt)"
DELAY = 4.0
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()

ROLES = ["ai_ax", "backend", "data_eng", "data_sci", "devops", "frontend", "mobile"]

# 제목 키워드(소문자 비교) — 직무 판정용
KW = {
    "ai_ax": ["ai", "머신러닝", "machine learning", "ml ", "ml엔지니어", "llm", "딥러닝", "인공지능", "ax", "mlops", "vision", "nlp"],
    "backend": ["백엔드", "back-end", "backend", "서버", "server", "spring", "java", "node"],
    "data_eng": ["데이터 엔지니어", "데이터엔지니어", "data engineer", "etl", "데이터 플랫폼", "데이터플랫폼", "빅데이터", "dw", "data platform"],
    "data_sci": ["데이터 분석", "데이터분석", "데이터 사이언티스트", "데이터사이언티스트", "data scientist", "data analyst", "분석가", "applied scientist", "analytics"],
    "devops": ["devops", "sre", "클라우드", "cloud", "인프라", "infra", "kubernetes", "플랫폼 엔지니어", "platform engineer", "devsecops"],
    "frontend": ["프론트엔드", "프론트", "front-end", "frontend", "front end", "react", "웹 퍼블리"],
    "mobile": ["안드로이드", "android", "ios", "모바일", "mobile", "flutter", "react native", "앱 개발", "app 개발"],
}

SARAMIN_QUERIES = {
    "ai_ax": ["AI 엔지니어", "머신러닝 엔지니어"],
    "backend": ["백엔드 개발자"],
    "data_eng": ["데이터 엔지니어"],
    "data_sci": ["데이터 분석가", "데이터 사이언티스트"],
    "devops": ["DevOps", "클라우드 엔지니어"],
    "frontend": ["프론트엔드 개발자"],
    "mobile": ["안드로이드 개발자", "iOS 개발자"],
}
WANTED_JOB_IDS = {  # 원티드 개발(518) 하위 직무 ID
    "ai_ax": [1634],
    "backend": [872],
    "data_eng": [655],
    "data_sci": [1024],
    "devops": [674],
    "frontend": [669],
    "mobile": [677, 678],
}


class Stop(Exception):
    pass


_last = [0.0]


def fetch(url):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = Fetcher.get(url, impersonate=None, stealthy_headers=False,
                    headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
    _last[0] = time.time()
    if r.status in (401, 403, 429):
        raise Stop(f"HTTP {r.status} — {url}")
    body = r.body.decode("utf-8", "ignore") if isinstance(r.body, bytes) else str(r.body)
    low = body[:20000].lower()
    if "captcha" in low or "recaptcha" in low:
        raise Stop(f"CAPTCHA 감지 — {url}")
    if "/login" in str(getattr(r, "url", "")):
        raise Stop(f"로그인 요구(리다이렉트) — {url}")
    return r, body


def role_score(title, role):
    t = " " + title.lower() + " "
    return sum(1 for k in KW[role] if k in t)


def best_role(title):
    s = {r: role_score(title, r) for r in ROLES}
    m = max(s.values())
    return [r for r in ROLES if s[r] == m] if m > 0 else []


def accepts(title, role, strict):
    """strict: 제목이 이 직무 키워드를 포함해야 함. 느슨: 다른 직무 키워드가 더 강하지 않으면 허용."""
    b = best_role(title)
    if strict:
        return role in b
    return not b or role in b


def clean(txt):
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n\n", txt)
    return txt.strip()


def quotas(total):
    base, extra = divmod(total, len(ROLES))
    return {r: base + (1 if i < extra else 0) for i, r in enumerate(ROLES)}


def existing(out, site):
    have = {}
    for p in out.glob(f"{site}_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            have[str(d["id"])] = d["role_code"]
        except Exception:
            pass
    return have


def save(out, site, rec):
    p = out / f"{site}_{rec['id']}.json"
    p.write_bytes(json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"))


def log_progress(n, total):
    if n % 10 == 0:
        print(f"[진행] {n}/{total}", flush=True)


# ---------------- 사람인 ----------------
SARAMIN = "https://www.saramin.co.kr"


def saramin_robots_check():
    rp = RobotFileParser()
    _, body = fetch(SARAMIN + "/robots.txt")
    rp.parse(body.splitlines())
    for path in ["/zf_user/search/recruit?searchword=x",
                 "/zf_user/jobs/relay/view?rec_idx=1",
                 "/zf_user/jobs/relay/view-detail?rec_idx=1"]:
        if not rp.can_fetch(UA, SARAMIN + path):
            raise Stop(f"robots.txt Disallow: {path}")
    print("[robots] 사람인 검색·상세 경로 허용 확인", flush=True)


def saramin_deadline_open(s):
    s = (s or "").strip()
    m = re.search(r"~\s*(\d{1,2})/(\d{1,2})", s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        y = TODAY.year if mo >= TODAY.month else TODAY.year + 1
        try:
            return dt.date(y, mo, d) >= TODAY
        except ValueError:
            return True
    if "마감" in s and "~" not in s and "오늘" not in s and "내일" not in s:
        return False
    return True  # 상시채용·채용시·오늘마감·내일마감


def collect_saramin(out, total):
    saramin_robots_check()
    q = quotas(total)
    have = existing(out, "saramin")
    got = {r: sum(1 for v in have.values() if v == r) for r in ROLES}
    n = len(have)
    seen = set(have)
    for role in ROLES:
        for strict in (True, False):
            for word in SARAMIN_QUERIES[role]:
                page = 1
                while got[role] < q[role] and page <= 3:
                    url = (f"{SARAMIN}/zf_user/search/recruit?searchword={quote(word)}"
                           f"&recruitPage={page}&recruitPageCount=40&recruitSort=reg_dt")
                    r, _ = fetch(url)
                    items = r.css("div.item_recruit")
                    if not items:
                        break
                    for it in items:
                        if got[role] >= q[role]:
                            break
                        rid = it.attrib.get("value")
                        title = clean(it.css("h2.job_tit a::attr(title)").get() or "")
                        corp = clean(it.css("strong.corp_name a::text").get() or "")
                        date = clean(it.css("div.job_date span.date::text").get() or "")
                        if not rid or rid in seen or not title:
                            continue
                        if not accepts(title, role, strict) or not saramin_deadline_open(date):
                            continue
                        seen.add(rid)
                        dr, _ = fetch(f"{SARAMIN}/zf_user/jobs/relay/view-detail?rec_idx={rid}")
                        text = clean(dr.get_all_text(ignore_tags=("script", "style")))
                        if len(text) < 200:  # 이미지 전용 공고는 텍스트가 없음 → 건너뜀
                            continue
                        rec = {
                            "site": "saramin", "id": rid, "role_code": role,
                            "company": corp, "title": title, "deadline": date,
                            "url": f"{SARAMIN}/zf_user/jobs/relay/view?rec_idx={rid}",
                            "detail_url": f"{SARAMIN}/zf_user/jobs/relay/view-detail?rec_idx={rid}",
                            "search_word": word,
                            "collected_at": dt.datetime.now(KST).isoformat(timespec="seconds"),
                            "body_text": text,
                        }
                        save(out, "saramin", rec)
                        got[role] += 1
                        n += 1
                        log_progress(n, total)
                    page += 1
    return got


# ---------------- 원티드 ----------------
WANTED = "https://www.wanted.co.kr"


def wanted_body(job):
    d = job.get("detail") or {}
    parts = []
    for key, label in [("intro", "회사 소개"), ("main_tasks", "주요 업무"), ("requirements", "자격 요건"),
                       ("preferred_points", "우대 사항"), ("benefits", "혜택 및 복지")]:
        if d.get(key):
            parts.append(f"## {label}\n{d[key]}")
    skills = [s.get("title") or s.get("text") for s in (job.get("skill_tags") or []) if isinstance(s, dict)]
    if skills:
        parts.append("## 기술 스택\n" + ", ".join(x for x in skills if x))
    return clean("\n\n".join(parts))


def collect_wanted(out, total):
    q = quotas(total)
    have = existing(out, "wanted")
    got = {r: sum(1 for v in have.values() if v == r) for r in ROLES}
    n = len(have)
    seen = set(have)
    for role in ROLES:
        for strict in (True, False):
            for jid in WANTED_JOB_IDS[role]:
                offset = 0
                while got[role] < q[role] and offset < 200:
                    url = (f"{WANTED}/api/chaos/navigation/v1/results?job_group_id=518&job_ids={jid}"
                           f"&country=kr&job_sort=job.latest_order&years=-1&locations=all&limit=20&offset={offset}")
                    _, body = fetch(url)
                    data = json.loads(body).get("data") or []
                    if not data:
                        break
                    for x in data:
                        if got[role] >= q[role]:
                            break
                        wid = str(x["id"])
                        title = x.get("position") or ""
                        if wid in seen or not accepts(title, role, strict):
                            continue
                        seen.add(wid)
                        _, db = fetch(f"{WANTED}/api/v4/jobs/{wid}")
                        job = json.loads(db).get("job") or {}
                        if job.get("status") != "active" or job.get("hidden"):
                            continue
                        due = job.get("due_time")
                        if due and dt.date.fromisoformat(due[:10]) < TODAY:
                            continue
                        text = wanted_body(job)
                        if len(text) < 200:
                            continue
                        rec = {
                            "site": "wanted", "id": wid, "role_code": role,
                            "company": (job.get("company") or {}).get("name", ""),
                            "title": job.get("position", title), "deadline": due or "상시",
                            "url": f"{WANTED}/wd/{wid}",
                            "category": f"518/{jid}",
                            "collected_at": dt.datetime.now(KST).isoformat(timespec="seconds"),
                            "body_text": text,
                        }
                        save(out, "wanted", rec)
                        got[role] += 1
                        n += 1
                        log_progress(n, total)
                    offset += 20
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=["saramin", "wanted"], required=True)
    ap.add_argument("--total", type=int, required=True)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "job_posting_resources"))
    a = ap.parse_args()
    out = Path(a.out) / a.site          # 사이트별 하위 폴더 saramin/ · wanted/ (2026-11-13 사용자 지시)
    out.mkdir(parents=True, exist_ok=True)
    fn = collect_saramin if a.site == "saramin" else collect_wanted
    try:
        got = fn(out, a.total)
        print(f"[완료] {a.site} 직무별: {got} 합계 {sum(got.values())}", flush=True)
    except Stop as e:
        print(f"[중단] {e}", flush=True)
        print(f"[중단 시점] 저장된 파일 {len(list(out.glob(a.site + '_*.json')))}개", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
