"""Initial data (runs once when the companies table is empty).

Question bank: the full DB (IT직무·인성 면접질문 xlsx) is not in this repo. This is a small sample in the same
category structure; replace via the import format in TRD §6.1.
"""
from datetime import timedelta

from . import db, security
from .routes_applicant import KST

ROLES = [("ai_ax", "AI/AX 엔지니어", "active"), ("backend", "백엔드 개발자", "preparing"), ("frontend", "프론트엔드 개발자", "preparing"),
         ("data_eng", "데이터 엔지니어", "preparing"), ("data_sci", "데이터 사이언티스트·분석가", "preparing"),
         ("devops", "DevOps·클라우드 엔지니어", "preparing"), ("mobile", "모바일 앱 개발자", "preparing")]

PERSONALITY = [
    ("협업", "팀 프로젝트에서 의견이 충돌했을 때 어떻게 조율했는지 한 가지 사례를 들어 설명해 주세요.", "p-conflict"),
    ("협업", "동료와 의견이 달랐던 경험과 그때 본인이 맡은 역할을 설명해 주세요.", "p-conflict"),
    ("책임감", "맡은 일에서 예상하지 못한 문제가 생겼을 때 끝까지 해결한 경험을 설명해 주세요.", "p-ownership"),
    ("성장", "최근 1년 동안 스스로 학습해서 익힌 기술이나 지식과 그 방법을 설명해 주세요.", "p-growth"),
    ("성장", "실패했던 경험에서 무엇을 배웠고 이후 어떻게 다르게 행동했는지 설명해 주세요.", "p-failure"),
    ("소통", "비전문가에게 기술적인 내용을 설명해야 했던 경험과 그때 쓴 방법을 설명해 주세요.", "p-communication"),
    ("문제해결", "제한된 시간 안에 여러 일을 처리해야 했을 때 우선순위를 어떻게 정했는지 설명해 주세요.", "p-priority"),
    ("윤리", "데이터나 AI 결과를 다룰 때 윤리적으로 고민했던 상황이 있다면 설명해 주세요.", "p-ethics"),
]
COMPANY = [
    ("지원동기", "우리 회사에 지원한 이유와 입사 후 하고 싶은 일을 설명해 주세요.", "c-motive"),
    ("지원동기", "이 직무에 지원하게 된 계기를 설명해 주세요.", "c-motive-role"),
    ("동기/지원 이유", "우리 회사의 서비스나 사업 중 관심 있는 부분과 그 이유를 설명해 주세요.", "c-interest"),
    ("조직 문화 적합성", "본인이 일하기 좋은 팀 문화는 어떤 모습이고, 그 안에서 어떤 역할을 하고 싶은지 설명해 주세요.", "c-culture"),
    ("조직 문화 적합성", "빠르게 변하는 환경에서 일했던 경험과 그때 적응한 방법을 설명해 주세요.", "c-change"),
]
JOB = [
    ("머신러닝", "과적합이 무엇인지 설명하고, 이를 줄이기 위해 써 본 방법을 한 가지 설명해 주세요.", "j-overfit"),
    ("머신러닝", "정밀도와 재현율의 차이를 설명하고, 어떤 상황에서 재현율을 더 중시하는지 예를 들어 주세요.", "j-pr"),
    ("머신러닝", "학습 데이터와 실제 서비스 데이터의 분포가 달라졌을 때 어떻게 감지하고 대응할지 설명해 주세요.", "j-drift"),
    ("딥러닝", "트랜스포머의 셀프 어텐션이 어떤 문제를 해결하는지 설명해 주세요.", "j-attention"),
    ("딥러닝", "학습이 잘 되지 않을 때 확인하는 순서를 설명해 주세요.", "j-debug-train"),
    ("딥러닝", "배치 정규화의 역할과 추론 시 동작 차이를 설명해 주세요.", "j-bn"),
    ("LLM·RAG", "RAG 시스템에서 검색 품질이 낮을 때 개선할 수 있는 방법을 한 가지 설명해 주세요.", "j-rag"),
    ("LLM·RAG", "LLM이 사실과 다른 답을 만드는 문제를 줄이기 위해 시스템 차원에서 할 수 있는 방법을 설명해 주세요.", "j-hallucination"),
    ("LLM·RAG", "프롬프트 엔지니어링과 파인튜닝 중 하나를 선택해야 할 때의 기준을 설명해 주세요.", "j-ft"),
    ("컴퓨터 비전", "객체 탐지 모델의 성능을 평가할 때 mAP가 무엇을 의미하는지 설명해 주세요.", "j-map"),
    ("데이터 사이언스", "결측치가 많은 데이터를 다룰 때의 처리 방법과 선택 기준을 설명해 주세요.", "j-missing"),
    ("데이터 사이언스", "A/B 테스트 결과를 해석할 때 주의할 점을 한 가지 설명해 주세요.", "j-ab"),
    ("파이썬", "파이썬에서 리스트와 제너레이터의 차이와 대용량 데이터 처리 시 선택 기준을 설명해 주세요.", "j-generator"),
    ("MLOps", "모델을 서비스에 배포한 뒤 모니터링해야 할 지표를 설명해 주세요.", "j-monitor"),
    ("MLOps", "온프레미스 GPU 서버에서 LLM 추론 비용과 지연 시간을 줄이는 방법을 한 가지 설명해 주세요.", "j-serving"),
    ("AX", "현업 업무 하나를 AI로 자동화한다면 무엇을 고르고 어떤 순서로 진행할지 설명해 주세요.", "j-ax"),
]

PROBLEMS = [
    ("basic", "이동 평균", "정수 리스트 nums와 정수 k가 주어질 때, 길이 k인 연속 구간의 평균을 앞에서부터 차례로 담은 리스트를 반환하세요. "
     "평균은 소수점 둘째 자리에서 반올림합니다(round(x, 2)). k가 nums 길이보다 크면 빈 리스트를 반환합니다.",
     "def moving_average(nums, k):\n    # 여기에 코드를 작성하세요\n    return []\n", "moving_average",
     [([[1, 2, 3, 4, 5], 2], [1.5, 2.5, 3.5, 4.5], True), ([[10, 20, 30], 3], [20.0], True), ([[1, 2], 3], [], False),
      ([[5, 5, 5, 5], 1], [5.0, 5.0, 5.0, 5.0], False), ([[1, 2, 4], 2], [1.5, 3.0], False)]),
    ("basic", "단어 빈도 상위 k개", "문자열 text를 공백으로 나눈 단어 중 가장 많이 나온 k개를 반환하세요. 빈도가 같으면 사전 순으로 앞선 단어가 먼저입니다. "
     "대소문자는 구분하지 않고 소문자로 바꿔 셉니다.",
     "def top_k_words(text, k):\n    # 여기에 코드를 작성하세요\n    return []\n", "top_k_words",
     [(["a b a c b a", 2], ["a", "b"], True), (["Apple apple banana", 1], ["apple"], True), (["x y z", 2], ["x", "y"], False),
      (["b a", 5], ["a", "b"], False), (["The the THE cat", 2], ["the", "cat"], False)]),
    ("applied", "혼동 행렬 지표", "실제 레이블 y_true와 예측 레이블 y_pred(0 또는 1의 리스트)가 주어질 때 [precision, recall, f1]을 반환하세요. "
     "각 값은 round(x, 3)으로 반올림하고, 분모가 0이면 해당 값은 0.0입니다.",
     "def binary_metrics(y_true, y_pred):\n    # 여기에 코드를 작성하세요\n    return [0.0, 0.0, 0.0]\n", "binary_metrics",
     [([[1, 0, 1, 1], [1, 0, 0, 1]], [1.0, 0.667, 0.8], True), ([[0, 0], [0, 0]], [0.0, 0.0, 0.0], True),
      ([[1, 1, 0, 0], [1, 1, 1, 1]], [0.5, 1.0, 0.667], False), ([[1, 0, 1], [0, 1, 0]], [0.0, 0.0, 0.0], False)]),
    ("applied", "문서 청크 분할", "문자열 text를 길이 size의 조각으로 나누되, 이웃한 조각이 overlap 글자만큼 겹치도록 나눈 리스트를 반환하세요. "
     "마지막 조각은 size보다 짧을 수 있고, 이전 조각에 완전히 포함되는 조각은 만들지 않습니다. overlap < size가 보장됩니다.",
     "def chunk_text(text, size, overlap):\n    # 여기에 코드를 작성하세요\n    return []\n", "chunk_text",
     [(["abcdefgh", 4, 1], ["abcd", "defg", "gh"], True), (["abc", 5, 2], ["abc"], True), (["abcdef", 3, 0], ["abc", "def"], False),
      (["abcdefg", 3, 2], ["abc", "bcd", "cde", "def", "efg"], False)]),
]


def seed():
    if db.one("SELECT 1 FROM companies LIMIT 1"):
        return
    for i, (code, name, status) in enumerate(ROLES):
        db.run("INSERT INTO job_roles (code, name, status, sort) VALUES (%s,%s,%s,%s)", code, name, status, i)
    c = db.run("""INSERT INTO companies (name, intro) VALUES (%s,%s) RETURNING id""", "헤드헌터랩",
               "사내 업무를 AI로 자동화하는 AX 솔루션을 만드는 데모 회사입니다. 문서 검색·요약 에이전트와 사내 LLM 플랫폼을 운영합니다.")
    db.run("""INSERT INTO users (role, company_id, email, password_hash, name, phone, email_verified, must_change_password)
              VALUES ('recruiter',%s,'recruiter@headhunter.ai',%s,'김담당','010-1111-2222',true,true)""", c["id"], security.hash_pw("changeme123!"))
    db.run("""INSERT INTO users (role, email, password_hash, name, email_verified)
              VALUES ('operator','ops@headhunter.ai',%s,'운영자',true)""", security.hash_pw("operator123!"))
    db.run("""INSERT INTO users (role, email, password_hash, name, phone, email_verified, age14_confirmed)
              VALUES ('applicant','applicant@headhunter.ai',%s,'이지원','010-3333-4444',true,true)""", security.hash_pw("test1234!"))
    from datetime import datetime
    today = datetime.now(KST).date()
    j = db.run("""INSERT INTO jobs (company_id, role_code, title, company_intro, duties, requirements, preferred, employment_type, location,
                  headcount, open_date, close_date, status) VALUES (%s,'ai_ax',%s,%s,%s,%s,%s,'정규직','서울 강남구',2,%s,%s,'published') RETURNING id""",
               c["id"], "AI/AX 엔지니어 (LLM 서비스)",
               "사내 업무를 AI로 자동화하는 AX 솔루션을 만듭니다. 온프레미스 LLM 기반 문서 검색·요약 에이전트를 운영합니다.",
               "- 사내 LLM(RAG) 서비스 설계·개발\n- 업무 자동화 에이전트 개발\n- 모델 서빙·모니터링 파이프라인 구축",
               "- Python 개발 경험\n- 머신러닝·딥러닝 기본 이해\n- LLM 또는 RAG 프로젝트 경험",
               "- vLLM·Ollama 등 모델 서빙 경험\n- PostgreSQL·벡터 DB 경험", today - timedelta(days=3), today + timedelta(days=14))
    db.run("INSERT INTO eval_criteria (job_id) VALUES (%s)", j["id"])
    j2 = db.run("""INSERT INTO jobs (company_id, role_code, title, company_intro, duties, requirements, preferred, location, open_date, close_date, status)
                   VALUES (%s,'ai_ax',%s,%s,%s,%s,'','서울 강남구',%s,%s,'published') RETURNING id""",
                c["id"], "AI 데이터 분석 인턴 (코딩테스트 없음)", "사내 데이터로 AI 활용 과제를 찾는 인턴 포지션입니다.",
                "- 업무 데이터 분석과 AI 적용 과제 발굴", "- Python 기초", today - timedelta(days=1), today + timedelta(days=7))
    db.run("INSERT INTO eval_criteria (job_id, coding_enabled) VALUES (%s,false)", j2["id"])
    for cat, rows in (("personality", PERSONALITY), ("company", COMPANY), ("job", JOB)):
        for sub, text, grp in rows:
            db.run("INSERT INTO question_bank (category, subcategory, text, dup_group) VALUES (%s,%s,%s,%s)", cat, sub, text, grp)
    for level, title, stmt, starter, fn, tests in PROBLEMS:
        db.run("INSERT INTO coding_problems (level, title, statement, starter, func_name, tests) VALUES (%s,%s,%s,%s,%s,%s)",
               level, title, stmt, starter, fn, db.J([{"args": a, "expected": e, "public": p} for a, e, p in tests]))
