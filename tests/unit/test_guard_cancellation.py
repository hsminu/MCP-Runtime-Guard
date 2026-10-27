"""B7.2 A 리뷰 후속: 예약 전 Task 취소 격리와 예약 후 의미 보존(I6/I8/I9).

전송 계수는 B 단위 Transport 대역이다. 실제 wire·A shield 연결 검증은 아니다.
"""

import asyncio
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
from guard_app.domain.enums import ApprovalStatus, Label, RequestStatus, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_policy_change import _PolicyHarness


def healthy(h):
    h.store.states["healthy"] = h.store.states["task"].model_copy(
        update={"task_id": "healthy", "status": TaskStatus.RUNNING, "high_watermark": Label.PUBLIC}
    )


def assert_cancelled_denial(h, outcome):
    assert isinstance(outcome, Denied)
    assert_cancelled_task(h)


def assert_cancelled_task(h):
    assert not h.guard._global_halt
    assert h.store.states["task"].status is TaskStatus.HALTED
    assert not [req for req in h.transport.calls if req.task_id == "task"]
    assert h.audit[-1].task_id == "task"
    assert h.audit[-1].phase == "before_dispatch"
    assert h.audit[-1].exception_type == "CancelledError"


@pytest.mark.parametrize("stage", ["load", "sequence", "decision"])
def test_actual_submit_cancellation_halts_only_its_task_before_reservation(stage):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        entered = asyncio.Event()
        once = True

        async def pause():
            nonlocal once
            if once:
                once = False
                entered.set()
                await asyncio.Event().wait()

        if stage == "load":
            original = h.store.load_task

            async def load(task_id):
                if task_id == "task":
                    await pause()
                return await original(task_id)

            h.store.load_task = load
        elif stage == "sequence":
            original = h.guard._next_call_seq

            async def sequence(task_id):
                if task_id == "task":
                    await pause()
                return await original(task_id)

            h.guard._next_call_seq = sequence
        else:
            original = h.store.record_decision

            async def decision(req, policy, **kwargs):
                if req.task_id == "task":
                    await pause()
                return await original(req, policy, **kwargs)

            h.store.record_decision = decision

        work = asyncio.create_task(h.guard.submit("task", _call(), expected_epoch=1))
        await entered.wait()
        work.cancel("PRIVATE_cancel_message")
        outcome = await asyncio.wait_for(work, 1)
        assert not work.cancelled()  # 기존 Denied 변환을 유지한다.
        assert_cancelled_denial(h, outcome)
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        assert [req.task_id for req in h.transport.calls] == ["healthy"]
        assert "PRIVATE_" not in repr(h.audit) + repr(outcome)

    asyncio.run(scenario())


def test_access_checker_cancelled_error_is_also_task_local():
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        h.access.error = asyncio.CancelledError("PRIVATE_checker")
        outcome = await h.guard.submit("task", _call())
        assert_cancelled_denial(h, outcome)
        assert "PRIVATE_checker" not in repr(outcome) + repr(h.audit)
        h.access.error = None
        assert isinstance(await h.guard.submit("healthy", _call()), Released)

    asyncio.run(scenario())


@pytest.mark.parametrize("replacement", [False, True])
def test_cancelled_lock_waiter_waits_for_owner_without_blocking_other_tasks(replacement):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        lock = h.guard._task_lock("task")
        await lock.acquire()
        work = asyncio.create_task(h.guard.submit("task", _call(), expected_epoch=1))
        await asyncio.sleep(0)
        work.cancel()
        await asyncio.sleep(0)
        assert not work.done()
        assert h.store.states["task"].status is TaskStatus.RUNNING
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        if replacement:
            h.store.states["task"] = h.store.states["task"].model_copy(update={"epoch": 2})
        lock.release()
        outcome = await asyncio.wait_for(work, 1)
        assert isinstance(outcome, Denied)
        assert not h.guard._global_halt
        assert not [req for req in h.transport.calls if req.task_id == "task"]
        assert h.store.states["task"].status is (
            TaskStatus.RUNNING if replacement else TaskStatus.HALTED
        )

    asyncio.run(scenario())


def test_cancellation_while_waiting_for_policy_barrier_halts_without_entering_decision():
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        h.guard._policy_ready.clear()
        work = asyncio.create_task(h.guard.submit("task", _call()))
        await asyncio.sleep(0)
        work.cancel()
        assert_cancelled_denial(h, await asyncio.wait_for(work, 1))
        assert h.store.requests == {}
        h.guard._policy_ready.set()
        assert isinstance(await h.guard.submit("healthy", _call()), Released)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["load", "status", "bad_status", "audit"])
def test_cancellation_containment_failure_still_halts_globally(failure):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        started = asyncio.Event()

        async def record_decision(req, decision, **kwargs):
            started.set()
            await asyncio.Event().wait()

        h.store.record_decision = record_decision
        work = asyncio.create_task(h.guard.submit("task", _call()))
        await started.wait()
        if failure in {"load", "status"}:
            h.store.fail_at.add(failure)
        elif failure == "audit":
            h.audit_error = RuntimeError("PRIVATE_audit")
        else:
            original = h.store.set_task_status

            async def bad_status(*args, **kwargs):
                result = await original(*args, **kwargs)
                return result.model_copy(update={"status": TaskStatus.RUNNING})

            h.store.set_task_status = bad_status
        work.cancel()
        outcome = await asyncio.wait_for(work, 1)
        assert isinstance(outcome, Denied)
        assert h.guard._global_halt
        assert h.transport.calls == []
        h.store.fail_at.clear()
        h.audit_error = None
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert h.transport.calls == []
        assert "PRIVATE_" not in repr(outcome) + repr(h.audit)

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["status", "audit"])
def test_cancellation_during_stop_recording_still_halts_globally(stage):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        entered = asyncio.Event()
        h.access.error = asyncio.CancelledError("PRIVATE_checker")

        async def interrupted(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        if stage == "status":
            h.store.set_task_status = interrupted
        else:
            h.guard._failure_audit = interrupted
        work = asyncio.create_task(h.guard.submit("task", _call()))
        await entered.wait()
        work.cancel("PRIVATE_containment_cancel")
        outcome = await asyncio.wait_for(work, 1)
        assert isinstance(outcome, Denied)
        assert h.guard._global_halt
        assert h.store.states["task"].status is (
            TaskStatus.RUNNING if stage == "status" else TaskStatus.HALTED
        )
        h.access.error = None
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert h.transport.calls == []
        assert "PRIVATE_" not in repr(outcome) + repr(h.audit)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["load", "decision", "reserve"])
def test_store_errors_keep_global_halt_for_other_tasks(failure):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        h.store.fail_at.add(failure)
        assert isinstance(await h.guard.submit("task", _call()), Denied)
        assert h.guard._global_halt
        h.store.fail_at.clear()
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        assert h.transport.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("approval", [False, True])
def test_cancellation_during_reservation_ack_keeps_existing_uncertainty_boundary(approval):
    async def scenario():
        h = _Harness(label=Label.INTERNAL if approval else Label.PUBLIC)
        healthy(h)
        started = asyncio.Event()
        if approval:
            pending = await h.guard.submit("task", _call("send"))
            assert isinstance(pending, Pending)
            original = h.store.consume_and_reserve

            async def reserve(**kwargs):
                await original(**kwargs)
                started.set()
                await asyncio.Event().wait()

            h.store.consume_and_reserve = reserve
            work = asyncio.create_task(
                h.guard.human_control("operator").approve(pending.approval_id)
            )
        else:
            original = h.store.reserve_dispatch

            async def reserve(*args, **kwargs):
                await original(*args, **kwargs)
                started.set()
                await asyncio.Event().wait()

            h.store.reserve_dispatch = reserve
            work = asyncio.create_task(h.guard.submit("task", _call("send")))
        await started.wait()
        work.cancel()
        assert isinstance(await asyncio.wait_for(work, 1), Denied)  # 기존 ACK 불명 반환 보존.
        assert h.guard._global_halt
        assert h.transport.calls == []
        assert RequestStatus.DISPATCHING in h.store.statuses.values()
        if approval:
            assert h.store.approvals[pending.approval_id].status is ApprovalStatus.CONSUMED
        assert isinstance(await h.guard.submit("healthy", _call()), Denied)
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["read", "send"])
def test_actual_cancellation_after_reservation_keeps_read_withheld_send_unknown(name):
    async def scenario():
        h = _Harness(source_label=Label.PUBLIC)
        healthy(h)
        entered = asyncio.Event()
        original = h.transport.call

        async def transport(req):
            if req.task_id == "task":
                h.transport.calls.append(req)
                entered.set()
                await asyncio.Event().wait()
            return await original(req)

        h.transport.call = transport
        work = asyncio.create_task(h.guard.submit("task", _call(name)))
        await entered.wait()
        work.cancel()
        outcome = await asyncio.wait_for(work, 1)
        assert isinstance(outcome, Withheld if name == "read" else OutcomeUnknown)
        assert h.store.states["task"].status is (
            TaskStatus.HALTED if name == "read" else TaskStatus.UNKNOWN_OUTCOME
        )
        assert h.audit[-1].phase == "after_dispatch"
        assert not h.guard._global_halt
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        assert len([req for req in h.transport.calls if req.task_id == "task"]) == 1

    asyncio.run(scenario())


def test_cancelled_approval_lock_waiter_does_not_globally_halt():
    async def scenario():
        h = _Harness(label=Label.INTERNAL, source_label=Label.PUBLIC)
        healthy(h)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        lock = h.guard._task_lock("task")
        await lock.acquire()
        work = asyncio.create_task(h.guard.human_control("operator").approve(pending.approval_id))
        await asyncio.sleep(0)
        work.cancel()
        await asyncio.sleep(0)
        assert not work.done()
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        lock.release()
        assert_cancelled_denial(h, await asyncio.wait_for(work, 1))
        assert h.store.consume_count == 0
        await h.guard.aclose()

    asyncio.run(scenario())


def test_approval_cancellation_before_consumption_is_task_local():
    async def scenario():
        h = _Harness(label=Label.INTERNAL, source_label=Label.PUBLIC)
        healthy(h)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        entered = asyncio.Event()
        original = h.store.get_approval

        async def get_approval(approval_id):
            if not entered.is_set():
                entered.set()
                await asyncio.Event().wait()
            return await original(approval_id)

        h.store.get_approval = get_approval
        work = asyncio.create_task(h.guard.human_control("operator").approve(pending.approval_id))
        await entered.wait()
        work.cancel()
        assert_cancelled_denial(h, await asyncio.wait_for(work, 1))
        assert h.store.consume_count == 0
        assert isinstance(await h.guard.submit("healthy", _call()), Released)
        await h.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_source", ["task", "checker"])
def test_sqlite_cancelled_task_halt_and_audit_survive_restart(tmp_path, cancel_source):
    async def scenario():
        h = _PolicyHarness()
        h.add_task("healthy")
        sequence = count(1)
        path = tmp_path / "cancel.db"
        with SQLiteStore(path, audit_id_factory=lambda: f"audit-{next(sequence)}") as store:
            for state in h.store.states.values():
                await store.create_task(state, created_at_utc=h.now)
            h.guard = h.build_guard(store)
            if cancel_source == "task":
                entered = asyncio.Event()
                original_sequence = h.guard._next_call_seq

                async def next_call_seq(task_id):
                    if task_id == "task":
                        entered.set()
                        await asyncio.Event().wait()
                    return await original_sequence(task_id)

                h.guard._next_call_seq = next_call_seq
                work = asyncio.create_task(h.guard.submit("task", _call()))
                await entered.wait()
                work.cancel("PRIVATE_cancellation")
                assert isinstance(await asyncio.wait_for(work, 1), Denied)
            else:
                original_checker = h.access.check_access

                def check_access(req, ctx):
                    raise asyncio.CancelledError("PRIVATE_checker_cancellation")

                h.access.check_access = check_access
                assert isinstance(await h.guard.submit("task", _call()), Denied)
                h.access.check_access = original_checker
            assert (await store.load_task("task")).status is TaskStatus.HALTED
            assert not h.guard._global_halt
            assert h.audit[-1].exception_type == "CancelledError"
            audits = (
                store._owned_connection()
                .execute(
                    "SELECT task_id,reason_code,detail FROM audit_events WHERE event_type='HALTED'"
                )
                .fetchall()
            )
            assert [tuple(row) for row in audits] == [("task", Reason.INTERNAL_ERROR.value, None)]
            assert isinstance(await h.guard.submit("healthy", _call()), Released)
            assert [req.task_id for req in h.transport.calls] == ["healthy"]
            await h.guard.aclose()
        with SQLiteStore(path) as store:
            assert (await store.load_task("task")).status is TaskStatus.HALTED
            assert (await store.load_task("healthy")).status is TaskStatus.RUNNING

    asyncio.run(scenario())
