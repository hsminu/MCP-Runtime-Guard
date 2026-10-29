"""B6-01: 정책 불일치 시 승인 종결과 Task 정지의 원자성 (I4/I6/I8)."""

import asyncio
import sqlite3
import threading
import time

import pytest

from guard_app.domain.contracts import Denied
from guard_app.domain.enums import ApprovalStatus, CommitOutcome, RequestStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from tests.unit.test_atomic_approval_store import _finish as _finish_real
from tests.unit.test_atomic_approval_store import _open as _open_real
from tests.unit.test_atomic_approval_store import _snapshot as _store_snapshot
from tests.unit.test_atomic_approval_store import _store as _real_store
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_human_control_revision import _pending
from tests.unit.test_store_recovery import RUN, _close, _open, _rows


async def _business(store):
    return await store._run_on_writer_async(
        lambda db: (
            db.execute("SELECT status FROM tasks WHERE task_id=?", ("task",)).fetchone()[0],
            db.execute(
                "SELECT status FROM approvals WHERE approval_id=?", ("approval",)
            ).fetchone()[0],
            db.execute("SELECT status FROM requests WHERE request_id=?", ("request",)).fetchone()[
                0
            ],
        )
    )


async def _audit(store):
    return await store._run_on_writer_async(
        lambda db: tuple(
            tuple(row)
            for row in db.execute(
                "SELECT event_type,reason_code FROM audit_events "
                "WHERE event_type IN ('HALTED','APPROVAL_INVALIDATED') ORDER BY event_type"
            )
        )
    )


@pytest.mark.parametrize("approved", [False, True])
def test_policy_mismatch_invalidates_and_halts_in_one_operation(tmp_path, approved):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            if approved:
                await store.transition_approval(
                    "approval",
                    ApprovalStatus.APPROVED,
                    expected_row_revision=0,
                    approver_id="operator",
                    now_utc=harness.now,
                    reason=Reason.OK,
                )
            guard._policy_version = "changed"
            result = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.POLICY_CHANGED,)
            assert await _business(store) == (
                TaskStatus.HALTED.value,
                ApprovalStatus.INVALIDATED.value,
                RequestStatus.DENIED.value,
            )
            assert await _audit(store) == (
                ("APPROVAL_INVALIDATED", Reason.POLICY_CHANGED.value),
                ("HALTED", Reason.POLICY_CHANGED.value),
            )
            assert handle._completed
            assert harness.transport.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("point", ["before", "after"])
def test_policy_mismatch_exception_at_invalidate_halt_boundary_is_never_partial(
    tmp_path, monkeypatch, point
):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, _handle):
            guard._on_global_halt = store.halt_execution
            original = guard._approvals.invalidate_task

            async def injected(*args, **kwargs):
                if point == "after":
                    await original(*args, **kwargs)
                raise StoreError(Reason.INTERNAL_ERROR)

            guard._policy_version = "changed"
            with monkeypatch.context() as patch:
                patch.setattr(guard._approvals, "invalidate_task", injected)
                result = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(result, Denied)
            assert guard._global_halt
            assert not store.execution_state().execution_enabled
            actual = await _business(store)
            expected = (
                (
                    TaskStatus.WAITING_APPROVAL.value,
                    ApprovalStatus.PENDING.value,
                    RequestStatus.WAITING_APPROVAL.value,
                )
                if point == "before"
                else (
                    TaskStatus.HALTED.value,
                    ApprovalStatus.INVALIDATED.value,
                    RequestStatus.DENIED.value,
                )
            )
            assert actual == expected
            assert harness.transport.calls == []

    asyncio.run(scenario())


def test_policy_mismatch_without_approval_keeps_existing_halt_path():
    async def scenario():
        harness = _Harness()
        harness.guard._policy_version = "changed"
        result = await harness.guard.submit("task", _call("send"), expected_epoch=1)
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.POLICY_CHANGED,)
        assert harness.store.states["task"].status is TaskStatus.HALTED
        assert not harness.store.approvals
        assert not harness.transport.calls
        await harness.guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("conflict", [Reason.APPROVAL_RACE, Reason.APPROVAL_STALE])
def test_policy_mismatch_after_another_approval_end_still_halts_task(
    tmp_path, monkeypatch, conflict
):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, _handle):
            original = store.finish_approval

            async def competing(approval_id, status, **kwargs):
                await original(
                    approval_id,
                    ApprovalStatus.REJECTED,
                    expected_approval_revision=kwargs["expected_approval_revision"],
                    task_after=TaskStatus.RUNNING,
                    expected_task_revision=kwargs["expected_task_revision"],
                    reason=Reason.OK,
                    now_utc=kwargs["now_utc"],
                )
                raise StoreError(conflict)

            guard._policy_version = "changed"
            with monkeypatch.context() as patch:
                patch.setattr(store, "finish_approval", competing)
                await guard.submit("task", _call("send"), expected_epoch=1)
            assert await _business(store) == (
                TaskStatus.HALTED.value,
                ApprovalStatus.REJECTED.value,
                RequestStatus.DENIED.value,
            )
            assert not guard._global_halt
            assert harness.transport.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("conflict", [Reason.APPROVAL_RACE, Reason.APPROVAL_STALE])
def test_competing_end_is_halted_before_policy_invalidation_returns(
    tmp_path, monkeypatch, conflict
):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, _handle):
            guard._on_global_halt = store.halt_execution
            original_finish = store.finish_approval
            original_invalidate = guard._approvals.invalidate_task

            async def competing(approval_id, status, **kwargs):
                await original_finish(
                    approval_id,
                    ApprovalStatus.REJECTED,
                    expected_approval_revision=kwargs["expected_approval_revision"],
                    task_after=TaskStatus.RUNNING,
                    expected_task_revision=kwargs["expected_task_revision"],
                    reason=Reason.OK,
                    now_utc=kwargs["now_utc"],
                )
                raise StoreError(conflict)

            async def fail_after_invalidation(*args, **kwargs):
                await original_invalidate(*args, **kwargs)
                raise StoreError(Reason.INTERNAL_ERROR)

            guard._policy_version = "changed"
            with monkeypatch.context() as patch:
                patch.setattr(store, "finish_approval", competing)
                patch.setattr(guard._approvals, "invalidate_task", fail_after_invalidation)
                await guard.submit("task", _call("send"), expected_epoch=1)
            assert await _business(store) == (
                TaskStatus.HALTED.value,
                ApprovalStatus.REJECTED.value,
                RequestStatus.DENIED.value,
            )
            assert guard._global_halt
            assert not store.execution_state().execution_enabled
            assert harness.transport.calls == []

    asyncio.run(scenario())


def test_pending_invalidation_is_limited_to_policy_change_and_old_transition_is_unchanged(tmp_path):
    async def scenario():
        async with _pending(tmp_path) as (_guard, store, harness, _handle):
            before = await _business(store)
            with pytest.raises(StoreError) as old_error:
                await store.transition_approval(
                    "approval",
                    ApprovalStatus.INVALIDATED,
                    expected_row_revision=0,
                    approver_id="operator",
                    now_utc=harness.now,
                    reason=Reason.POLICY_CHANGED,
                )
            assert old_error.value.reason is Reason.APPROVAL_RACE
            with pytest.raises(StoreError) as new_error:
                await store.finish_approval(
                    "approval",
                    ApprovalStatus.INVALIDATED,
                    expected_approval_revision=0,
                    task_after=TaskStatus.HALTED,
                    expected_task_revision=1,
                    reason=Reason.INTERNAL_ERROR,
                    halt_reason=Reason.INTERNAL_ERROR,
                    now_utc=harness.now,
                )
            assert new_error.value.reason is Reason.APPROVAL_RACE
            assert await _business(store) == before

    asyncio.run(scenario())


def test_policy_mismatch_detects_corruption_and_globally_halts(tmp_path):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            guard._on_global_halt = store.halt_execution
            await store._run_on_writer_async(
                lambda db: db.execute(
                    "UPDATE approvals SET snapshot_hash=? WHERE approval_id=?",
                    ("a" * 64, "approval"),
                )
            )
            guard._policy_version = "changed"
            result = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(result, Denied)
            assert guard._global_halt
            assert not store.execution_state().execution_enabled
            assert await _business(store) == (
                TaskStatus.WAITING_APPROVAL.value,
                ApprovalStatus.PENDING.value,
                RequestStatus.WAITING_APPROVAL.value,
            )
            events = await store._run_on_writer_async(
                lambda db: db.execute(
                    "SELECT count(*) FROM audit_events WHERE event_type=?",
                    ("APPROVAL_INTEGRITY_FAILED",),
                ).fetchone()[0]
            )
            assert events == 1
            assert harness.transport.calls == []
            assert handle._completed

    asyncio.run(scenario())


def test_policy_mismatch_commit_ack_loss_closes_execution_without_partial_rows(
    tmp_path, monkeypatch
):
    connect = sqlite3.connect
    armed = threading.Event()

    class LostAckConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            result = super().execute(sql, parameters)
            if sql == "COMMIT" and armed.is_set():
                armed.clear()
                raise sqlite3.OperationalError("injected lost acknowledgement")
            return result

    async def scenario():
        with monkeypatch.context() as patch:
            patch.setattr(
                sqlite3,
                "connect",
                lambda *args, **kwargs: connect(*args, **kwargs, factory=LostAckConnection),
            )
            async with _pending(tmp_path) as (guard, store, harness, _handle):
                guard._on_global_halt = store.halt_execution
                guard._policy_version = "changed"
                original = store.finish_approval

                async def lose_finish_ack(*args, **kwargs):
                    armed.set()
                    return await original(*args, **kwargs)

                patch.setattr(store, "finish_approval", lose_finish_ack)
                result = await guard.submit("task", _call("send"), expected_epoch=1)
                assert isinstance(result, Denied)
                assert not armed.is_set()
                assert not store.execution_state().execution_enabled
                assert store.execution_state().commit_outcome is CommitOutcome.UNKNOWN
                with connect(tmp_path / "revision.db") as db:
                    actual = (
                        db.execute("SELECT status FROM tasks WHERE task_id='task'").fetchone()[0],
                        db.execute(
                            "SELECT status FROM approvals WHERE approval_id='approval'"
                        ).fetchone()[0],
                        db.execute(
                            "SELECT status FROM requests WHERE request_id='request'"
                        ).fetchone()[0],
                    )
                assert actual == (
                    TaskStatus.HALTED.value,
                    ApprovalStatus.INVALIDATED.value,
                    RequestStatus.DENIED.value,
                )
                assert harness.transport.calls == []

    asyncio.run(scenario())


def test_competing_rejection_running_recovery_blocks_old_task_execution(tmp_path):
    path = tmp_path / "competing-restart.db"
    # The other transaction legitimately rejected and resumed before policy cleanup ran.
    with _real_store(path) as first:
        _open_real(first)
        _finish_real(first)
        saved = _store_snapshot(path)
        assert saved["tasks"][0][7] == TaskStatus.RUNNING.value
        assert saved["approvals"][0][4] == ApprovalStatus.REJECTED.value
        assert saved["requests"][0][9] == RequestStatus.DENIED.value

    async def scenario():
        reopened = await _open(path, tmp_path)
        guard = None
        try:
            recovered = await reopened.recover(deadline=time.monotonic() + 20, run_id=RUN)
            assert recovered.changed_task_count == 1
            assert recovered.changed_approval_count == 0
            assert recovered.changed_request_count == 0
            assert (await reopened.load_task("task-1")).status is TaskStatus.HALTED
            assert await _rows(reopened, "SELECT status FROM approvals") == [
                (ApprovalStatus.REJECTED.value,)
            ]
            assert await _rows(reopened, "SELECT status FROM requests") == [
                (RequestStatus.DENIED.value,)
            ]
            await reopened.activate_run()
            harness = _Harness()
            guard = GuardService(
                reopened,
                harness.registry,
                harness.catalog,
                harness.transport,
                harness.access,
                profile_id="default",
                policy_version="v1",
                operator_id="operator",
                request_id_factory=lambda: "post-restart",
                approval_id_factory=lambda: "unused",
                next_call_seq=reopened.allocate_call_seq,
                failure_audit=harness.failure_audit,
                utc_now=lambda: harness.now,
                monotonic=lambda: harness.monotonic,
                on_global_halt=reopened.halt_execution,
            )
            result = await guard.submit("task-1", _call("send"), expected_epoch=3)
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.TASK_NOT_RUNNING,)
            assert harness.transport.calls == []
            with pytest.raises(StoreError) as stale:
                await reopened.allocate_call_seq("task-1")
            assert stale.value.reason is Reason.TASK_NOT_RUNNING
        finally:
            if guard is not None:
                await guard.aclose()
            await _close(reopened)

    asyncio.run(scenario())
