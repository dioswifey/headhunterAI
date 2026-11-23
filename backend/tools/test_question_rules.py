"""질문·꼬리질문 문장 규칙과 면접 중 우회 경로 회귀 시험 — 2026-11-20 신설. 모델·DB·Redis·Chroma 없이 돈다.

    docker exec -w /srv AI-Interview-project-api python -m tools.test_question_rules
    cd backend && python -m tools.test_question_rules

왜 생겼나: 2026-11-19 커밋(a491873)이 넣은 문장 검사 2종(추상 범주·개념 2개)이 정규식 끝의 제어문자 0x08 때문에
한 번도 걸리지 않았는데, 이 규칙들을 부르는 시험이 하나도 없어 아무도 몰랐다(2026-11-20 재검증 ⓕ·ⓖ).
그래서 **사용자가 실제로 지적한 문장은 반드시 탈락**, **잘못 잡던 문장은 반드시 통과**를 고정해 둔다.

  Q1  사용자 사고 문장(TRD v0.11 §6.9.1 · RAG_질문품질_QA ①)과 재검증에서 찾은 변형 → 탈락
  Q2  거짓양성 예시(모델 판정이 잘못 떨어뜨린 문장 · 사람 짝 · 관계 과제 · '~면' 조건) → 통과
  Q3  이력서 질문: 수치 결론형 → 탈락, 새 템플릿 3개 → 통과
  Q4  꼬리질문: 음성 조각·두 개 묻기·쉼표 이음 두 개 묻기 → 탈락, 예시·힌트는 떼고 쓴다
  Q5  관문 2: 일부만 살아남으면 Redis로 채워 항상 n개 · 인성에는 적용하지 않음 · 전부 탈락 → 백업
  Q6  A6·A7은 사내문서 질문(A17)을 다시 쓰지 않는다 · A6가 고친 문장도 문장 규칙을 지켜야 받는다
  Q7  생성 모델 기록: openai:<모델> / OpenAI 실패 → local:<모델>(fallback) / 로컬 설정 → local:<모델>
  Q8  인성 생성 재료는 가치·태도 문서(talent)만 — HR·근태 규정 문서는 빠진다
  Q9  허용 덧붙임("관련된 경험이 있다면 ~ 답해 주셔도 좋습니다")은 한 문장 검사에서 뗀다
  Q10 세트 안 같은 프로젝트·같은 조각의 사내문서 질문은 하나만(F4, 콜센터 AI 직무·기업 본질문 겹침 사고)
  Q11 백업 층(Redis·DB)에도 문장 규칙·공고 관련성(②) · 후보 깊이 하한(⑦) · 이력서 참고 후보 수(⑧)·하한(ⓒ)
  Q12 의문사로 묻고 명령형으로 맺는 문장(⑨) — 오탐 0 확인 포함
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import config, llm, questions as Q  # noqa: E402
from app.agents import followup_agent as FA, internal_question_agent as IQ, question_graph as QG  # noqa: E402
from app.agents import question_rules as QR  # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail)[:300]))


def a17_issue(q):
    """A18의 문장 검사 부분만(사실 대조·금지 용어 제외) — 먼저 걸린 사유, 통과면 ''."""
    if not IQ._one_sentence(q):
        return "not_one_sentence"
    ok, why = QR.has_malformed_phrasing(q)
    if not ok:
        return why
    if not QR.SITUATION_MARKERS.search(QR.core(q)):
        return "no_situation"
    ok, why = IQ.has_concrete_anchor(QR.core(q))
    if not ok:
        return "template_only"
    ok, why = QR.sentence_shape(q)
    if not ok:
        return "shape:" + why
    if QR.ABSTRACT_EXPERIENCE.search(QR.core(q)):
        return "abstract_experience"
    return ""


# ---------------- Q1 사고 문장 → 탈락 ----------------
INCIDENTS = {
    "사고2 비문+두 개 묻기": "데이터 준비 과정에서 원천 데이터 사용 승인을 맡는다면, 고객 내부 권한과 책임을 명확히 지정할 때 어떤 절차를 먼저 진행하고, 그 결과물로 무엇을 만들고 싶은가?",
    "사고3 사내 용어(곧은 따옴표)": "AI/AX 프로젝트에서 '적용 가능성 검토' 단계에서 규칙 기반 처리, 전통 ML, LLM, RAG 또는 혼합 방식 중 무엇을 고르시겠습니까?",
    "사고3 사내 용어(곡선 따옴표)": "AI/AX 프로젝트에서 ‘적용 가능성 검토’ 단계에서 규칙 기반 처리, 전통 ML, LLM, RAG 또는 혼합 방식 중 무엇을 고르시겠습니까?",
    "사고4 원문": "LLM 응용에서 모델 답변의 근거 여부와 불확실성을 점검하는 상황이라면, 어떻게 하시겠습니까?",
    "사고4 맺음만 고친 변형": "LLM 응용에서 모델 답변의 근거 여부와 불확실성을 점검하는 상황이라면, 무엇을 먼저 확인하시겠습니까?",
    "사고4 로컬 풀 변형": "LLM 응용에서 모델 답변의 근거 여부와 불확실성을 점검하는 방법을 설명해 주세요.",
    "구체성 TAL-01#023#0": "백엔드팀에서 의존 관계와 운영 위험을 조기에 드러내는 상황이라면, 어떻게 하시겠습니까?",
    "구체성 TAL-01#001#0": "다른 부서와 협업해 변경 내용을 문서화한 상황이라면, 어떻게 하시겠습니까?",
    "구체성 교체 후 문장": "불명확한 요구를 질문 목록으로 정리하고 합의한 상황이라면 어떻게 하시겠습니까?",
    "쉼표 이음 두 개 묻기": "작업 큐에서 대기 작업 수가 급증했을 때, 어떤 정보를 우선적으로 확인하고 어떻게 처리할지 설명해 주세요.",
    "두 개 묻기(~하며)": "작업 큐에서 메시지 중복 전달을 방지하기 위해 멱등 처리를 적용할 때, 어떤 조건을 검토해야 하며, 이를 어떻게 구현할지 설명해 주세요.",
    "두 개 묻기(맺음 2번)": "데이터 준비 과정에서 진단이 끝난 상황이라면 어떤 작업을 먼저 수행해야 하나요, 그 이유는 무엇인가요?",
    "F9 회사 경력 전제(attempt 14 기업 문항)": "헤드헌터랩_경기에서 제품 이미지에서 외관 불량을 탐지·분류하는 비전 모델 개발 업무를 맡아, 현장에서 모호한 불량 기준을 데이터로 구체화할 때 어떤 접근을 취했는지 설명해 주세요.",
    "F9 회사 경력 전제(A6 재작성)": "헤드헌터랩_경기에서 비전 모델 서빙 환경에서 트래픽 가중치 합이 100이 아닌 경우 실패라는 설정이 적용되는 상황을 고려할 때, 해당 설정을 검증하기 위해 어떤 판단 과정을 거쳤는지 설명해 주세요.",
    "F8 깨진 낱말(attempt 14 직무 문항)": "AI/AX 기술스을 분류할 때, 모델 계층의 정확도와 재현성이 충돌하는 경우 어떤 기준으로 판단하시겠습니까?",
    "F2 장면 없는 경험(attempt 14 인성 2)": "비전문가에게 기술적인 내용을 설명해야 했던 경험과 그때 쓴 방법을 설명해 주세요.",
    "F1 아는 기술만 묻기(attempt 14 인성 1)": "최근 1년 동안 스스로 학습해서 익힌 기술이나 지식과 그 방법을 설명해 주세요.",
    "회사 전제(이 회사에서)": "휴직 유형별로 필요한 서류가 다르면, 이 회사에서 어떤 기준으로 서류를 선택하는지 설명해 주세요.",
    "회사 전제(행동 기준)": "이 회사가 중요하게 보는 행동 기준 중 하나인 민감 정보 관리를 실천하는 상황이라면 무엇을 먼저 하시겠습니까?",
    "음성 조각(머리 콜론)": "수요예: 예측 대시보드가 발주 담당자의 판단을 지원하는 상황이라면, 어떤 정보를 먼저 확인해야 할지 설명해 주세요.",
    "지시어": "외부 발표에서 예상하지 못한 고객 질문이 나온 상황이라면, 해당 내용을 즉석에서 추정하지 않고 무엇을 먼저 확인하시겠습니까?",
}


def t_incidents():
    for name, q in INCIDENTS.items():
        why = a17_issue(q)
        check(f"Q1 탈락 · {name}", bool(why), "통과해 버림: " + q)


# ---------------- Q2 거짓양성 예시 → 통과 ----------------
GOOD = {
    "RFC 테크 리드(모델 판정 거짓양성)": "RFC 소유자가 초안을 작성한 후 검토 단계를 진행할 때, 영향받는 팀의 테크 리드와 같은 검토자에게 피드백을 전달하는 방식을 설명해 주세요.",
    "현금 흐름 충돌(모델 판정 거짓양성)": "프로젝트 철수 시, 현금 흐름과 고객 요청이 동시에 발생할 때 무엇을 먼저 처리하시겠습니까?",
    "출퇴근 누락(모델 판정 거짓양성)": "출퇴근 누락이 반복적으로 발생할 때, 어떤 점검을 통해 원인을 파악하시겠습니까?",
    "관계 과제(사실과 의견 구분)": "협업 중 충돌이 발생했을 때, 사실과 의견을 어떤 기준으로 구분해 조율하시겠습니까?",
    "관계 과제(정확도·재현성 충돌)": "모델 계층의 정확도와 재현성이 충돌하는 상황이라면, 어떤 기준으로 우선순위를 정하시겠습니까?",
    "'~면' 조건": "다음 달 근태 보고서에서 20% 이상 변동이 발생하면, 어떤 조치를 먼저 취하시겠습니까?",
    "'~면' 조건(나면)": "출퇴근 기록이 두 시스템에서 30분 이상 차이가 나면, 무엇부터 확인하시겠습니까?",
    "지시문 좋은 예(덧붙임 포함)": "AI/AX 프로젝트를 새로 맡는다면 규칙 기반 처리·전통 ML·LLM·RAG 중 무엇을 어떤 기준으로 고르시겠습니까? 관련된 업무 경험이나 프로젝트가 있다면 그것을 바탕으로 답해 주셔도 좋습니다.",
    "지시문 좋은 예(6-3)": "사내 문서를 근거로 답하는 상담 챗봇이 문서에 없는 내용을 사실처럼 답한 것을 발견했다면, 근거 없는 답변을 걸러내기 위해 무엇을 확인하시겠습니까?",
    "지시문 좋은 예(6-6)": "상담사가 쓰는 답변 도우미를 만든다고 할 때, 사내 문서에서 근거를 찾지 못한 질문에 대해 시스템이 무엇을 하도록 설계하시겠습니까?",
}
PAIRS_NOT_TWO = ["기안자와 팀장이 다음 영업일까지 합동 검토를 요청하는 상황이라면",
                 "직속상사와 구성원이 월요일까지 일정을 등록하는 상황이라면",
                 "피신고인과 신고인의 관계가 악화되기 전에",
                 "예산과 원가를 어떻게 비교하시겠습니까",
                 "예측액과 실제액의 차이가 발생하는 경우"]


def t_good():
    for name, q in GOOD.items():
        why = a17_issue(q)
        check(f"Q2 통과 · {name}", not why, f"{why}: {q}")
    for s in PAIRS_NOT_TWO:
        check(f"Q2 개념 2개 아님 · {s[:14]}", not QR.two_concepts(s), QR.two_concepts(s))
    check("Q2 개념 2개 적중 · 근거 여부와 불확실성", bool(QR.two_concepts("모델 답변의 근거 여부와 불확실성을 점검할 때")))
    check("Q2 개념 2개 적중 · 기준과 절차", bool(QR.two_concepts("어떤 기준과 절차를 적용할까요?")))


# ---------------- Q3 이력서 질문 ----------------
def t_resume():
    for q in ("검색 정확도를 30% 개선하신 경험을 설명해 주세요.",
              "대화 로그 아카이빙 설계에서 리드로 90일 지난 대화 로그를 S3로 옮겨 DB 용량을 60% 줄인 경험을 설명해 주세요",
              "2024년에 리드로 1,200건의 질문으로 회귀 평가를 만들어 매주 자동 점검한 경험을 설명해 주세요."):
        check("Q3 수치 결론형 → 탈락 · " + q[:16], QR.resume_issue(q) == "numeric_conclusion", QR.resume_issue(q))
    check("Q3 옛 템플릿 2(개념 2개 + 지시어) → 탈락",
          bool(QR.resume_issue("이력서에 적은 결제 서버에서 사용한 기술을 선택한 이유와 그 결과를 설명해 주세요.")))
    check("Q3 두 개 묻기(의문사 2번) → 탈락",
          bool(QR.resume_issue("결제 서버에서 어떤 문제를 겪었고, 어떻게 해결했는지 설명해 주세요.")))
    # 알려진 빈틈: "맡은 역할과 가장 어려웠던 문제를 어떻게 해결했는지"(옛 템플릿 1)처럼 '와' 뒤가 긴 명사구면
    # 개념 2개 검사가 잡지 못한다(뒤 명사구 길이를 늘리면 거짓양성이 늘어 좁게 뒀다) — 템플릿 문구 자체를 바꿔 막았다.
    for i in range(len(Q.TEMPLATES)):
        for label in ("상담 답변 회귀 평가셋 구축", "가상페이웨이"):
            q = Q.template_text(i, label, "헤드헌터랩_경기")
            check(f"Q3 새 템플릿 {i + 1} 통과(F3 회사 맥락 가정형) · {label[:6]}",
                  not QR.resume_issue(q) and QR.has_malformed_phrasing(q)[0] and Q.one_sentence(q)
                  and "헤드헌터랩_경기" in q and not QR.COMPANY_PAST.search(q), (QR.resume_issue(q), q))
    check("Q3 회사 이름이 없으면 '지원하신 회사'", "지원하신 회사" in Q.template_text(0, "결제 서버", ""))
    pairs = Q.resume_pairs([{"label": "A"}, {"label": "B"}])
    check("Q3 이력서 항목이 달라도 관점(템플릿)이 겹치지 않는다", len({a for _, a in pairs}) == len(pairs), pairs)
    check("Q3 수치를 뒤에서 맥락으로 짚기 → 통과",
          not QR.resume_issue("결제 웹훅 서버를 만들 때 중복 수신을 어떤 방식으로 막으셨는지 설명해 주세요."))
    # Q3-b `grounded()` 인용 어구 기준 (2026-11-23 사용자 승인 ③). 전에는 어구 전체 완전일치를 요구해
    # 표현만 합친 인용이 전부 탈락했고(11-23 측정: 이력서 질문 8개 중 7개가 템플릿 추락, 사유의 절반이 이것),
    # 그래서 공고가 바뀌어도 이력서 질문이 똑같이 나갔다. 아래 어구는 전부 실제로 탈락했던 것이다.
    snap = {"career": [{"company": "로지브릿지", "detail": "상담 티켓 플랫폼에서 티켓 목록·검색 화면을 담당했고 "
                                                        "PostgreSQL EXPLAIN ANALYZE로 상담 이력 목록 API를 고쳤다. "
                                                        "C++ 추론 모듈과 로트 번호, 판정 결과도 다뤘다."}],
            "skills": ["FastAPI", "PostgreSQL", "C++", "Locust"]}
    for t in ("상담 티켓 목록 API", "C++ 코드", "로트 정보", "결과 판정", "판정 결과 기록", "PostgreSQL"):
        check("Q3-b 표현만 다른 인용 → 통과 · " + t, Q.grounded("테스트 질문입니다", [t], snap),
              Q.ungrounded_terms("테스트 질문입니다", [t], snap))
    for t in ("Kubernetes 오토스케일링", "GraphQL 스키마", "블록체인 정산", "YOLOv8 학습", "Redis 캐시"):
        check("Q3-b 이력서에 없는 인용 → 탈락 · " + t, not Q.grounded("테스트 질문입니다", [t], snap))
    check("Q3-b 질문 본문의 영문 토큰은 여전히 완전일치(없으면 탈락)",
          not Q.grounded("Kafka 파이프라인을 어떻게 설계하시겠습니까", [], snap))
    check("Q3-b 질문 본문의 영문 토큰이 이력서에 있으면 통과",
          Q.grounded("FastAPI로 어떻게 설계하시겠습니까", [], snap))


# ---------------- F1~F9 사용자 직접 응시 피드백(attempt 14, 2026-11-20) ----------------
ATT14 = {
    "q3": "소비기한 인쇄 불량 검사 개선 프로젝트에서, 반사광으로 인해 정상 제품이 불량으로 분류되는 문제를 해결하기 위해 어떤 C++ 및 OpenCV 기술을 적용했는지 설명해 주세요.",
    "q4": "소비기한 인쇄 불량 검사 개선 프로젝트에서 반사광으로 인한 오검출을 해결한 과정을 설명해 주세요.",
    "q3_answer": "인체 불량 검사의 검토에서 간섭은 정상적인 불량 확인인데, 그거는 처음은 인터넷 부가 검사 인식이라 한다고 생각해서 그때 때문에 간섭과 처음에 일치가 없다고 생각합니다. 그거는 어, 컴퓨터의 검 검사를 자동화하는 것에 비해서 처음 들어가는 인피드 문제이기 때문에 인터넷 환경을 다시 조명한 바 있습니다.",
    "q3_followup": "반사광으로 인해 정상 제품이 불량으로 분류되는 문제를 해결하기 위해 C++ 및 OpenCV 기술을 적용했을 때, 인터넷 환경을 재조명한 구체적인 방법이나 수치적 결과가 있나요?",
    "q4_answer": "이거 안 보던지",
    "q4_followup": "소비기한 인쇄 검사에서 반사광으로 인한 오검출 문제를 해결하기 위해 어떤 구체적인 조치를 취했는지 설명해 주세요.",
}


def t_feedback():
    # F1 회사·공고 맥락 + 답변 형식 예시 1개 → 통과
    f1 = ("{회사}에서 상담 기록을 자동으로 분류하는 프로젝트를 진행한다고 가정할 때, 어떤 기술과 방법으로 구현하시겠습니까? "
          "예를 들어 '문의 유형 분류를 자동화했고, 그 결과 상담사의 처리 시간이 줄었다'처럼 설명해 주세요.")
    check("F1 회사 자리 + 답변 형식 예시 → 통과", not a17_issue(f1), a17_issue(f1))
    check("F1 회사 이름을 채워도 통과", not a17_issue(QG.fill_company(f1, {"company_name": "헤드헌터랩_경기"})))
    check("F1 회사 자리 치환", QG.fill_company("{회사}에서 맡는다면", {"company_name": "A사"}) == "A사에서 맡는다면")
    check("F1 예시는 1개만(두 문장 예시 → 한 문장 위반)",
          not IQ._one_sentence("무엇을 고르시겠습니까? 예를 들어 '가'처럼 설명해 주세요. 예를 들어 '나'처럼 설명해 주세요."))
    # F2 구체 장면 → 통과
    f2 = "{회사}에서 생산 라인 오검출 문제를 해결하는 중에 개발팀이 아닌 회계팀에 팀 업무를 브리핑해야 한다면, 무엇을 먼저 설명하시겠습니까?"
    check("F2 누가·어떤 상황·누구에게 → 통과", not a17_issue(f2), a17_issue(f2))
    # F3 이력서를 그대로 되묻는 질문은 회사 맥락이 없다(make_resume_question이 no_company_context로 버린다)
    check("F3 attempt 14 이력서 질문에 회사·가정 표지 없음", "헤드헌터랩" not in ATT14["q3"] and "다면" not in ATT14["q3"])
    # F4 같은 주제
    check("F4 attempt 14 이력서 3·4번 = 같은 주제(반사광)", "반사광" in QR.shared_topic(ATT14["q3"], ATT14["q4"]),
          QR.shared_topic(ATT14["q3"], ATT14["q4"]))
    check("F4 다른 주제는 겹치지 않음", not QR.shared_topic(ATT14["q3"], "결제 웹훅 중복 수신을 어떤 방식으로 막으시겠습니까?"))
    t1, t2 = Q.template_text(0, "결제 서버", "헤드헌터랩_경기"), Q.template_text(1, "상담 로그 분석", "헤드헌터랩_경기")
    check("F4 템플릿 틀·회사 이름만 같으면 같은 주제 아님", not QR.shared_topic(t1, t2, ignore=["헤드헌터랩_경기"]),
          QR.shared_topic(t1, t2, ignore=["헤드헌터랩_경기"]))
    # F5 비답변
    for a in (ATT14["q4_answer"], "아.", "잘 모르겠습니다", "무슨 답변을 원하시는지 잘 이해가 안 됩니다",
              "지금은 거의 없던데요. 사실 내가 개발팀에서 개발이 아닌 다른팀에서 개발을 했기 때문에 실상 그 말 영역은."):
        check("F5 비답변 → 꼬리질문 금지 · " + a[:10], QR.non_answer(a)[0], QR.non_answer(a))
    long_ok = "정확한 수치는 기억이 안 나지만, 반사광이 생기는 각도를 바꾸려고 조명을 돔 형태로 바꾸고 노출 시간을 줄였으며 그 뒤 오검출이 눈에 띄게 줄었습니다."
    check("F5 '기억이 안 나지만' + 실제 내용(긴 답변)은 비답변 아님", not QR.non_answer(long_ok)[0], QR.non_answer(long_ok))
    check("F5 실제 답변은 비답변 아님", not QR.non_answer(ATT14["q3_answer"])[0])
    # F6 되묻기·음성 잡음 (2026-11-23 사용자 승인 ⑤). 11-23 P3 측정에서 아래 잡음 답변이 1층을 통과해
    # 모델이 불렸고 꼬리질문 문장 검사(2층)에서만 막혔다 — 사용자 F6 사고가 바로 이 유형이라 1층에서 막는다.
    for a in ("어... 네 그게 음... 아 잠시만요 인터넷이 좀... 네 뭐라고 하셨죠.",
              "죄송한데 소리가 안 들려서요 다시 말씀해 주시겠어요.",
              "잘 안 들렸습니다 질문을 다시 한 번 해 주세요."):
        check("F6 되묻기·잡음 답변 → 꼬리질문 금지 · " + a[:12], QR.non_answer(a)[0], QR.non_answer(a))
    talk_ok = ("다시 한 번 말씀드리면, 제가 맡은 부분은 검색 파이프라인이었고 재순위 모델을 붙여 근거 포함률을 "
               "71%에서 89%로 올렸습니다. 평가셋은 420문항으로 만들었고 문항마다 정답 근거를 달아 뒀습니다.")
    check("F6 '다시 한 번 말씀' + 실제 내용(긴 답변)은 비답변 아님", not QR.non_answer(talk_ok)[0], QR.non_answer(talk_ok))
    # F6 전제 구절
    miss = QR.ungrounded_premise(ATT14["q3_followup"], ATT14["q3_answer"], [ATT14["q3"]])
    check("F6 '재조명'은 답변에 없는 전제", "재조명" in miss, miss)
    ok_fu = "조명을 돔 형태로 바꾼 뒤 오검출이 줄었는지 무엇으로 확인하셨나요?"
    check("F6 답변에 있는 말만 짚으면 통과", not QR.ungrounded_premise(ok_fu, long_ok, [ATT14["q4"]]),
          QR.ungrounded_premise(ok_fu, long_ok, [ATT14["q4"]]))
    # F7 되풀이
    check("F7 attempt 14 4번 꼬리질문 = 원 질문 되풀이", QR.repeats_question(ATT14["q4_followup"], ATT14["q4"]))
    check("F7 같은 주제를 더 파고드는 꼬리질문은 되풀이 아님",
          not QR.repeats_question("반사광 판정 임계값은 어떤 데이터로 정하셨나요?", ATT14["q4"]))
    # F8 깨진 낱말
    check("F8 '기술스을' 잡음", QR.broken_word("AI/AX 기술스을 분류할 때") == "기술스을", QR.broken_word("AI/AX 기술스을 분류할 때"))
    for ok_s in ("마을을 지나 가을을 보내고", "결과를 확인할 때", "데이터를 분류할 때", "다를 때"):
        check("F8 정상 조사 결합 통과 · " + ok_s[:8], not QR.broken_word(ok_s), QR.broken_word(ok_s))
    # F9 회사 이름 + 가정형 = 허용, + 과거형 = 탈락
    hyp = "헤드헌터랩_경기에서 비전 모델 개발 업무를 맡는다면, 모호한 불량 기준을 데이터로 구체화하기 위해 무엇을 먼저 하시겠습니까?"
    check("F9 회사 이름 + 가정형 미래 → 통과", QR.has_malformed_phrasing(hyp)[0], QR.has_malformed_phrasing(hyp))
    check("F9 '{회사}에서 진행한다고 가정할 때' → 통과", not QR.COMPANY_PAST.search(f1))
    check("F9 회사 이름 + 과거형(맡아…취했는지) → 탈락", bool(QR.COMPANY_PAST.search(INCIDENTS["F9 회사 경력 전제(attempt 14 기업 문항)"])))
    bank = "우리 회사의 서비스나 사업 중 관심 있는 부분과 그 이유를 설명해 주세요."
    check("F9 면접 중 회사 맞춤 재작성이 회사 경력 전제면 은행 원문으로",
          not QG._keep_rewrite(INCIDENTS["F9 회사 경력 전제(attempt 14 기업 문항)"], bank))
    check("F8 면접 중 재작성에 깨진 낱말이면 은행 원문으로", not QG._keep_rewrite(INCIDENTS["F8 깨진 낱말(attempt 14 직무 문항)"], bank))
    check("F9 가정형 회사 맞춤 재작성은 그대로 쓴다", QG._keep_rewrite(hyp, bank))
    # 2026-11-20 지원 20(공고 4 · 헤드헌터랩_서울) 실제 출제 문장
    a20 = {
        "q8": "헤드헌터랩_서울에서 고객 상담 기록과 상품 문서를 활용한 RAG 파이프라인을 설계하고 개발하는 업무에서, 검색 결과와 생성 답변을 "
              "분석해 정확도를 개선하는 과정에서 어떤 도구 호출 또는 대화 흐름 문제를 해결해 본 적이 있나요?",
        "q4": "헤드헌터랩_서울의 RAG 파이프라인 설계 및 개발 경험을 바탕으로, 소비기한 인쇄 불량 검사 개선에서 쓴 기술을 헤드헌터랩_서울의 "
              "비슷한 업무에 어떻게 적용할지 설명해 주세요.",
        "q3": "헤드헌터랩_서울에서 소비기한 인쇄 불량 검사 개선 프로젝트에서 가장 어려웠던 문제를 다시 맡는다면, 어떤 방식으로 문제를 해결할지 "
              "설명해 주세요. 비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다",
    }
    check("F9 지원20-8번 '~해결해 본 적이 있나요' → 회사 경력 전제", QR.company_past(a20["q8"], "헤드헌터랩_서울"))
    check("F9 지원20-8번 재작성은 은행 원문으로", not QG._keep_rewrite(a20["q8"], bank, "헤드헌터랩_서울"))
    check("F9 지원20-4번 '(회사)의 … 경험을 바탕으로' → 회사 경력 전제", QR.company_past(a20["q4"], "헤드헌터랩_서울"))
    check("F9 지원20-3번 '(회사)에서 (이력서 프로젝트)에서' → 이중 장소", QR.resume_issue(a20["q3"], "헤드헌터랩_서울") == "company_past:project_mix",
          QR.resume_issue(a20["q3"], "헤드헌터랩_서울"))
    check("F9 실제 회사 이름(일반 표현 밖)도 잡는다", QR.company_past("A사에서 결제 시스템을 맡아 어떤 문제를 겪었는지 설명해 주세요.", "A사"))
    for i in range(len(Q.TEMPLATES)):
        t = Q.template_text(i, "소비기한 인쇄 불량 검사 개선", "헤드헌터랩_서울")
        check(f"F9 템플릿 {i + 1}은 이중 장소·경력 전제 아님", not QR.resume_issue(t, "헤드헌터랩_서울"), QR.resume_issue(t, "헤드헌터랩_서울"))
    g = Q.generic_template_text("헤드헌터랩_서울")
    check("F4 라벨 없는 대체 템플릿은 문장 규칙 통과", not QR.resume_issue(g, "헤드헌터랩_서울") and Q.one_sentence(g), QR.resume_issue(g, "헤드헌터랩_서울"))
    check("F4 라벨 없는 대체 템플릿은 소비기한 질문과 주제가 겹치지 않음",
          not QR.shared_topic(g, a20["q3"], ignore=["헤드헌터랩_서울"]), QR.shared_topic(g, a20["q3"], ignore=["헤드헌터랩_서울"]))
    a17 = "헤드헌터랩_서울에서 문서검색 RAG 시스템의 운영 협의를 맡는다면, 고객도 할 수 있는 반복 업무의 추가 위탁 요청을 어떤 기준으로 판단하시겠습니까?"
    check("끝 문장부호 — 마침표 없는 '~좋습니다' → 마침표", QR.end_punct(a20["q3"]).endswith("좋습니다."))
    check("끝 문장부호 — '~하시겠습니까' → 물음표", QR.end_punct("무엇을 확인하시겠습니까").endswith("?"))
    check("끝 문장부호 — 이미 있으면 그대로", QR.end_punct("설명해 주세요.") == "설명해 주세요.")
    check("F9 사내문서 질문의 가정형은 그대로 통과", not QR.company_past(a17, "헤드헌터랩_서울"))


def t_followup_judge():
    """F5·F6·F7이 judge_detail 안에서 실제로 꼬리질문을 막는지(가짜 모델)."""
    calls = []
    reply = {}

    def chat(system, user, schema, **kw):
        calls.append(user)
        return dict(reply)

    att = {"followups_used": 0}
    saved = [(llm, "chat_json", llm.chat_json)]
    try:
        llm.chat_json = chat
        q4 = {"parent_id": None, "text": ATT14["q4"], "gen_method": "llm", "category": "resume", "resume_ref": {"quote": "반사광 오검출"}}
        r = FA.judge_detail(att, q4, ATT14["q4_answer"])
        check("F5 judge: 비답변 → 꼬리질문 없음 · 모델 호출 0회", r["followup"] is None and not calls, (r, len(calls)))
        base = {"question_topic": "t", "answer_topic": "a", "addresses_question": False, "logic": 2, "relevance": 2,
                "inconsistent_with_resume": False, "generic_only": True}
        reply.update(base, followup_question=ATT14["q4_followup"])
        r = FA.judge_detail(att, q4, ATT14["q3_answer"])
        check("F7 judge: 원 질문 되풀이 → 생략", r["followup"] is None and r.get("skip") == "repeat_question", r)
        q3 = {"parent_id": None, "text": ATT14["q3"], "gen_method": "llm", "category": "resume", "resume_ref": {"quote": "반사광"}}
        reply.update(followup_question="그때 인터넷 환경을 재조명하신 구체적인 방법을 설명해 주세요.")
        r = FA.judge_detail(att, q3, ATT14["q3_answer"])
        check("F6 judge: 답변에 없는 전제(재조명) → 생략", r["followup"] is None and str(r.get("skip", "")).startswith("ungrounded"), r)
        reply.update(followup_question="검사 자동화를 시작할 때 어떤 판정 기준을 먼저 정하셨나요?")
        r = FA.judge_detail(att, q3, ATT14["q3_answer"])
        check("F6·F7 통과하는 정상 꼬리질문은 남는다", r["followup"] is not None, r)
        r = FA.judge_detail({"followups_used": config.MAX_FOLLOWUPS}, q3, ATT14["q3_answer"])
        check(f"F4 꼬리질문 전체 상한 {config.MAX_FOLLOWUPS}개", r["followup"] is None)
    finally:
        for m, n, v in saved:
            setattr(m, n, v)


# ---------------- Q4 꼬리질문 ----------------
def t_followup():
    bad = {"음성 조각 머리": "초반 테스: 골든셋을 어떻게 만들었는지 설명해 주세요.",
           "맺음 2번": "어떤 기준으로 정했나요? 그리고 결과는 어땠나요?",
           "쉼표 이음": "실패 유형의 비율을 어떻게 측정하고, 그 비율을 데이터셋 구성에 적용한 수치는 무엇인가요?",
           "낱말 사이 콜론": "매주 1,20:00건을 돌린 판정 점수가 떨어진 원인은 무엇인가요?"}
    for k, fq in bad.items():
        check("Q4 readable 탈락 · " + k, not FA.readable(fq)[0], fq)
    check("Q4 정상 꼬리질문 통과", FA.readable("그 판단 기준을 어떻게 정하셨는지 구체적으로 설명해 주세요.")[0])
    c = FA.strip_examples("11~20개 데이터셋을 확인하는 데 걸리는 평균 시간은 얼마나 되나요? (예: 1시간, 2시간 등)")
    check("Q4 괄호 예시 떼기", c == "11~20개 데이터셋을 확인하는 데 걸리는 평균 시간은 얼마나 되나요?", c)
    c = FA.strip_examples("정확히 어떤 방식으로 중복 수신을 막았는지 설명해 주세요. 예를 들어 웹훅 중복 빈도를 알려주세요.")
    check("Q4 '예를 들어' 둘째 문장 떼기", c == "정확히 어떤 방식으로 중복 수신을 막았는지 설명해 주세요.", c)
    c = FA.strip_examples("어떤 기준으로, 예를 들어 정확도나 속도로 판단했는지")
    check("Q4 예시를 떼면 질문이 안 되면 None(생략)", c is None, c)
    check("Q4 예시 없는 문장은 그대로", FA.strip_examples("어떤 기준으로 판단하셨나요?") == "어떤 기준으로 판단하셨나요?")


# ---------------- Q5 관문 2 ----------------
class _RetrieveEnv:
    """retrieve()만 돌린다. 사내문서 질문은 요청한 수만큼 돌려주고, 관문 2는 `keep` 수만큼만 통과시킨다(없으면 전부)."""

    def __init__(self, keep=None):
        self.keep = keep or {}
        self.filter_calls, self.redis_calls, self.bank_calls, self.asked = [], [], [], []

    def pick_internal(self, vecs, role, cat, n, used, terms, min_sim=None):
        self.asked.append((cat, n))
        return [{"id": None, "iq_id": f"IQ-{cat}-{i}", "doc_id": f"D-{cat}-{i}", "text": f"{cat} 사내문서 질문 {i}을 설명해 주세요.",
                 "dup_group": f"iq{cat}{i}", "subcategory": "", "competency": f"c{cat}{i}"} for i in range(n)]

    def filt(self, cands, job, cat):
        self.filter_calls.append(cat)
        k = self.keep.get(cat, len(cands or []))
        kept = (cands or [])[:k]
        return kept or None

    def pick_redis(self, v, role, cat, n, used):
        self.redis_calls.append((cat, n))
        return [{"id": None, "doc_id": f"R-{cat}-{i}", "text": f"{cat} 은행 문항 {i}을 설명해 주세요.",
                 "dup_group": f"r{cat}{i}", "subcategory": ""} for i in range(n)]

    def pick_bank(self, cat, n, exclude=()):
        self.bank_calls.append((cat, n))
        return [{"id": 900 + i, "text": f"DB {cat} 문항 {i}", "dup_group": f"db{cat}{i}", "subcategory": ""} for i in range(n)]

    def __enter__(self):
        self.saved = [(QG, n, getattr(QG, n)) for n in ("_pick_internal", "_filter_by_posting", "_pick_redis", "_query_vectors",
                                                        "_iq_terms", "_resume_bank_refs", "_enrich_character")]
        self.saved += [(Q, "pick_bank", Q.pick_bank), (Q, "pick_character", Q.pick_character)]
        QG._pick_internal, QG._filter_by_posting, QG._pick_redis = self.pick_internal, self.filt, self.pick_redis
        QG._query_vectors = lambda s: ([[0.1, 0.2]], {"mode": "baseline", "queries": 1})
        QG._iq_terms = lambda: ([], "ready")
        QG._resume_bank_refs = lambda s, role, used, iq_terms=None, seen_topics=None: []   # ④(2026-11-23) 인자 추가
        QG._enrich_character = lambda b: b
        Q.pick_bank = self.pick_bank
        Q.pick_character = lambda n, used=(), snap=None: self.pick_bank("personality", n)
        return self

    def __exit__(self, *a):
        for m, n, v in self.saved:
            setattr(m, n, v)

    def run(self):
        s = {"use_llm": True, "job": {"role_code": "ai_ax", "title": "AI"}, "resume_fallback": None, "snapshot": {}, "log": []}
        return QG.retrieve(s)


def t_posting_gate():
    need = dict(Q.PLAN)
    with _RetrieveEnv(keep={"job": 2}) as env:
        out = env.run()
        p = out["picks"]
        check("Q5 관문 2는 인성에 적용하지 않음", "personality" not in env.filter_calls, env.filter_calls)
        # 2026-11-23 ⑦: 깊이 = max(n*3, MIN_CAND_DEPTH). n=1(기업)이면 3개뿐이라 주제가 겹치면 전멸했다.
        check("Q5 관문 2 대상은 후보를 넉넉히 뽑는다(통과 질문이 풀에 있으면 백업으로 안 내려가게)",
              ("job", max(need["job"] * 3, QG.MIN_CAND_DEPTH)) in env.asked, env.asked)
        check("Q5 인성도 후보를 넉넉히 뽑는다(같은 문서 질문을 걸러도 n개가 남게, F4 세트 중복)",
              ("personality", max(need["personality"] * 3, QG.MIN_CAND_DEPTH)) in env.asked, env.asked)
        check("Q5 관문 2 통과 2개 → 직무 문항 수 유지", len(p["job"]) == need["job"], len(p["job"]))
        check("Q5 모자란 1개만 Redis에서 채움", ("job", 1) in env.redis_calls, env.redis_calls)
        check("Q5 남은 사내문서 질문은 그대로", sum(1 for b in p["job"] if b.get("iq_id")) == need["job"] - 1)
        check("Q5 출처 표기 internal_q+redis", out["retrieve_report"].get("job") == "internal_q+redis", out["retrieve_report"])
        check("Q5 기본 질문 수(인성·직무·기업) 유지",
              sum(len(p[c]) for c in ("personality", "job", "company")) == need["personality"] + need["job"] + need["company"])
    with _RetrieveEnv(keep={"company": 0, "job": 0}) as env:
        out = env.run()
        p = out["picks"]
        check("Q5 전부 탈락 → 백업 층으로 n개", len(p["job"]) == need["job"] and len(p["company"]) == need["company"],
              {k: len(v) for k, v in p.items()})
        check("Q5 전부 탈락 → 출처 redis", out["retrieve_report"].get("job") == "redis", out["retrieve_report"])

    class NoRedis(_RetrieveEnv):
        def pick_redis(self, v, role, cat, n, used):
            self.redis_calls.append((cat, n))
            return None
    with NoRedis(keep={"job": 1}) as env:
        out = env.run()
        p = out["picks"]
        check("Q5 Redis도 없으면 DB 은행으로 모자란 만큼", len(p["job"]) == need["job"] and ("job", 2) in env.bank_calls,
              (len(p["job"]), env.bank_calls))
        check("Q5 출처 internal_q+db", out["retrieve_report"].get("job") == "internal_q+db", out["retrieve_report"])


    with _RetrieveEnv() as env:
        out = env.run()
        check("Q5 통과 질문이 충분하면 백업을 부르지 않는다", not env.redis_calls and not env.bank_calls, (env.redis_calls, env.bank_calls))
    check("Q5 관문 2 비교 문장은 회사 자리·덧붙임을 뗀다",
          QG._gate_text("{회사}에서 RAG 검색 기능을 맡는다면 무엇을 먼저 확인하시겠습니까? 예를 들어 '검색 기준을 바꿨고, 그 결과 정확도가 달라졌다'처럼 설명해 주세요.")
          == "RAG 검색 기능을 맡는다면 무엇을 먼저 확인하시겠습니까?")


# ---------------- Q10 세트 안 같은 프로젝트·같은 조각 중복 금지(F4) ----------------
# 2026-11-20 사용자 지적(실제 면접): 아래 두 본질문이 한 세트에 같이 나갔다 — 둘 다 콜센터 AI 상담 프로젝트에서 나온 질문.
USER_DUP_JOB = "{회사}에서 상담사의 답변을 돕는 AI를 만든다고 가정할 때, 의도를 파악하기 어려운 문의에 적절히 대응하는지 확인하려면 검증 시나리오를 어떻게 구성하시겠습니까?"
USER_DUP_COMP = "{회사}에서 상담사가 AI 추천 답변을 검토해 사용하는 서비스를 운영한다면, 고객 문의 해결에 도움이 되는지 판단하기 위해 어떤 운영 지표를 중요하게 보시겠습니까?"


class _TopicEnv(_RetrieveEnv):
    """실제 풀의 출처 모양 그대로: 직무 후보 1순위 = 콜센터 공개판, 같은 조각(MSK-15#s6) 질문 2개, 같은 문서 다른 조각.
    기업 후보 1순위 = 콜센터 상세판(사용자 지적 문장)."""
    POOL = {
        "job": [("IQ-job-0a8afaac48-1", "PRJ-CC-PUB", "콜센터 AI 상담 구축 프로젝트 — 공개판", "PRJ-CC-PUB#s23", USER_DUP_JOB),
                ("IQ-job-f5ed205080-0", "MSK-15", "RAG 검색 모듈 단위코드 설명", "MSK-15#s6", "{회사}에서 RAG 검색 모듈을 구현한다면 빈 목록 응답 상태를 어떻게 설계하시겠습니까?"),
                ("IQ-job-f5ed205080-1", "MSK-15", "RAG 검색 모듈 단위코드 설명", "MSK-15#s6", "{회사}에서 RAG 검색 모듈을 점검한다면 어떤 정보를 확인하시겠습니까?"),
                ("IQ-job-x1", "MSK-30", "수요예측 대시보드 구축 프로젝트 — 상세판", "MSK-30#s2", "{회사}에서 수요예측 대시보드를 만든다면 무엇을 먼저 정하시겠습니까?"),
                ("IQ-job-x2", "MSK-33", "배치 서버 과부하 장애 배치 서버 과부하로 대시보드 지연 — 요약", "MSK-33#s1", "{회사}에서 배치 지연이 생긴다면 무엇부터 보시겠습니까?"),
                ("IQ-job-x3", "MSK-40", "아키텍처·인프라·보안 사전검토 가이드", "MSK-40#s3", "{회사}에서 보안 사전검토를 맡는다면 무엇을 확인하시겠습니까?")],
        "company": [("IQ-comp-770244baba-1", "MSK-08", "콜센터 AI 상담 구축 프로젝트 — 상세판", "MSK-08#s3", USER_DUP_COMP),
                    ("IQ-comp-y1", "MSK-12", "콜센터 AI 응답 오류 장애 콜센터 AI 응답 오류(환각) — 상세", "MSK-12#s2", "{회사}에서 상담 AI 환각을 줄인다면 무엇을 바꾸시겠습니까?"),
                    ("IQ-comp-y2", "MSK-50", "고객사 온보딩 플레이북 — 킥오프부터 안정화 90일까지", "MSK-50#s4", "{회사}에서 고객사 온보딩을 맡는다면 첫 주에 무엇을 하시겠습니까?")],
        "personality": [("IQ-pers-a", "TAL-01", "헤드헌터랩 인재상", "TAL-01#s24", "{회사}에서 동료와 의견이 다르다면 어떻게 하시겠습니까?"),
                        ("IQ-pers-b", "TAL-01", "헤드헌터랩 인재상", "TAL-01#s25", "{회사}에서 일정이 겹친다면 무엇을 먼저 하시겠습니까?"),
                        ("IQ-pers-c", "TAL-02", "인재상 행동지표 사례집", "TAL-02#s12", "{회사}에서 실수를 발견했다면 어떻게 알리시겠습니까?")],
    }

    def pick_internal(self, vecs, role, cat, n, used, terms, min_sim=None):
        self.asked.append((cat, n))
        return [{"id": None, "iq_id": iq, "doc_id": d, "doc_title": t, "chunk_id": c, "text": x, "dup_group": iq,
                 "subcategory": "", "competency": iq} for iq, d, t, c, x in self.POOL.get(cat, [])[:n]]


def t_set_topics():
    need = dict(Q.PLAN)
    with _TopicEnv() as env:
        out = env.run()
        p = out["picks"]
        texts = [b["text"] for c in ("personality", "job", "company") for b in p[c]]
        check("Q10 사용자 지적 두 질문(콜센터 AI 직무·기업)은 한 세트에 같이 안 나온다",
              not (USER_DUP_JOB in texts and USER_DUP_COMP in texts), texts)
        chunks = [b.get("chunk_id") for c in ("personality", "job", "company") for b in p[c] if b.get("iq_id")]
        check("Q10 같은 조각(MSK-15#s6) 질문 2개는 한 세트에 하나만", chunks.count("MSK-15#s6") <= 1, chunks)
        topics = [QG._topic_key(b) for c in ("personality", "job", "company") for b in p[c] if b.get("iq_id")]
        check("Q10 세트 안 사내문서 질문의 주제(프로젝트·문서)는 모두 다르다", len(topics) == len(set(topics)), topics)
        check("Q10 걸러도 기본 질문 수 유지(모자라면 Redis로 채움)",
              all(len(p[c]) == need[c] for c in ("personality", "job", "company")), {c: len(p[c]) for c in p})
    check("Q10 주제 열쇠: 콜센터 상세판·공개판·장애 기록은 한 묶음",
          len({QG._topic_key({"doc_title": t}) for t in ("콜센터 AI 상담 구축 프로젝트 — 상세판", "콜센터 AI 상담 구축 프로젝트 — 공개판",
                                                         "콜센터 AI 응답 오류 장애 콜센터 AI 응답 오류 — 요약")}) == 1)
    check("Q10 주제 열쇠: 수요예측 대시보드·배치 서버 장애는 한 묶음",
          QG._topic_key({"doc_title": "수요예측 대시보드 구축 프로젝트 — 공개판"})
          == QG._topic_key({"doc_title": "배치 서버 과부하 장애 배치 서버 과부하로 대시보드 지연 — 요약"}))
    check("Q10 주제 열쇠: 묶음 밖 문서는 문서 ID로 가른다",
          QG._topic_key({"doc_title": "제안서 작성 가이드", "doc_id": "MSK-60"}) != QG._topic_key({"doc_title": "프로젝트 가격 산정 모델", "doc_id": "MSK-61"}))
    # Q10-b 이력서 참고 문항도 세트 안 중복 검사를 거친다 (2026-11-23 사용자 승인 ④).
    # 사고: 공고 3 세트에서 이력서 참고 문항과 직무 질문이 둘 다 JD-AIAX-01에서 나와 같은 주제를 두 번 물었다.
    seen = set()
    first = QG._distinct_topics([{"iq_id": "IQ-job-a", "doc_id": "JD-AIAX-01", "doc_title": "AI/AX 직무기술서",
                                  "chunk_id": "JD-AIAX-01#s2", "text": "직무 질문"}], 1, seen)
    again = QG._distinct_topics([{"iq_id": "IQ-job-b", "doc_id": "JD-AIAX-01", "doc_title": "AI/AX 직무기술서",
                                  "chunk_id": "JD-AIAX-01#s17", "text": "참고 문항"}], 1, seen)
    check("Q10-b 같은 문서(JD-AIAX-01)의 참고 문항은 두 번째에서 걸린다", bool(first) and not again, (first, again))
    other = QG._distinct_topics([{"iq_id": "IQ-job-c", "doc_id": "MSK-62", "doc_title": "실패 제안 회고 사례집",
                                  "chunk_id": "MSK-62#s11", "text": "다른 문서"}], 1, seen)
    check("Q10-b 다른 문서의 참고 문항은 그대로 통과", bool(other), other)
    import inspect  # noqa: E402 — 호출부가 seen_topics를 실제로 넘기는지(④가 연결됐는지) 확인
    check("Q10-b `_resume_bank_refs`가 seen_topics를 받는다", "seen_topics" in inspect.signature(QG._resume_bank_refs).parameters)
    check("Q10-b retrieve가 seen_topics를 넘겨 부른다",
          "_resume_bank_refs(s, role, used, iq_terms, seen_topics)" in inspect.getsource(QG.retrieve))


# ---------------- Q11 백업 층 검사 ② · 후보 깊이 ⑦ · 이력서 참고 깊이 ⑧ (2026-11-23) ----------------
def t_backup_screen():
    import inspect  # noqa: E402 — 호출부가 실제로 연결됐는지(⑦·⑧) 확인
    job = {"id": 1, "role_code": "backend", "title": "백엔드 개발자 (상담 플랫폼 API)",
           "duties": "상담 티켓 API 설계·운영", "requirements": "FastAPI, PostgreSQL"}
    # ② 사용자가 실제로 받은 백업 층 질문 = 탈락해야 할 유형
    bad = [{"text": "비전문가에게 기술적인 내용을 설명해야 했던 경험과 그때 쓴 방법을 설명해 주세요."},   # abstract_experience
           {"text": "헤드헌터랩_경기에서 비전 모델 개발 업무를 맡아 어떤 접근을 취했는지 설명해 주세요."}]  # company_past
    for b in bad:
        check("Q11 백업 층 문장 검사 → 탈락 · " + b["text"][:16], bool(QG.backup_issue(b["text"])), QG.backup_issue(b["text"]))
    ok_text = "상담 티켓 목록 API가 느려졌을 때 어떤 지표를 먼저 확인하시겠습니까?"
    check("Q11 멀쩡한 백업 문항은 통과", not QG.backup_issue(ok_text), QG.backup_issue(ok_text))
    # 안전망 ⓐ — 전부 문장 규칙에 걸려도 **후보를 돌려준다**(8문항이 깨지면 안 된다)
    rep = {"checked": 0, "rule_rejects": []}
    out = QG.screen_backup(list(bad), job, "job", rep)
    check("Q11 전부 탈락 → 안전망이 원래 후보를 유지한다", len(out) == len(bad), out)
    check("Q11 안전망 작동이 기록된다", rep.get("fallback") == "rule", rep)
    check("Q11 탈락 사유가 기록된다", len(rep["rule_rejects"]) == 2, rep["rule_rejects"])
    check("Q11 검사 건수가 기록된다", rep["checked"] == 2, rep)
    check("Q11 빈 후보는 그대로", QG.screen_backup([], job, "job", {}) == [])
    # 인성은 공고 관련성을 적용하지 않는다(임베딩을 부르지 않는지까지 확인)
    called = []
    real = QG.llm.embed
    QG.llm.embed = lambda *a, **k: called.append(1) or real(*a, **k)
    try:
        QG.screen_backup([{"text": ok_text}], job, "personality", {})
    finally:
        QG.llm.embed = real
    check("Q11 인성 백업에는 공고 관련성을 걸지 않는다(임베딩 호출 없음)", not called, called)
    # ⑦ 후보 깊이 · ⑧ 참고 깊이 상수와 연결
    check("Q11 ⑦ 후보 깊이 하한 상수", QG.MIN_CAND_DEPTH >= 8, QG.MIN_CAND_DEPTH)
    check("Q11 ⑦ retrieve가 max(n*3, 하한)으로 부른다", "depth = max(n * 3, MIN_CAND_DEPTH)" in inspect.getsource(QG.retrieve))
    check("Q11 ⑧ 이력서 참고 후보 수 상수", QG.RESUME_BANK_DEPTH > 1, QG.RESUME_BANK_DEPTH)
    # 2026-11-23 ⓒ로 사내문서 경로만 하한이 0.55로 갈라졌다(Redis 경로는 0.60 그대로) — 아래 ⓒ 검사로 이어진다.
    check("Q11 ⑧ 참고 검색이 하한 상수를 인자로 넘긴다",
          "min_sim=RESUME_BANK_IQ_MIN_SIM" in inspect.getsource(QG._resume_bank_refs))
    check("Q11 ⑧-2 검색 결과에 raw_sim·role_bonus가 실린다",
          '"raw_sim"' in inspect.getsource(IQ.search_questions) and '"role_bonus"' in inspect.getsource(IQ.search_questions))
    # ⓒ(2026-11-23) 이력서 참고 하한 — 사내문서 경로만 0.55, Redis 경로는 0.60 그대로
    check("Q11 ⓒ 사내문서 참고 하한 0.55", QG.RESUME_BANK_IQ_MIN_SIM == 0.55, QG.RESUME_BANK_IQ_MIN_SIM)
    check("Q11 ⓒ Redis 참고 하한은 0.60 그대로", QG.RESUME_BANK_MIN_SIM == 0.60, QG.RESUME_BANK_MIN_SIM)
    src_rb = inspect.getsource(QG._resume_bank_refs)
    check("Q11 ⓒ 사내문서 경로가 새 하한을 쓴다", "min_sim=RESUME_BANK_IQ_MIN_SIM" in src_rb)
    check("Q11 ⓒ Redis 경로는 기존 하한을 쓴다", "< RESUME_BANK_MIN_SIM" in src_rb)
    # Q11-c ⑧ 버그 고정(2026-11-23 4차 재측정에서 발견) — `n`(요구 개수)을 깊이로 쓰면 "하한 통과 후보가 n개
    # 있어야 한다"가 돼, 실제 분포(0.55↑ 2개)에서 참고가 **한 건도** 붙지 않았다. 깊이는 `k`로만 넓힌다.
    asked = []

    def _spy_pick(vectors, role_code, cat, n, used, terms, min_sim=None, k=None):
        asked.append({"cat": cat, "n": n, "k": k, "min_sim": min_sim})
        if len(asked) > 9:
            return None
        # 하한을 통과한 후보가 **1개뿐인** 상황을 재현한다(지금 코드면 n=1이라 통과, 옛 코드면 n=5라 None)
        return None if n > 1 else [{"iq_id": "IQ-job-x", "doc_id": "MSK-12", "text": "{회사}에서 …를 맡는다면 …?",
                                    "dup_group": "g1", "competency": "g1", "sim": 0.6477, "raw_sim": 0.5977,
                                    "chunk_id": "MSK-12#s1", "doc_title": "단위코드", "doc_refs": [], "company_facts": []}]
    real_pick, real_embed = QG._pick_internal, QG.llm.embed
    QG._pick_internal, QG.llm.embed = _spy_pick, (lambda texts, **kw: [[0.1, 0.2, 0.3] for _ in texts])
    try:
        s = {"use_llm": True, "job": {"role_code": "ai_ax", "title": "AI"}, "candidates": [{"label": "A", "item": {"detail": "가"}},
                                                                                           {"label": "B", "item": {"detail": "나"}}],
             "snapshot": {}, "log": []}
        refs = QG._resume_bank_refs(s, "ai_ax", set(), ["금지"], set())
        got_iq = [r for r in (refs or []) if r and r.get("iq_id")]
        rb_calls = [a for a in asked if a["cat"] == "job"]
        check("Q11-c ⑧ 참고 검색은 요구 개수 1로 부른다", rb_calls and all(a["n"] == 1 for a in rb_calls), rb_calls)
        check("Q11-c ⑧ 깊이는 k로 넓힌다", rb_calls and all((a["k"] or 0) > 1 for a in rb_calls), rb_calls)
        check("Q11-c 하한 통과 후보가 1개뿐이어도 참고가 붙는다", len(got_iq) >= 1, refs)
    finally:
        QG._pick_internal, QG.llm.embed = real_pick, real_embed


# ---------------- Q12 ⑨ 문장 호응 깨짐 (2026-11-23) ----------------
def t_malformed_ask():
    import inspect  # noqa: E402 — 호출부 연결 확인용
    # 사용자가 받은 문장(Redis 은행 원문) — 반드시 탈락
    bad = ["현장에서 불량 기준이 모호할 때, 어떻게 데이터 수집과 라벨링 기준을 정의하고 모델 성능을 개선해 주세요.",
           "무엇을 먼저 점검하고 다음 단계를 진행해 주세요.",
           "왜 그런 선택을 했고 다음에는 다르게 적용하십시오."]
    for t in bad:
        check("Q12 ⑨ 의문사+명령형 맺음 → 탈락 · " + t[:18], QR.malformed_ask(t), t)
        check("Q12 ⑨ 백업 층 검사에도 걸린다 · " + t[:12], QG.backup_issue(t) == "malformed_ask", QG.backup_issue(t))
    # 오탐 0 — 특히 '~는지 설명해 주세요'·'~처럼 설명해 주세요'(인성 질문의 답변 형식 예시)는 통과해야 한다
    good = ["현장에서 불량 기준이 모호할 때 무엇을 먼저 확인하시는지 설명해 주세요.",
            "제조 현장에서 학습 데이터와 실제 서비스 데이터의 분포가 달라졌을 때, 어떻게 감지하고 대응할지 설명해 주세요.",
            "객체 탐지 모델의 성능을 평가할 때 mAP가 무엇을 의미하는지 설명해 주세요.",
            "학습이 잘 되지 않을 때 확인하는 순서를 설명해 주세요.",
            "정밀도와 재현율의 차이를 설명하고, 어떤 것을 더 중시해야 하는지 이유를 설명해 주세요.",
            "비전 모델 개발 업무를 맡아 현장 적용 시 오탐과 미탐 문제가 생긴다면, 어떻게 접근할지 설명해 주세요.",
            "우리 회사에 지원한 이유와 입사 후 하고 싶은 일을 설명해 주세요.",
            "헤드헌터랩_경기에서 규정 안내 모델 개발을 맡는다면, 허용할 실패 수준을 정하기 위해 업무 담당자와 어떻게 "
            "의견을 조율하시겠습니까? 예를 들어 '의견을 이렇게 조율했고, 그 결과 합의 내용이 이렇게 달라졌다'처럼 설명해 주세요."]
    for t in good:
        check("Q12 ⑨ 정상 문장은 통과 · " + t[:20], not QR.malformed_ask(t), t)
    check("Q12 ⑨ A18 검증에도 연결됐다", "malformed_ask" in inspect.getsource(IQ.validate))


# ---------------- Q13 F11 "다시 맡는다면" · Q14 F12 회사명 반복 (2026-11-23 사용자 지적) ----------------
def t_company_again():
    import inspect  # noqa: E402 — 호출부 연결 확인용
    CO = "헤드헌터랩_경기"
    # 사용자가 지적한 5차 Q3·Q4 원문 — 반드시 탈락
    bad = ["헤드헌터랩_경기에서 소비기한 인쇄 불량 검사를 다시 맡는다면, 반사광으로 인한 오검출 문제를 어떻게 해결하시겠습니까? "
           "비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다.",
           "헤드헌터랩_경기에서 비전 검사 시스템을 다시 맡는다면, 영상 전처리 단계에서 입력 영상 밝기 조절과 작업자 육안 판정을 "
           "비교해 결과를 기록하는 방식을 어떻게 개선하시겠습니까? 비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다."]
    for t in bad:
        check("Q13 F11 회사명+'다시' → 탈락 · " + t[:22], QR.company_again(t, CO) == "company_again", QR.company_again(t, CO))
        check("Q13 F11 이력서 질문 검사에도 걸린다 · " + t[:14], QR.resume_issue(t, CO) == "company_again", QR.resume_issue(t, CO))
    # 허용: 이전 회사·프로젝트 이름을 같은 문장에 밝힌 형태
    ok_prev = ("다온팩토리솔루션에서 하신 소비기한 인쇄 불량 검사를, 헤드헌터랩_경기에서 같은 문제로 마주한다면 어떤 방식으로 "
               "해결하시겠습니까? 비슷한 경험이 있다면 그 경험을 바탕으로 답해 주셔도 좋습니다.")
    check("Q13 F11 이전 회사명을 밝히면 통과", not QR.company_again(ok_prev, CO, ("다온팩토리솔루션",)), QR.company_again(ok_prev, CO, ("다온팩토리솔루션",)))
    check("Q13 F11 '다시'가 함께 있어도 이전 회사명이 있으면 통과",
          not QR.company_again("다온팩토리솔루션에서 하신 검사를 헤드헌터랩_경기에서 다시 맡는다면 어떻게 하시겠습니까?", CO, ("다온팩토리솔루션",)))
    # 템플릿 — 새 문구는 전부 통과해야 한다('다시' 제거 확인)
    for i in range(len(Q.TEMPLATES)):
        t = Q.template_text(i, "소비기한 인쇄 불량 검사 개선", CO)
        check(f"Q13 템플릿 {i + 1}에 F11 위반 없음", not QR.company_again(t, CO), t)
    tp = Q.template_text(0, "소비기한 인쇄 불량 검사", CO, "다온팩토리솔루션")
    check("Q13 이전 회사명을 알면 그 이름을 밝히는 템플릿", "다온팩토리솔루션에서 하신" in tp and not QR.company_again(tp, CO, ("다온팩토리솔루션",)), tp)
    check("Q13 TEMPLATE_GENERIC에 '다시' 없음", "다시" not in Q.TEMPLATE_GENERIC, Q.TEMPLATE_GENERIC)
    check("Q13 ANGLES에 '회사에서 다시' 없음", not any("다시" in a for a in Q.ANGLES), Q.ANGLES)
    check("Q13 TEMPLATE_2는 그대로(원래 '다시'가 없다)", "적용한다고 가정할 때" in Q.TEMPLATE_2)
    check("Q13 백업 층 검사에도 걸린다", QG.backup_issue(bad[0], CO) == "company_again" if "company" in
          inspect.signature(QG.backup_issue).parameters else True)
    check("Q13 A18 검증에 연결됐다", "company_again" in inspect.getsource(IQ.validate))
    # 이전 회사 추정은 보수적이어야 한다 — 경력이 둘 이상이면 프로젝트에 회사를 붙이지 않는다
    snap2 = {"career": [{"company": "다온팩토리솔루션", "detail": "가"}, {"company": "머신비전 장비사", "detail": "나"}],
             "projects": [{"name": "소비기한 인쇄 불량 검사", "detail": "다"}]}
    cands2 = {c["label"]: c for c in Q.resume_candidates(snap2)}
    check("Q13 경력이 둘이면 프로젝트의 이전 회사는 비워 둔다", cands2["소비기한 인쇄 불량 검사"]["prev_company"] == "",
          cands2["소비기한 인쇄 불량 검사"])
    check("Q13 경력 항목은 그 회사명이 이전 회사", cands2["다온팩토리솔루션"]["prev_company"] == "다온팩토리솔루션")
    snap1 = {"career": [{"company": "다온팩토리솔루션", "detail": "가"}], "projects": [{"name": "소비기한 인쇄 불량 검사", "detail": "다"}]}
    c1 = {c["label"]: c for c in Q.resume_candidates(snap1)}
    check("Q13 경력이 하나면 프로젝트도 그 회사로 잇는다", c1["소비기한 인쇄 불량 검사"]["prev_company"] == "다온팩토리솔루션")


def t_nested_abstract():
    """Q15 F13(2026-11-23 사용자 지적) — 추상 명사구 겹침. 사용자 원문 Q8을 '반드시 탈락'으로 고정한다."""
    import inspect  # noqa: E402 — 호출부 연결 확인용
    q8 = ("헤드헌터랩_경기에서 AI 모델 배포 시 검증 결과가 누락되지 않도록 기록하는 업무를 맡는다면, 검증 증거를 "
          "남기는 업무 방식의 어떤 점이 본인의 지원 동기와 맞는다고 생각하십니까?")
    check("Q15 F13 사용자 지적 Q8 → 탈락", QR.nested_abstract(q8), QR.NESTED_ABSTRACT.findall(QR.core(q8)))
    check("Q15 F13 겹친 조각을 두 개 찾는다", len(QR.NESTED_ABSTRACT.findall(QR.core(q8))) >= 2)
    check("Q15 F13 백업 층 검사에도 걸린다", QG.backup_issue(q8) in ("nested_abstract", "company_again"), QG.backup_issue(q8))
    check("Q15 F13 A18 검증에 연결됐다", "nested_abstract" in inspect.getsource(IQ.validate))
    # 오탐 0 — '~하는 업무'를 **한 번만** 쓴 문장은 정상이다(풀 602개 중 44개가 그 형태였고 그 자체는 문제가 아니다)
    ok = ["{회사}에서 부서 사업계획서 제출 전 인력의 중복 배치를 확인하는 업무를 맡는다면, 계획의 실행 가능성을 높이는 데 "
          "사전 확인이 어떤 의미가 있다고 보십니까?",
          "헤드헌터랩_서울에서 고객 보고서용 데이터를 분석한다고 가정할 때, 집계한 데이터 총량이 고객의 기존 보고서와 "
          "다르다면 차이의 원인을 파악하기 위해 무엇을 확인하시겠습니까?",
          "{회사}에서 API 기능 구현을 맡는다면, 요청 처리 중 오류가 발생했을 때 처리 흐름을 확인할 수 있도록 로그를 "
          "어떻게 설계하시겠습니까?",
          "헤드헌터랩_경기에서 규정 안내 모델 개발을 맡는다면, 허용할 실패 수준을 정하기 위해 업무 담당자와 어떻게 "
          "의견을 조율하시겠습니까? 예를 들어 '의견을 이렇게 조율했고, 그 결과 합의 내용이 이렇게 달라졌다'처럼 설명해 주세요."]
    for t in ok:
        check("Q15 F13 '~하는 업무'를 한 번만 쓰면 통과 · " + t[:18], not QR.nested_abstract(t), t)
    # 지시문: 기업 카테고리 지침이 구체성을 **요구**하는지(예전에는 금지만 있었다)
    g = IQ.CAT_GUIDE["company"]
    check("Q15 F13 기업 지침이 구체적 대상을 요구한다", "그대로 남긴다" in g or "구체적인 대상" in g, g[:60])
    check("Q15 F13 기업 지침의 금지가 세 가지로 좁혀졌다", "고객사 이름" in g and "내부 금액" in g and "사건 번호" in g)
    check("Q15 F13 기업 지침에 추상 명사구 겹침 금지가 있다", "겹쳐 쓰지 않는다" in g)


def t_company_head():
    import inspect  # noqa: E402 — 호출부 연결 확인용
    # F12: 세트 8문항 중 회사명을 쓰는 문항은 상한까지만
    job = {"company_name": "헤드헌터랩_경기"}
    st = {"company_used": 0, "company_dropped": 0}
    outs = [QG.fill_company("{회사}에서 A를 맡는다면 무엇을 확인하시겠습니까?", job, st) for _ in range(8)]
    named = [t for t in outs if "헤드헌터랩_경기" in t]
    check(f"Q14 F12 회사명 쓰는 문항은 {QG.COMPANY_NAME_MAX}개 이하", len(named) <= QG.COMPANY_NAME_MAX, len(named))
    check("Q14 F12 나머지는 '이 직무에서'로 맥락 유지(F1 유지)",
          all("이 직무에서" in t for t in outs if "헤드헌터랩_경기" not in t), outs[-1])
    check("Q14 F12 회사명 문항의 문두 표현이 하나가 아니다", len({t.split(" ")[0] for t in named}) > 1, named)
    check("Q14 F12 state 없이 부르면 기존 동작(전부 회사명)",
          "헤드헌터랩_경기" in QG.fill_company("{회사}에서 A를 맡는다면?", job))
    check("Q14 F12 버린 수가 기록된다", st["company_dropped"] == 8 - len(named), st)


# ---------------- Q6 A6·A7은 A17을 다시 쓰지 않는다 ----------------
def t_no_rewrite():
    calls = []
    revised = "헤드헌터랩_경기에서 비전 모델 서빙 환경의 설정을 검증하기 위해 어떤 판단 과정을 거쳤는지 설명해 주세요."

    def chat(system, user, schema, **kw):
        calls.append(user)
        props = schema.get("properties") or {}
        if "scores" in props:
            return {"scores": [2] * 6, "pass": False, "problems": ["x"], "revised_question": revised}
        return {"answerable": False, "asks": ["a", "b"], "revised_question": "좁힌 질문을 설명해 주세요."}

    a17 = {"category": "job", "eval_items": ["expertise"], "text": "AI 모델 서빙 설정에서 가중치 합이 100이 아니면 실패하는 규칙이 있는 상황이라면, 무엇을 먼저 검증하시겠습니까?",
           "orig": "same", "gen_method": "internal_q:IQ-job-x-0", "doc_refs": ["D#s1"], "iq_source": True}
    a17["orig"] = a17["text"]
    other = {"category": "job", "eval_items": ["expertise"], "text": "백엔드 API 응답이 느려진 상황이라면 무엇을 먼저 확인하시겠습니까?",
             "orig": "백엔드 API 응답이 느려진 상황이라면 무엇을 먼저 확인하시겠습니까?", "gen_method": "redis:job:1"}
    saved = [(llm, "chat_json", llm.chat_json), (QG, "REVIEW", QG.REVIEW), (Q, "_too_broad", Q._too_broad)]
    try:
        llm.chat_json, QG.REVIEW = chat, True
        s = {"use_llm": True, "job": {"role_name": "AI", "company_name": "C"}, "drafts": [dict(a17), dict(other)],
             "snapshot": {}, "forbid_terms": ["금지"], "log": []}
        out = QG.evaluate_philosophy(s)
        d0, d1 = out["drafts"]
        check("Q6 A6는 A17 문장을 그대로 둔다", d0["text"] == a17["text"], d0["text"])
        check("Q6 A6는 A17에 모델을 부르지 않는다", not any(a17["text"] in u for u in calls), len(calls))
        check("Q6 A6가 고친 문장이 규칙(회사 전제·두 개 묻기)을 어기면 받지 않는다", d1["text"] != revised, d1["text"])
        Q._too_broad = lambda q, secs: True          # A7: 전부 '넓다'고 보게 해도
        calls.clear()
        out = QG.evaluate_answerability({"use_llm": True, "drafts": [dict(a17), dict(other)], "snapshot": {}, "forbid_terms": [], "log": []})
        check("Q6 A7도 A17은 그대로", out["drafts"][0]["text"] == a17["text"], out["drafts"][0]["text"])
        check("Q6 A7은 A17이 아닌 문항만 좁힌다", len(calls) == 1, len(calls))
    finally:
        for m, n, v in saved:
            setattr(m, n, v)


# ---------------- Q7 생성 모델 기록 ----------------
def t_gen_model():
    chunk = {"chunk_id": "TAL-01#001#0", "body": "가치 기준 본문 " * 30,
             "meta": {"doc_id": "TAL-01", "sec": "s1", "doc_title": "인재상", "sec_title": "협업", "doc_type": "talent"}}
    gen = {"questions": [{"question": "q", "intent": "i", "competency": "c", "signals": "s", "keywords": [], "facts": []}]}
    saved = [(llm, "openai_chat_json", llm.openai_chat_json), (llm, "chat_json", llm.chat_json),
             (config, "IQ_GEN_PROVIDER", config.IQ_GEN_PROVIDER)]
    try:
        llm.chat_json = lambda *a, **k: gen
        llm.openai_chat_json = lambda *a, **k: (gen, {"model": "gpt-test", "prompt_tokens": 10, "completion_tokens": 5})
        config.IQ_GEN_PROVIDER = "openai"
        _, u = IQ.generate_for_chunk(chunk, "personality")
        check("Q7 OpenAI 성공 → openai:<모델>", u.get("gen_model") == "openai:gpt-test", u)

        def boom(*a, **k):
            raise llm.LLMError("key expired")
        llm.openai_chat_json = boom
        _, u = IQ.generate_for_chunk(chunk, "personality")
        check("Q7 OpenAI 실패 → local:<모델> + fallback 사유(멈추지 않음)",
              u.get("gen_model") == "local:" + config.LLM_MODEL and u.get("fallback") == "LLMError", u)
        config.IQ_GEN_PROVIDER = "local"
        _, u = IQ.generate_for_chunk(chunk, "personality")
        check("Q7 로컬 설정 → local:<모델>", u.get("gen_model") == "local:" + config.LLM_MODEL, u)
        m = IQ._meta_of({"question": "q"}, chunk, "personality", "openai:gpt-test")
        check("Q7 메타데이터 gen_model에 그대로 저장", m["gen_model"] == "openai:gpt-test", m.get("gen_model"))
    finally:
        for mod, n, v in saved:
            setattr(mod, n, v)


# ---------------- Q8 인성 재료 문서 ----------------
def t_personality_docs():
    from app import internal_store
    from app.agents import internal_forbid
    rows = [{"id": f"{d}#00{i}#0", "document": "본문 " * 120,
             "metadata": {"doc_id": d, "doc_type": t, "disclosure": "public", "status": "active", "sec": f"s{i}"}}
            for d, t in (("TAL-01", "talent"), ("HR-01", "attendance"), ("HR-02", "hr"), ("MSK-06", "hiring_guide"),
                         ("MSK-26", "casebook"), ("JD-1", "jd")) for i in range(2)]
    saved = [(internal_store, "chunks", internal_store.chunks), (internal_forbid, "load_terms", internal_forbid.load_terms),
             (internal_forbid, "chunk_terms", internal_forbid.chunk_terms), (internal_forbid, "find_terms", internal_forbid.find_terms),
             (internal_forbid, "masked_hits", internal_forbid.masked_hits)]
    try:
        internal_store.chunks = lambda name, documents=False: rows
        internal_forbid.load_terms = lambda r=None: ["x"]
        internal_forbid.chunk_terms = lambda t: t
        internal_forbid.find_terms = lambda text, terms: []
        internal_forbid.masked_hits = lambda text: []
        docs = {c["meta"]["doc_id"] for c in IQ.select_chunks("personality")}
        check("Q8 인성 재료 = talent 문서만", docs == {"TAL-01"}, docs)
        jdocs = {c["meta"]["doc_id"] for c in IQ.select_chunks("job")}
        check("Q8 직무 재료는 그대로", jdocs == {"JD-1"}, jdocs)
    finally:
        for m, n, v in saved:
            setattr(m, n, v)


# ---------------- Q9 허용 덧붙임 ----------------
def t_suffix():
    q = GOOD["지시문 좋은 예(덧붙임 포함)"]
    check("Q9 덧붙임은 한 문장 검사에서 뗀다", IQ._one_sentence(q))
    check("Q9 덧붙임이 없는 두 문장은 여전히 탈락", not IQ._one_sentence("무엇을 고르시겠습니까? 그 이유도 설명해 주세요."))


def t_fewshot():
    """few-shot 검증 장치 1층(2026-11-20): 등록부 ↔ 지시문 일치 · 예문 고유어 새기 · 베끼기 점수."""
    from app import prompts
    from app.agents import fewshot_guard as FG
    for e in FG.REGISTRY:
        raw = (prompts.DIR / f"{e['prompt']}.md").read_text(encoding="utf-8").replace("{{", "{").replace("}}", "}")
        check(f"FS 등록 예문이 지시문에 있다 · {e['id']}", e["text"] in raw)
    q = "{회사}에서 수요예측 모델을 운영하는 중에 개발팀이 아닌 회계팀에 업무를 브리핑해야 한다면, 무엇을 먼저 설명하시겠습니까?"
    check("FS 근거에 없는 예문 고유어(회계팀·브리핑) → 새어 듦", FG.leak_terms(q, "수요예측 모델 운영 기준과 재학습 주기") == ["브리핑", "회계팀"],
          FG.leak_terms(q, "수요예측 모델 운영 기준"))
    check("FS 근거에 있으면 새어 든 것 아님", not FG.leak_terms(q, "재무회계팀에 월간 업무를 브리핑한다"))
    check("FS 일반 묘사어(답변 도우미)는 탈락 사유 아님",
          not FG.leak_terms("{회사}에서 상담사용 AI 답변 도우미를 개선한다고 가정할 때, 무엇을 먼저 확인하시겠습니까?", "상담 AI 추천 답변"))
    jid, jac = FG.copy_score("{회사}에서 생산 라인 오검출 문제를 해결하는 중에 개발팀이 아닌 회계팀에 팀 업무를 브리핑해야 한다면, 무엇을 먼저 설명하시겠습니까?")
    check("FS 예문을 그대로 쓰면 베끼기 점수 최고", jid == "IQ-6-3-good-F2-accounting" and jac >= FG.COPY_JACCARD, (jid, jac))
    _, jac2 = FG.copy_score("{회사}에서 배치 작업 실패가 반복된다면, 재시도 간격을 어떤 기준으로 정하시겠습니까?")
    check("FS 다른 내용은 베끼기 점수 낮음", jac2 < FG.COPY_JACCARD, jac2)


def main():
    t_fewshot()
    t_incidents()
    t_good()
    t_resume()
    t_feedback()
    t_followup_judge()
    t_followup()
    t_posting_gate()
    t_set_topics()
    t_backup_screen()
    t_malformed_ask()
    t_company_again()
    t_nested_abstract()
    t_company_head()
    t_no_rewrite()
    t_gen_model()
    t_personality_docs()
    t_suffix()
    total = len(OK) + len(FAIL)
    print(f"\n{len(OK)}/{total}" + (f"  실패: {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
