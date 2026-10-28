"""Guard의 영속 순번 실패 범위와 격리(SEQ-01·02, I5/I8)."""

import asyncio

import pytest

from guard_app.domain.contracts import Denied, Released
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from tests.unit.test_guard import _call, _Harness


@pytest.mark.parametrize(
    "reason",
    [
        Reason.TASK_NOT_RUNNING,
        Reason.TASK_UNCLASSIFIED,
        Reason.STATE_MISSING,
        Reason.INVALID_ARGUMENTS,
    ],
)
def test_allocator_task_local_failures_leave_other_task_running(reason):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        h.store.states["other"] = h.store.states["task"].model_copy(update={"task_id": "other"})

        async def sequence(task_id):
            if task_id == "task":
                raise StoreError(reason)
            return 1

        h.guard._next_call_seq = sequence
        denied = await h.guard.submit("task", _call())
        assert isinstance(denied, Denied)
        assert denied.reasons == (reason,)
        assert not h.guard._global_halt
        assert not h.transport.calls
        assert not any(item[0] in ("decision", "reserve") for item in h.store.trace)
        assert h.store.states["task"].status is (
            TaskStatus.HALTED if reason is Reason.INVALID_ARGUMENTS else TaskStatus.RUNNING
        )
        assert h.audit[-1].reason is reason
        assert isinstance(await h.guard.submit("other", _call()), Released)
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", [Reason.STATE_COMMIT_FAILED, Reason.INTERNAL_ERROR])
def test_allocator_integrity_failure_halts_globally(reason):
    async def scenario():
        observed = []
        h = _Harness(source_label=Label.PUBLIC, on_global_halt=observed.append)
        h.store.states["other"] = h.store.states["task"].model_copy(update={"task_id": "other"})

        async def sequence(task_id):
            raise StoreError(reason)

        h.guard._next_call_seq = sequence
        denied = await h.guard.submit("task", _call())
        assert isinstance(denied, Denied)
        assert denied.reasons == (reason,)
        assert h.guard._global_halt
        assert observed == [reason]
        assert isinstance(await h.guard.submit("other", _call()), Denied)
        assert not h.transport.calls
        await h.guard.aclose()
        assert observed == [reason]

    asyncio.run(scenario())


def test_allocator_storeerror_subclass_does_not_get_task_local_treatment():
    class StoreSubclassError(StoreError):
        pass

    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)

        async def sequence(task_id):
            raise StoreSubclassError(Reason.TASK_NOT_RUNNING)

        h.guard._next_call_seq = sequence
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert h.guard._global_halt
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_late_halted_task_is_denied_without_affecting_other_task():
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        h.store.states["other"] = h.store.states["task"].model_copy(update={"task_id": "other"})
        h.store.states["task"] = h.store.states["task"].model_copy(
            update={"status": TaskStatus.HALTED}
        )
        denied = await h.guard.submit("task", _call())
        assert isinstance(denied, Denied)
        assert denied.reasons == (Reason.TASK_NOT_RUNNING,)
        assert not h.guard._global_halt
        assert h.transport.calls == []
        assert isinstance(await h.guard.submit("other", _call()), Released)
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())
