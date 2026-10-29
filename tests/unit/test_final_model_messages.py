"""A 리뷰 후속: NOTIFY-01 고정 문구와 Released 본문 비조회(I5/I9)."""

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


def test_nonexecution_cause_uses_only_the_fixed_termination_message(subtests):
    for case_0, cause in enumerate(list(MESSAGES)):
        for case_1, reason in enumerate(
            [Reason.INTERNAL_TO_EXTERNAL, Reason.TOOL_FINGERPRINT_CHANGED, Reason.INTERNAL_ERROR]
        ):
            with subtests.test(case_0=case_0, case_1=case_1):
                notice = final(cause, Denied(request_id="PRIVATE_request", reasons=(reason,)))
                assert final_model_message(notice) == (False, MESSAGES[cause])
                assert reason.value not in final_model_message(notice)[1]
                assert "PRIVATE_" not in final_model_message(notice)[1]


def test_executed_denial_reuses_existing_reason_visibility(subtests):
    for case_0, reason in enumerate(list(Reason)):
        with subtests.test(case_0=case_0):
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
def test_executed_released_returns_empty_marker_and_preserves_success_meaning(is_error, structured):
    result = ReleasedResult(
        text=PRIVATE + " 한글", structured_content=structured, is_error=is_error
    )
    released = Released(request_id="PRIVATE_request", result=result, label=Label.CONFIDENTIAL)
    notice = final(outcome=released)
    success, content = final_model_message(notice)
    assert success is True
    assert content == ""
    assert PRIVATE not in content
    assert PRIVATE not in repr(notice)
    assert final_model_message(notice) == outcome_model_message(released) == (True, "")


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
def test_executed_released_does_not_read_or_validate_body(result, monkeypatch):
    outcome = Released.model_construct(request_id="PRIVATE_request", result=result, label=None)
    forged = PendingFinal.model_construct(cause="executed", outcome=outcome)
    original = Released.__getattribute__

    def unreadable(self, name):
        if name == "result":
            pytest.fail("고정 문구 함수는 Released 본문을 읽지 않는다")
        return original(self, name)

    monkeypatch.setattr(Released, "__getattribute__", unreadable)
    assert final_model_message(forged) == (True, "")


def test_executed_released_does_not_serialize_non_utf8_body():
    result = ReleasedResult.model_construct(
        text="\ud800PRIVATE_payload", structured_content=None, is_error=False
    )
    outcome = Released.model_construct(request_id="PRIVATE_request", result=result, label=None)
    forged = PendingFinal.model_construct(cause="executed", outcome=outcome)
    assert final_model_message(forged) == (True, "")
