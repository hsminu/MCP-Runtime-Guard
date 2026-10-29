"""Guard 전역 정지가 SQLite 권한을 닫아도 정리 작업은 허용한다(SEQ-02)."""

import asyncio
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, TaskIdentity
from guard_app.domain.enums import SessionEvent, StorePhase, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness


def test_guard_store_failure_halts_real_store_once_and_preserves_cleanup(tmp_path):
    async def scenario():
        h = _Harness()
        ids = count(1)
        store = SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(ids)}")
        try:
            await store.create_task(h.store.states["task"], created_at_utc=h.now)
            observed = []

            def on_halt(reason):
                observed.append(reason)
                store.halt_execution(reason)

            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: f"request-{next(h.ids)}",
                approval_id_factory=lambda: f"approval-{next(h.ids)}",
                next_call_seq=h.sequence,
                failure_audit=h.failure_audit,
                utc_now=lambda: h.now,
                monotonic=lambda: h.monotonic,
                on_global_halt=on_halt,
            )
            original_load = store._load_task

            def fail_once(task_id):
                store._load_task = original_load
                raise StoreError(Reason.STATE_COMMIT_FAILED)

            store._load_task = fail_once
            result = await guard.submit("task", _call())
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.STATE_COMMIT_FAILED,)
            assert observed == [Reason.STATE_COMMIT_FAILED]
            state = store.execution_state()
            assert state.phase is StorePhase.FAULTED
            assert not state.execution_enabled
            assert state.reason is Reason.STATE_COMMIT_FAILED
            assert state.commit_outcome is None
            assert h.transport.calls == []
            with pytest.raises(StoreError) as error:
                await store.create_task(h.store.states["task"], created_at_utc=h.now)
            assert error.value.reason is Reason.STATE_COMMIT_FAILED
            current = await store.load_task("task")
            halted = await store.set_task_status(
                "task",
                TaskStatus.HALTED,
                expected_row_revision=current.row_revision,
                at_utc=h.now,
                reason=Reason.TASK_NOT_RUNNING,
            )
            assert halted.status is TaskStatus.HALTED
            assert isinstance(await guard.submit("task", _call()), Denied)
            await guard.aclose()
            assert observed == [Reason.STATE_COMMIT_FAILED]
            assert store.execution_state().reason is Reason.STATE_COMMIT_FAILED
        finally:
            store.close()
            assert await store.wait_closed(5.0)

    asyncio.run(scenario())


def test_guard_normal_close_halts_store_but_allows_cleanup(tmp_path):
    async def scenario():
        h = _Harness()
        ids = count(1)
        store = SQLiteStore(tmp_path / "close.db", audit_id_factory=lambda: f"audit-{next(ids)}")
        try:
            task = h.store.states["task"]
            await store.create_task(task, created_at_utc=h.now)
            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: f"request-{next(h.ids)}",
                approval_id_factory=lambda: f"approval-{next(h.ids)}",
                next_call_seq=h.sequence,
                failure_audit=h.failure_audit,
                utc_now=lambda: h.now,
                monotonic=lambda: h.monotonic,
                on_global_halt=store.halt_execution,
            )
            await guard.aclose()
            state = store.execution_state()
            assert state.phase is StorePhase.FAULTED
            assert not state.execution_enabled
            assert state.requires_restart
            assert state.reason is Reason.GUARD_CLOSED
            assert isinstance(await guard.submit("task", _call()), Denied)
            assert h.transport.calls == []
            with pytest.raises(StoreError):
                await store.create_task(task, created_at_utc=h.now)
            current = await store.load_task("task")
            halted = await store.set_task_status(
                "task",
                TaskStatus.HALTED,
                expected_row_revision=current.row_revision,
                at_utc=h.now,
                reason=Reason.TASK_NOT_RUNNING,
            )
            assert halted.status is TaskStatus.HALTED
            await store.record_session_event(
                TaskIdentity(task_id="task", epoch=task.epoch),
                SessionEvent.REQUEST_USER_INPUT,
                at_utc=h.now,
            )
        finally:
            store.close()
            assert await store.wait_closed(5.0)

    asyncio.run(scenario())
