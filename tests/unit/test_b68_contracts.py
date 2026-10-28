"""SEQ-01·02와 B-SQL-04의 계약 입력 검증(I9)."""

import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guard_app.domain.models import MAX_SAFE_SEQUENCE, FailureAuditRecord
from guard_app.domain.reasons import Reason
from guard_app.ports import FailureAuditStore, SequenceAllocator

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def _record(**updates):
    values = {
        "task_id": "TASK_SECRET",
        "request_id": "REQUEST_SECRET",
        "phase": "before_dispatch",
        "reason": Reason.INTERNAL_ERROR,
        "exception_type": "ExceptionType",
        "at_utc": NOW,
        "run_id": "RUN_SECRET",
        "epoch": MAX_SAFE_SEQUENCE,
    }
    values.update(updates)
    return FailureAuditRecord(**values)


def test_failure_audit_record_is_immutable_and_repr_omits_identifiers():
    record = _record()
    assert MAX_SAFE_SEQUENCE == 9007199254740991
    assert all(
        secret not in repr(record) for secret in ("TASK_SECRET", "REQUEST_SECRET", "RUN_SECRET")
    )
    assert record.model_dump()["task_id"] == "TASK_SECRET"
    with pytest.raises(ValidationError):
        record.reason = Reason.STATE_MISSING
    with pytest.raises(ValidationError):
        record.model_copy(update={"epoch": MAX_SAFE_SEQUENCE + 1})
    assert FailureAuditRecord.model_validate_json(record.model_dump_json()) == record


def test_failure_audit_record_allows_absent_binding_and_optional_request():
    record = FailureAuditRecord(
        task_id="task",
        phase="after_dispatch",
        reason=Reason.POST_DISPATCH_ERROR,
        exception_type="TimeoutError",
        at_utc=NOW,
    )
    assert record.request_id is None
    assert record.run_id is None
    assert record.epoch is None


@pytest.mark.parametrize("value", ["", "3Error", "Name With Space", "예외", "a" * 65])
def test_failure_audit_record_rejects_non_ascii_or_invalid_exception_names(value):
    with pytest.raises(ValidationError):
        _record(exception_type=value)


@pytest.mark.parametrize(
    "value", [None, NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=9)))]
)
def test_failure_audit_record_requires_utc(value):
    with pytest.raises(ValidationError):
        _record(at_utc=value)


@pytest.mark.parametrize(
    "updates",
    [
        {"task_id": ""},
        {"request_id": ""},
        {"run_id": ""},
        {"epoch": -1},
        {"epoch": MAX_SAFE_SEQUENCE + 1},
        {"epoch": True},
        {"phase": "other"},
        {"reason": "INTERNAL_ERROR"},
    ],
)
def test_failure_audit_record_rejects_invalid_fields(updates):
    with pytest.raises(ValidationError):
        _record(**updates)


def test_store_contracts_expose_async_methods():
    assert inspect.iscoroutinefunction(SequenceAllocator.allocate_call_seq)
    assert inspect.iscoroutinefunction(FailureAuditStore.record_failure_audit)
