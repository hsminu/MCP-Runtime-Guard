"""기술설계서 7장: Task lock 안에서 판정·예약·응답 커밋을 집행한다.

실제 ID·순번 공급, 오류 감사, Transport·Store는 구성 시 담당 구현을 주입한다.
이 서비스는 SDK·SQLite·파일·URL에 직접 접근하지 않는다.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from guard_app.core.policy import effective_label, evaluate
from guard_app.domain.contracts import (
    Denied,
    Outcome,
    OutcomeUnknown,
    Pending,
    TrustedContext,
    Withheld,
)
from guard_app.domain.enums import Decision, Label, RegStatus, RequestStatus, TaskStatus, ToolKind
from guard_app.domain.errors import DispatchError, ResolutionError, StoreError
from guard_app.domain.models import (
    AccessResult,
    PolicyDecision,
    PolicyInput,
    ProposedCall,
    ResolvedRequest,
    TaskState,
)
from guard_app.domain.reasons import Reason
from guard_app.ports import AccessChecker, Registry, Store, Transport
from guard_app.security.digest import json_digest
from guard_app.security.failclosed import after_dispatch, fail_closed
from guard_app.services.approvals import ApprovalService, HumanControl, _ApprovalTaskMismatchError
from guard_app.services.catalog import Catalog
from guard_app.services.response_gate import ResponseGate


@dataclass(frozen=True, slots=True)
class FailureEvent:
    """C의 내구성 감사 연결용 서비스 메타데이터. 본문·인자·예외 원문은 없다."""

    task_id: str
    request_id: str | None
    phase: Literal["before_dispatch", "after_dispatch"]
    reason: Reason
    exception_type: str


@dataclass(frozen=True, slots=True)
class _Allowed:
    request: ResolvedRequest
    state: TaskState


class GuardConfigError(ValueError):
    """고정 문구의 서비스 구성 오류."""


class GuardService:
    """일반 도구 실행의 유일한 공개 입구는 submit이다.

    request_id_factory·approval_id_factory는 C의 ID 공급자이고 next_call_seq는
    기존 Task를 포함해 중복 없는 순번을 공급해야 한다. 기존 Store 초안에 이
    공급 포트가 없어 구성 callback으로 분리했다. 재시작 시 이를 0으로 초기화하면
    안 된다. failure_audit도 C가 내구성 저장을 연결해야 한다.
    """

    def __init__(
        self,
        store: Store,
        registry: Registry,
        catalog: Catalog,
        transport: Transport,
        access_checker: AccessChecker,
        *,
        profile_id: str,
        policy_version: str,
        operator_id: str,
        request_id_factory: Callable[[], str],
        approval_id_factory: Callable[[], str],
        next_call_seq: Callable[[str], Awaitable[int]],
        failure_audit: Callable[[FailureEvent], Awaitable[None]],
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float],
        approval_ttl_seconds: int = 300,
        taint_mode: str = "accumulate",
        max_result_bytes: int = 262144,
        access_timeout_seconds: float = 30.0,
    ) -> None:
        for value in (profile_id, policy_version, operator_id):
            if (
                type(value) is not str
                or not value
                or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
            ):
                raise GuardConfigError("Guard 식별자가 유효하지 않다")
        if taint_mode not in ("accumulate", "last_read", "none"):
            raise GuardConfigError("Guard 추적 모드가 유효하지 않다")
        if (
            type(access_timeout_seconds) not in (int, float)
            or not math.isfinite(access_timeout_seconds)
            or access_timeout_seconds <= 0
        ):
            raise GuardConfigError("접근 검사 제한 시간이 유효하지 않다")
        callbacks = (
            request_id_factory,
            approval_id_factory,
            next_call_seq,
            failure_audit,
            utc_now,
            monotonic,
        )
        if any(not callable(callback) for callback in callbacks):
            raise GuardConfigError("Guard 공급 함수가 유효하지 않다")
        self._store = store
        self._registry = registry
        self._catalog = catalog
        self._transport = transport
        self._access_checker = access_checker
        self._profile_id = profile_id
        self._policy_version = policy_version
        self._operator_id = operator_id
        self._request_id_factory = request_id_factory
        self._next_call_seq = next_call_seq
        self._failure_audit = failure_audit
        self._utc_now = utc_now
        self._monotonic = monotonic
        self._mode = taint_mode
        self._access_timeout = float(access_timeout_seconds)
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_halt = False
        self._gate = ResponseGate(
            store,
            catalog,
            utc_now=utc_now,
            global_halt=self._halt_globally,
            max_result_bytes=max_result_bytes,
        )
        self._approvals = ApprovalService(
            store,
            catalog,
            _ApprovalHost(self),
            approver_id=operator_id,
            approval_id_factory=approval_id_factory,
            utc_now=utc_now,
            monotonic=monotonic,
            ttl_seconds=approval_ttl_seconds,
        )

    def human_control(self, operator_id: str) -> HumanControl:
        """A의 인증된 제어 채널에만 전달한다. 모델 ToolView에 포함하지 않는다."""
        return self._approvals.human_channel(operator_id)

    async def submit(self, task_id: str, call: ProposedCall) -> Outcome:
        """I1·I4·I5: WAITING 중에는 반환하며 사람을 기다리는 lock은 없다."""
        return await fail_closed(
            lambda: self._submit_locked(task_id, call),
            lambda exc: self._pre_dispatch_failure(task_id, exc),
            on_containment_error=self._pre_failure_fallback,
        )

    async def _submit_locked(self, task_id: str, call: ProposedCall) -> Outcome:
        if type(task_id) is not str or not task_id:
            raise ResolutionError(Reason.STATE_MISSING)
        if self._global_halt:
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
        async with self._task_lock(task_id):
            decided = await fail_closed(
                lambda: self._decide(task_id, call),
                lambda exc: self._pre_dispatch_failure(task_id, exc),
                on_containment_error=self._pre_failure_fallback,
            )
            if not isinstance(decided, _Allowed):
                return decided
            return await self._dispatch_reserved(decided.request, decided.state)

    def _task_lock(self, task_id: str) -> asyncio.Lock:
        return self._locks.setdefault(task_id, asyncio.Lock())

    def _policy_matches(self, state: TaskState) -> bool:
        return (
            not self._global_halt
            and state.profile_id == self._profile_id
            and state.policy_version == self._policy_version
        )

    async def _decide(self, task_id: str, call: ProposedCall) -> _Allowed | Denied | Pending:
        if self._global_halt:
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
        state = TaskState.model_validate(await self._store.load_task(task_id))
        if state.task_id != task_id:
            raise StoreError(Reason.STATE_MISSING)
        if not self._policy_matches(state):
            await self._halt_task(task_id, Reason.POLICY_CHANGED)
            await self._approvals.invalidate_task(task_id, Reason.POLICY_CHANGED)
            return Denied(request_id=None, reasons=(Reason.POLICY_CHANGED,))
        if state.unclassified:
            return Denied(request_id=None, reasons=(Reason.TASK_UNCLASSIFIED,))
        if state.status is not TaskStatus.RUNNING:
            return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))
        call = ProposedCall.model_validate(call)
        req = self._catalog.resolve(
            state,
            call,
            request_id=self._request_id_factory(),
            call_seq=await self._next_call_seq(task_id),
        )
        decision = await self._evaluate_request(req, state, phase="submit")
        await self._store.record_decision(
            req,
            decision,
            arguments_digest=json_digest(req.arguments.to_dict()),
            at_utc=self._utc_now(),
        )
        if decision.decision is Decision.DENY:
            return Denied(request_id=req.request_id, reasons=decision.reasons)
        if decision.decision is Decision.ASK:
            appr = await self._approvals.open(req, state, decision)
            return Pending(
                request_id=req.request_id,
                approval_id=appr.approval_id,
                reasons=decision.reasons,
            )
        await self._store.reserve_dispatch(req.request_id, now_utc=self._utc_now())
        return _Allowed(req, state)

    async def _evaluate_request(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        phase: Literal["submit", "approval_recheck"],
    ) -> PolicyDecision:
        if self._global_halt:
            raise ResolutionError(Reason.INTERNAL_ERROR)
        if not self._policy_matches(state):
            raise ResolutionError(Reason.POLICY_CHANGED)
        registration_status = self._registry.status(req.tool.server_id)
        fingerprint = self._registry.fingerprint(req.tool)
        # AGT에 원문을 전달하기 전 로컬 전제조건과 경계 거부를 판정한다.
        if fingerprint is None:
            raise ResolutionError(Reason.TOOL_FINGERPRINT_CHANGED)
        preliminary = evaluate(
            PolicyInput(
                request=req,
                state=state,
                effective_label=effective_label(state, self._mode),
                registration_status=registration_status or RegStatus.PENDING,
                registered_fingerprint=fingerprint,
                access=AccessResult(allowed=True, reason="", error=False),
                policy_version=self._policy_version,
                phase=phase,
            )
        )
        if preliminary.decision is Decision.DENY:
            return preliminary
        try:
            async with asyncio.timeout(self._access_timeout):
                access = await self._access_checker.check_access(
                    req, TrustedContext(state=state, operator_id=self._operator_id)
                )
        except TimeoutError:
            access = AccessResult(allowed=False, reason="", error=True)
        # await 중 등록 상태나 지문이 바뀌어도 현재 값을 다시 검사한다.
        fingerprint = self._registry.fingerprint(req.tool)
        if fingerprint is None:
            raise ResolutionError(Reason.TOOL_FINGERPRINT_CHANGED)
        return evaluate(
            PolicyInput(
                request=req,
                state=state,
                effective_label=effective_label(state, self._mode),
                registration_status=self._registry.status(req.tool.server_id) or RegStatus.PENDING,
                registered_fingerprint=fingerprint,
                access=access,
                policy_version=self._policy_version,
                phase=phase,
            )
        )

    async def _dispatch_reserved(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        expires_at_utc: datetime | None = None,
        monotonic_deadline: float | None = None,
    ) -> Outcome:
        # 예약 후 결과를 예약 전 래퍼로 되돌리지 않는다.
        return await after_dispatch(
            lambda: self._dispatch_and_commit(
                req, state, expires_at_utc=expires_at_utc, monotonic_deadline=monotonic_deadline
            ),
            lambda exc: self._contain_post_dispatch(req, exc),
            on_containment_error=lambda exc: self._post_failure_fallback(req, exc),
        )

    async def _dispatch_and_commit(
        self,
        req: ResolvedRequest,
        expected: TaskState,
        *,
        expires_at_utc: datetime | None,
        monotonic_deadline: float | None,
    ) -> Outcome:
        state = TaskState.model_validate(await self._store.load_task(req.task_id))
        allowed_versions = {expected.security_version}
        if expected.status is TaskStatus.WAITING_APPROVAL:
            # C 계약 초안은 소비 시 security_version의 증가 여부가 미합의다.
            # 양쪽 전이를 허용하되 사본의 보안 맥락은 그대로여야 한다.
            allowed_versions.add(expected.security_version + 1)
        if (
            not self._policy_matches(state)
            or state.status is not TaskStatus.RUNNING
            or state.unclassified
            or state.epoch != expected.epoch
            or state.security_version not in allowed_versions
            or state.high_watermark != expected.high_watermark
            or state.escalations != expected.escalations
            or effective_label(state, self._mode) != effective_label(expected, self._mode)
        ):
            raise ResolutionError(Reason.TASK_NOT_RUNNING)
        current = self._catalog.resolve(
            state,
            ProposedCall(
                tool_ref=f"{req.tool.server_id}__{req.tool.tool_name}",
                arguments=req.arguments,
                model_call_id=req.request_id,
            ),
            request_id=req.request_id,
            call_seq=req.call_seq,
        )
        if current != req:
            raise ResolutionError(Reason.TOOL_FINGERPRINT_CHANGED)
        # 소비 ACK 이전의 대기 시간도 만료에 포함한다. 예약 후라 Denied로 바꾸지 않는다.
        if expires_at_utc is not None or monotonic_deadline is not None:
            now_mono = self._monotonic()
            if (
                expires_at_utc is None
                or monotonic_deadline is None
                or type(now_mono) not in (int, float)
                or not math.isfinite(now_mono)
                or now_mono < 0
                or now_mono >= monotonic_deadline
                or self._utc_now() >= expires_at_utc
            ):
                raise ResolutionError(Reason.APPROVAL_STALE)
        raw = await self._transport.call(req)
        return await self._gate.handle(req, raw, expected_state=state)

    async def _halt_task(
        self, task_id: str, reason: Reason, *, expected_epoch: int | None = None
    ) -> TaskState:
        state = TaskState.model_validate(await self._store.load_task(task_id))
        if state.task_id != task_id:
            if expected_epoch is not None:
                raise _ApprovalTaskMismatchError()
            raise StoreError(Reason.STATE_MISSING)
        if expected_epoch is not None and state.epoch != expected_epoch:
            raise _ApprovalTaskMismatchError()
        if state.status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED):
            return state
        halted = TaskState.model_validate(
            await self._store.set_task_status(
                task_id,
                TaskStatus.HALTED,
                expected_row_revision=state.row_revision,
                at_utc=self._utc_now(),
                reason=reason,
            )
        )
        if (
            halted.task_id != state.task_id
            or halted.epoch != state.epoch
            or halted.status is not TaskStatus.HALTED
            or halted.high_watermark < state.high_watermark
            or (state.unclassified and not halted.unclassified)
            or halted.security_version < state.security_version
        ):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        return halted

    def _halt_globally(self) -> None:
        self._global_halt = True

    async def _pre_dispatch_failure(self, task_id: str, exc: BaseException) -> Denied:
        if isinstance(exc, (StoreError, asyncio.CancelledError)):
            self._halt_globally()
        reason = (
            exc.reason if isinstance(exc, (ResolutionError, StoreError)) else Reason.INTERNAL_ERROR
        )
        await self._failure_audit(
            FailureEvent(task_id, None, "before_dispatch", reason, _exception_type(exc))
        )
        return Denied(request_id=None, reasons=(reason,))

    def _pre_failure_fallback(self, exc: BaseException) -> Denied:
        self._halt_globally()
        return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))

    async def _contain_post_dispatch(self, req: ResolvedRequest, exc: BaseException) -> Outcome:
        await self._failure_audit(
            FailureEvent(
                req.task_id,
                req.request_id,
                "after_dispatch",
                Reason.POST_DISPATCH_ERROR,
                _exception_type(exc),
            )
        )
        is_send = req.tool.kind is ToolKind.SEND
        contained = TaskState.model_validate(
            await self._store.commit_request_failure(
                req,
                request_status=RequestStatus.UNKNOWN_OUTCOME if is_send else RequestStatus.WITHHELD,
                task_status=TaskStatus.UNKNOWN_OUTCOME if is_send else TaskStatus.HALTED,
                reason=Reason.POST_DISPATCH_ERROR,
                at_utc=self._utc_now(),
                exception_type=_exception_type(exc),
            )
        )
        if (
            contained.task_id != req.task_id
            or contained.epoch != req.epoch
            or contained.status
            is not (TaskStatus.UNKNOWN_OUTCOME if is_send else TaskStatus.HALTED)
        ):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        if is_send:
            return OutcomeUnknown(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))
        return Withheld(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))

    def _post_failure_fallback(self, req: ResolvedRequest, exc: BaseException) -> Outcome:
        self._halt_globally()
        if req.tool.kind is ToolKind.SEND:
            return OutcomeUnknown(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))
        return Withheld(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))


class _ApprovalHost:
    """승인 서비스에만 제공하는 Guard 실행 권한. 앱의 일반 실행 API가 아니다."""

    def __init__(self, guard: GuardService) -> None:
        self._guard = guard

    def task_lock(self, task_id: str) -> asyncio.Lock:
        return self._guard._task_lock(task_id)

    def policy_matches(self, state: TaskState) -> bool:
        return self._guard._policy_matches(state)

    def effective_task_label(self, state: TaskState) -> Label:
        return effective_label(state, self._guard._mode)

    async def evaluate_request(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        phase: Literal["submit", "approval_recheck"],
    ) -> PolicyDecision:
        return await self._guard._evaluate_request(req, state, phase=phase)

    async def dispatch_reserved(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        expires_at_utc: datetime | None = None,
        monotonic_deadline: float | None = None,
    ) -> Outcome:
        return await self._guard._dispatch_reserved(
            req, state, expires_at_utc=expires_at_utc, monotonic_deadline=monotonic_deadline
        )

    async def halt_task(
        self, task_id: str, reason: Reason, *, expected_epoch: int | None = None
    ) -> TaskState:
        return await self._guard._halt_task(task_id, reason, expected_epoch=expected_epoch)

    def global_halt(self) -> None:
        self._guard._halt_globally()

    async def pre_dispatch_failure(self, task_id: str, exc: BaseException) -> Denied:
        return await self._guard._pre_dispatch_failure(task_id, exc)


def _exception_type(exc: BaseException) -> str:
    name = exc.exception_type if isinstance(exc, DispatchError) else type(exc).__name__
    if name.isascii() and name.isidentifier() and len(name) <= 64:
        return name
    return "Exception"
