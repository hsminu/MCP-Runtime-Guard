"""B6.8 explicit 시작은 준비 전 실행을 닫고 기존 파일을 보존한다."""

import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest

from guard_app.domain.enums import StorePhase
from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.storage import SQLiteStore
from tests.unit.test_registry_store import legacy_database
from tests.unit.test_store_v3_migration import _v2_database

NOW = datetime(2026, 10, 28, tzinfo=UTC)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_explicit_open_preserves_existing_file_bytes_and_journal_mode(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    if version == 1:
        legacy_database(path)
    elif version == 2:
        _v2_database(path)
    else:
        with SQLiteStore(path):
            pass
    before = path.read_bytes()
    with sqlite3.connect(path) as connection:
        old_journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
    before = path.read_bytes()
    store = SQLiteStore(path, startup="explicit")
    try:
        state = store.execution_state()
        assert state.phase is StorePhase.MIGRATING
        assert not state.execution_enabled
        assert not state.requires_restart
        assert (
            store._run_on_writer(
                lambda connection: connection.execute("PRAGMA user_version").fetchone()[0]
            )
            == version
        )
        assert (
            store._run_on_writer(
                lambda connection: connection.execute("PRAGMA journal_mode").fetchone()[0]
            )
            == old_journal
        )
        assert path.read_bytes() == before
    finally:
        store.close()
        assert asyncio.run(store.wait_closed(5))
    assert path.read_bytes() == before


def test_explicit_open_of_empty_database_leaves_schema_uncreated(tmp_path):
    path = tmp_path / "new.db"
    store = SQLiteStore(path, startup="explicit")
    try:
        assert store.execution_state().phase is StorePhase.MIGRATING
        version, application, names = store._run_on_writer(
            lambda connection: (
                connection.execute("PRAGMA user_version").fetchone()[0],
                connection.execute("PRAGMA application_id").fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
                ).fetchall(),
            )
        )
        assert (version, application, names) == (0, 0, [])
    finally:
        store.close()
        assert asyncio.run(store.wait_closed(5))


@pytest.mark.parametrize("phase", [StorePhase.MIGRATING, StorePhase.RECOVERING])
def test_explicit_unready_phases_reject_all_store_and_registry_operations(tmp_path, phase):
    async def scenario():
        store = SQLiteStore(tmp_path / "gate.db", startup="explicit")
        try:
            if phase is StorePhase.RECOVERING:
                # 27a prepare_schema will enter this phase; inject it here to test the gate alone.
                with store._state_lock:
                    store._state = store._state.model_copy(update={"phase": phase})
            before = store.execution_state()
            calls = (
                lambda: store.load_task("task"),
                lambda: store.create_task(
                    TaskState(task_id="task", epoch=1, profile_id="p", policy_version="v"),
                    created_at_utc=NOW,
                ),
                lambda: store.list_tasks(),
                lambda: store.list_registrations(),
                lambda: store.load_registration("server"),
                lambda: store.allocate_call_seq("task"),
            )
            for operation in calls:
                with pytest.raises(StoreError):
                    await operation()
            assert store.execution_state() == before
        finally:
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("startup", [None, False, 1, "", "EXPLICIT", "other"])
def test_invalid_startup_is_rejected_without_creating_database(tmp_path, startup):
    path = tmp_path / "PRIVATE_DB_FILENAME.db"
    with pytest.raises(StoreError) as error:
        SQLiteStore(path, startup=startup)
    assert "PRIVATE_DB_FILENAME" not in repr(error.value)
    assert not path.exists()


def test_explicit_fault_and_close_keep_execution_disabled(tmp_path):
    from guard_app.domain.reasons import Reason

    store = SQLiteStore(tmp_path / "close.db", startup="explicit")
    store.halt_execution(Reason.INTERNAL_ERROR)
    before = store.execution_state()
    assert before.phase is StorePhase.FAULTED
    assert not before.execution_enabled
    store.close()
    store.close()
    assert asyncio.run(store.wait_closed(5))
    assert store.execution_state() == before
