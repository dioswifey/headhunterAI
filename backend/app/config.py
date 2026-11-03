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
CONSENT_VERSION = "2026-10-28.v1"

VERSIONS = {"prompt": "p-2026.10.28", "rubric": "r-1.0", "ocr": "tesseract-5 " + OCR_LANG}
