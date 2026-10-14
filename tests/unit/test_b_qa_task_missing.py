"""B-QA-02: I2/I6/I7/T20 단위 검증. 대역 호출은 실제 wire 계수가 아니다."""

import asyncio
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.storage.sqlite_store import SQLiteStore
from tests.unit.test_guard import _call, _Harness


class _CorruptLookupError(StoreError):
    """정상 부재를 뜻하지 않는 저장소 하위 오류."""


async def _command(h, command, task_id):
    if command == "submit":
        return await h.guard.submit(task_id, _call())
    return await getattr(h.guard.human_control("operator"), command)(task_id)


@pytest.mark.parametrize("command", ["submit", "cancel", "emergency_halt"])
@pytest.mark.parametrize("real_store", [False, True])
def test_initial_missing_task_does_not_halt_healthy_task_t20(command, real_store, tmp_path):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        opened = None
        if real_store:
            audit_ids = count(1)
            opened = SQLiteStore(
                tmp_path / "qa-missing.db",
                audit_id_factory=lambda: f"audit-{next(audit_ids)}",
                utc_now=lambda: h.now,
            )
            await opened.create_task(h.store.states["task"], created_at_utc=h.now)
            h.store = opened
            h.guard._store = opened
            h.guard._approvals._store = opened
            h.guard._gate._store = opened
        try:
            result = await _command(h, command, "missing")
            assert result == Denied(request_id=None, reasons=(Reason.STATE_MISSING,))
            assert not h.transport.calls
            assert not h.access.calls
            assert not h.audit
            state = await h.store.load_task("task")
            assert state.status is TaskStatus.RUNNING

            released = await h.guard.submit("task", _call())
            assert isinstance(released, Released)
            assert len(h.transport.calls) == 1
            assert len(h.access.calls) == 1
        finally:
            if opened is not None:
                opened.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["submit", "cancel", "emergency_halt"])
@pytest.mark.parametrize("kind", ["io", "foreign", "broken", "corrupt_missing"])
def test_initial_lookup_corruption_still_globally_halts_t20(command, kind):
    async def scenario():
        h = _Harness()
        healthy = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        h.store.states["healthy"] = healthy
        original = h.store.load_task

        async def bad_lookup(task_id):
            if task_id == "task":
                if kind == "io":
                    raise StoreError(Reason.STATE_COMMIT_FAILED)
                if kind == "corrupt_missing":
                    raise _CorruptLookupError(Reason.STATE_MISSING)
                if kind == "foreign":
                    return healthy
                return {"task_id": "task", "epoch": -1}
            return await original(task_id)

        h.store.load_task = bad_lookup
        assert isinstance(await _command(h, command, "task"), Denied)
        assert h.store.states["healthy"] == healthy
        assert not h.store.trace
        assert not h.transport.calls
        assert not h.access.calls
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["decision", "reserve", "sequence"])
def test_state_missing_after_initial_lookup_keeps_global_halt_t20(operation):
    async def scenario():
        h = _Harness()
        healthy = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        h.store.states["healthy"] = healthy

        async def missing(*args, **kwargs):
            raise StoreError(Reason.STATE_MISSING)

        if operation == "sequence":
            h.guard._next_call_seq = missing
        elif operation == "decision":
            h.store.record_decision = missing
        else:
            h.store.reserve_dispatch = missing
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert not h.transport.calls
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["cancel", "emergency_halt"])
def test_second_task_lookup_missing_is_not_normal_absence_t20(command):
    async def scenario():
        h = _Harness()
        h.store.states["healthy"] = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        original = h.store.load_task
        lookups = 0

        async def lookup(task_id):
            nonlocal lookups
            if task_id == "task":
                lookups += 1
                if lookups > 1:
                    raise StoreError(Reason.STATE_MISSING)
            return await original(task_id)

        h.store.load_task = lookup
        assert isinstance(await _command(h, command, "task"), Denied)
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert not h.store.trace
        assert not h.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["submit", "cancel", "emergency_halt"])
def test_missing_task_with_live_approval_is_corruption_t20(command):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        healthy = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        h.store.states["healthy"] = healthy
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        original = h.store.load_task

        async def lookup(task_id):
            if task_id == "task":
                raise StoreError(Reason.STATE_MISSING)
            return await original(task_id)

        h.store.load_task = lookup
        assert isinstance(await _command(h, command, "task"), Denied)
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert h.store.states["healthy"] == healthy
        assert not h.transport.calls

    asyncio.run(scenario())


def test_approval_get_missing_is_not_normal_task_absence_t20():
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        h.store.states["healthy"] = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)

        async def missing(*args, **kwargs):
            raise StoreError(Reason.STATE_MISSING)

        h.store.get_approval = missing
        result = await h.guard.human_control("operator").approve(pending.approval_id)
        assert isinstance(result, Denied)
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert not h.transport.calls

    asyncio.run(scenario())
