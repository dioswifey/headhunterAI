"""합불 통보 (v0.11, EML-07~09, 2026-11-17 CrewAI 제거) — hybrid 공고에서 담당자가 최종 합불을 확정하면 그 결과만 짧게 알린다.

역할 경계
- 점수·강점·개선점은 절대 쓰지 않는다. 입력은 공고명·기업명·지원자 이름·확정된 합불(result)뿐이다.
- report_coach.py와 같은 패턴으로 llm.chat_json 1회 직접 호출(이전 CrewAI Agent/Task/Crew 래퍼 제거,
  2026-11-17 — 분해할 역할이 없는 2~3문장 포맷터라 멀티에이전트 구조가 공허했음).
- 실패하면 None → 호출한 쪽(decision_mail.py)이 템플릿 문구로 대체한다.
"""
import json
import re

from .. import llm, prompts

NOTICE_SCHEMA = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}
FORBIDDEN = re.compile(r"나이|성별|외모|학벌|점수|\d")  # 숫자·점수·인적 특성 언급 차단(입력에 아예 없으므로 지어낸 것)

SYSTEM = prompts.load("generate/decision_notice")


def write(report):
    """report: {job, company, name, result: 'pass'|'fail'}"""
    label = "합격" if report["result"] == "pass" else "불합격"
    facts = json.dumps({"job": report["job"], "company": report["company"], "name": report["name"], "result": label}, ensure_ascii=False)
    instruction = ("다음 전형 안내를 기대해도 좋다는 짧은 환영 문구를 넣는다." if label == "합격" else
                   "지원에 대한 감사와 다음 기회를 응원하는 짧은 문구를 넣는다.")
    user = f"아래 지원자의 1차 전형 결과가 '{label}'로 확정됐다.\n{facts}\n\n한국어 존댓말 2~3문장으로 message를 쓴다. 결과({label})를 분명히 알리고, {instruction}"
    try:
        out = llm.chat_json(SYSTEM, user, NOTICE_SCHEMA, timeout=90, temperature=0.3)
        message = str(out.get("message", "")).strip()
    except Exception:
        return None
    if len(message) < 10 or FORBIDDEN.search(message):
        return None
    return {"message": message[:500]}
