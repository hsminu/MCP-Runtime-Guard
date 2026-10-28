"""C4.19와 B6.13 실제 writer 사이 취소 및 연결 고장 경계(I6/I10)."""

import asyncio
import sqlite3
import threading
from itertools import count

import pytest

from guard_app.domain.enums import RegStatus, StorePhase
from guard_app.domain.errors import StoreError
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore
from tests.unit.test_server_registry import NOW, listing, settings


async def _until(event: threading.Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():  # noqa: ASYNC110 - SQLite writer의 이벤트
            await asyncio.sleep(0.005)


async def _activate(store: SQLiteStore) -> tuple[ServerRegistry, int]:
    registry = ServerRegistry(store, clock=lambda: NOW)
    await registry.load()
    entry = await registry.register(settings(), listing())
    entry = await registry.activate(
        "docs", expected_row_revision=entry.registration.row_revision, reviewed_by="operator"
    )
    assert registry.status("docs") is RegStatus.ACTIVE
    return registry, entry.registration.row_revision


def test_registry_cancelled_after_writer_commit_stays_pending_until_reread(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    armed, committed, release = threading.Event(), threading.Event(), threading.Event()

    class CommitBarrier(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            cursor = super().execute(statement, parameters)
            if statement == "COMMIT" and armed.is_set():
                armed.clear()
                committed.set()
                if not release.wait(5):
                    raise sqlite3.OperationalError("barrier expired")
            return cursor

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=CommitBarrier)

    monkeypatch.setattr(sqlite3, "connect", connect)

    async def scenario():
        ids = count()
        store = SQLiteStore(
            tmp_path / "committed.db", audit_id_factory=lambda: f"audit-{next(ids)}"
        )
        try:
            registry, revision = await _activate(store)
            armed.set()
            operation = asyncio.create_task(registry.block("docs", expected_row_revision=revision))
            await _until(committed)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done(), "started write must finish before cancellation propagates"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 3)
            assert registry.status("docs") is RegStatus.PENDING
            saved = await store.load_registration("docs")
            assert saved is not None
            assert saved.registration.status is RegStatus.BLOCKED
            await registry.refresh("docs")
            assert registry.status("docs") is RegStatus.BLOCKED
        finally:
            release.set()
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())


def test_registry_cancelled_before_writer_start_stays_pending_until_reread(tmp_path):
    async def scenario():
        ids = count()
        store = SQLiteStore(tmp_path / "queued.db", audit_id_factory=lambda: f"audit-{next(ids)}")
        entered, release = threading.Event(), threading.Event()

        def occupy(_connection):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("barrier expired")

        hold = None
        try:
            registry, revision = await _activate(store)
            hold = asyncio.create_task(store._run_on_writer_async(occupy))
            await _until(entered)
            operation = asyncio.create_task(registry.block("docs", expected_row_revision=revision))
            await asyncio.sleep(0)
            with store._writer._condition:
                assert len(store._writer._jobs) == 1
                assert not store._writer._jobs[0].started
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 3)
            assert registry.status("docs") is RegStatus.PENDING
            release.set()
            await hold
            saved = await store.load_registration("docs")
            assert saved is not None
            assert saved.registration.status is RegStatus.ACTIVE
            await registry.refresh("docs")
            assert registry.status("docs") is RegStatus.ACTIVE
        finally:
            release.set()
            if hold is not None:
                await asyncio.gather(hold, return_exceptions=True)
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())


def test_registry_failed_commit_and_faulted_read_never_reactivate_server(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    armed = threading.Event()

    class LostCommitAck(sqlite3.Connection):
        def execute(self, statement, parameters=(), /):
            cursor = super().execute(statement, parameters)
            if statement == "COMMIT" and armed.is_set():
                armed.clear()
                raise sqlite3.OperationalError("private commit failure")
            return cursor

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=LostCommitAck)

    monkeypatch.setattr(sqlite3, "connect", connect)

    async def scenario():
        ids = count()
        store = SQLiteStore(tmp_path / "faulted.db", audit_id_factory=lambda: f"audit-{next(ids)}")
        try:
            registry, revision = await _activate(store)
            armed.set()
            with pytest.raises(StoreError):
                await registry.block("docs", expected_row_revision=revision)
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not store.execution_state().execution_enabled
            assert registry.status("docs") is RegStatus.PENDING
            with pytest.raises(StoreError):
                await registry.refresh("docs")
            assert registry.status("docs") is RegStatus.PENDING
            with pytest.raises(StoreError):
                await store.load_registration("docs")
        finally:
            store.close()
            assert await store.wait_closed(3)

    asyncio.run(scenario())
