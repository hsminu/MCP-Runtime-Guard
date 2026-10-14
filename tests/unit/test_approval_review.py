"""B5 I3/I6/I7/I9/T10–T12: 사람 전용 승인 상세의 읽기·유효성 경계.

Store·Registry·Host 대역에서 메모리 사본 조회를 검증한다. 실제 UI·SQLite·
MCP wire 검증을 뜻하지 않으며, 조회가 승인 소비나 상태 쓰기를 하지 않는지 센다.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from guard_app.core.snapshot import make_snapshot
from guard_app.domain.contracts import ApprovalRecord, Denied, Withheld
from guard_app.domain.enums import ApprovalStatus, Label, RegStatus, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.security.digest import canonical_json, json_digest
from tests.unit.test_approvals import _open, _setup


def _assert_read_only(setup, state, approvals, statuses):
    _, store, host, *_ = setup
    assert store.state is state
    assert store.approvals == approvals
    assert store.statuses == statuses
    assert not store.consumed
    assert not host.dispatched
    assert not host.halted
    assert not host.phases
    assert not host.lock.locked()


def test_review_returns_same_memory_snapshot_and_does_not_consume_or_write():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        result = await service.human_channel("operator-1").review(approval.approval_id)

        assert result is snapshot
        assert b"RAW_ARGUMENT_MARKER" in result.arguments_bytes
        assert "RAW_ARGUMENT_MARKER" not in repr(result)
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize("clock_name", ["utc", "mono"])
def test_review_rechecks_each_clock_after_awaited_task_read(clock_name):
    async def scenario():
        setup = _setup(ttl_seconds=10)
        service, store, host, clock, *_ = setup
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        async def load_task(task_id):
            assert host.lock.locked()
            assert task_id == state.task_id
            await asyncio.sleep(0)
            if clock_name == "utc":
                clock.utc = snapshot.expires_at_utc
            else:
                clock.mono = snapshot.monotonic_deadline
            return state

        store.load_task = load_task
        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_review_remains_available_immediately_before_both_deadlines():
    async def scenario():
        setup = _setup(ttl_seconds=10)
        service, store, host, clock, *_ = setup
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        async def load_task(task_id):
            await asyncio.sleep(0)
            clock.utc = snapshot.expires_at_utc - timedelta(microseconds=1)
            clock.mono = snapshot.monotonic_deadline - 0.000001
            return state

        store.load_task = load_task
        assert await service.human_channel("operator-1").review(approval.approval_id) is snapshot
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_review_rejects_other_approval_id_even_when_the_remaining_metadata_matches():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        store.approvals[approval.approval_id] = approval.model_copy(
            update={"approval_id": "other-approval"}
        )
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def _attach_unrelated_approval(setup, approval, *, matching_metadata=False):
    service, store, *_ = setup
    original = service._memory_snapshots[approval.approval_id]
    other_req = original.request.model_copy(
        update={"task_id": "other-task", "request_id": "other-request"}
    )
    other_state = original.state.model_copy(update={"task_id": "other-task"})
    other_snapshot = make_snapshot(
        other_req,
        other_state,
        original.decision,
        effective_label=original.effective_label,
        approver_id=original.approver_id,
        expires_at_utc=original.expires_at_utc,
        monotonic_deadline=original.monotonic_deadline,
        hash_json=json_digest,
        canonicalize=canonical_json,
    )
    values = {"approval_id": "other-approval"}
    if not matching_metadata:
        values.update({"request_id": other_req.request_id, "snapshot_hash": other_snapshot.hash})
    other = approval.model_copy(update=values)
    store.approvals[other.approval_id] = other
    if not matching_metadata:
        service._memory_snapshots[other.approval_id] = other_snapshot
    return other, other_snapshot


@pytest.mark.parametrize("command", ["approve", "reject", "expire"])
@pytest.mark.parametrize("matching_metadata", [False, True])
def test_control_rejects_misdirected_approval_lookup_without_mutating_either_row(
    command, matching_metadata
):
    async def scenario():
        setup = _setup()
        service, store, host, clock, *_ = setup
        approval = await _open(setup)
        other, other_snapshot = _attach_unrelated_approval(
            setup, approval, matching_metadata=matching_metadata
        )
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        calls = []

        async def wrong_lookup(approval_id):
            calls.append(approval_id)
            return other

        store.get_approval = wrong_lookup
        if command == "expire":
            clock.mono += 300
        result = await getattr(service.human_channel("operator-1"), command)(approval.approval_id)

        assert isinstance(result, Denied)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert calls == [approval.approval_id]
        assert host.globally_halted
        assert approval.approval_id not in service._memory_snapshots
        if not matching_metadata:
            assert service._memory_snapshots[other.approval_id] is other_snapshot
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["cancel", "emergency_halt"])
def test_task_invalidation_rejects_wrong_approval_row_and_preserves_unrelated_memory(command):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        other, other_snapshot = _attach_unrelated_approval(setup, approval)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        async def wrong_lookup(approval_id):
            return other

        store.get_approval = wrong_lookup
        result = await getattr(service.human_channel("operator-1"), command)(state.task_id)

        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert host.globally_halted
        assert approval.approval_id not in service._memory_snapshots
        assert service._memory_snapshots[other.approval_id] is other_snapshot
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_failed_consume_refresh_rejects_wrong_approval_without_mutating_unrelated_row():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        other, other_snapshot = _attach_unrelated_approval(setup, approval)
        original_get = store.get_approval
        store.consume_result = False
        calls = []

        async def wrong_refresh(approval_id):
            calls.append(approval_id)
            if len(calls) == 1:
                return await original_get(approval_id)
            return other

        store.get_approval = wrong_refresh
        result = await service.human_channel("operator-1").approve(approval.approval_id)

        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert host.globally_halted
        assert calls == [approval.approval_id, approval.approval_id]
        assert store.approvals[approval.approval_id].status is ApprovalStatus.APPROVED
        assert store.approvals[other.approval_id] is other
        assert store.state.status is TaskStatus.WAITING_APPROVAL
        assert not store.consumed
        assert not host.dispatched
        assert not host.halted
        assert not host.lock.locked()
        assert approval.approval_id not in service._memory_snapshots
        assert service._memory_snapshots[other.approval_id] is other_snapshot

    asyncio.run(scenario())


def test_error_cleanup_rejects_wrong_approval_after_original_snapshot_removal():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        other, other_snapshot = _attach_unrelated_approval(setup, approval)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        original_get = store.get_approval
        calls = []
        store.transition_errors = 1

        async def wrong_cleanup(approval_id):
            calls.append(approval_id)
            if len(calls) == 1:
                return await original_get(approval_id)
            return other

        store.get_approval = wrong_cleanup
        result = await service.human_channel("operator-1").reject(approval.approval_id)

        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert host.globally_halted
        assert calls == [approval.approval_id, approval.approval_id]
        assert approval.approval_id not in service._memory_snapshots
        assert service._memory_snapshots[other.approval_id] is other_snapshot
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_wrong_transition_return_id_blocks_consume_and_preserves_unrelated_approval():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        other, other_snapshot = _attach_unrelated_approval(setup, approval)
        original_transition = store.transition_approval
        calls = []

        async def wrong_transition(approval_id, status, **kwargs):
            calls.append(approval_id)
            await original_transition(approval_id, status, **kwargs)
            return other

        store.transition_approval = wrong_transition
        result = await service.human_channel("operator-1").approve(approval.approval_id)

        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert host.globally_halted
        assert calls == [approval.approval_id]
        assert store.approvals[approval.approval_id].status is ApprovalStatus.APPROVED
        assert store.approvals[other.approval_id] is other
        assert store.state.status is TaskStatus.WAITING_APPROVAL
        assert not store.consumed
        assert not host.dispatched
        assert not host.halted
        assert not host.lock.locked()
        assert approval.approval_id not in service._memory_snapshots
        assert service._memory_snapshots[other.approval_id] is other_snapshot

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_revision", True),
        ("status", "PENDING"),
        ("snapshot_hash", "not-a-hash"),
        ("arguments", "RAW_ARGUMENT_MARKER"),
    ],
)
def test_review_revalidates_unchecked_store_record_and_contains_failure(field, value, capsys):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        values = approval.model_dump(mode="python")
        values[field] = value
        store.approvals[approval.approval_id] = (
            values if field == "arguments" else ApprovalRecord.model_construct(**values)
        )
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())
    output = capsys.readouterr()
    assert "RAW_ARGUMENT_MARKER" not in output.out
    assert "RAW_ARGUMENT_MARKER" not in output.err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approver_id", "other-operator"),
        ("request_id", "other-request"),
        ("snapshot_hash", "b" * 64),
    ],
)
def test_review_requires_matching_operator_request_and_snapshot_metadata(field, value):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        store.approvals[approval.approval_id] = approval.model_copy(update={field: value})
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status", [status for status in ApprovalStatus if status is not ApprovalStatus.PENDING]
)
def test_review_only_displays_pending_approval(status):
    async def scenario():
        setup = _setup()
        service, store, host, clock, *_ = setup
        approval = await _open(setup)
        update = {"status": status, "decided_at_utc": clock.utc}
        if status is ApprovalStatus.CONSUMED:
            update["consumed_at_utc"] = clock.utc
        store.approvals[approval.approval_id] = approval.model_copy(update=update)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize("memory_action", ["replace", "remove"])
def test_review_rechecks_memory_identity_after_awaited_task_read(memory_action):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        snapshot = service._memory_snapshots[approval.approval_id]

        async def load_task(task_id):
            await asyncio.sleep(0)
            if memory_action == "replace":
                service._memory_snapshots[approval.approval_id] = replace(snapshot)
            else:
                service._memory_snapshots.pop(approval.approval_id)
            return state

        store.load_task = load_task
        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize(("field", "value"), [("task_id", "other-task"), ("epoch", 3)])
def test_review_does_not_expose_details_from_a_different_task_identity(field, value):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        returned = store.state.model_copy(update={field: value})
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        async def load_task(task_id):
            return returned

        store.load_task = load_task
        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", TaskStatus.RUNNING),
        ("security_version", 1),
        ("high_watermark", Label.CONFIDENTIAL),
        ("profile_id", "other-profile"),
        ("policy_version", "changed-policy"),
    ],
)
def test_review_rejects_changed_task_security_context_without_state_writes(field, value):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        store.state = store.state.model_copy(update={field: value})
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize("registration_change", ["pending", "fingerprint", "tool"])
def test_review_requires_current_active_registration_and_the_same_tool(registration_change):
    async def scenario():
        setup = _setup()
        service, store, host, _, registry, *_ = setup
        approval = await _open(setup)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        if registration_change == "pending":
            registry.current_status = RegStatus.PENDING
        elif registration_change == "fingerprint":
            registry.current_fingerprint = "b" * 64
        else:
            tool = registry.record.tools[0].model_copy(update={"input_schema": {"type": "object"}})
            registry.record = registry.record.model_copy(update={"tools": (tool,)})

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_review_after_stop_latch_does_not_expose_details_or_halt_other_tasks():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        host.request_stop(state.task_id)

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_point", ["get", "load"])
def test_review_store_failure_returns_no_raw_data_and_global_halts(failure_point, capsys):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        store.fail = failure_point

        assert await service.human_channel("operator-1").review(approval.approval_id) is None
        assert host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())
    output = capsys.readouterr()
    assert not output.out
    assert not output.err


def test_cancelled_review_releases_lock_and_does_not_global_halt():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def load_task(task_id):
            entered.set()
            await release.wait()
            return state

        store.load_task = load_task
        review = asyncio.create_task(
            service.human_channel("operator-1").review(approval.approval_id)
        )
        await entered.wait()
        review.cancel()
        assert await review is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_approve_waits_for_review_lock_and_review_creates_no_execution_authority():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        control = service.human_channel("operator-1")
        entered, release = asyncio.Event(), asyncio.Event()
        original_load = store.load_task

        async def load_task(task_id):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return await original_load(task_id)

        store.load_task = load_task
        review = asyncio.create_task(control.review(approval.approval_id))
        await entered.wait()
        approve = asyncio.create_task(control.approve(approval.approval_id))
        await asyncio.sleep(0)
        assert not store.consumed
        assert not host.dispatched
        release.set()
        assert await review is snapshot
        assert isinstance(await approve, Withheld)
        assert store.consumed == [approval.approval_id]
        assert host.dispatched == [snapshot.request]
        assert await control.review(approval.approval_id) is None

    asyncio.run(scenario())


def test_review_rejects_wrong_operator_before_any_store_access():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        approval = await _open(setup)
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)
        store.fail = "get"

        assert await service._review(approval.approval_id, "other-operator") is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


@pytest.mark.parametrize("task_id", ["", None, [], True, "\ud800"])
def test_cancel_invalid_task_id_does_not_set_stop_latch_or_acquire_lock(task_id):
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        result = await service.human_channel("operator-1").cancel(task_id)
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.STATE_MISSING,)
        assert getattr(host, "stopped", None) is None
        assert not host.globally_halted
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())


def test_cancel_authentication_precedes_stop_latch():
    async def scenario():
        setup = _setup()
        service, store, host, *_ = setup
        state, approvals, statuses = store.state, dict(store.approvals), list(store.statuses)

        result = await service._cancel(state.task_id, "other-operator")
        assert result.reasons == (Reason.APPROVAL_CONTEXT_LOST,)
        assert getattr(host, "stopped", None) is None
        _assert_read_only(setup, state, approvals, statuses)

    asyncio.run(scenario())
