"""B-QA-01/08: 승인 시계 역행과 Store 반환값의 서비스 단위 회귀.

I2/I6/I7/I9, T09–T12/T20의 B 단위 부분이다. Host 위임 목록은 실제 wire나
수신 부작용 계수가 아니다. 파일 SQLite 검사는 승인 메타데이터 결속만 확인한다.
"""

import asyncio
from datetime import timedelta
from itertools import count
from pathlib import Path

import pytest

from guard_app.domain.contracts import ApprovalRecord, Denied, Withheld
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.models import Escalation, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.digest import json_digest
from guard_app.storage import SQLiteStore
from tests.unit.test_approvals import _open, _setup
from tests.unit.test_sqlite_decisions import NOW, task
from tests.unit.test_sqlite_lifecycle import Clock, pending, transition


@pytest.mark.parametrize("command", ["approve", "reject", "expire"])
def test_utc_before_creation_does_not_global_halt_valid_approval(command):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, req, _, _ = setup
        approval = await _open(setup)
        clock.utc -= timedelta(seconds=2)
        if command == "expire":
            clock.mono += 300
        result = await getattr(service.human_channel("operator-1"), command)(approval.approval_id)
        saved = store.approvals[approval.approval_id]
        assert saved.decided_at_utc == approval.created_at_utc
        assert saved.expires_at_utc == approval.expires_at_utc
        assert not host.globally_halted
        assert not host.halted
        assert not service._memory_snapshots
        if command == "approve":
            assert isinstance(result, Withheld)
            assert saved.status is ApprovalStatus.CONSUMED
            assert saved.consumed_at_utc == saved.decided_at_utc
            assert host.dispatched == [req]
            assert store.consumed == [approval.approval_id]
            assert host.dispatch_expiry == (approval.expires_at_utc, 400.0)
        else:
            assert isinstance(result, Denied)
            expected = ApprovalStatus.REJECTED if command == "reject" else ApprovalStatus.EXPIRED
            assert saved.status is expected
            assert saved.consumed_at_utc is None
            assert not host.dispatched
            assert not store.consumed
            assert store.state.status is TaskStatus.RUNNING
        repeated = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(repeated, Denied)
        assert repeated.reasons == (Reason.APPROVAL_RACE,)
        assert len(host.dispatched) == int(command == "approve")

    asyncio.run(scenario())


def test_clock_back_during_recheck_floors_consumption_at_previous_decision():
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, req, _, _ = setup
        approval = await _open(setup)
        clock.utc += timedelta(seconds=10)
        decided = clock.utc

        def move_clock_back():
            clock.utc -= timedelta(seconds=12)

        host.recheck_hook = move_clock_back
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        saved = store.approvals[approval.approval_id]
        assert isinstance(result, Withheld)
        assert saved.decided_at_utc == saved.consumed_at_utc == decided
        assert saved.expires_at_utc == approval.expires_at_utc
        assert host.dispatch_expiry == (approval.expires_at_utc, 400.0)
        assert store.consumed == [approval.approval_id]
        assert host.dispatched == [req]
        assert not host.globally_halted

    asyncio.run(scenario())


def test_clock_back_cannot_extend_monotonic_expiry_during_recheck():
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        clock.utc += timedelta(seconds=10)
        decided = clock.utc

        def reach_monotonic_deadline():
            clock.utc -= timedelta(seconds=12)
            clock.mono = 400.0

        host.recheck_hook = reach_monotonic_deadline
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        saved = store.approvals[approval.approval_id]
        assert isinstance(result, Denied)
        assert saved.status is ApprovalStatus.EXPIRED
        assert saved.decided_at_utc == decided
        assert saved.expires_at_utc == approval.expires_at_utc
        assert store.state.status is TaskStatus.RUNNING
        assert not host.globally_halted
        assert not host.dispatched
        assert not store.consumed
        assert not service._memory_snapshots

    asyncio.run(scenario())


def test_store_fresh_decision_time_remains_valid_when_utc_moves_back_after_ack():
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, req, _, _ = setup
        approval = await _open(setup)
        clock.utc += timedelta(seconds=2)
        original = store.transition_approval

        async def fresh_transition(*args, **kwargs):
            # 실제 Store의 fresh UTC는 호출 시각보다 늦을 수 있다. ACK 직후
            # wall clock 역행도 그 정상 결정 시각을 잘못된 DTO로 만들지 않는다.
            clock.utc += timedelta(seconds=1)
            kwargs["now_utc"] = clock.utc
            result = await original(*args, **kwargs)
            clock.utc -= timedelta(seconds=2)
            store.transition_approval = original
            return result

        store.transition_approval = fresh_transition
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        saved = store.approvals[approval.approval_id]
        assert isinstance(result, Withheld)
        assert (
            saved.decided_at_utc
            == saved.consumed_at_utc
            == (approval.created_at_utc + timedelta(seconds=3))
        )
        assert saved.expires_at_utc == approval.expires_at_utc
        assert host.dispatched == [req]
        assert not host.globally_halted

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["approve", "reject", "expire"])
def test_file_sqlite_approval_survives_utc_before_creation_without_ttl_extension(
    tmp_path: Path, command: str
) -> None:
    async def scenario():
        setup = _setup()
        service, _, host, clock, _, req, state, initial = setup
        identifiers = count(1)
        with SQLiteStore(
            tmp_path / "approval-clock.db",
            audit_id_factory=lambda: f"event-{next(identifiers)}",
            utc_now=clock.utc_now,
        ) as store:
            service._store = store
            await store.create_task(state, created_at_utc=clock.utc)
            await store.record_decision(
                req, initial, arguments_digest=json_digest(req.arguments), at_utc=clock.utc
            )

            async def dispatched(request, waiting, *, expires_at_utc, monotonic_deadline):
                saved = await store.get_approval("approval-1")
                assert saved is not None
                assert saved.status is ApprovalStatus.CONSUMED
                assert not service._memory_snapshots
                assert host.lock.locked()
                assert expires_at_utc == saved.expires_at_utc
                assert monotonic_deadline == 400.0
                host.dispatched.append(request)
                return Withheld(
                    request_id=request.request_id, reasons=(Reason.POST_DISPATCH_ERROR,)
                )

            host.dispatch_reserved = dispatched
            approval = await _open(setup)
            clock.utc -= timedelta(seconds=2)
            if command == "expire":
                clock.mono = 400.0
            result = await getattr(service.human_channel("operator-1"), command)(
                approval.approval_id
            )
            saved = await store.get_approval(approval.approval_id)
            assert saved is not None
            assert saved.decided_at_utc == approval.created_at_utc
            assert saved.expires_at_utc == approval.expires_at_utc
            assert not host.globally_halted
            assert not host.halted
            assert (await store.load_task(state.task_id)).status is TaskStatus.RUNNING
            if command == "approve":
                assert isinstance(result, Withheld)
                assert saved.status is ApprovalStatus.CONSUMED
                assert saved.consumed_at_utc == approval.created_at_utc
                assert host.dispatched == [req]
            else:
                assert isinstance(result, Denied)
                expected = (
                    ApprovalStatus.REJECTED if command == "reject" else ApprovalStatus.EXPIRED
                )
                assert saved.status is expected
                assert saved.consumed_at_utc is None
                assert not host.dispatched
            assert "RAW_ARGUMENT_MARKER" not in saved.model_dump_json()
            again = await service.human_channel("operator-1").approve(approval.approval_id)
            assert isinstance(again, Denied)
            assert again.reasons == (Reason.APPROVAL_RACE,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ApprovalStatus.PENDING, ApprovalStatus.APPROVED),
        (ApprovalStatus.PENDING, ApprovalStatus.REJECTED),
        (ApprovalStatus.PENDING, ApprovalStatus.EXPIRED),
        (ApprovalStatus.PENDING, ApprovalStatus.CANCELLED),
        (ApprovalStatus.APPROVED, ApprovalStatus.EXPIRED),
        (ApprovalStatus.APPROVED, ApprovalStatus.INVALIDATED),
    ],
)
def test_direct_sqlite_transition_persists_same_floored_time_as_return_and_audit(
    tmp_path: Path, source: ApprovalStatus, target: ApprovalStatus
) -> None:
    identifiers = count(1)
    clock = Clock()
    with SQLiteStore(
        tmp_path / "direct-clock.db",
        audit_id_factory=lambda: f"event-{next(identifiers)}",
        utc_now=clock,
    ) as store:
        asyncio.run(store.create_task(task(), created_at_utc=NOW))
        before = pending(store)
        if source is ApprovalStatus.APPROVED:
            before = transition(store, ApprovalStatus.APPROVED)
        clock.now = NOW - timedelta(seconds=2)
        after = asyncio.run(
            store.transition_approval(
                before.approval_id,
                target,
                expected_row_revision=before.row_revision,
                approver_id=before.approver_id,
                now_utc=clock.now,
                reason=Reason.OK,
            )
        )
        assert after.decided_at_utc == (before.decided_at_utc or before.created_at_utc)
        assert after.expires_at_utc == before.expires_at_utc
        assert asyncio.run(store.get_approval(before.approval_id)) == after
        audit_at = (
            store._owned_connection()
            .execute(
                "SELECT at_utc FROM audit_events WHERE event_type=?",
                ("APPROVAL_" + target.name,),
            )
            .fetchone()[0]
        )
        assert audit_at == after.decided_at_utc.isoformat(timespec="microseconds")


def _history_setup():
    setup = list(_setup())
    state = setup[6].model_copy(
        update={
            "last_read_label": Label.INTERNAL,
            "escalations": (
                Escalation(
                    at_seq=8,
                    source_id="previous-resource",
                    from_label=Label.PUBLIC,
                    to_label=Label.INTERNAL,
                ),
            ),
        }
    )
    setup[1].state = state
    setup[6] = state
    return tuple(setup)


_RESUME_CHANGES = [
    pytest.param("task_id", "other-task", id="wrong-task"),
    pytest.param("epoch", 3, id="wrong-epoch"),
    pytest.param("profile_id", "other-profile", id="changed-profile"),
    pytest.param("policy_version", "other-policy", id="changed-policy"),
    pytest.param("high_watermark", Label.PUBLIC, id="lower-high-watermark"),
    pytest.param("high_watermark", Label.CONFIDENTIAL, id="unexpected-high-watermark"),
    pytest.param("unclassified", True, id="changed-unclassified"),
    pytest.param("last_read_label", Label.PUBLIC, id="lost-last-read-label"),
    pytest.param("last_read_label", Label.CONFIDENTIAL, id="changed-last-read-label"),
    pytest.param("escalations", (), id="lost-history"),
    pytest.param("status", TaskStatus.WAITING_APPROVAL, id="not-resumed"),
    pytest.param("status", TaskStatus.HALTED, id="halted-result"),
    pytest.param("row_revision", 1, id="unchanged-revision"),
    pytest.param("row_revision", 3, id="skipped-revision"),
    pytest.param("security_version", 2, id="unexpected-security-version"),
]


@pytest.mark.parametrize(("field", "value"), _RESUME_CHANGES)
def test_reject_resume_rejects_store_returning_changed_task_context(field, value):
    async def scenario():
        setup = _history_setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        original = store.set_task_status

        async def changed_status(*args, **kwargs):
            returned = await original(*args, **kwargs)
            if args[1] is TaskStatus.RUNNING:
                store.set_task_status = original
                return returned.model_copy(update={field: value})
            return returned

        store.set_task_status = changed_status
        result = await service.human_channel("operator-1").reject(approval.approval_id)
        assert isinstance(result, Denied)
        assert result.reasons != (Reason.OK,)
        assert host.globally_halted
        assert not store.consumed
        assert not host.dispatched
        assert not service._memory_snapshots
        assert store.approvals[approval.approval_id].status is ApprovalStatus.REJECTED
        assert not host.lock.locked()

    asyncio.run(scenario())


_TRANSITION_CHANGES = [
    pytest.param("approval_id", "other-approval", id="wrong-approval"),
    pytest.param("request_id", "other-request", id="wrong-request"),
    pytest.param("snapshot_hash", "b" * 64, id="changed-snapshot-hash"),
    pytest.param("approver_id", "other-operator", id="changed-actor"),
    pytest.param("expires_at_utc", timedelta(seconds=301), id="extended-expiry"),
    pytest.param("expires_at_utc", timedelta(seconds=299), id="changed-expiry"),
    pytest.param("created_at_utc", timedelta(seconds=-1), id="changed-creation-time"),
    pytest.param("decided_at_utc", timedelta(seconds=1), id="wrong-decision-time"),
    pytest.param("status", ApprovalStatus.REJECTED, id="wrong-target-status"),
    pytest.param("status", ApprovalStatus.PENDING, id="unchanged-status"),
    pytest.param("status", ApprovalStatus.CONSUMED, id="unauthorized-consumption"),
    pytest.param("row_revision", 0, id="unchanged-revision"),
    pytest.param("row_revision", 2, id="skipped-revision"),
]


def _change_approval(record: ApprovalRecord, field: str, value: object) -> ApprovalRecord:
    if isinstance(value, timedelta):
        value = record.created_at_utc + value
    changes = {field: value}
    if field == "status" and value is ApprovalStatus.PENDING:
        changes["decided_at_utc"] = None
    if field == "status" and value is ApprovalStatus.CONSUMED:
        changes["consumed_at_utc"] = record.decided_at_utc
    return record.model_copy(update=changes)


@pytest.mark.parametrize(("field", "value"), _TRANSITION_CHANGES)
def test_approval_transition_changed_return_is_contained_before_consume(field, value):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        # Store fresh UTC는 호출 시각보다 늦을 수 있다. 그 대신 요청 시각보다
        # 이른 결정 반환을 검사하며 UTC 역행으로 정상 ACK를 거부하지 않는다.
        clock.utc += timedelta(seconds=2)
        original = store.transition_approval

        async def changed_transition(*args, **kwargs):
            returned = await original(*args, **kwargs)
            store.transition_approval = original
            return _change_approval(returned, field, value)

        store.transition_approval = changed_transition
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert host.globally_halted
        assert not store.consumed
        assert not host.dispatched
        assert not service._memory_snapshots
        assert not host.lock.locked()

    asyncio.run(scenario())


@pytest.mark.parametrize("bump_status_version", [False, True])
def test_valid_resume_preserves_task_context_and_accepts_supported_version_bump(
    bump_status_version,
):
    async def scenario():
        setup = _setup(bump_status_version=bump_status_version)
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        waiting: TaskState = store.state
        result = await service.human_channel("operator-1").reject(approval.approval_id)
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.OK,)
        assert store.state == waiting.model_copy(
            update={
                "status": TaskStatus.RUNNING,
                "row_revision": waiting.row_revision + 1,
                "security_version": waiting.security_version + int(bump_status_version),
            }
        )
        assert not host.globally_halted
        assert not host.dispatched
        assert not store.consumed

    asyncio.run(scenario())
