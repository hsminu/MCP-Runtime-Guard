"""B-QA-05: 승인 사본 폐기 이후에도 종결 처리를 같은 Task lock으로 직렬화한다.

I3/I6/I7/I9, T09–T12/T37의 서비스 단위 부분이다. Store await를 멈추는 대역으로
잠금·메모리 관계를 관찰하며 실제 SQLite 원자성이나 wire 계수를 주장하지 않는다.
"""

import asyncio
from typing import cast

import pytest

from guard_app.domain.contracts import Denied, Withheld
from guard_app.domain.enums import ApprovalStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.services.approvals import ApprovalService
from tests.unit.test_approvals import _open, _setup


@pytest.mark.parametrize("first_command", ["reject", "expire", "cancel"])
@pytest.mark.parametrize("next_command", ["approve", "reject", "expire"])
def test_control_waits_for_terminal_write_after_raw_snapshot_is_removed(
    first_command, next_command
):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, state, _ = setup
        approval = await _open(setup)
        control = service.human_channel("operator-1")
        entered, release = asyncio.Event(), asyncio.Event()
        transition = store.finish_approval
        getter = store.get_approval
        transitions, gets = [], []

        async def yielding_transition(*args, **kwargs):
            assert host.lock.locked()
            transitions.append(args[1])
            entered.set()
            await release.wait()
            return await transition(*args, **kwargs)

        async def counted_get(approval_id):
            gets.append(approval_id)
            return await getter(approval_id)

        store.finish_approval = yielding_transition
        store.get_approval = counted_get
        if first_command == "expire":
            clock.mono += 300
        target = state.task_id if first_command == "cancel" else approval.approval_id
        first = asyncio.create_task(getattr(control, first_command)(target))
        await entered.wait()
        assert not service._memory_snapshots
        owner = service._closing_approvals[approval.approval_id]
        assert (owner.task_id, owner.epoch) == (state.task_id, state.epoch)
        assert "RAW_ARGUMENT_MARKER" not in repr(owner)
        assert service._has_task_context(state.task_id)
        reads_before = len(gets)
        second = asyncio.create_task(getattr(control, next_command)(approval.approval_id))
        await asyncio.sleep(0)
        assert not second.done()
        assert len(gets) == reads_before
        assert len(transitions) == 1
        assert store.approvals[approval.approval_id].status is ApprovalStatus.PENDING
        release.set()
        result1, result2 = await asyncio.gather(first, second)
        assert isinstance(result1, Denied)
        assert isinstance(result2, Denied)
        assert result2.reasons == (Reason.APPROVAL_RACE,)
        assert len(transitions) == 1
        assert not service._closing_approvals
        assert not service._memory_snapshots
        assert not store.consumed
        assert not host.dispatched
        assert not host.globally_halted
        assert not host.lock.locked()

    asyncio.run(scenario())


@pytest.mark.parametrize("first_command", ["reject", "expire"])
@pytest.mark.parametrize("next_command", ["approve", "reject", "expire"])
def test_closing_owner_is_held_until_waiting_task_resume_ack(first_command, next_command):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, state, _ = setup
        approval = await _open(setup)
        control = service.human_channel("operator-1")
        entered, release = asyncio.Event(), asyncio.Event()
        finish = store.finish_approval
        getter = store.get_approval
        gets = []

        async def yielding_resume(*args, **kwargs):
            assert kwargs["task_after"] is TaskStatus.RUNNING
            assert host.lock.locked()
            entered.set()
            await release.wait()
            return await finish(*args, **kwargs)

        async def counted_get(approval_id):
            gets.append(approval_id)
            return await getter(approval_id)

        store.finish_approval = yielding_resume
        store.get_approval = counted_get
        if first_command == "expire":
            clock.mono += 300
        first = asyncio.create_task(getattr(control, first_command)(approval.approval_id))
        await entered.wait()
        assert not service._memory_snapshots
        assert service._closing_approvals[approval.approval_id].epoch == state.epoch
        assert store.state.status is TaskStatus.WAITING_APPROVAL
        assert store.approvals[approval.approval_id].status is ApprovalStatus.PENDING
        reads_before = len(gets)
        second = asyncio.create_task(getattr(control, next_command)(approval.approval_id))
        await asyncio.sleep(0)
        assert not second.done()
        assert len(gets) == reads_before
        release.set()
        await asyncio.gather(first, second)
        assert store.state.status is TaskStatus.RUNNING
        assert not service._closing_approvals
        assert not host.globally_halted
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["approve", "reject", "expire"])
@pytest.mark.parametrize("status", [ApprovalStatus.PENDING, ApprovalStatus.APPROVED])
def test_unknown_task_owner_cannot_write_live_approval_without_a_lock(command, status):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        if status is ApprovalStatus.APPROVED:
            store.approvals[approval.approval_id] = approval.model_copy(
                update={"status": status, "row_revision": 1, "decided_at_utc": clock.utc}
            )
        persisted = store.approvals[approval.approval_id]
        restarted = ApprovalService(
            cast(Store, store),
            service._catalog,
            host,
            approver_id="operator-1",
            approval_id_factory=lambda: "unused",
            utc_now=clock.utc_now,
            monotonic=clock.monotonic,
        )
        writes, locks = [], []

        async def forbidden_transition(*args, **kwargs):
            writes.append(args)
            raise AssertionError("승인 소유 Task를 모르는 제어는 행을 변경할 수 없다")

        def forbidden_lock(task_id):
            locks.append(task_id)
            raise AssertionError("승인 메타데이터로 Task lock을 추측할 수 없다")

        store.transition_approval = forbidden_transition
        store.finish_approval = forbidden_transition
        host.task_lock = forbidden_lock
        result = await getattr(restarted.human_channel("operator-1"), command)(approval.approval_id)
        assert result.reasons == (Reason.APPROVAL_CONTEXT_LOST,)
        assert not writes
        assert not locks
        assert store.approvals[approval.approval_id] == persisted
        assert store.state.status is TaskStatus.WAITING_APPROVAL
        assert not restarted._memory_snapshots
        assert not restarted._closing_approvals
        assert not host.globally_halted
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("next_command", ["approve", "reject", "expire"])
def test_terminal_storage_failure_keeps_raw_free_owner_and_future_control_lock(next_command):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, state, _ = setup
        approval = await _open(setup)
        transitions = []

        async def failed_transition(*args, **kwargs):
            assert host.lock.locked()
            transitions.append(args[1])
            raise StoreError()

        store.finish_approval = failed_transition
        control = service.human_channel("operator-1")
        failed = await control.reject(approval.approval_id)
        assert failed.reasons == (Reason.INTERNAL_ERROR,)
        assert host.globally_halted
        assert not service._memory_snapshots
        owner = service._closing_approvals[approval.approval_id]
        assert (owner.task_id, owner.epoch) == (state.task_id, state.epoch)
        assert "RAW_ARGUMENT_MARKER" not in repr(owner)
        assert service._has_task_context(state.task_id)
        before = len(transitions)
        await host.lock.acquire()
        retry = asyncio.create_task(getattr(control, next_command)(approval.approval_id))
        await asyncio.sleep(0)
        assert not retry.done()
        assert len(transitions) == before
        host.lock.release()
        result = await retry
        assert result.reasons == (Reason.APPROVAL_CONTEXT_LOST,)
        assert len(transitions) == before
        assert service._closing_approvals[approval.approval_id] is owner
        assert store.approvals[approval.approval_id].status is ApprovalStatus.PENDING
        assert not store.consumed
        assert not host.dispatched
        assert "RAW_ARGUMENT_MARKER" not in result.model_dump_json()

    asyncio.run(scenario())


def test_missing_task_with_failed_closing_approval_is_not_treated_as_normal_absence():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, state, _ = setup
        approval = await _open(setup)
        store.fail = "transition"
        await service.human_channel("operator-1").reject(approval.approval_id)
        assert service._has_task_context(state.task_id)
        host.globally_halted = False

        async def lost_task(task_id):
            raise StoreError(Reason.STATE_MISSING)

        store.load_task = lost_task
        result = await service.human_channel("operator-1").cancel(state.task_id)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert host.globally_halted
        assert not service._memory_snapshots
        assert service._has_task_context(state.task_id)
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["approve", "reject", "expire"])
def test_unknown_owner_read_cannot_acquire_authority_from_a_snapshot_published_during_await(
    command,
):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        service._memory_snapshots.clear()
        getter = store.get_approval
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def yielding_get(approval_id):
            entered.set()
            await release.wait()
            return await getter(approval_id)

        async def forbidden_transition(*args, **kwargs):
            writes.append(args)
            raise AssertionError("읽기 경로는 새 사본이 게시돼도 잠금 없이 전이할 수 없다")

        store.get_approval = yielding_get
        store.transition_approval = forbidden_transition
        store.finish_approval = forbidden_transition
        clock.mono += 300
        lookup = asyncio.create_task(
            getattr(service.human_channel("operator-1"), command)(approval.approval_id)
        )
        await entered.wait()
        service._memory_snapshots[approval.approval_id] = snapshot
        release.set()
        result = await lookup
        assert result.reasons == (Reason.APPROVAL_CONTEXT_LOST,)
        assert not writes
        assert service._memory_snapshots[approval.approval_id] is snapshot
        assert store.approvals[approval.approval_id].status is ApprovalStatus.PENDING
        assert store.state.status is TaskStatus.WAITING_APPROVAL
        assert not host.globally_halted
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


def test_parallel_approval_controls_consume_once_and_drop_all_owner_metadata():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        host.dispatch_started, host.dispatch_finish = asyncio.Event(), asyncio.Event()
        control = service.human_channel("operator-1")
        first = asyncio.create_task(control.approve(approval.approval_id))
        await host.dispatch_started.wait()
        retries = [
            asyncio.create_task(getattr(control, command)(approval.approval_id))
            for command in ("approve", "reject", "expire")
        ]
        await asyncio.sleep(0)
        assert all(not retry.done() for retry in retries)
        assert service._inflight_tasks[approval.approval_id] == store.state.task_id
        assert not service._memory_snapshots
        assert len(store.consumed) == len(host.dispatched) == 1
        host.dispatch_finish.set()
        result, *retry_results = await asyncio.gather(first, *retries)
        assert isinstance(result, Withheld)
        assert all(isinstance(item, Denied) for item in retry_results)
        assert len(store.consumed) == len(host.dispatched) == 1
        assert not service._inflight_tasks
        assert not service._closing_approvals
        assert not service._memory_snapshots
        assert not host.globally_halted

    asyncio.run(scenario())
