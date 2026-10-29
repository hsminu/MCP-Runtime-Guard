"""A143: real writer shutdown ordering (I6, STOP-01, REC-01; T41/T42 stop path)."""

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime
from itertools import count

import pytest

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import SessionOptions
from guard_app.services.guard import SystemControlError
from guard_app.services.product_assembly import assemble_guard
from guard_app.services.runner import CodexAuthenticationError
from guard_app.services.session_runtime import SessionRuntime
from guard_app.storage import SQLiteStore
from tests.unit.test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs, spec
from tests.unit.test_session_runtime import Session, synthetic_preparation


@pytest.mark.asyncio
async def test_shutdown_records_session_stop_before_real_writer_closes(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    armed, committed, release = threading.Event(), threading.Event(), threading.Event()

    # B's test_b_registry_writer_integration.py CommitBarrier is function-local.
    # Keep its post-COMMIT barrier here, without replacing Store or STOP-01.
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
    ids = count()
    path = tmp_path / "shutdown.db"
    store = SQLiteStore(path, audit_id_factory=lambda: f"audit-{next(ids)}")
    session = Session()

    async def factory(state, options, resources):
        session.task_id = state.task_id
        return session

    runtime = assembled = hold = timer = None
    try:
        async with asyncio.timeout(8):
            assembled = await assemble_guard(
                store,
                inputs(
                    Peer([READ_DEFINITION]), Registry(spec(READ_DEFINITION).fingerprint), document()
                ),
            )
            runtime = SessionRuntime(
                store,
                owned_store=store,
                assembled=assembled,
                preparation=synthetic_preparation(),
                session_factory=factory,
            )
            created = await runtime.create_session(SessionOptions())
            state = await store.load_task(created.task_id)
            assert state.status is TaskStatus.RUNNING
            armed.set()
            hold = asyncio.create_task(
                store.create_task(
                    state.model_copy(update={"task_id": "unrelated-task"}),
                    created_at_utc=datetime.now(UTC),
                )
            )
            assert await asyncio.to_thread(committed.wait, 3)
            session.error = CodexAuthenticationError()
            session.release.set()
            runner = runtime.runner_for_task(created.task_id)
            runner.add_user_message("one")
            assert (await runner.advance()).status == "halted"
            records = tuple(runtime._records._operations)
            assert len(records) == 1
            assert not records[0].done()
            # The real stop's read is queued behind the unrelated committed write.
            with store._writer._condition:
                assert len(store._writer._jobs) == 1
                assert not store._writer._jobs[0].started
            assert not release.is_set()
            timer = threading.Timer(0.2, release.set)
            timer.start()
            await runtime.close()
            await hold
            assert await store.wait_closed(3)
            assert records[0].done()
            assert not records[0].cancelled()
            error = records[0].exception()
            reopened = SQLiteStore(path, audit_id_factory=lambda: f"audit-{next(ids)}")
            try:
                saved = await reopened.load_task(created.task_id)
            finally:
                reopened.close()
                assert await reopened.wait_closed(3)
            # Report both regression signals, even when BackgroundWork consumed the error.
            assert (isinstance(error, SystemControlError), saved.status) == (
                False,
                TaskStatus.HALTED,
            )
            assert error is None
    finally:
        release.set()
        if timer is not None:
            timer.cancel()
            await asyncio.to_thread(timer.join, 1)
            assert not timer.is_alive()
        try:
            async with asyncio.timeout(8):
                if hold is not None:
                    await asyncio.gather(hold, return_exceptions=True)
                if runtime is not None:
                    await runtime.close()
                elif assembled is not None:
                    await assembled.aclose()
        finally:
            store.close()
            assert await store.wait_closed(3)
