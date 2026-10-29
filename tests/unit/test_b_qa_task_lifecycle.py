"""B-QA-09: I3/I5/I6/I8, T11/T20/T36/T37의 Task 수명 단위 회귀.

lock 참조·Store ACK·승인 수명을 관찰한다. SQLite는 실제 파일 DB이며,
Transport/접근 판정기는 메모리 대역이다. MCP wire·수신 부작용·C 독립 인수는 별도다.
"""

import asyncio
import gc
from datetime import timedelta
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness

TASK_COUNT = 12


async def _settle():
    for _ in range(4):
        await asyncio.sleep(0)
    gc.collect()


@pytest.mark.parametrize("real_store", [False, True])
@pytest.mark.parametrize("mode", ["idle", "cancel", "pending_cancel"])
def test_many_tasks_release_idle_locks_and_confirmed_stop_signals(mode, real_store, tmp_path):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        states = h.store.states
        template = states["task"]
        opened = None
        if real_store:
            identifiers = count(1)
            opened = SQLiteStore(
                tmp_path / "lifecycle.db",
                audit_id_factory=lambda: f"audit-{next(identifiers)}",
                utc_now=lambda: h.now,
            )
            h.guard._store = opened
            h.guard._gate._store = opened
            h.guard._approvals._store = opened
        store = opened or h.store
        control = h.guard.human_control("operator")
        try:
            for number in range(TASK_COUNT):
                task_id = f"task-{number}"
                state = template.model_copy(
                    update={
                        "task_id": task_id,
                        "high_watermark": Label.INTERNAL
                        if mode == "pending_cancel"
                        else Label.PUBLIC,
                    }
                )
                if opened:
                    await opened.create_task(state, created_at_utc=h.now)
                else:
                    states[task_id] = state
                result = await h.guard.submit(
                    task_id, _call("send" if mode == "pending_cancel" else "read")
                )
                if mode == "pending_cancel":
                    assert isinstance(result, Pending)
                else:
                    assert isinstance(result, Released)
                if mode != "idle":
                    await control.cancel(task_id)
                    assert (await store.load_task(task_id)).status is TaskStatus.HALTED
                    calls = len(h.transport.calls)
                    assert isinstance(await h.guard.submit(task_id, _call()), Denied)
                    assert len(h.transport.calls) == calls
                await _settle()
                assert not h.guard._locks
                assert not h.guard._stop_requests
            assert len(h.transport.calls) == (0 if mode == "pending_cancel" else TASK_COUNT)
            assert not h.guard._global_halt
            assert not h.guard._approvals._memory_snapshots
        finally:
            if opened:
                opened.close()

    asyncio.run(scenario())


def test_borrowed_lock_preserves_identity_and_stop_until_last_reference_is_gone():
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        borrowed = h.guard._task_lock("task")
        assert h.guard._task_lock("task") is borrowed
        await h.guard.human_control("operator").cancel("task")
        await _settle()
        assert h.guard._task_lock("task") is borrowed
        assert h.guard._stop_requests["task"].is_set()
        del borrowed
        await _settle()
        assert not h.guard._locks
        assert not h.guard._stop_requests
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["cancel", "emergency_halt"])
def test_queued_submit_and_control_share_the_lock_during_dispatch_and_halt(command):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        entered, release = asyncio.Event(), asyncio.Event()
        halt_entered, halt_release = asyncio.Event(), asyncio.Event()
        call = h.transport.call
        status = h.store.set_task_status

        async def delayed_call(req):
            entered.set()
            await release.wait()
            return await call(req)

        async def delayed_halt(*args, **kwargs):
            halt_entered.set()
            await halt_release.wait()
            return await status(*args, **kwargs)

        h.transport.call = delayed_call
        h.store.set_task_status = delayed_halt
        first = asyncio.create_task(h.guard.submit("task", _call()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            lock = h.guard._task_lock("task")
            queued = asyncio.create_task(h.guard.submit("task", _call()))
            await asyncio.sleep(0)
            control = h.guard.human_control("operator")
            stopping = asyncio.create_task(getattr(control, command)("task"))
            await asyncio.sleep(0)
            assert h.guard._stop_requests["task"].is_set()
            assert not queued.done()
            assert not stopping.done()
            release.set()
            assert isinstance(await first, Released)
            assert isinstance(await queued, Denied)
            await asyncio.wait_for(halt_entered.wait(), 1)
            assert h.guard._task_lock("task") is lock
            assert lock.locked()
            assert h.store.states["task"].status is TaskStatus.RUNNING
            assert h.guard._stop_requests["task"].is_set()
            assert len(h.transport.calls) == 1
            halt_release.set()
            assert isinstance(await stopping, Denied)
            assert h.store.states["task"].status is TaskStatus.HALTED
            del lock
            await _settle()
            assert not h.guard._locks
            assert not h.guard._stop_requests
            assert len(h.transport.calls) == 1
        finally:
            release.set()
            halt_release.set()

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["approve", "reject", "expire", "cancel", "emergency_halt"])
def test_pending_approval_keeps_approval_context_and_releases_it_after_completion(command):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        await _settle()
        assert not h.guard._locks
        assert h.guard._approvals._has_task_context("task")
        assert "task" not in h.guard._stop_requests or not h.guard._stop_requests["task"].is_set()
        assert await control.review(pending.approval_id) is not None
        if command == "expire":
            h.now += timedelta(seconds=301)
            h.monotonic += 301
        identifier = "task" if command in ("cancel", "emergency_halt") else pending.approval_id
        result = await getattr(control, command)(identifier)
        assert isinstance(result, Released if command == "approve" else Denied)
        await _settle()
        assert not h.guard._locks
        assert not h.guard._stop_requests
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._inflight_tasks
        assert len(h.transport.calls) == int(command == "approve")

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["task_id", "epoch", "profile_id", "row_revision"])
def test_invalid_halt_ack_never_retires_a_set_stop_signal(field):
    async def scenario():
        h = _Harness()
        state = h.store.states["task"]

        async def bad_status(*args, **kwargs):
            changed = state.model_copy(
                update={"status": TaskStatus.HALTED, "row_revision": state.row_revision + 1}
            )
            return changed.model_copy(
                update={
                    field: {
                        "task_id": "other-task",
                        "epoch": state.epoch + 1,
                        "profile_id": "other-profile",
                        "row_revision": state.row_revision,
                    }[field]
                }
            )

        h.store.set_task_status = bad_status
        assert isinstance(await h.guard.human_control("operator").cancel("task"), Denied)
        await _settle()
        assert not h.guard._locks
        assert h.guard._stop_requests["task"].is_set()
        assert h.store.states["task"] is state
        assert h.guard._global_halt
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_at", ["load", "status"])
def test_failed_halt_write_keeps_the_stop_signal_and_global_halt(fail_at):
    async def scenario():
        h = _Harness()
        h.store.fail_at.add(fail_at)
        assert isinstance(await h.guard.human_control("operator").cancel("task"), Denied)
        await _settle()
        assert not h.guard._locks
        assert h.guard._stop_requests["task"].is_set()
        assert h.guard._global_halt
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert not h.transport.calls

    asyncio.run(scenario())


def test_unknown_task_stop_is_preserved_without_a_confirmed_terminal_state():
    async def scenario():
        h = _Harness()
        result = await h.guard.human_control("operator").cancel("unknown")
        assert result == Denied(request_id=None, reasons=(Reason.STATE_MISSING,))
        await _settle()
        assert not h.guard._locks
        assert h.guard._stop_requests["unknown"].is_set()
        assert not h.guard._global_halt
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status", [TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED]
)
@pytest.mark.parametrize("real_store", [False, True])
def test_confirmed_terminal_task_cannot_resume_after_stop_resources_are_released(
    status, real_store, tmp_path
):
    async def scenario():
        h = _Harness()
        state = h.store.states["task"].model_copy(update={"status": status})
        h.store.states["task"] = state
        opened = None
        if real_store:
            identifiers = count(1)
            opened = SQLiteStore(
                tmp_path / "terminal.db",
                audit_id_factory=lambda: f"audit-{next(identifiers)}",
                utc_now=lambda: h.now,
            )
            await opened.create_task(state, created_at_utc=h.now)
            h.guard._store = opened
            h.guard._approvals._store = opened
        store = opened or h.store
        try:
            await h.guard.human_control("operator").cancel("task")
            await _settle()
            assert not h.guard._locks
            assert not h.guard._stop_requests
            assert isinstance(await h.guard.submit("task", _call()), Denied)
            assert (await store.load_task("task")) == state
            assert not h.transport.calls
            assert not h.access.calls
            assert not h.guard._global_halt
        finally:
            if opened:
                opened.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["cancel", "emergency_halt"])
def test_cancelled_control_waiter_does_not_drop_a_lock_or_an_unconfirmed_stop(command):
    async def scenario():
        h = _Harness()
        owner = h.guard._task_lock("task")
        await owner.acquire()
        control = h.guard.human_control("operator")
        waiting = asyncio.create_task(getattr(control, command)("task"))
        await asyncio.sleep(0)
        waiting.cancel()
        await _settle()
        # 예약 전 취소는 전역 정지 대신 소유자의 lock 해제 뒤 Task를 정리한다.
        # lock을 쥔 채 기다리면 테스트 자체가 정리 경로와 서로 기다리게 된다.
        assert not waiting.done()
        assert h.guard._task_lock("task") is owner
        assert owner.locked()
        assert h.guard._stop_requests["task"].is_set()
        assert h.guard._stop_requests["task"].terminal_epoch is None
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert not h.guard._global_halt
        h.store.states["healthy"] = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        owner.release()
        assert isinstance(await asyncio.wait_for(waiting, 1), Denied)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.guard._stop_requests["task"].terminal_epoch == 1
        del owner
        await _settle()
        assert not h.guard._locks
        assert not h.guard._stop_requests
        assert not h.guard._global_halt
        assert [req.task_id for req in h.transport.calls] == ["healthy"]

    asyncio.run(scenario())


def test_real_halted_write_with_invalid_ack_waits_for_a_fresh_terminal_read(tmp_path):
    async def scenario():
        h = _Harness()
        identifiers = count(1)
        with SQLiteStore(
            tmp_path / "invalid-ack.db",
            audit_id_factory=lambda: f"audit-{next(identifiers)}",
            utc_now=lambda: h.now,
        ) as store:
            await store.create_task(h.store.states["task"], created_at_utc=h.now)
            h.guard._store = store
            h.guard._approvals._store = store
            write = store.set_task_status
            load = store.load_task
            written = False
            entered, release = asyncio.Event(), asyncio.Event()

            async def invalid_ack(*args, **kwargs):
                nonlocal written
                committed = await write(*args, **kwargs)
                written = True
                return committed.model_copy(update={"task_id": "other-task"})

            async def delayed_terminal_read(task_id):
                state = await load(task_id)
                if written:
                    entered.set()
                    await release.wait()
                return state

            store.set_task_status = invalid_ack
            store.load_task = delayed_terminal_read
            stopping = asyncio.create_task(h.guard.human_control("operator").cancel("task"))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                assert (await load("task")).status is TaskStatus.HALTED
                assert h.guard._stop_requests["task"].is_set()
                assert not stopping.done()
                release.set()
                assert isinstance(await stopping, Denied)
                await _settle()
                assert not h.guard._locks
                assert not h.guard._stop_requests
                assert h.guard._global_halt
                assert not h.transport.calls
            finally:
                release.set()

    asyncio.run(scenario())


def test_new_stop_does_not_use_a_previous_commands_terminal_ack():
    async def scenario():
        h = _Harness()
        borrowed = h.guard._task_lock("task")
        control = h.guard.human_control("operator")
        await control.cancel("task")
        assert h.store.states["task"].status is TaskStatus.HALTED
        h.store.fail_at.add("load")
        assert isinstance(await control.cancel("task"), Denied)
        del borrowed
        await _settle()
        assert not h.guard._locks
        assert h.guard._stop_requests["task"].is_set()
        assert h.guard._global_halt
        assert not h.transport.calls

    asyncio.run(scenario())
