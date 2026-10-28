"""SEQ-03: v3의 정확한 DDL과 명시적 이관 원자성(MGV-01/02/04/05)."""

import sqlite3
from pathlib import Path

import pytest

from guard_app.domain.errors import StoreError
from guard_app.storage import SQLiteStore
from guard_app.storage.sqlite_store import (
    _SCHEMA,
    _SCHEMA_V2,
    APPLICATION_ID,
    SCHEMA_VERSION,
    _migrate_to_v3,
)
from tests.unit.test_registry_store import SELECT_ROWS, legacy_database

STAMP = "2026-10-28T00:00:00+00:00"
FP = "a" * 64


def _v2_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for _, ddl in _SCHEMA_V2:
            connection.execute(ddl)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=2")
        for name in ("task-a", "task-b", "task-empty"):
            connection.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, 1, "profile", "policy", 1, 0, 0, "running", 3, 4, STAMP),
            )
        connection.execute(
            "INSERT INTO escalations VALUES (?,?,?,?,?)", ("task-a", 11, "source", 0, 1)
        )
        connection.execute(
            "INSERT INTO escalations VALUES (?,?,?,?,?)", ("task-b", 2, "source", 0, 1)
        )
        for request_id, task_id, seq in (("r-a", "task-a", 7), ("r-b", "task-b", 12)):
            connection.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    task_id,
                    seq,
                    "server",
                    "read",
                    None,
                    FP,
                    1,
                    '["AGT_DENY"]',
                    "WAITING_APPROVAL",
                    None,
                ),
            )
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("approval", "r-a", FP, "operator", "PENDING", STAMP, STAMP, None, None, 0),
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
            ("audit", "r-a", "task-a", "DECIDED", "AGT_DENY", None, None, STAMP),
        )
        connection.execute(
            "INSERT INTO registrations VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("server", "pending", "boundary", "internal", 2, None, None, None, 1, STAMP),
        )
        connection.execute(
            "INSERT INTO registration_tools VALUES (?,?,?,?,?,?,?,?,?)",
            ("server", "read", 0, "read", None, None, "{}", FP, "reviewed"),
        )
        connection.execute(
            "INSERT INTO scan_reports VALUES (?,?,?,?,?)", ("scan", "server", "{}", STAMP, STAMP)
        )
        connection.execute("UPDATE registrations SET last_scan_id='scan' WHERE server_id='server'")


def _facts(connection: sqlite3.Connection, names: tuple[str, ...]) -> dict[str, list[tuple]]:
    return {name: connection.execute(SELECT_ROWS[name]).fetchall() for name in names}


def _migrate(path: Path, version: int) -> None:
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _migrate_to_v3(connection, version)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_new_v3_ddl_is_exact_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    with SQLiteStore(path) as store:
        actual = store._run_on_writer(
            lambda connection: dict(
                connection.execute(
                    "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
                ).fetchall()
            )
        )
        assert actual == dict(_SCHEMA)
        assert SCHEMA_VERSION == 3
    with SQLiteStore(path) as store:
        assert store._inspect_startup().schema_version == 3


@pytest.mark.parametrize("version", [1, 2])
def test_constructor_rejects_legacy_without_changing_file_bytes(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "legacy.db"
    (legacy_database if version == 1 else _v2_database)(path)
    before = path.read_bytes()
    with pytest.raises(StoreError):
        SQLiteStore(path)
    assert path.read_bytes() == before


def test_v2_migration_preserves_rows_and_seeds_all_sequences(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    _v2_database(path)
    unchanged = (
        "tasks",
        "escalations",
        "approvals",
        "audit_events",
        "registrations",
        "registration_tools",
        "scan_reports",
    )
    with sqlite3.connect(path) as connection:
        before = _facts(connection, unchanged)
        requests = connection.execute("SELECT * FROM requests ORDER BY 1").fetchall()
    _migrate(path, 2)
    with sqlite3.connect(path) as connection:
        assert _facts(connection, unchanged) == before
        migrated = connection.execute("SELECT * FROM requests ORDER BY 1").fetchall()
        assert [row[:11] for row in migrated] == requests
        assert [row[11:] for row in migrated] == [(None, None, 0, "v2")] * 2
        assert connection.execute(
            "SELECT task_id,last_issued FROM task_call_sequences ORDER BY task_id"
        ).fetchall() == [("task-a", 11), ("task-b", 12), ("task-empty", 0)]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
            ).fetchall()
        ) == dict(_SCHEMA)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute("SELECT reason_code FROM audit_events").fetchone()[0] == "AGT_DENY"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,"
                "arguments_digest,status) VALUES (?,?,?,?,?,?,?)",
                ("new", "task-a", 13, "server", "read", FP, "DECIDED"),
            )
    # 옛 AGT reason 읽기 변환은 B2.10에서 처리한다. 재개방은 DDL을 확인한다.
    with SQLiteStore(path) as store:
        assert (
            store._run_on_writer(
                lambda connection: connection.execute("PRAGMA user_version").fetchone()[0]
            )
            == 3
        )


def test_v1_migration_seeds_existing_request_and_marks_legacy(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    legacy_database(path)
    _migrate(path, 1)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT task_id,last_issued FROM task_call_sequences"
        ).fetchall() == [("task", 3)]
        assert connection.execute(
            "SELECT epoch,kind,row_revision,legacy_source FROM requests"
        ).fetchall() == [(None, None, 0, "v1")]
        assert connection.execute("SELECT request_id FROM approvals").fetchall() == [("req",)]


@pytest.mark.parametrize("version", [1, 2])
def test_mid_migration_failure_rolls_back_bytes_and_restores_fk(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "fault.db"
    (legacy_database if version == 1 else _v2_database)(path)
    before = path.read_bytes()

    class FaultConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if statement.startswith('ALTER TABLE "requests_new"'):
                raise sqlite3.OperationalError("TEST_SECRET_migration")
            return super().execute(statement, parameters)

    with sqlite3.connect(path, isolation_level=None, factory=FaultConnection) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(StoreError) as error:
            _migrate_to_v3(connection, version)
        assert "TEST_SECRET" not in repr(error.value)
        assert not connection.in_transaction
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert path.read_bytes() == before
