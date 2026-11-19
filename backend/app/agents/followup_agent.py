"""A3 꼬리질문 에이전트 — 그래프 1-B 노드 `judge_followup`이 쓰는 판단 단위 (ADLC 6.1-B, `ADLC/followup/`).

2026-11-17 분리: 이 로직은 2026-11-09부터 `question_graph`의 함수로 그래프 1-B 안에서 돌고 있었으나
**전용 모듈이 아니었고 프롬프트도 코드 문자열**이었다(`prompts/evaluate/followup_judge.md`는 어떤 코드도
읽지 않는 죽은 파일이었다). 이번 분리의 범위는 **구조와 프롬프트 위치만**이고 판단 동작은 그대로다:
  - 노드 계약 유지: `judge(att, q, answer) → (꼬리질문, 사유) | None`
  - 프롬프트를 `prompts/evaluate/followup_judge.md`(+ `_ideal.md`)에서 읽는다 → 사용자가 파일로 고칠 수 있다
  - `question_graph`는 같은 이름(`PRE_SYSTEM`·`PRE_SCHEMA`·`judge_detail`·`judge`)을 그대로 다시 내보낸다
    → `tools/eval_followup.py` · `tools/sim_applicants.py` · `tools/test_graph1b_loop.py`가 수정 없이 돈다
판단 기준 자체를 바꾸는 일(사내문서 연계·금지 용어 검사·새 컨셉)은 `ADLC/followup/README.md` '할 일 1'의
사용자 결정이 먼저다 — 이 파일에서 하지 않았다.

판단 순서 (INT-06·07)
  ① 생략 규칙: 이미 꼬리질문 / 전체 상한(MAX_FOLLOWUPS) 소진 / 내용 없음 / 5자 미만
  → 모델 선평가(`FOLLOWUP_TIMEOUT` 안, 로컬 Qwen)
  ② 기대 키워드 0개 · ③ 모범 답변 대비 구체성 ≤ SPECIFICITY_MAX · ④ 주제 이탈·논리/관련성 ≤2 · 이력서와 다름 · 일반론
  → 문장 검사(차단 목록 · 150자) → 힌트 금지(B-07)
②는 코드가 정하지만 꼬리질문 문장은 모델이 만들므로, 모델이 실패·시간 초과면 ②가 걸려도 생략한다.
"""
import logging

from .. import config, llm, prompts

log = logging.getLogger(__name__)

PRE_SCHEMA = {"type": "object", "properties": {
    "question_topic": {"type": "string"}, "answer_topic": {"type": "string"}, "addresses_question": {"type": "boolean"},
    "logic": {"type": "integer", "minimum": 1, "maximum": 5}, "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
    "inconsistent_with_resume": {"type": "boolean"}, "generic_only": {"type": "boolean"}, "followup_question": {"type": "string"}},
    "required": ["question_topic", "answer_topic", "addresses_question", "logic", "relevance", "inconsistent_with_resume",
                 "generic_only", "followup_question"]}
# 판단 ③(ADLC 6.1-B): 은행 문항에 모범 답변 요지(model_answer)가 있을 때만 구체성 점수를 더 묻는다.
# 모범 답변이 없으면 PRE_SYSTEM·PRE_SCHEMA를 그대로 써서 기존 동작(eval_followup 기준선)과 같다.
PRE_SCHEMA_IDEAL = {**PRE_SCHEMA, "properties": {**PRE_SCHEMA["properties"], "specificity": {"type": "integer", "minimum": 1, "maximum": 5}},
                    "required": PRE_SCHEMA["required"] + ["specificity"]}
SPECIFICITY_MAX = 2          # ③ 구체성 이 점수 이하 → 꼬리질문
MAX_FOLLOWUP_CHARS = 150     # 꼬리질문 문장 길이 상한

# 프롬프트는 불러올 때 한 번만 읽어 **같은 객체**로 들고 있는다(어떤 프롬프트를 보냈는지 시험이 식별로 확인한다).
# 파일을 읽지 못하면(지우거나 중괄호를 잘못 넣어 깨진 경우) 꼬리질문만 쉬고 면접은 계속 간다 — 노드가 죽지 않는다.
try:
    PRE_SYSTEM = prompts.load("evaluate/followup_judge").rstrip("\n")
    PRE_SYSTEM_IDEAL = PRE_SYSTEM + "\n" + prompts.load("evaluate/followup_judge_ideal").rstrip("\n")
    PROMPT_ERROR = None
except Exception as e:  # noqa: BLE001 — 파일 없음·형식 오류 등
    PRE_SYSTEM = PRE_SYSTEM_IDEAL = None
    PROMPT_ERROR = f"{type(e).__name__}: {e}"
    log.error("꼬리질문 프롬프트를 읽지 못했습니다(%s) — 꼬리질문 없이 면접을 진행합니다", PROMPT_ERROR)


# 2026-11-19 사용자 지적: 꼬리질문에 "초반 테스: '골든셋'을 구체적으로…"처럼 **음성 인식이 잘라 놓은 조각**이
# 그대로 머리에 붙어 나왔다. 한국어 조각이라 기존 `_bad_tokens`(영문·숫자 검사)로는 안 걸린다.
# 질문은 그 자체로 읽혀야 한다 — 답변을 안 본 사람이 읽어도 말이 되어야 한다.
import re as _re

_FRAGMENT_HEAD = _re.compile(r"^\s*[^,.?!]{1,12}\s*[::]\s*")   # "초반 테스: ", "데이터베이:스 " 같은 머리 조각
_MID_COLON = _re.compile(r"[가-힣]\s*[::]\s*[가-힣]")            # 낱말 안에 콜론이 끼어든 흔적
_ASK = r"(?:설명해\s*주세요|알려\s*주세요|해\s*주세요|하시겠습니까|했나요|인가요|나요)"
_MULTI_ASK = _re.compile(_ASK + r"[\s\S]{0,200}?" + _ASK)   # 한 꼬리질문 안에 묻는 맺음이 2번 이상


def readable(fq):
    """(ok, reason). 꼬리질문이 **그 자체로 읽히는 한 문장**인지. 음성 조각·콜론 끼임·두 개 묻기를 막는다."""
    if _FRAGMENT_HEAD.search(fq):
        return False, "fragment_head"
    if _MID_COLON.search(fq):
        return False, "mid_colon"
    if _MULTI_ASK.search(fq):
        return False, "multi_ask"
    return True, ""


def judge_detail(att, q, answer):
    """A3 꼬리질문 판단 (INT-06·07, ADLC 6.1-B 의사 결정 표). DB 없이 돈다(eval_followup이 그대로 부름).
    반환 {'followup': (꼬리질문, 사유) | None, 'keyword_hits': list | None, 'specificity': int | None}."""
    from .. import evaluation, questions as Q
    from . import question_graph as QG        # 늦은 import: 순환 방지 + 평가 도구가 QG.bank_reference를 갈아끼울 수 있게
    out = {"followup": None, "keyword_hits": None, "specificity": None}
    if PRE_SYSTEM is None:                    # 프롬프트 파일 문제 → 꼬리질문 생략(면접은 계속)
        return out
    if q["parent_id"] is not None or att["followups_used"] >= config.MAX_FOLLOWUPS:
        return out
    if evaluation.NO_CONTENT.match(answer) or len(answer.strip()) < 5:
        return out
    bref = QG.bank_reference(q)
    groups, ideal = bref["keywords"], bref["ideal"]
    if groups:
        out["keyword_hits"] = QG.keyword_hits(groups, answer)
    ref = f"\n근거 이력서 항목: {q['resume_ref']['quote']}" if q.get("resume_ref") and q["resume_ref"].get("quote") else ""
    system, schema = (PRE_SYSTEM_IDEAL, PRE_SCHEMA_IDEAL) if ideal else (PRE_SYSTEM, PRE_SCHEMA)
    user = f"질문: {q['text']}{ref}" + (f"\n모범 답변 요지: {ideal[:800]}" if ideal else "") + f"\n답변: {answer[:1500]}"
    try:
        r = llm.chat_json(system, user, schema, timeout=config.FOLLOWUP_TIMEOUT)
    except llm.LLMError:
        return out
    if ideal and isinstance(r.get("specificity"), int):
        out["specificity"] = r["specificity"]
    kw_zero = groups and not out["keyword_hits"]
    abstract = out["specificity"] is not None and out["specificity"] <= SPECIFICITY_MAX
    off = (not r["addresses_question"]) or r["logic"] <= 2 or r["relevance"] <= 2
    if kw_zero:
        reason = "기대 키워드 0개"
    elif abstract:
        reason = "모범 답변 대비 구체성 2점 이하"
    elif off:
        reason = "논리성·관련성 2점 이하"
    elif r["inconsistent_with_resume"]:
        reason = "이력서와 다름"
    elif r["generic_only"]:
        reason = "일반론"
    else:
        return out
    fq = (r.get("followup_question") or "").strip()
    if not fq or Q.BLOCKLIST.search(fq) or len(fq) > MAX_FOLLOWUP_CHARS:
        return out
    # 근거 검사(2026-11-19): 답변 속 숫자·용어(STT 오타·잡음 포함)를 검증 없이 그대로 꼬리질문에 옮기지 못하게 —
    # "허용" 범위는 원 질문·이력서 근거·모범 답변 요지·기대 키워드뿐, 답변 원문 자체는 포함하지 않는다.
    base = " ".join([q.get("text") or "", (q.get("resume_ref") or {}).get("quote") or "", ideal or "",
                      " ".join(w for g in (groups or []) for w in g)])
    if Q._bad_tokens(fq, base, {}, {}):
        return out
    ok, why = readable(fq)
    if not ok:
        log.info("꼬리질문 생략(%s): %s", why, fq[:60])
        return out                                       # 음성 조각·콜론 끼임·두 개 묻기 → 꼬리질문을 만들지 않는다
    if groups:   # 힌트 금지(B-07): 지원자가 말하지 않은 기대 키워드가 꼬리질문에 있으면(원 질문에 있던 말은 제외) 생략
        said, qn, fn = set(out["keyword_hits"]), QG._norm(q["text"]), QG._norm(fq)
        for g in groups:
            if g[0] not in said and any(QG._norm(w) in fn and QG._norm(w) not in qn for w in g if QG._norm(w)):
                return out
    out["followup"] = (fq, reason)
    return out


def judge(att, q, answer):
    """A3 노드 계약(ADLC 6.1-B): judge(att, q, answer) → (꼬리질문, 사유) | None.
    새 꼬리질문 설계로 바꿀 때도 이 서명을 유지하면 그래프 1-B는 고치지 않는다."""
    return judge_detail(att, q, answer)["followup"]


def info():
    """진단용 — 프롬프트를 파일에서 읽었는지, 판단 상수가 무엇인지."""
    return {"module": __name__, "prompt_files": ["evaluate/followup_judge", "evaluate/followup_judge_ideal"],
            "prompt_loaded": PRE_SYSTEM is not None, "prompt_error": PROMPT_ERROR,
            "specificity_max": SPECIFICITY_MAX, "max_followup_chars": MAX_FOLLOWUP_CHARS,
            "max_followups": config.MAX_FOLLOWUPS, "timeout_sec": config.FOLLOWUP_TIMEOUT}
