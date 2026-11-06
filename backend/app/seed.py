"""Initial data (runs once when the companies table is empty).

Question bank: the full DB (IT직무·인성 면접질문 xlsx) is not in this repo. This is a small sample in the same
category structure; replace via the import format in TRD §6.1.
"""
from datetime import timedelta

from . import db, security, config
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
    import secrets
    pw = lambda v: security.hash_pw(v or secrets.token_urlsafe(24))   # 배포에서 값이 없으면 아무도 모르는 비밀번호
    for i, (code, name, status) in enumerate(ROLES):
        db.run("INSERT INTO job_roles (code, name, status, sort) VALUES (%s,%s,%s,%s)", code, name, status, i)
    c = db.run("""INSERT INTO companies (name, intro) VALUES (%s,%s) RETURNING id""", "헤드헌터랩",
               "사내 업무를 AI로 자동화하는 AX 솔루션을 만드는 데모 회사입니다. 문서 검색·요약 에이전트와 사내 LLM 플랫폼을 운영합니다.")
    db.run("""INSERT INTO users (role, company_id, email, password_hash, name, phone, email_verified, must_change_password)
              VALUES ('recruiter',%s,'recruiter@headhunter.ai',%s,%s,'010-1111-2222',true,true)""", c["id"], "헤드헌터랩", pw(config.SEED_RECRUITER_PASSWORD))
    db.run("""INSERT INTO users (role, email, password_hash, name, email_verified)
              VALUES ('operator','ops@headhunter.ai',%s,'운영자',true)""", pw(config.SEED_OPERATOR_PASSWORD))
    db.run("""INSERT INTO users (role, email, password_hash, name, phone, email_verified, age14_confirmed)
              VALUES ('applicant','applicant@headhunter.ai',%s,'이지원','010-3333-4444',true,true)""", pw(config.SEED_APPLICANT_PASSWORD))
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


# ---------- 개발 테스트용 기업 3곳 · 공고 9개 (2026-11-06 사용자 지시, PRD v0.9 ACC-07·APP-10) ----------
# 공고 문장은 원티드 공고 구성(회사 소개·주요 업무·자격 요건·우대 사항)을 따라 새로 쓴 가상 공고다.
# 원티드는 robots.txt부터 403으로 막혀 있어 수집하지 않았고, 실제 회사 문장을 옮기지 않았다.
# (회사명, 담당자 이메일, 회사 소개, 근무지, [(직무, 제목, 주요 업무, 자격 요건, 우대 사항, 고용 형태, 인원, 시작(오늘-일), 마감(오늘+일), 코딩테스트)])
DEMO_COMPANIES = [
    ("헤드헌터랩_서울", "recruiter.seoul@headhunter.ai",
     "금융·커머스 고객사에 AI 상담·검색 서비스를 공급하는 B2B AI 회사입니다. 고객 상담 기록과 상품 문서를 근거로 답하는 상담 에이전트를 직접 만들고 운영합니다.",
     "서울 강남구", [
        ("ai_ax", "LLM 애플리케이션 엔지니어 (AI 상담 에이전트)",
         "- 고객사 상담 문서 기반 RAG 파이프라인 설계·개발\n- 상담 에이전트 도구 호출·대화 흐름 설계\n- 답변 품질 평가셋 구축과 회귀 평가 자동화\n- 고객사 PoC 기술 지원",
         "- Python 실무 경험 2년 이상\n- LLM API 또는 오픈소스 LLM으로 서비스를 만들어 본 경험\n- 검색(BM25·벡터 검색) 기본 원리 이해\n- Git 기반 협업 경험",
         "- LangChain·LangGraph 등 오케스트레이션 도구 사용 경험\n- 금융 도메인 프로젝트 경험\n- 프롬프트·검색 개선을 지표로 검증해 본 경험",
         "정규직", 2, 2, 21, True),
        ("backend", "백엔드 개발자 (상담 플랫폼 API)",
         "- 상담 플랫폼 REST API 설계·개발\n- 고객사 연동(웹훅·SSO) 개발\n- 대화 로그 저장 구조 설계와 성능 개선",
         "- Python(FastAPI·Django) 또는 Java(Spring) 실무 경험 3년 이상\n- RDBMS 설계와 쿼리 튜닝 경험\n- 테스트 코드 작성 습관",
         "- 대용량 트래픽 서비스 운영 경험\n- Docker·Kubernetes 운영 경험\n- 메시지 큐(Kafka·RabbitMQ) 사용 경험",
         "정규직", 1, 5, 25, True),
        ("data_sci", "데이터 분석가 (상담 품질·지표)",
         "- 상담 지표(해결률·재문의율) 정의와 대시보드 운영\n- 상담 로그 분석으로 개선 과제 발굴\n- A/B 테스트 설계와 결과 해석",
         "- SQL로 원하는 데이터를 직접 뽑을 수 있는 분\n- Python(pandas) 분석 경험\n- 통계적 가설 검정 기본 이해",
         "- 텍스트 데이터 분석 경험\n- BI 도구(Superset·Tableau 등) 사용 경험",
         "정규직", 1, 8, 14, False),
     ]),
    ("헤드헌터랩_경기", "recruiter.gyeonggi@headhunter.ai",
     "제조 현장의 AX(AI 전환)를 돕는 스마트팩토리 솔루션 회사입니다. 비전 검사와 설비 센서 데이터 분석으로 불량과 설비 정지를 줄입니다.",
     "경기 성남시 분당구", [
        ("ai_ax", "AI 엔지니어 (제조 비전 검사)",
         "- 제품 외관 불량 탐지 모델 개발·개선\n- 학습 데이터 수집·라벨링 기준 정리\n- 엣지 장비 추론 최적화와 현장 배포\n- 현장 담당자와 불량 기준 협의",
         "- 딥러닝 기반 컴퓨터 비전 프로젝트 경험\n- PyTorch 사용 경험\n- 모델 성능 지표(mAP·재현율)를 근거로 개선해 본 경험",
         "- ONNX·TensorRT 변환 경험\n- 제조·품질 도메인 경험\n- 적은 불량 데이터 문제를 다뤄 본 경험",
         "정규직", 2, 1, 28, True),
        ("backend", "백엔드 개발자 (설비 데이터 수집 플랫폼)",
         "- 설비 센서 데이터 수집 서버 개발\n- 시계열 데이터 저장·조회 API 개발\n- 현장 장비 연동 프로토콜 대응",
         "- 백엔드 실무 경험 2년 이상(언어 무관)\n- 리눅스 서버 운영 기본 지식\n- 비동기 처리 이해",
         "- 시계열 DB(TimescaleDB·InfluxDB) 경험\n- MQTT·OPC UA 경험\n- Go 또는 Rust 경험",
         "정규직", 1, 3, 20, True),
        ("data_sci", "데이터 사이언티스트 (설비 예지 보전)",
         "- 설비 고장 예측 모델 개발\n- 센서 데이터 이상 탐지\n- 분석 결과를 현장 의사결정 보고서로 정리",
         "- 머신러닝 모델링 경험(분류·회귀·이상 탐지)\n- Python(pandas·scikit-learn) 능숙\n- 시계열 데이터 처리 경험",
         "- 제조 데이터 분석 경험\n- 통계·산업공학 전공 또는 그에 준하는 지식",
         "정규직", 1, 10, 10, False),
     ]),
    ("헤드헌터랩_인천", "recruiter.incheon@headhunter.ai",
     "항만·물류 기업의 운영 데이터를 다루는 물류 AI 회사입니다. 물동량 수요 예측과 배차·경로 최적화 서비스를 만듭니다.",
     "인천 연수구", [
        ("ai_ax", "AX 엔지니어 (물류 업무 자동화)",
         "- 물류 운영 문서(선적·통관 서류) 정보 추출 자동화\n- 사내 업무용 LLM 에이전트 개발\n- 자동화 전후 업무 시간 측정과 개선 보고",
         "- Python 개발 경험\n- LLM 또는 OCR을 업무 자동화에 적용해 본 경험\n- 현업 담당자와 요구사항을 정리해 본 경험",
         "- 문서 이해 모델(레이아웃·표 추출) 경험\n- 사내 시스템(ERP·WMS) 연동 경험",
         "정규직", 1, 0, 30, True),
        ("backend", "백엔드 개발자 (배차·경로 서비스)",
         "- 배차·경로 최적화 엔진 API 개발\n- 운송사·화주 연동 API 개발\n- 서비스 장애 대응과 모니터링",
         "- 백엔드 실무 경험 3년 이상\n- PostgreSQL 사용 경험\n- API 설계 원칙 이해",
         "- 지도·위치 데이터(PostGIS) 경험\n- 최적화 알고리즘 구현 경험\n- 클라우드(AWS·GCP) 운영 경험",
         "정규직", 2, 6, 18, True),
        ("data_sci", "데이터 분석가 (물동량 수요 예측)",
         "- 항만 물동량·주문 데이터 분석\n- 수요 예측 모델 개발과 정확도 관리\n- 운영팀 주간 리포트 작성",
         "- SQL·Python 데이터 분석 경험\n- 시계열 예측 모델 경험\n- 분석 결과를 비전공자에게 설명해 본 경험",
         "- 물류·유통 도메인 경험\n- 예측 모델 운영(재학습·모니터링) 경험",
         "계약직", 1, 4, 12, False),
     ]),
]


def ensure_demo_companies():
    """개발 테스트용 기업 3곳을 없으면 넣는다(회사 이름 기준, 이미 있으면 그대로). 개발 모드에서만."""
    if not config.DEV_MODE:
        return
    from datetime import datetime
    import secrets
    today = datetime.now(KST).date()
    for name, email, intro, location, posts in DEMO_COMPANIES:
        if db.one("SELECT 1 FROM companies WHERE name=%s", name):
            continue
        c = db.run("INSERT INTO companies (name, intro) VALUES (%s,%s) RETURNING id", name, intro)
        if not db.one("SELECT 1 FROM users WHERE email=%s", email):
            db.run("""INSERT INTO users (role, company_id, email, password_hash, name, email_verified, must_change_password)
                      VALUES ('recruiter',%s,%s,%s,%s,true,true)""", c["id"], email,
                   security.hash_pw(config.SEED_RECRUITER_PASSWORD or secrets.token_urlsafe(24)), name)
        for role, title, duties, req, pref, etype, head, ago, left, coding in posts:
            j = db.run("""INSERT INTO jobs (company_id, role_code, title, company_intro, duties, requirements, preferred, employment_type,
                          location, headcount, open_date, close_date, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'published') RETURNING id""",
                       c["id"], role, title, intro, duties, req, pref, etype, location, head,
                       today - timedelta(days=ago), today + timedelta(days=left))
            db.run("INSERT INTO eval_criteria (job_id, coding_enabled) VALUES (%s,%s)", j["id"], coding)


def sync_recruiter_names():
    """기업 담당자 계정 이름 = 소속 기업명 (PRD v0.9 ACC-07). 회사 이름이 바뀌어도 맞춰 둔다."""
    db.run("""UPDATE users u SET name=c.name FROM companies c
              WHERE u.company_id=c.id AND u.role='recruiter' AND u.name<>c.name""")
