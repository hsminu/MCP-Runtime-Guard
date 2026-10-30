"""B6.8: SQLite call sequence durability and authority (SEQ-01/02)."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.contracts import Denied
from guard_app.domain.enums import CommitOutcome, Label, StorePhase, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import MAX_SAFE_SEQUENCE
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.sqlite_writer_test_support import writer_connection
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_sqlite_decisions import NOW, record, task
from tests.unit.test_store_recovery import RUN, _close, _open, _rows
from tests.unit.test_store_run_activation import _new_task

_ROOT = Path(__file__).resolve().parents[2]
_KILLED = 86
_BOOT = (
    "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
    "from tests.unit.test_b68_call_seq_durability import _child_main; _child_main(sys.argv[3:])"
)


def _ids():
    ids = count(1)
    return lambda: f"audit-{next(ids)}"


def test_interleaved_tasks_have_independent_sequences_and_unchanged_task_rows(tmp_path):
    async def check():
        with SQLiteStore(tmp_path / "sequences.db", audit_id_factory=_ids()) as store:
            await store.create_task(task(), created_at_utc=NOW)
            await store.create_task(task(task_id="other"), created_at_utc=NOW)
            original = (await store.load_task("task-1"), await store.load_task("other"))
            values = [
                await store.allocate_call_seq(name)
                for name in ("task-1", "other", "other", "task-1", "task-1", "other")
            ]
            assert values == [1, 1, 2, 2, 3, 3]
            assert (await store.load_task("task-1"), await store.load_task("other")) == original
            rows = await store._run_on_writer_async(
                lambda connection: connection.execute(
                    "SELECT task_id,detail FROM audit_events "
                    "WHERE event_type='CALL_SEQ_ALLOCATED' ORDER BY rowid"
                ).fetchall()
            )
            assert [(row[0], json.loads(row[1])["call_seq"]) for row in rows] == list(
                zip(("task-1", "other", "other", "task-1", "task-1", "other"), values, strict=True)
            )

    asyncio.run(check())


def test_same_task_epoch_change_keeps_the_sequence(tmp_path):
    """인위적 DB fixture: 재시작 시 옛 Task의 실제 재개 권한을 뜻하지 않는다."""
    with SQLiteStore(tmp_path / "epoch.db", audit_id_factory=_ids()) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        assert asyncio.run(store.allocate_call_seq("task-1")) == 1
        writer_connection(store).execute("UPDATE tasks SET epoch=epoch+1 WHERE task_id='task-1'")
        assert asyncio.run(store.load_task("task-1")).epoch == task().epoch + 1
        assert asyncio.run(store.allocate_call_seq("task-1")) == 2


def test_sequence_upper_bound_stops_guard_task_without_wrapping(tmp_path):
    async def check():
        h = _Harness(label=Label.PUBLIC)
        store = SQLiteStore(tmp_path / "upper.db", audit_id_factory=_ids())
        guard = None
        try:
            await store.create_task(h.store.states["task"], created_at_utc=h.now)
            await store._run_on_writer_async(
                lambda connection: connection.execute(
                    "UPDATE task_call_sequences SET last_issued=? WHERE task_id='task'",
                    (MAX_SAFE_SEQUENCE - 1,),
                ),
                write=True,
            )
            assert await store.allocate_call_seq("task") == MAX_SAFE_SEQUENCE
            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: "request-bound",
                approval_id_factory=lambda: "approval-bound",
                next_call_seq=store.allocate_call_seq,
                failure_audit=h.failure_audit,
                utc_now=lambda: h.now,
                monotonic=lambda: h.monotonic,
            )
            outcome = await guard.submit("task", _call(), expected_epoch=1)
            assert isinstance(outcome, Denied)
            assert outcome.reasons == (Reason.INVALID_ARGUMENTS,)
            assert (await store.load_task("task")).status is TaskStatus.HALTED
            assert not h.transport.calls
            row = await store._run_on_writer_async(
                lambda connection: connection.execute(
                    "SELECT last_issued FROM task_call_sequences WHERE task_id='task'"
                ).fetchone()
            )
            assert row[0] == MAX_SAFE_SEQUENCE
        finally:
            if guard is not None:
                await guard.aclose()
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(check())


def test_unknown_commit_closes_authority_and_prevents_allocation_and_reservation(
    tmp_path, monkeypatch
):
    original = sqlite3.connect
    armed = threading.Event()

    class LostCommitAck(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            result = super().execute(sql, parameters)
            if sql == "COMMIT" and armed.is_set():
                armed.clear()
                raise sqlite3.OperationalError("private commit fault")
            return result

    monkeypatch.setattr(
        sqlite3, "connect", lambda *args, **kw: original(*args, **kw, factory=LostCommitAck)
    )
    with SQLiteStore(tmp_path / "unknown.db", audit_id_factory=_ids()) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        armed.set()
        with pytest.raises(StoreError) as fault:
            asyncio.run(store.allocate_call_seq("task-1"))
        assert fault.value.reason is Reason.STATE_COMMIT_FAILED
        state = store.execution_state()
        assert state.phase is StorePhase.FAULTED
        assert state.commit_outcome is CommitOutcome.UNKNOWN
        assert not state.execution_enabled
        assert "private commit fault" not in repr(fault.value) + repr(state)
        with pytest.raises(StoreError):
            asyncio.run(store.allocate_call_seq("task-1"))
        with pytest.raises(StoreError):
            asyncio.run(store.reserve_dispatch("request-1", now_utc=NOW))


def test_concurrent_allocations_have_no_duplicates(tmp_path):
    async def check():
        store = SQLiteStore(tmp_path / "concurrent.db", audit_id_factory=_ids())
        try:
            await store.create_task(task(), created_at_utc=NOW)
            allocations = await asyncio.gather(
                *(store.allocate_call_seq("task-1") for _ in range(40))
            )
            assert len(set(allocations)) == 40
            assert all(0 < value <= MAX_SAFE_SEQUENCE for value in allocations)
        finally:
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(check())


def _child_main(args):
    path, point, report_path = args
    phase, _, number = point.partition(":")
    target = int(number) if number else 0
    state = {"armed": False, "index": 0, "sql": []}
    original_connect = sqlite3.connect

    class KillConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if state["armed"]:
                state["index"] += 1
                if phase == "before" and state["index"] == target:
                    os._exit(_KILLED)
                result = super().execute(sql, parameters)
                state["sql"].append(" ".join(sql.split()))
                if phase == "after" and state["index"] == target:
                    os._exit(_KILLED)
                return result
            return super().execute(sql, parameters)

    sqlite3.connect = lambda *a, **kw: original_connect(*a, **kw, factory=KillConnection)
    child_ids = count(1)
    store = SQLiteStore(path, audit_id_factory=lambda: f"child-audit-{next(child_ids)}")
    asyncio.run(store.create_task(task(), created_at_utc=NOW))
    assert asyncio.run(store.allocate_call_seq("task-1")) == 1  # acknowledged baseline
    record(store)  # A real request uses the first acknowledged number.
    state["armed"] = True
    assert asyncio.run(store.allocate_call_seq("task-1")) == 2
    state["armed"] = False
    store.close()
    assert asyncio.run(store.wait_closed(5))
    Path(report_path).write_text(json.dumps(state["sql"]), encoding="utf-8")


def _kill_child(folder, name, point):
    db = folder / f"{name}.db"
    report = folder / f"{name}.json"
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and internal test entry point
        [
            sys.executable,
            "-I",
            "-c",
            _BOOT,
            str(_ROOT / "src"),
            str(_ROOT),
            str(db),
            point,
            str(report),
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result, db, report


def _read_only_sequence(db):
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        counter = connection.execute(
            "SELECT last_issued FROM task_call_sequences WHERE task_id='task-1'"
        ).fetchone()[0]
        events = connection.execute(
            "SELECT detail FROM audit_events WHERE event_type='CALL_SEQ_ALLOCATED' ORDER BY rowid"
        ).fetchall()
        duplicates = connection.execute(
            "SELECT task_id,call_seq,count(*) FROM requests "
            "GROUP BY task_id,call_seq HAVING count(*)>1"
        ).fetchall()
        requests = connection.execute(
            "SELECT task_id,call_seq FROM requests ORDER BY 1,2"
        ).fetchall()
    return counter, [json.loads(row[0])["call_seq"] for row in events], duplicates, requests


def test_allocation_atomic_across_process_death_and_explicit_recovery(tmp_path):
    reference, _, report = _kill_child(tmp_path, "reference", "none")
    assert reference.returncode == 0, reference.stderr
    statements = json.loads(report.read_text(encoding="utf-8"))
    commit = next(i for i, sql in enumerate(statements, 1) if sql.upper().startswith("COMMIT"))
    points = [
        (phase, index)
        for index, sql in enumerate(statements, 1)
        if sql.upper().startswith(("UPDATE", "INSERT", "COMMIT"))
        for phase in ("before", "after")
    ]
    assert points
    assert any(index == commit for _, index in points)

    async def recovered(db, expected):
        store = await _open(db, tmp_path)
        try:
            report = await store.recover(deadline=time.monotonic() + 20, run_id=RUN)
            assert (report.unresolved_count, report.quarantined_count) == (0, 0)
            await store.activate_run()
            before = await _rows(store, "SELECT * FROM tasks ORDER BY 1")
            before_sequences = await _rows(store, "SELECT * FROM task_call_sequences ORDER BY 1")
            before_audit = await _rows(store, "SELECT * FROM audit_events ORDER BY 1")
            before_requests = await _rows(store, "SELECT * FROM requests ORDER BY 1")
            changes = await store._run_on_writer_async(lambda connection: connection.total_changes)
            with pytest.raises(StoreError) as old_error:
                await store.allocate_call_seq("task-1")
            assert old_error.value.reason is Reason.TASK_NOT_RUNNING
            assert (
                await store._run_on_writer_async(lambda connection: connection.total_changes)
                == changes
            )
            assert await _rows(store, "SELECT * FROM tasks ORDER BY 1") == before
            assert (
                await _rows(store, "SELECT * FROM task_call_sequences ORDER BY 1")
                == before_sequences
            )
            assert await _rows(store, "SELECT * FROM audit_events ORDER BY 1") == before_audit
            assert await _rows(store, "SELECT * FROM requests ORDER BY 1") == before_requests
            await _new_task(store, "new-task")
            assert await store.allocate_call_seq("new-task") == 1
            assert await _rows(
                store, "SELECT last_issued FROM task_call_sequences WHERE task_id='task-1'"
            ) == [(expected,)]
        finally:
            await _close(store)

    for phase, index in points:
        name = f"{phase}-{index}"
        result, db, _ = _kill_child(tmp_path, name, f"{phase}:{index}")
        assert result.returncode == _KILLED, (name, result.stderr)
        expected = 2 if index == commit and phase == "after" else 1
        counter, events, duplicates, requests = _read_only_sequence(db)
        assert counter >= 1  # at least the acknowledged baseline, never reused
        assert (counter, events, duplicates, requests) == (
            expected,
            list(range(1, expected + 1)),
            [],
            [("task-1", 1)],
        )
        asyncio.run(recovered(db, expected))
