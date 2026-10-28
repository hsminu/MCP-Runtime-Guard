"""REC-01–04: v3 회차·보고·격리 표와 이관 원자성."""

import sqlite3
from pathlib import Path

import pytest

from guard_app.domain.errors import StoreError
from guard_app.storage import SQLiteStore
from guard_app.storage.sqlite_store import _migrate_to_v3
from tests.unit.test_registry_store import legacy_database
from tests.unit.test_store_v3_migration import _v2_database

TABLES = ("store_runs", "recovery_reports", "recovery_quarantine", "task_run_owners")
COUNT_QUERIES = (
    "SELECT count(*) FROM store_runs",
    "SELECT count(*) FROM recovery_reports",
    "SELECT count(*) FROM recovery_quarantine",
    "SELECT count(*) FROM task_run_owners",
)
STAMP = "2026-10-28T00:00:00.000000+00:00"


@pytest.mark.parametrize("version", [0, 1, 2])
def test_new_and_migrated_v3_have_empty_strict_recovery_tables(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "store.db"
    if version == 0:
        with SQLiteStore(path):
            pass
    else:
        (legacy_database if version == 1 else _v2_database)(path)
        with sqlite3.connect(path, isolation_level=None) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            _migrate_to_v3(connection, version)

    with sqlite3.connect(path) as connection:
        strict = {row[1]: row[5] for row in connection.execute("PRAGMA table_list")}
        for table, query in zip(TABLES, COUNT_QUERIES, strict=True):
            assert strict[table] == 1
            assert connection.execute(query).fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        (
            "INSERT INTO store_runs VALUES (?,?,?,?,?,?)",
            ("bad", "BROKEN", STAMP, STAMP, None, None),
        ),
        (
            "INSERT INTO recovery_reports VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("run", 3, 1, STAMP, 0, 0, 0, 0, 0, 1),
        ),
        (
            "INSERT INTO recovery_reports VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("missing", 3, 1, STAMP, 0, 0, 0, 0, 0, 0),
        ),
        (
            "INSERT INTO recovery_quarantine VALUES (?,?,?,?,?,?)",
            ("r-a", "run", "task-a", "DISPATCHING", "BROKEN", STAMP),
        ),
        ("INSERT INTO task_run_owners VALUES (?,?)", ("task-a", "missing")),
    ],
)
def test_recovery_tables_enforce_state_and_relationships(
    tmp_path: Path, statement: str, parameters: tuple[object, ...]
) -> None:
    path = tmp_path / "store.db"
    _v2_database(path)
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _migrate_to_v3(connection, 2)
        connection.execute(
            "INSERT INTO store_runs VALUES (?,?,?,?,?,?)",
            ("run", "RECOVERY_COMMITTED", STAMP, STAMP, None, None),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, parameters)


@pytest.mark.parametrize("version", [1, 2])
def test_recovery_table_creation_failure_rolls_back_legacy_migration(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "store.db"
    (legacy_database if version == 1 else _v2_database)(path)
    before = path.read_bytes()

    class FaultConnection(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            if statement.startswith("CREATE TABLE recovery_quarantine"):
                raise sqlite3.OperationalError("TEST_SECRET_schema_failure")
            return super().execute(statement, parameters)

    with sqlite3.connect(path, isolation_level=None, factory=FaultConnection) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(StoreError) as raised:
            _migrate_to_v3(connection, version)
        assert "TEST_SECRET" not in repr(raised.value)
        assert not connection.in_transaction
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert path.read_bytes() == before
