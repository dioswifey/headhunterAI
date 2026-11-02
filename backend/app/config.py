"""Runtime settings (TRD §2.3). All values come from environment variables set in docker-compose.yml."""
import os


def _int(name, default):
    return int(os.getenv(name, default))


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://headhunter:headhunter@localhost:5432/headhunter")
DEV_MODE = os.getenv("DEV_MODE", "1") == "1"
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
