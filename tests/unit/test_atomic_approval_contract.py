"""B6-01 proposal: atomic approval result and integrity-error contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from guard_app.domain.contracts import ApprovalCommit, ApprovalRecord
from guard_app.domain.enums import ApprovalStatus, RequestStatus, TaskStatus
from guard_app.domain.errors import StoreError, StoreIntegrityError
from guard_app.domain.models import TaskState
from guard_app.domain.reasons import Reason


def _approval() -> ApprovalRecord:
    now = datetime(2026, 10, 29, tzinfo=UTC)
    return ApprovalRecord(
        approval_id="approval",
        request_id="request",
        snapshot_hash="a" * 64,
        approver_id="operator",
        status=ApprovalStatus.PENDING,
        created_at_utc=now,
        expires_at_utc=now + timedelta(minutes=1),
    )


def _task() -> TaskState:
    return TaskState(
        task_id="task",
        epoch=0,
        profile_id="profile",
        policy_version="version",
        status=TaskStatus.WAITING_APPROVAL,
    )


def test_approval_commit_is_frozen_and_contains_typed_rows():
    approval, task = _approval(), _task()
    result = ApprovalCommit(
        approval=approval, task=task, request_status=RequestStatus.WAITING_APPROVAL
    )
    assert result.approval == approval
    assert result.task == task
    assert result.request_status is RequestStatus.WAITING_APPROVAL
    with pytest.raises(ValidationError):
        result.request_status = RequestStatus.DENIED


@pytest.mark.parametrize(
    "changes",
    [
        {"request_status": "WAITING_APPROVAL"},
        {"approval": {}},
        {"task": {}},
        {"extra": "unexpected"},
    ],
)
def test_approval_commit_rejects_invalid_or_extra_fields(changes):
    fields = {
        "approval": _approval(),
        "task": _task(),
        "request_status": RequestStatus.WAITING_APPROVAL,
    }
    fields.update(changes)
    with pytest.raises(ValidationError):
        ApprovalCommit.model_validate(fields)


def test_store_integrity_error_has_only_fixed_reason():
    error = StoreIntegrityError()
    assert isinstance(error, StoreError)
    assert error.reason is Reason.INTERNAL_ERROR
    assert str(error) == "INTERNAL_ERROR"
    with pytest.raises(TypeError):
        StoreIntegrityError(Reason.APPROVAL_RACE)
