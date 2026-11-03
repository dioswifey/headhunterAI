"""CrewAI 리포트 코치 (v0.6) — 지원자 정량 리포트 메일의 '코치 코멘트'와 '다음 연습 과제'만 쓴다.

역할 경계
- 점수·합불은 만들지 않는다. 입력은 evaluation.compute가 이미 확정한 숫자와 강점·개선점뿐이다.
- 모델 호출은 CrewAI 기본(LiteLLM 직접 호출)이 아니라 `LocalChatJson`을 거쳐 `llm.chat_json` 하나로 간다
  → Ollama(로컬)만 사용, LangSmith 추적 규칙도 그대로 적용된다.
- 실패하거나 결과가 규칙에 어긋나면 None을 돌려주고, 호출한 쪽이 템플릿 문구로 대체한다.
"""
import json
import re

from crewai import Agent, BaseLLM, Crew, Process, Task

from .. import llm

COACH_SCHEMA = {"type": "object", "properties": {
    "coaching": {"type": "string"},
    "actions": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3}},
    "required": ["coaching", "actions"]}
FORBIDDEN = re.compile(r"합격|불합격|합불|탈락|통과\s*여부|나이|성별|외모|학벌|출신")


class LocalChatJson(BaseLLM):
    """CrewAI → llm.chat_json 어댑터. CrewAI 에이전트 형식(Final Answer)에 맞춰 JSON 결과를 돌려준다."""

    # CrewAI 1.x의 BaseLLM은 pydantic 모델 → 설정값도 필드로 둔다
    json_schema: dict = {}
    timeout_sec: int = 90

    def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        user = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") != "system")
        out = llm.chat_json(system or "너는 도움이 되는 한국어 작성자다.", user, self.json_schema, timeout=self.timeout_sec, temperature=0.3)
        return "Thought: 정리했습니다.\nFinal Answer: " + json.dumps(out, ensure_ascii=False)

    def supports_function_calling(self):
        return False

    def supports_stop_words(self):
        return False

    def get_context_window_size(self):
        return 6144


def write(report):
    """report: {job, item_scores_ko: {항목: 점수}, interview_score, coding, strengths, improvements}"""
    coach = Agent(
        role="AI 면접 코치",
        goal="지원자가 다음 면접에서 더 잘할 수 있도록, 이미 산출된 점수와 강점·개선점만 근거로 구체적인 조언을 쓴다",
        backstory="IT 직군 면접 코칭 경력이 많은 코치. 근거 없는 칭찬이나 지어낸 경험을 쓰지 않고, 합격 여부를 언급하지 않는다.",
        llm=LocalChatJson(model=llm.model_version(), temperature=0.3, json_schema=COACH_SCHEMA), allow_delegation=False, verbose=False, max_iter=2,
    )
    facts = json.dumps(report, ensure_ascii=False)
    task = Task(
        description=(
            "아래는 한 지원자의 AI 면접 정량 결과다(점수는 100점 환산, 시스템이 이미 확정한 값).\n"
            f"{facts}\n\n"
            "다음을 한국어 존댓말로 쓴다.\n"
            "1) coaching: 3~4문장. 가장 높은 항목과 가장 낮은 항목을 점수와 함께 짚고, 강점을 유지하면서 개선점을 어떻게 연습할지 설명한다.\n"
            "2) actions: 다음 면접 전까지 해 볼 연습 과제 2~3개. 각각 한 문장, 바로 실천할 수 있게 구체적으로.\n"
            "규칙: 합격·불합격·합불을 언급하지 않는다. 입력에 없는 경험·사실을 지어내지 않는다. 나이·성별·외모·학벌 등 인적 특성을 언급하지 않는다."
        ),
        expected_output='{"coaching": "...", "actions": ["...", "..."]} 형식의 JSON',
        agent=coach,
    )
    try:
        res = Crew(agents=[coach], tasks=[task], process=Process.sequential, verbose=False).kickoff()
        raw = getattr(res, "raw", str(res)).strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        out = json.loads(raw)
        coaching = str(out.get("coaching", "")).strip()
        actions = [str(a).strip() for a in out.get("actions", []) if str(a).strip()][:3]
    except Exception:
        return None
    text = coaching + " " + " ".join(actions)
    if len(coaching) < 20 or not actions or FORBIDDEN.search(text):
        return None
    # 지어낸 수치 차단: 두 자리 이상 숫자·퍼센트는 입력(facts)에 있는 값만 허용 (예: '오버피팅 15% 감소' 같은 창작 수치)
    for n in re.findall(r"\d+(?:\.\d+)?", text):
        if len(n.replace(".", "")) >= 2 and n not in facts:
            return None
    return {"coaching": coaching[:700], "actions": [a[:200] for a in actions]}
