"""I2/I3/I5/I6/I7/I8/I9, T20/T37: 긴급 제어와 저장 대기 지점 단위 검증.

Store·접근 판정기·transport는 메모리 대역이며 실제 MCP·SQLite·wire 검증이 아니다.
"""

import asyncio

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
from guard_app.domain.enums import Label, RequestStatus, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import PendingResultError

from .test_guard import _call, _Harness


@pytest.mark.parametrize("phase", ["submit", "approval"])
@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
def test_urgent_control_during_store_wait_prevents_dispatch(phase, action, monkeypatch):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send")) if phase == "approval" else None
        if phase == "approval":
            assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id) if pending else None
        completions = []
        if handle is not None:
            original_complete = handle._complete

            def count_completion(value):
                accepted = original_complete(value)
                if accepted:
                    completions.append(value)
                return accepted

            monkeypatch.setattr(handle, "_complete", count_completion)
        entered, release = asyncio.Event(), asyncio.Event()
        name = "get_approval" if pending else "record_decision"
        original = getattr(h.store, name)

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        setattr(h.store, name, delayed)
        execution = asyncio.create_task(
            control.approve(pending.approval_id)
            if pending
            else h.guard.submit("task", _call("send"))
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            stopping = asyncio.create_task(getattr(control, action)("task"))
            await asyncio.sleep(0)
            assert h.guard._stop_requests["task"].is_set()
            assert not stopping.done()
            release.set()
            assert isinstance(await asyncio.wait_for(execution, 1), Denied)
            await asyncio.wait_for(stopping, 1)
            assert h.store.states["task"].status is TaskStatus.HALTED
            assert h.store.consume_count == 0
            assert h.transport.calls == []
            assert not h.guard._global_halt
            if handle is not None:
                final = await asyncio.wait_for(handle.wait(), 1)
                assert isinstance(final.outcome, Denied)
                assert len(completions) == 1
                with pytest.raises(PendingResultError):
                    await handle.wait()
        finally:
            release.set()

    asyncio.run(scenario())


def test_wrong_operator_cannot_set_stop_latch():
    async def scenario():
        h = _Harness()
        result = await h.guard._approvals._cancel("task", "untrusted")
        assert isinstance(result, Denied)
        assert not h.guard._stop_requests
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert h.transport.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["submit", "approval"])
def test_stop_during_reservation_ack_keeps_unknown_send_semantics(phase):
    async def scenario():
        h = _Harness(label=Label.INTERNAL if phase == "approval" else Label.PUBLIC)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send")) if phase == "approval" else None
        if phase == "approval":
            assert isinstance(pending, Pending)
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
        try:
            await asyncio.wait_for(entered.wait(), 1)
            halt = asyncio.create_task(control.emergency_halt("task"))
            await asyncio.sleep(0)
            release.set()
            assert isinstance(await asyncio.wait_for(execution, 1), OutcomeUnknown)
            await asyncio.wait_for(halt, 1)
            assert h.store.states["task"].status is TaskStatus.UNKNOWN_OUTCOME
            assert h.transport.calls == []
            assert h.store.consume_count == int(phase == "approval")
            assert next(iter(h.store.statuses.values())) is RequestStatus.UNKNOWN_OUTCOME
        finally:
            release.set()

    asyncio.run(scenario())


def test_stop_during_read_reservation_ack_withholds_without_dispatch():
    async def scenario():
        h = _Harness()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.reserve_dispatch

        async def delayed(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()

        h.store.reserve_dispatch = delayed
        execution = asyncio.create_task(h.guard.submit("task", _call()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            halt = asyncio.create_task(h.guard.human_control("operator").emergency_halt("task"))
            await asyncio.sleep(0)
            release.set()
            assert isinstance(await asyncio.wait_for(execution, 1), Withheld)
            await asyncio.wait_for(halt, 1)
            assert h.store.states["task"].status is TaskStatus.HALTED
            assert next(iter(h.store.statuses.values())) is RequestStatus.WITHHELD
            assert h.store.consume_count == 0
            assert h.transport.calls == []
        finally:
            release.set()

    asyncio.run(scenario())


def test_stop_during_decision_audit_prevents_reservation():
    async def scenario():
        h = _Harness()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.record_decision

        async def delayed(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()

        h.store.record_decision = delayed
        execution = asyncio.create_task(h.guard.submit("task", _call("send")))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            halt = asyncio.create_task(h.guard.human_control("operator").cancel("task"))
            await asyncio.sleep(0)
            release.set()
            assert isinstance(await asyncio.wait_for(execution, 1), Denied)
            await asyncio.wait_for(halt, 1)
            assert not any(event[0] == "reserve" for event in h.store.trace)
            assert not h.transport.calls
        finally:
            release.set()

    asyncio.run(scenario())


def test_checker_cancellation_halts_only_its_task():
    async def scenario():
        h = _Harness()
        h.store.states["healthy"] = h.store.states["task"].model_copy(update={"task_id": "healthy"})
        h.access.error = asyncio.CancelledError()
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert not h.guard._global_halt
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.transport.calls == []
        h.access.error = None
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        assert [req.task_id for req in h.transport.calls] == ["healthy"]

    asyncio.run(scenario())
