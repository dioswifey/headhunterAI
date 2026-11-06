"""요금제 결제 (PRD v0.9 PAY-07~12, TRD v0.6 14장) — 지금은 네이버페이 자동결제(정기결제)만.

흐름: 구독 시작(pending, 1회용 reg_state) → 브라우저가 네이버페이 등록창(JS SDK) → returnUrl
     → 서버가 등록 완료 API(recurrentId) → 첫 달 결제(예약·승인) → active, current_period_end = +1개월
     → 스케줄러가 next_charge_at에 자동 결제, 실패하면 하루 1번 3일 재시도 → 계속 실패하면 expired(Basic)
     → 해지: 네이버페이 등록 해지 + status=canceled, current_period_end까지는 Advanced 유지(PAY-04)
규칙: 금액은 config.PLANS(서버 요금표)에서만, 승인 결과는 서버가 직접 받은 응답으로만 반영, 카드·계좌 번호는 저장하지 않는다.
"""
import calendar
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config, db
from . import mock, naverpay
from .base import PayError

KST = ZoneInfo("Asia/Seoul")
LIVE_STATUSES = ("active", "canceled", "past_due")


def adapter(provider):
    return mock if provider == "mock" else naverpay


def enabled():
    return config.PAYMENTS_MODE in ("test", "live", "mock")


def provider_now():
    return "mock" if config.PAYMENTS_MODE == "mock" else "naverpay"


def add_month(dt):
    """KST 달력 기준 한 달 뒤(1월 31일 → 2월 28/29일)."""
    k = dt.astimezone(KST)
    y, m = (k.year + (k.month // 12), k.month % 12 + 1)
    d = min(k.day, calendar.monthrange(y, m)[1])
    return k.replace(year=y, month=m, day=d)


def merchant_user_id(owner_type, owner_id):
    """네이버페이에 보내는 사용자 키 — 개인정보가 아닌 해시값(문서: 개인 식별 정보 전달 금지)."""
    key = (config.NAVERPAY_CLIENT_SECRET or "hh-mock").encode()
    return hmac.new(key, f"{owner_type}:{owner_id}".encode(), hashlib.sha256).hexdigest()[:40]


def owner_of(user):
    if user["role"] == "applicant":
        return "user", user["id"], "applicant_advanced"
    if user["role"] in ("recruiter", "operator") and user.get("company_id"):
        return "company", user["company_id"], "company_advanced"   # 결제 주체 미결(결제 문서 미결 4) → 지금은 회사 담당자 누구나
    raise PayError("NoOwner", "결제할 수 있는 계정이 아닙니다.")


def live_subscription(owner_type, owner_id):
    return db.one(f"""SELECT * FROM subscriptions WHERE owner_type=%s AND owner_id=%s AND status IN {LIVE_STATUSES}
                      ORDER BY id DESC LIMIT 1""", owner_type, owner_id)


def plan_of(owner_type, owner_id):
    """'advanced' | 'basic'. 해지·재시도 중이어도 current_period_end까지는 advanced(PAY-04·PAY-06)."""
    s = live_subscription(owner_type, owner_id)
    if s and s["current_period_end"] and s["current_period_end"] > datetime.now(KST):
        return "advanced"
    return "basic"


def summary(user):
    try:
        ot, oid, code = owner_of(user)
    except PayError:
        return {"enabled": enabled(), "plan": "basic", "subscription": None}
    s = live_subscription(ot, oid)
    view = None
    if s:
        view = {"id": s["id"], "status": s["status"], "amount": s["amount"], "plan_code": s["plan_code"],
                "current_period_end": s["current_period_end"].isoformat() if s["current_period_end"] else None,
                "next_charge_at": s["next_charge_at"].isoformat() if s["next_charge_at"] and s["status"] in ("active", "past_due") else None}
    pays = db.all_("""SELECT p.id, p.amount, p.status, p.paid_at, p.period_start, p.period_end, p.fail_code FROM payments p
                      JOIN subscriptions s ON s.id=p.subscription_id WHERE s.owner_type=%s AND s.owner_id=%s
                      ORDER BY p.id DESC LIMIT 12""", ot, oid)
    for p in pays:
        for k in ("paid_at", "period_start", "period_end"):
            p[k] = p[k].isoformat() if p[k] else None
    return {"enabled": enabled(), "mode": config.PAYMENTS_MODE, "plan": plan_of(ot, oid), "plan_code": code,
            "price": config.PLANS[code]["amount"], "subscription": view, "payments": pays}


def start(user):
    """구독 시작 → 등록창 호출 값. 이미 쓰는 중이면 거절."""
    if not enabled():
        raise PayError("Disabled", "결제가 아직 열리지 않았습니다.")
    ot, oid, code = owner_of(user)
    if live_subscription(ot, oid):
        raise PayError("AlreadySubscribed", "이미 Advanced를 이용 중입니다.")
    plan = config.PLANS[code]
    state = secrets.token_urlsafe(24)
    with db.tx() as conn:   # 오래된 미완료 등록은 정리(같은 소유자당 pending 1건)
        conn.execute("UPDATE subscriptions SET status='failed', reg_state=NULL, updated_at=now() WHERE owner_type=%s AND owner_id=%s AND status='pending'", (ot, oid))
        sub = conn.execute("""INSERT INTO subscriptions (owner_type, owner_id, plan_code, amount, provider, status, reg_state, created_by)
                              VALUES (%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING *""",
                           (ot, oid, code, plan["amount"], provider_now(), state, user["id"])).fetchone()
    return_url = f"{config.PUBLIC_BASE_URL}/api/payments/naverpay/return?sub={sub['id']}&state={state}"
    return adapter(sub["provider"]).sdk_params(plan, sub, return_url, merchant_user_id(ot, oid))


def _event(sub_id, action, actor=None):
    db.run("INSERT INTO audit_logs (actor_id, action, target_type, target_id) VALUES (%s,%s,'subscription',%s)", actor, action, str(sub_id))


def finish_registration(sub_id, state, q):
    """returnUrl 처리. 성공하면 첫 달 결제까지 하고 'ok', 아니면 실패 사유 코드."""
    sub = db.one("SELECT * FROM subscriptions WHERE id=%s", sub_id)
    if not sub or sub["status"] != "pending" or not sub["reg_state"] or not hmac.compare_digest(sub["reg_state"], state or ""):
        return sub, "invalid"
    db.run("UPDATE subscriptions SET reg_state=NULL, updated_at=now() WHERE id=%s", sub_id)   # 1회용
    if q.get("resultCode") != "Success":
        db.run("UPDATE subscriptions SET status='failed', updated_at=now() WHERE id=%s", sub_id)
        return sub, (q.get("resultCode") or "Fail")[:40]
    ad = adapter(sub["provider"])
    try:
        rid = ad.approve_registration(q.get("reserveId", ""), q.get("tempReceiptId", ""), f"reg-{sub_id}-{q.get('reserveId', '')}"[:64])
    except PayError as e:
        db.run("UPDATE subscriptions SET status='failed', updated_at=now() WHERE id=%s", sub_id)
        return sub, e.code[:40]
    db.run("UPDATE subscriptions SET recurrent_id=%s, updated_at=now() WHERE id=%s", rid, sub_id)
    sub = db.one("SELECT * FROM subscriptions WHERE id=%s", sub_id)
    ok, code = charge_period(sub, first=True)
    if not ok:   # 첫 결제 실패 → 등록도 해지해 다음 결제가 생기지 않게
        try:
            ad.expire(rid, "첫 결제 실패", False, f"exp-{sub_id}-first")
        except PayError:
            pass
        db.run("UPDATE subscriptions SET status='failed', updated_at=now() WHERE id=%s", sub_id)
        return sub, code
    _event(sub_id, "subscription_started", sub["created_by"])
    return sub, "ok"


def charge_period(sub, first=False):
    """한 기간(1개월) 결제. 성공하면 기간을 늘린다. (성공 여부, 실패 코드)."""
    now = datetime.now(KST)
    start_at = now if first or not sub["current_period_end"] or sub["current_period_end"] < now else sub["current_period_end"]
    end_at = add_month(start_at)
    plan = config.PLANS[sub["plan_code"]]
    mpid = f"hh{sub['id']}-{start_at.astimezone(KST):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    pay = db.one("""INSERT INTO payments (subscription_id, merchant_pay_id, amount, provider, period_start, period_end)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""", sub["id"], mpid, sub["amount"], sub["provider"], start_at, end_at)
    try:
        pid, receipt = adapter(sub["provider"]).charge(sub["recurrent_id"], sub["amount"], plan["name"], mpid,
                                                       merchant_user_id(sub["owner_type"], sub["owner_id"]))
    except PayError as e:
        db.run("UPDATE payments SET status='failed', fail_code=%s, fail_message=%s WHERE id=%s", e.code[:40], e.message[:300], pay["id"])
        return False, e.code[:40]
    with db.tx() as conn:
        conn.execute("UPDATE payments SET status='paid', provider_payment_id=%s, paid_at=now(), receipt=%s WHERE id=%s",
                     (pid, db.J(receipt), pay["id"]))
        conn.execute("""UPDATE subscriptions SET status='active', current_period_end=%s, next_charge_at=%s, retry_count=0, updated_at=now()
                        WHERE id=%s""", (end_at, end_at, sub["id"]))
    return True, "ok"


def cancel(user):
    """해지(PAY-04): 다음 결제만 멈춘다. 네이버페이 등록을 바로 해지하고 기간 끝까지 Advanced 유지."""
    ot, oid, _ = owner_of(user)
    s = live_subscription(ot, oid)
    if not s or s["status"] == "canceled":
        raise PayError("NotSubscribed", "해지할 정기결제가 없습니다.")
    if s["recurrent_id"]:
        adapter(s["provider"]).expire(s["recurrent_id"], "이용자 해지", True, f"exp-{s['id']}-user")
    db.run("UPDATE subscriptions SET status='canceled', canceled_at=now(), next_charge_at=NULL, updated_at=now() WHERE id=%s", s["id"])
    _event(s["id"], "subscription_canceled", user["id"])
    return s


def renew_due():
    """스케줄러(1분마다 housekeeping): 갱신일이 된 구독을 결제하고, 기간이 끝난 해지·실패 구독을 정리한다."""
    if not enabled():
        return
    now = datetime.now(KST)
    for s in db.all_("SELECT * FROM subscriptions WHERE status IN ('active','past_due') AND next_charge_at <= now() ORDER BY id LIMIT 20"):
        ok, code = charge_period(s)
        if ok:
            _event(s["id"], "subscription_renewed")
            continue
        n = s["retry_count"] + 1
        if n >= config.RENEW_RETRY_DAYS:   # 3일 동안 실패 → Basic으로(등록도 해지)
            try:
                adapter(s["provider"]).expire(s["recurrent_id"], "결제 실패로 해지", False, f"exp-{s['id']}-fail")
            except PayError:
                pass
            db.run("UPDATE subscriptions SET status='expired', retry_count=%s, next_charge_at=NULL, updated_at=now() WHERE id=%s", n, s["id"])
            _event(s["id"], "subscription_expired_payment_failed")
        else:
            db.run("UPDATE subscriptions SET status='past_due', retry_count=%s, next_charge_at=%s, updated_at=now() WHERE id=%s",
                   n, now + timedelta(days=1), s["id"])
    db.run("UPDATE subscriptions SET status='expired', updated_at=now() WHERE status='canceled' AND current_period_end < now()")
    db.run("UPDATE subscriptions SET status='failed', reg_state=NULL, updated_at=now() WHERE status='pending' AND created_at < now() - interval '1 hour'")


def refund(payment_id, operator_id, reason):
    """운영자 환불(환불 기준은 미결 → 운영자가 판단해 전액 취소만). 기간도 같이 끝낸다."""
    p = db.one("SELECT p.*, s.provider s_provider FROM payments p JOIN subscriptions s ON s.id=p.subscription_id WHERE p.id=%s", payment_id)
    if not p or p["status"] != "paid":
        raise PayError("NotPaid", "취소할 수 있는 결제가 아닙니다.")
    adapter(p["s_provider"]).cancel_payment(p["provider_payment_id"], p["amount"], reason or "운영자 환불", f"cnl-{p['merchant_pay_id']}")
    db.run("UPDATE payments SET status='canceled', canceled_at=now() WHERE id=%s", payment_id)
    db.run("UPDATE subscriptions SET current_period_end=least(current_period_end, now()), updated_at=now() WHERE id=%s", p["subscription_id"])
    db.run("INSERT INTO audit_logs (actor_id, action, target_type, target_id) VALUES (%s,'payment_refunded','payment',%s)", operator_id, str(payment_id))
