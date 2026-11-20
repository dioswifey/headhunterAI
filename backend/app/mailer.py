"""SMTP 발송 (v0.6). Gmail SMTP만 사용 — 계정·앱 비밀번호는 .env(커밋 안 됨)에서만 읽는다."""
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

from . import config


class MailError(Exception):
    pass


def enabled():
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD)


def route(to_addr):
    """실제로 보낼 주소. 개발 환경에서 테스트 계정 주소면 TEST_MAIL_REDIRECT로 바꾼다(그 외는 그대로)."""
    if config.DEV_MODE and config.TEST_MAIL_REDIRECT and (to_addr or "").strip().lower() == config.TEST_APPLICANT_EMAIL.lower():
        return config.TEST_MAIL_REDIRECT
    return to_addr


def send(to_addr, subject, text, html):
    if not enabled():
        raise MailError("smtp_not_configured")
    msg = EmailMessage()
    msg["From"] = config.MAIL_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="headhunter.ai")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            if config.SMTP_STARTTLS:
                s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(type(e).__name__) from e
