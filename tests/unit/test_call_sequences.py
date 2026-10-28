"""SEQ-01/02: writer의 영속 순번·감사·취소 경계(SQV, I6/I9)."""

import asyncio
import json
import sqlite3
import threading
from itertools import count

import pytest

from guard_app.domain.enums import CommitOutcome, StorePhase, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import MAX_SAFE_SEQUENCE
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.sqlite_writer_test_support import writer_connection
from tests.unit.test_sqlite_decisions import NOW, record, request, task


def _ids():
    sequence = count(1)
    return lambda: f"audit-{next(sequence)}"


@pytest.fixture
def store(tmp_path):
    with SQLiteStore(tmp_path / "sequences.db", audit_id_factory=_ids()) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        yield instance


def _seq(store, task_id="task-1"):
    return asyncio.run(store.allocate_call_seq(task_id))


def _audit(store):
    return (
        writer_connection(store)
        .execute(
            "SELECT event_type,task_id,detail FROM audit_events "
            "WHERE event_type='CALL_SEQ_ALLOCATED' ORDER BY rowid"
        )
        .fetchall()
    )


def test_first_sequence_independent_task_counters_gaps_and_atomic_audits(store):
    asyncio.run(store.create_task(task(task_id="other"), created_at_utc=NOW))
    before = asyncio.run(store.load_task("task-1"))
    assert [_seq(store), _seq(store), _seq(store, "other"), _seq(store)] == [1, 2, 1, 3]
    assert asyncio.run(store.load_task("task-1")) == before
    assert [(event, task_id, json.loads(detail)) for event, task_id, detail in _audit(store)] == [
        ("CALL_SEQ_ALLOCATED", "task-1", {"call_seq": 1, "run_id": None}),
        ("CALL_SEQ_ALLOCATED", "task-1", {"call_seq": 2, "run_id": None}),
        ("CALL_SEQ_ALLOCATED", "other", {"call_seq": 1, "run_id": None}),
        ("CALL_SEQ_ALLOCATED", "task-1", {"call_seq": 3, "run_id": None}),
    ]
    assert "TEST_SECRET" not in repr(_audit(store))


def test_sequence_survives_epoch_change_and_reopening(tmp_path):
    path = tmp_path / "epoch.db"
    ids = _ids()
    with SQLiteStore(path, audit_id_factory=ids) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        assert _seq(store) == 1
        writer_connection(store).execute("UPDATE tasks SET epoch=4 WHERE task_id='task-1'")
        assert _seq(store) == 2
    with SQLiteStore(path, audit_id_factory=ids) as reopened:
        assert _seq(reopened) == 3
        assert asyncio.run(reopened.load_task("task-1")).epoch == 4


@pytest.mark.parametrize("task_id", ["", 42, None])
def test_invalid_task_id_is_fixed_error_without_allocation(store, task_id):
    with pytest.raises(StoreError) as error:
        _seq(store, task_id)
    assert error.value.reason is Reason.INVALID_ARGUMENTS
    assert _audit(store) == []
    assert store.execution_state().execution_enabled


def test_unknown_task_is_missing_without_creating_counter(store):
    with pytest.raises(StoreError) as error:
        _seq(store, "missing")
    assert error.value.reason is Reason.STATE_MISSING
    rows = (
        writer_connection(store)
        .execute("SELECT task_id FROM task_call_sequences ORDER BY task_id")
        .fetchall()
    )
    assert [tuple(row) for row in rows] == [("task-1",)]
    assert store.execution_state().execution_enabled


@pytest.mark.parametrize("status", [TaskStatus.READY, TaskStatus.HALTED, TaskStatus.CLOSED])
def test_nonrunning_task_does_not_allocate(tmp_path, status):
    with SQLiteStore(tmp_path / "inactive.db", audit_id_factory=_ids()) as store:
        asyncio.run(store.create_task(task(status=status), created_at_utc=NOW))
        with pytest.raises(StoreError) as error:
            _seq(store)
        assert error.value.reason is Reason.TASK_NOT_RUNNING
        assert _audit(store) == []
        assert store.execution_state().execution_enabled


def test_sequence_upper_bound_does_not_wrap_or_change_counter(store):
    writer_connection(store).execute(
        "UPDATE task_call_sequences SET last_issued=? WHERE task_id='task-1'",
        (MAX_SAFE_SEQUENCE - 1,),
    )
    assert _seq(store) == MAX_SAFE_SEQUENCE
    with pytest.raises(StoreError) as error:
        _seq(store)
    assert error.value.reason is Reason.INVALID_ARGUMENTS
    assert (
        writer_connection(store)
        .execute("SELECT last_issued FROM task_call_sequences")
        .fetchone()[0]
        == MAX_SAFE_SEQUENCE
    )
    assert [json.loads(row[2])["call_seq"] for row in _audit(store)] == [MAX_SAFE_SEQUENCE]
    assert store.execution_state().execution_enabled


@pytest.mark.parametrize("corruption", ["missing", "below_request", "below_escalation"])
def test_counter_corruption_closes_store_authority(store, corruption):
    connection = writer_connection(store)
    if corruption == "missing":
        connection.execute("DELETE FROM task_call_sequences WHERE task_id='task-1'")
    elif corruption == "below_request":
        record(store, request(call_seq=9))
    else:
        connection.execute("UPDATE tasks SET high_watermark=1 WHERE task_id='task-1'")
        connection.execute("INSERT INTO escalations VALUES (?,?,?,?,?)", ("task-1", 9, "s", 0, 1))
    with pytest.raises(StoreError) as error:
        _seq(store)
    assert error.value.reason is Reason.INTERNAL_ERROR
    assert not store.execution_state().execution_enabled
    assert store.execution_state().phase is StorePhase.FAULTED
    assert _audit(store) == []


def test_authority_halt_rejects_allocation_without_audit(store):
    store.halt_execution(Reason.STATE_COMMIT_FAILED)
    with pytest.raises(StoreError) as error:
        _seq(store)
    assert error.value.reason is Reason.STATE_COMMIT_FAILED
    assert _audit(store) == []


def test_failed_allocation_audit_rolls_back_counter(store):
    writer_connection(store).execute(
        "CREATE TRIGGER fail_sequence_audit BEFORE INSERT ON audit_events "
        "WHEN NEW.event_type='CALL_SEQ_ALLOCATED' BEGIN SELECT RAISE(ABORT, 'TEST_SECRET'); END"
    )
    with pytest.raises(StoreError) as error:
        _seq(store)
    assert error.value.reason is Reason.STATE_COMMIT_FAILED
    assert "TEST_SECRET" not in repr(error.value)
    assert (
        writer_connection(store)
        .execute("SELECT last_issued FROM task_call_sequences")
        .fetchone()[0]
        == 0
    )
    assert _audit(store) == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("before", CommitOutcome.ROLLED_BACK), ("after", CommitOutcome.UNKNOWN)],
)
def test_allocation_commit_failure_faults_and_does_not_leak(
    tmp_path, monkeypatch, failure, expected
):
    original = sqlite3.connect
    armed = threading.Event()

    class FaultConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if armed.is_set() and sql == "COMMIT" and failure == "before":
                armed.clear()
                raise sqlite3.OperationalError("TEST_SECRET_before_commit")
            result = super().execute(sql, parameters)
            if armed.is_set() and sql == "COMMIT" and failure == "after":
                armed.clear()
                raise sqlite3.OperationalError("TEST_SECRET_after_commit")
            return result

    monkeypatch.setattr(
        sqlite3, "connect", lambda *args, **kw: original(*args, **kw, factory=FaultConnection)
    )
    with SQLiteStore(tmp_path / "fault.db", audit_id_factory=_ids()) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        armed.set()
        with pytest.raises(StoreError) as error:
            _seq(store)
        assert error.value.reason is Reason.STATE_COMMIT_FAILED
        state = store.execution_state()
        assert state.phase is StorePhase.FAULTED
        assert state.commit_outcome is expected
        assert not state.execution_enabled
        assert "TEST_SECRET" not in repr(error.value) + repr(state)
        with pytest.raises(StoreError):
            asyncio.run(store.load_task("task-1"))


def test_started_cancelled_allocation_is_committed_and_not_reused(tmp_path, monkeypatch):
    original = sqlite3.connect
    armed, committed, release = threading.Event(), threading.Event(), threading.Event()

    class CommitBarrier(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            result = super().execute(sql, parameters)
            if sql == "COMMIT" and armed.is_set():
                armed.clear()
                committed.set()
                assert release.wait(5)
            return result

    monkeypatch.setattr(
        sqlite3, "connect", lambda *args, **kw: original(*args, **kw, factory=CommitBarrier)
    )

    async def scenario():
        store = SQLiteStore(tmp_path / "cancel.db", audit_id_factory=_ids())
        try:
            await store.create_task(task(), created_at_utc=NOW)
            armed.set()
            work = asyncio.create_task(store.allocate_call_seq("task-1"))
            async with asyncio.timeout(3):
                while not committed.is_set():  # noqa: ASYNC110
                    await asyncio.sleep(0.005)
            work.cancel()
            await asyncio.sleep(0.05)
            assert not work.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert await store.allocate_call_seq("task-1") == 2
            assert store.execution_state().execution_enabled
        finally:
            release.set()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())
