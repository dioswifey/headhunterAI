"""headhunterAI API + static web (admin/, applicant/, shared/, images/)."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, routes_admin, routes_applicant, routes_auth, routes_payments, scheduler, security, seed

WEB_ROOT = Path("/web")


@asynccontextmanager
async def lifespan(_app):
    db.pool.open(wait=True)
    _ensure_schema()
    _migrate()
    seed.seed()
    seed.ensure_demo_companies()
    seed.sync_recruiter_names()
    scheduler.start()
    yield
    db.pool.close()


def _ensure_schema():
    """빈 DB면 schema.sql을 적용한다(Railway Postgres용. 로컬은 initdb가 이미 적용)."""
    if db.one("SELECT to_regclass('public.companies') AS t")["t"]:
        return
    path = Path(config.SCHEMA_PATH)
    if path.exists():
        with db.tx() as conn:
            conn.execute(path.read_text(encoding="utf-8"))


# 이미 있는 DB(로컬·Railway)에 v0.9 변경을 적용한다. 모두 IF NOT EXISTS라 여러 번 돌아도 같다. 원본은 schema.sql
MIGRATIONS_V09 = [
    "ALTER TABLE attempt_questions ADD COLUMN IF NOT EXISTS answer_source TEXT CHECK (answer_source IN ('voice','typed','voice+typed'))",
]


def _migrate():
    with db.tx() as conn:
        for sql in MIGRATIONS_V09:
            conn.execute(sql)
        if not conn.execute("SELECT to_regclass('public.subscriptions') AS t").fetchone()["t"]:
            text = Path(config.SCHEMA_PATH).read_text(encoding="utf-8") if Path(config.SCHEMA_PATH).exists() else ""
            a, b = text.find("-- 결제 (v0.9"), text.find("-- 감사·삭제 로그")
            if 0 <= a < b:
                conn.execute(text[a:b])


app = FastAPI(title="headhunterAI", lifespan=lifespan)
app.include_router(routes_auth.router)
app.include_router(routes_applicant.router)
app.include_router(routes_admin.router)
app.include_router(routes_admin.ops_router)
app.include_router(routes_payments.router)


@app.middleware("http")
async def no_stale_static(request, call_next):
    """화면 파일(HTML·JS·CSS)은 브라우저가 매번 최신인지 확인하게 한다. 수정 후 일반 새로고침만으로 반영되도록."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/images/"):   # 이미지·지구 점 데이터는 자주 안 바뀜 → 7일 캐시(첫 화면 재방문 속도)
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif not path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.post("/api/dev/publish-now/{job_id}")
def dev_publish_now(job_id: int, user=Depends(security.current_user)):
    """개발 환경 전용: 결과 공개 시각(마감 다음 날 10:00)을 지금으로 당긴다. 운영 빌드(DEV_MODE=0)에는 없음."""
    if not config.DEV_MODE:
        raise HTTPException(404)
    db.run("""UPDATE attempts SET publish_at=now() WHERE application_id IN (SELECT id FROM applications WHERE job_id=%s)""", job_id)
    db.audit(user["id"], "dev_publish_now", "job", job_id)
    return {"ok": True}


@app.post("/api/dev/reset-my-applications")
def dev_reset_my_applications(app_id: int | None = None, user=Depends(security.current_user)):
    """개발 환경 전용: 지원자 테스트 계정(이지원)만 자기 지원 기록을 지운다(app_id를 주면 그 건만, 없으면 전부)(응시·답변·평가 포함, 이력서 파일은 유지).
    지원 건당 1회 응시(APP-02) 때문에 같은 공고로 장치 점검·면접을 반복 테스트하기 위한 것."""
    if not config.DEV_MODE or user["email"] != config.TEST_APPLICANT_EMAIL:
        raise HTTPException(404)
    only = " AND id=%s" if app_id else ""
    args = (user["id"], app_id) if app_id else (user["id"],)
    n = db.one("SELECT count(*) AS n FROM applications WHERE user_id=%s" + only, *args)["n"]
    db.run("DELETE FROM applications WHERE user_id=%s" + only, *args)
    db.audit(user["id"], "dev_reset_applications", "user", user["id"])
    return {"ok": True, "deleted": n}


@app.get("/privacy")
def privacy_page():
    """개인정보 처리방침(v0.9, 원본 legal/privacy_policy.md → main/privacy.html)"""
    return FileResponse(WEB_ROOT / "main" / "privacy.html")


@app.get("/terms")
def terms_page():
    """이용약관(v0.9, 원본 legal/terms_of_service.md → main/terms.html)"""
    return FileResponse(WEB_ROOT / "main" / "terms.html")


@app.get("/")
@app.get("/main.html")
def root():
    """공통 메인: 지원자·기업 담당자가 함께 들어오는 첫 페이지"""
    page = WEB_ROOT / "main" / "index.html"
    return FileResponse(page) if page.exists() else RedirectResponse("/applicant/")


for name in ("main", "admin", "applicant", "shared", "images"):
    if (WEB_ROOT / name).exists():
        app.mount(f"/{name}", StaticFiles(directory=WEB_ROOT / name, html=True), name=name)
