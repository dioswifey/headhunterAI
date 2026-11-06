"""결제 API (PRD v0.9 PAY-07~12, TRD v0.6 4장·14장)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import config, payments, security
from .payments import PayError

router = APIRouter(prefix="/api/payments")


def _billing_page(sub, result):
    base = "/admin/#/billing" if sub and sub["owner_type"] == "company" else "/applicant/#/billing"
    return f"{base}?result={result}"


def _pay_user(user):
    if user["role"] in ("recruiter", "operator") and user.get("must_change_password"):
        raise HTTPException(403, "초기 비밀번호를 먼저 변경해 주세요.")
    return user


@router.get("/me")
def my_plan(user=Depends(security.current_user)):
    """내(지원자) 또는 우리 회사(기업 담당자)의 요금제·정기결제·결제 내역."""
    return payments.summary(user)


@router.post("/subscriptions")
def subscribe(user=Depends(security.current_user)):
    """Advanced 정기결제 시작 → 네이버페이 등록창 호출 값(공개 값만). 금액은 서버 요금표에서 정한다."""
    try:
        return payments.start(_pay_user(user))
    except PayError as e:
        raise HTTPException(409 if e.code == "AlreadySubscribed" else 400, e.message or e.code)


@router.get("/naverpay/return")
def naverpay_return(request: Request, sub: int, state: str = ""):
    """네이버페이 등록창 → returnUrl. 1회용 state로 우리 주문을 확인하고, 서버가 등록 완료·첫 결제를 직접 요청한다."""
    q = dict(request.query_params)
    s, result = payments.finish_registration(sub, state, q)
    return RedirectResponse(_billing_page(s, result), status_code=303)


@router.post("/subscriptions/cancel")
def cancel(user=Depends(security.current_user)):
    try:
        payments.cancel(_pay_user(user))
    except PayError as e:
        raise HTTPException(400, e.message or e.code)
    return payments.summary(user)


class Refund(BaseModel):
    reason: str = ""


@router.post("/{payment_id}/refund")
def refund(payment_id: int, body: Refund, user=Depends(security.operator)):
    """운영자 전용 전액 취소(환불 기준 미결 → 운영자 판단, 감사 로그)."""
    try:
        payments.refund(payment_id, user["id"], body.reason)
    except PayError as e:
        raise HTTPException(400, e.message or e.code)
    return {"ok": True}


@router.get("/config")
def pay_config():
    """화면이 결제 버튼을 열지 정할 때 쓰는 공개 정보(키 없음)."""
    return {"enabled": payments.enabled(), "mode": config.PAYMENTS_MODE,
            "plans": {k: {"amount": v["amount"], "name": v["name"]} for k, v in config.PLANS.items()}}
