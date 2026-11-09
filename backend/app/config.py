"""Runtime settings (TRD §2.3). All values come from environment variables set in docker-compose.yml."""
import os


def _int(name, default):
    return int(os.getenv(name, default))


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

# CrewAI (v0.6, 리포트 메일의 코칭 문단 작성 전용). 모델 호출은 llm.chat_json을 거치고, CrewAI 자체 원격 전송(텔레메트리·추적)은 끈다
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_DISABLE_TRACKING", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
REPORT_WRITER = os.getenv("REPORT_WRITER", "crewai")  # crewai | template

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
MAX_FOLLOWUPS = 4
CONSENT_VERSION = "2026-11-06.v2"   # v2: 제3자 제공 4요소·자동화된 결정 거부권·음성 삭제·국외 보관 문구 (legal/consent_forms.md)

VERSIONS = {"prompt": "p-2026.11.05b", "rubric": "r-1.0", "ocr": "tesseract-5 " + OCR_LANG}
