"""headhunterAI API + static web (admin/, applicant/, shared/, images/)."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, routes_admin, routes_applicant, routes_auth, scheduler, security, seed

WEB_ROOT = Path("/web")


@asynccontextmanager
async def lifespan(_app):
    db.pool.open(wait=True)
    seed.seed()
    scheduler.start()
    yield
    db.pool.close()


app = FastAPI(title="headhunterAI", lifespan=lifespan)
app.include_router(routes_auth.router)
app.include_router(routes_applicant.router)
app.include_router(routes_admin.router)
app.include_router(routes_admin.ops_router)


@app.post("/api/dev/publish-now/{job_id}")
def dev_publish_now(job_id: int, user=Depends(security.current_user)):
    """개발 환경 전용: 결과 공개 시각(마감 다음 날 10:00)을 지금으로 당긴다. 운영 빌드(DEV_MODE=0)에는 없음."""
    if not config.DEV_MODE:
        raise HTTPException(404)
    db.run("""UPDATE attempts SET publish_at=now() WHERE application_id IN (SELECT id FROM applications WHERE job_id=%s)""", job_id)
    db.audit(user["id"], "dev_publish_now", "job", job_id)
    return {"ok": True}


@app.get("/")
def root():
    return RedirectResponse("/applicant/")


for name in ("admin", "applicant", "shared", "images"):
    if (WEB_ROOT / name).exists():
        app.mount(f"/{name}", StaticFiles(directory=WEB_ROOT / name, html=True), name=name)
