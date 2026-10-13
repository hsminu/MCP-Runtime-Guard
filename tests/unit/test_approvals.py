"""B5 I2/I3/I6/I7/I9/T09–T12/T20/T37의 서비스 단위 검증.

Store·Host는 이 파일 전용 대역이다. SQLite 원자성, UI 제어 큐나 실제 wire
계수를 검증하지 않는다. dispatch 목록은 Host 위임 횟수만 뜻한다.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from guard_app.core.policy import evaluate
from guard_app.domain.contracts import ApprovalRecord, Denied, RegisteredServer, Withheld
from guard_app.domain.enums import (
    ApprovalStatus,
    Decision,
    Label,
    RegStatus,
    TaskStatus,
    ToolKind,
    TrustTier,
)
from guard_app.domain.errors import ResolutionError, StoreError
from guard_app.domain.models import (
    AccessResult,
    Boundary,
    PolicyDecision,
    PolicyInput,
    ProposedCall,
    ResolvedRequest,
    TaskState,
    ToolSpec,
)
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.services.approvals import ApprovalConfigError, ApprovalService, HumanControl
from guard_app.services.catalog import Catalog

_NO_OVERRIDE = object()


class _Clock:
    def __init__(self) -> None:
        self.utc = datetime(2026, 10, 11, tzinfo=UTC)
        self.mono = 100.0

    def utc_now(self) -> datetime:
        return self.utc

    def monotonic(self) -> float:
        return self.mono


class _Registry:
    def __init__(self, registration: RegisteredServer) -> None:
        self.record = registration
        self.current_status = RegStatus.ACTIVE
        self.current_fingerprint = registration.tools[0].fingerprint

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self.record if server_id == self.record.server_id else None

    def status(self, server_id: str) -> RegStatus | None:
        return self.current_status if server_id == self.record.server_id else None

    def fingerprint(self, tool: ToolSpec) -> str | None:
        return self.current_fingerprint


class _Store:
    def __init__(self, state: TaskState, *, bump_status_version: bool = False) -> None:
        self.state = state
        self.approvals: dict[str, ApprovalRecord] = {}
        self.consumed: list[str] = []
        self.statuses: list[TaskStatus] = []
        self.fail: str | None = None
        self.consume_result = True
        self.bump_status_version = bump_status_version
        self.mutate_waiting: dict[str, object] = {}
        self.on_consume = None
        self.transition_errors = 0
        self.consume_return_override = _NO_OVERRIDE

    async def load_task(self, task_id: str) -> TaskState:
        if self.fail == "load":
            raise StoreError()
        assert task_id == self.state.task_id
        return self.state

    async def set_task_status(self, task_id, status, *, expected_row_revision, at_utc, reason):
        if self.fail == "set_status":
            raise StoreError()
        assert task_id == self.state.task_id
        assert expected_row_revision == self.state.row_revision
        assert self.state.status not in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME)
        update = {"status": status, "row_revision": self.state.row_revision + 1}
        if self.bump_status_version:
            update["security_version"] = self.state.security_version + 1
        if status is TaskStatus.WAITING_APPROVAL:
            update.update(self.mutate_waiting)
        self.state = self.state.model_copy(update=update)
        self.statuses.append(status)
        return self.state

    async def create_approval(self, approval: ApprovalRecord):
        if self.fail == "create":
            raise StoreError()
        assert approval.approval_id not in self.approvals
        self.approvals[approval.approval_id] = approval

    async def get_approval(self, approval_id):
        if self.fail == "get":
            raise StoreError()
        return self.approvals.get(approval_id)

    async def transition_approval(
        self, approval_id, status, *, expected_row_revision, approver_id, now_utc, reason
    ):
        if self.fail == "transition":
            raise StoreError()
        if self.transition_errors:
            self.transition_errors -= 1
            raise ValueError("RAW_ARGUMENT_MARKER")
        record = self.approvals[approval_id]
        assert record.row_revision == expected_row_revision
        allowed = {
            ApprovalStatus.PENDING: {
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.CANCELLED,
            },
            ApprovalStatus.APPROVED: {ApprovalStatus.INVALIDATED, ApprovalStatus.EXPIRED},
        }
        assert status in allowed[record.status]
        record = record.model_copy(
            update={
                "status": status,
                "row_revision": record.row_revision + 1,
                "decided_at_utc": now_utc,
            }
        )
        self.approvals[approval_id] = record
        return record

    async def consume_and_reserve(
        self,
        *,
        approval_id,
        snapshot_hash,
        request_id,
        task_id,
        expected_security_version,
        now_utc,
    ):
        if self.fail == "consume":
            raise StoreError()
        record = self.approvals[approval_id]
        assert record.status is ApprovalStatus.APPROVED
        assert record.snapshot_hash == snapshot_hash
        assert record.request_id == request_id
        assert self.state.task_id == task_id
        assert self.state.status is TaskStatus.WAITING_APPROVAL
        assert self.state.security_version == expected_security_version
        assert record.expires_at_utc > now_utc
        if self.consume_return_override is not _NO_OVERRIDE:
            return self.consume_return_override
        if self.on_consume is not None:
            self.on_consume()
        if not self.consume_result:
            return False
        self.approvals[approval_id] = record.model_copy(
            update={
                "status": ApprovalStatus.CONSUMED,
                "consumed_at_utc": now_utc,
                "row_revision": record.row_revision + 1,
            }
        )
        self.state = self.state.model_copy(
            update={"status": TaskStatus.RUNNING, "row_revision": self.state.row_revision + 1}
        )
        self.consumed.append(approval_id)
        return True


class _Host:
    def __init__(self, store: _Store, registry: _Registry, clock: _Clock) -> None:
        self.store, self.registry, self.clock = store, registry, clock
        self.lock = asyncio.Lock()
        self.policy_version = "policy-1"
        self.profile_id = "default"
        self.dispatched: list[ResolvedRequest] = []
        self.phases: list[str] = []
        self.halted: list[Reason] = []
        self.globally_halted = False
        self.force_decision: Decision | None = None
        self.fail: str | None = None
        self.recheck_hook = None
        self.dispatch_started = None
        self.dispatch_finish = None
        self.service: ApprovalService | None = None
        self.dispatch_expiry: tuple[datetime, float] | None = None

    def task_lock(self, task_id: str) -> asyncio.Lock:
        assert task_id == self.store.state.task_id
        return self.lock

    def request_stop(self, task_id):
        self.stopped = task_id

    def check_stop(self, task_id):
        if getattr(self, "stopped", None) == task_id:
            raise ResolutionError(Reason.TASK_NOT_RUNNING)

    def policy_matches(self, state: TaskState) -> bool:
        return (
            not self.globally_halted
            and state.policy_version == self.policy_version
            and state.profile_id == self.profile_id
        )

    def effective_task_label(self, state: TaskState) -> Label:
        return state.high_watermark

    async def evaluate_request(self, req, state, *, phase):
        assert self.lock.locked()
        self.phases.append(phase)
        if self.fail == "evaluate":
            raise ValueError("RAW_ARGUMENT_MARKER")
        if self.recheck_hook is not None:
            self.recheck_hook()
        decision = evaluate(
            PolicyInput(
                request=req,
                state=state,
                effective_label=state.high_watermark,
                registration_status=self.registry.current_status,
                registered_fingerprint=self.registry.current_fingerprint,
                access=AccessResult(allowed=True, error=False, reason=""),
                policy_version=self.policy_version,
                phase=phase,
            )
        )
        if self.force_decision is not None:
            return decision.model_copy(update={"decision": self.force_decision})
        return decision

    async def dispatch_reserved(self, req, state, *, expires_at_utc=None, monotonic_deadline=None):
        assert self.lock.locked()
        assert self.store.approvals["approval-1"].status is ApprovalStatus.CONSUMED
        assert self.service is not None
        assert not self.service._memory_snapshots
        assert type(expires_at_utc) is datetime
        assert type(monotonic_deadline) is float
        self.dispatch_expiry = (expires_at_utc, monotonic_deadline)
        self.dispatched.append(req)
        if self.dispatch_started is not None:
            self.dispatch_started.set()
        if self.dispatch_finish is not None:
            await self.dispatch_finish.wait()
        return Withheld(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))

    async def halt_task(self, task_id, reason, *, expected_epoch=None):
        if expected_epoch is not None and (
            task_id != self.store.state.task_id or self.store.state.epoch != expected_epoch
        ):
            raise StoreError(Reason.APPROVAL_STALE)
        self.halted.append(reason)
        self.store.state = self.store.state.model_copy(
            update={
                "status": TaskStatus.HALTED,
                "security_version": self.store.state.security_version + 1,
                "row_revision": self.store.state.row_revision + 1,
            }
        )
        return self.store.state

    def global_halt(self):
        self.globally_halted = True

    async def pre_dispatch_failure(self, task_id, exc):
        if self.fail == "containment":
            raise StoreError()
        if isinstance(exc, StoreError):
            self.global_halt()
        return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))


def _setup(*, bump_status_version=False, ttl_seconds=300):
    state = TaskState(
        task_id="task-1",
        epoch=2,
        profile_id="default",
        policy_version="policy-1",
        high_watermark=Label.INTERNAL,
        status=TaskStatus.RUNNING,
    )
    tool = ToolSpec(
        server_id="sender",
        tool_name="send",
        kind=ToolKind.SEND,
        input_schema={
            "type": "object",
            "required": ["destination", "body"],
            "properties": {"destination": {"type": "string"}, "body": {"type": "string"}},
        },
        destination_arg="destination",
        resource_arg=None,
        fingerprint="a" * 64,
    )
    registry = _Registry(
        RegisteredServer(
            server_id="sender",
            status=RegStatus.ACTIVE,
            boundary=Boundary(
                boundary_id="server", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
            ),
            tools=(tool,),
        )
    )
    catalog = Catalog(
        registry,
        profile_id="default",
        destinations=(
            Boundary(
                boundary_id="external", tier=TrustTier.GENERAL_EXTERNAL, max_label=Label.INTERNAL
            ),
        ),
    )
    req = catalog.resolve(
        state,
        ProposedCall(
            tool_ref="sender__send",
            arguments={
                "destination": "external",
                "body": "RAW_ARGUMENT_MARKER",
            },
            model_call_id="model-untrusted",
        ),
        request_id="request-1",
        call_seq=9,
    )
    decision = PolicyDecision(
        decision=Decision.ASK,
        reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        verdicts=(),
        policy_version="policy-1",
        security_version=0,
    )
    store, clock = _Store(state, bump_status_version=bump_status_version), _Clock()
    host = _Host(store, registry, clock)
    service = ApprovalService(
        cast(Store, store),
        catalog,
        host,
        approver_id="operator-1",
        approval_id_factory=lambda: "approval-1",
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        ttl_seconds=ttl_seconds,
    )
    host.service = service
    return service, store, host, clock, registry, req, state, decision


async def _open(setup):
    service, _, host, _, _, req, state, decision = setup
    async with host.lock:
        return await service.open(req, state, decision)


@pytest.mark.parametrize("bump", [False, True])
def test_open_freezes_waiting_returned_version_and_only_persists_metadata(bump):
    async def scenario():
        setup = _setup(bump_status_version=bump)
        service, store, host, _, _, req, _, _ = setup
        approval = await _open(setup)
        snapshot = service._memory_snapshots[approval.approval_id]
        assert snapshot.state.status is TaskStatus.WAITING_APPROVAL
        assert snapshot.state.security_version == store.state.security_version == int(bump)
        assert snapshot.decision.security_version == snapshot.state.security_version
        assert snapshot.request == req
        assert snapshot.hash == approval.snapshot_hash
        assert "RAW_ARGUMENT_MARKER" not in approval.model_dump_json()
        assert "RAW_ARGUMENT_MARKER" not in repr(snapshot)
        assert not host.dispatched
        assert not store.consumed
        assert not host.lock.locked()

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_open_only_accepts_running_ask(decision):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, req, state, initial = setup
        async with host.lock:
            with pytest.raises(StoreError):
                await service.open(req, state, initial.model_copy(update={"decision": decision}))
        assert store.state.status is TaskStatus.RUNNING
        assert not store.approvals

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("high_watermark", Label.CONFIDENTIAL),
        ("profile_id", "other"),
        ("policy_version", "changed"),
        ("epoch", 3),
        ("unclassified", True),
    ],
)
def test_open_rejects_security_context_mutation_during_waiting_transition(field, value):
    async def scenario():
        setup = _setup()
        setup[1].mutate_waiting[field] = value
        with pytest.raises(StoreError):
            await _open(setup)
        assert not setup[1].approvals
        assert not setup[0]._memory_snapshots

    asyncio.run(scenario())


@pytest.mark.parametrize("bump", [False, True])
def test_approved_ask_rechecks_consumes_and_dispatches_same_request_once(bump):
    async def scenario():
        setup = _setup(bump_status_version=bump)
        service, store, host, _, _, req, _, _ = setup
        approval = await _open(setup)
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Withheld)
        assert store.consumed == [approval.approval_id]
        assert host.dispatched == [req]
        assert host.phases == ["approval_recheck"]
        assert host.dispatch_expiry == (approval.expires_at_utc, 400.0)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.CONSUMED
        assert not service._memory_snapshots
        assert not host.lock.locked()
        again = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(again, Denied)
        assert again.reasons == (Reason.APPROVAL_RACE,)
        assert len(host.dispatched) == len(store.consumed) == 1

    asyncio.run(scenario())


def test_double_click_is_serialized_and_waits_through_dispatch_completion():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        host.dispatch_started, host.dispatch_finish = asyncio.Event(), asyncio.Event()
        approval = await _open(setup)
        control = service.human_channel("operator-1")
        first = asyncio.create_task(control.approve(approval.approval_id))
        await host.dispatch_started.wait()
        second = asyncio.create_task(control.approve(approval.approval_id))
        await asyncio.sleep(0)
        assert host.lock.locked()
        assert not second.done()
        assert len(store.consumed) == len(host.dispatched) == 1
        host.dispatch_finish.set()
        result1, result2 = await asyncio.gather(first, second)
        assert isinstance(result1, Withheld)
        assert isinstance(result2, Denied)
        assert len(store.consumed) == len(host.dispatched) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("which", ["utc", "monotonic", "both"])
def test_either_clock_expires_pending_before_approval(which):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        if which in ("utc", "both"):
            clock.utc += timedelta(seconds=300)
        if which in ("monotonic", "both"):
            clock.mono += 300
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.EXPIRED
        assert store.state.status is TaskStatus.RUNNING
        assert not host.phases
        assert not host.dispatched
        assert not store.consumed
        assert not service._memory_snapshots

    asyncio.run(scenario())


@pytest.mark.parametrize("which", ["utc", "monotonic"])
def test_expiry_during_recheck_prevents_consume(which):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)

        def cross_deadline():
            if which == "utc":
                clock.utc += timedelta(seconds=300)
            else:
                clock.mono += 300

        host.recheck_hook = cross_deadline
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.EXPIRED
        assert not host.dispatched
        assert not store.consumed

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["reject", "expire"])
def test_reject_or_expire_discards_memory_and_safely_resumes(mode):
    async def scenario():
        setup = _setup(bump_status_version=True)
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        control = service.human_channel("operator-1")
        if mode == "expire":
            clock.mono += 300
            await control.expire(approval.approval_id)
            expected = ApprovalStatus.EXPIRED
        else:
            await control.reject(approval.approval_id)
            expected = ApprovalStatus.REJECTED
        assert store.approvals[approval.approval_id].status is expected
        assert store.state.status is TaskStatus.RUNNING
        assert not service._memory_snapshots
        assert not host.dispatched
        await control.approve(approval.approval_id)
        assert not host.dispatched

    asyncio.run(scenario())


def test_expire_command_cannot_force_early_expiry():
    async def scenario():
        setup = _setup()
        approval = await _open(setup)
        result = await setup[0].human_channel("operator-1").expire(approval.approval_id)
        assert result.reasons == (Reason.APPROVAL_RACE,)
        assert setup[1].approvals[approval.approval_id].status is ApprovalStatus.PENDING
        assert setup[1].state.status is TaskStatus.WAITING_APPROVAL
        assert setup[0]._memory_snapshots

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "emergency_halt"])
def test_cancel_and_emergency_halt_do_not_wait_for_human_or_resume(action):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, state, _ = setup
        approval = await _open(setup)
        await getattr(service.human_channel("operator-1"), action)(state.task_id)
        assert store.state.status is TaskStatus.HALTED
        assert store.approvals[approval.approval_id].status is ApprovalStatus.CANCELLED
        assert not service._memory_snapshots
        assert not host.lock.locked()
        await service.human_channel("operator-1").approve(approval.approval_id)
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_recheck_allow_or_deny_invalidates_and_never_consumes(decision):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        host.force_decision = decision
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert not store.consumed
        assert not host.dispatched
        assert store.state.status is TaskStatus.RUNNING
        assert not service._memory_snapshots

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["profile", "policy", "fingerprint", "registration", "version"])
def test_security_or_registration_change_halts_and_invalidates(change):
    async def scenario():
        setup = _setup()
        service, store, host, _, registry, _, _, _ = setup
        approval = await _open(setup)
        if change == "profile":
            host.profile_id = "changed"
        elif change == "policy":
            host.policy_version = "changed"
        elif change == "fingerprint":
            registry.current_fingerprint = "b" * 64
        elif change == "registration":
            registry.current_status = RegStatus.PENDING
        else:
            store.state = store.state.model_copy(update={"security_version": 9})
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert store.approvals[approval.approval_id].status in (
            ApprovalStatus.CANCELLED,
            ApprovalStatus.INVALIDATED,
        )
        assert store.state.status is TaskStatus.HALTED
        assert not host.dispatched
        assert not store.consumed
        assert not service._memory_snapshots

    asyncio.run(scenario())


def test_changed_destination_with_same_id_changes_snapshot_hash_and_cannot_execute():
    async def scenario():
        setup = _setup()
        service, store, host, _, registry, _, _, _ = setup
        approval = await _open(setup)
        service._catalog = Catalog(
            registry,
            profile_id="default",
            destinations=(
                Boundary(
                    boundary_id="external",
                    tier=TrustTier.APPROVED_EXTERNAL,
                    max_label=Label.INTERNAL,
                ),
            ),
        )
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.APPROVAL_STALE,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_hash", "b" * 64),
        ("request_id", "other-request"),
        ("approver_id", "other-operator"),
    ],
)
def test_persisted_approval_metadata_mismatch_fails_closed(field, value):
    async def scenario():
        setup = _setup()
        approval = await _open(setup)
        setup[1].approvals[approval.approval_id] = approval.model_copy(update={field: value})
        result = await setup[0].human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert setup[1].state.status is TaskStatus.HALTED
        assert not setup[1].consumed
        assert not setup[2].dispatched

    asyncio.run(scenario())


def test_restart_context_is_not_restored_from_hash_only_record():
    async def scenario():
        setup = _setup()
        approval = await _open(setup)
        service, store, host, clock, _, _, _, _ = setup
        restarted = ApprovalService(
            cast(Store, store),
            service._catalog,
            host,
            approver_id="operator-1",
            approval_id_factory=lambda: "unused",
            utc_now=clock.utc_now,
            monotonic=clock.monotonic,
        )
        result = await restarted.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.APPROVAL_CONTEXT_LOST,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.EXPIRED
        assert not restarted._memory_snapshots
        assert not host.dispatched
        assert not store.consumed
        assert store.state.status is TaskStatus.WAITING_APPROVAL

    asyncio.run(scenario())


def test_consume_conflict_invalidates_without_dispatch():
    async def scenario():
        setup = _setup()
        approval = await _open(setup)
        setup[1].consume_result = False
        result = await setup[0].human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.APPROVAL_RACE,)
        assert setup[1].approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert not setup[1].consumed
        assert not setup[2].dispatched
        assert not setup[0]._memory_snapshots

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["get", "load", "transition", "consume", "evaluate"])
def test_preconsume_exception_never_dispatches_and_discards_raw_memory(failure):
    async def scenario():
        setup = _setup()
        approval = await _open(setup)
        if failure == "evaluate":
            setup[2].fail = "evaluate"
        else:
            setup[1].fail = failure
        result = await setup[0].human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert "RAW_ARGUMENT_MARKER" not in result.model_dump_json()
        assert not setup[1].consumed
        assert not setup[2].dispatched
        assert not setup[0]._memory_snapshots
        if setup[1].state.status is not TaskStatus.HALTED:
            assert setup[2].globally_halted

    asyncio.run(scenario())


def test_containment_failure_activates_global_halt_and_returns_fixed_denial():
    async def scenario():
        setup = _setup()
        approval = await _open(setup)
        setup[1].fail, setup[2].fail = "get", "containment"
        result = await setup[0].human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert setup[2].globally_halted
        assert not setup[2].dispatched
        assert not setup[0]._memory_snapshots

    asyncio.run(scenario())


@pytest.mark.parametrize("operator", ["", "other-operator", 1, True])
def test_only_preconfigured_human_operator_gets_control_capability(operator):
    setup = _setup()
    with pytest.raises(ApprovalConfigError):
        setup[0].human_channel(operator)
    assert not hasattr(setup[0], "approve")
    with pytest.raises(ApprovalConfigError):
        HumanControl(setup[0], "operator-1", object())


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "300"])
def test_ttl_configuration_is_strict_positive_integer(ttl):
    with pytest.raises(ApprovalConfigError):
        _setup(ttl_seconds=ttl)


def test_unknown_approval_returns_fixed_denial_without_execution():
    async def scenario():
        setup = _setup()
        control = setup[0].human_channel("operator-1")
        for command in (control.approve, control.reject, control.expire):
            result = await command("unknown")
            assert isinstance(result, Denied)
            assert result.request_id is None
        assert not setup[1].consumed
        assert not setup[2].dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["approve", "reject", "cancel"])
def test_cancellation_while_waiting_for_task_lock_is_contained(command):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, state, _ = setup
        approval = await _open(setup)
        await host.lock.acquire()
        control = service.human_channel("operator-1")
        identifier = state.task_id if command == "cancel" else approval.approval_id
        queued = asyncio.create_task(getattr(control, command)(identifier))
        await asyncio.sleep(0)
        assert not queued.done()
        queued.cancel()
        result = await queued
        assert isinstance(result, Denied)
        assert host.globally_halted
        assert store.state.status is TaskStatus.WAITING_APPROVAL
        assert not host.dispatched
        assert not store.consumed
        host.lock.release()

    asyncio.run(scenario())


def test_known_resolution_error_during_recheck_invalidates_approved_and_halts():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)

        async def changed_definition(req, state, *, phase):
            from guard_app.domain.errors import ResolutionError

            raise ResolutionError(Reason.TOOL_FINGERPRINT_CHANGED)

        host.evaluate_request = changed_definition
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert result.reasons == (Reason.TOOL_FINGERPRINT_CHANGED,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert store.state.status is TaskStatus.HALTED
        assert not service._memory_snapshots
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("which", ["security", "policy", "task", "epoch", "unclassified"])
def test_open_rejects_stale_initial_decision_or_request_before_waiting_transition(which):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, req, state, decision = setup
        if which == "security":
            decision = decision.model_copy(update={"security_version": 2})
        elif which == "policy":
            decision = decision.model_copy(update={"policy_version": "other-policy"})
        elif which == "task":
            req = req.model_copy(update={"task_id": "other-task"})
        elif which == "epoch":
            req = req.model_copy(update={"epoch": 99})
        else:
            state = state.model_copy(update={"unclassified": True})
        async with host.lock:
            with pytest.raises(StoreError):
                await service.open(req, state, decision)
        assert store.state.status is TaskStatus.RUNNING
        assert not store.statuses
        assert not store.approvals
        assert not service._memory_snapshots

    asyncio.run(scenario())


def test_global_halt_raised_during_recheck_prevents_consume():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        host.recheck_hook = host.global_halt
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.POLICY_CHANGED,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert store.state.status is TaskStatus.HALTED
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


def test_last_read_experiment_metadata_alone_does_not_invalidate_accumulate_snapshot():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        store.state = store.state.model_copy(update={"last_read_label": Label.PUBLIC})
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Withheld)
        assert len(store.consumed) == len(host.dispatched) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["reject", "expire"])
def test_termination_command_cannot_resume_with_mismatched_persisted_request(command):
    async def scenario():
        setup = _setup()
        service, store, host, clock, _, _, _, _ = setup
        approval = await _open(setup)
        store.approvals[approval.approval_id] = approval.model_copy(
            update={"request_id": "other-request"}
        )
        clock.mono += 300
        result = await getattr(service.human_channel("operator-1"), command)(approval.approval_id)
        assert result.reasons == (Reason.APPROVAL_STALE,)
        assert store.state.status is TaskStatus.HALTED
        assert store.approvals[approval.approval_id].status is ApprovalStatus.CANCELLED
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


def test_error_after_snapshot_removal_still_terminalizes_live_approval():
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        store.transition_errors = 1
        result = await service.human_channel("operator-1").reject(approval.approval_id)
        assert result.reasons == (Reason.INTERNAL_ERROR,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.CANCELLED
        assert store.state.status is TaskStatus.HALTED
        assert not service._memory_snapshots
        assert not host.dispatched

    asyncio.run(scenario())


def test_removed_destination_invalidates_approval_and_halts_waiting_task():
    async def scenario():
        setup = _setup()
        service, store, host, _, registry, _, _, _ = setup
        approval = await _open(setup)
        service._catalog = Catalog(registry, profile_id="default", destinations=())
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (Reason.UNKNOWN_DESTINATION,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert store.state.status is TaskStatus.HALTED
        assert not service._memory_snapshots
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", [Reason.INVALID_ARGUMENTS, Reason.INTERNAL_ERROR])
def test_known_recheck_resolution_failure_never_leaves_orphan_waiting_task(reason):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)

        async def unresolved(req, state, *, phase):
            from guard_app.domain.errors import ResolutionError

            raise ResolutionError(reason)

        host.evaluate_request = unresolved
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert result.reasons == (reason,)
        assert store.approvals[approval.approval_id].status is ApprovalStatus.INVALIDATED
        assert store.state.status is TaskStatus.HALTED
        assert not service._memory_snapshots
        assert not store.consumed
        assert not host.dispatched

    asyncio.run(scenario())


@pytest.mark.parametrize("ack", [None, 0, 1, "false"])
def test_consume_ack_requires_exact_boolean_before_dispatch(ack):
    async def scenario():
        setup = _setup()
        service, store, host, _, _, _, _, _ = setup
        approval = await _open(setup)
        store.consume_return_override = ack
        result = await service.human_channel("operator-1").approve(approval.approval_id)
        assert isinstance(result, Denied)
        assert host.globally_halted
        assert not host.dispatched
        assert not service._memory_snapshots

    asyncio.run(scenario())
