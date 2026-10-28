"""B6.13 writer 스레드 소유와 Store 작업 취소 경계(I6/I9)."""

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.storage import SQLiteStore

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def _task(identifier: str) -> TaskState:
    return TaskState(task_id=identifier, epoch=1, profile_id="p", policy_version="v")


async def _until(event: threading.Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():  # noqa: ASYNC110 - writer의 threading.Event 신호
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("failure", [RuntimeError, OSError])
def test_writer_start_failure_is_sanitized(tmp_path, monkeypatch, failure):
    def fail_start(_thread):
        raise failure("TEST_SECRET_thread_start")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(StoreError) as captured:
        SQLiteStore(tmp_path / "TEST_SECRET_path.db")
    assert "TEST_SECRET" not in str(captured.value)
    assert "TEST_SECRET" not in repr(captured.value)
    assert captured.value.__suppress_context__


def test_sql_is_owned_by_one_daemon_writer_without_executor(tmp_path, monkeypatch):
    observed = []
    original = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            thread = threading.current_thread()
            observed.append((thread.ident, thread.name, thread.daemon))
            return super().execute(sql, parameters)

        def executemany(self, sql, parameters, /):
            thread = threading.current_thread()
            observed.append((thread.ident, thread.name, thread.daemon))
            return super().executemany(sql, parameters)

        def close(self):
            thread = threading.current_thread()
            observed.append((thread.ident, thread.name, thread.daemon))
            return super().close()

    def connect(*args, **kwargs):
        assert kwargs.get("check_same_thread") is True
        thread = threading.current_thread()
        observed.append((thread.ident, thread.name, thread.daemon))
        return original(*args, **kwargs, factory=ObservedConnection)

    def forbidden(*args, **kwargs):
        raise AssertionError("executor is forbidden")

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(asyncio, "to_thread", forbidden)
    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", forbidden)

    async def scenario():
        store = SQLiteStore(tmp_path / "threads.db")
        try:
            await store.create_task(_task("one"), created_at_utc=NOW)
            assert (await store.load_task("one")).task_id == "one"
            assert len(await store.list_tasks()) == 1
        finally:
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())
    assert observed
    assert len({item[0] for item in observed}) == 1
    assert observed[0][0] != threading.get_ident()
    assert all(name == "sqlite-store-writer" and daemon for _, name, daemon in observed)


def test_cancelled_queued_write_never_starts(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "queued.db")
        entered, release = threading.Event(), threading.Event()

        def hold(_connection):
            entered.set()
            assert release.wait(3)

        running = asyncio.create_task(store._run_on_writer_async(hold))
        try:
            await _until(entered)
            queued = asyncio.create_task(store.create_task(_task("queued"), created_at_utc=NOW))
            await asyncio.sleep(0)
            with store._writer._condition:
                assert len(store._writer._jobs) == 1
                assert not store._writer._jobs[0].started
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(queued, 1)
            release.set()
            await running
            assert await store.list_tasks() == ()
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["write", "read"])
def test_cancelled_started_operation_respects_its_write_boundary(tmp_path, monkeypatch, operation):
    original = sqlite3.connect
    entered, release = threading.Event(), threading.Event()
    enabled = threading.Event()

    class BlockingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            is_write = sql.startswith("INSERT INTO tasks")
            is_read = sql.startswith("SELECT task_id, epoch") and "FROM tasks" in sql
            if enabled.is_set() and (is_write if operation == "write" else is_read):
                enabled.clear()
                entered.set()
                assert release.wait(3)
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=BlockingConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)

    async def scenario():
        store = SQLiteStore(tmp_path / "started.db")
        try:
            if operation == "read":
                await store.create_task(_task("one"), created_at_utc=NOW)
            enabled.set()
            work = asyncio.create_task(
                store.create_task(_task("one"), created_at_utc=NOW)
                if operation == "write"
                else store.load_task("one")
            )
            await _until(entered)
            work.cancel()
            if operation == "write":
                await asyncio.sleep(0.05)
                assert not work.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(work, 1)
                assert (await store.load_task("one")).task_id == "one"
            else:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(work, 1)
                release.set()
                assert (await store.load_task("one")).task_id == "one"
        finally:
            release.set()
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())
