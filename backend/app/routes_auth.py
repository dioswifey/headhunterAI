"""Auth & public job routes (PRD 4.1 ACC-01~06, APP-01)."""
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import config, db, llm, presence, security

router = APIRouter(prefix="/api")
LOGIN_FAIL = "이메일 또는 비밀번호가 올바르지 않습니다."  # 계정 존재 여부를 드러내지 않음 (ACC-05)


class Signup(BaseModel):
    name: str
    email: str
    password: str
    phone: str
    age14: bool
    terms: bool
    privacy: bool


class Login(BaseModel):
    email: str
    password: str
    role: str  # applicant | recruiter


class ChangePw(BaseModel):
    current: str
    new: str


def public_user(u):
    return {k: u[k] for k in ("id", "role", "email", "name", "phone", "company_id", "must_change_password", "email_verified")}


@router.post("/auth/signup")
def signup(body: Signup, response: Response):
    if not (body.age14 and body.terms and body.privacy):
        raise HTTPException(400, "필수 항목에 모두 동의해야 가입할 수 있습니다.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(400, "이메일 형식을 확인해 주세요.")
    if not re.fullmatch(r"010-\d{4}-\d{4}", body.phone):
        raise HTTPException(400, "휴대전화번호는 010-0000-0000 형식으로 입력해 주세요.")
    if len(body.password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다.")
    if not body.name.strip():
        raise HTTPException(400, "이름을 입력해 주세요.")
    if db.one("SELECT 1 FROM users WHERE lower(email)=lower(%s)", body.email):
        raise HTTPException(409, "이미 가입된 이메일입니다.")
    # 인증 메일이 아직 없어 AUTO_VERIFY_EMAIL이면 이메일 인증을 자동 완료 처리 (TRD §2.4)
    u = db.run("""INSERT INTO users (role, email, password_hash, name, phone, email_verified, age14_confirmed)
                  VALUES ('applicant',%s,%s,%s,%s,%s,true) RETURNING *""",
               body.email.strip(), security.hash_pw(body.password), body.name.strip(), body.phone, config.AUTO_VERIFY_EMAIL)
    db.run("INSERT INTO consents (user_id, scope, version, items) VALUES (%s,'signup',%s,%s)",
           u["id"], config.CONSENT_VERSION, db.J({"terms": True, "privacy": True, "age14": True}))
    db.audit(u["id"], "signup", "user", u["id"])
    _set_cookie(response, security.create_session(u["id"]))
    return public_user(u)


def _set_cookie(response, token):
    response.set_cookie(security.COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 12, path="/")


@router.post("/auth/login")
def login(body: Login, response: Response):
    u = db.one("SELECT * FROM users WHERE lower(email)=lower(%s)", body.email.strip())
    roles = ("applicant",) if body.role == "applicant" else ("recruiter", "operator")
    if not u or u["role"] not in roles:
        raise HTTPException(401, LOGIN_FAIL)
    if u["locked_until"] and u["locked_until"] > security.now():
        raise HTTPException(429, "로그인 시도가 5회 실패해 10분간 제한됩니다. 잠시 후 다시 시도해 주세요.")
    if not security.verify_pw(u["password_hash"], body.password):
        fails = u["failed_logins"] + 1
        lock = security.now() + timedelta(minutes=10) if fails >= 5 else None
        db.run("UPDATE users SET failed_logins=%s, locked_until=%s WHERE id=%s", 0 if lock else fails, lock, u["id"])
        raise HTTPException(401, LOGIN_FAIL)
    db.run("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=%s", u["id"])
    _set_cookie(response, security.create_session(u["id"]))
    return public_user(u)


@router.post("/auth/logout")
def logout(response: Response, user=Depends(security.optional_user)):
    response.delete_cookie(security.COOKIE, path="/")
    if user:
        db.run("DELETE FROM sessions WHERE user_id=%s", user["id"])
    return {"ok": True}


@router.get("/auth/me")
def me(user=Depends(security.optional_user)):
    return public_user(user) if user else None


@router.post("/auth/change-password")
def change_password(body: ChangePw, user=Depends(security.current_user)):
    if not security.verify_pw(user["password_hash"], body.current):
        raise HTTPException(400, "현재 비밀번호가 올바르지 않습니다.")
    if len(body.new) < 8 or body.new == body.current:
        raise HTTPException(400, "새 비밀번호는 8자 이상이며 기존과 달라야 합니다.")
    db.run("UPDATE users SET password_hash=%s, must_change_password=false WHERE id=%s", security.hash_pw(body.new), user["id"])
    db.audit(user["id"], "change_password", "user", user["id"])
    return {"ok": True}


# ---------- public ----------
@router.get("/job-roles")
def job_roles():
    return db.all_("SELECT * FROM job_roles ORDER BY sort")


def publish_date(close_date):
    return (close_date + timedelta(days=1)).isoformat()


@router.get("/companies")
def companies(role: str | None = None):
    """공고를 올린 기업 목록(공개, APP-10): 기업마다 공개 공고 수와 지금 지원할 수 있는 공고 수(운영 중 직무·접수 중)."""
    return db.all_("""SELECT c.id, c.name, c.intro, MIN(j.location) location,
                      COUNT(j.id) jobs,
                      COUNT(j.id) FILTER (WHERE j.status='published' AND r.status='active'
                                          AND j.close_date >= (now() AT TIME ZONE 'Asia/Seoul')::date) open_jobs,
                      COUNT(j.id) FILTER (WHERE %s::text IS NOT NULL AND j.role_code=%s) role_jobs
                      FROM companies c JOIN jobs j ON j.company_id=c.id AND j.status IN ('published','closed')
                      JOIN job_roles r ON r.code=j.role_code
                      GROUP BY c.id ORDER BY c.id""", role, role)


@router.get("/jobs")
def jobs(role: str | None = None, q: str | None = None, company: int | None = None):
    rows = db.all_("""SELECT j.id, j.title, j.role_code, r.name role_name, r.status role_status, c.id company_id, c.name company,
                      j.location, j.employment_type, j.open_date, j.close_date, j.status, ec.coding_enabled,
                      (j.status='published' AND j.open_date <= (now() AT TIME ZONE 'Asia/Seoul')::date AND j.open_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - 7) AS is_new
                      FROM jobs j JOIN companies c ON c.id=j.company_id JOIN job_roles r ON r.code=j.role_code
                      JOIN eval_criteria ec ON ec.job_id=j.id
                      WHERE j.status IN ('published','closed')
                        AND (%s::text IS NULL OR j.role_code=%s)
                        AND (%s::int IS NULL OR j.company_id=%s)
                        AND (%s::text IS NULL OR j.title ILIKE '%%'||%s||'%%' OR c.name ILIKE '%%'||%s||'%%')
                      ORDER BY j.status='closed', r.status<>'active', r.sort, j.close_date""", role, role, company, company, q, q, q)
    live = presence.counts([r["id"] for r in rows if r["status"] == "published"])
    for r in rows:
        r["result_date"] = publish_date(r["close_date"])
        r["viewers"] = live.get(r["id"])          # 실측 · 표시 기준(10명) 미만이면 None
    return rows


@router.get("/jobs/{job_id}")
def job_detail(job_id: int):
    j = db.one("""SELECT j.*, c.name company, r.name role_name, r.status role_status, ec.coding_enabled,
                  (j.status='published' AND j.open_date <= (now() AT TIME ZONE 'Asia/Seoul')::date AND j.open_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - 7) AS is_new FROM jobs j JOIN companies c ON c.id=j.company_id
                  JOIN job_roles r ON r.code=j.role_code JOIN eval_criteria ec ON ec.job_id=j.id
                  WHERE j.id=%s AND j.status IN ('published','closed')""", job_id)
    if not j:
        raise HTTPException(404, "공고를 찾을 수 없습니다.")
    j["result_date"] = publish_date(j["close_date"])
    j["expected_minutes"] = 25 + (60 if j["coding_enabled"] else 0)
    return j


@router.post("/jobs/{job_id}/viewing")
def job_viewing(job_id: int, request: Request):
    """공고 상세를 보고 있다는 신호(30초마다). 반환 viewers = 실측 인원, 표시 기준 미만이면 null (presence.py)."""
    if not db.one("SELECT 1 FROM jobs WHERE id=%s AND status='published'", job_id):
        return {"viewers": None}
    return {"viewers": presence.beat(job_id, presence.client_ip(request))}


@router.get("/system/health")
def health():
    """APP-06 ⑤ AI 서버 상태."""
    ok_db = db.one("SELECT 1 ok") is not None
    return {"db": ok_db, "llm": llm.provider.health(), "dev_mode": config.DEV_MODE, "dev_bar": config.SHOW_DEVBAR}
