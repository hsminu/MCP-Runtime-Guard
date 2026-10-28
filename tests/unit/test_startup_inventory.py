"""B6 시작 전 v3 저장 사실 읽기의 B 단위 검사(I4/I8/I9, T11/T19, S03).

복구 상태를 결정하거나 순번을 배정하지 않는다. 테스트의 직접 SQL은 기존 분리
저장의 잔존 행·손상·WAL 읽기 시점을 구성하는 용도이며 C 독립 인수를 대신하지 않는다.
"""

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from guard_app.domain.enums import ApprovalStatus, Decision, Label, RequestStatus, ToolKind
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Escalation
from guard_app.storage import SQLiteStore
from guard_app.storage.sqlite_store import APPLICATION_ID, SCHEMA_VERSION
from tests.unit.sqlite_writer_test_support import writer_connection
from tests.unit.test_sqlite_decisions import (
    LATER,
    NOW,
    assert_sanitized,
    record,
    request,
    reserve,
    task,
)
from tests.unit.test_sqlite_lifecycle import consume, pending, transition


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    identifiers = iter(range(1, 100))
    with SQLiteStore(
        tmp_path / "guard.db",
        audit_id_factory=lambda: f"event-{next(identifiers)}",
        utc_now=lambda: LATER,
    ) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        yield instance


def database_facts(store: SQLiteStore) -> dict[str, object]:
    connection = writer_connection(store)
    tables = {
        "tasks": "SELECT * FROM tasks ORDER BY 1,2",
        "escalations": "SELECT * FROM escalations ORDER BY 1,2",
        "requests": "SELECT * FROM requests ORDER BY 1,2",
        "approvals": "SELECT * FROM approvals ORDER BY 1,2",
        "audit_events": "SELECT * FROM audit_events ORDER BY 1,2",
        "registrations": "SELECT * FROM registrations ORDER BY 1,2",
        "registration_tools": "SELECT * FROM registration_tools ORDER BY 1,2",
        "scan_reports": "SELECT * FROM scan_reports ORDER BY 1,2",
    }
    result: dict[str, object] = {
        table: [tuple(row) for row in connection.execute(query)] for table, query in tables.items()
    }
    result["schema"] = [
        tuple(row)
        for row in connection.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name")
    ]
    result["version"] = connection.execute("PRAGMA user_version").fetchone()[0]
    result["application"] = connection.execute("PRAGMA application_id").fetchone()[0]
    return result


def memory_contexts(store: SQLiteStore) -> tuple[dict[str, object], ...]:
    return store._test_context_snapshot()


def test_empty_inventory_leaves_unknown_maxima_and_does_not_choose_first_number(
    tmp_path: Path,
) -> None:
    with SQLiteStore(tmp_path / "empty.db") as instance:
        before = database_facts(instance)
        inventory = instance._inspect_startup()
        assert inventory.schema_version == SCHEMA_VERSION
        assert inventory.tasks == inventory.requests == inventory.approvals == ()
        assert database_facts(instance) == before
        assert not writer_connection(instance).in_transaction


def test_maxima_are_observed_per_task_without_allocation(tmp_path: Path) -> None:
    history = (
        Escalation(
            at_seq=6, source_id="source-a", from_label=Label.PUBLIC, to_label=Label.INTERNAL
        ),
        Escalation(
            at_seq=12,
            source_id="source-b",
            from_label=Label.INTERNAL,
            to_label=Label.CONFIDENTIAL,
        ),
    )
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=iter(("a", "b", "c")).__next__
    ) as instance:
        original = task(high_watermark=Label.CONFIDENTIAL, escalations=history)
        asyncio.run(instance.create_task(original, created_at_utc=NOW))
        asyncio.run(instance.create_task(task(task_id="empty-task"), created_at_utc=NOW))
        record(instance, request(call_seq=0))
        record(instance, request(request_id="request-3", call_seq=3))
        inventory = instance._inspect_startup()
        by_task = {entry.state.task_id: entry for entry in inventory.tasks}
        assert by_task["task-1"].state == original
        assert by_task["task-1"].request_sequence_max == 3
        assert by_task["task-1"].escalation_sequence_max == 12
        assert by_task["task-1"].persisted_sequence_max == 12
        assert by_task["empty-task"].request_sequence_max is None
        assert by_task["empty-task"].escalation_sequence_max is None
        assert by_task["empty-task"].persisted_sequence_max is None
        assert {entry.call_seq for entry in inventory.requests} == {0, 3}


def test_zero_is_a_persisted_number_and_not_an_empty_history(store: SQLiteStore) -> None:
    record(store, request(call_seq=0))
    value = store._inspect_startup().tasks[0]
    assert value.request_sequence_max == value.persisted_sequence_max == 0
    assert value.escalation_sequence_max is None


@pytest.mark.parametrize("kind", [ToolKind.READ, ToolKind.SEND])
def test_v3_preserves_request_kind_and_epoch_in_live_and_reopened_context(
    tmp_path: Path, kind: ToolKind
) -> None:
    path = tmp_path / "guard.db"
    req = request()
    req = req.model_copy(update={"tool": req.tool.model_copy(update={"kind": kind})})
    with SQLiteStore(path, audit_id_factory=iter(("decision", "reserve")).__next__) as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        record(instance, req)
        reserve(instance)
        assert instance._dispatch_contexts
        before_context = memory_contexts(instance)
        live = instance._inspect_startup()
        assert memory_contexts(instance) == before_context
    with SQLiteStore(path) as instance:
        reopened = instance._inspect_startup()
        assert memory_contexts(instance) == ({}, {}, {}, {})
        assert reopened == live
        value = reopened.requests[0]
        assert value.stored_epoch == req.epoch
        assert value.stored_kind is kind
        assert value.stored_row_revision == 0
        assert value.decision is Decision.ALLOW
        assert value.status is RequestStatus.DISPATCHING
        assert value.dispatch_reserved_at_utc == LATER


@pytest.mark.parametrize("stage", ["pending", "approved", "consumed"])
def test_approval_is_linked_to_existing_request_and_never_recreates_authority(
    store: SQLiteStore, stage: str
) -> None:
    expected = pending(store)
    if stage != "pending":
        expected = transition(store, ApprovalStatus.APPROVED)
    if stage == "consumed":
        assert consume(store)
        expected = asyncio.run(store.get_approval("approval-1"))
    before = database_facts(store)
    cached = memory_contexts(store)
    value = store._inspect_startup().approvals[0]
    assert value.task_id == "task-1"
    assert value.record == expected
    assert database_facts(store) == before
    assert memory_contexts(store) == cached


def test_repeated_inventory_preserves_all_tables_schema_and_cached_contexts(
    store: SQLiteStore,
) -> None:
    pending(store)
    connection = writer_connection(store)
    connection.execute(
        "INSERT INTO registrations VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "server-1",
            "pending",
            "boundary-1",
            "internal",
            2,
            None,
            None,
            None,
            1,
            NOW.isoformat(timespec="microseconds"),
        ),
    )
    connection.execute(
        "INSERT INTO scan_reports VALUES (?,?,?,?,?)",
        (
            "scan-1",
            "server-1",
            '{"details":"TEST_SECRET_scanner"}',
            NOW.isoformat(timespec="microseconds"),
            NOW.isoformat(timespec="microseconds"),
        ),
    )
    before, cached = database_facts(store), memory_contexts(store)
    first = store._inspect_startup()
    for _ in range(3):
        assert store._inspect_startup() == first
        assert database_facts(store) == before
        assert memory_contexts(store) == cached
    assert not connection.in_transaction


def test_inventory_calls_neither_audit_id_factory_nor_clock(store: SQLiteStore) -> None:
    pending(store)
    calls: list[str] = []

    def forbidden_id() -> str:
        calls.append("audit")
        raise ValueError("TEST_SECRET_audit")

    def forbidden_clock() -> object:
        calls.append("utc")
        raise ValueError("TEST_SECRET_clock")

    store._audit_id_factory = forbidden_id
    store._utc_now = forbidden_clock
    before = database_facts(store)
    store._inspect_startup()
    assert calls == []
    assert database_facts(store) == before


def test_inventory_records_are_frozen_and_repr_hides_persisted_identifiers(
    store: SQLiteStore,
) -> None:
    pending(store)
    inventory = store._inspect_startup()
    values = (
        (inventory, "schema_version", 99),
        (inventory.tasks[0], "request_sequence_max", 99),
        (inventory.requests[0], "request_id", "changed"),
        (inventory.approvals[0], "task_id", "changed"),
    )
    for value, name, replacement in values:
        with pytest.raises(FrozenInstanceError):
            setattr(value, name, replacement)
        rendered = repr(value)
        assert "task-1" not in rendered
        assert "request-1" not in rendered
        assert "approval-1" not in rendered
        assert "TEST_SECRET" not in rendered
    assert {field.name for field in fields(inventory.requests[0])} == {
        "request_id",
        "task_id",
        "call_seq",
        "status",
        "decision",
        "dispatch_reserved_at_utc",
        "stored_epoch",
        "stored_kind",
        "stored_row_revision",
    }


@pytest.mark.parametrize(
    "stage",
    [
        "waiting_without_approval",
        "pending_decided",
        "pending_dispatching",
        "approved_waiting",
        "consumed_dispatching",
        "dispatching_running",
        "dispatching_halted",
        "null_decision_and_reasons",
        "null_decision",
        "null_reasons",
    ],
)
def test_split_or_crash_stages_are_observed_without_rewriting_or_recovery_choice(
    store: SQLiteStore, stage: str
) -> None:
    connection = writer_connection(store)
    if stage.startswith(("pending_", "approved_", "consumed_")):
        pending(store)
        if stage == "approved_waiting":
            transition(store, ApprovalStatus.APPROVED)
        elif stage == "consumed_dispatching":
            transition(store, ApprovalStatus.APPROVED)
            assert consume(store)
        elif stage == "pending_decided":
            connection.execute("UPDATE requests SET status='DECIDED'")
        else:
            connection.execute("UPDATE requests SET status='DISPATCHING'")
    else:
        record(store)
        if stage == "waiting_without_approval":
            connection.execute("UPDATE tasks SET status='waiting_approval'")
        elif stage.startswith("null_"):
            nullable_queries = {
                "null_decision_and_reasons": "UPDATE requests SET decision=NULL,reasons=NULL",
                "null_decision": "UPDATE requests SET decision=NULL",
                "null_reasons": "UPDATE requests SET reasons=NULL",
            }
            connection.execute(nullable_queries[stage])
        else:
            reserve(store)
            if stage == "dispatching_halted":
                connection.execute("UPDATE tasks SET status='halted'")
    before, cached = database_facts(store), memory_contexts(store)
    inventory = store._inspect_startup()
    assert len(inventory.tasks) == len(inventory.requests) == 1
    assert inventory.tasks[0].state == asyncio.run(store.load_task("task-1"))
    if stage in ("null_decision_and_reasons", "null_decision"):
        assert inventory.requests[0].decision is None
    assert database_facts(store) == before
    assert memory_contexts(store) == cached


_CORRUPT_ROWS = [
    ("tasks", "created_at_utc", "TEST_SECRET_timestamp"),
    ("tasks", "created_at_utc", "2026-10-13T12:34:56+09:00"),
    ("tasks", "epoch", -1),
    ("tasks", "high_watermark", 4),
    ("tasks", "last_read_label", 4),
    ("tasks", "unclassified", 2),
    ("tasks", "status", "TEST_SECRET_status"),
    ("tasks", "security_version", -1),
    ("tasks", "row_revision", -1),
    ("tasks", "profile_id", ""),
    ("requests", "call_seq", -1),
    ("requests", "server_id", ""),
    ("requests", "tool_name", ""),
    ("requests", "destination_id", ""),
    ("requests", "arguments_digest", "TEST_SECRET_hash"),
    ("requests", "decision", 7),
    ("requests", "reasons", "TEST_SECRET_json"),
    ("requests", "reasons", '{"reason":"TEST_SECRET"}'),
    ("requests", "reasons", "[]"),
    ("requests", "reasons", "[true]"),
    ("requests", "reasons", '["TEST_SECRET_reason"]'),
    ("requests", "reasons", "[" * 1100 + '"TEST_SECRET_nested"' + "]" * 1100),
    ("requests", "status", "TEST_SECRET_status"),
    ("requests", "dispatch_reserved_at_utc", "TEST_SECRET_timestamp"),
    ("requests", "dispatch_reserved_at_utc", "2026-10-13T12:34:56.123456+09:00"),
    ("approvals", "snapshot_hash", "TEST_SECRET_hash"),
    ("approvals", "approver_id", ""),
    ("approvals", "status", "TEST_SECRET_status"),
    ("approvals", "created_at_utc", "TEST_SECRET_timestamp"),
    ("approvals", "expires_at_utc", "TEST_SECRET_timestamp"),
    ("approvals", "row_revision", -1),
]


@pytest.mark.parametrize(("table", "column", "value"), _CORRUPT_ROWS)
def test_corrupt_persisted_format_fails_with_fixed_error_without_mutation(
    store: SQLiteStore, table: str, column: str, value: object
) -> None:
    pending(store)
    connection = writer_connection(store)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    queries = {
        ("tasks", "created_at_utc"): "UPDATE tasks SET created_at_utc=?",
        ("tasks", "epoch"): "UPDATE tasks SET epoch=?",
        ("tasks", "high_watermark"): "UPDATE tasks SET high_watermark=?",
        ("tasks", "last_read_label"): "UPDATE tasks SET last_read_label=?",
        ("tasks", "unclassified"): "UPDATE tasks SET unclassified=?",
        ("tasks", "status"): "UPDATE tasks SET status=?",
        ("tasks", "security_version"): "UPDATE tasks SET security_version=?",
        ("tasks", "row_revision"): "UPDATE tasks SET row_revision=?",
        ("tasks", "profile_id"): "UPDATE tasks SET profile_id=?",
        ("requests", "call_seq"): "UPDATE requests SET call_seq=?",
        ("requests", "server_id"): "UPDATE requests SET server_id=?",
        ("requests", "tool_name"): "UPDATE requests SET tool_name=?",
        ("requests", "destination_id"): "UPDATE requests SET destination_id=?",
        ("requests", "arguments_digest"): "UPDATE requests SET arguments_digest=?",
        ("requests", "decision"): "UPDATE requests SET decision=?",
        ("requests", "reasons"): "UPDATE requests SET reasons=?",
        ("requests", "status"): "UPDATE requests SET status=?",
        ("requests", "dispatch_reserved_at_utc"): "UPDATE requests SET dispatch_reserved_at_utc=?",
        ("approvals", "snapshot_hash"): "UPDATE approvals SET snapshot_hash=?",
        ("approvals", "approver_id"): "UPDATE approvals SET approver_id=?",
        ("approvals", "status"): "UPDATE approvals SET status=?",
        ("approvals", "created_at_utc"): "UPDATE approvals SET created_at_utc=?",
        ("approvals", "expires_at_utc"): "UPDATE approvals SET expires_at_utc=?",
        ("approvals", "row_revision"): "UPDATE approvals SET row_revision=?",
    }
    connection.execute(queries[table, column], (value,))
    connection.execute("PRAGMA ignore_check_constraints=OFF")
    before, cached = database_facts(store), memory_contexts(store)
    with pytest.raises(StoreError) as error:
        store._inspect_startup()
    assert_sanitized(error.value)
    assert database_facts(store) == before
    assert memory_contexts(store) == cached
    assert not connection.in_transaction


@pytest.mark.parametrize("table", ["requests", "approvals", "escalations"])
def test_foreign_key_orphan_is_rejected_without_deleting_evidence(
    store: SQLiteStore, table: str
) -> None:
    pending(store)
    connection = writer_connection(store)
    connection.execute("PRAGMA foreign_keys=OFF")
    if table == "approvals":
        connection.execute("UPDATE approvals SET request_id='TEST_SECRET_missing'")
    elif table == "requests":
        connection.execute("UPDATE requests SET task_id='TEST_SECRET_missing'")
    else:
        connection.execute(
            "INSERT INTO escalations VALUES (?,?,?,?,?)", ("TEST_SECRET_missing", 1, "source", 0, 1)
        )
    connection.execute("PRAGMA foreign_keys=ON")
    before = database_facts(store)
    with pytest.raises(StoreError) as error:
        store._inspect_startup()
    assert_sanitized(error.value)
    assert database_facts(store) == before


@pytest.mark.parametrize(
    "corruption", ["negative_sequence", "above_watermark", "bad_chain", "empty_source"]
)
def test_invalid_escalation_history_is_not_reinterpreted_as_recovery_evidence(
    store: SQLiteStore, corruption: str
) -> None:
    connection = writer_connection(store)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute("UPDATE tasks SET high_watermark=2,last_read_label=2")
    history: list[tuple[object, ...]] = [("task-1", 4, "source-1", 0, 1)]
    if corruption == "negative_sequence":
        history = [("task-1", -1, "source-1", 0, 1)]
    elif corruption == "above_watermark":
        connection.execute("UPDATE tasks SET high_watermark=1,last_read_label=1")
        history = [("task-1", 4, "source-1", 0, 2)]
    elif corruption == "bad_chain":
        history.append(("task-1", 5, "source-2", 0, 2))
    else:
        history = [("task-1", 4, "", 0, 1)]
    connection.executemany("INSERT INTO escalations VALUES (?,?,?,?,?)", history)
    connection.execute("PRAGMA ignore_check_constraints=OFF")
    before, cached = database_facts(store), memory_contexts(store)
    with pytest.raises(StoreError) as error:
        store._inspect_startup()
    assert_sanitized(error.value)
    assert database_facts(store) == before
    assert memory_contexts(store) == cached


@pytest.mark.parametrize(
    "change",
    [
        "PRAGMA application_id=2",
        f"PRAGMA user_version={SCHEMA_VERSION + 1}",
        "ALTER TABLE requests ADD COLUMN TEST_SECRET_extra TEXT",
        "CREATE VIEW TEST_SECRET_extra AS SELECT request_id FROM requests",
    ],
)
def test_schema_tags_and_exact_current_structure_are_rechecked_and_never_repaired(
    store: SQLiteStore, change: str
) -> None:
    writer_connection(store).execute(change)
    before = database_facts(store)
    with pytest.raises(StoreError) as error:
        store._inspect_startup()
    assert_sanitized(error.value)
    assert database_facts(store) == before


def test_existing_transaction_is_preserved_instead_of_rolled_back(store: SQLiteStore) -> None:
    before = database_facts(store)
    connection = writer_connection(store)
    connection.execute("BEGIN")
    connection.execute("UPDATE tasks SET row_revision=row_revision+1")
    outstanding = database_facts(store)
    with pytest.raises(StoreError) as error:
        store._inspect_startup()
    assert_sanitized(error.value)
    assert connection.in_transaction
    assert database_facts(store) == outstanding
    connection.execute("ROLLBACK")
    assert database_facts(store) == before


def test_other_thread_can_inspect_and_closed_connection_is_sanitized(store: SQLiteStore) -> None:
    before = database_facts(store)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store._inspect_startup)
        assert future.result().tasks
    assert database_facts(store) == before
    store.close()
    with pytest.raises(StoreError) as error:
        store._inspect_startup()
    assert_sanitized(error.value)


class InspectConnection(sqlite3.Connection):
    before_history: Callable[[], None] | None = None
    fail_requests = False
    fail_commit = False
    fail_rollback = False

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        if self.before_history is not None and "SELECT task_id, at_seq" in sql:
            callback, self.before_history = self.before_history, None
            callback()
        if self.fail_requests and "FROM requests" in sql:
            raise sqlite3.OperationalError("TEST_SECRET_inventory_read")
        if self.fail_rollback and sql == "ROLLBACK":
            raise sqlite3.OperationalError("TEST_SECRET_inventory_rollback")
        result = super().execute(sql, parameters)
        if self.fail_commit and sql == "COMMIT":
            raise sqlite3.OperationalError("TEST_SECRET_inventory_commit_ack")
        return result


def inject_inspection_connection(patch: pytest.MonkeyPatch) -> Callable[..., sqlite3.Connection]:
    original = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> InspectConnection:
        return original(*args, **kwargs, factory=InspectConnection)

    patch.setattr(sqlite3, "connect", connect)
    return original


def test_sql_read_error_rolls_back_without_mutating_memory_or_saved_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inject_inspection_connection(monkeypatch)
    with SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        record(instance)
        connection = writer_connection(instance)
        assert connection.is_instance(InspectConnection)
        before, cached = database_facts(instance), memory_contexts(instance)
        connection.fail_requests = True
        with pytest.raises(StoreError) as error:
            instance._inspect_startup()
        assert_sanitized(error.value)
        connection.fail_requests = False
        assert database_facts(instance) == before
        assert memory_contexts(instance) == cached
        assert not connection.in_transaction


@pytest.mark.parametrize("fault", ["commit_ack", "rollback"])
def test_uncertain_read_transaction_discards_context_without_granting_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    path = tmp_path / "guard.db"
    inject_inspection_connection(monkeypatch)
    with SQLiteStore(path, audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        record(instance)
        before = database_facts(instance)
        connection = writer_connection(instance)
        assert connection.is_instance(InspectConnection)
        if fault == "commit_ack":
            connection.fail_commit = True
        else:
            connection.fail_requests = connection.fail_rollback = True
        with pytest.raises(StoreError) as error:
            instance._inspect_startup()
        assert_sanitized(error.value)
        assert instance._connection is None
        assert memory_contexts(instance) == ({}, {}, {}, {})
    with SQLiteStore(path) as instance:
        assert database_facts(instance) == before
        assert instance._inspect_startup().requests[0].status is RequestStatus.DECIDED
        with pytest.raises(StoreError):
            reserve(instance)


def test_tasks_requests_and_escalation_maxima_share_one_wal_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "guard.db"
    original_connect = inject_inspection_connection(monkeypatch)
    with SQLiteStore(path, audit_id_factory=lambda: "event-1") as instance:
        asyncio.run(instance.create_task(task(), created_at_utc=NOW))
        record(instance)
        connection = writer_connection(instance)
        assert connection.is_instance(InspectConnection)

        def other_writer() -> None:
            with original_connect(path) as other:
                other.execute(
                    "UPDATE tasks SET high_watermark=1,last_read_label=1,"
                    "security_version=8,row_revision=12"
                )
                other.execute(
                    "INSERT INTO escalations VALUES (?,?,?,?,?)", ("task-1", 17, "source", 0, 1)
                )
                other.execute(
                    "INSERT INTO requests SELECT 'request-17',task_id,17,server_id,tool_name,"
                    "destination_id,arguments_digest,decision,reasons,status,"
                    "dispatch_reserved_at_utc,epoch,kind,row_revision,legacy_source "
                    "FROM requests WHERE request_id='request-1'"
                )

        connection.before_history = other_writer
        first = instance._inspect_startup()
        assert first.tasks[0].state == task()
        assert first.tasks[0].request_sequence_max == first.tasks[0].persisted_sequence_max == 1
        assert first.tasks[0].escalation_sequence_max is None
        assert len(first.requests) == 1
        changed = instance._inspect_startup()
        assert changed.tasks[0].state.high_watermark is Label.INTERNAL
        assert changed.tasks[0].state.row_revision == 12
        assert changed.tasks[0].request_sequence_max == 17
        assert changed.tasks[0].escalation_sequence_max == 17
        assert changed.tasks[0].persisted_sequence_max == 17
        assert len(changed.requests) == 2
        assert database_facts(instance)["application"] == APPLICATION_ID
