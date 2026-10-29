"""Immediate startup must preserve rejected WAL files and checkpoint accepted stores."""

import subprocess
import sys

import pytest

from guard_app.domain.errors import StoreError
from guard_app.storage import SQLiteStore
from tests.unit.test_registry_store import legacy_database
from tests.unit.test_store_v3_migration import _v2_database


def _leave_uncheckpointed_wal(path, *, unsupported):
    # Exit without closing the SQLite connection to simulate an interrupted process.
    script = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute('PRAGMA journal_mode=WAL')
connection.execute("UPDATE tasks SET profile_id='uncheckpointed'")
if sys.argv[2] == '1':
    connection.execute('PRAGMA user_version=99')
os._exit(0)
"""
    subprocess.run(  # noqa: S603 - fixed script and the current interpreter
        [sys.executable, "-c", script, str(path), str(int(unsupported))], check=True
    )


@pytest.mark.parametrize("version", [1, 2, 99])
def test_immediate_rejection_preserves_main_and_existing_wal(tmp_path, version):
    path = tmp_path / "existing.db"
    (legacy_database if version == 1 else _v2_database)(path)
    _leave_uncheckpointed_wal(path, unsupported=version == 99)
    wal = path.with_name(path.name + "-wal")
    before = path.read_bytes(), wal.read_bytes()
    assert before[1]

    with pytest.raises(StoreError):
        SQLiteStore(path)

    assert path.read_bytes() == before[0]
    assert wal.exists()
    assert wal.read_bytes() == before[1]


def test_accepted_immediate_store_still_checkpoints_on_close(tmp_path):
    path = tmp_path / "accepted.db"
    wal = path.with_name(path.name + "-wal")
    store = SQLiteStore(path)
    try:
        changed = store._run_on_writer(
            lambda connection: (
                connection.execute(
                    "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "task",
                        1,
                        "profile",
                        "policy",
                        0,
                        0,
                        0,
                        "ready",
                        0,
                        0,
                        "2026-10-29T00:00:00.000000+00:00",
                    ),
                ).rowcount
            )
        )
        assert changed == 1
        assert wal.exists()
        assert wal.stat().st_size > 0
    finally:
        store.close()
        assert store._writer.wait_closed_sync(5)
    assert not wal.exists() or wal.stat().st_size == 0
