"""사람 승인 명령의 낡은 조회값을 실제 저장소와 Guard에서 검증한다."""

import asyncio
from contextlib import asynccontextmanager
from itertools import count

import pytest

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.reasons import Reason
from guard_app.services.guard import GuardService
from guard_app.storage import SQLiteStore
from tests.unit.test_guard import NOW, _call, _Harness
from tests.unit.test_pending_notifications import _Clock, _turns


@asynccontextmanager
async def _pending(tmp_path):
    clock = _Clock()
    harness = _Harness(label=Label.INTERNAL)
    ids = count(1)
    with SQLiteStore(
        tmp_path / "revision.db",
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
            approval_sleep=clock.sleep,
        )
        try:
            result = await guard.submit("task", _call("send"), expected_epoch=1)
            assert isinstance(result, Pending)
            handle = guard.pending_result(result.request_id)
            await _turns()
            yield guard, store, harness, handle
        finally:
            await guard.aclose()


async def _state(guard, store, harness, handle):
    def read(connection):
        return tuple(
            tuple(tuple(row) for row in connection.execute(sql))
            for sql in (
                "SELECT * FROM tasks ORDER BY task_id",
                "SELECT * FROM requests ORDER BY request_id",
                "SELECT * FROM approvals ORDER BY approval_id",
                "SELECT * FROM audit_events ORDER BY event_id",
            )
        )

    service = guard._approvals
    return (
        await store._run_on_writer_async(read),
        store._test_context_snapshot(),
        service._memory_snapshots.copy(),
        service._pending.copy(),
        service._results.copy(),
        service._expiry_tasks.copy(),
        tuple(harness.transport.calls),
        handle._completed,
    )


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_stale_revision_refuses_without_any_mutation(tmp_path, action):
    async def run():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            record = await store.get_approval("approval")
            before = await _state(guard, store, harness, handle)
            result = await getattr(guard.human_control("operator"), action)(
                "approval", expected_row_revision=record.row_revision + 1
            )
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.APPROVAL_RACE,)
            assert await _state(guard, store, harness, handle) == before
            assert not handle._completed

    asyncio.run(run())


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("revision", ["matching", "none"])
def test_current_revision_and_none_preserve_decision(tmp_path, action, revision):
    async def run():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            record = await store.get_approval("approval")
            expected = record.row_revision if revision == "matching" else None
            result = await getattr(guard.human_control("operator"), action)(
                "approval", expected_row_revision=expected
            )
            updated = await store.get_approval("approval")
            if action == "approve":
                assert isinstance(result, Released)
                assert updated.status is ApprovalStatus.CONSUMED
                assert len(harness.transport.calls) == 1
            else:
                assert isinstance(result, Denied)
                assert updated.status is ApprovalStatus.REJECTED
                assert harness.transport.calls == []
            assert handle._completed

    asyncio.run(run())


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("invalid", [True, -1, "0", 1.0])
def test_invalid_revision_refuses_without_mutation(tmp_path, action, invalid):
    async def run():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            before = await _state(guard, store, harness, handle)
            result = await getattr(guard.human_control("operator"), action)(
                "approval", expected_row_revision=invalid
            )
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.INVALID_ARGUMENTS,)
            assert await _state(guard, store, harness, handle) == before
            assert not handle._completed

    asyncio.run(run())


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_revision_checked_after_acquiring_task_lock(tmp_path, action):
    async def run():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            revision = (await store.get_approval("approval")).row_revision
            lock = guard._task_lock("task")
            await lock.acquire()
            command = asyncio.create_task(
                getattr(guard.human_control("operator"), action)(
                    "approval", expected_row_revision=revision
                )
            )
            try:
                await _turns()
                assert not command.done()
                await store._run_on_writer_async(
                    lambda connection: (
                        connection.execute(
                            "UPDATE approvals SET row_revision = ? WHERE approval_id = ?",
                            (revision + 1, "approval"),
                        ).rowcount
                    ),
                    write=True,
                )
                before = await _state(guard, store, harness, handle)
            finally:
                lock.release()
            result = await command
            assert isinstance(result, Denied)
            assert result.reasons == (Reason.APPROVAL_RACE,)
            assert await _state(guard, store, harness, handle) == before
            assert not handle._completed

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
def test_stop_still_works_after_stale_approval(tmp_path, action):
    async def run():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            control = guard.human_control("operator")
            revision = (await store.get_approval("approval")).row_revision
            stale = await control.approve("approval", expected_row_revision=revision + 1)
            assert stale.reasons == (Reason.APPROVAL_RACE,)
            await getattr(control, action)("task")
            assert (await store.load_task("task")).status is TaskStatus.HALTED
            assert (await store.get_approval("approval")).status is ApprovalStatus.CANCELLED
            assert handle._completed
            assert harness.transport.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("revision", ["stale", "matching", None, True, -1, "0"])
def test_revision_without_owned_context_preserves_live_row(tmp_path, action, revision, monkeypatch):
    async def run():
        async with _pending(tmp_path) as (guard, store, harness, handle):
            record = await store.get_approval("approval")
            assert record is not None
            assert record.status is ApprovalStatus.PENDING
            before = await _state(guard, store, harness, handle)
            # 새 Guard에는 승인 소유 메모리가 없다. 행은 여전히 실제 저장소에 있다.
            other = GuardService(
                store,
                harness.registry,
                harness.catalog,
                harness.transport,
                harness.access,
                profile_id="p",
                policy_version="v",
                operator_id="operator",
                request_id_factory=lambda: "other-request",
                approval_id_factory=lambda: "other-approval",
                next_call_seq=harness.sequence,
                failure_audit=harness.failure_audit,
                utc_now=lambda: NOW,
                monotonic=lambda: 100.0,
            )
            lock_attempts = []

            def unexpected_lock(task_id):
                lock_attempts.append(task_id)
                raise AssertionError("소유 관계 없이 Task lock을 추측할 수 없다")

            monkeypatch.setattr(other, "_task_lock", unexpected_lock)
            try:
                expected = (
                    record.row_revision + 1
                    if revision == "stale"
                    else record.row_revision
                    if revision == "matching"
                    else revision
                )
                result = await getattr(other.human_control("operator"), action)(
                    "approval", expected_row_revision=expected
                )
                assert isinstance(result, Denied)
                assert result.reasons == (Reason.APPROVAL_CONTEXT_LOST,)
                assert lock_attempts == []
                assert await _state(guard, store, harness, handle) == before
                assert not handle._completed
            finally:
                await other.aclose()

    asyncio.run(run())
