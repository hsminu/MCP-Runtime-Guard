"""B6-01: Guard containment and pending notification on integrity failure."""

import asyncio
from itertools import count

import pytest

from guard_app.domain.contracts import ApprovalCommit, Denied
from guard_app.domain.enums import ApprovalStatus, Label, StorePhase, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import _call, _Harness
from tests.unit.test_human_control_revision import _pending
from tests.unit.test_pending_notifications import _Clock, _turns


def test_corrupt_approval_halts_guard_and_completes_pending_without_execution(tmp_path):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            guard._on_global_halt = store.halt_execution

            def corrupt(connection):
                connection.execute(
                    "UPDATE approvals SET snapshot_hash=? WHERE approval_id=?",
                    ("b" * 64, "approval"),
                )

            await store._run_on_writer_async(corrupt)
            result = await guard.human_control("operator").approve("approval")
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert guard._global_halt
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not store.execution_state().execution_enabled
            assert not guard._approvals._memory_snapshots
            assert "approval" not in guard._approvals._expiry_tasks
            final = await asyncio.wait_for(handle.wait(), timeout=1)
            assert final.cause == "failed"
            assert isinstance(final.outcome, Denied)
            assert final.outcome.reasons == (Reason.INTERNAL_ERROR,)
            assert not harness.transport.calls

    asyncio.run(scenario())


def test_forged_open_ack_nested_bool_revision_halts_without_publishing_pending():
    async def scenario():
        harness = _Harness(label=Label.INTERNAL)
        original = harness.store.open_approval

        async def forge(*args, **kwargs):
            commit = await original(*args, **kwargs)
            fields = commit.task.model_dump()
            fields["row_revision"] = True
            invalid = type(commit.task).model_construct(**fields)
            return ApprovalCommit.model_construct(
                approval=commit.approval, task=invalid, request_status=commit.request_status
            )

        harness.store.open_approval = forge
        result = await harness.guard.submit("task", _call("send"))
        assert isinstance(result, Denied)
        assert harness.guard._global_halt
        assert not harness.guard._approvals._pending
        assert not harness.guard._approvals._memory_snapshots
        assert not harness.transport.calls
        await harness.guard.aclose()

    asyncio.run(scenario())


def test_forged_finish_ack_nested_bool_revision_halts_and_completes_pending(tmp_path):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            original = store.finish_approval

            async def forge(*args, **kwargs):
                commit = await original(*args, **kwargs)
                fields = commit.approval.model_dump()
                fields["row_revision"] = True
                invalid = type(commit.approval).model_construct(**fields)
                return ApprovalCommit.model_construct(
                    approval=invalid, task=commit.task, request_status=commit.request_status
                )

            store.finish_approval = forge
            try:
                result = await guard.human_control("operator").reject("approval")
                assert isinstance(result, Denied)
                assert guard._global_halt
                final = await asyncio.wait_for(handle.wait(), timeout=1)
                assert final.cause == "failed"
                assert isinstance(final.outcome, Denied)
                assert not harness.transport.calls
            finally:
                store.finish_approval = original

    asyncio.run(scenario())


def test_corrupt_task_during_open_halts_guard_before_pending_is_published(tmp_path):
    async def scenario():
        clock = _Clock()
        harness = _Harness(label=Label.INTERNAL)
        ids = count(1)
        with SQLiteStore(
            tmp_path / "open-corrupt.db",
            utc_now=lambda: clock.utc,
            audit_id_factory=lambda: f"audit-{next(ids)}",
        ) as store:
            await store.create_task(harness.store.states["task"], created_at_utc=clock.utc)
            guard = GuardService(
                store,
                harness.registry,
                harness.catalog,
                harness.transport,
                harness.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: "request",
                approval_id_factory=lambda: "approval",
                next_call_seq=harness.sequence,
                failure_audit=harness.failure_audit,
                utc_now=lambda: clock.utc,
                monotonic=lambda: clock.mono,
                on_global_halt=store.halt_execution,
            )
            original = store.open_approval

            async def corrupt_open(approval, *, expected_task):
                def corrupt(connection):
                    connection.execute(
                        "UPDATE tasks SET profile_id=? WHERE task_id=?", ("x", "task")
                    )

                await store._run_on_writer_async(corrupt)
                return await original(approval, expected_task=expected_task)

            store.open_approval = corrupt_open
            result = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INTERNAL_ERROR,)
            assert guard._global_halt
            assert store.execution_state().phase is StorePhase.FAULTED
            assert not store.execution_state().execution_enabled
            assert not guard._approvals._memory_snapshots
            assert not guard._approvals._pending
            assert not harness.transport.calls
            await guard.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", [Reason.APPROVAL_RACE, Reason.APPROVAL_STALE])
@pytest.mark.parametrize("action", ["reject", "cancel"])
def test_atomic_finish_conflict_preserves_pending_for_retry(tmp_path, reason, action):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):

            def rows(connection):
                return tuple(
                    tuple(tuple(row) for row in connection.execute(query))
                    for query in (
                        "SELECT * FROM tasks ORDER BY task_id",
                        "SELECT * FROM requests ORDER BY request_id",
                        "SELECT * FROM approvals ORDER BY approval_id",
                        "SELECT * FROM audit_events ORDER BY event_id",
                    )
                )

            before = await store._run_on_writer_async(rows)
            original = store.finish_approval
            snapshot = guard._approvals._memory_snapshots["approval"]

            async def conflict(*args, **kwargs):
                raise StoreError(reason)

            store.finish_approval = conflict
            try:
                control = guard.human_control("operator")
                command = control.reject if action == "reject" else control.cancel
                identifier = "approval" if action == "reject" else "task"
                result = await command(identifier)
                assert isinstance(result, Denied)
                assert result.reasons == (reason,)
                assert await store._run_on_writer_async(rows) == before
                assert not guard._global_halt
                assert store.execution_state().execution_enabled
                assert not harness.transport.calls
                assert guard._approvals._memory_snapshots["approval"] is snapshot
                assert "approval" in guard._approvals._expiry_tasks
                assert not guard._approvals._expiry_tasks["approval"].done()
                assert not handle._completed
            finally:
                store.finish_approval = original
            retry = await command(identifier)
            assert isinstance(retry, Denied)
            final = await asyncio.wait_for(handle.wait(), timeout=1)
            assert isinstance(final.outcome, Denied)
            record = await store.get_approval("approval")
            assert record.status is (
                ApprovalStatus.REJECTED if action == "reject" else ApprovalStatus.CANCELLED
            )
            state = await store.load_task("task")
            assert state.status is (TaskStatus.RUNNING if action == "reject" else TaskStatus.HALTED)
            assert not harness.transport.calls

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["approve", "submit"])
def test_policy_mismatch_does_not_halt_task_before_atomic_finish(tmp_path, entry):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            original = store.finish_approval
            seen = []

            async def observe(*args, **kwargs):
                def read(connection):
                    return (
                        connection.execute(
                            "SELECT status FROM tasks WHERE task_id=?", ("task",)
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT status FROM approvals WHERE approval_id=?", ("approval",)
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT status FROM requests WHERE request_id=?", ("request",)
                        ).fetchone()[0],
                    )

                seen.append(await store._run_on_writer_async(read))
                return await original(*args, **kwargs)

            store.finish_approval = observe
            guard._policy_version = "changed"
            try:
                if entry == "approve":
                    result = await guard.human_control("operator").approve("approval")
                else:
                    result = await guard.submit("task", _call("send"), expected_epoch=1)
                assert isinstance(result, Denied)
                assert result.reasons == (Reason.POLICY_CHANGED,)
                assert seen == [(TaskStatus.WAITING_APPROVAL.value, "PENDING", "WAITING_APPROVAL")]
                assert (await store.load_task("task")).status is TaskStatus.HALTED
                assert (await store.get_approval("approval")).status is ApprovalStatus.INVALIDATED
                assert not harness.transport.calls
                final = await asyncio.wait_for(handle.wait(), timeout=1)
                assert isinstance(final.outcome, Denied)
            finally:
                store.finish_approval = original

    asyncio.run(scenario())


def test_expired_timer_conflict_retries_with_backoff_and_finishes(tmp_path):
    async def scenario():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            clock = guard._approvals._sleep.__self__
            original = store.finish_approval
            attempts = 0

            async def conflict(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                raise StoreError(Reason.APPROVAL_RACE)

            async def wait_for_attempt(expected):
                for _ in range(200):
                    if attempts >= expected:
                        return
                    await asyncio.sleep(0.01)
                pytest.fail("expiry retry did not reach Store")

            store.finish_approval = conflict
            try:
                clock.advance(utc=301, mono=301)
                await wait_for_attempt(1)
                await _turns()
                assert attempts == 1
                assert not handle._completed
                assert "approval" in guard._approvals._memory_snapshots
                assert "approval" in guard._approvals._expiry_tasks
                await _turns()
                assert attempts == 1
                clock.advance(utc=1, mono=1)
                await wait_for_attempt(2)
                await _turns()
                assert attempts == 2
            finally:
                store.finish_approval = original
            clock.advance(utc=1, mono=1)
            await _turns()
            final = await asyncio.wait_for(handle.wait(), timeout=1)
            assert final.cause == "expired"
            assert (await store.get_approval("approval")).status is ApprovalStatus.EXPIRED
            assert not harness.transport.calls

    asyncio.run(scenario())
