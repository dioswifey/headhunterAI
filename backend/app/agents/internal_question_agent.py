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
    python -m app.agents.internal_question_agent retag [--dry-run]   # 조각 메타(dept 태그 등)만 질문에 다시 복사
    python -m app.agents.internal_question_agent export --out /tmp/iq     # 배포 사본(JSONL + 벡터)
호스트에서도 돈다(DB 불필요, Ollama HTTP만): cd backend && EMBED_BASE_URL=http://localhost:11434 python -m app.agents.internal_question_agent stats
"""
import argparse
import base64
import hashlib
import json
import logging
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import config, internal_store, llm, prompts  # noqa: E402
from app.agents import internal_forbid, internal_qcat, internal_search  # noqa: E402

log = logging.getLogger(__name__)   # 2026-11-20: 전에는 정의가 없어 OpenAI 실패 → 로컬 대체 경로가 NameError로 멈췄다

CATS = internal_qcat.CATS
PER_DOC = 5                # 문서마다 질문을 만들 조각 수(1차 범위, 6.19.4)
N_PER_CHUNK = 2             # 조각 1개에서 만들 질문 수 상한
DUP_SIM = 0.90              # 같은 카테고리 안 중복 판정(코사인)
MIN_LEN, MAX_LEN = 10, 160
MAX_LEN_WITH_SUFFIX = 260   # 2026-11-20 F1: 허용 덧붙임(경험 안내 · 답변 형식 예시)까지 합친 상한
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
    "personality": "조각에 적힌 행동 기준·가치가 드러나는 구체적인 업무 장면을 '{회사}에서 ~를 맡는다면 / 진행한다고 가정할 때'로 주고, 지원자 본인이라면 어떻게 판단·행동할지 묻는다. "
                   "질문 끝에 **답변 형식 예시 1개**를 덧붙인다 — \"예를 들어 '~했고, 그 결과 ~가 이렇게 달라졌다'처럼 설명해 주세요.\" "
                   "(답의 모양만 보여 주고, 조각에 없는 수치·제품명은 넣지 않는다 · 2026-11-20 사용자 응시 피드백 F1). "
                   "'이 회사의 기준'처럼 내부 규정을 안다고 전제하지 않고, 사내 규정·절차를 맞히게 하는 질문(휴가·근태·결재 규칙 등)은 만들지 않는다.",
    "job": "업무 상황을 주고 어떻게 판단·처리할지 묻는다. 조각에 적힌 기술·절차는 배경으로만 쓰고, 지원자가 그 시스템을 이미 안다고 전제하지 않는다.",
    # 2026-11-23 사용자 지적 F13: 예전 지침은 "내부 수치·고객사 이름은 쓰지 않는다"로 끝나 **금지만** 있었다.
    # 인성·직무에는 '구체적 업무 장면'·'업무 상황을 주고'라는 구체성 요구가 있는데 기업만 없어, 모델이 안전하게
    # 추상화했다(실측: 추상 명사구가 기업 18% vs 인성·직무 5% · 영문·숫자 토큰 0개가 기업 84%).
    # 사용자 지적 원문: "'검증 결과가 누락되지 않도록 기록하는 업무'? 이거 너무 추상적이잖아. 무슨 검증을 하는
    # 건데? 무슨 AI 모델을 배포하는지 제품과 서비스에 대한 아무런 앞뒤 정보가 없잖아."
    "company": "회사의 사업·고객·일하는 방식에 대한 이해와 지원 동기가 드러나게 묻는다. "
               "**조각에 적힌 구체적인 대상을 질문에 그대로 남긴다** — 업무 대상(어떤 시스템·어떤 제품·어떤 문서), "
               "산출물(무엇을 만들거나 남기는지), 절차·단계 이름, 공개된 제도·인증 이름. 그 대상이 없으면 질문이 "
               "'~를 관리하는 업무'처럼 뭉개져 지원자가 무엇을 답해야 할지 알 수 없다. "
               "쓰지 않는 것은 **고객사 이름 · 내부 금액 · 사건 번호**뿐이다(그 밖의 사실은 조각에 적힌 대로 쓴다). "
               "추상 명사구를 겹쳐 쓰지 않는다 — '~가 누락되지 않도록 기록하는 업무를 맡는다면, ~를 남기는 업무 방식의 "
               "어떤 점이'처럼 '~하는 ○○' 꼴을 두 번 이으면 읽기 어렵다. 한 번만 쓰고 나머지는 구체적인 이름으로 바꾼다.",
}

# 지원자가 이 회사에서 일했다고 전제하는 과거형 표현(A18 ⑦). `questions.PAST_PREMISE`와 같은 목적
PAST_PREMISE = re.compile(r"(하셨던|하셨는|하신\s*경험|진행하신|진행하셨|담당하신|담당하셨|맡으셨|맡았던|"
                          r"참여하신|참여하셨|수행하신|수행하셨|경험하신|구축하신|구축하셨|개발하신|개발하셨)")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|\d+(?:[:/]\d+)+|\d+(?:\.\d+)?%?")
SENT_END = re.compile(r"(?:[다요까]|니까)[.?!]\s*(?=\S)")

# A18 문장 규칙 — 2026-11-20 `question_rules`로 옮겼다(이력서 질문·꼬리질문도 같은 규칙을 쓴다).
# 이름은 그대로 다시 내보낸다(시험·측정 도구가 IQ.<이름>으로 부른다). 상세·결함 수리 내역은 question_rules 머리말.
from app.agents import fewshot_guard, question_rules as QR  # noqa: E402

EXPERIENCE_SUFFIX = QR.EXPERIENCE_SUFFIX
EXAMPLE_SUFFIX = QR.EXAMPLE_SUFFIX
COMPANY_PAST = QR.COMPANY_PAST
ABSTRACT_EXPERIENCE = QR.ABSTRACT_EXPERIENCE
COMPANY_SLOT = "{회사}"     # 생성 질문 안의 회사 자리. 면접 때 question_graph._internal_draft가 지원 회사 이름으로 바꾼다(F1·F3)
ASK_END = QR.ASK_END
MULTI_INTERROG = QR.MULTI_INTERROG
DANGLING_REF = QR.DANGLING_REF
QUOTED_PHRASE = QR.QUOTED_PHRASE
QUOTED_SENTENCE_END = QR.QUOTED_SENTENCE_END
FRAGMENT_COLON = QR.FRAGMENT_COLON
HOUSE_PREMISE = QR.HOUSE_PREMISE
SITUATION_MARKERS = QR.SITUATION_MARKERS
ABSTRACT_HEAD = QR.ABSTRACT_HEAD
TWO_CONCEPTS = QR.TWO_CONCEPTS
BARE_ASK = QR.BARE_ASK
has_malformed_phrasing = QR.has_malformed_phrasing
sentence_shape = QR.sentence_shape

# 인성 질문 재료 = 가치·태도 문서만(2026-11-20 리드 결정). HR·근태·평가 규정, 서류전형·회의 규칙, 품의 사례집,
# 고객사 평가 기준은 빼다 — 2026-11-20 재검증에서 "반차와 반반차를 동시에 사용할 때 연차 차감 규칙이…" 같은
# **사내 규정 퀴즈**가 인성 풀의 41%였고, 사고 1(커밋 기록, MSK-06 서류전형)·사고 2(MSK-53 데이터 준비도)도 여기서 나왔다.
PERSONALITY_DOC_TYPES = {"talent"}
PER_DOC_BY_CAT = {"personality": 10}
# 면접관이 지원자를 '어떻게 평가할지' 적은 문서 — 여기서 만든 질문은 평가 기준을 지원자 상황으로 뒤집어 말이 안 된다.
# 2026-11-20 사용자 지적: "내부 도구 개발을 맡는다면, 경험하지 않은 기술에 대해 동료가 판단을 요청할 때…"
# (출처 MSK-41 '인재상 면접 평가 루브릭' > '"모른다"는 답변의 처리' — 면접관이 '모른다' 답변을 채점하는 규칙)
INTERVIEWER_DOC = re.compile(r"루브릭|면접\s*평가|평가\s*기준|서류전형|채용\s*가이드|면접관")   # 인성 재료 문서가 6개로 줄어 문서당 조각을 더 쓴다

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
    # 2026-11-20: 허용 덧붙임("관련된 경험이 있다면 ~ 답해 주셔도 좋습니다")은 떼고 센다. 지시문은 이 덧붙임을
    # '한 문장 규칙의 예외'라고 했는데 검사는 떼지 않아, 덧붙인 질문이 전부 not_one_sentence로 떨어졌다.
    # 같은 날 F1: 본질문의 답변 형식 예시("예를 들어 '…'처럼 설명해 주세요")도 허용 덧붙임이다(QR.core가 둘 다 뗀다).
    return not SENT_END.search(QR.core(q))


def _length_ok(q):
    """본문(덧붙임을 뗀 것)은 10~160자, 덧붙임까지 합쳐도 260자 이하(음성으로 읽어 줄 수 있는 길이)."""
    return MIN_LEN <= len(QR.core(q)) <= MAX_LEN and len((q or "").strip()) <= MAX_LEN_WITH_SUFFIX


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
        if q_cat == "personality" and m.get("doc_type") not in PERSONALITY_DOC_TYPES:
            continue
        if INTERVIEWER_DOC.search(m.get("doc_title") or ""):
            continue                             # 면접관용 문서(평가 루브릭 등)는 지원자 질문 재료가 아니다(2026-11-20 사용자 지적)                             # 규정·절차 문서는 인성 질문 재료가 아니다(규정 퀴즈 방지, 2026-11-20)
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
    # 2026-11-20: 어느 모델이 만들었는지를 usage["gen_model"]에 남긴다(`openai:<모델>` / `local:<모델>`).
    # 전에는 저장 때 항상 LLM_MODEL을 적어, OpenAI로 만든 질문과 로컬 대체분을 나중에 가를 수 없었다.
    usage = None
    if config.IQ_GEN_PROVIDER == "openai":
        try:
            out, usage = llm.openai_chat_json(system, "위 조각으로 질문을 만들어라.", GEN_SCHEMA,
                                               timeout=120, temperature=0.4)
            usage = {**(usage or {}), "gen_model": "openai:" + str((usage or {}).get("model") or config.OPENAI_MODEL)}
        except Exception as e:  # noqa: BLE001 — 키 없음·한도·오류 → 로컬로 내려간다(생성이 멈추지 않게)
            log.warning("A17 OpenAI 생성 실패(%s) → 로컬 모델로 대체", e)
            # 2026-11-24(컨텍스트 엔지니어링 ③): 지시문 5,493자 + 조각 3,000자 = 약 8.5k자로 창 기본값(6144토큰)을
            # 넘긴다 → 로컬 대체 경로에서만 조각 뒷부분이 조용히 잘렸다. 창을 명시한다.
            out = llm.chat_json(system, "위 조각으로 질문을 만들어라.", GEN_SCHEMA, timeout=120, temperature=0.4,
                                num_ctx=8192)
            usage = {"gen_model": "local:" + config.LLM_MODEL, "fallback": type(e).__name__}
    else:
        out = llm.chat_json(system, "위 조각으로 질문을 만들어라.", GEN_SCHEMA, timeout=120, temperature=0.4,
                            num_ctx=8192)   # 위와 같은 이유(2026-11-24 ③)
        usage = {"gen_model": "local:" + config.LLM_MODEL}
    return (out.get("questions") or [])[:n], usage


# ---------------- 3 validate (A18) ----------------
# has_malformed_phrasing()은 question_rules로 옮겼다(위에서 다시 내보냄). 2026-11-20부터 6종:
# multi_ask · multi_interrog · dangling_ref · fragment_colon · house_premise · quoted_internal_term
def validate(cand, chunk, q_cat, terms):
    """(ok, reason). **탈락 사유를 반드시 돌려준다** — A15가 사유를 남기지 않아 원인을 못 찾은 일(QO-13)을 반복하지 않는다."""
    q = (cand.get("question") or "").strip()
    facts = [f.strip() for f in (cand.get("facts") or []) if f and f.strip()]
    body = chunk["body"]
    if not q:
        return False, "empty"
    if not _length_ok(q):
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
    # ⑨(2026-11-23 사용자 승인): 의문사로 묻고 명령형·청유형으로 맺는 문장(호응 깨짐).
    # 지금 풀 602개에는 해당 문장이 없지만(전수 확인), 다음 생성·sync에서 나오면 여기서 걸린다.
    if QR.malformed_ask(q):
        return False, "malformed_ask"
    # F11(2026-11-23 사용자 지적): A17 질문은 회사 자리가 `{회사}`다 — 그 자리와 '다시'가 함께 오면
    # 면접 때 회사 이름으로 치환되는 순간 "그 회사에서 예전에 해 봤다"는 뜻이 된다.
    if QR.company_again(q, "{회사}"):
        return False, "company_again"
    # F13(2026-11-23 사용자 지적): 추상 명사구를 겹쳐 쓴 문장("~하는 업무를 맡는다면, ~하는 업무 방식의 어떤 점이").
    # 한 번 쓰는 것은 정상이고 **두 번 이상 겹칠 때만** 걸린다(풀 602개 전수 13건 — 전부 기업 질문).
    if QR.nested_abstract(q):
        return False, "nested_abstract"
    allowed = _squash(body)
    if not all(_squash(f) in allowed for f in facts):
        return False, "facts_not_grounded"
    bad = sorted(t for t in set(TOKEN_RE.findall(q)) if _squash(t.strip(".")) not in allowed)
    if bad:
        return False, "bad_tokens:" + ",".join(bad[:4])   # 조각에 없는 영문·숫자 토큰 = 지어낸 것
    leak = fewshot_guard.leak_terms(q, body)
    if leak:
        return False, "fewshot_leak:" + ",".join(leak[:3])   # 지시문 예문에만 있는 말이 근거 없이 들어옴(2026-11-20)
    body_q = QR.core(q)                                  # 상황·구체성·문장 틀은 덧붙임(예시 따옴표 등)을 뗀 본문으로 본다
    if not SITUATION_MARKERS.search(body_q):
        return False, "no_situation"                     # 금지·조작 검사는 통과했지만 구체적 업무 상황 없이 조각 머리글·메타 설명만 베낀 질문
    concrete, why = has_concrete_anchor(body_q)
    if not concrete:
        return False, "template_only:" + (why or "")[:30]  # "다면" 어미만 있고 알맹이(숫자·인용·구체 명사) 없는 질문
    shaped, why2 = sentence_shape(q)
    if not shaped:
        return False, "shape:" + why2                     # 추상 범주 상황 · 개념 두 개 묶기 · 목적어 없는 맺음
    if QR.ABSTRACT_EXPERIENCE.search(body_q):
        return False, "abstract_experience"              # F2: 장면 없이 '~했던 경험'만 묻는다(가정형 구체 장면이어야 한다)
    return True, ""


# 문장 틀 검사(ABSTRACT_HEAD · TWO_CONCEPTS · BARE_ASK)와 sentence_shape()는 question_rules로 옮겼다(위에서 다시 내보냄).


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
    # 2026-11-23(0단계 1): 직무 목록을 적재 쪽 한 곳에서 가져온다 — 전에 5개를 여기에 또 적어 둬서,
    # `internal_docs_agent.DEPTS`에 직무를 더해도 **질문 메타에는 그 플래그가 안 실렸다**(관문 1이 계속 실패).
    from .internal_docs_agent import DEPTS as _DEPTS
    for d in _DEPTS:
        meta[f"dept_{d}"] = bool(m.get(f"dept_{d}"))
    return meta


def _qid(q_cat, chunk_id, i):
    return f"IQ-{q_cat[:4]}-{hashlib.sha256(chunk_id.encode()).hexdigest()[:10]}-{i}"


def _gen_jobs(chunks, q_cat, n, workers):
    """조각마다 (조각, 결과 또는 예외)를 **조각 순서대로** 돌려준다. workers > 1이면 생성 호출만 병렬(OpenAI 왕복 대기).
    검증·임베딩·저장은 호출 쪽에서 차례로 한다(임베딩은 로컬 Ollama 하나라 병렬로 해도 빨라지지 않는다)."""
    def one(c):
        try:
            return generate_for_chunk(c, q_cat, n=n)
        except Exception as e:  # noqa: BLE001 — 조각 하나의 실패가 전체를 멈추지 않게
            return e
    if workers <= 1:
        for c in chunks:
            yield c, one(c)
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, r in zip(chunks, ex.map(one, chunks)):
            yield c, r


def run_generate(cats=CATS, per_doc=None, limit=None, dry=False, only_changed=False, n=N_PER_CHUNK,
                 workers=1, replace=False, report_out=None):
    """A17 생성 → A18 검증 → 중복 제거 → 저장.
    per_doc: None이면 카테고리별 기본값(`PER_DOC_BY_CAT`, 없으면 PER_DOC).
    replace: 카테고리 전체를 새로 만든 결과로 **교체**한다 — 이번에 만들지 않은 옛 질문(재료에서 빠진 문서의 질문 포함)을
             지운다. 새 질문을 먼저 저장한 뒤 지우므로 교체 중에도 컬렉션이 비지 않는다. limit·sync와 같이 쓰지 않는다.
    report_out: 탈락 사유와 문장 전체를 JSON으로 남길 경로(감사용, 사람이 탈락 목록을 다시 볼 수 있게)."""
    try:
        terms = internal_forbid.load_terms()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"금지 용어 사전을 읽을 수 없습니다({type(e).__name__})")
    if replace and (limit or only_changed):
        raise SystemExit("replace는 전체 생성에서만 쓴다(limit·sync와 같이 쓰지 않는다)")
    report = {"spec": SPEC, "provider": config.IQ_GEN_PROVIDER, "cats": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    cost = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    detail = {"accepted": [], "rejected": []}
    for q_cat in cats:
        pd = per_doc or PER_DOC_BY_CAT.get(q_cat, PER_DOC)
        chunks = select_chunks(q_cat, per_doc=pd, limit=limit)
        if only_changed:
            have = internal_store.question_chunk_hashes(q_cat)
            chunks = [c for c in chunks if have.get(c["chunk_id"]) != chunk_hash(c["body"])]
        rep = {"chunks": len(chunks), "per_doc": pd, "generated": 0, "accepted": 0, "rejected": {}, "dup": 0,
               "llm_error": 0, "gen_model": {}, "removed_old": 0}
        report["cats"][q_cat] = rep
        print(f"\n[{q_cat}] 대상 조각 {len(chunks)}개 (문서당 {pd})" + (" (--dry-run)" if dry else ""))
        if dry or not chunks:
            docs = sorted({c["meta"].get("doc_id", "") for c in chunks})
            print(f"   문서 {len(docs)}개: {', '.join(docs[:40])}")
            for c in chunks[:5]:
                print(f"   {c['chunk_id']:<28} {c['meta'].get('doc_title', '')[:30]} > {c['meta'].get('sec_title', '')[:24]}")
            continue
        kept, kept_vecs = [], []
        t0 = time.time()
        for i, (c, res) in enumerate(_gen_jobs(chunks, q_cat, n, workers), 1):
            if isinstance(res, Exception):
                rep["llm_error"] += 1
                print(f"  [{i}/{len(chunks)}] {c['chunk_id']} 모델 실패({type(res).__name__}: {str(res)[:80]}) — 건너뜀")
                continue
            cands, usage = res
            usage = usage or {}
            if usage.get("prompt_tokens") is not None:
                cost["calls"] += 1
                cost["prompt_tokens"] += usage.get("prompt_tokens", 0)
                cost["completion_tokens"] += usage.get("completion_tokens", 0)
            gm = usage.get("gen_model") or ("local:" + config.LLM_MODEL)
            rep["gen_model"][gm] = rep["gen_model"].get(gm, 0) + 1
            rep["generated"] += len(cands)
            ok_here = []
            for cand in cands:
                ok, why = validate(cand, c, q_cat, terms)
                if not ok:
                    key = why.split(":")[0]
                    rep["rejected"][key] = rep["rejected"].get(key, 0) + 1
                    detail["rejected"].append({"q_cat": q_cat, "chunk_id": c["chunk_id"], "gen_model": gm,
                                               "reason": why, "question": (cand.get("question") or "").strip()})
                    continue
                cand["_gen_model"] = gm
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
        if not replace:
            for chunk_id in {c["chunk_id"] for _, c, _ in kept}:
                internal_store.delete_questions_of_chunk(q_cat, chunk_id)
        per_chunk = {}
        ids, docs_, metas, vecs2 = [], [], [], []
        for cand, c, v in kept:
            k = per_chunk.get(c["chunk_id"], 0)
            per_chunk[c["chunk_id"]] = k + 1
            ids.append(_qid(q_cat, c["chunk_id"], k))
            docs_.append(cand["question"].strip())
            metas.append(_meta_of(cand, c, q_cat, cand.get("_gen_model") or ("local:" + config.LLM_MODEL)))
            vecs2.append(v)
            detail["accepted"].append({"q_cat": q_cat, "id": ids[-1], "chunk_id": c["chunk_id"],
                                       "gen_model": metas[-1]["gen_model"], "question": docs_[-1]})
        if ids:
            internal_store.upsert_questions(q_cat, ids, docs_, metas, vecs2)
        if replace and ids:                      # 새 질문을 다 넣은 뒤 이번에 만들지 않은 옛 질문만 지운다
            new = set(ids)
            old = [r["id"] for r in internal_store.questions_all(q_cat, documents=False) if r["id"] not in new]
            if old:
                internal_store.question_collection(q_cat).delete(ids=old)
            rep["removed_old"] = len(old)
        rep["accepted"] = len(ids)
        print(f"[{q_cat}] 저장 {len(ids)}개 · 중복 {rep['dup']} · 탈락 {sum(rep['rejected'].values())} "
              f"{rep['rejected']} · 모델 실패 {rep['llm_error']} · 생성 모델 {rep['gen_model']}"
              + (f" · 옛 질문 삭제 {rep['removed_old']}" if replace else ""))
    cost["cost_usd"] = round(cost["prompt_tokens"] / 1e6 * config.OPENAI_PRICE_IN_PER_M
                             + cost["completion_tokens"] / 1e6 * config.OPENAI_PRICE_OUT_PER_M, 4)
    report["cost"] = cost
    if report_out:
        os.makedirs(os.path.dirname(report_out) or ".", exist_ok=True)
        with open(report_out, "w", encoding="utf-8") as f:
            json.dump({**report, **detail}, f, ensure_ascii=False, indent=1)
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


# ---------------- retag (2026-11-23 사용자 승인, 0단계) ----------------
def run_retag(dry=False):
    """저장된 질문의 **조각 유래 메타만** 원본 조각에서 다시 복사한다(모델·임베딩 호출 0회).

    왜 필요한가: 관문 1(직무 일치)은 **질문 메타**의 `dept_<직무>`를 본다(`search_questions`). 그 값은 질문을
    만들 때 조각 메타에서 복사되는데(`_meta_of`), `sync`는 **조각 본문 해시**로만 다시 만들 대상을 고르므로
    (`run_generate`의 `only_changed`) 조각의 **메타만** 바뀐 경우(예: 문서에 dept 태그를 더한 0단계 2)에는
    아무것도 다시 만들지 않는다 → 질문의 직무 태그가 옛 값으로 남는다.
    질문 문장·벡터·근거는 건드리지 않는다 — 바꾸는 것은 dept_* · doc_type · dept · ct_band · status · disclosure뿐이다.
    """
    from app.agents.internal_docs_agent import DEPTS
    chunks = {c["id"]: c["metadata"] for c in internal_store.chunks(internal_store.PUBLIC)}
    keys = [f"dept_{d}" for d in DEPTS] + ["doc_type", "dept", "ct_band", "status", "disclosure",
                                           "doc_title", "sec_title", "sec"]
    total = {"checked": 0, "updated": 0, "chunk_gone": 0}
    for q_cat in CATS:
        rows = internal_store.questions_all(q_cat, documents=False)
        ids, metas, changed_keys = [], [], {}
        for r in rows:
            total["checked"] += 1
            m = dict(r["metadata"] or {})
            cm = chunks.get(m.get("chunk_id") or "")
            if cm is None:
                total["chunk_gone"] += 1          # prune 대상(조각이 사라졌다)
                continue
            new = dict(m)
            for k in keys:
                v = cm.get(k)
                new[k] = bool(v) if k.startswith("dept_") else (v if v is not None else m.get(k))
            diff = [k for k in keys if new.get(k) != m.get(k)]
            if diff:
                for k in diff:
                    changed_keys[k] = changed_keys.get(k, 0) + 1
                ids.append(r["id"])
                metas.append(new)
        print(f"  [{q_cat}] 질문 {len(rows)} · 갱신 {len(ids)} {json.dumps(changed_keys, ensure_ascii=False)}"
              + (" (--dry-run)" if dry else ""))
        if ids and not dry:
            internal_store.question_collection(q_cat).update(ids=ids, metadatas=metas)
        total["updated"] += len(ids)
    print(f"retag: 확인 {total['checked']} · 갱신 {total['updated']} · 조각 사라짐 {total['chunk_gone']}")
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
        if not text or not _length_ok(text) or not _one_sentence(text):
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
        raw = float(h.get("sim") or 0)
        score = raw + (0.05 if on_role else 0.0)
        seen.add(group)
        pool.append({"id": None, "on_role": on_role, "doc_id": m.get("doc_id") or "", "iq_id": h["id"], "text": text,
                     "subcategory": m.get("intent") or m.get("competency") or "", "intent": m.get("intent") or "",
                     # 2026-11-23 ⑧-2: `sim`은 직무 가산점이 **더해진** 값이고, 하한(`min_sim`)은 **가산점 전
                     # 원시값**에 건다. 두 값이 달라 "0.648인데 왜 0.60 하한에 걸리지?"(실제 원시값 0.598)처럼
                     # 추적이 어려웠다 — 기준은 그대로 두고 `raw_sim`을 같이 실어 구분할 수 있게 한다.
                     "competency": m.get("competency") or "", "dup_group": group, "sim": round(score, 4),
                     "raw_sim": round(raw, 4), "role_bonus": round(score - raw, 4),
                     "chunk_id": m.get("chunk_id") or "", "sec": m.get("sec") or "", "doc_title": m.get("doc_title") or "",
                     # 2026-11-23 F13: 관리 문서(회계·결재·예산) 출신 질문을 재순위에서 뒤로 보내려면 호출 쪽이
                     # `doc_type`을 알아야 한다(`question_graph.DEMOTED_TYPES`). 거르기용이 아니라 정렬용이다.
                     "doc_type": m.get("doc_type") or "",
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
        s.add_argument("--per-doc", type=int, default=None, help="문서당 조각 수(기본: 카테고리별 PER_DOC_BY_CAT, 없으면 5)")
        s.add_argument("--limit", type=int, default=None, help="카테고리마다 조각 수 상한(시험용)")
        s.add_argument("--n", type=int, default=N_PER_CHUNK, help="조각 1개에서 만들 질문 수")
        s.add_argument("--workers", type=int, default=1, help="생성 호출 병렬 수(OpenAI일 때만 의미 있음)")
        s.add_argument("--replace", action="store_true", help="카테고리 전체를 새 결과로 교체(옛 질문 삭제)")
        s.add_argument("--report-out", default=None, help="탈락 사유·문장 전체를 남길 JSON 경로")
        s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("prune")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("retag")      # 조각 메타(dept 태그 등)만 질문에 다시 복사(2026-11-23 0단계)
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats")
    sub.add_parser("ready")
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if a.cmd in ("generate", "sync"):
        rep = run_generate(cats=a.cats, per_doc=a.per_doc, limit=a.limit, dry=a.dry_run,
                           only_changed=(a.cmd == "sync"), n=a.n, workers=a.workers,
                           replace=(a.replace and a.cmd == "generate"), report_out=a.report_out)
        if not a.dry_run:
            run_prune(dry=False)
        print("\n" + json.dumps(rep, ensure_ascii=False, indent=1))
    elif a.cmd == "prune":
        run_prune(dry=a.dry_run)
    elif a.cmd == "retag":
        run_retag(dry=a.dry_run)
    elif a.cmd == "stats":
        print(json.dumps(run_stats(), ensure_ascii=False, indent=1))
    elif a.cmd == "ready":
        print(json.dumps(dict(zip(("ok", "why"), ready())), ensure_ascii=False))
    else:
        run_export(a.out)


if __name__ == "__main__":
    sys.exit(main())
