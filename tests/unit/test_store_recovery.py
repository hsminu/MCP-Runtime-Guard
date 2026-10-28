"""REC-02/04 startup recovery, atomic rollback and persisted report tests."""

import asyncio
import json
import sqlite3
import time
from itertools import count
from types import SimpleNamespace

import pytest

from guard_app.domain.enums import StorePhase, StorePrepareStep, StoreStartFailure
from guard_app.domain.errors import StoreError, StorePrepareError
from guard_app.storage import SQLiteStore
from guard_app.storage import sqlite_store as store_module
from tests.unit.test_registry_store import legacy_database
from tests.unit.test_store_prepare_schema import _Backups
from tests.unit.test_store_v3_migration import _v2_database

STAMP = "2026-10-28T00:00:00.000000+00:00"
LATER = "2026-10-28T00:00:01.000000+00:00"
EXPIRES = "2026-10-28T00:01:00.000000+00:00"
RUN = "run-0123456789abcdef0123456789abcdef"
RUN2 = "run-fedcba9876543210fedcba9876543210"
AUDIT_IDS = count(1)


def _empty(path):
    with SQLiteStore(path):
        pass


def _task(connection, status="running", task_id="task"):
    connection.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, 1, "profile", "policy", 1, 0, 0, status, 7, 11, STAMP),
    )
    connection.execute("INSERT INTO task_call_sequences VALUES (?,?)", (task_id, 3))


def _request(connection, status, kind="read", task_id="task", request_id="req", seq=1):
    legacy = kind is None
    connection.execute(
        "INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,destination_id,"
        "arguments_digest,decision,reasons,status,dispatch_reserved_at_utc,epoch,kind,"
        "row_revision,legacy_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            request_id,
            task_id,
            seq,
            "server",
            "TEST_SECRET_TOOL",
            "destination",
            "a" * 64,
            1,
            '["AGT_DENY"]',
            status,
            LATER if status in {"DISPATCHING", "UNKNOWN_OUTCOME"} else None,
            None if legacy else 1,
            kind,
            5,
            "v1" if legacy else None,
        ),
    )


def _approval(connection, status):
    decided = LATER if status in {"APPROVED", "CONSUMED", "REJECTED"} else None
    consumed = LATER if status == "CONSUMED" else None
    connection.execute(
        "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("approval", "req", "b" * 64, "operator", status, STAMP, EXPIRES, decided, consumed, 4),
    )


async def _open(path, folder):

    store = SQLiteStore(
        path, startup="explicit", audit_id_factory=lambda: f"audit-{next(AUDIT_IDS)}"
    )
    backups = _Backups(folder, path)
    await store.prepare_schema(deadline=time.monotonic() + 20, begin_backup=backups.begin)
    return store


async def _close(store):
    store.close()
    assert await store.wait_closed(5)


async def _rows(store, sql, parameters=()):
    return await store._run_on_writer_async(
        lambda connection: [tuple(row) for row in connection.execute(sql, parameters).fetchall()]
    )


async def _snapshot(store):
    return {
        table: await _rows(store, query)
        for table, query in {
            "tasks": "SELECT * FROM tasks ORDER BY 1",
            "requests": "SELECT * FROM requests ORDER BY 1",
            "approvals": "SELECT * FROM approvals ORDER BY 1",
            "audit_events": "SELECT * FROM audit_events ORDER BY 1",
            "store_runs": "SELECT * FROM store_runs ORDER BY 1",
            "recovery_reports": "SELECT * FROM recovery_reports ORDER BY 1",
            "recovery_quarantine": "SELECT * FROM recovery_quarantine ORDER BY 1",
        }.items()
    }


def _database_bytes(path):
    wal = path.with_name(path.name + "-wal")
    return path.read_bytes(), wal.read_bytes() if wal.exists() else None


@pytest.mark.parametrize(
    ("task_status", "request_status", "kind", "expected_task", "expected_request", "category"),
    [
        ("ready", None, "read", "halted", None, None),
        ("running", None, "read", "halted", None, None),
        ("waiting_approval", None, "read", "halted", None, None),
        ("running", "DECIDED", "read", "halted", "DENIED", None),
        ("waiting_approval", "WAITING_APPROVAL", "read", "halted", "DENIED", None),
        ("running", "DISPATCHING", "read", "halted", "WITHHELD", None),
        ("running", "DISPATCHING", "send", "unknown_outcome", "UNKNOWN_OUTCOME", None),
        (
            "running",
            "DISPATCHING",
            None,
            "unknown_outcome",
            "UNKNOWN_OUTCOME",
            "LEGACY_METADATA_MISSING",
        ),
        ("halted", "DISPATCHING", "send", "halted", "UNKNOWN_OUTCOME", "TERMINAL_TASK_DISPATCHING"),
        ("closed", "DISPATCHING", None, "closed", "UNKNOWN_OUTCOME", "TERMINAL_TASK_DISPATCHING"),
        ("unknown_outcome", "DISPATCHING", "read", "unknown_outcome", "WITHHELD", None),
        ("halted", "UNKNOWN_OUTCOME", None, "halted", "UNKNOWN_OUTCOME", "LEGACY_METADATA_MISSING"),
    ],
)
def test_recovery_state_table(
    tmp_path, task_status, request_status, kind, expected_task, expected_request, category
):
    path = tmp_path / "case.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection, task_status)
        if request_status is not None:
            _request(connection, request_status, kind)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            steps = []
            report = await store.recover(
                deadline=time.monotonic() + 10, run_id=RUN, on_step=steps.append
            )
            assert steps == [StorePrepareStep.STORE_RECOVER]
            assert store.execution_state().phase is StorePhase.RECOVERED
            assert not store.execution_state().execution_enabled
            changed_task = int(task_status != expected_task)
            changed_request = int(request_status is not None and request_status != expected_request)
            assert (
                report.changed_task_count,
                report.changed_request_count,
                report.changed_approval_count,
            ) == (changed_task, changed_request, 0)
            assert report.unknown_outcome_count == int(expected_request == "UNKNOWN_OUTCOME")
            assert report.quarantined_count == int(category is not None)
            assert report.unresolved_count == 0
            assert await store.load_recovery_report(RUN) == report
            assert await store.load_recovery_report("no-such-run") is None
            tasks = await _rows(store, "SELECT status,row_revision,security_version FROM tasks")
            assert tasks == [(expected_task, 11 + changed_task, 7 + changed_task)]
            if request_status is not None:
                requests = await _rows(store, "SELECT status,row_revision,reasons FROM requests")
                assert requests == [(expected_request, 5 + changed_request, '["AGT_DENY"]')]
            items = (await store.list_recovery_items()).items
            assert len(items) == int(category is not None)
            if items:
                item = items[0]
                assert item.category.value == category
                assert item.original_status.value == request_status
                assert item.kind is None if kind is None else item.kind.value == kind
                assert item.epoch == (None if kind is None else 1)
                assert "TEST_SECRET_TOOL" not in repr(item)
            assert "TEST_SECRET" not in repr(report)
            assert "TEST_SECRET" not in repr(await _rows(store, "SELECT * FROM audit_events"))
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["PENDING", "APPROVED", "CONSUMED", "REJECTED"])
def test_approval_expiry_and_timestamps(tmp_path, status):
    path = tmp_path / "approval.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection, "waiting_approval")
        _request(connection, "WAITING_APPROVAL")
        _approval(connection, status)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            report = await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            rows = await _rows(
                store,
                "SELECT status,created_at_utc,expires_at_utc,decided_at_utc,"
                "consumed_at_utc,row_revision FROM approvals",
            )
            actual, created, expires, decided, consumed, revision = rows[0]
            changed = status in {"PENDING", "APPROVED"}
            assert actual == ("EXPIRED" if changed else status)
            assert revision == 4 + int(changed)
            assert expires == EXPIRES
            if changed:
                assert decided >= max(created, LATER if status == "APPROVED" else created)
            if status == "CONSUMED":
                assert consumed == LATER
            assert report.changed_approval_count == int(changed)
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_send_unknown_wins_over_later_read(tmp_path):
    path = tmp_path / "multiple.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DISPATCHING", "send", request_id="a-send", seq=1)
        _request(connection, "DISPATCHING", "read", request_id="z-read", seq=2)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            report = await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert await _rows(store, "SELECT status,row_revision,security_version FROM tasks") == [
                ("unknown_outcome", 12, 8)
            ]
            assert await _rows(
                store, "SELECT request_id,status FROM requests ORDER BY request_id"
            ) == [("a-send", "UNKNOWN_OUTCOME"), ("z-read", "WITHHELD")]
            assert (report.changed_task_count, report.changed_request_count) == (1, 2)
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "ready-dispatch",
        "waiting-dispatch",
        "pending-dispatch",
        "approved-dispatch",
        "high-watermark",
    ],
)
def test_invalid_or_unresolved_rolls_back_all_rows(tmp_path, case):
    path = tmp_path / "invalid.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(
            connection,
            {"ready-dispatch": "ready", "waiting-dispatch": "waiting_approval"}.get(
                case, "running"
            ),
        )
        _request(connection, "DISPATCHING", "send")
        if case in {"pending-dispatch", "approved-dispatch"}:
            _approval(connection, case.split("-")[0].upper())
        if case == "high-watermark":
            connection.execute("INSERT INTO escalations VALUES ('task',1,'source',1,2)")

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            before = await _snapshot(store)
            before_bytes = _database_bytes(path)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            expected = (
                StoreStartFailure.STORE_INTEGRITY_FAILED
                if case == "high-watermark"
                else StoreStartFailure.STORE_RECOVERY_UNRESOLVED
            )
            assert error.value.code is expected
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
            assert _database_bytes(path) == before_bytes
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_real_legacy_migration_recovers_without_inventing_metadata(tmp_path, version):
    path = tmp_path / "legacy.db"
    (legacy_database if version == 1 else _v2_database)(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tasks SET created_at_utc=?", (STAMP,))
        if version == 1:
            connection.execute("UPDATE tasks SET status='running'")
            connection.execute("DELETE FROM approvals")
            connection.execute(
                "UPDATE requests SET status='DISPATCHING',dispatch_reserved_at_utc=?", (LATER,)
            )
        else:
            connection.execute("DELETE FROM approvals")
            connection.execute(
                "UPDATE requests SET status='DISPATCHING',dispatch_reserved_at_utc=?", (LATER,)
            )

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            report = await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            items = (await store.list_recovery_items(run_id=RUN)).items
            assert report.quarantined_count == len(items) == (1 if version == 1 else 2)
            assert all(item.kind is None and item.epoch is None for item in items)
            assert all(item.category.value == "LEGACY_METADATA_MISSING" for item in items)
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_second_run_is_idempotent_and_has_one_summary_audit(tmp_path):
    path = tmp_path / "repeat.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DISPATCHING", None)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            first = await _snapshot(store)
        finally:
            await _close(store)
        store = await _open(path, tmp_path)
        try:
            report = await store.recover(deadline=time.monotonic() + 10, run_id=RUN2)
            second = await _snapshot(store)
            assert (
                report.changed_task_count,
                report.changed_request_count,
                report.changed_approval_count,
            ) == (0, 0, 0)
            assert (report.unknown_outcome_count, report.quarantined_count) == (1, 1)
            for table in ("tasks", "requests", "approvals", "recovery_quarantine"):
                assert second[table] == first[table]
            first_events = [row for row in first["audit_events"] if row[3] != "RECOVERY_COMMITTED"]
            second_events = [
                row for row in second["audit_events"] if row[3] != "RECOVERY_COMMITTED"
            ]
            assert second_events == first_events
            summaries = await _rows(
                store, "SELECT detail FROM audit_events WHERE event_type='RECOVERY_COMMITTED'"
            )
            assert len(summaries) == 2
            assert all(isinstance(json.loads(row[0]), dict) for row in summaries)
            assert await _rows(store, "SELECT state FROM store_runs WHERE run_id=?", (RUN,)) == [
                ("CLOSED",)
            ]
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_missing_task_relationship_is_integrity_failure_and_rolls_back(tmp_path):
    path = tmp_path / "orphan.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DECIDED")

    async def scenario():
        store = await _open(path, tmp_path)
        try:

            def corrupt(connection):
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute("UPDATE requests SET task_id='missing'")
                connection.execute("PRAGMA foreign_keys=ON")

            await store._run_on_writer_async(corrupt)
            before = await _snapshot(store)
            before_bytes = _database_bytes(path)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert error.value.code is StoreStartFailure.STORE_INTEGRITY_FAILED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
            assert _database_bytes(path) == before_bytes
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_pagination_returns_persisted_quarantine_in_request_order(tmp_path):
    path = tmp_path / "pages.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DISPATCHING", None, request_id="a", seq=1)
        _request(connection, "DISPATCHING", None, request_id="b", seq=2)

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            first = await store.list_recovery_items(run_id=RUN, limit=1)
            assert [item.request_id for item in first.items] == ["a"]
            assert first.next_after == "a"
            second = await store.list_recovery_items(run_id=RUN, after=first.next_after, limit=1)
            assert [item.request_id for item in second.items] == ["b"]
            assert second.next_after is None
            assert (await store.list_recovery_items(run_id="other")).items == ()
            for limit in (0, 501):
                with pytest.raises((ValueError, TypeError, StoreError)):
                    await store.list_recovery_items(limit=limit)
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize("expire_after", ["rows", "commit"])
def test_deadline_rolls_back_or_rejects_post_commit_ack(tmp_path, monkeypatch, expire_after):
    path = tmp_path / "deadline.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DISPATCHING", "send")
    clock = SimpleNamespace(now=0.0)
    armed = SimpleNamespace(value=False)
    original_connect = sqlite3.connect
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class DelayedConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            result = super().execute(statement, parameters)
            if armed.value and (
                (expire_after == "commit" and statement == "COMMIT")
                or (expire_after == "rows" and "SELECT * FROM requests" in statement)
            ):
                armed.value = False
                clock.now = 2.0
            return result

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=DelayedConnection),
    )

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            before = await _snapshot(store)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(
                    deadline=1.0, run_id=RUN, on_step=lambda step: setattr(armed, "value", True)
                )
            assert error.value.code is StoreStartFailure.STORE_DEADLINE_EXCEEDED
            assert store.execution_state().phase is StorePhase.FAULTED
            if expire_after == "rows":
                assert await _snapshot(store) == before
        finally:
            await _close(store)
        if expire_after == "commit":
            clock.now = 0.0
            store = await _open(path, tmp_path)
            try:
                report = await store.recover(deadline=10.0, run_id=RUN2)
                assert (
                    report.changed_task_count,
                    report.changed_request_count,
                    report.changed_approval_count,
                ) == (0, 0, 0)
            finally:
                await _close(store)

    asyncio.run(scenario())


def test_lost_commit_ack_faults_and_restart_reads_committed_facts(tmp_path, monkeypatch):
    path = tmp_path / "lost-ack.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DISPATCHING", "send")
    original_connect = sqlite3.connect
    armed = SimpleNamespace(value=False)

    class LostAckConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            result = super().execute(statement, parameters)
            if armed.value and statement == "COMMIT":
                armed.value = False
                raise sqlite3.OperationalError("TEST_SECRET commit acknowledgment")
            return result

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: original_connect(*args, **kwargs, factory=LostAckConnection),
    )

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            with pytest.raises(StorePrepareError) as error:
                await store.recover(
                    deadline=time.monotonic() + 10,
                    run_id=RUN,
                    on_step=lambda step: setattr(armed, "value", True),
                )
            assert error.value.code is StoreStartFailure.STORE_COMMIT_UNKNOWN
            assert "TEST_SECRET" not in str(error.value)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not store.execution_state().execution_enabled
        finally:
            await _close(store)
        store = await _open(path, tmp_path)
        try:
            report = await store.recover(deadline=time.monotonic() + 10, run_id=RUN2)
            assert (report.changed_task_count, report.changed_request_count) == (0, 0)
            assert report.unknown_outcome_count == 1
            assert await store.load_recovery_report(RUN) is not None
        finally:
            await _close(store)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["quarantine-status", "quarantine-category", "audit-dispatch", "audit-consumed"]
)
def test_contradictory_quarantine_and_audit_fail_atomically(tmp_path, case):
    path = tmp_path / "contradiction.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _task(connection, "halted", task_id="other")
        _request(connection, "DISPATCHING" if case == "quarantine-status" else "UNKNOWN_OUTCOME")
        if case.startswith("quarantine"):
            connection.execute(
                "INSERT INTO store_runs VALUES (?,'CLOSED',?,?,NULL,?)", (RUN2, STAMP, STAMP, STAMP)
            )
            connection.execute(
                "INSERT INTO recovery_quarantine VALUES (?,?,?,?,?,?)",
                ("req", RUN2, "task", "DISPATCHING", "LEGACY_METADATA_MISSING", STAMP),
            )
        else:
            event = "DISPATCH_RESERVED" if case == "audit-dispatch" else "CONSUMED"
            connection.execute(
                "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                ("old-event", "req", "other", event, None, None, None, STAMP),
            )

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            before, before_bytes = await _snapshot(store), _database_bytes(path)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert error.value.code is StoreStartFailure.STORE_INTEGRITY_FAILED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
            assert _database_bytes(path) == before_bytes
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_missing_unknown_outcome_reservation_is_integrity_failure(tmp_path):
    path = tmp_path / "missing-reservation.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection, "halted")
        _request(connection, "UNKNOWN_OUTCOME", None)
        connection.execute("UPDATE requests SET dispatch_reserved_at_utc=NULL")

    async def scenario():
        store = await _open(path, tmp_path)
        try:
            before, before_bytes = await _snapshot(store), _database_bytes(path)
            with pytest.raises(StorePrepareError) as error:
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert error.value.code is StoreStartFailure.STORE_INTEGRITY_FAILED
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
            assert _database_bytes(path) == before_bytes
        finally:
            await _close(store)

    asyncio.run(scenario())


def test_default_audit_factory_creates_distinct_summary_ids_each_run(tmp_path):
    path = tmp_path / "default-ids.db"

    async def scenario():
        for run in (RUN, RUN2):
            store = SQLiteStore(path, startup="explicit")
            try:
                await store.prepare_schema(
                    deadline=time.monotonic() + 10, begin_backup=_Backups(tmp_path, path).begin
                )
                await store.recover(deadline=time.monotonic() + 10, run_id=run)
                events = await _rows(
                    store,
                    "SELECT event_id FROM audit_events WHERE event_type='RECOVERY_COMMITTED'",
                )
            finally:
                await _close(store)
        assert len(events) == 2
        assert len({row[0] for row in events}) == 2

    asyncio.run(scenario())


def test_duplicate_audit_id_rolls_back_whole_recovery(tmp_path):
    path = tmp_path / "duplicate-audit.db"
    _empty(path)
    with sqlite3.connect(path) as connection:
        _task(connection)
        _request(connection, "DISPATCHING", "send")
        connection.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            ("duplicate", None, None, "existing", None, None, None, STAMP),
        )

    async def scenario():
        store = SQLiteStore(path, startup="explicit", audit_id_factory=lambda: "duplicate")
        try:
            await store.prepare_schema(
                deadline=time.monotonic() + 10, begin_backup=_Backups(tmp_path, path).begin
            )
            before, before_bytes = await _snapshot(store), _database_bytes(path)
            with pytest.raises(StorePrepareError):
                await store.recover(deadline=time.monotonic() + 10, run_id=RUN)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert await _snapshot(store) == before
            assert _database_bytes(path) == before_bytes
        finally:
            await _close(store)

    asyncio.run(scenario())
