"""I6·I9: 예외 원문을 받지 않고 경계의 의미만 전달하는 전용 오류."""

from guard_app.domain.reasons import Reason


class GuardError(Exception):
    """호출자에게 공개할 고정 reason_code만 가진다."""

    def __init__(self, reason: Reason) -> None:
        if not isinstance(reason, Reason):
            raise TypeError("reason은 Reason enum이어야 한다")
        self.reason = reason
        super().__init__(reason.value)


class ResolutionError(GuardError):
    """예약 전 요청 해석 실패. Guard가 DENY로 변환한다."""


class StoreError(GuardError):
    """저장 실패. 예약 전후에 따라 Guard가 다른 결과로 변환한다."""

    def __init__(self, reason: Reason = Reason.INTERNAL_ERROR) -> None:
        super().__init__(reason)


class DispatchError(GuardError):
    """예약 후 오류. 원문 대신 예외 타입 이름만 별도 메타데이터로 가진다."""

    def __init__(self, exception_type: str) -> None:
        if not isinstance(exception_type, str) or not exception_type.isidentifier():
            raise ValueError("예외 타입 이름만 전달해야 한다")
        self.exception_type = exception_type
        super().__init__(Reason.POST_DISPATCH_ERROR)
