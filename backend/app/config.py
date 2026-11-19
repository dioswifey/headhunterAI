"""Runtime settings (TRD §2.3). All values come from environment variables set in docker-compose.yml."""
import os


def _int(name, default):
    return int(os.getenv(name, default))


def _float(name, default):
    return float(os.getenv(name, default))


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://headhunter:headhunter@localhost:5432/headhunter")
DEV_MODE = os.getenv("DEV_MODE", "1") == "1"
# 이메일 인증 메일이 아직 없어서 가입 시 인증을 자동 완료할지. 기본은 DEV_MODE와 같고, 배포에서 따로 정한다
# 상단 DEV 바: 로컬에서만 보인다(2026-11-03 사용자 지시). Railway(RAILWAY_ENVIRONMENT가 있음)에서는 숨김. SHOW_DEVBAR로 직접 지정 가능
SHOW_DEVBAR = os.getenv("SHOW_DEVBAR", "1" if (DEV_MODE and not os.getenv("RAILWAY_ENVIRONMENT")) else "0") == "1"
AUTO_VERIFY_EMAIL = os.getenv("AUTO_VERIFY_EMAIL", "1" if DEV_MODE else "0") == "1"
# 시드 계정 비밀번호: 개발은 CLAUDE.md의 기본값, 배포는 환경 변수로만(없으면 무작위 → 로그인 불가)
SEED_RECRUITER_PASSWORD = os.getenv("SEED_RECRUITER_PASSWORD", "changeme123!" if DEV_MODE else "")
SEED_OPERATOR_PASSWORD = os.getenv("SEED_OPERATOR_PASSWORD", "operator123!" if DEV_MODE else "")
SEED_APPLICANT_PASSWORD = os.getenv("SEED_APPLICANT_PASSWORD", "test1234!" if DEV_MODE else "")
# 코드 실행기: 비어 있으면 공유 폴더 방식(로컬 Docker, 네트워크 차단 컨테이너), 있으면 HTTP 방식(Railway)
RUNNER_URL = os.getenv("RUNNER_URL", "").rstrip("/")
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "")
SCHEMA_PATH = os.getenv("SCHEMA_PATH", "/srv/db/schema.sql")
# 개발 전용: 접수 취소(지원 기록 초기화) 버튼을 쓸 수 있는 지원자 테스트 계정 = 시드 이지원
TEST_APPLICANT_EMAIL = os.getenv("TEST_APPLICANT_EMAIL", "applicant@headhunter.ai")

# 코딩테스트 게이트 임계값(DEC-07·08, v0.10 사용자 지시 — 공고마다 다르게 입력받지 않는 고정값)
CODING_GATE_SCORE = 60          # 하드·소프트 게이트 기준: 코딩테스트 점수가 이 미만이면 게이트 발동
CODING_GATE_SOFT_OVERRIDE = 85  # 소프트 게이트 보완 기준: AI 면접 점수(단독)가 이 이상이면 코딩 게이트 무시

# LLM provider (TRD §5.1) — dev: Ollama qwen3:4b, prod: Qwen3-8B (same OpenAI-less Ollama/vLLM endpoint)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://host.docker.internal:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:4b")
LLM_TIMEOUT = _int("LLM_TIMEOUT", 120)
FOLLOWUP_TIMEOUT = float(os.getenv("FOLLOWUP_TIMEOUT", "5"))  # INT-06: 5초 안에 판단 못 하면 생략

# 질문 은행 벡터 저장소 (Redis 8 Query Engine). 임베딩은 로컬 Ollama 모델만 사용
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", LLM_BASE_URL)
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:0.6b")
EMBED_DIM = _int("EMBED_DIM", 1024)
QUESTIONBANK_DIR = os.getenv("QUESTIONBANK_DIR", "/questionbank")
# 질문 은행 번역 에이전트 모델(영↔한). 비우면 LLM_MODEL과 같다
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL", "") or LLM_MODEL
CODING_GEN_MODEL = os.getenv("CODING_GEN_MODEL", "qwen3:8b")   # 코딩 문제 생성·검토(오프라인, 사용자 지시 2026-11-05)
# 코딩 문제은행 **생성만** 외부 API(사용자 결정 2026-11-05). openai | ollama. 검토·조회·채점·다른 기능은 로컬 Qwen
CODING_GEN_PROVIDER = os.getenv("CODING_GEN_PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = (os.getenv("OPENAI_MODEL", "") or "gpt-4.1-mini").strip()   # .env의 앞뒤 공백 제거
OPENAI_SEND_TEMPERATURE = os.getenv("OPENAI_SEND_TEMPERATURE", "1") == "1"   # 추론 모델은 temperature를 받지 않으면 0으로
OPENAI_PRICE_IN_PER_M = float(os.getenv("OPENAI_PRICE_IN_PER_M", "0") or 0)    # 비용 계산용(USD/100만 토큰), 사용자가 요금표로 넣음
OPENAI_PRICE_OUT_PER_M = float(os.getenv("OPENAI_PRICE_OUT_PER_M", "0") or 0)
# SQL 문제은행(2026-11-06): 실행 검증용 격리 DB(backend/db/sqlbank_init.sql) · 생성 모델(비우면 OPENAI_MODEL)
SQLBANK_URL = os.getenv("SQLBANK_URL", "postgresql://sqlbank_exec:sqlbank_exec@localhost:5432/sqlbank")
SQL_GEN_MODEL = os.getenv("SQL_GEN_MODEL", "").strip()
# 네이버 검색 API(공식, developers.naver.com) — SQLD 유형 통계용. 제목·요약만 읽고 원문은 저장하지 않는다
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()

# LangSmith 추적 (TRD v0.2 §13.4) — 개발 환경 + 시드·가상 데이터 전용. 운영(DEV_MODE=0)이나 키가 없으면 강제로 끈다.
LANGSMITH_TRACING = DEV_MODE and bool(os.getenv("LANGSMITH_API_KEY")) and os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
os.environ["LANGSMITH_TRACING"] = "true" if LANGSMITH_TRACING else "false"
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "headhunter-dev")

# 이메일 (v0.6) — Gmail SMTP만 사용(smtp.gmail.com:587, STARTTLS, 앱 비밀번호). 계정·비밀번호가 비면 발송하지 않는다
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "1") == "1"
MAIL_FROM = os.getenv("MAIL_FROM") or (f"headhunterAI <{SMTP_USER}>" if SMTP_USER else "")  # Gmail은 로그인 계정 주소로만 발신
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# 메일 문구 작성 (v0.11, 2026-11-17 CrewAI 제거 — 멀티에이전트·위임·도구를 쓰지 않는 1-Agent/1-Task 래퍼였음이 드러나
# llm.chat_json 직접 호출로 통합. agents/report_coach.py · agents/decision_notice.py)
REPORT_WRITER = os.getenv("REPORT_WRITER", "local")  # local | template

# 음성 서비스 (v0.9, TRD v0.6 14.6) — Qwen3-ASR(STT)·Qwen3-TTS(TTS) 로컬 모델 컨테이너(backend/speech).
# 비어 있거나 응답이 없으면 STT는 비상 입력란, TTS는 브라우저 내장 음성으로 대체(INT-01)
SPEECH_URL = os.getenv("SPEECH_URL", "").rstrip("/")
SPEECH_TOKEN = os.getenv("SPEECH_TOKEN", "")
STT_TIMEOUT = float(os.getenv("STT_TIMEOUT", "60"))
TTS_TIMEOUT = float(os.getenv("TTS_TIMEOUT", "90"))
TTS_DIR = os.getenv("TTS_DIR", "/data/tts")      # 질문 음성 캐시(질문 문장 해시 → wav). 개인정보 아님(질문 문장만)

# 결제 (v0.9, PRD PAY-07~12, TRD v0.6 14.5) — 네이버페이 자동결제. 금액은 이 요금표에서만 정한다
NAVERPAY_CLIENT_ID = os.getenv("NAVERPAY_CLIENT_ID", "").strip()
NAVERPAY_CLIENT_SECRET = os.getenv("NAVERPAY_CLIENT_SECRET", "").strip()
NAVERPAY_CHAIN_ID = os.getenv("NAVERPAY_CHAIN_ID", "").strip()
# off | test(네이버페이 개발 서버) | live(운영 서버) | mock(가짜 결제, 로컬 개발 전용). 비우면 키가 있을 때 test, 없으면 off
_pm = os.getenv("PAYMENTS_MODE", "").strip().lower()
if not _pm:
    _pm = "test" if (NAVERPAY_CLIENT_ID and NAVERPAY_CLIENT_SECRET and NAVERPAY_CHAIN_ID) else "off"
if _pm == "mock" and (os.getenv("RAILWAY_ENVIRONMENT") or not DEV_MODE):
    _pm = "off"   # 가짜 결제는 배포본에서 절대 켜지지 않는다(무료로 Advanced가 되는 것을 막음)
PAYMENTS_MODE = _pm
PLANS = {   # PRD PAY-01: 표시 가격 = 결제 금액(부가세 포함 여부 미결 → 표시 가격 그대로 과세 금액으로 보냄)
    "applicant_advanced": {"owner_type": "user", "amount": 9900, "name": "headhunterAI Advanced (지원자, 월간)"},
    "company_advanced": {"owner_type": "company", "amount": 89900, "name": "headhunterAI Advanced (기업, 월간)"},
}
RENEW_RETRY_DAYS = 3

OCR_LANG = os.getenv("OCR_LANG", "kor+eng")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
SANDBOX_DIR = os.getenv("SANDBOX_DIR", "/sandbox")

MAX_CONCURRENT_INTERVIEWS = _int("MAX_CONCURRENT_INTERVIEWS", 5)  # INT-08: GPU 1대당 5건
SESSION_HOURS = 2
HEARTBEAT_LOST_SEC = 15          # 5초 주기 × 3회 누락
CODING_MINUTES = 60
QUESTION_SECONDS = 90
FOLLOWUP_SECONDS = 60
# A17 사내문서 질문 **생성** 제공자 (2026-11-19 사용자 지시, 외부 AI 예외 ⑥).
# openai = 오프라인 사전 생성만 OpenAI로. 지원자 데이터는 보내지 않는다. 임베딩·면접 중 호출은 그대로 로컬.
IQ_GEN_PROVIDER = os.getenv("IQ_GEN_PROVIDER", "openai")

MAX_FOLLOWUPS = 2   # 2026-11-19 사용자 지시: 본질문 8 + 꼬리질문 최대 2 = 전체 10개를 넘지 않는다(안 나와도 됨)
QGRAPH_REVIEW = os.getenv("QGRAPH_REVIEW", "1") == "1"   # 그래프 1 A5·A6 모델 검수(0이면 규칙만)
# 사내문서 RAG를 그래프 1-A 기업 문항에 붙이는 스위치(ADLC v0.7 6.13.7 · P-4, 2026-11-13 구현).
# 0이면 retrieve_internal이 빈 컨텍스트를 돌려주고 guard_disclosure는 할 일이 없다(v0.6과 같은 질문 구성).
# 1이어도 금지 용어 사전 Redis idoc:forbid가 없으면 사내문서 경로를 쓰지 않는다(`internal_docs_agent forbid`로 만든다).
INTERNAL_DOCS_RAG = os.getenv("INTERNAL_DOCS_RAG", "1") == "1"
# Multi-Query + RRF (ADLC v0.8 6.18, 2026-11-16). 그래프 1-A retrieve·retrieve_internal의 검색을
# 관점이 다른 쿼리 RAG_MQ_N개(기존 단일 쿼리 포함)로 각각 검색해 RRF로 합친다. 0 = 기존 단일 쿼리(기준선).
# 실패하면 언제나 기존 단일 쿼리로 내려간다. 기본값은 2026-11-16 전후 비교 실측으로 정했다(ADLC 6.18.5).
RAG_MULTI_QUERY = os.getenv("RAG_MULTI_QUERY", "0") == "1"
RAG_MQ_N = _int("RAG_MQ_N", 3)            # 쿼리 수(기존 쿼리 포함)
RAG_MQ_K = _int("RAG_MQ_K", 16)           # 질문 은행 쿼리당 검색 깊이(기존 _pick_redis의 k와 같은 값에서 시작)
RAG_RRF_K = _int("RAG_RRF_K", 60)         # RRF 상수 k
# Dual RAG (2026-11-16, ADLC 6.18.3): 사내문서(Chroma) 근거를 기업 문항(A15)뿐 아니라 직무 이해도 문항 2개에도 넣는다
# (질문 은행 Redis 문항 + 사내문서 맥락 + 공고 + 이력서 → generate/job_question_rag, 7-B 관문 대상). 0 = 기업 문항에만(기존).
RAG_DUAL_JOB = os.getenv("RAG_DUAL_JOB", "0") == "1"
# ── 사내문서를 기본 질문 8개의 **주 출처**로 (2026-11-17 사용자 지시, ADLC v0.8 6.19)
# 인성·직무·기업 질문을 사내문서(Chroma)에서 미리 만들어 둔 질문 컬렉션(A17)에서 먼저 꺼내고,
# 모자랄 때만 Redis 질문 은행 → DB 은행으로 내려간다(Redis = 백업, 목표 20% 미만).
#   chroma = 사내문서 질문 우선(기본) · redis = 2026-11-16까지의 동작(Redis 우선)
RAG_QUESTION_SOURCE = os.getenv("RAG_QUESTION_SOURCE", "chroma")
RAG_IQ_MIN_SIM = _float("RAG_IQ_MIN_SIM", 0.30)   # 사내문서 질문 검색 유사도 하한. 질문↔공고는 조각↔공고보다 값이 낮다
                                                  # (실측: Redis 인성·기업 문항도 0.27~0.50 구간, ADLC 6.19.1)
RAG_IQ_REWRITE = os.getenv("RAG_IQ_REWRITE", "0") == "1"
FOLLOWUP_INTERNAL_REF = os.getenv("FOLLOWUP_INTERNAL_REF", "0") == "1"   # 2026-11-18: 사내문서 질문의 기대 키워드·모범답안을 꼬리질문 판단에 쓸지.
# 기본 0(끔) — 켜면 판단 ②③은 살아나지만 같은 변경이 B-07 힌트 금지 필터도 함께 깨워
# 160문항 중 99문항(62%)에서 꼬리질문이 조용히 생략될 수 있다(질문 밖 기대 키워드가 문항당
# 평균 3.04개 — 구조적 상한, eval_runs/followup_phase1_2026-11-18). A/B 실측 전까지 끈다.   # 1이면 사내문서 질문도 면접마다 다시 쓴다(A15 경로).
                                                  # 기본 0 = 그대로 쓴다 — 다시 쓰기가 8/20만 성공했기 때문(QO-13)
CONSENT_VERSION = "2026-11-09.v3"   # v2: 제3자 제공 4요소·자동화된 결정 거부권·음성 삭제·국외 보관 문구 · v3: LangSmith 국외 이전(PRD V9-14)

VERSIONS = {"prompt": "p-2026.11.16", "rubric": "r-1.0", "ocr": "tesseract-5 " + OCR_LANG}
