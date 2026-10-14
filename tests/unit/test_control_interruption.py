"""I2/I3/I5/I6/I7/I8/I9, T20/T37: 긴급 제어와 AGT 작업 수명 단위 검증.

Store·AGT·transport는 메모리 대역이며 실제 MCP·SQLite·wire 검증이 아니다.
"""

import asyncio
import gc
import weakref

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
from guard_app.domain.enums import Label, RequestStatus, TaskStatus
from guard_app.domain.models import AccessResult
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardConfigError

from .test_guard import _call, _Harness


@pytest.mark.parametrize("phase", ["submit", "approval"])
@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
@pytest.mark.parametrize("ignore_cancellation", [False, True])
def test_urgent_control_interrupts_agt_before_dispatch(phase, action, ignore_cancellation):
    async def scenario():
        h = _Harness(label=Label.INTERNAL, access_cleanup_timeout_seconds=0.01)
        control = h.guard.human_control("operator")
        pending = await h.guard.submit("task", _call("send")) if phase == "approval" else None
        if phase == "approval":
            assert isinstance(pending, Pending)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(req, ctx):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if not ignore_cancellation:
                    raise
                await release.wait()
            return AccessResult(allowed=True, reason="", error=False)

        h.access.check_access = slow
        execution = asyncio.create_task(
            control.approve(pending.approval_id)
            if pending
            else h.guard.submit("task", _call("send"))
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(getattr(control, action)("task"), 0.5)
            assert isinstance(await asyncio.wait_for(execution, 0.5), Denied)
            assert h.store.states["task"].status is TaskStatus.HALTED
            assert h.store.consume_count == 0
            assert h.transport.calls == []
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert h.transport.calls == []
            assert not h.guard._global_halt
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


def test_unfinished_agt_blocks_new_jobs_until_actual_completion():
    async def scenario():
        h = _Harness(access_timeout_seconds=0.01, access_cleanup_timeout_seconds=0.01)
        release, completed = asyncio.Event(), asyncio.Event()
        starts = 0

        async def cancellation_ignoring(req, ctx):
            nonlocal starts
            starts += 1
            if starts > 1:
                return AccessResult(allowed=True, reason="", error=False)
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return AccessResult(allowed=True, reason="", error=False)
            finally:
                completed.set()

        h.access.check_access = cancellation_ignoring
        try:
            for _ in range(3):
                result = await asyncio.wait_for(h.guard.submit("task", _call()), 0.5)
                assert isinstance(result, Denied)
                assert Reason.AGT_ERROR in result.reasons
            assert starts == 1
            assert not completed.is_set()
            assert h.transport.calls == []
            assert h.store.consume_count == 0
            assert not any(event[0] == "reserve" for event in h.store.trace)
            assert h.store.states["task"].status is TaskStatus.RUNNING
            assert not h.guard._global_halt

            release.set()
            await asyncio.wait_for(completed.wait(), 1)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert h.transport.calls == []

            recovered = await h.guard.submit("task", _call())
            assert isinstance(recovered, Released)
            assert starts == 2
            assert len(h.transport.calls) == 1
        finally:
            release.set()

    asyncio.run(scenario())


def test_concurrent_agt_limit_denies_extra_task_without_creating_checker():
    async def scenario():
        h = _Harness(
            max_access_checks=2,
            access_timeout_seconds=5,
            access_cleanup_timeout_seconds=0.01,
        )
        initial = h.store.states["task"]
        for task_id in ("task-2", "task-3"):
            h.store.states[task_id] = initial.model_copy(update={"task_id": task_id})
        release, two_entered = asyncio.Event(), asyncio.Event()
        starts = 0

        async def held(req, ctx):
            nonlocal starts
            starts += 1
            if starts == 2:
                two_entered.set()
            await release.wait()
            return AccessResult(allowed=True, reason="", error=False)

        h.access.check_access = held
        executions = [
            asyncio.create_task(h.guard.submit(task_id, _call())) for task_id in ("task", "task-2")
        ]
        try:
            await asyncio.wait_for(two_entered.wait(), 1)
            excess = await asyncio.wait_for(h.guard.submit("task-3", _call()), 0.5)
            assert isinstance(excess, Denied)
            assert Reason.AGT_ERROR in excess.reasons
            assert starts == 2
            assert h.transport.calls == []
            assert h.store.states["task-3"].status is TaskStatus.RUNNING
            assert not h.guard._global_halt
            release.set()
            outcomes = await asyncio.wait_for(asyncio.gather(*executions), 1)
            assert all(isinstance(outcome, Released) for outcome in outcomes)
            assert starts == 2
            assert len(h.transport.calls) == 2
            assert {req.task_id for req in h.transport.calls} == {"task", "task-2"}
        finally:
            release.set()

    asyncio.run(scenario())


@pytest.mark.parametrize("cause", ["timeout", "parent_cancel"])
def test_late_agt_exception_is_discarded_after_bounded_cleanup(cause):
    async def scenario():
        h = _Harness(
            access_timeout_seconds=0.01 if cause == "timeout" else 5,
            access_cleanup_timeout_seconds=0.01,
        )
        entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        leaked = []
        worker_refs = []
        starts = 0
        loop.set_exception_handler(lambda _loop, context: leaked.append(context))

        async def late_error(req, ctx):
            nonlocal starts
            starts += 1
            if starts > 1:
                return AccessResult(allowed=True, reason="", error=False)
            worker_refs.append(weakref.ref(asyncio.current_task()))
            entered.set()
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                raise RuntimeError("RAW_AGT_EXCEPTION_MARKER")
            finally:
                completed.set()

        h.access.check_access = late_error
        execution = asyncio.create_task(h.guard.submit("task", _call()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if cause == "parent_cancel":
                execution.cancel()
            result = await asyncio.wait_for(execution, 0.5)
            assert isinstance(result, Denied)
            assert h.transport.calls == []
            assert not completed.is_set()
            assert h.guard._global_halt is (cause == "parent_cancel")

            release.set()
            await asyncio.wait_for(completed.wait(), 1)
            for _ in range(4):
                await asyncio.sleep(0)
            gc.collect()
            assert all(ref() is None for ref in worker_refs)
            assert leaked == []
            assert h.transport.calls == []

            next_result = await h.guard.submit("task", _call())
            if cause == "timeout":
                assert isinstance(next_result, Released)
                assert starts == 2
                assert len(h.transport.calls) == 1
            else:
                assert isinstance(next_result, Denied)
                assert starts == 1
                assert h.transport.calls == []
        finally:
            release.set()
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_checker_cancellation_keeps_existing_global_halt_semantics():
    async def scenario():
        h = _Harness()
        h.access.error = asyncio.CancelledError()
        result = await h.guard.submit("task", _call())
        assert isinstance(result, Denied)
        assert h.guard._global_halt
        assert h.transport.calls == []

    asyncio.run(scenario())


def test_second_parent_cancellation_keeps_unfinished_checker_tracked():
    async def scenario():
        h = _Harness(access_timeout_seconds=5, access_cleanup_timeout_seconds=5)
        entered, cleanup_entered = asyncio.Event(), asyncio.Event()
        release, completed = asyncio.Event(), asyncio.Event()
        worker = None

        async def cancellation_ignoring(req, ctx):
            nonlocal worker
            worker = asyncio.current_task()
            entered.set()
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cleanup_entered.set()
                    await release.wait()
                return AccessResult(allowed=True, reason="", error=False)
            finally:
                completed.set()

        h.access.check_access = cancellation_ignoring
        execution = asyncio.create_task(h.guard.submit("task", _call()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            execution.cancel()
            await asyncio.wait_for(cleanup_entered.wait(), 1)
            execution.cancel()
            assert isinstance(await asyncio.wait_for(execution, 0.5), Denied)
            assert h.guard._global_halt
            assert not completed.is_set()
            assert worker in h.guard._access_checks
            assert worker in h.guard._unfinished_access_checks
            assert h.transport.calls == []

            release.set()
            await asyncio.wait_for(completed.wait(), 1)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert worker not in h.guard._access_checks
            assert worker not in h.guard._unfinished_access_checks
            assert h.transport.calls == []
        finally:
            release.set()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "2", None])
def test_invalid_concurrent_agt_limit_is_rejected(value):
    with pytest.raises(GuardConfigError):
        _Harness(max_access_checks=value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "0.1", None])
def test_invalid_agt_cleanup_timeout_is_rejected(value):
    with pytest.raises(GuardConfigError):
        _Harness(access_cleanup_timeout_seconds=value)
