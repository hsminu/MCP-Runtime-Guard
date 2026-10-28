"""REC-01/03, SEQ-02: recovered gate, run ownership and activation races."""

import asyncio
import json
import sqlite3
import threading
import time

import pytest

from guard_app.domain.contracts import Released, TaskIdentity
from guard_app.domain.enums import (
    ApprovalStatus,
    Decision,
    Label,
    SessionEvent,
    StorePhase,
    TaskStatus,
)
from guard_app.domain.errors import StoreError
from guard_app.domain.models import FailureAuditRecord
from guard_app.domain.reasons import Reason
from guard_app.security.digest import json_digest
from guard_app.services.guard import GuardService, durable_failure_audit
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_registry_store import FP, registration, report
from tests.unit.test_sqlite_decisions import LATER, NOW, decision, request, task
from tests.unit.test_sqlite_lifecycle import approval
from tests.unit.test_store_recovery import RUN, RUN2, _close, _open, _rows


async def _recovered(path, folder, run_id=RUN):
    store = await _open(path, folder)
    await store.recover(deadline=time.monotonic() + 10, run_id=run_id)
    return store


async def _new_task(store, task_id="task-1"):
    state = task(
        task_id=task_id, epoch=1, status=TaskStatus.READY, row_revision=0, security_version=0
    )
    await store.create_task(state, created_at_utc=NOW)
    return await store.set_task_status(
        task_id, TaskStatus.RUNNING, expected_row_revision=0, at_utc=NOW, reason=Reason.OK
    )


async def _record(store, *, ask=False):
    req = request(epoch=1)
    await store.record_decision(
        req,
        decision(
            decision=Decision.ASK if ask else Decision.ALLOW,
            reasons=(Reason.INTERNAL_TO_EXTERNAL,) if ask else (Reason.OK,),
            security_version=0,
        ),
        arguments_digest=json_digest(req.arguments),
        at_utc=NOW,
    )
    return req


def test_recovered_gates_and_activation_state(tmp_path):
    async def scenario():
        store = await _recovered(tmp_path / "gate.db", tmp_path)
        try:
            before = store.execution_state()
            assert before.phase is StorePhase.RECOVERED
            assert before.run_id == RUN
            assert not before.execution_enabled
            assert before.last_committed_run_state == "RECOVERY_COMMITTED"
            state = await _new_task(store)
            assert (await store.load_task(state.task_id)).status is TaskStatus.RUNNING
            assert await _rows(store, "SELECT task_id,run_id FROM task_run_owners") == [
                ("task-1", RUN)
            ]
            entry = await store.register_server(registration(), at_utc=NOW)
            await store.record_scan_report("scan-1", report(), at_utc=NOW)
            assert await store.load_registration("server-1") is not None
            await store.record_session_event(
                TaskIdentity(task_id="task-1", epoch=1), SessionEvent.REQUEST_USER_INPUT, at_utc=NOW
            )
            await store.record_failure_audit(
                FailureAuditRecord(
                    task_id="task-1",
                    phase="before_dispatch",
                    reason=Reason.INTERNAL_ERROR,
                    exception_type="RuntimeError",
                    at_utc=NOW,
                )
            )
            req = request(epoch=1)
            calls = (
                lambda: store.allocate_call_seq("task-1"),
                lambda: store.record_decision(
                    req, decision(), arguments_digest=json_digest(req.arguments), at_utc=NOW
                ),
                lambda: store.reserve_dispatch("request-1", now_utc=NOW),
                lambda: store.create_approval(approval()),
                lambda: store.transition_approval(
                    "approval-1",
                    ApprovalStatus.APPROVED,
                    expected_row_revision=0,
                    approver_id="operator-1",
                    now_utc=NOW,
                    reason=Reason.OK,
                ),
                lambda: store.consume_and_reserve(
                    approval_id="approval-1",
                    snapshot_hash="b" * 64,
                    request_id="request-1",
                    task_id="task-1",
                    expected_security_version=0,
                    now_utc=NOW,
                ),
                lambda: store.activate_server(
                    "server-1",
                    expected_row_revision=entry.registration.row_revision + 1,
                    tool_fingerprints={"read": FP},
                    reviewed_by="operator",
                    at_utc=NOW,
                ),
            )
            for operation in calls:
                with pytest.raises(StoreError):
                    await operation()
            active = await store.activate_run()
            assert active.phase is StorePhase.READY
            assert active.execution_enabled
            assert active.run_id == RUN
            assert active.last_committed_run_state == "ACTIVE"
            assert active.state_revision > before.state_revision
            assert await _rows(store, "SELECT state FROM store_runs") == [("ACTIVE",)]
            assert await store.allocate_call_seq("task-1") == 1
            current = await store.load_registration("server-1")
            assert current is not None
            activated = await store.activate_server(
                "server-1",
                expected_row_revision=current.registration.row_revision,
                tool_fingerprints={"read": FP},
                reviewed_by="operator",
                at_utc=NOW,
            )
            assert activated.registration.status.value == "active"
            with pytest.raises(StoreError):
                await store.activate_run()
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_explicit_task_requires_new_epoch_one_and_old_run_cannot_allocate(tmp_path):
    path = tmp_path / "ownership.db"

    async def scenario():
        first = await _recovered(path, tmp_path)
        try:
            for epoch in (0, 2):
                with pytest.raises(StoreError):
                    await first.create_task(
                        task(task_id=f"wrong-{epoch}", epoch=epoch), created_at_utc=NOW
                    )
            await _new_task(first, "old")
            await first.activate_run()
        finally:
            await _close(first)
        second = await _recovered(path, tmp_path, RUN2)
        try:
            await _new_task(second, "new")
            await second.activate_run()
            old = await second.load_task("old")
            with pytest.raises(StoreError) as old_control:
                await second.set_task_status(
                    "old",
                    TaskStatus.CLOSED,
                    expected_row_revision=old.row_revision,
                    at_utc=NOW,
                    reason=Reason.OK,
                )
            assert old_control.value.reason is Reason.TASK_NOT_RUNNING
            # Make the former Task appear runnable to isolate ownership from status checks.
            await second._run_on_writer_async(
                lambda connection: connection.execute(
                    "UPDATE tasks SET status='running' WHERE task_id='old'"
                ),
                write=True,
            )
            with pytest.raises(StoreError) as error:
                await second.allocate_call_seq("old")
            assert error.value.reason is Reason.TASK_NOT_RUNNING
            assert await second.allocate_call_seq("new") == 1
            rows = await _rows(
                second, "SELECT detail FROM audit_events WHERE event_type='CALL_SEQ_ALLOCATED'"
            )
            assert len(rows) == 1
            assert json.loads(rows[0][0])["run_id"] == RUN2
            await second.record_failure_audit(
                FailureAuditRecord(
                    task_id="new",
                    phase="before_dispatch",
                    reason=Reason.INTERNAL_ERROR,
                    exception_type="RuntimeError",
                    at_utc=NOW,
                )
            )
            details = await _rows(
                second, "SELECT detail FROM audit_events WHERE event_type='FAILURE_BEFORE_DISPATCH'"
            )
            assert json.loads(details[0][0])["run_id"] == RUN2
        finally:
            await _close(second)

    asyncio.run(scenario())


def test_actual_guard_read_after_recovery_and_activation(tmp_path):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        store = await _recovered(tmp_path / "guard.db", tmp_path)
        guard = None
        try:
            await store.create_task(h.store.states["task"], created_at_utc=h.now)
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
                next_call_seq=store.allocate_call_seq,
                failure_audit=durable_failure_audit(store),
                on_global_halt=store.halt_execution,
                utc_now=lambda: h.now,
                monotonic=lambda: h.monotonic,
            )
            await store.activate_run()
            result = await guard.submit("task", _call())
            assert isinstance(result, Released)
            assert len(h.transport.calls) == 1
            assert await _rows(store, "SELECT call_seq,epoch,kind,legacy_source FROM requests") == [
                (1, 1, "read", None)
            ]
        finally:
            if guard is not None:
                await guard.aclose()
            await _close(store)

    asyncio.run(scenario())


def test_concurrent_activation_acknowledges_only_once(tmp_path):
    async def scenario():
        store = await _recovered(tmp_path / "concurrent.db", tmp_path)
        try:
            values = await asyncio.gather(
                store.activate_run(), store.activate_run(), return_exceptions=True
            )
            assert sum(isinstance(value, StoreError) for value in values) == 1
            assert sum(not isinstance(value, BaseException) for value in values) == 1
            assert store.execution_state().phase is StorePhase.READY
            assert await _rows(store, "SELECT state FROM store_runs") == [("ACTIVE",)]
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["allocate", "reserve", "consume"])
def test_run_binding_is_rechecked_in_same_transaction(tmp_path, operation):
    async def scenario():
        store = await _recovered(tmp_path / "binding.db", tmp_path)
        try:
            store._utc_now = lambda: LATER
            await _new_task(store)
            await store.activate_run()
            if operation in {"reserve", "consume"}:
                await _record(store, ask=operation == "consume")
            if operation == "consume":
                await store.set_task_status(
                    "task-1",
                    TaskStatus.WAITING_APPROVAL,
                    expected_row_revision=1,
                    at_utc=NOW,
                    reason=Reason.OK,
                )
                await store.create_approval(approval())
                await store.transition_approval(
                    "approval-1",
                    ApprovalStatus.APPROVED,
                    expected_row_revision=0,
                    approver_id="operator-1",
                    now_utc=LATER,
                    reason=Reason.OK,
                )

            def change_owner(connection):
                connection.execute(
                    "INSERT INTO store_runs VALUES (?,'CLOSED',?,?,NULL,?)",
                    (
                        RUN2,
                        NOW.isoformat(timespec="microseconds"),
                        NOW.isoformat(timespec="microseconds"),
                        NOW.isoformat(timespec="microseconds"),
                    ),
                )
                connection.execute(
                    "UPDATE task_run_owners SET run_id=? WHERE task_id='task-1'", (RUN2,)
                )

            await store._run_on_writer_async(change_owner, write=True)
            before = await _rows(store, "SELECT * FROM requests ORDER BY 1")
            approvals = await _rows(store, "SELECT * FROM approvals ORDER BY 1")
            audits = await _rows(store, "SELECT * FROM audit_events ORDER BY 1")
            if operation == "allocate":
                call = store.allocate_call_seq("task-1")
            elif operation == "reserve":
                call = store.reserve_dispatch("request-1", now_utc=LATER)
            else:
                call = store.consume_and_reserve(
                    approval_id="approval-1",
                    snapshot_hash="b" * 64,
                    request_id="request-1",
                    task_id="task-1",
                    expected_security_version=0,
                    now_utc=LATER,
                )
            with pytest.raises(StoreError) as error:
                await call
            assert error.value.reason is Reason.TASK_NOT_RUNNING
            assert await _rows(store, "SELECT * FROM requests ORDER BY 1") == before
            assert await _rows(store, "SELECT * FROM approvals ORDER BY 1") == approvals
            assert await _rows(store, "SELECT * FROM audit_events ORDER BY 1") == audits
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_activation_rejects_migrating_and_recovering_phases(tmp_path):
    async def scenario():
        path = tmp_path / "before-recovery.db"
        store = SQLiteStore(path, startup="explicit")
        try:
            with pytest.raises(StoreError):
                await store.activate_run()
            from tests.unit.test_store_prepare_schema import _Backups

            await store.prepare_schema(
                deadline=time.monotonic() + 10, begin_backup=_Backups(tmp_path, path).begin
            )
            assert store.execution_state().phase is StorePhase.RECOVERING
            with pytest.raises(StoreError):
                await store.activate_run()
            assert not store.execution_state().execution_enabled
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_activation_commit_ack_loss_faults_and_does_not_open_gate(tmp_path, monkeypatch):
    original = sqlite3.connect
    armed = threading.Event()

    class LostAckConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            result = super().execute(statement, parameters)
            if statement == "COMMIT" and armed.is_set():
                armed.clear()
                raise sqlite3.OperationalError("PRIVATE_ACK_CONTENT")
            return result

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original(*args, **kwargs, factory=LostAckConnection),
    )

    async def scenario():
        store = await _recovered(tmp_path / "lost-ack.db", tmp_path)
        try:
            armed.set()
            with pytest.raises(StoreError) as error:
                await store.activate_run()
            assert "PRIVATE_ACK_CONTENT" not in str(error.value)
            state = store.execution_state()
            assert state.phase is StorePhase.FAULTED
            assert not state.execution_enabled
            assert state.requires_restart
            with pytest.raises(StoreError):
                await store.record_failure_audit(
                    FailureAuditRecord(
                        task_id="task-1",
                        phase="before_dispatch",
                        reason=Reason.INTERNAL_ERROR,
                        exception_type="RuntimeError",
                        at_utc=NOW,
                    )
                )
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize("interrupt", ["halt", "close", "cancel"])
def test_activation_commit_blocked_interruption_never_reopens_gate(
    tmp_path, monkeypatch, interrupt
):
    original = sqlite3.connect
    armed, entered, release = threading.Event(), threading.Event(), threading.Event()

    class BlockedConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if statement == "COMMIT" and armed.is_set():
                armed.clear()
                entered.set()
                if not release.wait(5):
                    raise sqlite3.OperationalError("timeout")
            return super().execute(statement, parameters)

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original(*args, **kwargs, factory=BlockedConnection),
    )

    async def scenario():
        store = await _recovered(tmp_path / "blocked.db", tmp_path)
        activation = None
        try:
            armed.set()
            activation = asyncio.create_task(store.activate_run())
            assert await asyncio.to_thread(entered.wait, 5)
            if interrupt == "halt":
                store.halt_execution(Reason.INTERNAL_ERROR)
            elif interrupt == "close":
                store.close()
            else:
                activation.cancel()
                await asyncio.sleep(0)
                assert not activation.done()
            release.set()
            with pytest.raises((StoreError, asyncio.CancelledError)):
                await activation
            assert not store.execution_state().execution_enabled
            assert store.execution_state().phase in (StorePhase.FAULTED, StorePhase.CLOSED)
        finally:
            release.set()
            if activation is not None and not activation.done():
                await asyncio.gather(activation, return_exceptions=True)
            await _close(store)

    asyncio.run(scenario())


def test_cancelled_queued_activation_never_commits_or_opens_gate(tmp_path):
    async def scenario():
        store = await _recovered(tmp_path / "queued.db", tmp_path)
        entered, release = threading.Event(), threading.Event()
        blocker = None
        try:

            def hold_writer(_connection):
                entered.set()
                assert release.wait(5)

            blocker = asyncio.create_task(store._run_on_writer_async(hold_writer))
            assert await asyncio.to_thread(entered.wait, 5)
            activation = asyncio.create_task(store.activate_run())
            await asyncio.sleep(0)
            activation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await activation
            release.set()
            await blocker
            assert not store.execution_state().execution_enabled
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _rows(store, "SELECT state FROM store_runs") == [("RECOVERY_COMMITTED",)]
        finally:
            release.set()
            if blocker is not None and not blocker.done():
                await asyncio.gather(blocker, return_exceptions=True)
            await _close(store)

    asyncio.run(scenario())


def test_global_halt_keeps_failure_audit_and_cleanup_available(tmp_path):
    async def scenario():
        store = await _recovered(tmp_path / "halt.db", tmp_path)
        try:
            await _new_task(store)
            await store.activate_run()
            store.halt_execution(Reason.INTERNAL_ERROR)
            await store.record_failure_audit(
                FailureAuditRecord(
                    task_id="task-1",
                    phase="before_dispatch",
                    reason=Reason.INTERNAL_ERROR,
                    exception_type="RuntimeError",
                    at_utc=NOW,
                )
            )
            details = await _rows(
                store, "SELECT detail FROM audit_events WHERE event_type='FAILURE_BEFORE_DISPATCH'"
            )
            assert json.loads(details[0][0])["run_id"] == RUN
            result = await store.set_task_status(
                "task-1",
                TaskStatus.HALTED,
                expected_row_revision=1,
                at_utc=NOW,
                reason=Reason.TASK_NOT_RUNNING,
            )
            assert result.status is TaskStatus.HALTED
            with pytest.raises(StoreError):
                await store.allocate_call_seq("task-1")
            assert not store.execution_state().execution_enabled
        finally:
            await _close(store)

    asyncio.run(scenario())
