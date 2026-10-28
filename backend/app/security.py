"""Password hashing (Argon2), server-side sessions, role guards (ACC-01~06)."""
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, HTTPException

from . import config, db

ph = PasswordHasher()
COOKIE = "hh_session"


def hash_pw(pw):
    return ph.hash(pw)


def verify_pw(hashed, pw):
    try:
        return ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def now():
    return datetime.now(timezone.utc)


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions (token, user_id, expires_at) VALUES (%s,%s,%s)",
           token, user_id, now() + timedelta(hours=config.SESSION_HOURS))
    return token


def _user_from_token(token):
    if not token:
        return None
    row = db.one("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                    WHERE s.token=%s AND s.expires_at > now()""", token)
    if row:  # sliding expiration: 마지막 요청 후 2시간
        db.run("UPDATE sessions SET expires_at=%s WHERE token=%s",
               now() + timedelta(hours=config.SESSION_HOURS), token)
    return row


def current_user(hh_session: str | None = Cookie(default=None)):
    user = _user_from_token(hh_session)
    if not user:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


def optional_user(hh_session: str | None = Cookie(default=None)):
    return _user_from_token(hh_session)


def applicant(hh_session: str | None = Cookie(default=None)):
    user = current_user(hh_session)
    if user["role"] != "applicant":
        raise HTTPException(403, "지원자 전용 기능입니다.")
    return user


def recruiter(hh_session: str | None = Cookie(default=None)):
    user = current_user(hh_session)
    if user["role"] not in ("recruiter", "operator"):
        raise HTTPException(403, "기업 담당자 전용 기능입니다.")
    if user["must_change_password"]:
        raise HTTPException(403, "초기 비밀번호를 먼저 변경해 주세요.")
    return user


def operator(hh_session: str | None = Cookie(default=None)):
    user = current_user(hh_session)
    if user["role"] != "operator":
        raise HTTPException(403, "운영자 전용 기능입니다.")
    return user
