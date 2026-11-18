"""contextualize 재시도 경로 단위 검사 (2026-11-13 fix2) — 모델·DB 없이 호스트에서 돈다.

    cd backend && python -m tools.test_contextualize_retry

가짜 llm.chat_json으로 다음을 확인한다.
  C1 첫 답이 토큰 검사 통과 → 그대로 채택, 호출 1회
  C2 첫 답에 지어낸 수치 → 걸린 토큰을 알려 주고 재생성 → 둘째 답 채택, 호출 2회, 둘째 요청에 피드백·토큰 포함
  C3 두 번 다 지어낸 수치 → 원문, 호출 2회(3회째 없음)
  C4 첫 답이 한 문장 규칙 위반 → 재시도 없이 원문, 호출 1회
  C5 모델 오류 → 원문
  C6 _bad_tokens / _tokens_ok 일치
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if "app.db" not in sys.modules:  # 호스트에 psycopg가 없어도 import 되게
    sys.modules["app.db"] = types.ModuleType("app.db")
try:
    import langsmith  # noqa: F401
except ImportError:  # 호스트에 langsmith가 없으면 traceable을 그대로 통과시키는 가짜
    _ls = types.ModuleType("langsmith")
    _ls.traceable = lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda f: f))
    sys.modules["langsmith"] = _ls

from app import llm, questions as Q  # noqa: E402

JOB = {"company_name": "헤드헌터랩_서울", "role_name": "AI/AX 엔지니어",
       "company_intro": "고객사 상담 문서 기반 RAG 서비스를 만드는 회사", "duties": "RAG 파이프라인 설계·개발",
       "requirements": "Python, LLM 경험"}
BASE = "우리 회사의 서비스나 사업 중 관심 있는 부분과 그 이유를 설명해 주세요."
GOOD = "헤드헌터랩_서울의 RAG 파이프라인 설계 업무에서 답변 품질을 어떻게 평가할지 설명해 주세요."
FAKE = "헤드헌터랩_서울의 RAG 정확도가 85%로 떨어지면 어떻게 해결할지 설명해 주세요."
TWO = "RAG를 써 본 적이 있나요. 그 경험을 설명해 주세요."


def run(answers):
    calls = []

    def fake(system, user, schema, **kw):
        calls.append(user)
        a = answers[len(calls) - 1]
        if isinstance(a, Exception):
            raise a
        return {"question": a, "company_facts": []}

    orig_chat, orig_load = llm.chat_json, Q.prompts.load
    llm.chat_json, Q.prompts.load = fake, (lambda name, **kw: "SYSTEM")
    try:
        return Q.contextualize(BASE, JOB), calls
    finally:
        llm.chat_json, Q.prompts.load = orig_chat, orig_load


def main():
    res = []

    out, c = run([GOOD])
    res.append(("C1 통과 답 채택·1회", out == GOOD and len(c) == 1))

    out, c = run([FAKE, GOOD])
    res.append(("C2 재생성 후 채택·2회", out == GOOD and len(c) == 2))
    res.append(("C2 피드백에 걸린 토큰 85%", len(c) == 2 and "85%" in c[1] and "[다시 쓰기]" in c[1]))

    out, c = run([FAKE, FAKE, GOOD])
    res.append(("C3 두 번 실패 → 원문·2회", out == BASE and len(c) == 2))

    out, c = run([TWO, GOOD])
    res.append(("C4 한 문장 위반 → 재시도 없이 원문", out == BASE and len(c) == 1))

    out, c = run([llm.LLMError("down")])
    res.append(("C5 모델 오류 → 원문", out == BASE))

    bad = Q._bad_tokens(FAKE, BASE, JOB, {})
    res.append(("C6 _bad_tokens == ['85%']", bad == ["85%"] and not Q._tokens_ok(FAKE, BASE, JOB, {}) and Q._tokens_ok(GOOD, BASE, JOB, {})))

    # C7 (2026-11-18) 거절 사유 관측이 **판정을 바꾸지 않는지** 고정한다.
    # ungrounded_terms는 grounded의 사유 설명용이라 둘의 판단이 어긋나면 사유 기록이 거짓이 된다.
    SNAP = {"projects": [{"name": "사내 검색 RAG", "role": "개발", "detail": "LangChain으로 파이프라인 구현",
                          "tech": ["LangChain", "Python"], "page": 1}]}
    cases = [("LangChain 파이프라인을 어떻게 구현했는지 설명해 주세요.", []),
             ("Kubernetes로 배포한 과정을 설명해 주세요.", []),
             ("Python과 LangChain을 함께 쓴 이유를 설명해 주세요.", ["LangChain"]),
             ("정확도를 99% 올린 방법을 설명해 주세요.", []),
             ("담당한 역할을 설명해 주세요.", []),
             ("담당한 역할을 설명해 주세요.", ["없는용어"])]
    mism = [q for q, cit in cases if Q.grounded(q, cit, SNAP) != (not Q.ungrounded_terms(q, cit, SNAP))]
    res.append((f"C7 grounded ↔ ungrounded_terms 판정 일치 {len(cases)}건" + (f" 어긋남 {mism}" if mism else ""), not mism))
    res.append(("C7 걸린 토큰을 그대로 집어낸다",
                Q.ungrounded_terms("Kubernetes로 배포한 과정을 설명해 주세요.", [], SNAP) == ["Kubernetes"]
                and Q.ungrounded_terms("정확도를 99% 올린 방법을 설명해 주세요.", [], SNAP) == ["99%"]))
    # 사유 분류가 검사 순서를 그대로 따르는지
    res.append(("C7 _reject_reason 단계 분류",
                Q._reject_reason("", [], SNAP)["stage"] == "empty"
                and Q._reject_reason("첫 문장입니다. 두 번째 문장을 설명해 주세요.", [], SNAP)["stage"] == "one_sentence"
                and Q._reject_reason("실례지만 나이가 어떻게 되시나요?", [], SNAP)["stage"] == "blocklist"
                and Q._reject_reason("Kubernetes로 배포한 과정을 설명해 주세요.", [], SNAP) ==
                {"stage": "grounded", "terms": ["Kubernetes"], "n_terms": 1, "cited": []}
                and Q._reject_reason("LangChain 파이프라인을 어떻게 구현했는지 설명해 주세요.", [], SNAP)["stage"] == "fit_scope"))
    # 기록부가 터져도 흐름이 멈추지 않아야 한다(조건 2)
    acc = []
    Q._note_reject(acc, "질문입니다 설명해 주세요.", None, None)   # snapshot None → 내부에서 예외
    res.append(("C7 기록 실패는 조용히 넘어간다(예외 전파 없음)", acc == []))

    for name, ok in res:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(ok for _, ok in res)
    print(f"{n}/{len(res)}")
    return 0 if n == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
