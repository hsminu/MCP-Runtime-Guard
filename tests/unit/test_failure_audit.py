"""원문 없는 실패 감사·Guard 시간 공급자의 실제 SQLite 연결(I9, B-SQL-04)."""

import asyncio
import json
from datetime import UTC, datetime
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, Withheld
from guard_app.domain.enums import Label
from guard_app.domain.errors import StoreError
from guard_app.domain.models import FailureAuditRecord
from guard_app.domain.reasons import Reason
from guard_app.services.guard import FailureEvent, GuardService, durable_failure_audit
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness

NOW = datetime(2026, 10, 13, 12, 34, 56, 123456, tzinfo=UTC)


async def _rows(store):
    return await store._run_on_writer_async(
        lambda connection: [
            tuple(row)
            for row in connection.execute(
                """SELECT request_id,task_id,event_type,reason_code,detail,at_utc
                FROM audit_events WHERE event_type LIKE 'FAILURE_%' ORDER BY event_id"""
            ).fetchall()
        ]
    )


@pytest.mark.parametrize(
    ("phase", "event_type", "request_id"),
    [
        ("before_dispatch", "FAILURE_BEFORE_DISPATCH", None),
        ("after_dispatch", "FAILURE_AFTER_DISPATCH", "request-1"),
    ],
)
def test_failure_audit_persists_only_fixed_fields_and_works_after_guard_halt(
    tmp_path, phase, event_type, request_id
):
    async def scenario():
        ids = count()
        store = SQLiteStore(tmp_path / "audit.db", audit_id_factory=lambda: f"id-{next(ids)}")
        try:
            store.halt_execution(Reason.INTERNAL_ERROR)
            record = FailureAuditRecord(
                task_id="task-1",
                request_id=request_id,
                phase=phase,
                reason=Reason.STATE_COMMIT_FAILED,
                exception_type="RuntimeError",
                at_utc=NOW,
            )
            await store.record_failure_audit(record)
            rows = await _rows(store)
            assert len(rows) == 1
            request, task, event, reason, detail, timestamp = rows[0]
            assert (request, task, event, reason) == (
                request_id,
                "task-1",
                event_type,
                "STATE_COMMIT_FAILED",
            )
            assert json.loads(detail) == {
                "exception_type": "RuntimeError",
                "run_id": None,
                "epoch": None,
            }
            assert datetime.fromisoformat(timestamp) == NOW
        finally:
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["not-an-id", "예외", "x" * 65, ""])
def test_invalid_failure_audit_never_writes_and_has_fixed_reason(tmp_path, invalid):
    async def scenario():
        ids = count()
        store = SQLiteStore(tmp_path / "audit.db", audit_id_factory=lambda: f"id-{next(ids)}")
        try:
            forged = FailureAuditRecord.model_construct(
                task_id="task-1",
                request_id=None,
                phase="before_dispatch",
                reason=Reason.INTERNAL_ERROR,
                exception_type=invalid,
                at_utc=NOW,
                run_id=None,
                epoch=None,
            )
            with pytest.raises(StoreError) as error:
                await store.record_failure_audit(forged)
            assert error.value.reason is Reason.INVALID_ARGUMENTS
            assert await _rows(store) == []
        finally:
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("binding", [{"run_id": "untrusted"}, {"epoch": 1}])
def test_unbound_run_metadata_is_rejected_before_write(tmp_path, binding):
    async def scenario():
        ids = count()
        store = SQLiteStore(tmp_path / "audit.db", audit_id_factory=lambda: f"id-{next(ids)}")
        try:
            record = FailureAuditRecord(
                task_id="task-1",
                phase="before_dispatch",
                reason=Reason.INTERNAL_ERROR,
                exception_type="RuntimeError",
                at_utc=NOW,
                **binding,
            )
            with pytest.raises(StoreError) as error:
                await store.record_failure_audit(record)
            assert error.value.reason is Reason.INVALID_ARGUMENTS
            assert await _rows(store) == []
        finally:
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_failure_event_adapter_requires_trusted_utc_and_preserves_guard_supplied_time(tmp_path):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        ids = count()
        store = SQLiteStore(tmp_path / "audit.db", audit_id_factory=lambda: f"id-{next(ids)}")
        guard = None
        try:
            # Existing five-argument event remains constructible, but no UTC is guessed.
            old = FailureEvent(
                "task", None, "before_dispatch", Reason.INTERNAL_ERROR, "RuntimeError"
            )
            with pytest.raises(StoreError) as error:
                await durable_failure_audit(store)(old)
            assert error.value.reason is Reason.INVALID_ARGUMENTS
            await store.create_task(h.store.states["task"], created_at_utc=NOW)
            h.access.error = RuntimeError("PRIVATE_EXCEPTION_CONTENT")
            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: f"request-{next(h.ids)}",
                approval_id_factory=lambda: f"approval-{next(h.ids)}",
                next_call_seq=h.sequence,
                failure_audit=durable_failure_audit(store),
                utc_now=lambda: NOW,
                monotonic=lambda: h.monotonic,
            )
            assert isinstance(await guard.submit("task", _call()), Denied)
            rows = await _rows(store)
            assert len(rows) == 1
            assert datetime.fromisoformat(rows[0][5]) == NOW
            assert json.loads(rows[0][4]) == {
                "exception_type": "RuntimeError",
                "run_id": None,
                "epoch": None,
            }
            assert "PRIVATE_EXCEPTION_CONTENT" not in repr(rows)
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_guard_after_dispatch_failure_audit_uses_guard_utc_and_request_id(tmp_path):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        ids = count()
        store = SQLiteStore(tmp_path / "audit.db", audit_id_factory=lambda: f"id-{next(ids)}")
        guard = None
        try:
            await store.create_task(h.store.states["task"], created_at_utc=NOW)
            h.transport.error = RuntimeError("PRIVATE_TRANSPORT_CONTENT")
            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: f"request-{next(h.ids)}",
                approval_id_factory=lambda: f"approval-{next(h.ids)}",
                next_call_seq=h.sequence,
                failure_audit=durable_failure_audit(store),
                utc_now=lambda: NOW,
                monotonic=lambda: h.monotonic,
            )
            assert isinstance(await guard.submit("task", _call()), Withheld)
            rows = await _rows(store)
            assert len(rows) == 1
            request_id, task_id, event, reason, detail, timestamp = rows[0]
            assert (request_id, task_id, event, reason) == (
                "request-1",
                "task",
                "FAILURE_AFTER_DISPATCH",
                "POST_DISPATCH_ERROR",
            )
            assert datetime.fromisoformat(timestamp) == NOW
            assert json.loads(detail) == {
                "exception_type": "RuntimeError",
                "run_id": None,
                "epoch": None,
            }
            assert "PRIVATE_TRANSPORT_CONTENT" not in repr(rows)
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())
