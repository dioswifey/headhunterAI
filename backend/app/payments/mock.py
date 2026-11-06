"""가짜 결제 어댑터 — 로컬 개발 전용(PAYMENTS_MODE=mock, Railway·DEV_MODE=0에서는 config가 off로 바꿈).
네이버페이 키 없이 등록 → 승인 → 갱신 → 해지 흐름을 시험한다. 실제 돈은 오가지 않는다."""
import uuid

from .base import PayError

FAIL_NEXT = {"charge": 0}   # 시험용: 다음 n번의 charge를 실패시킨다(qa_checks에서 사용)


def sdk_params(plan, sub, return_url, merchant_user_id):
    sep = "&" if "?" in return_url else "?"
    return {"mock": True, "redirect": f"{return_url}{sep}resultCode=Success&reserveId=mock-rsv-{uuid.uuid4().hex[:12]}"
                                      f"&tempReceiptId=mock-tmp-{uuid.uuid4().hex[:12]}"}


def approve_registration(reserve_id, temp_receipt_id, idem):
    if not str(reserve_id).startswith("mock-"):
        raise PayError("InvalidParameter", "가짜 등록 번호가 아님")
    return "mock-rec-" + uuid.uuid4().hex[:16]


def charge(recurrent_id, amount, product_name, merchant_pay_id, merchant_user_id):
    if FAIL_NEXT["charge"] > 0:
        FAIL_NEXT["charge"] -= 1
        raise PayError("NotEnoughAccountBalance", "가짜 결제 실패(시험)")
    return "mock-pay-" + uuid.uuid4().hex[:16], {"primaryPayMeans": "MOCK", "totalPayAmount": amount}


def expire(recurrent_id, reason, by_user, idem):
    return None


def cancel_payment(payment_id, amount, reason, idem):
    return None
