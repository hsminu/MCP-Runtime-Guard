"""전역 정지와 실제 정책 변경의 사유를 구분한다."""

import asyncio
from itertools import count

from guard_app.domain.contracts import Denied, Pending
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_approvals import _open, _setup
from tests.unit.test_guard import _call
from tests.unit.test_policy_change import _PolicyHarness


def test_submit_halt_during_sqlite_load_does_not_change_task_or_approval(tmp_path):
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        ids = count(1)
        store = SQLiteStore(
            tmp_path / "guard.db",
            audit_id_factory=lambda: f"audit-{next(ids)}",
            utc_now=lambda: h.now,
        )
        try:
            await store.create_task(h.store.states["task"], created_at_utc=h.now)
            guard = h.build_guard(store)
            pending = await guard.submit("task", _call("send"))
            assert isinstance(pending, Pending)
            waiting = await store.load_task("task")
            await store.set_task_status(
                "task",
                TaskStatus.RUNNING,
                expected_row_revision=waiting.row_revision,
                at_utc=h.now,
                reason=Reason.OK,
            )
            original_load = store.load_task

            async def load_and_halt(task_id):
                state = await original_load(task_id)
                guard._halt_globally()
                return state

            store.load_task = load_and_halt
            result = await guard.submit("task", _call("read"))
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert (await original_load("task")).status is TaskStatus.RUNNING
            assert (await store.get_approval(pending.approval_id)).status is ApprovalStatus.PENDING
            reasons = await store._run_on_writer_async(
                lambda connection: connection.execute(
                    "SELECT reason_code FROM audit_events WHERE reason_code IS NOT NULL"
                ).fetchall()
            )
            assert Reason.POLICY_CHANGED.value not in [row[0] for row in reasons]
        finally:
            store.close()
            assert await store.wait_closed(5)

    asyncio.run(scenario())


def _track_reasons(store, halt_reasons=None):
    observed = []
    original = store.transition_approval
    original_finish = store.finish_approval

    async def transition(*args, **kwargs):
        observed.append((args[1], kwargs["reason"]))
        return await original(*args, **kwargs)

    async def finish(*args, **kwargs):
        observed.append((args[1], kwargs["reason"]))
        if halt_reasons is not None:
            halt_reasons.append(kwargs["halt_reason"])
        return await original_finish(*args, **kwargs)

    store.transition_approval = transition
    store.finish_approval = finish
    return observed


def _prepare_host(host):
    host.policy_mismatch_reason = lambda: (
        Reason.INTERNAL_ERROR if host.globally_halted else Reason.POLICY_CHANGED
    )


def test_guard_approval_host_reports_global_halt_reason():
    h = _PolicyHarness(label=Label.INTERNAL)
    host = h.guard._approvals._host
    assert host.policy_mismatch_reason() is Reason.POLICY_CHANGED
    h.guard._halt_globally()
    assert host.policy_mismatch_reason() is Reason.INTERNAL_ERROR


def test_approval_halt_during_first_task_read_records_internal_error():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        _prepare_host(host)
        approval = await _open(setup)
        reasons = _track_reasons(store)
        original = service._load_snapshot_task

        async def load_and_halt(snapshot):
            state = await original(snapshot)
            host.global_halt()
            return state

        service._load_snapshot_task = load_and_halt
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.CANCELLED
        assert reasons == [(ApprovalStatus.CANCELLED, Reason.INTERNAL_ERROR)]
        assert host.halted == [Reason.INTERNAL_ERROR]
        assert not store.consumed
        assert not host.dispatched
        await service.aclose()

    asyncio.run(scenario())


def test_approval_halt_during_recheck_records_internal_error():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        _prepare_host(host)
        approval = await _open(setup)
        reasons = _track_reasons(store)
        host.recheck_hook = host.global_halt
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert reasons[-1] == (ApprovalStatus.INVALIDATED, Reason.INTERNAL_ERROR)
        assert host.halted == [Reason.INTERNAL_ERROR]
        assert not store.consumed
        assert not host.dispatched
        await service.aclose()

    asyncio.run(scenario())


def test_resume_halt_during_read_preserves_prior_approval_reason():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        _prepare_host(host)
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        halt_reasons = []
        reasons = _track_reasons(store, halt_reasons)
        original = service._load_snapshot_task

        async def load_and_halt(current):
            state = await original(current)
            host.global_halt()
            return state

        service._load_snapshot_task = load_and_halt
        await service._finish(approval, snapshot, ApprovalStatus.CANCELLED, Reason.OK, resume=True)
        assert reasons == [(ApprovalStatus.CANCELLED, Reason.OK)]
        assert halt_reasons == [Reason.INTERNAL_ERROR]
        assert store.state.status is TaskStatus.HALTED
        await service.aclose()

    asyncio.run(scenario())


def test_real_policy_change_retains_policy_changed_reason():
    async def scenario():
        h = _PolicyHarness(label=Label.INTERNAL)
        pending = await h.guard.submit("task", _call("send"))
        assert isinstance(pending, Pending)
        handle = h.guard.pending_result(pending.request_id)
        await h.apply(h.prepare())
        assert (await handle.wait()).outcome.reasons == (Reason.POLICY_CHANGED,)
        assert h.store.states["task"].status is TaskStatus.HALTED
        assert not h.guard._global_halt
        await h.guard.aclose()

    asyncio.run(scenario())
