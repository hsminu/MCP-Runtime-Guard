"""I6·I9: 오류 계약이 고정 사유만 공개하고 원문을 보관하지 않는지 검사한다."""

import pytest

from guard_app.domain.errors import DispatchError, ResolutionError, StoreError
from guard_app.domain.reasons import Reason


def test_resolution_error_exposes_only_reason_code() -> None:
    error = ResolutionError(Reason.UNKNOWN_SERVER)

    assert error.reason is Reason.UNKNOWN_SERVER
    assert str(error) == "UNKNOWN_SERVER"
    assert error.args == ("UNKNOWN_SERVER",)


def test_store_error_defaults_to_internal_error() -> None:
    error = StoreError()

    assert error.reason is Reason.INTERNAL_ERROR
    assert str(error) == "INTERNAL_ERROR"


def test_dispatch_error_keeps_type_without_exception_message() -> None:
    error = DispatchError("TimeoutError")

    assert error.reason is Reason.POST_DISPATCH_ERROR
    assert error.exception_type == "TimeoutError"
    assert str(error) == "POST_DISPATCH_ERROR"


def test_dispatch_error_rejects_raw_exception_message() -> None:
    with pytest.raises(ValueError, match="예외 타입 이름") as exc_info:
        DispatchError("token=TEST_SECRET_001")

    assert "TEST_SECRET_001" not in str(exc_info.value)


def test_error_reason_requires_enum() -> None:
    with pytest.raises(TypeError):
        ResolutionError("UNKNOWN_SERVER")  # type: ignore[arg-type]
