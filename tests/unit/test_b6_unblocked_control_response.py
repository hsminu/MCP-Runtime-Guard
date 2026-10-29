"""B6.11: 사람 제어와 승인된 응답 커밋의 Task lock 경합 (I3/I5/I6/I8, T37)."""

import asyncio

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, Label, RequestStatus, TaskStatus, TrustTier
from guard_app.domain.models import Boundary
from guard_app.domain.reasons import Reason
from tests.unit.test_guard import _call, _Harness


@pytest.mark.parametrize("kind", ["read", "send"])
@pytest.mark.parametrize("command", ["approve", "reject", "cancel", "emergency_halt"])
def test_human_control_waits_for_approved_response_commit(kind, command):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        if kind == "read":
            h.registry.record = h.registry.record.model_copy(
                update={
                    "boundary": Boundary(
                        boundary_id="docs",
                        tier=TrustTier.GENERAL_EXTERNAL,
                        max_label=Label.INTERNAL,
                    )
                }
            )
        pending = await h.guard.submit("task", _call(kind))
        assert isinstance(pending, Pending)
        control = h.guard.human_control("operator")
        entered, release = asyncio.Event(), asyncio.Event()
        commit_name = "commit_observation" if kind == "read" else "commit_send_outcome"
        original = getattr(h.store, commit_name)
        commits = 0

        async def delayed_commit(*args, **kwargs):
            nonlocal commits
            entered.set()
            await release.wait()
            result = await original(*args, **kwargs)
            commits += 1
            return result

        setattr(h.store, commit_name, delayed_commit)
        first = asyncio.create_task(control.approve(pending.approval_id))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            lock = h.guard._task_lock("task")
            assert lock.locked()
            identity = pending.approval_id if command in ("approve", "reject") else "task"
            second = asyncio.create_task(getattr(control, command)(identity))
            await asyncio.sleep(0)
            assert not second.done()
            assert h.guard._task_lock("task") is lock
            assert commits == 0
            assert h.store.consume_count == 1
            assert len(h.transport.calls) == 1
            assert h.store.statuses[pending.request_id] is RequestStatus.DISPATCHING
            release.set()
            assert isinstance(await asyncio.wait_for(first, 2), Released)
            result = await asyncio.wait_for(second, 2)
            assert isinstance(result, Denied)
            if command in ("approve", "reject"):
                assert result.reasons == (Reason.APPROVAL_RACE,)
            else:
                assert result.reasons == (Reason.TASK_NOT_RUNNING,)
            assert h.store.states["task"].status is (
                TaskStatus.HALTED if command in ("cancel", "emergency_halt") else TaskStatus.RUNNING
            )
            assert h.store.approvals[pending.approval_id].status is ApprovalStatus.CONSUMED
            assert h.store.statuses[pending.request_id] is RequestStatus.SUCCEEDED
            assert commits == 1
            assert h.store.consume_count == 1
            assert len(h.transport.calls) == 1
            assert not h.guard._global_halt
        finally:
            release.set()
            if second is not None and not second.done():
                second.cancel()
                await asyncio.gather(second, return_exceptions=True)
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
            await h.guard.aclose()

    asyncio.run(scenario())
