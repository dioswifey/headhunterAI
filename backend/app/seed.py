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


# 2026-11-19 사용자 지시 — 직무 7개 전부 공고가 보여야 한다(화면에서 '준비 중'만 뜨면 안 됨).
# 프론트엔드·모바일·DevOps·데이터 엔지니어 공고를 **없으면** 넣는다(제목 기준, 이미 있으면 그대로).
# 공고는 둘러볼 수 있고, 질문 은행이 없는 직무의 지원·면접은 서버가 막는다(routes_applicant.apply).
EXTRA_JOBS = [
    {
     'role_code': 'frontend',
     'title': '프론트엔드 개발자 (사내 AI 콘솔)',
     'company_intro': 'AI 기능과 복잡한 데이터를 구성원이 직관적으로 활용할 수 있는 웹 경험으로 구현하는 역할입니다.',
     'duties': '- 사내 AI 콘솔의 주요 화면과 사용자 흐름을 설계하고 개발합니다.\n- AI 응답과 작업 진행 상태를 실시간으로 보여주는 인터페이스를 구현합니다.\n- 공통 컴포넌트와 상태 관리 구조를 정비해 제품의 일관성과 확장성을 높입니다.\n- 렌더링 성능과 오류를 분석하고 사용성과 운영 안정성을 개선합니다.\n- 기획 및 백엔드 담당자와 협업해 요구사항을 기능으로 구체화합니다.\n- 테스트와 코드 리뷰를 통해 프론트엔드 개발 품질을 지속적으로 높입니다.',
     'requirements': '- React와 TypeScript를 활용해 웹 서비스를 개발한 경험이 필요합니다.\n- 비동기 API 연동과 복잡한 상태를 안정적으로 관리할 수 있어야 합니다.\n- 재사용성과 유지보수성을 고려해 컴포넌트 구조를 설계할 수 있어야 합니다.\n- 웹 표준과 접근성 및 반응형 UI에 대한 기본 이해가 필요합니다.\n- 문제를 주도적으로 정의하고 협업 과정에서 기술적 판단을 명확히 설명할 수 있어야 합니다.',
     'preferred': '- LLM 또는 AI 에이전트 기반 제품의 사용자 화면을 개발한 경험을 우대합니다.\n- WebSocket이나 SSE를 활용해 실시간 데이터를 처리한 경험을 우대합니다.\n- 재사용 가능한 UI 자산을 표준화하고 이를 조직 내에서 지속적으로 관리·확장해 본 분을 우대합니다.\n- 프론트엔드 성능 측정과 오류 모니터링을 바탕으로 개선한 경험을 우대합니다.\n- AI 코딩 도구를 개발과 테스트 및 문서화 과정에 적용한 경험을 우대합니다.',
     'employment_type': '정규직',
     'location': '서울 강남구',
     'headcount': 2,
     'status': 'published',
    },
    {
     'role_code': 'mobile',
     'title': '모바일 앱 개발자 (지원자용 앱)',
     'company_intro': '헤드헌터랩의 지원자용 앱에서 구직자가 채용 정보를 탐색하고 지원 과정을 원활하게 이어갈 수 있도록 모바일 경험을 개발합니다. 기획·디자인·백엔드 담당자와 협업해 기능 정의부터 출시 후 개선까지 제품 전반에 참여합니다.',
     'duties': '- 지원자의 채용 정보 탐색과 지원 흐름을 이해하고 모바일 기능으로 구현합니다.\n- 기획·디자인·백엔드 담당자와 요구사항과 기술적 범위를 조율합니다.\n- 서버 데이터와 앱 상태를 안정적으로 연동하고 다양한 예외 상황을 처리합니다.\n- Android와 iOS 앱의 빌드·검수·스토어 배포 및 버전 관리를 수행합니다.\n- 로그와 사용자 데이터를 바탕으로 오류와 성능 문제를 분석해 개선합니다.',
     'requirements': '- 안드로이드 또는 iOS 환경에서 지원자용 모바일 앱을 구현하고 실제 출시까지 이끌어 본 분을 모십니다.\n- 모바일 UI와 비동기 데이터 흐름 및 상태 관리에 대한 이해가 필요합니다.\n- REST API 연동과 오류 처리 및 디버깅 역량을 갖춘 분이어야 합니다.\n- 사용자 관점에서 요구사항을 해석하고 적절한 구현 방안을 제안할 수 있어야 합니다.\n- 직군 간 협업 과정에서 진행 상황과 기술적 쟁점을 명확하게 공유할 수 있어야 합니다.',
     'preferred': '- 모바일 앱의 설계부터 출시와 운영 개선까지 전 과정을 경험한 분을 우대합니다.\n- 채용·구직 또는 사용자 계정과 지원 절차를 다루는 서비스 개발 경험이 있으면 좋습니다.\n- 앱 성능과 안정성 및 아키텍처를 개선한 경험이 있는 분을 우대합니다.\n- Android·iOS 환경에서 네이티브 기능을 직접 구현했거나 앱에 통합해 본 분을 우대합니다.\n- 분석 이벤트를 설계하고 데이터를 근거로 제품을 개선한 경험을 우대합니다.',
     'employment_type': '정규직',
     'location': '서울 강남구',
     'headcount': 1,
     'status': 'published',
    },
    {
     'role_code': 'devops',
     'title': 'DevOps·클라우드 엔지니어',
     'company_intro': '헤드헌터랩의 서비스를 안정적으로 운영할 수 있도록 클라우드 인프라와 배포 환경을 설계하고 개선합니다. 자동화와 관측 가능성을 바탕으로 개발 생산성, 시스템 신뢰성, 비용 효율을 함께 높이는 역할입니다.',
     'duties': '- 클라우드 인프라를 설계하고 안정적으로 운영합니다.\n- 컨테이너 기반 실행 환경과 오케스트레이션 체계를 구축하고 관리합니다.\n- CI/CD 파이프라인과 배포 프로세스를 자동화합니다.\n- 모니터링·로깅·알림 체계를 고도화하고 장애에 대응합니다.\n- IaC를 활용해 인프라 변경 사항을 일관되게 관리합니다.\n- 클라우드 보안과 비용 현황을 점검하고 개선 과제를 실행합니다.',
     'requirements': '- DevOps·SRE·클라우드 인프라 분야의 실무 경험이 있으신 분을 찾습니다.\n- 퍼블릭 클라우드 환경을 직접 구축하거나 운영해 본 경험이 필요합니다.\n- 배포·통합 파이프라인과 인프라 구성을 코드 기반으로 설계하고 운영 효율을 높여 본 역량을 갖춘 분을 찾습니다.\n- 시스템 지표와 로그를 바탕으로 장애 원인을 분석할 수 있어야 합니다.\n- 개발 조직과 기술적 의사결정을 명확하게 공유할 수 있어야 합니다.',
     'preferred': '- 서비스 안정성과 확장성을 고려해 인프라 구조를 개선한 경험이 있으신 분을 우대합니다.\n- 관측 가능성 체계를 구축하거나 운영 지표를 개선에 활용한 경험이 있으신 분을 우대합니다.\n- 클라우드 보안 강화 또는 취약점 대응 경험이 있으신 분을 우대합니다.\n- 인프라 비용을 분석하고 최적화한 경험이 있으신 분을 우대합니다.\n- 반복되는 운영 업무를 자동화하거나 내부 도구로 개선한 경험이 있으신 분을 우대합니다.',
     'employment_type': '정규직',
     'location': '서울 강남구',
     'headcount': 1,
     'status': 'published',
    },
    {
     'role_code': 'data_eng',
     'title': '데이터 엔지니어 (적재 파이프라인)',
     'company_intro': '헤드헌터랩의 다양한 데이터 소스를 안정적으로 수집하고 적재하는 파이프라인을 설계·운영합니다. 데이터의 신뢰성과 활용성을 높일 수 있는 저장 구조와 운영 체계를 함께 구축합니다.',
     'duties': '- 배치 및 스트리밍 기반의 데이터 수집·적재 파이프라인을 설계하고 운영합니다.\n- 원천 데이터의 특성에 맞는 스키마와 저장 구조를 설계합니다.\n- 워크플로우 자동화와 모니터링을 통해 파이프라인의 안정성을 개선합니다.\n- 데이터 누락·중복·지연 등 품질 문제를 탐지하고 원인을 해결합니다.\n- 내부 데이터 요구사항을 정리해 활용 가능한 테이블과 데이터 마트로 구현합니다.',
     'requirements': '- 3년 이상 데이터 엔지니어로서 적재 흐름을 설계·구현했거나 백엔드 기반의 데이터 처리 체계를 개발한 경험, 또는 그에 상응하는 실무 역량을 갖춘 분을 찾습니다.\n- Python과 SQL을 활용해 데이터 수집·변환 로직을 구현할 수 있어야 합니다.\n- RDB 환경에 맞춰 데이터 구조를 설계하고 실제 서비스에서 안정적으로 관리해 본 경험을 갖춘 분을 찾습니다.\n- 대규모 데이터를 정기 수집 방식이나 실시간 처리 방식으로 안정적으로 적재하는 흐름을 설계하고 실제 서비스 환경에서 관리해 본 분을 찾습니다.\n- 파이프라인 장애와 데이터 이상을 분석해 해결할 수 있어야 합니다.',
     'preferred': '- Airflow를 비롯한 오케스트레이션 플랫폼으로 데이터 적재 흐름을 설계·운영해 본 분을 우선적으로 고려합니다.\n- Kafka 또는 CDC 기반의 실시간 데이터 처리 경험을 우대합니다.\n- 데이터 웨어하우스나 데이터 레이크를 설계하고 운영한 경험을 우대합니다.\n- 데이터 품질 검증과 모니터링 체계를 구축한 경험을 우대합니다.',
     'employment_type': '정규직',
     'location': '서울 강남구',
     'headcount': 1,
     'status': 'published',
    },
    # 2026-11-23 사용자 지시 — ai_ax 기준 도메인을 'Python 기반 사내 AI 제품 · AI Agent 개발'로 세운다.
    # 제조 비전 공고(기존)는 그대로 두고 이 공고를 더한다(사용자 결정: "공고 6은 두고 새 공고 추가").
    {
     'role_code': 'ai_ax',
     'title': 'AI Agent 엔지니어 (사내 AI 제품·업무 자동화)',
     'company_intro': '사내 업무와 자사 제품에 AI Agent를 연결해 사람이 하던 판단과 실행을 자동화하는 역할입니다. Python 기반으로 LLM API와 사내 시스템을 잇고, 에이전트가 실제 업무에서 믿고 쓸 만한 수준으로 동작하도록 평가와 운영 체계를 함께 만듭니다.',
     'duties': '- AI Agent 서비스를 설계하고 구현하며 운영합니다.\n- 백엔드 API를 설계하고 사내·외부 시스템과 연동합니다.\n- 프롬프트를 다듬고 에이전트 평가와 관측 체계를 세웁니다.\n- 비용과 응답 속도를 최적화하고 배포와 모니터링을 이어갑니다.\n- 자사 제품의 기존 인지 기능 위에 판단·추천·실행 기능을 더합니다.\n- 제품 운영·인사·회계·영업·마케팅 등 사내 업무에서 자동화할 일을 찾아 에이전트로 만듭니다.',
     'requirements': '- AI·ML·백엔드·데이터·플랫폼 엔지니어링 실무 경력이 3년 이상이거나 그에 준하는 역량을 갖춘 분을 모십니다.\n- Python으로 AI·LLM 서비스나 백엔드 서비스를 개발한 경험이 필요합니다.\n- LLM API로 실제 서비스 기능을 만들어 본 경험이 있어야 합니다.\n- 외부 시스템 연동과 예외 처리를 안정적으로 설계할 수 있어야 합니다.\n- 문제를 직접 정의하고 판단 근거를 명확하게 설명할 수 있어야 합니다.',
     'preferred': '- LangGraph·LangChain·LlamaIndex 등 에이전트 프레임워크를 써 본 분을 우대합니다.\n- 여러 LLM 모델을 비교해 고르거나 호출을 나누는 구조를 만들어 본 경험을 우대합니다.\n- 복잡한 업무 규칙을 자동화된 흐름으로 설계하고 운영해 본 분을 우대합니다.\n- 에이전트의 실패를 유형으로 나누고 평가셋으로 되풀이를 막아 본 경험을 우대합니다.\n- 새 기술을 빠르게 익혀 실무 제품에 적용하는 데 즐거움을 느끼는 분을 우대합니다.',
     'employment_type': '정규직',
     'location': '서울 강남구',
     'headcount': 2,
     'status': 'published',
    },
]


def ensure_extra_jobs():
    """직무별 공고가 비어 있지 않게 채운다. 여러 번 돌려도 중복되지 않는다."""
    from datetime import datetime
    today = datetime.now(KST).date()
    c = db.one("SELECT id, intro FROM companies WHERE name=%s", "헤드헌터랩")
    if not c:
        return
    for jb in EXTRA_JOBS:
        if db.one("SELECT 1 FROM jobs WHERE title=%s", jb["title"]):
            continue
        if not db.one("SELECT 1 FROM job_roles WHERE code=%s", jb["role_code"]):
            continue
        j = db.run("""INSERT INTO jobs (company_id, role_code, title, company_intro, duties, requirements, preferred,
                      employment_type, location, headcount, open_date, close_date, status)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                   c["id"], jb["role_code"], jb["title"], jb.get("company_intro") or c["intro"],
                   jb.get("duties"), jb.get("requirements"), jb.get("preferred"),
                   jb.get("employment_type") or "정규직", jb.get("location") or "서울 강남구",
                   jb.get("headcount") or 1, today - timedelta(days=3), today + timedelta(days=30),
                   jb.get("status") or "published")
        db.run("INSERT INTO eval_criteria (job_id, coding_enabled) VALUES (%s,%s)", j["id"], False)


# ---------- 공고 문구 맞추기: RAG 재생성 한국어 + 영어판 (2026-11-20 사용자 지시) ----------
# 원본 backend/db/seed/job_texts.json(제목 기준). 로컬 DB에 적용한 재생성 한국어 문구가 배포 DB에는 없어서,
# 기동 때 제목이 같은 공고에 한국어(재생성본)와 *_en 열을 채운다. 여러 번 돌려도 결과가 같다.
# 담당자가 내용을 고친 공고는 건드리지 않는다: 지금 한국어가 json 한국어와 같거나(영어만 채움)
# 시드가 처음 넣은 문구 그대로일 때만(한국어도 바꿈) 적용한다.
_KO_FIELDS = ("company_intro", "duties", "requirements", "preferred")
_EN_FIELDS = ("title", "company_intro", "duties", "requirements", "preferred", "location")
# seed()가 넣는 본사 공고 2개의 처음 문구(seed() 본문과 같아야 한다)
_SEED_HQ = {
    "AI/AX 엔지니어 (LLM 서비스)": (
        "사내 업무를 AI로 자동화하는 AX 솔루션을 만듭니다. 온프레미스 LLM 기반 문서 검색·요약 에이전트를 운영합니다.",
        "- 사내 LLM(RAG) 서비스 설계·개발\n- 업무 자동화 에이전트 개발\n- 모델 서빙·모니터링 파이프라인 구축",
        "- Python 개발 경험\n- 머신러닝·딥러닝 기본 이해\n- LLM 또는 RAG 프로젝트 경험",
        "- vLLM·Ollama 등 모델 서빙 경험\n- PostgreSQL·벡터 DB 경험"),
    "AI 데이터 분석 인턴 (코딩테스트 없음)": (
        "사내 데이터로 AI 활용 과제를 찾는 인턴 포지션입니다.", "- 업무 데이터 분석과 AI 적용 과제 발굴", "- Python 기초", ""),
}


def _seed_originals():
    out = dict(_SEED_HQ)
    for _name, _email, intro, _loc, posts in DEMO_COMPANIES:
        for _role, title, duties, req, pref, *_ in posts:
            out[title] = (intro, duties, req, pref)
    for jb in EXTRA_JOBS:
        out[jb["title"]] = (jb.get("company_intro") or "", jb.get("duties") or "", jb.get("requirements") or "", jb.get("preferred") or "")
    return out


def apply_job_texts(path=None):
    import json
    import logging
    from pathlib import Path
    log = logging.getLogger("seed")
    p = Path(path or Path(config.SCHEMA_PATH).parent / "seed" / "job_texts.json")
    if not p.exists():
        return {"skipped": "no file"}
    texts = json.loads(p.read_text(encoding="utf-8"))["jobs"]
    norm = lambda s: "\n".join(l.strip() for l in (s or "").replace("\r", "").strip().split("\n"))  # noqa: E731
    originals = _seed_originals()
    done = {"ko+en": 0, "en": 0, "same": 0, "edited": 0}
    for t in texts:
        for row in db.all_("SELECT * FROM jobs WHERE title=%s", t["title"]):
            cur = tuple(norm(row[k]) for k in _KO_FIELDS)
            want_ko = tuple(norm(t["ko"][k]) for k in _KO_FIELDS)
            seeded = originals.get(t["title"])
            if cur == want_ko:
                set_ko = False
            elif seeded and cur == tuple(norm(v) for v in seeded):
                set_ko = True
            else:
                done["edited"] += 1      # 담당자가 고친 공고 — 영어도 맞지 않으므로 둘 다 그대로
                log.warning("job_texts: 공고 %s(%s) 내용이 바뀌어 있어 건너뜀", row["id"], t["title"])
                continue
            en_same = all((row.get(k + "_en") or "") == (t["en"].get(k) or "") for k in _EN_FIELDS)
            if not set_ko and en_same:
                done["same"] += 1
                continue
            cols = [f"{k}_en=%s" for k in _EN_FIELDS] + ([f"{k}=%s" for k in _KO_FIELDS] if set_ko else [])
            vals = [t["en"].get(k) for k in _EN_FIELDS] + ([t["ko"][k] for k in _KO_FIELDS] if set_ko else [])
            db.run(f"UPDATE jobs SET {', '.join(cols)}, updated_at=now() WHERE id=%s", *vals, row["id"])
            done["ko+en" if set_ko else "en"] += 1
    return done


def activate_all_roles():
    """직무 상태를 모두 active로 — 공고 둘러보기가 '준비 중'으로 막히지 않게(2026-11-19 사용자 지시).
    AI 면접 자체의 가능 여부는 질문 은행 유무로 결정되며, 지원 차단은 서버가 따로 한다."""
    db.run("UPDATE job_roles SET status='active' WHERE status<>'active'")
