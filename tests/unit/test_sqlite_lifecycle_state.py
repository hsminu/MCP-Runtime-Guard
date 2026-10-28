"""B6.13 실행 권한과 연결 고장의 Store 단위 검증(SEQ-02, I8/I9)."""

import asyncio
import sqlite3
import subprocess
import sys
import threading
import time
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.enums import ApprovalStatus, CommitOutcome, StorePhase, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.security.digest import json_digest
from guard_app.storage import SQLiteStore
from tests.unit.test_sqlite_decisions import LATER, NOW, decision, record, request, reserve, task
from tests.unit.test_sqlite_lifecycle import approval, failure, pending, transition


def _ids():
    identifiers = count(1)
    return lambda: f"event-{next(identifiers)}"


def test_guard_halt_blocks_every_new_execution_gate_but_preserves_reads_and_cleanup(tmp_path):
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=_ids()) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        req = request()
        store.halt_execution(Reason.TASK_NOT_RUNNING)
        snapshot = store.execution_state()
        assert snapshot.phase is StorePhase.FAULTED
        assert snapshot.execution_enabled is False
        assert snapshot.requires_restart is True
        assert snapshot.reason is Reason.TASK_NOT_RUNNING
        assert snapshot.commit_outcome is None

        async def check_gates():
            operations = (
                lambda: store.create_task(task(task_id="new"), created_at_utc=NOW),
                lambda: store.record_decision(
                    req,
                    decision(),
                    arguments_digest=json_digest(req.arguments.to_dict()),
                    at_utc=NOW,
                ),
                lambda: store.reserve_dispatch(req.request_id, now_utc=LATER),
                lambda: store.create_approval(approval()),
                lambda: store.consume_and_reserve(
                    approval_id="approval-1",
                    snapshot_hash="b" * 64,
                    request_id=req.request_id,
                    task_id=req.task_id,
                    expected_security_version=7,
                    now_utc=LATER,
                ),
                lambda: store.transition_approval(
                    "approval-1",
                    ApprovalStatus.APPROVED,
                    expected_row_revision=0,
                    approver_id="operator-1",
                    now_utc=LATER,
                    reason=Reason.OK,
                ),
                lambda: store.activate_server(
                    "server-1",
                    expected_row_revision=0,
                    tool_fingerprints={"read": "a" * 64},
                    reviewed_by="operator-1",
                    at_utc=LATER,
                ),
            )
            for operation in operations:
                with pytest.raises(StoreError) as error:
                    await operation()
                assert error.value.reason is Reason.TASK_NOT_RUNNING
            current = await store.load_task("task-1")
            stopped = await store.set_task_status(
                current.task_id,
                TaskStatus.HALTED,
                expected_row_revision=current.row_revision,
                at_utc=LATER,
                reason=Reason.TASK_NOT_RUNNING,
            )
            assert stopped.status is TaskStatus.HALTED

        asyncio.run(check_gates())
        store.halt_execution(Reason.INTERNAL_ERROR)
        assert store.execution_state() == snapshot


def test_cleanup_after_global_halt_can_finish_a_previously_reserved_request(tmp_path):
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=_ids()) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        req = request()
        record(store, req)
        reserve(store)
        store.halt_execution(Reason.STATE_COMMIT_FAILED)
        assert failure(store, req).status is TaskStatus.HALTED


def test_pending_approval_can_be_rejected_after_global_halt(tmp_path):
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=_ids(), utc_now=lambda: LATER
    ) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        pending(store)
        store.halt_execution(Reason.STATE_COMMIT_FAILED)
        assert transition(store, ApprovalStatus.REJECTED).status is ApprovalStatus.REJECTED


@pytest.mark.parametrize("faulted", [False, True])
def test_close_updates_ready_once_and_preserves_existing_fault(tmp_path, faulted):
    store = SQLiteStore(tmp_path / "guard.db", audit_id_factory=_ids())
    initial = store.execution_state()
    if faulted:
        store.halt_execution(Reason.TASK_NOT_RUNNING)
    before_close = store.execution_state()
    store.close()
    store.close()
    after_close = store.execution_state()
    assert not after_close.execution_enabled
    if faulted:
        assert after_close == before_close
    else:
        assert after_close.phase is StorePhase.CLOSED
        assert after_close.state_revision == initial.state_revision + 1
    assert asyncio.run(store.wait_closed(5.0))
    assert store.execution_state() == after_close


class CommitFaultConnection(sqlite3.Connection):
    fault = ""
    observed = None

    def execute(self, sql, parameters=(), /):
        if self.observed is not None:
            self.observed.append(sql)
        if self.fault in ("before_commit", "commit_and_rollback") and sql == "COMMIT":
            raise sqlite3.OperationalError("TEST_SECRET_before_commit")
        if self.fault == "rollback" and sql.startswith("INSERT INTO tasks"):
            raise sqlite3.OperationalError("TEST_SECRET_before_rollback")
        if self.fault in ("rollback", "commit_and_rollback") and sql == "ROLLBACK":
            raise sqlite3.OperationalError("TEST_SECRET_rollback")
        result = super().execute(sql, parameters)
        if self.fault == "after_commit" and sql == "COMMIT":
            raise sqlite3.OperationalError("TEST_SECRET_after_commit")
        return result


@pytest.mark.parametrize(
    ("fault", "outcome"),
    [
        ("before_commit", CommitOutcome.ROLLED_BACK),
        ("after_commit", CommitOutcome.UNKNOWN),
        ("rollback", CommitOutcome.ROLLED_BACK),
        ("commit_and_rollback", CommitOutcome.UNKNOWN),
    ],
)
def test_fault_discards_connection_closes_gate_and_reports_exact_commit_outcome(
    tmp_path, monkeypatch, fault, outcome
):
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=CommitFaultConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    calls = []
    with SQLiteStore(tmp_path / "TEST_SECRET_path.db", audit_id_factory=_ids()) as store:
        initial = store.execution_state()
        assert initial.phase is StorePhase.READY
        assert initial.execution_enabled is True
        assert initial.run_id is None

        def arm(connection):
            assert isinstance(connection, CommitFaultConnection)
            connection.observed = calls
            connection.fault = fault

        store._run_on_writer(arm)
        with pytest.raises(StoreError) as error:
            asyncio.run(store.create_task(task(), created_at_utc=NOW))
        assert error.value.reason is Reason.INTERNAL_ERROR
        state = store.execution_state()
        assert state.phase is StorePhase.FAULTED
        assert state.execution_enabled is False
        assert state.requires_restart is True
        assert state.commit_outcome is outcome
        assert state.state_revision == initial.state_revision + 1
        calls_before_retry = len(calls)
        with pytest.raises(StoreError) as retry_error:
            asyncio.run(store.load_task("task-1"))
        assert retry_error.value.reason is Reason.INTERNAL_ERROR
        assert len(calls) == calls_before_retry
        store.halt_execution(Reason.INTERNAL_ERROR)
        assert store.execution_state() == state
        assert "TEST_SECRET" not in repr(state) + repr(error.value) + repr(retry_error.value)


def test_discard_close_failure_remains_unconfirmed_after_writer_shutdown(tmp_path, monkeypatch):
    original_connect = sqlite3.connect

    class CloseFailureConnection(CommitFaultConnection):
        fail_close = False

        def close(self):
            super().close()
            if self.fail_close:
                raise sqlite3.OperationalError("TEST_SECRET_close")

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=CloseFailureConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    store = SQLiteStore(tmp_path / "close-fault.db", audit_id_factory=_ids())

    def arm(connection):
        assert isinstance(connection, CloseFailureConnection)
        connection.fault = "after_commit"
        connection.fail_close = True

    store._run_on_writer(arm)
    with pytest.raises(StoreError):
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
    assert store.execution_state().phase is StorePhase.FAULTED
    store.close()
    assert not asyncio.run(store.wait_closed(5.0))


def test_halt_applies_to_queued_but_not_yet_started_write(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "guard.db", audit_id_factory=_ids())
        entered, release = threading.Event(), threading.Event()

        def hold(connection):
            entered.set()
            assert release.wait(5)

        running = asyncio.create_task(store._run_on_writer_async(hold))
        try:
            await _wait_for_thread_event(entered)
            queued = asyncio.create_task(store.create_task(task(), created_at_utc=NOW))
            await _wait_for_queued_jobs(store, 1)
            store.halt_execution(Reason.TASK_NOT_RUNNING)
            release.set()
            await running
            with pytest.raises(StoreError) as error:
                await queued
            assert error.value.reason is Reason.TASK_NOT_RUNNING
            assert await store.list_tasks() == ()
        finally:
            release.set()
            store.close()
            assert await store.wait_closed(5.0)

    asyncio.run(scenario())


async def _wait_for_thread_event(event):
    async with asyncio.timeout(5):
        while not event.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.005)


async def _wait_for_queued_jobs(store, count):
    async with asyncio.timeout(5):
        while True:
            with store._writer._condition:
                if len(store._writer._jobs) == count:
                    return
            await asyncio.sleep(0.005)


def test_full_queue_rejects_without_running_or_changing_authority(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "queue.db", max_pending_jobs=1, audit_id_factory=_ids())
        entered, release = threading.Event(), threading.Event()

        def hold(_connection):
            entered.set()
            assert release.wait(5)

        running = asyncio.create_task(store._run_on_writer_async(hold))
        queued = None
        try:
            await _wait_for_thread_event(entered)
            queued = asyncio.create_task(store.create_task(task(), created_at_utc=NOW))
            await _wait_for_queued_jobs(store, 1)
            before = store.execution_state()
            with pytest.raises(StoreError) as error:
                await store.create_task(task(task_id="overflow"), created_at_utc=NOW)
            assert error.value.reason is Reason.INTERNAL_ERROR
            assert store.execution_state() == before
            release.set()
            await running
            await queued
            assert [item.task_id for item in await store.list_tasks()] == ["task-1"]
        finally:
            release.set()
            await asyncio.gather(running, *([queued] if queued else []), return_exceptions=True)
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_close_from_other_thread_rejects_pending_and_allows_started_to_finish(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "close.db", audit_id_factory=_ids())
        entered, release = threading.Event(), threading.Event()
        close_errors = []

        def hold(connection):
            entered.set()
            assert release.wait(5)
            return connection.execute("SELECT 1").fetchone()[0]

        running = asyncio.create_task(store._run_on_writer_async(hold))
        queued = None
        try:
            await _wait_for_thread_event(entered)
            queued = asyncio.create_task(store.create_task(task(), created_at_utc=NOW))
            await _wait_for_queued_jobs(store, 1)

            def close():
                try:
                    store.close()
                    store.close()
                except Exception as error:  # noqa: BLE001 - test observes the no-throw contract
                    close_errors.append(error)

            thread = threading.Thread(target=close)
            thread.start()
            thread.join(timeout=0.5)
            assert not thread.is_alive()
            assert not close_errors
            with pytest.raises(StoreError):
                await queued
            with pytest.raises(StoreError):
                await store.create_task(task(task_id="closed"), created_at_utc=NOW)
            assert not await store.wait_closed(0.2)
            release.set()
            assert await running == 1
            assert await store.wait_closed(5)
        finally:
            release.set()
            await asyncio.gather(running, *([queued] if queued else []), return_exceptions=True)
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_close_after_writer_dequeue_keeps_started_store_write(tmp_path, monkeypatch):
    async def scenario():
        store = SQLiteStore(tmp_path / "dequeued.db", audit_id_factory=_ids())
        entered, release = threading.Event(), threading.Event()
        execute = store._writer._execute

        def hold_before_gate(operation, *, test_raw_exceptions=False):
            entered.set()
            assert release.wait(5)
            return execute(operation, test_raw_exceptions=test_raw_exceptions)

        monkeypatch.setattr(store._writer, "_execute", hold_before_gate)
        work = asyncio.create_task(store.create_task(task(), created_at_utc=NOW))
        try:
            await _wait_for_thread_event(entered)
            # _execute runs only after the writer dequeues the job and marks it started.
            assert not work.done()
            store.close()
            release.set()
            await work
            assert await store.wait_closed(5)
            with sqlite3.connect(tmp_path / "dequeued.db") as connection:
                assert connection.execute("SELECT task_id FROM tasks").fetchall() == [("task-1",)]
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_loop_state_and_close_are_responsive_during_sqlite_write_lock(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    begin_entered = threading.Event()
    monitor = threading.Event()

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if monitor.is_set() and sql == "BEGIN IMMEDIATE":
                begin_entered.set()
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=ObservedConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)

    async def scenario():
        path = tmp_path / "locked.db"
        store = SQLiteStore(path, audit_id_factory=_ids(), busy_timeout_ms=3000)
        lock = original_connect(path, isolation_level=None)
        work = None
        try:
            lock.execute("BEGIN IMMEDIATE")
            monitor.set()
            work = asyncio.create_task(store.create_task(task(), created_at_utc=NOW))
            await _wait_for_thread_event(begin_entered)
            ticks = [time.perf_counter()]
            deadline = ticks[0] + 0.8
            while time.perf_counter() < deadline:
                await asyncio.sleep(0.01)
                ticks.append(time.perf_counter())
            max_gap = max(b - a for a, b in zip(ticks, ticks[1:], strict=False))
            start = time.perf_counter()
            snapshot = store.execution_state()
            state_s = time.perf_counter() - start
            start = time.perf_counter()
            store.close()
            close_s = time.perf_counter() - start
            assert snapshot.phase is StorePhase.READY
            assert max_gap < 0.25
            assert state_s < 0.1
            assert close_s < 0.1
            assert not await store.wait_closed(0.2)
            lock.execute("ROLLBACK")
            await work
            assert await store.wait_closed(5)
            print(
                f"writer responsiveness: heartbeat={max_gap:.6f}s "
                f"state={state_s:.6f}s close={close_s:.6f}s"
            )
        finally:
            if lock.in_transaction:
                lock.execute("ROLLBACK")
            lock.close()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def test_blocked_daemon_writer_does_not_prevent_interpreter_exit(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src"
    script = """
import asyncio
import sys
import threading
sys.path.insert(0, sys.argv[1])
from guard_app.storage import SQLiteStore

async def run():
    store = SQLiteStore(sys.argv[2])
    entered = threading.Event()
    blocked = threading.Event()
    def hold(_connection):
        entered.set()
        blocked.wait()
    asyncio.create_task(store._run_on_writer_async(hold))
    while not entered.is_set():
        await asyncio.sleep(0.01)
    print('writer-blocked', flush=True)

asyncio.run(run())
"""
    result = subprocess.run(  # noqa: S603 - sys.executable and a fixed local test script
        [sys.executable, "-I", "-c", script, str(source), str(tmp_path / "daemon.db")],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "writer-blocked"
