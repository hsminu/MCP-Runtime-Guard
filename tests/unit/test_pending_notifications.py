"""NOTIFY-01·T45의 B 단위 검증(I3/I5/I6/I9/I10). 실제 C wire·A 중계는 별도다."""

import asyncio
import json
from datetime import timedelta
from itertools import count

import pytest
from pydantic import ValidationError

from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    PendingFinal,
    Released,
    Withheld,
)
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus, TrustTier
from guard_app.domain.models import Boundary
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import ApprovalConfigError, PendingResultError
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import NOW, _call, _Harness
from tests.unit.test_sqlite_decisions import NOW as STORE_NOW
from tests.unit.test_sqlite_decisions import task
from tests.unit.test_sqlite_lifecycle import Clock, pending, transition


class _Clock:
    """실제 시간 대기 없이 명시적으로 timer 대기를 깨운다."""

    def __init__(self):
        self.utc = NOW
        self.mono = 100.0
        self.waiters = []

    async def sleep(self, delay):
        assert 0 < delay <= 1
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        try:
            await future
        finally:
            self.waiters.remove(future)

    def advance(self, *, utc=0, mono=0):
        self.utc += timedelta(seconds=utc)
        self.mono += mono
        for future in tuple(self.waiters):
            if not future.done():
                future.set_result(None)


async def _turns():
    # 스케줄러에 실행권만 넘긴다. 만료 판단은 주입 시계만 사용한다.
    for _ in range(8):
        await asyncio.sleep(0)


async def _setup(**kwargs):
    clock = _Clock()
    harness = _Harness(
        label=Label.INTERNAL,
        approval_sleep=clock.sleep,
        utc_now=lambda: clock.utc,
        monotonic=lambda: clock.mono,
        **kwargs,
    )
    result = await harness.guard.submit("task", _call("send"), expected_epoch=1)
    assert isinstance(result, Pending)
    handle = harness.guard.pending_result(result.request_id)
    await _turns()
    return harness, clock, result, handle


def _single_confirmation(monkeypatch, handle):
    confirmations = []
    original = handle._complete

    def counted(value):
        accepted = original(value)
        if accepted:
            confirmations.append(value)
        return accepted

    monkeypatch.setattr(handle, "_complete", counted)
    return confirmations


@pytest.mark.parametrize("clock", ["mono", "utc", "both"])
def test_unattended_expiry_notifies_once_without_dispatch(clock, monkeypatch):
    async def scenario():
        h, timer, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        timer.advance(utc=300 if clock != "mono" else 0, mono=300 if clock != "utc" else 0)
        final = await handle.wait()
        assert final.cause == "expired"
        assert isinstance(final.outcome, Denied)
        assert final.outcome.reasons == (Reason.APPROVAL_STALE,)
        assert (final.task_id, final.epoch, final.request_id, final.approval_id) == (
            "task",
            1,
            value.request_id,
            value.approval_id,
        )
        assert h.store.approvals[value.approval_id].status is ApprovalStatus.EXPIRED
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert h.transport.calls == []
        assert h.store.consume_count == 0
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._expiry_tasks
        await h.guard.human_control("operator").approve(value.approval_id)
        await h.guard.human_control("operator").expire(value.approval_id)
        assert len(confirmations) == 1
        with pytest.raises(PendingResultError):
            await handle.wait()
        with pytest.raises(PendingResultError):
            h.guard.pending_result(value.request_id)
        await h.guard.aclose()

    asyncio.run(scenario())


def test_utc_rollback_cannot_extend_monotonic_deadline():
    async def scenario():
        h, clock, _, handle = await _setup()
        clock.advance(utc=-3600, mono=300)
        assert (await handle.wait()).cause == "expired"
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["approve", "expiry"])
def test_approval_and_expiry_race_has_one_terminal_result(first, monkeypatch):
    async def scenario():
        h, clock, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        if first == "expiry":
            clock.advance(utc=300, mono=300)
        approval = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        if first == "approve":
            await _turns()
            clock.advance(utc=300, mono=300)
        await approval
        final = await handle.wait()
        assert final.cause == ("executed" if first == "approve" else "expired")
        assert len(h.transport.calls) == (1 if first == "approve" else 0)
        assert len(confirmations) == 1
        assert not h.guard._approvals._expiry_tasks
        await h.guard.aclose()

    asyncio.run(scenario())


def test_fast_completion_is_retained_for_late_handle_lookup_and_double_click(monkeypatch):
    async def scenario():
        h, _, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        control = h.guard.human_control("operator")
        first, second = await asyncio.gather(
            control.approve(value.approval_id), control.approve(value.approval_id)
        )
        assert isinstance(first, Released)
        assert isinstance(second, Denied)
        assert len(h.transport.calls) == h.store.consume_count == 1
        assert h.guard.pending_result(value.request_id) is handle
        final = await h.guard.pending_result(value.request_id).wait()
        assert final.outcome == first
        assert final.cause == "executed"
        assert len(confirmations) == 1
        assert handle._result is None
        assert not h.guard._approvals._pending
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_after_completion", [False, True])
def test_wait_cancellation_keeps_completion_available(cancel_after_completion):
    async def scenario():
        h, clock, value, handle = await _setup()
        waiter = asyncio.create_task(handle.wait())
        await _turns()
        with pytest.raises(PendingResultError):
            await handle.wait()
        if cancel_after_completion:
            await h.guard.human_control("operator").reject(value.approval_id)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        if not cancel_after_completion:
            assert h.store.approvals[value.approval_id].status is ApprovalStatus.PENDING
            clock.advance(mono=300)
        final = await handle.wait()
        assert final.cause == ("rejected" if cancel_after_completion else "expired")
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["reject", "cancel", "emergency_halt", "cancel_pending"])
def test_rejection_and_task_stop_notify_once(action, monkeypatch):
    async def scenario():
        h, clock, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        if action == "cancel_pending":
            assert await h.guard.cancel_pending("task", expected_epoch=1) == 1
        else:
            control = h.guard.human_control("operator")
            await getattr(control, action)(value.approval_id if action == "reject" else "task")
        final = await handle.wait()
        assert final.cause == ("rejected" if action == "reject" else "cancelled")
        clock.advance(utc=300, mono=300)
        await _turns()
        await h.guard.human_control("operator").approve(value.approval_id)
        assert len(confirmations) == 1
        assert h.transport.calls == []
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._expiry_tasks
        await h.guard.aclose()

    asyncio.run(scenario())


def test_old_epoch_cancel_and_unrelated_fingerprint_cannot_touch_pending():
    async def scenario():
        h, _, value, handle = await _setup()
        assert await h.guard.cancel_pending("task", expected_epoch=0) == 0
        assert await h.guard.invalidate_tool("other", "send") == 0
        assert await h.guard.invalidate_tool("docs", "other") == 0
        assert not handle._completed
        assert h.store.approvals[value.approval_id].status is ApprovalStatus.PENDING
        await h.guard.aclose()
        assert (await handle.wait()).cause == "shutdown"

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["cancel_pending", "invalidate_tool"])
def test_stop_and_expiry_race_has_one_notification(command, monkeypatch):
    async def scenario():
        h, clock, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        stop = asyncio.create_task(
            h.guard.cancel_pending("task", expected_epoch=1)
            if command == "cancel_pending"
            else h.guard.invalidate_tool("docs", "send")
        )
        clock.advance(utc=300, mono=300)
        await stop
        final = await handle.wait()
        assert final.cause in {"cancelled", "invalidated", "expired"}
        assert len(confirmations) == 1
        assert h.transport.calls == []
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.store.approvals[value.approval_id].status in {
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        }
        await h.guard.aclose()

    asyncio.run(scenario())


def test_registry_fingerprint_invalidation_notifies_without_human_action(monkeypatch):
    async def scenario():
        h, _, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        h.registry.current_fingerprint = "b" * 64
        assert await h.guard.invalidate_tool("docs", "send") == 1
        final = await handle.wait()
        assert final.cause == "invalidated"
        assert final.outcome.reasons == (Reason.TOOL_FINGERPRINT_CHANGED,)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert h.transport.calls == []
        assert await h.guard.invalidate_tool("docs", "send") == 0
        await h.guard.human_control("operator").approve(value.approval_id)
        assert len(confirmations) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["transition", "status", "get_approval"])
def test_timer_storage_failure_is_terminal_fail_closed(failure, monkeypatch):
    async def scenario():
        h, clock, _, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        h.store.fail_at.add(failure)
        clock.advance(mono=300)
        final = await handle.wait()
        assert final.cause == "failed"
        assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
        assert len(confirmations) == 1
        assert h.guard._global_halt
        assert not h.guard._approvals._memory_snapshots
        assert h.transport.calls == []
        h.store.fail_at.clear()
        await h.guard.aclose()

    asyncio.run(scenario())


def test_shutdown_cancels_pending_and_joins_timers_without_human_channel():
    async def scenario():
        h, clock, value, handle = await _setup()
        waiter = asyncio.create_task(handle.wait())
        await _turns()
        await h.guard.aclose()
        final = await waiter
        assert final.cause == "shutdown"
        assert h.store.approvals[value.approval_id].status is ApprovalStatus.CANCELLED
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert not clock.waiters
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._expiry_tasks
        assert h.transport.calls == []
        assert isinstance(await h.guard.submit("task", _call("send")), Denied)
        await h.guard.aclose()

    asyncio.run(scenario())


def test_shutdown_discards_unconsumed_released_body():
    async def scenario():
        h, _, value, handle = await _setup()
        await h.guard.human_control("operator").approve(value.approval_id)
        assert isinstance(handle._result.outcome, Released)
        await h.guard.aclose()
        assert handle._result is None
        assert not h.guard._approvals._results
        with pytest.raises(PendingResultError):
            await handle.wait()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["expiry", "shutdown"])
def test_consumed_execution_finishes_once_before_shutdown_or_late_expiry(stop, monkeypatch):
    async def scenario():
        h, clock, value, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.transport.call

        async def blocked(req):
            entered.set()
            await release.wait()
            return await original(req)

        h.transport.call = blocked
        approval = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        await entered.wait()
        assert h.store.consume_count == 1
        assert not h.guard._approvals._expiry_tasks
        shutdown = None
        if stop == "shutdown":
            shutdown = asyncio.create_task(h.guard.aclose())
        else:
            clock.advance(utc=300, mono=300)
        await _turns()
        assert not handle._completed
        release.set()
        returned = await approval
        assert isinstance(returned, Released)
        if shutdown is not None:
            await shutdown
            assert handle._result is None
            assert h.store.states["task"].status is TaskStatus.HALTED
        else:
            assert (await handle.wait()).outcome == returned
            await h.guard.aclose()
        assert len(confirmations) == len(h.transport.calls) == h.store.consume_count == 1

    asyncio.run(scenario())


def test_recheck_invalidation_has_one_final_and_no_raw_repr():
    async def scenario():
        h, _, value, handle = await _setup()
        h.registry.current_fingerprint = "b" * 64
        returned = await h.guard.human_control("operator").approve(value.approval_id)
        final = await handle.wait()
        assert final.cause == "invalidated"
        assert final.outcome == returned
        assert "RAW_SECRET_MARKER" not in repr(final)
        assert "RAW_SECRET_MARKER" not in repr(handle)
        assert h.transport.calls == []
        await h.guard.aclose()

    asyncio.run(scenario())


def test_dto_rejects_pending_and_mismatched_request_and_nonexecution_release():
    async def scenario():
        h, _, value, handle = await _setup()
        await h.guard.human_control("operator").approve(value.approval_id)
        final = await handle.wait()
        for update in (
            {"outcome": value},
            {"request_id": "different"},
            {"cause": "expired"},
            {"epoch": True},
            {"extra": "RAW_SECRET_MARKER"},
        ):
            with pytest.raises(ValidationError):
                final.model_copy(update=update)
        assert PendingFinal.model_validate(final) == final
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", [Reason.APPROVAL_STALE, Reason.OK])
def test_sqlite_audit_distinguishes_guard_expiry_from_human_expiry(tmp_path, reason):
    clock = Clock()
    identifiers = count(1)
    with SQLiteStore(
        tmp_path / "expiry.db", utc_now=clock, audit_id_factory=lambda: f"audit-{next(identifiers)}"
    ) as store:
        asyncio.run(store.create_task(task(), created_at_utc=STORE_NOW))
        pending(store)
        transition(store, ApprovalStatus.EXPIRED, reason=reason)
        rows = (
            store._owned_connection()
            .execute("SELECT detail FROM audit_events WHERE event_type='APPROVAL_EXPIRED'")
            .fetchall()
        )
        assert len(rows) == 1
        if reason is Reason.APPROVAL_STALE:
            assert json.loads(rows[0][0]) == {"source": "guard_expiry"}
        else:
            assert rows[0][0] is None


@pytest.mark.parametrize("bad", ["bad__server", "bad_", "\n"])
def test_fingerprint_entry_rejects_invalid_server_ids(bad):
    async def scenario():
        h, _, _, _ = await _setup()
        with pytest.raises(ApprovalConfigError):
            await h.guard.invalidate_tool(bad, "send")
        await h.guard.aclose()

    asyncio.run(scenario())


def test_approved_read_still_notifies_only_after_observation_commit():
    async def scenario():
        h, _, previous, _ = await _setup()
        await h.guard.human_control("operator").reject(previous.approval_id)
        h.registry.record = h.registry.record.model_copy(
            update={
                "boundary": Boundary(
                    boundary_id="external",
                    tier=TrustTier.GENERAL_EXTERNAL,
                    max_label=Label.INTERNAL,
                )
            }
        )
        h.store.observation_started, h.store.observation_release = asyncio.Event(), asyncio.Event()
        value = await h.guard.submit("task", _call("read"))
        assert isinstance(value, Pending)
        handle = h.guard.pending_result(value.request_id)
        approval = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        await h.store.observation_started.wait()
        assert not handle._completed
        h.store.observation_release.set()
        returned = await approval
        assert isinstance(returned, Released)
        assert (await handle.wait()).outcome == returned
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["read", "send"])
def test_post_dispatch_failure_uses_original_terminal_outcome(kind):
    async def scenario():
        h, _, previous, _ = await _setup()
        await h.guard.human_control("operator").reject(previous.approval_id)
        if kind == "read":
            h.registry.record = h.registry.record.model_copy(
                update={
                    "boundary": Boundary(
                        boundary_id="external",
                        tier=TrustTier.GENERAL_EXTERNAL,
                        max_label=Label.INTERNAL,
                    )
                }
            )
        h.transport.error = RuntimeError("RAW_SECRET_MARKER")
        value = await h.guard.submit("task", _call(kind))
        assert isinstance(value, Pending)
        handle = h.guard.pending_result(value.request_id)
        returned = await h.guard.human_control("operator").approve(value.approval_id)
        final = await handle.wait()
        assert final.cause == "executed"
        assert final.outcome == returned
        assert isinstance(returned, Withheld if kind == "read" else OutcomeUnknown)
        assert "RAW_SECRET_MARKER" not in repr(final)
        assert len(h.transport.calls) == 1
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "action", ["expiry", "approve", "reject", "cancel", "fingerprint", "close"]
)
def test_real_sqlite_and_guard_finalize_same_pending_once(tmp_path, action):
    async def scenario():
        clock = _Clock()
        h = _Harness(label=Label.INTERNAL)
        identifiers = count(1)
        with SQLiteStore(
            tmp_path / "notifications.db",
            utc_now=lambda: clock.utc,
            audit_id_factory=lambda: f"audit-{next(identifiers)}",
        ) as store:
            await store.create_task(h.store.states["task"], created_at_utc=clock.utc)
            guard = GuardService(
                store,
                h.registry,
                h.catalog,
                h.transport,
                h.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: "request",
                approval_id_factory=lambda: "approval",
                next_call_seq=h.sequence,
                failure_audit=h.failure_audit,
                utc_now=lambda: clock.utc,
                monotonic=lambda: clock.mono,
                approval_sleep=clock.sleep,
            )
            value = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(value, Pending)
            handle = guard.pending_result(value.request_id)
            await _turns()
            if action == "expiry":
                clock.advance(utc=300, mono=300)
            elif action in {"approve", "reject"}:
                await getattr(guard.human_control("operator"), action)(value.approval_id)
            elif action == "cancel":
                await guard.cancel_pending("task", expected_epoch=1)
            elif action == "fingerprint":
                await guard.invalidate_tool("docs", "send")
            else:
                await guard.aclose()
            final = await handle.wait()
            expected = {
                "expiry": "expired",
                "approve": "executed",
                "reject": "rejected",
                "cancel": "cancelled",
                "fingerprint": "invalidated",
                "close": "shutdown",
            }
            assert final.cause == expected[action]
            assert isinstance(final.outcome, Released if action == "approve" else Denied)
            assert len(h.transport.calls) == (1 if action == "approve" else 0)
            assert "RAW_SECRET_MARKER" not in repr(final)
            assert not store._approval_contexts
            assert not store._request_contexts
            assert not guard._approvals._memory_snapshots
            if action == "expiry":
                row = (
                    store._owned_connection()
                    .execute("SELECT detail FROM audit_events WHERE event_type='APPROVAL_EXPIRED'")
                    .fetchone()
                )
                assert json.loads(row[0]) == {"source": "guard_expiry"}
            await guard.aclose()

    asyncio.run(scenario())


def test_timer_clock_failure_notifies_fixed_failure_and_halts(monkeypatch):
    async def scenario():
        h, clock, _, handle = await _setup()
        confirmations = _single_confirmation(monkeypatch, handle)
        clock.mono = float("nan")
        clock.advance()
        final = await handle.wait()
        assert final.cause == "failed"
        assert len(confirmations) == 1
        assert h.guard._global_halt
        assert h.transport.calls == []
        assert not h.guard._approvals._memory_snapshots
        clock.mono = 100
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["shutdown", "fingerprint", "cancelled"])
def test_termination_waits_for_approval_creation_before_finalizing(action):
    async def scenario():
        clock = _Clock()
        h = _Harness(
            label=Label.INTERNAL,
            approval_sleep=clock.sleep,
            utc_now=lambda: clock.utc,
            monotonic=lambda: clock.mono,
        )
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.store.create_approval

        async def blocked(record):
            await original(record)
            entered.set()
            await release.wait()

        h.store.create_approval = blocked
        submit = asyncio.create_task(h.guard.submit("task", _call("send")))
        await entered.wait()
        assert h.guard._approvals._opening
        termination = asyncio.create_task(
            h.guard.aclose()
            if action == "shutdown"
            else h.guard.invalidate_tool("docs", "send")
            if action == "fingerprint"
            else h.guard.cancel_pending("task", expected_epoch=1)
        )
        await _turns()
        assert not termination.done()
        release.set()
        value = await submit
        assert isinstance(value, Pending)
        handle = h.guard.pending_result(value.request_id)
        await termination
        final = await handle.wait()
        assert final.cause == ("invalidated" if action == "fingerprint" else action)
        assert not h.guard._approvals._opening
        assert not h.guard._approvals._memory_snapshots
        assert not h.guard._approvals._expiry_tasks
        assert h.transport.calls == []
        assert h.store.states["task"].status is TaskStatus.HALTED
        await h.guard.aclose()

    asyncio.run(scenario())


def test_cancelling_timer_waiting_on_approval_lock_does_not_halt_valid_execution():
    async def scenario():
        h, clock, value, handle = await _setup()
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.access.check_access

        async def blocked(req, context):
            entered.set()
            await release.wait()
            return await original(req, context)

        h.access.check_access = blocked
        approval = asyncio.create_task(h.guard.human_control("operator").approve(value.approval_id))
        await entered.wait()
        clock.advance(utc=300)
        await _turns()
        assert not handle._completed
        # 타이머가 lock을 얻기 전 UTC가 돌아왔다. fresh 두 시계 검사로 확정한다.
        clock.advance(utc=-300)
        release.set()
        returned = await approval
        await _turns()
        assert isinstance(returned, Released)
        assert not h.guard._global_halt
        assert (await handle.wait()).outcome == returned
        assert h.store.consume_count == len(h.transport.calls) == 1
        assert not h.guard._approvals._expiry_tasks
        await h.guard.aclose()

    asyncio.run(scenario())


def test_timer_keeps_monitoring_if_utc_rolls_back_before_expiry_lock_is_acquired():
    async def scenario():
        h, clock, _, handle = await _setup()
        lock = h.guard._task_lock("task")
        await lock.acquire()
        clock.advance(utc=300)
        await _turns()
        clock.advance(utc=-300)
        lock.release()
        await _turns()
        assert not handle._completed
        assert h.guard._approvals._expiry_tasks
        assert h.guard._approvals._memory_snapshots
        clock.advance(mono=300)
        assert (await handle.wait()).cause == "expired"
        assert h.transport.calls == []
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())
