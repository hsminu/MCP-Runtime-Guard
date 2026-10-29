"""B6-01: SQLite 승인 생성·종결 원자성 단위 검증 (I4/I6/I8, T11/T19).

SQL 오류 주입은 B 단위 검증이며 C의 강제 종료·독립 계수 인수를 대체하지 않는다.
"""

import asyncio
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from itertools import count

import pytest

from guard_app.domain.enums import (
    ApprovalStatus,
    CommitOutcome,
    Decision,
    RequestStatus,
    TaskStatus,
)
from guard_app.domain.errors import StoreError, StoreIntegrityError
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_sqlite_decisions import LATER, NOW, decision, record, task
from tests.unit.test_sqlite_lifecycle import approval
from tests.unit.test_store_recovery import RUN, _close
from tests.unit.test_store_recovery import _open as _prepare


def _snapshot(path, connect=sqlite3.connect):
    with connect(path) as db:
        return {
            table: db.execute(sql).fetchall()
            for table, sql in {
                "tasks": "SELECT * FROM tasks ORDER BY 1",
                "requests": "SELECT * FROM requests ORDER BY 1",
                "approvals": "SELECT * FROM approvals ORDER BY 1",
                "audit_events": "SELECT * FROM audit_events ORDER BY 1",
            }.items()
        }


@contextmanager
def _store(path, *, utc_now=lambda: LATER):
    ids = count(1)
    store = SQLiteStore(path, audit_id_factory=lambda: f"event-{next(ids)}", utc_now=utc_now)
    try:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        record(
            store, result=decision(decision=Decision.ASK, reasons=(Reason.INTERNAL_TO_EXTERNAL,))
        )
        yield store
    finally:
        store.close()
        asyncio.run(store.wait_closed(5))


def _open(store, expected=None):
    return asyncio.run(store.open_approval(approval(), expected_task=expected or task()))


def _finish(store, status=ApprovalStatus.REJECTED, **updates):
    args = {
        "expected_approval_revision": 0,
        "task_after": TaskStatus.RUNNING,
        "expected_task_revision": 12,
        "reason": Reason.OK,
        "now_utc": LATER,
    }
    args.update(updates)
    return asyncio.run(store.finish_approval("approval-1", status, **args))


async def _recover(path, folder):
    store = await _prepare(path, folder)
    try:
        report = await store.recover(deadline=time.monotonic() + 15, run_id=RUN)
        assert report.unresolved_count == 0
    finally:
        await _close(store)


def test_open_and_reject_commit_task_request_approval_and_audits(tmp_path):
    path = tmp_path / "atomic.db"
    with _store(path) as store:
        created = _open(store)
        assert created.approval == approval()
        assert created.task.status is TaskStatus.WAITING_APPROVAL
        assert created.task.row_revision == task().row_revision + 1
        assert created.task.security_version == task().security_version
        assert created.request_status is RequestStatus.WAITING_APPROVAL
        opened = _snapshot(path)
        assert [row[3] for row in opened["audit_events"]] == [
            "DECIDED",
            "TASK_WAITING_APPROVAL",
            "APPROVAL_PENDING",
        ]
        done = _finish(store)
        assert done.approval.status is ApprovalStatus.REJECTED
        assert done.approval.row_revision == 1
        assert done.task.status is TaskStatus.RUNNING
        assert done.task.row_revision == 13
        assert done.task.security_version == task().security_version
        assert done.request_status is RequestStatus.DENIED
        events = [row[3] for row in _snapshot(path)["audit_events"]]
        assert events[-2:] == ["APPROVAL_REJECTED", "TASK_RUNNING"] or events[-2:] == [
            "TASK_RUNNING",
            "APPROVAL_REJECTED",
        ]


@pytest.mark.parametrize("change", [{"row_revision": 10}, {"epoch": 4}])
def test_open_task_conflicts_leave_all_rows_unchanged(tmp_path, change):
    path = tmp_path / "conflict.db"
    with _store(path) as store:
        before = _snapshot(path)
        with pytest.raises(StoreError) as error:
            _open(store, task(**change))
        assert error.value.reason is Reason.APPROVAL_RACE
        assert _snapshot(path) == before


@pytest.mark.parametrize("field", ["expected_approval_revision", "expected_task_revision"])
def test_finish_stale_revision_changes_no_rows(tmp_path, field):
    path = tmp_path / "stale.db"
    with _store(path) as store:
        _open(store)
        before = _snapshot(path)
        with pytest.raises(StoreError) as error:
            _finish(store, **{field: 99})
        assert error.value.reason is Reason.APPROVAL_RACE
        assert _snapshot(path) == before


def test_finish_task_epoch_mismatch_is_a_conflict_without_further_writes(tmp_path):
    path = tmp_path / "epoch.db"
    with _store(path) as store:
        _open(store)
        store._run_on_writer(
            lambda db: db.execute("UPDATE tasks SET epoch=4 WHERE task_id='task-1'")
        )
        before = _snapshot(path)
        with pytest.raises(StoreError) as error:
            _finish(store)
        assert error.value.reason is Reason.APPROVAL_RACE
        assert _snapshot(path) == before


def test_competing_approvals_and_consumptions_cannot_reserve_twice(tmp_path):
    path = tmp_path / "race.db"
    with _store(path) as store:
        _open(store)

        async def scenario():
            async def approve():
                return await store.transition_approval(
                    "approval-1",
                    ApprovalStatus.APPROVED,
                    expected_row_revision=0,
                    approver_id="operator-1",
                    now_utc=LATER,
                    reason=Reason.OK,
                )

            outcomes = await asyncio.gather(approve(), approve(), return_exceptions=True)
            assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
            assert sum(isinstance(outcome, StoreError) for outcome in outcomes) == 1

            async def consume():
                return await store.consume_and_reserve(
                    approval_id="approval-1",
                    snapshot_hash="b" * 64,
                    request_id="request-1",
                    task_id="task-1",
                    expected_security_version=7,
                    now_utc=LATER,
                )

            assert sorted(await asyncio.gather(consume(), consume())) == [False, True]

        asyncio.run(scenario())
        after = _snapshot(path)
        assert after["approvals"][0][4] == ApprovalStatus.CONSUMED.value
        assert after["requests"][0][9] == RequestStatus.DISPATCHING.value
        assert [row[3] for row in after["audit_events"]].count("CONSUMED") == 1


def test_open_expired_approval_changes_no_rows(tmp_path):
    path = tmp_path / "expired.db"
    with _store(path, utc_now=lambda: NOW + timedelta(minutes=1)) as store:
        before = _snapshot(path)
        with pytest.raises(StoreError) as error:
            _open(store)
        assert error.value.reason is Reason.APPROVAL_STALE
        assert _snapshot(path) == before


def test_reject_expired_approval_changes_no_rows(tmp_path):
    path = tmp_path / "expired-reject.db"
    with _store(path) as store:
        _open(store)
        before = _snapshot(path)
        with pytest.raises(StoreError) as error:
            _finish(store, now_utc=NOW + timedelta(minutes=1))
        assert error.value.reason is Reason.APPROVAL_STALE
        assert _snapshot(path) == before


@pytest.mark.parametrize("status", [ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED])
def test_finish_pending_updates_all_rows_for_other_terminal_statuses(tmp_path, status):
    path = tmp_path / "terminal.db"
    with _store(path) as store:
        _open(store)
        result = _finish(store, status)
        assert result.approval.status is status
        assert result.approval.row_revision == 1
        assert result.task.status is TaskStatus.RUNNING
        assert result.request_status is RequestStatus.DENIED
        assert f"APPROVAL_{status.name}" in [row[3] for row in _snapshot(path)["audit_events"]]


def test_finish_approved_as_invalidated_halts_the_task(tmp_path):
    path = tmp_path / "invalidated.db"
    with _store(path) as store:
        _open(store)
        asyncio.run(
            store.transition_approval(
                "approval-1",
                ApprovalStatus.APPROVED,
                expected_row_revision=0,
                approver_id="operator-1",
                now_utc=LATER,
                reason=Reason.OK,
            )
        )
        result = _finish(
            store,
            ApprovalStatus.INVALIDATED,
            expected_approval_revision=1,
            task_after=TaskStatus.HALTED,
            halt_reason=Reason.APPROVAL_STALE,
        )
        assert result.approval.status is ApprovalStatus.INVALIDATED
        assert result.approval.row_revision == 2
        assert result.task.status is TaskStatus.HALTED
        assert result.task.security_version == task().security_version + 1
        assert result.request_status is RequestStatus.DENIED


@pytest.mark.parametrize("reason", [Reason.POLICY_CHANGED, Reason.INTERNAL_ERROR])
def test_pending_invalidation_is_limited_to_policy_changes(tmp_path, reason):
    path = tmp_path / "pending-invalidation.db"
    with _store(path) as store:
        _open(store)
        before = _snapshot(path)
        if reason is Reason.POLICY_CHANGED:
            result = _finish(
                store,
                ApprovalStatus.INVALIDATED,
                task_after=TaskStatus.HALTED,
                reason=reason,
                halt_reason=reason,
            )
            assert result.approval.status is ApprovalStatus.INVALIDATED
            assert result.task.status is TaskStatus.HALTED
        else:
            with pytest.raises(StoreError) as error:
                _finish(
                    store,
                    ApprovalStatus.INVALIDATED,
                    task_after=TaskStatus.HALTED,
                    reason=reason,
                    halt_reason=reason,
                )
            assert error.value.reason is Reason.APPROVAL_RACE
            assert _snapshot(path) == before


@pytest.mark.parametrize(
    "args",
    [
        {"task_after": TaskStatus.HALTED},
        {"task_after": TaskStatus.RUNNING, "halt_reason": Reason.INTERNAL_ERROR},
    ],
)
def test_finish_bad_halt_reason_is_rejected_without_writes(tmp_path, args):
    path = tmp_path / "bad-args.db"
    with _store(path) as store:
        _open(store)
        before = _snapshot(path)
        with pytest.raises(StoreError) as error:
            _finish(store, **args)
        assert error.value.reason is Reason.INVALID_ARGUMENTS
        assert _snapshot(path) == before


def test_finish_preserves_task_already_halted(tmp_path):
    path = tmp_path / "halted.db"
    with _store(path) as store:
        _open(store)
        halted = asyncio.run(
            store.set_task_status(
                "task-1",
                TaskStatus.HALTED,
                expected_row_revision=12,
                at_utc=LATER,
                reason=Reason.TASK_NOT_RUNNING,
            )
        )
        result = _finish(
            store,
            task_after=TaskStatus.HALTED,
            expected_task_revision=12,
            halt_reason=Reason.TASK_NOT_RUNNING,
        )
        assert result.task == halted
        assert result.approval.status is ApprovalStatus.REJECTED
        assert result.request_status is RequestStatus.DENIED


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE tasks SET status='halted', row_revision=row_revision+1 WHERE task_id='task-1'",
        "UPDATE tasks SET row_revision=row_revision+1 WHERE task_id='task-1'",
        "UPDATE tasks SET security_version=security_version+1 WHERE task_id='task-1'",
    ],
)
def test_external_task_tamper_halts_and_leaves_business_rows_unchanged(tmp_path, statement):
    path = tmp_path / "task-tamper.db"
    with _store(path) as store:
        _open(store)
        store._run_on_writer(lambda db: db.execute(statement))
        before = _snapshot(path)
        with pytest.raises(StoreIntegrityError) as error:
            _finish(store)
        assert error.value.reason is Reason.INTERNAL_ERROR
        assert not store.execution_state().execution_enabled
        after = _snapshot(path)
        assert all(after[key] == before[key] for key in ("tasks", "requests", "approvals"))
        assert [row[3] for row in after["audit_events"]].count("APPROVAL_INTEGRITY_FAILED") == 1


@pytest.mark.parametrize("operation", ["open", "finish"])
def test_external_request_legacy_marker_tamper_halts_before_transition(tmp_path, operation):
    path = tmp_path / "request-tamper.db"
    with _store(path) as store:
        if operation == "finish":
            _open(store)
        store._run_on_writer(
            lambda db: db.execute(
                "UPDATE requests SET legacy_source=? WHERE request_id=?", ("v1", "request-1")
            )
        )
        before = _snapshot(path)
        with pytest.raises(StoreIntegrityError) as error:
            (_open if operation == "open" else _finish)(store)
        assert error.value.reason is Reason.INTERNAL_ERROR
        assert not store.execution_state().execution_enabled
        after = _snapshot(path)
        assert all(after[key] == before[key] for key in ("tasks", "requests", "approvals"))
        assert [row[3] for row in after["audit_events"]].count("APPROVAL_INTEGRITY_FAILED") == 1


def test_external_approval_tamper_halts_and_audits_without_partial_transition(tmp_path):
    path = tmp_path / "tamper.db"
    with _store(path) as store:
        _open(store)
        store._run_on_writer(
            lambda db: db.execute(
                "UPDATE approvals SET snapshot_hash=? WHERE approval_id=?", ("c" * 64, "approval-1")
            )
        )
        before = _snapshot(path)
        with pytest.raises(StoreIntegrityError) as error:
            _finish(store)
        assert error.value.reason is Reason.INTERNAL_ERROR
        assert not store.execution_state().execution_enabled
        after = _snapshot(path)
        assert all(after[key] == before[key] for key in ("tasks", "requests", "approvals"))
        assert [row[3] for row in after["audit_events"]].count("APPROVAL_INTEGRITY_FAILED") == 1


def test_integrity_audit_failure_closes_connection_and_keeps_business_rows(tmp_path):
    path = tmp_path / "integrity-audit-failure.db"
    with _store(path) as store:
        _open(store)

        def inject(db):
            db.execute(
                "UPDATE approvals SET snapshot_hash=? WHERE approval_id=?", ("c" * 64, "approval-1")
            )
            db.execute(
                "CREATE TRIGGER fail_integrity_audit BEFORE INSERT ON audit_events "
                "WHEN NEW.event_type='APPROVAL_INTEGRITY_FAILED' "
                "BEGIN SELECT RAISE(ABORT, 'TEST_SECRET'); END"
            )

        store._run_on_writer(inject)
        before = _snapshot(path)
        with pytest.raises(StoreIntegrityError) as error:
            _finish(store)
        assert error.value.reason is Reason.INTERNAL_ERROR
        assert error.value.args == (Reason.INTERNAL_ERROR.value,)
        assert "TEST_SECRET" not in repr(error.value)
        state = store.execution_state()
        assert not state.execution_enabled
        assert state.requires_restart
        assert state.commit_outcome is CommitOutcome.ROLLED_BACK
        assert store._connection is None
        with pytest.raises(StoreError):
            asyncio.run(store.load_task("task-1"))
        assert _snapshot(path) == before


@pytest.mark.parametrize("operation", ["open", "finish"])
@pytest.mark.parametrize("point", ["BEGIN", "TASKS", "APPROVALS", "REQUESTS", "AUDIT", "COMMIT"])
def test_failure_at_transaction_boundaries_leaves_no_partial_rows(
    tmp_path, monkeypatch, operation, point
):
    path = tmp_path / "rollback.db"
    original_connect = sqlite3.connect
    armed = threading.Event()
    marker = {
        "BEGIN": "BEGIN IMMEDIATE",
        "TASKS": "UPDATE TASKS",
        "APPROVALS": "INSERT INTO APPROVALS" if operation == "open" else "UPDATE APPROVALS",
        "REQUESTS": "UPDATE REQUESTS",
        "AUDIT": "INSERT INTO AUDIT_EVENTS",
        "COMMIT": "COMMIT",
    }[point]

    class FaultConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            normalized = " ".join(sql.upper().split())
            if armed.is_set() and normalized.startswith(marker):
                armed.clear()
                raise sqlite3.OperationalError("injected failure")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: original_connect(*a, **kw, factory=FaultConnection)
    )
    with _store(path) as store:
        if operation == "finish":
            _open(store)
        before = _snapshot(path, original_connect)
        armed.set()
        with pytest.raises(StoreError):
            _open(store) if operation == "open" else _finish(store)
        assert not armed.is_set(), f"failure point was not reached: {operation}/{point}"
        assert _snapshot(path, original_connect) == before
        if point == "COMMIT":
            assert store.execution_state().commit_outcome is CommitOutcome.ROLLED_BACK
    asyncio.run(_recover(path, tmp_path))
    after = _snapshot(path, original_connect)
    assert after["tasks"][0][7] == TaskStatus.HALTED.value
    assert after["requests"][0][9] == RequestStatus.DENIED.value
    if operation == "finish":
        assert after["approvals"][0][4] == ApprovalStatus.EXPIRED.value
    else:
        assert after["approvals"] == []


@pytest.mark.parametrize("operation", ["open", "finish"])
def test_lost_commit_ack_faults_without_partial_commit_and_recovers(
    tmp_path, monkeypatch, operation
):
    path = tmp_path / "unknown.db"
    original_connect = sqlite3.connect
    armed = threading.Event()

    class LostAckConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            result = super().execute(sql, parameters)
            if sql == "COMMIT" and armed.is_set():
                armed.clear()
                raise sqlite3.OperationalError("injected lost acknowledgement")
            return result

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: original_connect(*a, **kw, factory=LostAckConnection)
    )
    with _store(path) as store:
        if operation == "finish":
            _open(store)
        armed.set()
        with pytest.raises(StoreError):
            _open(store) if operation == "open" else _finish(store)
        assert not armed.is_set()
        assert store.execution_state().commit_outcome is CommitOutcome.UNKNOWN
        assert not store.execution_state().execution_enabled
        saved = _snapshot(path, original_connect)
        if operation == "open":
            assert saved["tasks"][0][7] == TaskStatus.WAITING_APPROVAL.value
            assert saved["requests"][0][9] == RequestStatus.WAITING_APPROVAL.value
            assert saved["approvals"][0][4] == ApprovalStatus.PENDING.value
        else:
            assert saved["tasks"][0][7] == TaskStatus.RUNNING.value
            assert saved["requests"][0][9] == RequestStatus.DENIED.value
            assert saved["approvals"][0][4] == ApprovalStatus.REJECTED.value
    asyncio.run(_recover(path, tmp_path))
    recovered = _snapshot(path, original_connect)
    assert recovered["tasks"][0][7] == TaskStatus.HALTED.value
    assert recovered["requests"][0][9] == RequestStatus.DENIED.value
    expected = ApprovalStatus.EXPIRED if operation == "open" else ApprovalStatus.REJECTED
    assert recovered["approvals"][0][4] == expected.value
