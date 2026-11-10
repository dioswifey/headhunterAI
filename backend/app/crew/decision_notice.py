"""CrewAI 합불 통보 (v0.10, EML-07~09) — hybrid 공고에서 담당자가 최종 합불을 확정하면 그 결과만 짧게 알린다.

역할 경계
- 점수·강점·개선점은 절대 쓰지 않는다. 입력은 공고명·기업명·지원자 이름·확정된 합불(result)뿐이다.
- report_coach.py와 같은 LocalChatJson 어댑터로 llm.chat_json(로컬 Ollama)만 호출한다.
- 실패하면 None → 호출한 쪽(decision_mail.py)이 템플릿 문구로 대체한다.
"""
import json
import re

from crewai import Agent, Crew, Process, Task

from .. import llm
from .report_coach import LocalChatJson

NOTICE_SCHEMA = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}
FORBIDDEN = re.compile(r"나이|성별|외모|학벌|점수|\d")  # 숫자·점수·인적 특성 언급 차단(입력에 아예 없으므로 지어낸 것)


def write(report):
    """report: {job, company, name, result: 'pass'|'fail'}"""
    label = "합격" if report["result"] == "pass" else "불합격"
    writer = Agent(
        role="채용 결과 통보 작성자",
        goal="담당자가 확정한 1차 합불 결과를 지원자에게 정중하고 간결하게 알린다",
        backstory="채용 담당 커뮤니케이션 작성자. 점수나 사유를 지어내지 않고, 확정된 결과만 전달한다.",
        llm=LocalChatJson(model=llm.model_version(), temperature=0.3, json_schema=NOTICE_SCHEMA), allow_delegation=False, verbose=False, max_iter=2,
    )
    facts = json.dumps({"job": report["job"], "company": report["company"], "name": report["name"], "result": label}, ensure_ascii=False)
    task = Task(
        description=(
            f"아래 지원자의 1차 전형 결과가 '{label}'로 확정됐다.\n{facts}\n\n"
            f"한국어 존댓말 2~3문장으로 message를 쓴다. 결과({label})를 분명히 알리고, "
            + ("다음 전형 안내를 기대해도 좋다는 짧은 환영 문구를 넣는다." if label == "합격" else "지원에 대한 감사와 다음 기회를 응원하는 짧은 문구를 넣는다.")
            + "\n규칙: 점수·순위·구체적 사유를 언급하지 않는다. 나이·성별·외모·학벌 등 인적 특성을 언급하지 않는다. 입력에 없는 사실을 지어내지 않는다."
        ),
        expected_output='{"message": "..."} 형식의 JSON',
        agent=writer,
    )
    try:
        res = Crew(agents=[writer], tasks=[task], process=Process.sequential, verbose=False).kickoff()
        raw = getattr(res, "raw", str(res)).strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        message = str(json.loads(raw).get("message", "")).strip()
    except Exception:
        return None
    if len(message) < 10 or FORBIDDEN.search(message):
        return None
    return {"message": message[:500]}
