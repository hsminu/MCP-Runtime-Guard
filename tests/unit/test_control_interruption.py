"""I2/I3/I6/I7/I8, T20/T37: cancel before/after reservation during AGT waits."""

import asyncio

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.models import AccessResult

from .test_guard import _call, _Harness


@pytest.mark.parametrize("phase", ["submit", "approval"])
@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
@pytest.mark.parametrize("ignore_cancellation", [False, True])
def test_urgent_control_interrupts_agt_before_dispatch(phase, action, ignore_cancellation):
    async def run():
        h = _Harness(label=Label.INTERNAL)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send")) if phase == "approval" else None
        entered, late = asyncio.Event(), asyncio.Event()

        async def slow(req, ctx):
            entered.set()
            try:
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                if not ignore_cancellation:
                    raise
                await late.wait()
            return AccessResult(allowed=True, reason="", error=False)

        h.access.check_access = slow
        execution = asyncio.create_task(
            control.approve(pending.approval_id)
            if pending
            else h.guard.submit("task", _call("send"))
        )
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(getattr(control, action)("task"), 0.5)
        assert isinstance(await asyncio.wait_for(execution, 0.5), Denied)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.store.consume_count == 0
        assert h.transport.calls == []
        late.set()
        await asyncio.sleep(0)
        assert h.transport.calls == []
        assert not h.guard._global_halt

    asyncio.run(run())


def test_wrong_operator_cannot_set_stop_latch():
    async def run():
        h = _Harness()
        await h.guard._approvals._cancel("task", "untrusted")
        assert not h.guard._stop_requests
        assert h.store.states["task"].status is TaskStatus.RUNNING

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["submit", "approval"])
def test_stop_during_reservation_ack_keeps_unknown_send_semantics(phase):
    async def run():
        h = _Harness(label=Label.INTERNAL if phase == "approval" else Label.PUBLIC)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send")) if phase == "approval" else None
        entered, release = asyncio.Event(), asyncio.Event()
        name = "consume_and_reserve" if pending else "reserve_dispatch"
        original = getattr(h.store, name)

        async def delayed(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        setattr(h.store, name, delayed)
        execution = asyncio.create_task(
            control.approve(pending.approval_id)
            if pending
            else h.guard.submit("task", _call("send"))
        )
        await asyncio.wait_for(entered.wait(), 1)
        halt = asyncio.create_task(control.emergency_halt("task"))
        await asyncio.sleep(0)
        release.set()
        assert isinstance(await asyncio.wait_for(execution, 1), OutcomeUnknown)
        await asyncio.wait_for(halt, 1)
        assert h.store.states["task"].status is TaskStatus.UNKNOWN_OUTCOME
        assert h.transport.calls == []

    asyncio.run(run())


def test_stop_during_decision_audit_prevents_reservation():
    async def run():
        h = _Harness()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.record_decision

        async def delayed(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()

        h.store.record_decision = delayed
        execution = asyncio.create_task(h.guard.submit("task", _call("send")))
        await asyncio.wait_for(entered.wait(), 1)
        halt = asyncio.create_task(h.guard.human_control("operator").cancel("task"))
        await asyncio.sleep(0)
        release.set()
        assert isinstance(await asyncio.wait_for(execution, 1), Denied)
        await asyncio.wait_for(halt, 1)
        assert not any(event[0] == "reserve" for event in h.store.trace)
        assert not h.transport.calls

    asyncio.run(run())


def test_review_returns_original_bytes_only_while_pending():
    async def run():
        h = _Harness(label=Label.INTERNAL)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        snap = await control.review(pending.approval_id)
        assert snap is h.guard._approvals._memory_snapshots[pending.approval_id]
        assert await h.guard._approvals._review(pending.approval_id, "untrusted") is None
        await control.approve(pending.approval_id)
        assert await control.review(pending.approval_id) is None
        assert h.store.consume_count == 1
        assert len(h.transport.calls) == 1

    asyncio.run(run())
