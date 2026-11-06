"""네이버페이 자동결제 어댑터 (공식 문서 docs.pay.naver.com, 2026-11-06 확인).

- 결제 API: https://{dev-pay|pay}.paygate.naver.com/naverpay-partner/naverpay/payments/{버전}/{API명}
- 헤더: X-Naver-Client-Id · X-Naver-Client-Secret · X-NaverPay-Chain-Id · X-NaverPay-Idempotency-Key(64자 이내)
- 본문: application/x-www-form-urlencoded, 응답 {code, message, body}
- 등록창: JS SDK https://nsp.pay.naver.com/sdk/js/naverpay.min.js (payType=recurrent) → returnUrl?resultCode&reserveId&tempReceiptId
"""
import httpx

from .. import config
from .base import PayError

SDK_URL = "https://nsp.pay.naver.com/sdk/js/naverpay.min.js"


def _host():
    return "https://pay.paygate.naver.com" if config.PAYMENTS_MODE == "live" else "https://dev-pay.paygate.naver.com"


def _post(path, data, idem, timeout=30):
    url = f"{_host()}/naverpay-partner/naverpay/payments/{path}"
    headers = {"X-Naver-Client-Id": config.NAVERPAY_CLIENT_ID, "X-Naver-Client-Secret": config.NAVERPAY_CLIENT_SECRET,
               "X-NaverPay-Chain-Id": config.NAVERPAY_CHAIN_ID, "X-NaverPay-Idempotency-Key": idem[:64]}
    try:
        r = httpx.post(url, data=data, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise PayError("NetworkError", f"{type(e).__name__}") from e
    try:
        j = r.json()
    except ValueError:
        raise PayError(f"HTTP{r.status_code}", "응답을 읽을 수 없음")
    # 409 = 같은 멱등 키 재요청 → 이전 응답이 그대로 온다(문서). 결과 코드로 판단
    if j.get("code") != "Success":
        raise PayError(j.get("code") or f"HTTP{r.status_code}", (j.get("message") or "")[:300])
    return j.get("body") or {}


def sdk_params(plan, sub, return_url, merchant_user_id):
    """브라우저가 Naver.Pay.create / open 에 넘길 값(공개 값만). 시크릿은 넣지 않는다."""
    return {"sdk": SDK_URL,
            "create": {"payType": "recurrent", "mode": "production" if config.PAYMENTS_MODE == "live" else "development",
                       "clientId": config.NAVERPAY_CLIENT_ID, "chainId": config.NAVERPAY_CHAIN_ID},
            "open": {"productCode": sub["plan_code"], "productName": plan["name"], "totalPayAmount": sub["amount"],
                     "returnUrl": return_url, "merchantUserId": merchant_user_id}}


def approve_registration(reserve_id, temp_receipt_id, idem):
    b = _post("recurrent/regist/v1/approval", {"reserveId": reserve_id, "tempReceiptId": temp_receipt_id}, idem)
    if not b.get("recurrentId"):
        raise PayError("NoRecurrentId", "등록 번호 없음")
    return b["recurrentId"]


def charge(recurrent_id, amount, product_name, merchant_pay_id, merchant_user_id):
    """자동결제 예약 → 승인. 성공하면 (paymentId, 영수증 요약). 과세 금액 = 전액(면세 0)."""
    b = _post("recurrent/pay/v3/reserve", {"recurrentId": recurrent_id, "totalPayAmount": amount, "taxScopeAmount": amount,
                                            "taxExScopeAmount": 0, "productName": product_name, "merchantPayId": merchant_pay_id,
                                            "merchantUserId": merchant_user_id}, "rsv-" + merchant_pay_id)
    pid = b.get("paymentId")
    if not pid:
        raise PayError("NoPaymentId", "결제 번호 없음")
    a = _post("recurrent/pay/v3/approval", {"paymentId": pid}, "apv-" + merchant_pay_id, timeout=60)  # 문서: 60초
    d = a.get("detail") or {}
    if d.get("admissionState") and d["admissionState"] != "SUCCESS":
        raise PayError("AdmissionFail", "결제 승인 실패")
    receipt = {k: d.get(k) for k in ("admissionYmdt", "totalPayAmount", "primaryPayMeans", "cardCorpCode", "payHistId") if d.get(k) is not None}
    return pid, receipt   # 카드·계좌 번호(마스킹 값 포함)는 저장하지 않는다


def expire(recurrent_id, reason, by_user, idem):
    try:
        _post("recurrent/expire/v1/request", {"recurrentId": recurrent_id, "expireRequester": "1" if by_user else "2",
                                               "expireReason": reason[:200]}, idem)
    except PayError as e:
        if e.code != "Expired":   # 이미 해지된 번호는 성공으로 본다
            raise


def cancel_payment(payment_id, amount, reason, idem):
    _post("v1/cancel", {"paymentId": payment_id, "cancelAmount": amount, "cancelReason": reason[:200], "cancelRequester": "2",
                        "taxScopeAmount": amount, "taxExScopeAmount": 0}, idem, timeout=60)
