"""A17 사내문서 질문 생성 · A18 질문 검증 — ADLC v0.8 6.19.4 (2026-11-17 사용자 지시).

**이 프로젝트의 중심 축**: 기본 질문 8개를 Redis 질문 은행에서 꺼내 쓰는 대신,
사내문서(Chroma `internal_docs_public`)에서 **인성 · 직무 · 기업 질문을 미리 만들어 벡터화**해 두고
면접 때 공고·이력서로 검색해 꺼낸다. Redis는 백업(목표: 지원자에게 가는 질문의 20% 미만).

왜 '미리 만들어 두는' 방식인가 — 2026-11-16 실측: 면접 중에 조각을 근거로 은행 문항을 다시 쓰는
실시간 방식(A15)은 지원자 20명 중 8명만 성공했고(40%) 실패 사유도 남지 않았다(6.17.4 QO-13).
미리 만들면 ① 실패분을 다시 시도·사람이 검수할 수 있고 ② 면접 중 모델 호출이 0이고
③ 결과가 Redis 문항과 같은 성질(완성된 한 문장 + 임베딩)이라 그래프 1-A가 거의 그대로 쓴다.

흐름 (A13 적재가 끝난 뒤 오프라인으로 돈다)
    1 select   : public 조각 중 카테고리(`internal_qcat`)가 맞는 것을 문서마다 상위 N개
    2 generate : 조각 1개 → 질문 후보 1~3개            (A17, 로컬 Qwen `llm.chat_json`)
    3 validate : 규칙 검사 — 탈락 사유를 **반드시 남긴다** (A18, 모델 호출 없음)
    4 dedupe   : 질문 문장 임베딩 → 같은 카테고리 안 코사인 ≥ 0.90 이면 버림
    5 upsert   : Chroma `internal_questions_{personality|job|company}`
    6 prune    : 원본 조각이 사라졌거나 본문이 바뀐 조각에서 나온 질문 정리

규칙(ADLC 2.3): 모델 호출은 `llm.chat_json`·`llm.embed`만, 외부 AI 금지(사내문서가 가상 데이터라도
이건 제품 실행 경로다 — CLAUDE.md 외부 AI 예외 ③은 '문서 집필'에만 해당). 공개 등급은 public만 읽는다.

실행(컨테이너 안):
    python -m app.agents.internal_question_agent generate [--cats personality job company] [--per-doc 5] [--limit N] [--dry-run]
    python -m app.agents.internal_question_agent sync          # 조각 본문이 바뀐 것만 다시
    python -m app.agents.internal_question_agent stats
    python -m app.agents.internal_question_agent prune [--dry-run]
    python -m app.agents.internal_question_agent export --out /tmp/iq     # 배포 사본(JSONL + 벡터)
호스트에서도 돈다(DB 불필요, Ollama HTTP만): cd backend && EMBED_BASE_URL=http://localhost:11434 python -m app.agents.internal_question_agent stats
"""
import argparse
import base64
import hashlib
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import config, internal_store, llm, prompts  # noqa: E402
from app.agents import internal_forbid, internal_qcat, internal_search  # noqa: E402

CATS = internal_qcat.CATS
PER_DOC = 5                 # 문서마다 질문을 만들 조각 수(1차 범위, 6.19.4)
N_PER_CHUNK = 2             # 조각 1개에서 만들 질문 수 상한
DUP_SIM = 0.90              # 같은 카테고리 안 중복 판정(코사인)
MIN_LEN, MAX_LEN = 10, 160
SPEC = "A17 v1 (ADLC v0.8 6.19.4)"

GEN_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {
        "type": "object",
        "properties": {"question": {"type": "string"}, "intent": {"type": "string"},
                       "competency": {"type": "string"}, "signals": {"type": "string"},
                       "keywords": {"type": "array", "items": {"type": "string"}},
                       "facts": {"type": "array", "items": {"type": "string"}}},
        "required": ["question", "intent", "competency", "signals", "keywords", "facts"]}}},
    "required": ["questions"]}

CAT_LABEL = {"personality": "인성 질문 — 인재상·협업·책임감·학습·소통처럼 '어떤 사람인가'를 보는 질문",
             "job": "직무 이해도 질문 — 실제 업무·기술 판단·문제 해결 방식을 보는 질문",
             "company": "기업 이해 질문 — 회사가 하는 일·일하는 방식·고객을 이해하고 있는지 보는 질문"}
CAT_GUIDE = {
    "personality": "지원자 본인의 경험을 묻되, 이 회사가 중요하게 보는 행동 기준이 배경으로 깔리게 한다. 특정 기술 지식을 묻지 않는다.",
    "job": "업무 상황을 주고 어떻게 판단·처리할지 묻는다. 조각에 적힌 기술·절차는 배경으로만 쓰고, 지원자가 그 시스템을 이미 안다고 전제하지 않는다.",
    "company": "회사의 사업·고객·일하는 방식에 대한 이해와 지원 동기가 드러나게 묻는다. 내부 수치·고객사 이름은 쓰지 않는다.",
}

# 지원자가 이 회사에서 일했다고 전제하는 과거형 표현(A18 ⑦). `questions.PAST_PREMISE`와 같은 목적
PAST_PREMISE = re.compile(r"(하셨던|하셨는|하신\s*경험|진행하신|진행하셨|담당하신|담당하셨|맡으셨|맡았던|"
                          r"참여하신|참여하셨|수행하신|수행하셨|경험하신|구축하신|구축하셨|개발하신|개발하셨)")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:[:/]\d+)+|\d+(?:\.\d+)?%?")
SENT_END = re.compile(r"(?:[다요까]|니까)[.?!]\s*(?=\S)")

# A18 3차 추가(2026-11-19) — OpenAI 전환 뒤에도 100%는 아니라 비문 검사기를 따로 둔다(사용자 지시).
# "관련된 경험이 있다면 ~ 답해 주셔도 좋습니다" 덧붙임은 예외로 통과(사용자가 허용한 문구) — 세 검사 전에 떼어낸다.
EXPERIENCE_SUFFIX = re.compile(r"[,.]?\s*관련된?\s*(?:업무\s*)?경험[^.?!]{0,20}있다면[^.?!]*(?:답해|말씀해|설명해)\s*주(?:셔도|시면)\s*좋습니다\.?\s*$")
# ① 물음표·종결 요청이 2개 이상 → "묻는 것이 둘 이상"(규칙6 "묻는 것은 하나만")
# "?"와 "하시겠습니까" 등은 한 문장 끝에 같이 붙어 있는 게 보통이라(…하시겠습니까?) 그대로 세면 한 질문이 2개로
# 겹쳐 세어진다 — "?"가 뒤따르지 않는 요청 어미만 별도로 센다.
ASK_END = re.compile(r"\?|(?:하시겠습니까|할까요|해\s*주세요|해주세요|주시겠습니까|바랍니다|부탁드립니다)(?!\s*\?)")
# ② 선행사 없이 등장하는 지시어("그 결과물", "이 과정" 등)
DANGLING_REF = re.compile(r"그\s*결과물|이\s*과정|해당\s*부분|그것을|그\s*부분|이\s*결과(?!물)|그\s*결과(?!물)|해당\s*내용|이\s*내용|그\s*내용")
# ③ 따옴표로 감싼, 설명 없는 사내 단계·절차 명사구(예: '적용 가능성 검토') — 안에 종결 어미가 없는 짧은 명사구만 잡는다
QUOTED_PHRASE = re.compile(r"['\"'''""「」『』]([^'\"'''""「」『』]{2,16})['\"'''""」』]")
QUOTED_SENTENCE_END = re.compile(r"(다|요|까|라|네|죠)[.!?]?$")
# A18 신규(2026-11-19): 조각 머리글·메타 설명만 베껴 구체적 업무 상황이 없는 질문을 걸러낸다(internal_question.md 규칙1)
# "다면"은 한국어 조건형 어미("~라면/한다면/있다면/된다면/본다면" 등)를 통째로 잡는다 — 개별 동사 나열은
# "~이 있다면"처럼 흔한 표현을 놓치는 일(검증 중 발견)이 있어 어미 하나로 단순화했다.
SITUATION_MARKERS = re.compile(r"(다면|상황|경우|때|진행\s*중|직면|마주)")

# A18 2차 수정(2026-11-19, 사용자 지적 → 조정자 지시): 1차 수정(SITUATION_MARKERS)은 "~다면" 어미만 보기 때문에,
# 조건형 어미는 붙었지만 알맹이 없이 intent·competency·expected_keywords만 되풀이한 질문("~상황이라면 어떻게
# 하시겠습니까")을 걸러내지 못했다. 로컬 모델(A6·A7과 같은 패턴)로 판단해 봤으나 591개 실측에서 42~43%를
# 탈락시켰고 그중 다수가 사람이 보기엔 멀쩡했다(예: "RFC 소유자가…테크 리드…" — 모델이 표면 형태("~때 어떻게")만
# 보고 내용을 다시 안 따지는 경향, 두 번 조정해도 비율이 그대로라 모델 역량 한계로 판단해 폐기). 대신 순수 규칙으로:
# 숫자·인용부호·영문 토큰이 하나도 없고, 한글 토큰에서 조사·어미를 뗀 나머지가 전부 "추상 메타 어휘"
# (가치·원칙·역할 설명에 쓰는 상위어)에 걸리면 탈락시킨다. 부서·팀 이름은 '누가'일 뿐이라 그 자체로 앵커로 치지
# 않는다(그래서 "백엔드팀에서 의존 관계와 운영 위험을…" 예문이 "백엔드팀"만으로 통과하던 문제를 잡았다).
# 591개 실측 0.3%(2/591, 둘 다 원래 지적된 두 문장) — 상세 `documents/03_QA_doc/RAG_질문품질_QA.md`.
CONCRETE_FILLER = {
    "어떻게", "하시겠습니까", "할까요", "설명해", "주세요", "무엇", "인가요", "있을까요", "있나요",
    "생각하시겠습니까", "싶은가", "할것인가요", "계획인가요", "그리고", "그러나", "하지만", "또는", "또한",
    "이때", "만약", "어떤", "이라고", "할수있을까요", "해야할까요", "할수있나요", "것인가요",
}
CONCRETE_ABSTRACT = {
    "의존", "관계", "운영", "위험", "문제", "협업", "소통", "조정", "문서화", "변경", "내용", "상황",
    "경우", "업무", "과정", "절차", "방식", "기준", "역량", "가치", "역할", "책임", "판단", "대응",
    "결정", "관리", "활용", "적용", "반영", "고려", "유지", "개선", "확인", "처리", "진행", "수행",
    "담당", "설명", "부서", "팀", "회사", "직원", "구성원", "인원", "조직", "다른", "같은", "동일",
    "관련", "해당", "본인", "스스로", "노력", "자세", "마음가짐", "태도", "철학", "방향", "성향",
    "성격", "수준", "정도", "범위", "중요", "핵심", "우선", "방법", "요소",
    "조기", "드러내", "나타내", "보여주", "표현", "지키", "따르", "갖추", "드러나",
}
CONCRETE_SUFFIXES = sorted([
    "이라면", "한다면", "있다면", "된다면", "하다면", "했던", "하고", "해서", "하여", "으로", "에서",
    "에게", "부터", "까지", "처럼", "같이", "이나", "이란", "로서", "로써", "한테",
    "라면", "다면", "이면", "하는", "할", "한", "이", "가", "을", "를", "은", "는", "의", "에", "로",
    "과", "와", "나", "도", "만",
], key=len, reverse=True)


def _strip_concrete_suffixes(tok):
    changed = True
    while changed and tok:
        changed = False
        for suf in CONCRETE_SUFFIXES:
            if len(tok) > len(suf) and tok.endswith(suf):
                tok = tok[: -len(suf)]
                changed = True
                break
    return tok


def has_concrete_anchor(q):
    """(ok, 사유). 숫자·인용부호·영문 토큰, 또는 추상 메타 어휘가 아닌 한글 명사 중 하나라도 있으면 True."""
    if re.search(r"\d", q):
        return True, "숫자"
    if re.search(r"['\"'‘’“”「」『』]", q):
        return True, "인용부호"
    if re.search(r"[A-Za-z]{2,}", q):
        return True, "영문 고유명사"
    for t in re.findall(r"[가-힣]{2,}", q):
        if t in CONCRETE_FILLER:
            continue
        stem = _strip_concrete_suffixes(t)
        if not stem or stem in CONCRETE_FILLER:
            continue
        if stem.endswith("팀") and len(stem) <= 6:   # 부서·팀 이름은 '누가'일 뿐 '무슨 일'이 아니다
            continue
        if stem in CONCRETE_ABSTRACT:
            continue
        if any((stem.startswith(a) or a.startswith(stem)) for a in CONCRETE_ABSTRACT if len(stem) >= 2 and len(a) >= 2):
            continue
        return True, f"구체 명사 '{t}'(stem:{stem})"
    return False, "구체 앵커 없음"


def _squash(s):
    return re.sub(r"\s+", "", (s or "").lower())


def _one_sentence(q):
    return not SENT_END.search((q or "").strip())


def _blocklist():
    from app import questions as Q
    return Q.BLOCKLIST


def _asks_interview_method(text):
    from app import questions as Q
    return Q.asks_interview_method(text)


def chunk_hash(body):
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:32]


# ---------------- 1 select ----------------
def _ref(m):
    return f"{m.get('doc_id', '')}#{m.get('sec', '')}"


def select_chunks(q_cat, per_doc=PER_DOC, limit=None):
    """public 조각 중 이 카테고리에 맞는 것을 문서마다 상위 per_doc개. 긴 조각(내용이 많은 쪽)을 먼저 고른다.

    안전: `disclosure=public` · `status ∈ internal_search.ALLOWED_STATUS` 조각만. 금지 용어가 든 조각은 버린다.
    (Chroma `where`를 쓰지 않고 앱에서 거른다 — 전체 조각을 한 번 읽는 오프라인 작업이라 필터 비용이 의미 없다.)
    """
    try:
        terms = internal_forbid.chunk_terms(internal_forbid.load_terms())
    except Exception as e:  # noqa: BLE001 — 사전을 못 읽으면 질문을 만들지 않는다(보수적, 6.13.8)
        raise SystemExit(f"금지 용어 사전을 읽을 수 없습니다({type(e).__name__}) — A13 적재를 먼저 돌리세요")
    by_doc = {}
    for c in internal_store.chunks(internal_store.PUBLIC, documents=True):
        m, body = c["metadata"], c.get("document") or ""
        if m.get("disclosure") != "public" or m.get("status") not in internal_search.ALLOWED_STATUS:
            continue
        if not internal_qcat.matches(m, q_cat):
            continue
        if internal_forbid.find_terms(body, terms) or internal_forbid.masked_hits(body):
            continue
        if len(body) < 200:                      # 너무 짧은 조각(제목·표 머리)은 질문 재료가 안 된다
            continue
        by_doc.setdefault(m.get("doc_id") or "", []).append({"chunk_id": c["id"], "body": body, "meta": m})
    out = []
    for did in sorted(by_doc):
        rows = sorted(by_doc[did], key=lambda x: (-len(x["body"]), x["chunk_id"]))[:per_doc]
        out.extend(rows)
    out.sort(key=lambda x: x["chunk_id"])
    return out[:limit] if limit else out


# ---------------- 2 generate (A17) ----------------
def generate_for_chunk(chunk, q_cat, n=N_PER_CHUNK):
    m = chunk["meta"]
    system = prompts.load("generate/internal_question", cat_label=CAT_LABEL[q_cat], cat_guide=CAT_GUIDE[q_cat],
                          doc_title=m.get("doc_title", ""), sec_title=m.get("sec_title", ""),
                          chunk_ref=_ref(m), doc_type=m.get("doc_type", ""), chunk_body=chunk["body"][:3000],
                          seconds=config.QUESTION_SECONDS, n_questions=n)
    # 2026-11-19 사용자 지시 — 외부 AI 금지 규칙의 **예외 ⑥(A17 사내문서 질문 생성)**.
    # 계기: 로컬 Qwen 4B가 비문("그 결과물로 무엇을 만들고 싶은가")·사내 용어 전제("'적용 가능성 검토' 단계에서")를
    # 반복 생산했고(사용자 실측 "10문제 중 1문제만 쓸 만함"), 같은 4B 검증으로는 걸러지지도 않았다.
    # 보내는 것: 가상 회사 사내문서 조각뿐. 지원자 데이터 0건. 면접 중이 아니라 **오프라인 사전 생성**.
    # (임베딩은 그대로 로컬 — Chroma가 qwen3-embedding 1024차원이라 바꾸면 검색이 성립하지 않는다.)
    usage = None
    if config.IQ_GEN_PROVIDER == "openai":
        try:
            out, usage = llm.openai_chat_json(system, "위 조각으로 질문을 만들어라.", GEN_SCHEMA,
                                               timeout=120, temperature=0.4)
        except Exception as e:  # noqa: BLE001 — 키 없음·한도·오류 → 로컬로 내려간다(생성이 멈추지 않게)
            log.warning("A17 OpenAI 생성 실패(%s) → 로컬 모델로 대체", e)
            out = llm.chat_json(system, "위 조각으로 질문을 만들어라.", GEN_SCHEMA, timeout=120, temperature=0.4)
    else:
        out = llm.chat_json(system, "위 조각으로 질문을 만들어라.", GEN_SCHEMA, timeout=120, temperature=0.4)
    return (out.get("questions") or [])[:n], usage


# ---------------- 3 validate (A18) ----------------
def has_malformed_phrasing(q):
    """(ok, reason) — A18 3차(2026-11-19): OpenAI 전환 후에도 남는 비문 3종을 규칙으로 잡는다.
    "관련된 경험이 있다면 ~ 답해 주셔도 좋습니다" 덧붙임(사용자 허용 문구)은 검사 전에 떼어낸다."""
    stripped = EXPERIENCE_SUFFIX.sub("", q)
    if len(ASK_END.findall(stripped)) >= 2:
        return False, "multi_ask"                         # 묻는 것이 둘 이상(규칙6 "묻는 것은 하나만")
    if DANGLING_REF.search(stripped):
        return False, "dangling_ref"                      # 선행사 없는 지시어("그 결과물" 등)
    for m in QUOTED_PHRASE.finditer(stripped):
        phrase = m.group(1).strip()
        if phrase and not QUOTED_SENTENCE_END.search(phrase):
            return False, "quoted_internal_term:" + phrase[:20]  # 설명 없는 사내 단계·절차 명사구
    return True, ""


def validate(cand, chunk, q_cat, terms):
    """(ok, reason). **탈락 사유를 반드시 돌려준다** — A15가 사유를 남기지 않아 원인을 못 찾은 일(QO-13)을 반복하지 않는다."""
    q = (cand.get("question") or "").strip()
    facts = [f.strip() for f in (cand.get("facts") or []) if f and f.strip()]
    body = chunk["body"]
    if not q:
        return False, "empty"
    if not (MIN_LEN <= len(q) <= MAX_LEN):
        return False, "length"
    if not _one_sentence(q):
        return False, "not_one_sentence"
    if _blocklist().search(q):
        return False, "blocklist"
    if not terms:
        return False, "forbid_unavailable"               # 검사할 수 없으면 쓰지 않는다(6.13.8)
    if internal_forbid.find_terms(q, terms) or any(internal_forbid.find_terms(f, terms) for f in facts):
        return False, "forbid_term"
    if internal_forbid.masked_hits(q) or any(internal_forbid.masked_hits(f) for f in facts):
        return False, "mask_marker"
    if _asks_interview_method(q):
        return False, "interview_meta"
    if PAST_PREMISE.search(q):
        return False, "premise"                          # 이 회사에서 일했다고 전제하는 질문
    if "사내문서" in q or "내부 자료" in q or "내부 문서" in q:
        return False, "mentions_internal_doc"
    ok_phrasing, why_phrasing = has_malformed_phrasing(q)
    if not ok_phrasing:
        return False, why_phrasing
    allowed = _squash(body)
    if not all(_squash(f) in allowed for f in facts):
        return False, "facts_not_grounded"
    bad = sorted(t for t in set(TOKEN_RE.findall(q)) if _squash(t.strip(".")) not in allowed)
    if bad:
        return False, "bad_tokens:" + ",".join(bad[:4])   # 조각에 없는 영문·숫자 토큰 = 지어낸 것
    if not SITUATION_MARKERS.search(q):
        return False, "no_situation"                     # 금지·조작 검사는 통과했지만 구체적 업무 상황 없이 조각 머리글·메타 설명만 베낀 질문
    concrete, why = has_concrete_anchor(q)
    if not concrete:
        return False, "template_only:" + (why or "")[:30]  # "다면" 어미만 있고 알맹이(숫자·인용·구체 명사) 없는 질문
    shaped, why2 = sentence_shape(q)
    if not shaped:
        return False, "shape:" + why2                     # 추상 범주 상황 · 개념 두 개 묶기 · 목적어 없는 맺음
    return True, ""


# A18 3차(2026-11-19, 사용자 지적): "LLM 응용에서 모델 답변의 근거 여부와 불확실성을 점검하는 상황이라면,
# 어떻게 하시겠습니까?" — ① 상황이 추상 범주("LLM 응용에서")뿐 ② 서로 다른 개념 둘을 '와'로 묶음
# ③ 맺음에 목적어 없음. 셋 다 기계로 잡는다.
ABSTRACT_HEAD = re.compile(
    r"^\s*(?:[가-힣A-Za-z0-9·/\s]{0,20})?"
    r"(?:LLM|AI|RAG|ML|데이터|모델|서비스|시스템|운영|업무|기술|분석|개발|프로젝트)\s*"
    r"(?:응용|활용|처리|관리|구축|운영|업무|과정|환경|영역|분야)\s*(?:에서|에서는|에선)")
# '와/과'로 묶인 서로 다른 개념(각각 2글자 이상 명사구) + 바로 뒤에 목적격/주격 조사
TWO_CONCEPTS = re.compile(r"[가-힣]{2,}\s*(?:여부|성|도|율|량|감)?\s*(?:와|과)\s+[가-힣]{2,}\s*(?:여부|성|도|율|량|감)?\s*(?:을|를|의|이|가)")
# 목적어 없이 맨 '어떻게 하시겠습니까'로만 끝나는 맺음
BARE_ASK = re.compile(r"(?:,|\s)\s*어떻게\s*(?:하|대응하|처리하)시겠습니까\s*\?*\s*$")


def sentence_shape(q):
    """(ok, reason). 문장 틀 검사 — 추상 범주 상황 · 개념 두 개 묶기 · 목적어 없는 맺음."""
    if ABSTRACT_HEAD.search(q):
        return False, "abstract_head"
    if TWO_CONCEPTS.search(q):
        return False, "two_concepts"
    if BARE_ASK.search(q):
        return False, "bare_ask"
    return True, ""


# ---------------- 4 dedupe + 5 upsert ----------------
def _cos(a, b):
    import math
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


def _meta_of(cand, chunk, q_cat, model):
    m = chunk["meta"]
    meta = {
        "q_cat": q_cat, "question": (cand.get("question") or "").strip(),
        "intent": (cand.get("intent") or "")[:120], "competency": (cand.get("competency") or "")[:40],
        "model_answer": (cand.get("signals") or "")[:600],
        "expected_keywords": ", ".join([k.strip() for k in (cand.get("keywords") or []) if k and k.strip()][:6]),
        "company_facts": internal_qcat.pipe([f.strip() for f in (cand.get("facts") or []) if f and f.strip()]),
        "doc_refs": internal_qcat.pipe([_ref(m)]),
        "chunk_id": chunk["chunk_id"], "chunk_hash": chunk_hash(chunk["body"]),
        "doc_id": m.get("doc_id", ""), "sec": m.get("sec", ""), "doc_title": m.get("doc_title", ""),
        "sec_title": m.get("sec_title", ""), "doc_type": m.get("doc_type", ""),
        "disclosure": m.get("disclosure", ""), "status": m.get("status", ""),
        "dept": m.get("dept", ""), "ct_band": m.get("ct_band", ""),
        "lang": "ko", "gen_model": model, "embed_model": config.EMBED_MODEL,
        "spec": SPEC, "updated_at": int(time.time()),
    }
    for d in ("ai_ax", "backend", "data_sci", "consult", "sales"):
        meta[f"dept_{d}"] = bool(m.get(f"dept_{d}"))
    return meta


def _qid(q_cat, chunk_id, i):
    return f"IQ-{q_cat[:4]}-{hashlib.sha256(chunk_id.encode()).hexdigest()[:10]}-{i}"


def run_generate(cats=CATS, per_doc=PER_DOC, limit=None, dry=False, only_changed=False, n=N_PER_CHUNK):
    try:
        terms = internal_forbid.load_terms()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"금지 용어 사전을 읽을 수 없습니다({type(e).__name__})")
    report = {"spec": SPEC, "cats": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    cost = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for q_cat in cats:
        chunks = select_chunks(q_cat, per_doc=per_doc, limit=limit)
        if only_changed:
            have = internal_store.question_chunk_hashes(q_cat)
            chunks = [c for c in chunks if have.get(c["chunk_id"]) != chunk_hash(c["body"])]
        rep = {"chunks": len(chunks), "generated": 0, "accepted": 0, "rejected": {}, "dup": 0, "llm_error": 0}
        report["cats"][q_cat] = rep
        print(f"\n[{q_cat}] 대상 조각 {len(chunks)}개" + (" (--dry-run)" if dry else ""))
        if dry or not chunks:
            for c in chunks[:5]:
                print(f"   {c['chunk_id']:<28} {c['meta'].get('doc_title', '')[:30]} > {c['meta'].get('sec_title', '')[:24]}")
            continue
        kept, kept_vecs = [], []
        t0 = time.time()
        for i, c in enumerate(chunks, 1):
            try:
                cands, usage = generate_for_chunk(c, q_cat, n=n)
                if usage:
                    cost["calls"] += 1
                    cost["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    cost["completion_tokens"] += usage.get("completion_tokens", 0)
            except llm.LLMError as e:
                rep["llm_error"] += 1
                print(f"  [{i}/{len(chunks)}] {c['chunk_id']} 모델 실패({e}) — 건너뜀")
                continue
            rep["generated"] += len(cands)
            ok_here = []
            for cand in cands:
                ok, why = validate(cand, c, q_cat, terms)
                if not ok:
                    key = why.split(":")[0]
                    rep["rejected"][key] = rep["rejected"].get(key, 0) + 1
                    continue
                ok_here.append(cand)
            if not ok_here:
                print(f"  [{i}/{len(chunks)}] {c['chunk_id']} 통과 0개")
                continue
            texts = [x["question"].strip() for x in ok_here]
            try:
                vecs = llm.embed(texts)
            except llm.LLMError as e:
                rep["llm_error"] += 1
                print(f"  [{i}/{len(chunks)}] {c['chunk_id']} 임베딩 실패({e}) — 다음 sync 재시도")
                continue
            for cand, v in zip(ok_here, vecs):
                if any(_cos(v, kv) >= DUP_SIM for kv in kept_vecs):
                    rep["dup"] += 1
                    continue
                kept.append((cand, c, v))
                kept_vecs.append(v)
            print(f"  [{i}/{len(chunks)}] {c['chunk_id']:<28} 통과 {len(ok_here)} · 누적 {len(kept)}  ({time.time() - t0:.0f}초)")
        # 5 upsert — 조각 단위로 먼저 지우고 새로 넣는다(같은 조각에서 질문 수가 줄어도 남지 않게)
        for chunk_id in {c["chunk_id"] for _, c, _ in kept}:
            internal_store.delete_questions_of_chunk(q_cat, chunk_id)
        per_chunk = {}
        ids, docs_, metas, vecs2 = [], [], [], []
        model = getattr(config, "LLM_MODEL", "")
        for cand, c, v in kept:
            k = per_chunk.get(c["chunk_id"], 0)
            per_chunk[c["chunk_id"]] = k + 1
            ids.append(_qid(q_cat, c["chunk_id"], k))
            docs_.append(cand["question"].strip())
            metas.append(_meta_of(cand, c, q_cat, model))
            vecs2.append(v)
        if ids:
            internal_store.upsert_questions(q_cat, ids, docs_, metas, vecs2)
        rep["accepted"] = len(ids)
        print(f"[{q_cat}] 저장 {len(ids)}개 · 중복 {rep['dup']} · 탈락 {sum(rep['rejected'].values())} "
              f"{rep['rejected']} · 모델 실패 {rep['llm_error']}")
    cost["cost_usd"] = round(cost["prompt_tokens"] / 1e6 * config.OPENAI_PRICE_IN_PER_M
                             + cost["completion_tokens"] / 1e6 * config.OPENAI_PRICE_OUT_PER_M, 4)
    report["cost"] = cost
    print(f"\n[비용] OpenAI 호출 {cost['calls']}회 · 입력 {cost['prompt_tokens']} · 출력 {cost['completion_tokens']} 토큰"
          + (f" · 약 ${cost['cost_usd']}" if config.OPENAI_PRICE_IN_PER_M or config.OPENAI_PRICE_OUT_PER_M else " · 금액 미산출(OPENAI_PRICE_* 미설정)"))
    return report


# ---------------- 6 prune ----------------
def run_prune(dry=False):
    """원본 조각이 사라졌거나 본문이 바뀐 조각에서 나온 질문을 지운다(바뀐 쪽은 sync가 다시 만든다)."""
    live = {}
    for c in internal_store.chunks(internal_store.PUBLIC, documents=True):
        m = c["metadata"]
        if m.get("disclosure") == "public":
            live[c["id"]] = chunk_hash(c.get("document") or "")
    total = 0
    for q_cat in CATS:
        try:
            have = internal_store.question_chunk_hashes(q_cat)
        except Exception as e:  # noqa: BLE001
            print(f"  [{q_cat}] 질문 컬렉션을 읽지 못함({type(e).__name__}) — 건너뜀")
            continue
        gone = [cid for cid in have if cid not in live]
        stale = [cid for cid, h in have.items() if cid in live and live[cid] != h]
        print(f"  [{q_cat}] 조각 사라짐 {len(gone)} · 본문 바뀜 {len(stale)}" + (" (--dry-run)" if dry else ""))
        if dry:
            continue
        for cid in gone + stale:
            internal_store.delete_questions_of_chunk(q_cat, cid)
            total += 1
    print(f"prune: {total}건 정리" if not dry else "(--dry-run) 정리 안 함")
    return total


# ---------------- stats / export ----------------
def run_stats():
    out = {"persist_dir": internal_store.PERSIST_DIR, "cats": {}}
    for q_cat in CATS:
        try:
            rows = internal_store.questions_all(q_cat, documents=False)
        except Exception as e:  # noqa: BLE001
            out["cats"][q_cat] = {"error": type(e).__name__}
            continue
        docs_ = {}
        comp = {}
        for r in rows:
            m = r["metadata"]
            docs_[m.get("doc_id", "")] = docs_.get(m.get("doc_id", ""), 0) + 1
            c = m.get("competency") or "(없음)"
            comp[c] = comp.get(c, 0) + 1
        out["cats"][q_cat] = {"questions": len(rows), "docs": len(docs_),
                              "top_competency": sorted(comp.items(), key=lambda x: -x[1])[:8]}
    return out


def run_export(out_dir):
    """배포 사본 — 질문·메타·벡터를 JSONL로. `bank_seed`와 같은 방식(벡터는 base64)."""
    import numpy as np
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    path = os.path.join(out_dir, "internal_questions.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"spec": SPEC, "embed_model": config.EMBED_MODEL,
                                      "embed_dim": config.EMBED_DIM}}, ensure_ascii=False) + "\n")
        for q_cat in CATS:
            coll = internal_store.question_collection(q_cat)
            got = coll.get(include=["metadatas", "documents", "embeddings"])
            embs = got.get("embeddings")                 # chromadb가 numpy 배열로 돌려줘서 `or []`는 쓸 수 없다(진리값 모호)
            for i, qid in enumerate(got.get("ids") or []):
                emb = embs[i] if embs is not None else []
                rec = {"q_cat": q_cat, "id": qid, "document": (got.get("documents") or [])[i],
                       "metadata": (got.get("metadatas") or [])[i],
                       "embedding": base64.b64encode(np.asarray(emb, dtype="float32").tobytes()).decode()}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    print(f"내보냄: {path} ({n}개)")
    return n


# ---------------- 그래프 1-A가 쓰는 검색 ----------------
def search_questions(vector, q_cat, n, used=(), role_code=None, min_sim=None, forbid_terms=None, k=None):
    """사내문서 질문 컬렉션에서 후보 n개. `question_graph._pick_redis`와 **같은 모양**을 돌려준다.

    vector: 벡터 1개 또는 벡터 목록(Multi-Query면 목록 → 질문 ID 기준 RRF로 합친다).
    반환: [{text, doc_id, iq_id, subcategory, competency, dup_group, sim, chunk_id, doc_refs, ...}] 또는 None(부족).
    안전: 돌아온 질문의 `disclosure`·`status`를 다시 확인하고(B-09), 금지 용어가 든 질문은 버린다.
    """
    min_sim = config.RAG_IQ_MIN_SIM if min_sim is None else min_sim
    depth = k or max(16, n * 6)
    vectors = vector if (vector and isinstance(vector[0], (list, tuple))) else [vector]
    try:
        if len(vectors) == 1:
            hits = internal_store.query_questions(vectors[0], q_cat, k=depth)
        else:
            from . import rag_fusion
            res = rag_fusion.parallel(lambda v: internal_store.query_questions(v, q_cat, k=depth),
                                      [(v,) for v in vectors])
            lists = [r for ok, r in res if ok]
            if not lists:
                return None
            hits = rag_fusion.rrf(lists, key=lambda h: h["id"], k=config.RAG_RRF_K, limit=depth)
    except Exception:  # noqa: BLE001 — 컬렉션이 없거나 Chroma 오류 → 호출 쪽이 Redis로 내려간다
        return None

    pool, seen = [], set(used)
    for h in hits:
        m = h["metadata"] or {}
        text = (h.get("document") or m.get("question") or "").strip()
        if m.get("q_cat") != q_cat:
            return None                                   # 컬렉션이 섞였다 → 쓰지 않는다
        if m.get("disclosure") != "public" or m.get("status") not in internal_search.ALLOWED_STATUS:
            return None                                   # B-09 사후 재확인
        if float(h.get("sim") or 0) < min_sim:
            continue
        if not text or not (MIN_LEN <= len(text) <= MAX_LEN) or not _one_sentence(text):
            continue
        if _blocklist().search(text):
            continue
        if forbid_terms and (internal_forbid.find_terms(text, forbid_terms) or internal_forbid.masked_hits(text)):
            continue
        group = (m.get("competency") or "").strip() or m.get("doc_id") or h["id"]
        if group in seen:
            continue
        # 2026-11-19 사용자 지시(전제조건): **공고·직무와 무관한 질문은 아예 나오면 안 된다.**
        # 전에는 직무 일치를 +0.05 가산점으로만 썼는데, 유사도만 높으면 무관한 질문이 그대로 1번으로 뽑혔다
        # (AI/AX 공고에 "공개 저장소의 커밋 기록으로 직무 역량 평가" 질문이 나온 사고). → 직무 일치를 먼저 본다.
        on_role = bool(role_code and m.get(f"dept_{role_code}"))
        score = float(h.get("sim") or 0) + (0.05 if on_role else 0.0)
        seen.add(group)
        pool.append({"id": None, "on_role": on_role, "doc_id": m.get("doc_id") or "", "iq_id": h["id"], "text": text,
                     "subcategory": m.get("intent") or m.get("competency") or "", "intent": m.get("intent") or "",
                     "competency": m.get("competency") or "", "dup_group": group, "sim": round(score, 4),
                     "chunk_id": m.get("chunk_id") or "", "sec": m.get("sec") or "",
                     "doc_refs": internal_qcat.unpipe(m.get("doc_refs")),
                     "company_facts": internal_qcat.unpipe(m.get("company_facts")),
                     "model_answer": m.get("model_answer") or "",
                     "expected_keywords": m.get("expected_keywords") or ""})
        if len(pool) >= n * 4:                            # 직무 일치분을 고르려면 후보를 더 모아야 한다
            break
    # 2026-11-19 사용자 지시 — 공고·직무 일치가 **전제조건**이다.
    # 직무가 맞는 후보가 n개 이상이면 그 안에서만 고른다. 모자랄 때만 나머지로 채우고,
    # 그마저 부족하면 None을 돌려 Redis·DB 은행(백업 층)으로 내려보낸다.
    on = [b for b in pool if b.get("on_role")]
    off = [b for b in pool if not b.get("on_role")]
    if role_code and len(on) >= n:
        pool = on
    elif role_code and on:
        off.sort(key=lambda b: -b["sim"])
        pool = on + off[: n - len(on)]
    if len(pool) < n:
        return None                                       # 모자라면 Redis·DB 은행으로(백업 층)
    keep = set(random.sample(range(len(pool)), n))
    return [b for i, b in enumerate(pool) if i in keep]


def ready():
    """질문 컬렉션이 쓸 만한가 — (True/False, 사유). 그래프 1-A가 분기 로그에 남긴다."""
    if not config.INTERNAL_DOCS_RAG:
        return False, "internal_docs_rag_off"
    if config.RAG_QUESTION_SOURCE != "chroma":
        return False, f"source={config.RAG_QUESTION_SOURCE}"
    try:
        ok, why = internal_forbid.rag_ready()
    except Exception as e:  # noqa: BLE001
        return False, f"forbid_{type(e).__name__}"
    if not ok:
        return False, why
    try:
        n = sum(internal_store.count_questions(c) for c in CATS)
    except Exception as e:  # noqa: BLE001
        return False, f"store_{type(e).__name__}"
    return (n > 0), ("empty" if n == 0 else f"{n}questions")


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "sync"):
        s = sub.add_parser(name)
        s.add_argument("--cats", nargs="*", default=list(CATS), choices=list(CATS))
        s.add_argument("--per-doc", type=int, default=PER_DOC)
        s.add_argument("--limit", type=int, default=None, help="카테고리마다 조각 수 상한(시험용)")
        s.add_argument("--n", type=int, default=N_PER_CHUNK, help="조각 1개에서 만들 질문 수")
        s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("prune")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats")
    sub.add_parser("ready")
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd in ("generate", "sync"):
        rep = run_generate(cats=a.cats, per_doc=a.per_doc, limit=a.limit, dry=a.dry_run,
                           only_changed=(a.cmd == "sync"), n=a.n)
        if not a.dry_run:
            run_prune(dry=False)
        print("\n" + json.dumps(rep, ensure_ascii=False, indent=1))
    elif a.cmd == "prune":
        run_prune(dry=a.dry_run)
    elif a.cmd == "stats":
        print(json.dumps(run_stats(), ensure_ascii=False, indent=1))
    elif a.cmd == "ready":
        print(json.dumps(dict(zip(("ok", "why"), ready())), ensure_ascii=False))
    else:
        run_export(a.out)


if __name__ == "__main__":
    sys.exit(main())
