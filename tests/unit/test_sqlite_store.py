"""B6 첫 단계의 실제 파일 SQLite 단위 검사(I4/I6/I9, S03 단위 부분).

테스트의 직접 SQL은 스키마 관찰·오염/장애 주입용이다. C의 독립 계약/프로세스
중단·승인 소비·wire/공개 검증을 대신하지 않는다(T07/T11/T19의 저장소 단위 기반).
"""

import asyncio
import sqlite3
import traceback
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Escalation, TaskState
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from guard_app.storage.sqlite_store import APPLICATION_ID, SCHEMA_VERSION
from tests.unit.sqlite_writer_test_support import writer_connection

NOW = datetime(2026, 10, 13, 12, 34, 56, 123456, tzinfo=UTC)
TABLES = {
    "tasks",
    "escalations",
    "requests",
    "approvals",
    "audit_events",
    "registrations",
    "registration_tools",
    "scan_reports",
}


def state(**updates: object) -> TaskState:
    values: dict[str, object] = {
        "task_id": "task-1",
        "epoch": 1,
        "profile_id": "default",
        "policy_version": "v1",
    }
    values.update(updates)
    return TaskState.model_validate(values)


def create(store: SQLiteStore, value: TaskState, at: datetime = NOW) -> None:
    asyncio.run(store.create_task(value, created_at_utc=at))


def load(store: SQLiteStore, identifier: str = "task-1") -> TaskState:
    return asyncio.run(store.load_task(identifier))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    with SQLiteStore(tmp_path / "guard.db") as instance:
        yield instance


def assert_sanitized(error: StoreError) -> None:
    assert error.args == (error.reason.value,)
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert "TEST_SECRET" not in rendered
    assert "sqlite3.OperationalError:" not in rendered
    assert "sqlite3.IntegrityError:" not in rendered


@pytest.mark.parametrize("timeout", [0, 1, 17, 1000, 2500])
def test_file_settings_are_verified(tmp_path: Path, timeout: int) -> None:
    with SQLiteStore(tmp_path / "guard.db", busy_timeout_ms=timeout) as instance:
        connection = writer_connection(instance)
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == timeout
        assert connection.isolation_level is None
        assert connection.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
        assert not connection.in_transaction


def test_initial_migration_creates_only_design_tables_and_tags(store: SQLiteStore) -> None:
    connection = writer_connection(store)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    rows = connection.execute("PRAGMA table_list").fetchall()
    assert {row[1] for row in rows if not row[1].startswith("sqlite_")} == TABLES
    assert all(row[5] == 1 for row in rows if row[1] in TABLES)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("status", list(TaskStatus))
def test_task_roundtrip_preserves_status_versions_and_history(
    store: SQLiteStore, status: TaskStatus
) -> None:
    original = state(
        status=status,
        epoch=7,
        security_version=9,
        row_revision=11,
        high_watermark=Label.CONFIDENTIAL,
        last_read_label=Label.INTERNAL,
        unclassified=True,
        escalations=(
            Escalation(
                at_seq=2, source_id="source-1", from_label=Label.PUBLIC, to_label=Label.INTERNAL
            ),
            Escalation(
                at_seq=5,
                source_id="source-2",
                from_label=Label.INTERNAL,
                to_label=Label.CONFIDENTIAL,
            ),
        ),
    )
    create(store, original)
    assert load(store) == original
    assert not writer_connection(store).in_transaction
    assert (
        writer_connection(store).execute("SELECT created_at_utc FROM tasks").fetchone()[0]
        == "2026-10-13T12:34:56.123456+00:00"
    )


def test_reopen_is_idempotent_and_does_not_reset_task(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    original = state(
        status=TaskStatus.HALTED,
        high_watermark=Label.CONFIDENTIAL,
        unclassified=True,
        epoch=8,
        row_revision=13,
    )
    for index in range(3):
        with SQLiteStore(path) as instance:
            if index == 0:
                create(instance, original)
            assert load(instance) == original
            assert (
                writer_connection(instance).execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
            )


def test_empty_existing_database_can_migrate(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    sqlite3.connect(path).close()
    with SQLiteStore(path) as instance:
        create(instance, state())
        assert load(instance) == state()


@pytest.mark.parametrize(
    "statement",
    [
        f"PRAGMA user_version={SCHEMA_VERSION + 1}",
        "PRAGMA user_version=-1",
        "PRAGMA application_id=1",
        "CREATE TABLE unrelated (id TEXT)",
        "CREATE TABLE sqliteXhidden (id TEXT)",
    ],
)
def test_unknown_database_is_not_adopted_or_overwritten(tmp_path: Path, statement: str) -> None:
    path = tmp_path / "guard.db"
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
        before = connection.execute("SELECT name,sql FROM sqlite_schema ORDER BY name").fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()
        application = connection.execute("PRAGMA application_id").fetchone()
    with pytest.raises(StoreError) as error:
        SQLiteStore(path)
    assert_sanitized(error.value)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT name,sql FROM sqlite_schema ORDER BY name").fetchall()
            == before
        )
        assert connection.execute("PRAGMA user_version").fetchone() == version
        assert connection.execute("PRAGMA application_id").fetchone() == application


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE tasks ADD COLUMN raw_body TEXT",
        "DROP TABLE escalations",
        f"PRAGMA user_version={SCHEMA_VERSION + 1}",
        "PRAGMA application_id=0",
        "CREATE TABLE sqliteXhidden (id TEXT)",
    ],
)
def test_modified_current_schema_or_tag_is_rejected(tmp_path: Path, statement: str) -> None:
    path = tmp_path / "guard.db"
    with SQLiteStore(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
    with pytest.raises(StoreError) as error:
        SQLiteStore(path)
    assert_sanitized(error.value)


@pytest.mark.parametrize("timeout", [-1, True, 1.5, "1000", 1 << 31])
def test_bad_timeout_does_not_create_database(tmp_path: Path, timeout: object) -> None:
    path = tmp_path / "guard.db"
    with pytest.raises(StoreError) as error:
        SQLiteStore(path, busy_timeout_ms=timeout)  # type: ignore[arg-type]
    assert_sanitized(error.value)
    assert not path.exists()


@pytest.mark.parametrize("path", ["", ":memory:"])
def test_requires_file_database(path: str) -> None:
    with pytest.raises(StoreError) as error:
        SQLiteStore(path)
    assert_sanitized(error.value)


def test_connect_failure_does_not_expose_path(tmp_path: Path) -> None:
    path = tmp_path / "TEST_SECRET_missing" / "guard.db"
    with pytest.raises(StoreError) as error:
        SQLiteStore(path)
    assert_sanitized(error.value)
    assert not path.parent.exists()


def test_missing_task_is_state_missing_without_creating_default(store: SQLiteStore) -> None:
    with pytest.raises(StoreError) as error:
        load(store, "absent")
    assert error.value.reason is Reason.STATE_MISSING
    assert_sanitized(error.value)
    assert writer_connection(store).execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    assert not writer_connection(store).in_transaction


def test_duplicate_id_keeps_original_state(store: SQLiteStore) -> None:
    original = state(high_watermark=Label.CONFIDENTIAL, status=TaskStatus.HALTED)
    create(store, original)
    with pytest.raises(StoreError) as error:
        create(store, state())
    assert_sanitized(error.value)
    assert load(store) == original


@pytest.mark.parametrize("identifier", ["a'); DROP TABLE tasks; --", 'a"; SELECT 1; --', "한글🙂"])
def test_identifiers_use_binding_and_tasks_remain_isolated(
    store: SQLiteStore, identifier: str
) -> None:
    original = state(task_id=identifier)
    other = state(task_id="other", status=TaskStatus.CLOSED)
    create(store, original)
    create(store, other)
    assert load(store, identifier) == original
    assert load(store, "other") == other
    assert writer_connection(store).execute("SELECT count(*) FROM tasks").fetchone()[0] == 2


@pytest.mark.parametrize(
    "at", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=9))), "TEST_SECRET"]
)
def test_non_utc_creation_time_fails_before_writes(store: SQLiteStore, at: datetime) -> None:
    with pytest.raises(StoreError) as error:
        create(store, state(), at)
    assert_sanitized(error.value)
    assert writer_connection(store).execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epoch", True),
        ("epoch", -1),
        ("epoch", 1 << 63),
        ("high_watermark", 99),
        ("status", "TEST_SECRET"),
        ("task_id", ""),
        ("profile_id", "\ud800"),
        ("row_revision", -1),
        ("unclassified", 1),
    ],
)
def test_constructed_invalid_model_cannot_bypass_validation(
    store: SQLiteStore, field: str, value: object
) -> None:
    fields = state().model_dump(mode="python")
    fields[field] = value
    invalid = TaskState.model_construct(**fields)
    with pytest.raises(StoreError) as error:
        create(store, invalid)
    assert_sanitized(error.value)
    assert writer_connection(store).execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("identifier", ["", 1, True, "\ud800"])
def test_bad_lookup_id_is_not_missing_task(store: SQLiteStore, identifier: str) -> None:
    with pytest.raises(StoreError) as error:
        load(store, identifier)
    assert error.value.reason is Reason.INTERNAL_ERROR
    assert_sanitized(error.value)


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        ("UPDATE tasks SET high_watermark=?", (99,)),
        ("UPDATE tasks SET epoch=?", (-1,)),
        ("UPDATE tasks SET unclassified=?", (2,)),
        ("UPDATE tasks SET status=?", ("TEST_SECRET",)),
        ("UPDATE tasks SET profile_id=?", ("",)),
        ("UPDATE tasks SET created_at_utc=?", ("TEST_SECRET",)),
        ("UPDATE tasks SET created_at_utc=?", ("2026-10-13T12:34:56+09:00",)),
    ],
)
def test_corrupt_rows_are_not_coerced_to_safe_defaults(
    store: SQLiteStore, statement: str, parameters: tuple[object, ...]
) -> None:
    create(store, state())
    connection = writer_connection(store)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(statement, parameters)
    connection.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(StoreError) as error:
        load(store)
    assert_sanitized(error.value)
    assert not connection.in_transaction


def test_create_rolls_back_task_if_escalation_insert_fails(store: SQLiteStore) -> None:
    connection = writer_connection(store)
    connection.execute("""CREATE TRIGGER fail_history BEFORE INSERT ON escalations
        BEGIN SELECT RAISE(ABORT, 'TEST_SECRET_history'); END""")
    original = state(
        high_watermark=Label.INTERNAL,
        escalations=(
            Escalation(
                at_seq=1, source_id="source", from_label=Label.PUBLIC, to_label=Label.INTERNAL
            ),
        ),
    )
    with pytest.raises(StoreError) as error:
        create(store, original)
    assert_sanitized(error.value)
    assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM escalations").fetchone()[0] == 0
    assert not connection.in_transaction
    connection.execute("DROP TRIGGER fail_history")
    create(store, original)
    assert load(store) == original


def test_schema_enforces_foreign_key_unique_and_strict_types(store: SQLiteStore) -> None:
    create(store, state())
    connection = writer_connection(store)
    statement = """INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,
        arguments_digest,status) VALUES (?,?,?,?,?,?,?)"""
    connection.execute(statement, ("r1", "task-1", 1, "s", "read", "a" * 64, "DECIDED"))
    for parameters in [
        ("r2", "task-1", 1, "s", "read", "a" * 64, "DECIDED"),
        ("r2", "missing", 2, "s", "read", "a" * 64, "DECIDED"),
        ("r2", "task-1", "bad", "s", "read", "a" * 64, "DECIDED"),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, parameters)
    statement = """INSERT INTO approvals (approval_id,request_id,snapshot_hash,approver_id,
        status,created_at_utc,expires_at_utc,row_revision) VALUES (?,?,?,?,?,?,?,?)"""
    parameters = ("a1", "r1", "b" * 64, "operator", "PENDING", "now", "later", 0)
    connection.execute(statement, parameters)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(statement, ("a2", *parameters[1:]))


def test_public_operations_and_close_can_run_from_other_threads(store: SQLiteStore) -> None:
    create(store, state())
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(load, store).result() == state()
        executor.submit(create, store, state(task_id="other")).result()
        assert executor.submit(load, store, "other").result() == state(task_id="other")
        executor.submit(store.close).result()
    assert asyncio.run(store.wait_closed(5.0))
    with pytest.raises(StoreError) as error:
        load(store)
    assert_sanitized(error.value)


def test_close_is_idempotent_and_operations_then_fail(store: SQLiteStore) -> None:
    store.close()
    store.close()
    for operation in (lambda: load(store), lambda: create(store, state())):
        with pytest.raises(StoreError) as error:
            operation()
        assert_sanitized(error.value)


def test_non_database_file_is_rejected_without_error_chain(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    path.write_bytes(b"TEST_SECRET_not_a_database")
    with pytest.raises(StoreError) as error:
        SQLiteStore(path)
    assert_sanitized(error.value)
    assert path.read_bytes() == b"TEST_SECRET_not_a_database"


def test_busy_begin_creates_no_task_and_leaves_no_transaction(tmp_path: Path) -> None:
    path = tmp_path / "guard.db"
    with SQLiteStore(path, busy_timeout_ms=0) as instance:
        with sqlite3.connect(path, isolation_level=None) as other:
            other.execute("BEGIN IMMEDIATE")
            with pytest.raises(StoreError) as error:
                create(instance, state())
            assert_sanitized(error.value)
            assert not writer_connection(instance).in_transaction
            assert other.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
            other.execute("ROLLBACK")
        create(instance, state())
        assert load(instance) == state()


@pytest.mark.parametrize(
    "history",
    [
        (
            Escalation(
                at_seq=1, source_id="source", from_label=Label.INTERNAL, to_label=Label.INTERNAL
            ),
        ),
        (
            Escalation(
                at_seq=1, source_id="source", from_label=Label.PUBLIC, to_label=Label.CONFIDENTIAL
            ),
        ),
        (
            Escalation(
                at_seq=1, source_id="source", from_label=Label.PUBLIC, to_label=Label.INTERNAL
            ),
            Escalation(
                at_seq=1,
                source_id="source-2",
                from_label=Label.INTERNAL,
                to_label=Label.CONFIDENTIAL,
            ),
        ),
    ],
)
def test_invalid_history_cannot_be_saved(
    store: SQLiteStore, history: tuple[Escalation, ...]
) -> None:
    with pytest.raises(StoreError) as error:
        create(store, state(high_watermark=Label.INTERNAL, escalations=history))
    assert_sanitized(error.value)
    assert writer_connection(store).execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_corrupt_escalation_chain_is_not_loaded(store: SQLiteStore) -> None:
    original = state(
        high_watermark=Label.CONFIDENTIAL,
        escalations=(
            Escalation(
                at_seq=1, source_id="source-1", from_label=Label.PUBLIC, to_label=Label.INTERNAL
            ),
            Escalation(
                at_seq=2,
                source_id="source-2",
                from_label=Label.INTERNAL,
                to_label=Label.CONFIDENTIAL,
            ),
        ),
    )
    create(store, original)
    writer_connection(store).execute("UPDATE escalations SET from_label=0 WHERE at_seq=2")
    with pytest.raises(StoreError) as error:
        load(store)
    assert_sanitized(error.value)


def test_cancelled_before_start_creates_no_task(store: SQLiteStore) -> None:
    async def cancel_before_start() -> None:
        pending = asyncio.create_task(store.create_task(state(), created_at_utc=NOW))
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(cancel_before_start())
    assert writer_connection(store).execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


class FaultConnection(sqlite3.Connection):
    fail_migration = False
    fail_commit = False
    before_history: Callable[[], None] | None = None

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        if self.fail_migration and sql.startswith("CREATE TABLE requests"):
            raise sqlite3.OperationalError("TEST_SECRET_migration")
        if self.before_history is not None and "SELECT at_seq, source_id" in sql:
            callback, self.before_history = self.before_history, None
            callback()
        result = super().execute(sql, parameters)
        if self.fail_commit and sql == "COMMIT":
            raise sqlite3.OperationalError("TEST_SECRET_commit_ack")
        return result


def inject_connection(monkeypatch: pytest.MonkeyPatch, *, migration: bool = False) -> None:
    original = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> FaultConnection:
        connection = original(*args, **kwargs, factory=FaultConnection)
        connection.fail_migration = migration
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_migration_failure_rolls_back_schema_and_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "guard.db"
    with monkeypatch.context() as patch:
        inject_connection(patch, migration=True)
        with pytest.raises(StoreError) as error:
            SQLiteStore(path)
        assert_sanitized(error.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
    with SQLiteStore(path) as instance:
        create(instance, state())
        assert load(instance) == state()


def test_lost_commit_ack_discards_connection_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inject_connection(monkeypatch)
    path = tmp_path / "guard.db"
    with SQLiteStore(path) as instance:
        connection = writer_connection(instance)
        assert connection.is_instance(FaultConnection)
        connection.fail_commit = True
        with pytest.raises(StoreError) as error:
            create(instance, state())
        assert_sanitized(error.value)
        with pytest.raises(StoreError):
            load(instance)
    with SQLiteStore(path) as instance:
        assert load(instance) == state()
        assert writer_connection(instance).execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_task_and_history_read_share_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inject_connection(monkeypatch)
    path = tmp_path / "guard.db"
    with SQLiteStore(path) as instance:
        create(instance, state())
        connection = writer_connection(instance)
        assert connection.is_instance(FaultConnection)

        def change_from_other_writer() -> None:
            with sqlite3.connect(path) as other:
                other.execute(
                    "UPDATE tasks SET high_watermark=1,last_read_label=1,"
                    "security_version=1,row_revision=1"
                )
                other.execute(
                    "INSERT INTO escalations VALUES (?,?,?,?,?)", ("task-1", 1, "source", 0, 1)
                )

        connection.before_history = change_from_other_writer
        assert load(instance) == state()
        changed = load(instance)
        assert changed.high_watermark is Label.INTERNAL
        assert changed.row_revision == 1
        assert len(changed.escalations) == 1
