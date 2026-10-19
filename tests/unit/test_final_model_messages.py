"""A 리뷰 후속: NOTIFY-01 확정 문구와 executed에서만 공개 본문 전달(I5/I9)."""

import json
import traceback

import pytest

from guard_app.core.model_messages import (
    MODEL_VISIBLE_REASONS,
    ModelMessageError,
    final_model_message,
    outcome_model_message,
)
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    PendingFinal,
    Released,
    ReleasedResult,
    Withheld,
)
from guard_app.domain.enums import Label
from guard_app.domain.reasons import Reason

PRIVATE = "PRIVATE_final_body"
MESSAGES = {
    "rejected": "승인 요청이 거절되었습니다",
    "expired": "승인 요청이 만료되었습니다",
    "cancelled": "요청이 취소되었습니다",
    "invalidated": "승인 요청이 무효화되었습니다",
    "failed": "작업이 정지되었습니다",
    "shutdown": "작업이 정지되었습니다",
}


def final(cause="executed", outcome=None):
    return PendingFinal(
        task_id="PRIVATE_task",
        epoch=3,
        request_id="PRIVATE_request",
        approval_id="PRIVATE_approval",
        cause=cause,
        outcome=outcome or Denied(request_id="PRIVATE_request", reasons=(Reason.INTERNAL_ERROR,)),
    )


@pytest.mark.parametrize("cause", list(MESSAGES))
@pytest.mark.parametrize(
    "reason", [Reason.INTERNAL_TO_EXTERNAL, Reason.TOOL_FINGERPRINT_CHANGED, Reason.INTERNAL_ERROR]
)
def test_nonexecution_cause_uses_only_the_fixed_termination_message(cause, reason):
    notice = final(cause, Denied(request_id="PRIVATE_request", reasons=(reason,)))
    assert final_model_message(notice) == (False, MESSAGES[cause])
    assert reason.value not in final_model_message(notice)[1]
    assert "PRIVATE_" not in final_model_message(notice)[1]


@pytest.mark.parametrize("reason", list(Reason))
def test_executed_denial_reuses_existing_reason_visibility(reason):
    denied = Denied(request_id="PRIVATE_request", reasons=(reason,))
    assert final_model_message(final(outcome=denied)) == outcome_model_message(denied)
    if reason not in MODEL_VISIBLE_REASONS:
        assert reason.value not in final_model_message(final(outcome=denied))[1]


def test_private_reason_masks_other_reasons_in_executed_denial():
    denied = Denied(
        request_id="PRIVATE_request",
        reasons=(Reason.INVALID_ARGUMENTS, Reason.APPROVAL_STALE),
    )
    assert final_model_message(final(outcome=denied)) == (
        False,
        "이 요청은 정책에 의해 차단되었습니다",
    )


@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("structured", [None, {"value": PRIVATE, "count": 1, "array": [True, 0.1]}])
def test_executed_released_serializes_only_released_payload_and_preserves_success_meaning(
    is_error, structured
):
    result = ReleasedResult(
        text=PRIVATE + " 한글", structured_content=structured, is_error=is_error
    )
    released = Released(request_id="PRIVATE_request", result=result, label=Label.CONFIDENTIAL)
    notice = final(outcome=released)
    success, content = final_model_message(notice)
    assert success is True
    assert json.loads(content) == {
        "text": PRIVATE + " 한글",
        "structured_content": structured,
        "is_error": is_error,
    }
    assert "한글" in content
    assert "PRIVATE_task" not in content
    assert "PRIVATE_request" not in content
    assert "PRIVATE_approval" not in content
    assert "CONFIDENTIAL" not in content
    assert PRIVATE not in repr(notice)
    assert outcome_model_message(released) == (True, "")  # 기존 API 본문 비조회 보존.
    assert final_model_message(notice) == final_model_message(notice)


@pytest.mark.parametrize("kind", [Withheld, OutcomeUnknown])
def test_executed_postdispatch_failure_is_stopped_without_reasons_or_body(kind):
    outcome = kind(request_id="PRIVATE_request", reasons=(Reason.POST_DISPATCH_ERROR,))
    assert final_model_message(final(outcome=outcome)) == (False, "작업이 정지되었습니다")


def test_nonexecuted_released_body_is_not_read_even_in_a_forged_notice():
    class Unreadable:
        def __getattribute__(self, name):
            pytest.fail("비실행 알림은 공개 본문도 읽지 않는다")

        def __repr__(self):
            pytest.fail("본문 repr을 만들지 않는다")

    released = Released.model_construct(
        request_id="PRIVATE_request", result=Unreadable(), label=None
    )
    for cause in MESSAGES:
        forged = final(cause).model_construct(**(final(cause).model_dump() | {"outcome": released}))
        with pytest.raises(ModelMessageError) as caught:
            final_model_message(forged)
        assert "PRIVATE_" not in repr(caught.value)


@pytest.mark.parametrize(
    "value", [None, PRIVATE, {}, True, Denied(request_id=None, reasons=(Reason.OK,))]
)
def test_non_pending_final_input_is_a_fixed_error(value):
    with pytest.raises(ModelMessageError) as caught:
        final_model_message(value)
    assert "PRIVATE_" not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is caught.value.__cause__ is None


@pytest.mark.parametrize("cause", ["PRIVATE_cause", None, [], {}, True])
def test_unknown_or_non_string_cause_is_a_fixed_error(cause):
    forged = PendingFinal.model_construct(**(final().model_dump() | {"cause": cause}))
    with pytest.raises(ModelMessageError) as caught:
        final_model_message(forged)
    assert "PRIVATE_" not in repr(caught.value)


def test_missing_fields_and_nonfinal_outcome_are_rejected():
    pending = Pending(
        request_id="PRIVATE_request", approval_id="PRIVATE_approval", reasons=(Reason.OK,)
    )
    for value in (
        PendingFinal.model_construct(),
        PendingFinal.model_construct(cause="executed"),
        PendingFinal.model_construct(cause="executed", outcome=pending),
        PendingFinal.model_construct(
            cause="failed", outcome=Denied.model_construct(reasons=(PRIVATE,))
        ),
    ):
        with pytest.raises(ModelMessageError) as caught:
            final_model_message(value)
        assert "PRIVATE_" not in repr(caught.value)


@pytest.mark.parametrize(
    "result", [None, PRIVATE, {"text": PRIVATE}, ReleasedResult.model_construct(text=object())]
)
def test_unvalidated_released_body_never_enters_errors(result):
    outcome = Released.model_construct(request_id="PRIVATE_request", result=result, label=None)
    forged = PendingFinal.model_construct(cause="executed", outcome=outcome)
    with pytest.raises(ModelMessageError) as caught:
        final_model_message(forged)
    assert "PRIVATE_" not in repr(caught.value)


def test_serialization_failure_has_no_raw_exception_context():
    result = ReleasedResult.model_construct(
        text="\ud800PRIVATE_payload", structured_content=None, is_error=False
    )
    outcome = Released.model_construct(request_id="PRIVATE_request", result=result, label=None)
    forged = PendingFinal.model_construct(cause="executed", outcome=outcome)
    with pytest.raises(ModelMessageError) as caught:
        final_model_message(forged)
    assert caught.value.__context__ is caught.value.__cause__ is None
    assert "PRIVATE_" not in "".join(traceback.format_exception(caught.value))
