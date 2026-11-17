"""리포트 코치 (v0.11, 2026-11-17 CrewAI 제거) — 지원자 정량 리포트 메일의 '코치 코멘트'만 쓴다.

역할 경계
- 점수·합불은 만들지 않는다. 입력은 evaluation.compute가 이미 확정한 숫자와 강점·개선점뿐이다.
- 모델 호출은 다른 agents/evaluate 모듈과 같은 패턴으로 llm.chat_json 1회 직접 호출(이전 CrewAI Agent/Task/Crew
  래퍼는 멀티에이전트·위임·도구를 쓰지 않는 1-Agent/1-Task였음이 드러나 걷어냄, 2026-11-17 사용자 결정).
- 연습 과제(actions)는 코딩 문제은행과 연동되지 않는 지어낸 조언이었으므로 삭제 — coaching 하나만 쓴다(2026-11-17).
- 실패하거나 결과가 규칙에 어긋나면 None을 돌려주고, 호출한 쪽이 템플릿 문구로 대체한다.
"""
import logging

from .. import llm, prompts, report_guard

log = logging.getLogger(__name__)
LAST_REJECT = None   # 마지막 호출에서 버린 사유 코드(측정·디버깅용, 내용 원문은 남기지 않는다)

COACH_SCHEMA = {"type": "object", "properties": {"coaching": {"type": "string"}}, "required": ["coaching"]}
FORBIDDEN = report_guard.FORBIDDEN

SYSTEM = prompts.load("generate/report_coach")


def _reject(code):
    global LAST_REJECT
    LAST_REJECT = code
    log.warning("report_coach rejected: %s", code)
    return None


def write(report):
    """report: {job, item_scores: {항목: 점수}, interview_score, coding, strengths, improvements}"""
    global LAST_REJECT
    LAST_REJECT = None
    import json
    facts = json.dumps(report, ensure_ascii=False)
    tie = report_guard.is_tie(report.get("item_scores") or {})
    instruction = (
        "coaching: 3~5문장. 항목 점수가 모두 같아(동점) 높고 낮은 항목이 없다. 최고·최저 항목을 비교하지 말고, "
        "항목 간 차이가 없다는 점과 개선점을 어떻게 연습할지 설명한다."
        if tie else
        "coaching: 3~5문장. 가장 높은 항목과 가장 낮은 항목을 점수와 함께 짚고, 강점을 유지하면서 개선점을 어떻게 연습할지 설명한다."
    )
    user = f"아래는 한 지원자의 AI 면접 정량 결과다(점수는 100점 환산, 시스템이 이미 확정한 값).\n{facts}\n\n{instruction}"
    try:
        out = llm.chat_json(SYSTEM, user, COACH_SCHEMA, timeout=90, temperature=0.3)
        coaching = str(out.get("coaching", "")).strip()
    except Exception as e:  # noqa: BLE001 — 모델·JSON 실패는 템플릿 대체
        return _reject(f"exception:{type(e).__name__}")
    if len(coaching) < 20:
        return _reject("too_short")
    code = report_guard.check_text([coaching], report_guard.number_tokens(facts))
    if code:
        return _reject(code)
    return {"coaching": coaching[:700]}
