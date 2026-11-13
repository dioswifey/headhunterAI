"""사내문서 금지 용어 사전 `idoc:forbid` + 적재 감사 결과 `idoc:audit` — ADLC v0.7 6.13.7 ③ · 6.14.0 P-1·P-2.

**모든 등급**(public·internal·confidential·abstract) 문서의 `<!--forbid: a|b-->` 주석을 모아
Redis 해시 하나에 둔다. 조각 자신의 `forbid_terms` 메타만 보면 restricted 문서에만 적힌 고객사명이
공개판에 남아 있어도 걸리지 않기 때문이다(6.13.6 ⑧).

  - `idoc:forbid`        해시. 필드 = 금지 용어, 값 = 그 용어를 적은 doc_id 목록(`|A|B|`)
  - `idoc:forbid:meta`   해시. built_at · terms · docs · source_hash(용어 집합의 SHA-256)
  - `idoc:audit`         해시. 마지막 감사 게이트 결과(ok · at · critical · warnings · 조각 수)

**사전은 파일로 저장하지 않는다**(평문 고객사명이 git·배포 번들에 들어가지 않게, 6.13.7 ③).

쓰는 쪽(검색 ⑧ · `retrieve_internal` 4-B · `_rule_ok` · `guard_disclosure` 7-B · 적재 `audit`)의 규칙 — 6.13.8:
  사전을 읽을 수 없으면(Redis 오류 · 키 없음 · 비어 있음) **빈 목록으로 보지 않고** `ForbidUnavailable`을 낸다.
  `forbid_ok()`는 그때 False(= 사내문서 경로 사용 불가, 보수적)를 돌려준다.

일반 단어 처리 — 2026-11-13 팀장 결정: 일반 명사(주소·전화번호)는 조각·감사 검사에서 제외, 질문 문장 검사에만
사용 — 고유명사·금액 검사는 그대로. `주소`·`전화번호`는 고유명사가 아니라 개인정보
**항목 이름**이다. 공개판 본문(백엔드 JD의 'API 주소' 등 9조각)에 일반 단어로 나오므로 조각 버리기·감사 게이트에는
쓰지 않고(`chunk_terms`), 질문 문장 검사에는 전부 쓴다.

쓰기(`write`·`record_audit`)는 적재 에이전트 A13(`agents/internal_docs_agent.py`)만 한다(ADLC 2.2 쓰기 권한).
redis 패키지는 함수 안에서 늦게 불러온다(호스트 단위 검사가 redis 없이 돌게).
"""
import hashlib
import re
import time

KEY = "idoc:forbid"
META_KEY = "idoc:forbid:meta"
AUDIT_KEY = "idoc:audit"
# 용어별 검사 범위(2026-11-13 팀장 결정): both = 조각·감사 + 질문 문장 / question = 질문 문장 검사에만.
# 일반 명사(개인정보 항목 이름)는 공개판 본문('API 주소' 등)에 흔해 조각·감사에 쓰면 rag_ready()가 늘 닫힌다.
# 여기 없는 용어는 both.
TERM_SCOPE = {"주소": "question", "전화번호": "question"}
GENERIC_TERMS = frozenset(t for t, sc in TERM_SCOPE.items() if sc == "question")   # 호환 이름


def term_scope(t):
    return TERM_SCOPE.get(t, "both")


# 접두어 금지 용어 → 상세판 ID로 좁힘(2026-11-13 사용자 결정: 원본 주석은 그대로, 사전 만들 때 코드에서 변환).
# 접두어 그대로면 공개해도 되는 이름(공개판 PRJ-CC-PUB 등)까지 걸려 감사 게이트가 늘 실패한다.
NARROW = {
    "INC": ("INC-AI-DET", "INC-SV-DET"),
    "INC-AI": ("INC-AI-DET",),
    "INC-SV": ("INC-SV-DET",),
    "PRJ-CC": ("PRJ-CC-DET",),
    "PRJ-FC": ("PRJ-FC-DET",),
    "ISS": ("ISS-01",),
    "REQ": ("REQ-AIAX-01",),
}

# 마스킹 사본의 대체 표식(A15 프롬프트·A16 guard_disclosure가 같이 쓴다 — 질문·company_facts에 있으면 은행 원문으로).
# 값은 mask_rules.json `markers`와 같아야 한다(internal_masking.load_rules가 확인).
MASK_MARKERS = ("[금액]", "[비율]", "[점수]", "[공수]", "[규모]", "[일정]", "[고객 정보]")
MASK_ALIAS_RE = re.compile(r"고객사[A-Z]{1,2}(?![A-Za-z])")     # R1 가명(고객사A, 고객사B …)
MASK_ID_RE = re.compile(r"(?<![A-Za-z0-9])MSK-\d{2}(?![0-9])")  # 사본 doc_id가 문장에 새어 나온 경우


MASK_TOKENS = MASK_MARKERS                       # rag-impl 인터페이스 이름 — 표식이 늘면 MASK_MARKERS만 고친다
PSEUDONYM_RE = re.compile(r"(?:고객사|거래처|협력사|업체)\s?[A-Z]{1,2}(?![A-Za-z])")   # R1 고객사 가명 · R17 업체 가명


def masked_hits(text):
    """text에 든 마스킹 표식·가명·사본 ID 목록(없으면 []). A15 contextualize_rag · 7-B guard_disclosure가 부른다."""
    if not text:
        return []
    out = [m for m in MASK_TOKENS if m in text]
    out += sorted(set(PSEUDONYM_RE.findall(text)))
    out += MASK_ID_RE.findall(text)
    return out


def mask_markers_in(text):
    """text에 든 마스킹 표식 종류 목록(없으면 []). 질문 문장 검사용 — 걸리면 그 문항은 은행 원문으로."""
    if not text:
        return []
    out = [m for m in MASK_MARKERS if m in text]
    if PSEUDONYM_RE.search(text):           # masked_hits와 같은 가명 기준(고객사·거래처·협력사·업체)
        out.append("alias")
    if MASK_ID_RE.search(text):
        out.append("msk_id")
    return out


class ForbidUnavailable(Exception):
    """사전을 읽을 수 없음 → 호출 쪽은 사내문서 경로를 쓰지 않는다."""


def _client(r=None):
    if r is not None:
        return r
    import redis                        # 늦은 import
    from .. import config
    return redis.Redis.from_url(config.REDIS_URL, decode_responses=True, socket_timeout=3)


def _s(v):
    return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v


# ---------------- 만들기 (적재 에이전트만) ----------------
def collect(docs):
    """파싱된 문서 목록 → {용어: [doc_id, ...]}. 등급과 상관없이 모든 문서·모든 섹션.
    접두어 용어는 NARROW로 상세판 ID로 바꿔 넣는다. 마스킹 사본(masked)은 건너뛴다(사본은 forbid가 비어 있다)."""
    out = {}
    for d in docs:
        if d.get("masked"):
            continue
        for sec in d.get("sections") or []:
            for t in sec.get("forbid") or []:
                t = (t or "").strip()
                for u in NARROW.get(t, (t,) if t else ()):
                    ids = out.setdefault(u, [])
                    if d["doc_id"] not in ids:
                        ids.append(d["doc_id"])
    return out


def terms_hash(terms):
    return hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest()


def write(mapping, docs_count=0, r=None):
    """사전을 통째로 바꾼다. 임시 키에 다 쓴 뒤 RENAME → 읽는 쪽이 반쯤 찬 사전을 보지 않는다."""
    if not mapping:
        raise ValueError("금지 용어가 0개입니다 — 빈 사전은 쓰지 않습니다(읽는 쪽이 '사용 불가'로 처리)")
    r = _client(r)
    tmp = f"{KEY}:tmp:{int(time.time() * 1000)}"
    r.hset(tmp, mapping={t: "|" + "|".join(ids) + "|" for t, ids in mapping.items()})
    r.rename(tmp, KEY)
    r.hset(META_KEY, mapping={"built_at": str(int(time.time())), "terms": str(len(mapping)),
                              "docs": str(docs_count), "source_hash": terms_hash(mapping)})
    return len(mapping)


# ---------------- 읽기 (검색 · 관문) ----------------
def load_terms(r=None):
    """금지 용어 전체 목록(질문 문장 검사용). 읽을 수 없거나 비어 있으면 ForbidUnavailable."""
    try:
        raw = _client(r).hkeys(KEY)
    except Exception as e:                      # Redis 연결·명령 오류
        raise ForbidUnavailable(type(e).__name__) from e
    terms = sorted({_s(k) for k in (raw or [])})
    if not terms:
        raise ForbidUnavailable("empty")
    return terms


def chunk_terms(terms):
    """조각 버리기·감사 게이트용 — 범위가 both인 용어만(question 전용 일반 명사 제외)."""
    return [t for t in terms if term_scope(t) in ("both", "chunk")]


def question_terms(terms):
    """질문 문장 검사용 — 전부(범위 both·question)."""
    return [t for t in terms if term_scope(t) in ("both", "question")]


save = write          # 호환 이름(rag-impl 초안)
load = load_terms     # 호환 이름(rag-impl 초안)


def meta(r=None):
    try:
        return {_s(k): _s(v) for k, v in (_client(r).hgetall(META_KEY) or {}).items()}
    except Exception:
        return {}


def _pattern(t):
    # 영문·숫자 ID(PRJ-CC 등)는 앞뒤가 영문·숫자가 아닐 때만 — ISSUE ≠ ISS, 단 PRJ-CC-PUB는 PRJ-CC로 걸린다.
    # 한글 용어는 부분 문자열 그대로.
    if re.fullmatch(r"[A-Za-z0-9\-_]+", t):
        return r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])"
    return re.escape(t)


def find_terms(text, terms):
    """text에 들어 있는 금지 용어 목록. 공백을 지운 문장도 같이 본다('대한 전자'처럼 띄어 써서 빠져나가는 경우)."""
    if not text or not terms:
        return []
    squashed = re.sub(r"\s+", "", text)
    out = []
    for t in terms:
        p = _pattern(t)
        if re.search(p, text) or re.search(p, squashed) or (" " in t and re.search(_pattern(re.sub(r"\s+", "", t)), squashed)):
            out.append(t)
    return out


hits = find_terms        # 별칭(rag-impl 초안 이름)


def forbid_ok(text, terms=None, r=None):
    """True = 금지 용어 0건. 사전을 읽을 수 없으면 **False**(보수적, 6.13.8)."""
    if terms is None:
        try:
            terms = load_terms(r)
        except ForbidUnavailable:
            return False
    return not find_terms(text, terms)


# ---------------- 감사 게이트 결과 ----------------
def record_audit(report, r=None):
    """감사 결과를 남긴다. 본문·용어 원문은 쓰지 않고 판정·건수·사유 종류만(B-08)."""
    r = _client(r)
    r.delete(AUDIT_KEY)
    r.hset(AUDIT_KEY, mapping={
        "ok": "1" if report["ok"] else "0", "at": str(int(time.time())),
        "critical": "|".join(sorted(report["critical"])), "warnings": "|".join(sorted(report["warnings"])),
        "public_chunks": str(report["counts"]["public"]), "restricted_chunks": str(report["counts"]["restricted"]),
        "dict_hash": report.get("dict_hash") or ""})


def audit_status(r=None):
    """{ok: bool, at, critical[], warnings[]} — 기록이 없거나 읽을 수 없으면 None."""
    try:
        raw = {_s(k): _s(v) for k, v in (_client(r).hgetall(AUDIT_KEY) or {}).items()}
    except Exception:
        return None
    if not raw:
        return None
    return {"ok": raw.get("ok") == "1", "at": int(raw.get("at") or 0),
            "critical": [x for x in (raw.get("critical") or "").split("|") if x],
            "warnings": [x for x in (raw.get("warnings") or "").split("|") if x]}


def rag_ready(r=None):
    """사내문서 경로를 열어도 되는지 — (ok, 사유). 그래프 1-A `retrieve_internal`이 맨 앞에서 부른다.

    ① 사전을 읽을 수 있어야 하고 ② 마지막 감사 게이트가 통과여야 한다.
    감사 실패 때 public 컬렉션을 지우는 대신 이 신호로 '사내문서 없는 상태'로 떨어진다(H-08, 비파괴).
    """
    try:
        load_terms(r)
    except ForbidUnavailable as e:
        return False, f"forbid_unavailable:{e}"
    st = audit_status(r)
    if st is None:
        return False, "audit_missing"
    if not st["ok"]:
        return False, "audit_failed"
    return True, ""
