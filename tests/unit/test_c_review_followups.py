"""B7.12·NOTIFY-01/STOP-01·T45: 본문 수명과 예약 뒤 취소(I5/I8/I9)의 B 단위 검증."""

import asyncio

import pytest

from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    Released,
    TaskIdentity,
    Withheld,
)
from guard_app.domain.enums import Label, RequestStatus, SystemStopReason, TaskStatus, TrustTier
from guard_app.domain.models import Boundary
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import PendingResultError
from guard_app.services.guard import SystemControlError
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_pending_notifications import _single_confirmation, _turns
from tests.unit.test_policy_change import _PolicyHarness
from tests.unit.test_system_stop import _Audit


async def _pending(h, *, task_id="task", name="send"):
    state = h.store.states[task_id]
    value = await h.guard.submit(task_id, _call(name), expected_epoch=state.epoch)
    assert isinstance(value, Pending)
    return value, h.guard.pending_result(value.request_id)


async def _released(h, *, task_id="task"):
    value, handle = await _pending(h, task_id=task_id)
    assert isinstance(await h.guard.human_control("operator").approve(value.approval_id), Released)
    assert isinstance(handle._result.outcome, Released)
    return value, handle


async def _stop(h, action, *, epoch=1):
    if action == "cancel_pending":
        return await h.guard.cancel_pending("task", expected_epoch=epoch)
    if action == "system_stop":
        return await h.guard.system_control(_Audit()).stop_task(
            TaskIdentity(task_id="task", epoch=epoch), SystemStopReason.WATCHDOG_EXCEEDED
        )
    return await h.apply(h.prepare())


def _assert_discarded(h, value, handle):
    assert handle._result is None
    assert handle._completed
    assert value.request_id not in h.guard._approvals._results
    assert value.approval_id not in h.guard._approvals._pending
    with pytest.raises(PendingResultError):
        h.guard.pending_result(value.request_id)


@pytest.mark.parametrize("action", ["cancel_pending", "system_stop", "policy_change"])
def test_task_stop_discards_completed_unconsumed_released(action):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        value, handle = await _released(h)
        result = await _stop(h, action)
        if action == "cancel_pending":
            assert result == 0  # 완료된 요청은 새 취소 알림 수에 넣지 않는다.
        else:
            assert h.store.states["task"].status is TaskStatus.HALTED
        _assert_discarded(h, value, handle)
        with pytest.raises(PendingResultError, match="^최종 결과는 한 번만 소비할 수 있다$"):
            await handle.wait()
        assert len(h.transport.calls) == h.store.consume_count == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel_pending", "system_stop", "policy_change", "shutdown"])
def test_waiter_already_waiting_receives_released_when_task_stops(action):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        value, handle = await _pending(h)
        waiter = asyncio.create_task(handle.wait())
        await _turns()
        returned = await h.guard.human_control("operator").approve(value.approval_id)
        assert handle._waiting
        if action == "shutdown":
            await h.guard.aclose()
        else:
            await _stop(h, action)
        final = await asyncio.wait_for(waiter, 1)
        assert final.cause == "executed"
        assert isinstance(final.outcome, Released)
        assert final.outcome == returned
        _assert_discarded(h, value, handle)
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel_pending", "system_stop", "policy_change", "shutdown"])
@pytest.mark.parametrize("cancel_before_stop", [False, True])
def test_cancelled_waiter_does_not_retain_released_after_stop(action, cancel_before_stop):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        value, handle = await _pending(h)
        waiter = asyncio.create_task(handle.wait())
        await _turns()
        await h.guard.human_control("operator").approve(value.approval_id)
        waiter.cancel()
        if cancel_before_stop:
            with pytest.raises(asyncio.CancelledError):
                await waiter
        if action == "shutdown":
            await h.guard.aclose()
        else:
            await _stop(h, action)
        if not cancel_before_stop:
            with pytest.raises(asyncio.CancelledError):
                await waiter
        _assert_discarded(h, value, handle)
        with pytest.raises(PendingResultError):
            await handle.wait()
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel_pending", "system_stop", "policy_change"])
def test_task_stop_does_not_discard_other_epoch_or_unaffected_task(action):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        old_value, old_handle = await _released(h)
        h.add_task("other", label=Label.INTERNAL)
        other_value, other_handle = await _released(h, task_id="other")
        if action == "policy_change":
            # 정책 적용이 정지시키지 않는 이미 종결된 Task의 미소비 결과.
            h.store.states["other"] = h.store.states["other"].model_copy(
                update={"status": TaskStatus.CLOSED}
            )
        h.store.states["task"] = h.store.states["task"].model_copy(update={"epoch": 2})
        value, handle = await _released(h)
        await _stop(h, action, epoch=2)
        _assert_discarded(h, value, handle)
        assert (await old_handle.wait()).request_id == old_value.request_id
        assert (await other_handle.wait()).request_id == other_value.request_id
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())


def test_stale_epoch_cancel_and_stop_do_not_discard_current_released():
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        value, handle = await _released(h)
        assert await h.guard.cancel_pending("task", expected_epoch=0) == 0
        with pytest.raises(SystemControlError):
            await _stop(h, "system_stop", epoch=0)
        assert isinstance((await handle.wait()).outcome, Released)
        assert not h.guard._global_halt
        assert h.store.statuses[value.request_id] is RequestStatus.SUCCEEDED
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["denied", "withheld", "unknown"])
def test_task_stop_keeps_completed_results_without_released_body(kind):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        if kind == "withheld":
            _external_read(h)
        value, handle = await _pending(h, name="read" if kind == "withheld" else "send")
        if kind == "denied":
            await h.guard.human_control("operator").reject(value.approval_id)
        else:
            h.transport.error = RuntimeError("PRIVATE_transport")
            await h.guard.human_control("operator").approve(value.approval_id)
        before = handle._result
        await _stop(h, "system_stop")
        assert await handle.wait() is before
        await h.guard.aclose()

    asyncio.run(scenario())


def _external_read(h):
    h.registry.record = h.registry.record.model_copy(
        update={
            "boundary": Boundary(
                boundary_id="docs", tier=TrustTier.GENERAL_EXTERNAL, max_label=Label.INTERNAL
            )
        }
    )


@pytest.mark.parametrize("name", ["read", "send"])
def test_cancelled_approval_callback_failure_sets_guard_global_halt(name, monkeypatch, caplog):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        if name == "read":
            _external_read(h)
        value, handle = await _pending(h, name=name)
        entered = asyncio.Event()
        original_complete = h.guard._approvals._complete

        def complete(*args, **kwargs):
            original_complete(*args, **kwargs)
            raise RuntimeError("PRIVATE_CALLBACK")

        async def transport(req):
            h.transport.calls.append(req)
            entered.set()
            await asyncio.Event().wait()

        h.transport.call = transport
        monkeypatch.setattr(h.guard._approvals, "_complete", complete)
        work = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        await asyncio.wait_for(entered.wait(), 1)
        work.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(work, 1)
        monkeypatch.setattr(h.guard._approvals, "_complete", original_complete)
        final = await asyncio.wait_for(handle.wait(), 1)
        assert isinstance(final.outcome, Withheld if name == "read" else OutcomeUnknown)
        assert h.guard._global_halt
        assert h.store.states["task"].status is (
            TaskStatus.HALTED if name == "read" else TaskStatus.UNKNOWN_OUTCOME
        )
        assert isinstance(await h.guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == h.store.consume_count == 1
        assert "PRIVATE" not in repr(final) + repr(h.audit) + repr(caught.value) + caplog.text
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
@pytest.mark.parametrize("stage", ["transport", "commit"])
def test_reserved_approval_cancellation_completes_once_then_propagates(name, stage, monkeypatch):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        if name == "read":
            _external_read(h)
        value, handle = await _pending(h, name=name)
        confirmations = _single_confirmation(monkeypatch, handle)
        entered = asyncio.Event()
        effects = []
        original = h.transport.call

        async def transport(req):
            raw = await original(req)
            effects.append(req.request_id)  # 이미 수신됐을 수 있는 전송을 모사한다.
            if stage == "transport":
                entered.set()
                await asyncio.Event().wait()
            return raw

        async def commit(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        h.transport.call = transport
        if stage == "commit":
            setattr(
                h.store, "commit_observation" if name == "read" else "commit_send_outcome", commit
            )
        work = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        await asyncio.wait_for(entered.wait(), 1)
        work.cancel("PRIVATE_cancel_reason")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(work, 1)
        assert work.cancelled()
        final = await asyncio.wait_for(handle.wait(), 1)
        assert final.cause == "executed"
        assert isinstance(final.outcome, Withheld if name == "read" else OutcomeUnknown)
        assert final.outcome.reasons == (Reason.POST_DISPATCH_ERROR,)
        assert final.request_id == value.request_id
        assert h.store.states["task"].status is (
            TaskStatus.HALTED if name == "read" else TaskStatus.UNKNOWN_OUTCOME
        )
        assert h.store.statuses[value.request_id] is (
            RequestStatus.WITHHELD if name == "read" else RequestStatus.UNKNOWN_OUTCOME
        )
        assert h.audit[-1].exception_type == "CancelledError"
        assert "PRIVATE" not in repr(final) + repr(h.audit)
        assert "RAW_SECRET_MARKER" not in repr(final) + repr(h.audit)
        assert not h.guard._global_halt
        assert not h.guard._approvals._inflight_tasks
        assert not h.guard._approvals._expiry_tasks
        assert isinstance(
            await h.guard.human_control("operator").approve(value.approval_id), Denied
        )
        assert isinstance(await h.guard.submit("task", _call(name)), Denied)
        assert (
            len(confirmations)
            == len(h.transport.calls)
            == len(effects)
            == h.store.consume_count
            == 1
        )
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
@pytest.mark.parametrize("failure", ["store", "audit", "second_cancel", "containment_cancel"])
def test_cancelled_dispatch_recording_failure_completes_with_global_halt(
    name, failure, monkeypatch
):
    async def scenario():
        h = _Harness(label=Label.INTERNAL)
        if name == "read":
            _external_read(h)
        value, handle = await _pending(h, name=name)
        confirmations = _single_confirmation(monkeypatch, handle)
        entered, containing = asyncio.Event(), asyncio.Event()

        async def transport(req):
            h.transport.calls.append(req)
            entered.set()
            if failure == "containment_cancel":
                raise RuntimeError("PRIVATE_transport")
            await asyncio.Event().wait()

        async def audit(event):
            raise RuntimeError("PRIVATE_audit")

        async def recording(*args, **kwargs):
            containing.set()
            await asyncio.Event().wait()

        h.transport.call = transport
        if failure == "store":
            h.store.fail_at.add("failure")
        elif failure == "audit":
            h.guard._failure_audit = audit
        else:
            h.store.commit_request_failure = recording
        work = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        await asyncio.wait_for(entered.wait(), 1)
        if failure == "containment_cancel":
            await asyncio.wait_for(containing.wait(), 1)
        work.cancel("PRIVATE_first_cancel")
        if failure == "second_cancel":
            await asyncio.wait_for(containing.wait(), 1)
            work.cancel("PRIVATE_second_cancel")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(work, 1)
        final = await asyncio.wait_for(handle.wait(), 1)
        assert final.cause == "executed"
        assert isinstance(final.outcome, Withheld if name == "read" else OutcomeUnknown)
        assert h.guard._global_halt
        assert not h.guard._approvals._inflight_tasks
        assert len(confirmations) == len(h.transport.calls) == h.store.consume_count == 1
        assert isinstance(await h.guard.submit("task", _call(name)), Denied)
        assert len(h.transport.calls) == 1
        assert "PRIVATE" not in repr(final)
        await h.guard.aclose()

    asyncio.run(scenario())
