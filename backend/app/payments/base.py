"""결제 제공사 공통 (TRD v0.6 14장). 제공사별 어댑터는 같은 함수 이름을 쓴다:
sdk_params · approve_registration · charge · expire · cancel_payment."""


class PayError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
