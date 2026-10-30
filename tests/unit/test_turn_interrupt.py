"""Turn interrupts preserve Task execution and the approval transaction (I3/I5/I8/I9)."""

import asyncio
from contextlib import asynccontextmanager
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, TurnInterrupt
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus, TurnInterruptOutcome
from guard_app.domain.errors import StoreError
from guard_app.domain.queries import ApprovalListLane
from guard_app.domain.reasons import Reason
from guard_app.services.approvals import ApprovalConfigError, PendingResultError
from guard_app.services.guard import GuardService, durable_failure_audit
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_pending_notifications import _Clock, _turns
from tests.unit.test_store_queries import approval_query


@asynccontextmanager
async def _real_guard(tmp_path, *, label=Label.INTERNAL, source_label=Label.CONFIDENTIAL):
    clock = _Clock()
    h = _Harness(label=label, source_label=source_label)
    ids = count(1)
    audit_ids = count(1)
    with SQLiteStore(
        tmp_path / "turn.db",
        utc_now=lambda: clock.utc,
        audit_id_factory=lambda: f"audit-{next(audit_ids)}",
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
            request_id_factory=lambda: f"request-{next(ids)}",
            approval_id_factory=lambda: f"approval-{next(ids)}",
            next_call_seq=h.sequence,
            failure_audit=h.failure_audit,
            utc_now=lambda: clock.utc,
            monotonic=lambda: clock.mono,
            approval_sleep=clock.sleep,
        )
        try:
            yield guard, store, h
        finally:
            await guard.aclose()


async def _rows(store):
    def read(connection):
        return tuple(
            tuple(tuple(row) for row in connection.execute(query))
            for query in (
                "SELECT * FROM tasks ORDER BY rowid",
                "SELECT * FROM requests ORDER BY rowid",
                "SELECT * FROM approvals ORDER BY rowid",
                "SELECT * FROM audit_events ORDER BY rowid",
            )
        )

    return await store._run_on_writer_async(read)


async def _pending(guard):
    result = await guard.submit("task", _call("send"), expected_epoch=1)
    assert isinstance(result, Pending)
    await _turns()
    return result, guard.pending_result(result.request_id)


def test_idle_interrupt_is_read_only_and_idempotent(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            before = await _rows(store)
            for _ in range(2):
                result = await guard.interrupt_turn("task", expected_epoch=1)
                assert result == TurnInterrupt(
                    outcome=TurnInterruptOutcome.NOTHING_IN_FLIGHT,
                    cancelled_approvals=0,
                    call_in_progress=False,
                )
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert await _rows(store) == before

    asyncio.run(run())


def test_waiting_approval_is_cancelled_once_and_new_calls_work(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            pending, handle = await _pending(guard)
            result = await guard.interrupt_turn("task", expected_epoch=1)
            assert result.outcome is TurnInterruptOutcome.APPROVAL_CANCELLED
            assert result.cancelled_approvals == 1
            assert result.call_in_progress is False
            final = await asyncio.wait_for(handle.wait(), 2)
            assert final.cause == "cancelled"
            assert isinstance(final.outcome, Denied)
            assert final.outcome.reasons == (Reason.TURN_INTERRUPTED,)
            assert (
                await store.get_approval(pending.approval_id)
            ).status is ApprovalStatus.CANCELLED
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert not h.transport.calls
            assert "RAW_SECRET_MARKER" not in repr(result) + repr(final) + repr(await _rows(store))
            events = await store._run_on_writer_async(
                lambda c: c.execute(
                    "SELECT reason_code FROM audit_events WHERE event_type='APPROVAL_CANCELLED'"
                ).fetchall()
            )
            assert [row[0] for row in events] == [Reason.TURN_INTERRUPTED.value]
            page = await store.list_approvals(approval_query(ApprovalListLane.HISTORY))
            row = next(r for r in page.rows if r.approval_id == pending.approval_id)
            assert row.reason_code is Reason.TURN_INTERRUPTED
            second = await guard.interrupt_turn("task", expected_epoch=1)
            assert second.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
            allowed = await guard.submit("task", _call("read"), expected_epoch=1)
            assert isinstance(allowed, Released)
            assert len(h.transport.calls) == 1
            # Reading a CONFIDENTIAL source can block a SEND, so use a fresh task for ASK below.

    asyncio.run(run())


def test_cancelled_task_can_open_a_new_ask(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            first, _ = await _pending(guard)
            assert (await guard.interrupt_turn("task", expected_epoch=1)).cancelled_approvals == 1
            next_pending, handle = await _pending(guard)
            assert next_pending.approval_id != first.approval_id
            assert (await store.load_task("task")).status is TaskStatus.WAITING_APPROVAL
            result = await guard.human_control("operator").approve(next_pending.approval_id)
            assert isinstance(result, Released)
            assert (await handle.wait()).cause == "executed"
            assert len(h.transport.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["read", "send"])
def test_active_transport_is_not_interrupted(tmp_path, kind):
    async def run():
        async with _real_guard(tmp_path, label=Label.PUBLIC, source_label=Label.INTERNAL) as (
            guard,
            store,
            h,
        ):
            started = asyncio.Event()
            release = asyncio.Event()
            original = h.transport.call

            async def blocked(req):
                started.set()
                await release.wait()
                if kind == "send":
                    raise RuntimeError("RAW_SECRET_MARKER")
                return await original(req)

            h.transport.call = blocked
            execution = asyncio.create_task(guard.submit("task", _call(kind), expected_epoch=1))
            try:
                await asyncio.wait_for(started.wait(), 3)
                result = await asyncio.wait_for(guard.interrupt_turn("task", expected_epoch=1), 3)
                assert result.outcome is TurnInterruptOutcome.CALL_COMPLETING
                assert result.call_in_progress is True
                assert result.cancelled_approvals == 0
                assert not execution.done()
            finally:
                release.set()
            finished = await asyncio.wait_for(execution, 3)
            if kind == "read":
                assert isinstance(finished, Released)
                state = await store.load_task("task")
                assert state.status is TaskStatus.RUNNING
                assert state.high_watermark is Label.INTERNAL
            else:
                assert isinstance(finished, OutcomeUnknown)
                assert (await store.load_task("task")).status is TaskStatus.UNKNOWN_OUTCOME
            assert "RAW_SECRET_MARKER" not in repr(result) + repr(finished)

    asyncio.run(run())


def test_wrong_epoch_halted_and_missing_are_read_only(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            before = await _rows(store)
            for task_id, epoch in (("task", 2), ("missing", 1)):
                result = await guard.interrupt_turn(task_id, expected_epoch=epoch)
                assert result.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
                assert await _rows(store) == before
            await guard.human_control("operator").emergency_halt("task")
            before = await _rows(store)
            result = await guard.interrupt_turn("task", expected_epoch=1)
            assert result.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
            assert await _rows(store) == before

    asyncio.run(run())


@pytest.mark.parametrize(
    ("task_id", "epoch"), [("", 1), ("\ud800", 1), ("task", -1), ("task", True)]
)
def test_invalid_identity_is_fixed_and_does_not_leak(tmp_path, task_id, epoch):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            before = await _rows(store)
            with pytest.raises(ApprovalConfigError) as exc:
                await guard.interrupt_turn(task_id, expected_epoch=epoch)
            assert "RAW_SECRET_MARKER" not in str(exc.value)
            assert await _rows(store) == before

    asyncio.run(run())


def test_guard_close_rejects_turn_interrupt(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, _, _):
            await guard.aclose()
            with pytest.raises(StoreError, match="GUARD_CLOSED") as exc:
                await guard.interrupt_turn("task", expected_epoch=1)
            assert exc.value.reason is Reason.GUARD_CLOSED
            assert "RAW_SECRET_MARKER" not in str(exc.value)

    asyncio.run(run())


def test_concurrent_approval_and_interrupt_choose_exactly_one(tmp_path, capsys):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            counts = {"cancelled": 0, "executed": 0}
            for i in range(50):
                pending, handle = await _pending(guard)
                previous_calls = len(h.transport.calls)
                control = guard.human_control("operator")
                if i % 2:
                    tasks = [
                        asyncio.create_task(control.approve(pending.approval_id)),
                        asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1)),
                    ]
                    approval, interrupt = await asyncio.gather(*tasks)
                else:
                    tasks = [
                        asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1)),
                        asyncio.create_task(control.approve(pending.approval_id)),
                    ]
                    interrupt, approval = await asyncio.gather(*tasks)
                final = await asyncio.wait_for(handle.wait(), 3)
                record = await store.get_approval(pending.approval_id)
                assert final.cause in counts
                counts[final.cause] += 1
                if final.cause == "cancelled":
                    assert interrupt.cancelled_approvals == 1
                    assert record.status is ApprovalStatus.CANCELLED
                    assert isinstance(approval, Denied)
                    assert len(h.transport.calls) == previous_calls
                else:
                    assert record.status is ApprovalStatus.CONSUMED
                    assert isinstance(approval, Released)
                    assert len(h.transport.calls) == previous_calls + 1
                assert (await store.load_task("task")).status is TaskStatus.RUNNING
            print(f"turn race: cancelled={counts['cancelled']}, executed={counts['executed']}")

    asyncio.run(run())
    print(capsys.readouterr().out.strip())


def test_interrupt_waits_for_opening_without_requesting_stop(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            entered, release = asyncio.Event(), asyncio.Event()
            original = store.open_approval

            async def delayed(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)

            store.open_approval = delayed
            submit = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await _turns()
                assert not interrupt.done()
                signal = guard._stop_requests.get("task")
                assert signal is None or not signal.is_set()
            finally:
                release.set()
            pending = await asyncio.wait_for(submit, 3)
            assert isinstance(pending, Pending)
            result = await asyncio.wait_for(interrupt, 3)
            assert result.cancelled_approvals == 1
            assert result.outcome in (
                TurnInterruptOutcome.APPROVAL_CANCELLED,
                TurnInterruptOutcome.CALL_COMPLETING,
            )
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            final = await asyncio.wait_for(guard.pending_result(pending.request_id).wait(), 3)
            assert final.cause == "cancelled"
            assert h.transport.calls == []

    asyncio.run(run())


def test_interrupt_leaves_other_task_pending_and_callable(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            state = (await store.load_task("task")).model_copy(update={"task_id": "other"})
            await store.create_task(state, created_at_utc=h.now)
            first, first_handle = await _pending(guard)
            second = await guard.submit("other", _call("send"), expected_epoch=1)
            assert isinstance(second, Pending)
            second_handle = guard.pending_result(second.request_id)
            result = await guard.interrupt_turn("task", expected_epoch=1)
            assert result.cancelled_approvals == 1
            assert (await first_handle.wait()).cause == "cancelled"
            assert (await store.get_approval(first.approval_id)).status is ApprovalStatus.CANCELLED
            assert (await store.get_approval(second.approval_id)).status is ApprovalStatus.PENDING
            assert (await store.load_task("other")).status is TaskStatus.WAITING_APPROVAL
            assert not second_handle._completed
            assert isinstance(
                await guard.human_control("operator").approve(second.approval_id), Released
            )
            assert (await second_handle.wait()).cause == "executed"
            assert len(h.transport.calls) == 1

    asyncio.run(run())


def test_approved_send_already_consumed_keeps_running(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            pending, handle = await _pending(guard)
            started, release = asyncio.Event(), asyncio.Event()
            original = h.transport.call

            async def blocked(req):
                started.set()
                await release.wait()
                return await original(req)

            h.transport.call = blocked
            approval = asyncio.create_task(
                guard.human_control("operator").approve(pending.approval_id)
            )
            try:
                await asyncio.wait_for(started.wait(), 3)
                assert (
                    await store.get_approval(pending.approval_id)
                ).status is ApprovalStatus.CONSUMED
                interrupt = await asyncio.wait_for(
                    guard.interrupt_turn("task", expected_epoch=1), 3
                )
                assert interrupt.outcome is TurnInterruptOutcome.CALL_COMPLETING
                assert interrupt.call_in_progress
                assert interrupt.cancelled_approvals == 0
                assert not approval.done()
            finally:
                release.set()
            assert isinstance(await asyncio.wait_for(approval, 3), Released)
            assert (await handle.wait()).cause == "executed"
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert len(h.transport.calls) == 1

    asyncio.run(run())


def test_stale_epoch_does_not_cancel_pending_approval(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            pending, handle = await _pending(guard)
            before = await _rows(store)
            result = await guard.interrupt_turn("task", expected_epoch=2)
            assert result.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
            assert await _rows(store) == before
            assert (await store.get_approval(pending.approval_id)).status is ApprovalStatus.PENDING
            assert not handle._completed

    asyncio.run(run())


def test_stale_approval_command_cannot_hide_pending_from_interrupt(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            pending, handle = await _pending(guard)
            record = await store.get_approval(pending.approval_id)
            entered, release = asyncio.Event(), asyncio.Event()
            original = store.get_approval
            calls = 0

            async def blocked(approval_id):
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set()
                    await release.wait()
                return await original(approval_id)

            store.get_approval = blocked
            approve = asyncio.create_task(
                guard.human_control("operator").approve(
                    pending.approval_id, expected_row_revision=record.row_revision + 1
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 3)
                interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await _turns()
            finally:
                release.set()
            denied = await asyncio.wait_for(approve, 3)
            outcome = await asyncio.wait_for(interrupt, 3)
            assert denied.reasons == (Reason.APPROVAL_RACE,)
            assert outcome.cancelled_approvals == 1
            assert (
                await store.get_approval(pending.approval_id)
            ).status is ApprovalStatus.CANCELLED
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert (await handle.wait()).cause == "cancelled"
            assert h.transport.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["store_error", "cancelled"])
def test_interrupt_finish_failure_closes_guard_and_notifies_once(tmp_path, failure):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            _, handle = await _pending(guard)
            entered, release = asyncio.Event(), asyncio.Event()
            original = store.finish_approval

            async def failing(*args, **kwargs):
                entered.set()
                await release.wait()
                raise StoreError(Reason.STATE_COMMIT_FAILED)

            store.finish_approval = failing
            attempt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if failure == "cancelled":
                    attempt.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await attempt
                else:
                    release.set()
                    with pytest.raises(StoreError):
                        await asyncio.wait_for(attempt, 3)
                assert guard._global_halt
                final = await asyncio.wait_for(handle.wait(), 2)
                assert final.cause == "failed"
                assert isinstance(final.outcome, Denied)
                assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
                assert h.transport.calls == []
            finally:
                release.set()
                store.finish_approval = original

    asyncio.run(run())


def test_approved_but_unconsumed_approval_can_be_cancelled(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            pending, handle = await _pending(guard)
            record = await store.get_approval(pending.approval_id)
            await store.transition_approval(
                pending.approval_id,
                ApprovalStatus.APPROVED,
                expected_row_revision=record.row_revision,
                approver_id="operator",
                now_utc=record.created_at_utc,
                reason=Reason.OK,
            )
            result = await guard.interrupt_turn("task", expected_epoch=1)
            assert result.cancelled_approvals == 1
            assert (
                await store.get_approval(pending.approval_id)
            ).status is ApprovalStatus.CANCELLED
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert (await handle.wait()).cause == "cancelled"
            assert h.transport.calls == []

    asyncio.run(run())


def test_queued_submission_does_not_hide_existing_pending_approval(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            pending, handle = await _pending(guard)
            lock = guard._task_lock("task")
            await lock.acquire()
            submit = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            try:
                await _turns()
                interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await _turns()
            finally:
                lock.release()
            await asyncio.wait_for(submit, 3)
            result = await asyncio.wait_for(interrupt, 3)
            assert result.cancelled_approvals == 1
            assert (
                await store.get_approval(pending.approval_id)
            ).status is ApprovalStatus.CANCELLED
            assert (await handle.wait()).cause == "cancelled"
            assert (await store.load_task("task")).status is TaskStatus.RUNNING

    asyncio.run(run())


def test_guard_close_during_task_lookup_rejects_interrupt(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            entered, release = asyncio.Event(), asyncio.Event()
            original = store.load_task

            async def blocked(task_id):
                entered.set()
                await release.wait()
                return await original(task_id)

            store.load_task = blocked
            interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await guard.aclose()
            finally:
                release.set()
            with pytest.raises(StoreError, match="GUARD_CLOSED") as exc:
                await asyncio.wait_for(interrupt, 3)
            assert exc.value.reason is Reason.GUARD_CLOSED
            store.load_task = original

    asyncio.run(run())


def test_repeated_cancellation_while_waiting_for_task_lock_leaves_no_lock_leak(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            pending, handle = await _pending(guard)
            lock = guard._task_lock("task")
            await lock.acquire()
            interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
            try:
                await _turns()
                interrupt.cancel()
                lock.release()
                interrupt.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await interrupt
                await asyncio.wait_for(lock.acquire(), 3)
                lock.release()
                assert not guard._global_halt
                assert not handle._completed
                assert (
                    await store.get_approval(pending.approval_id)
                ).status is ApprovalStatus.PENDING
                assert (
                    await guard.interrupt_turn("task", expected_epoch=1)
                ).cancelled_approvals == 1
                assert (await asyncio.wait_for(handle.wait(), 3)).cause == "cancelled"
            finally:
                if lock.locked():
                    lock.release()

    asyncio.run(run())


def test_cancelling_interrupt_during_read_only_lookup_does_not_halt(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, _):
            pending, handle = await _pending(guard)
            before = await _rows(store)
            entered = asyncio.Event()
            original = store.load_task

            async def blocked(task_id):
                entered.set()
                await asyncio.Event().wait()
                return await original(task_id)

            store.load_task = blocked
            interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
            await asyncio.wait_for(entered.wait(), 3)
            interrupt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await interrupt
            store.load_task = original
            assert not guard._global_halt
            assert not handle._completed
            assert await _rows(store) == before
            assert (await store.get_approval(pending.approval_id)).status is ApprovalStatus.PENDING
            assert (await guard.interrupt_turn("task", expected_epoch=1)).cancelled_approvals == 1
            assert (await handle.wait()).cause == "cancelled"

    asyncio.run(run())


@pytest.mark.parametrize("method", ["load_task", "finish_approval"])
def test_unexpected_error_fails_closed_without_exception_text(tmp_path, method):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            pending, handle = await _pending(guard)
            original = getattr(store, method)

            async def fail(*args, **kwargs):
                raise RuntimeError("RAW_SECRET_MARKER")

            setattr(store, method, fail)
            try:
                with pytest.raises(StoreError, match="INTERNAL_ERROR") as caught:
                    await guard.interrupt_turn("task", expected_epoch=1)
                assert caught.value.reason is Reason.INTERNAL_ERROR
                assert "RAW_SECRET_MARKER" not in str(caught.value)
                assert "RAW_SECRET_MARKER" not in repr(caught.value.__cause__)
                assert "RAW_SECRET_MARKER" not in repr(caught.value.__context__)
                assert guard._global_halt
                final = await asyncio.wait_for(handle.wait(), 2)
                assert final.cause == "failed"
                assert isinstance(final.outcome, Denied)
                assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
                assert "RAW_SECRET_MARKER" not in repr(final)
                assert "RAW_SECRET_MARKER" not in repr(await _rows(store))
                assert h.transport.calls == []
                assert (
                    await store.get_approval(pending.approval_id)
                ).status is ApprovalStatus.PENDING
            finally:
                setattr(store, method, original)

    asyncio.run(run())


def _pause_first_resolution(h):
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.catalog.aresolve
    calls = 0

    async def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    h.catalog.aresolve = paused
    return entered, release


def test_interrupt_during_decision_cancels_only_the_approval_it_later_opens(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            entered, release = _pause_first_resolution(h)
            first = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                interrupt = await asyncio.wait_for(
                    guard.interrupt_turn("task", expected_epoch=1), 3
                )
                assert interrupt.outcome is TurnInterruptOutcome.CALL_COMPLETING
                assert interrupt.call_in_progress
                assert interrupt.cancelled_approvals == 0
            finally:
                release.set()
            pending = await asyncio.wait_for(first, 3)
            assert isinstance(pending, Pending)
            final = await asyncio.wait_for(guard.pending_result(pending.request_id).wait(), 3)
            assert final.cause == "cancelled"
            assert final.outcome.reasons == (Reason.TURN_INTERRUPTED,)
            assert (
                await store.get_approval(pending.approval_id)
            ).status is ApprovalStatus.CANCELLED
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert h.transport.calls == []

    asyncio.run(run())


def test_submission_after_interrupt_keeps_its_own_approval(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            entered, release = _pause_first_resolution(h)
            first = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                interrupt = await asyncio.wait_for(
                    guard.interrupt_turn("task", expected_epoch=1), 3
                )
                assert interrupt.outcome is TurnInterruptOutcome.CALL_COMPLETING
                second = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
                await _turns()
                assert not second.done()
            finally:
                release.set()
            old = await asyncio.wait_for(first, 3)
            assert isinstance(old, Pending)
            assert (await guard.pending_result(old.request_id).wait()).cause == "cancelled"
            new = await asyncio.wait_for(second, 3)
            assert isinstance(new, Pending)
            assert new.approval_id != old.approval_id
            assert (await store.get_approval(new.approval_id)).status is ApprovalStatus.PENDING
            assert (await store.load_task("task")).status is TaskStatus.WAITING_APPROVAL
            handle = guard.pending_result(new.request_id)
            assert not handle._completed
            assert isinstance(
                await guard.human_control("operator").approve(new.approval_id), Released
            )
            assert (await handle.wait()).cause == "executed"
            assert len(h.transport.calls) == 1

    asyncio.run(run())


def test_auto_cancellation_and_human_approval_have_one_winner(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            entered, resolve = _pause_first_resolution(h)
            opened, publish = asyncio.Event(), asyncio.Event()
            original_open = guard._approvals.open

            async def paused_open(*args, **kwargs):
                record = await original_open(*args, **kwargs)
                opened.set()
                await publish.wait()
                return record

            guard._approvals.open = paused_open
            submit = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                interrupt = await asyncio.wait_for(
                    guard.interrupt_turn("task", expected_epoch=1), 3
                )
                assert interrupt.outcome is TurnInterruptOutcome.CALL_COMPLETING
                resolve.set()
                await asyncio.wait_for(opened.wait(), 3)
                approval_id = next(iter(guard._approvals._pending))
                approval = asyncio.create_task(guard.human_control("operator").approve(approval_id))
                await _turns()
            finally:
                resolve.set()
                publish.set()
            pending = await asyncio.wait_for(submit, 3)
            assert isinstance(pending, Pending)
            result = await asyncio.wait_for(approval, 3)
            handle = guard.pending_result(pending.request_id)
            final = await asyncio.wait_for(handle.wait(), 3)
            with pytest.raises(PendingResultError):
                await handle.wait()
            record = await store.get_approval(pending.approval_id)
            if final.cause == "cancelled":
                assert record.status is ApprovalStatus.CANCELLED
                assert isinstance(result, Denied)
                assert len(h.transport.calls) == 0
            else:
                assert final.cause == "executed"
                assert record.status is ApprovalStatus.CONSUMED
                assert isinstance(result, Released)
                assert len(h.transport.calls) == 1
            assert (await store.load_task("task")).status is TaskStatus.RUNNING

    asyncio.run(run())


def test_interrupt_marks_existing_submit_before_awaiting_task_state(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            deciding, resolve = _pause_first_resolution(h)
            loading, finish_load = asyncio.Event(), asyncio.Event()
            original_load = store.load_task
            interrupt_task = None

            async def blocked_load(task_id):
                if asyncio.current_task() is interrupt_task:
                    loading.set()
                    await finish_load.wait()
                return await original_load(task_id)

            store.load_task = blocked_load
            old_submit = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
            try:
                await asyncio.wait_for(deciding.wait(), 3)
                interrupt_task = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await asyncio.wait_for(loading.wait(), 3)
                resolve.set()
                old = await asyncio.wait_for(old_submit, 3)
                assert isinstance(old, Pending)
                old_final = await asyncio.wait_for(guard.pending_result(old.request_id).wait(), 3)
                assert old_final.cause == "cancelled"
                new = await asyncio.wait_for(
                    guard.submit("task", _call("send"), expected_epoch=1), 3
                )
                assert isinstance(new, Pending)
                assert (await store.get_approval(new.approval_id)).status is ApprovalStatus.PENDING
            finally:
                resolve.set()
                finish_load.set()
            await asyncio.wait_for(interrupt_task, 3)
            assert (await store.get_approval(new.approval_id)).status is ApprovalStatus.PENDING
            assert (await store.load_task("task")).status is TaskStatus.WAITING_APPROVAL
            assert not guard.pending_result(new.request_id)._completed
            assert h.transport.calls == []
            store.load_task = original_load

    asyncio.run(run())


def test_interrupt_with_no_original_call_ignores_later_pending_and_reports_nothing(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            loading, finish_load = asyncio.Event(), asyncio.Event()
            original_load = store.load_task
            interrupt_task = None

            async def blocked_load(task_id):
                if asyncio.current_task() is interrupt_task:
                    loading.set()
                    await finish_load.wait()
                return await original_load(task_id)

            store.load_task = blocked_load
            interrupt_task = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
            try:
                await asyncio.wait_for(loading.wait(), 3)
                pending = await asyncio.wait_for(
                    guard.submit("task", _call("send"), expected_epoch=1), 3
                )
                assert isinstance(pending, Pending)
            finally:
                finish_load.set()
            result = await asyncio.wait_for(interrupt_task, 3)
            assert result.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
            assert not result.call_in_progress
            assert result.cancelled_approvals == 0
            assert (await store.get_approval(pending.approval_id)).status is ApprovalStatus.PENDING
            assert (await store.load_task("task")).status is TaskStatus.WAITING_APPROVAL
            assert not guard.pending_result(pending.request_id)._completed
            assert h.transport.calls == []
            store.load_task = original_load

    asyncio.run(run())


def test_interrupt_counts_automatic_cancel_that_finishes_while_lock_waits(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, store, h):
            deciding, resolve = _pause_first_resolution(h)
            loading, finish_load = asyncio.Event(), asyncio.Event()
            opened, publish = asyncio.Event(), asyncio.Event()
            acquiring, finish_acquire = asyncio.Event(), asyncio.Event()
            original_load = store.load_task
            original_open = guard._approvals.open
            original_acquire = guard._turn_acquire_or_call
            interrupt_task = None
            load_count = 0

            async def blocked_load(task_id):
                nonlocal load_count
                if asyncio.current_task() is interrupt_task:
                    load_count += 1
                    if load_count == 1:
                        loading.set()
                        await finish_load.wait()
                return await original_load(task_id)

            async def paused_open(*args, **kwargs):
                record = await original_open(*args, **kwargs)
                opened.set()
                await publish.wait()
                return record

            async def delayed_acquire(task_id, epoch):
                acquiring.set()
                await finish_acquire.wait()
                return await original_acquire(task_id, epoch)

            store.load_task = blocked_load
            guard._approvals.open = paused_open
            guard._turn_acquire_or_call = delayed_acquire
            submit = asyncio.create_task(guard.submit("task", _call("send"), expected_epoch=1))
            try:
                await asyncio.wait_for(deciding.wait(), 3)
                interrupt_task = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await asyncio.wait_for(loading.wait(), 3)
                resolve.set()
                await asyncio.wait_for(opened.wait(), 3)
                finish_load.set()
                await asyncio.wait_for(acquiring.wait(), 3)
                publish.set()
                pending = await asyncio.wait_for(submit, 3)
                assert isinstance(pending, Pending)
                assert (await guard.pending_result(pending.request_id).wait()).cause == "cancelled"
            finally:
                resolve.set()
                finish_load.set()
                publish.set()
                finish_acquire.set()
            result = await asyncio.wait_for(interrupt_task, 3)
            assert result.outcome is TurnInterruptOutcome.APPROVAL_CANCELLED
            assert result.cancelled_approvals == 1
            assert not result.call_in_progress
            assert (
                await store.get_approval(pending.approval_id)
            ).status is ApprovalStatus.CANCELLED
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert h.transport.calls == []
            store.load_task = original_load

    asyncio.run(run())


def test_interrupt_does_not_wait_for_prior_lock_waiter_after_dispatch_starts(tmp_path):
    async def run():
        async with _real_guard(tmp_path) as (guard, _, h):
            pending, handle = await _pending(guard)
            started, finish_transport = asyncio.Event(), asyncio.Event()
            original_transport = h.transport.call
            original_acquire = guard._turn_acquire_or_call
            lock = guard._task_lock("task")
            await lock.acquire()
            released = False

            async def blocked_transport(req):
                started.set()
                await finish_transport.wait()
                return await original_transport(req)

            async def release_then_acquire(task_id, epoch):
                nonlocal released
                lock.release()
                released = True
                return await original_acquire(task_id, epoch)

            h.transport.call = blocked_transport
            guard._turn_acquire_or_call = release_then_acquire
            approval = asyncio.create_task(
                guard.human_control("operator").approve(pending.approval_id)
            )
            interrupt = None
            try:
                await _turns()
                assert not approval.done()
                interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await asyncio.wait_for(started.wait(), 3)
                result = await asyncio.wait_for(asyncio.shield(interrupt), 3)
                assert result.outcome is TurnInterruptOutcome.CALL_COMPLETING
                assert result.call_in_progress
                assert result.cancelled_approvals == 0
                assert not approval.done()
            finally:
                finish_transport.set()
                if not released:
                    lock.release()
            assert isinstance(await asyncio.wait_for(approval, 3), Released)
            assert (await handle.wait()).cause == "executed"
            if interrupt is not None and not interrupt.done():
                await asyncio.wait_for(interrupt, 3)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("kind", "label"),
    [("read", Label.PUBLIC), ("send", Label.PUBLIC), ("send", Label.INTERNAL)],
    ids=["read-allow", "send-allow", "send-ask"],
)
def test_queued_submission_is_denied_and_later_submission_is_unchanged(tmp_path, kind, label):
    async def run():
        async with _real_guard(tmp_path, label=label, source_label=label) as (guard, store, h):
            entered, release = asyncio.Event(), asyncio.Event()
            original_call = h.transport.call
            original_reserve = store.reserve_dispatch
            original_open = store.open_approval
            host_calls, reservations, approvals = [], [], []

            async def call(req):
                host_calls.append(req.request_id)
                if len(host_calls) == 1:
                    entered.set()
                    await release.wait()
                return await original_call(req)

            async def reserve(request_id, **kwargs):
                reservations.append(request_id)
                return await original_reserve(request_id, **kwargs)

            async def open_approval(*args, **kwargs):
                approvals.append(args[0].approval_id)
                return await original_open(*args, **kwargs)

            guard._failure_audit = durable_failure_audit(store)
            h.transport.call = call
            store.reserve_dispatch = reserve
            store.open_approval = open_approval
            first = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            second = third = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                second = asyncio.create_task(guard.submit("task", _call(kind), expected_epoch=1))
                await _turns()
                assert not second.done()
                assert len(guard._turn_instances) == 2
                interrupted = await asyncio.wait_for(
                    guard.interrupt_turn("task", expected_epoch=1), 3
                )
                assert interrupted.outcome is TurnInterruptOutcome.CALL_COMPLETING
                assert interrupted.call_in_progress
                assert interrupted.cancelled_approvals == 0
                third = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
                await _turns()
                assert not third.done()
            finally:
                release.set()
            assert isinstance(await asyncio.wait_for(first, 3), Released)
            denied = await asyncio.wait_for(second, 3)
            assert isinstance(denied, Denied)
            assert denied.request_id is None
            assert denied.reasons == (Reason.TURN_INTERRUPTED,)
            assert isinstance(await asyncio.wait_for(third, 3), Released)
            assert len(host_calls) == len(h.transport.calls) == 2
            assert len(reservations) == 2
            assert not approvals
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            requests = await store._run_on_writer_async(
                lambda c: c.execute(
                    "SELECT request_id,status FROM requests ORDER BY rowid"
                ).fetchall()
            )
            assert len(requests) == 2
            assert all(row[1] == "SUCCEEDED" for row in requests)
            assert (await _rows(store))[2] == ()
            audit = await store._run_on_writer_async(
                lambda c: c.execute(
                    "SELECT request_id,task_id,event_type,reason_code,detail "
                    "FROM audit_events WHERE event_type='FAILURE_BEFORE_DISPATCH'"
                ).fetchall()
            )
            assert len(audit) == 1
            assert tuple(audit[0][:4]) == (
                None,
                "task",
                "FAILURE_BEFORE_DISPATCH",
                "TURN_INTERRUPTED",
            )
            assert "RAW_SECRET_MARKER" not in repr(denied) + repr(await _rows(store))

    asyncio.run(run())


def test_wrong_epoch_interrupt_does_not_deny_a_queued_submission(tmp_path):
    async def run():
        async with _real_guard(tmp_path, label=Label.PUBLIC, source_label=Label.PUBLIC) as (
            guard,
            store,
            h,
        ):
            entered, release = asyncio.Event(), asyncio.Event()
            original = h.transport.call
            calls = 0

            async def blocked(req):
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set()
                    await release.wait()
                return await original(req)

            h.transport.call = blocked
            first = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                second = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
                await _turns()
                assert not second.done()
                result = await asyncio.wait_for(guard.interrupt_turn("task", expected_epoch=2), 3)
                assert result.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
            finally:
                release.set()
            assert isinstance(await asyncio.wait_for(first, 3), Released)
            assert isinstance(await asyncio.wait_for(second, 3), Released)
            assert len(h.transport.calls) == 2
            assert (await store.load_task("task")).status is TaskStatus.RUNNING
            assert h.audit == []

    asyncio.run(run())


def test_submit_arriving_during_interrupt_lookup_is_not_in_the_snapshot(tmp_path):
    async def run():
        async with _real_guard(tmp_path, label=Label.PUBLIC, source_label=Label.PUBLIC) as (
            guard,
            store,
            h,
        ):
            active, release = asyncio.Event(), asyncio.Event()
            lookup, continue_lookup = asyncio.Event(), asyncio.Event()
            original_call = h.transport.call
            original_load = store.load_task
            interrupt = None
            calls = 0

            async def blocked_call(req):
                nonlocal calls
                calls += 1
                if calls == 1:
                    active.set()
                    await release.wait()
                return await original_call(req)

            async def blocked_load(task_id):
                if asyncio.current_task() is interrupt:
                    lookup.set()
                    await continue_lookup.wait()
                return await original_load(task_id)

            h.transport.call = blocked_call
            store.load_task = blocked_load
            first = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            second = None
            try:
                await asyncio.wait_for(active.wait(), 3)
                interrupt = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await asyncio.wait_for(lookup.wait(), 3)
                second = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
                await _turns()
                assert not second.done()
                continue_lookup.set()
                result = await asyncio.wait_for(interrupt, 3)
                assert result.outcome is TurnInterruptOutcome.CALL_COMPLETING
            finally:
                continue_lookup.set()
                release.set()
            assert isinstance(await asyncio.wait_for(first, 3), Released)
            assert isinstance(await asyncio.wait_for(second, 3), Released)
            assert len(h.transport.calls) == 2
            assert h.audit == []
            assert (await store.load_task("task")).status is TaskStatus.RUNNING

    asyncio.run(run())


def test_multiple_interrupts_deny_a_matching_waiter_once(tmp_path):
    async def run():
        async with _real_guard(tmp_path, label=Label.PUBLIC, source_label=Label.PUBLIC) as (
            guard,
            store,
            h,
        ):
            entered, release = asyncio.Event(), asyncio.Event()
            original = h.transport.call

            async def blocked(req):
                entered.set()
                await release.wait()
                return await original(req)

            h.transport.call = blocked
            first = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                second = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
                await _turns()
                assert not second.done()
                wrong, right, again = await asyncio.gather(
                    guard.interrupt_turn("task", expected_epoch=2),
                    guard.interrupt_turn("task", expected_epoch=1),
                    guard.interrupt_turn("task", expected_epoch=1),
                )
                assert wrong.outcome is TurnInterruptOutcome.NOTHING_IN_FLIGHT
                assert right.outcome is TurnInterruptOutcome.CALL_COMPLETING
                assert again.outcome is TurnInterruptOutcome.CALL_COMPLETING
            finally:
                release.set()
            assert isinstance(await asyncio.wait_for(first, 3), Released)
            result = await asyncio.wait_for(second, 3)
            assert isinstance(result, Denied)
            assert result.request_id is None
            assert result.reasons == (Reason.TURN_INTERRUPTED,)
            assert len(h.audit) == 1
            assert h.audit[0].reason is Reason.TURN_INTERRUPTED
            assert h.audit[0].request_id is None
            assert len(h.transport.calls) == 1
            assert (await store.load_task("task")).status is TaskStatus.RUNNING

    asyncio.run(run())


def test_interrupted_waiter_audit_failure_halts_globally_without_raw_exception(tmp_path):
    async def run():
        async with _real_guard(tmp_path, label=Label.PUBLIC, source_label=Label.PUBLIC) as (
            guard,
            store,
            h,
        ):
            entered, release = asyncio.Event(), asyncio.Event()
            original = h.transport.call

            async def blocked(req):
                entered.set()
                await release.wait()
                return await original(req)

            h.transport.call = blocked
            h.audit_error = RuntimeError("RAW_SECRET_MARKER")
            first = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                second = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
                await _turns()
                await guard.interrupt_turn("task", expected_epoch=1)
            finally:
                release.set()
            assert isinstance(await asyncio.wait_for(first, 3), Released)
            result = await asyncio.wait_for(second, 3)
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert guard._global_halt
            assert len(h.transport.calls) == 1
            assert "RAW_SECRET_MARKER" not in repr(result)
            assert "RAW_SECRET_MARKER" not in repr(await _rows(store))
            assert isinstance(await guard.submit("task", _call("read"), expected_epoch=1), Denied)
            assert len(h.transport.calls) == 1

    asyncio.run(run())


def test_cancelled_interrupt_validation_releases_queued_submission(tmp_path):
    async def run():
        async with _real_guard(tmp_path, label=Label.PUBLIC, source_label=Label.PUBLIC) as (
            guard,
            store,
            h,
        ):
            entered, release = asyncio.Event(), asyncio.Event()
            checking = asyncio.Event()
            original_call = h.transport.call
            original_load = store.load_task
            interrupted = None
            calls = 0

            async def blocked_call(req):
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set()
                    await release.wait()
                return await original_call(req)

            async def blocked_load(task_id):
                if asyncio.current_task() is interrupted:
                    checking.set()
                    await asyncio.Event().wait()
                return await original_load(task_id)

            h.transport.call = blocked_call
            store.load_task = blocked_load
            first = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                second = asyncio.create_task(guard.submit("task", _call("read"), expected_epoch=1))
                await _turns()
                interrupted = asyncio.create_task(guard.interrupt_turn("task", expected_epoch=1))
                await asyncio.wait_for(checking.wait(), 3)
                interrupted.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await interrupted
            finally:
                release.set()
            assert isinstance(await asyncio.wait_for(first, 3), Released)
            assert isinstance(await asyncio.wait_for(second, 3), Released)
            assert not guard._global_halt
            assert h.audit == []
            assert len(h.transport.calls) == 2
            assert (await store.load_task("task")).status is TaskStatus.RUNNING

    asyncio.run(run())
