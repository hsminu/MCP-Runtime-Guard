"""8장: 메모리 사본, 사람 전용 제어, 재평가와 원자적 승인 소비.

Store의 실제 원자성은 B6 구현·C 독립 계약 검증에 의존한다. 이 서비스는 SQL이나
도구 전송을 구현하지 않으며 같은 Guard Task lock으로 예약과 공개를 연결한다.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import ValidationError

from guard_app.core.approval_time import approval_event_time
from guard_app.core.snapshot import ApprovalSnapshot, make_snapshot
from guard_app.domain.contracts import (
    ApprovalRecord,
    Denied,
    Outcome,
    OutcomeUnknown,
    PendingFinal,
    PendingFinalCause,
    Released,
    Withheld,
)
from guard_app.domain.enums import ApprovalStatus, Decision, Label, SystemStopReason, TaskStatus
from guard_app.domain.errors import ResolutionError, StoreError
from guard_app.domain.models import PolicyDecision, ProposedCall, ResolvedRequest, TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.security.digest import canonical_json, json_digest
from guard_app.security.failclosed import fail_closed
from guard_app.services.catalog import Catalog
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.state_checks import checked_status_change


class ApprovalConfigError(ValueError):
    """설정 원문을 공개하지 않는 승인 설정 오류."""


class _ApprovalTaskMismatchError(StoreError):
    """승인 사본과 연결되지 않은 상태로 Task 행을 변경하지 않는다."""


class _ApprovalRecordMismatchError(StoreError):
    """조회·전이 대상과 연결되지 않은 승인 행을 소비하거나 변경하지 않는다."""


class ApprovalHost(Protocol):
    """서비스 내부 협력 인터페이스. 공통 ports 계약을 확장하지 않는다."""

    def task_lock(self, task_id: str) -> asyncio.Lock: ...

    def request_stop(self, task_id: str) -> None: ...

    def check_stop(self, task_id: str) -> None: ...

    def policy_matches(self, state: TaskState) -> bool: ...

    def effective_task_label(self, state: TaskState) -> Label: ...

    async def evaluate_request(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        phase: Literal["submit", "approval_recheck"],
    ) -> PolicyDecision: ...

    async def dispatch_reserved(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        expires_at_utc: datetime | None = None,
        monotonic_deadline: float | None = None,
        on_cancelled: Callable[[Outcome], None] | None = None,
    ) -> Outcome: ...

    async def halt_task(
        self, task_id: str, reason: Reason, *, expected_epoch: int | None = None
    ) -> TaskState: ...

    def global_halt(self) -> None: ...

    async def pre_dispatch_failure(self, task_id: str, exc: BaseException) -> Denied: ...


@dataclass(frozen=True, slots=True)
class _Reserved:
    request: ResolvedRequest
    state: TaskState
    expires_at_utc: datetime
    monotonic_deadline: float


@dataclass(frozen=True, slots=True)
class _ClosingApproval:
    """원문 없이 종결 중 승인의 Task lock 소유 관계만 유지한다."""

    task_id: str
    epoch: int


_LIVE = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
_CONFIG_REASONS = (
    Reason.POLICY_CHANGED,
    Reason.TOOL_FINGERPRINT_CHANGED,
    Reason.REG_NOT_ACTIVE,
    Reason.UNKNOWN_SERVER,
    Reason.TOOL_UNSUPPORTED,
)


class PendingResultError(ValueError):
    """모르는 요청·중복 소비·종료 시 원문 폐기의 고정 오류."""


class PendingResultHandle:
    """NOTIFY-01 단일 소비 핸들. 대기 취소는 공유 결과·승인·타이머와 무관하다.

    완료가 먼저면 wait까지 보관한다. 소비 직후 서비스와 핸들 모두 본문 참조를
    놓는다. Task 정지·앱 종료 시 미소비 Released는 폐기하며 wait는 고정 오류를
    낸다. 이미 대기 중이면 수신을 보장하되 그 대기마저 취소되면 폐기한다.
    """

    def __init__(self, consumed: Callable[[], None]) -> None:
        self._ready = asyncio.Event()
        self._result: PendingFinal | None = None
        self._waiting = False
        self._completed = False
        self._consumed = False
        self._discard_requested = False
        self._consumed_callback = consumed

    def __repr__(self) -> str:
        return "PendingResultHandle(<redacted>)"

    async def wait(self) -> PendingFinal:
        if self._waiting or self._consumed:
            raise PendingResultError("최종 결과는 한 번만 소비할 수 있다")
        self._waiting = True
        try:
            await self._ready.wait()
            result = self._result
            if result is None:
                raise PendingResultError("종료된 최종 결과의 원문은 폐기되었다")
            self._result = None
            self._consumed = True
            self._consumed_callback()
            return result
        finally:
            self._waiting = False
            if self._discard_requested:
                self._discard_released()

    def _complete(self, result: PendingFinal) -> bool:
        if self._completed:
            return False
        self._result = result
        self._completed = True
        self._ready.set()
        return True

    def _discard_released(self) -> None:
        if self._result is not None and isinstance(self._result.outcome, Released):
            if self._waiting:
                self._discard_requested = True
                return
            self._result = None
            self._consumed = True
            self._consumed_callback()
            self._ready.set()


@dataclass(frozen=True, slots=True)
class _OpeningOwner:
    task_id: str
    epoch: int
    request_id: str
    server_id: str
    tool_name: str


@dataclass(frozen=True, slots=True)
class _PendingOwner(_OpeningOwner):
    approval_id: str
    handle: PendingResultHandle


class ApprovalService:
    """운영자 사본은 메모리에만 둔다. 소비 이후에는 반드시 사본을 제거한다.

    open/invalidate_task는 이미 Guard Task lock을 잡은 호출자가 사용한다.
    HumanControl은 승인 대기 동안 lock을 유지하지 않고, 사람 명령 처리 시에만
    같은 lock을 잡아 재평가부터 응답 커밋까지 보호한다.
    """

    def __init__(
        self,
        store: Store,
        catalog: Catalog,
        host: ApprovalHost,
        *,
        approver_id: str,
        approval_id_factory: Callable[[], str],
        utc_now: Callable[[], datetime],
        monotonic: Callable[[], float],
        ttl_seconds: int = 300,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if (
            type(approver_id) is not str
            or not approver_id
            or any(0xD800 <= ord(char) <= 0xDFFF for char in approver_id)
        ):
            raise ApprovalConfigError("사전 등록 승인자가 올바르지 않다")
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ApprovalConfigError("승인 유효 시간은 양의 정수여야 한다")
        if not all(callable(value) for value in (approval_id_factory, utc_now, monotonic, sleep)):
            raise ApprovalConfigError("승인 ID·시간 공급자가 올바르지 않다")
        self._store = store
        self._catalog = catalog
        self._host = host
        self._approver_id = approver_id
        self._approval_id_factory = approval_id_factory
        self._utc_now = utc_now
        self._monotonic = monotonic
        self._ttl_seconds = ttl_seconds
        self._sleep = sleep
        self._memory_snapshots: dict[str, ApprovalSnapshot] = {}
        self._closing_approvals: dict[str, _ClosingApproval] = {}
        self._inflight_tasks: dict[str, str] = {}
        self._channel_key = object()
        self._pending: dict[str, _PendingOwner] = {}
        self._opening: dict[str, _OpeningOwner] = {}
        self._results: dict[str, PendingResultHandle] = {}
        self._seen_requests: set[str] = set()
        self._causes: dict[str, PendingFinalCause] = {}
        self._expiry_tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False
        self._policy_invalidations = 0

    def pending_result(self, request_id: str) -> PendingResultHandle:
        """Pending 반환 전에 등록한 핸들. 완료 후 늦은 조회도 한 번 소비할 수 있다."""
        if type(request_id) is not str or request_id not in self._results:
            raise PendingResultError("대기 가능한 최종 결과가 없다")
        return self._results[request_id]

    def _replace_policy(self, catalog: Catalog, ttl_seconds: int) -> None:
        """Guard가 Task 정리와 판정 대기 장벽 안에서만 호출한다. await하지 않는다."""
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ApprovalConfigError("승인 유효 시간은 양의 정수여야 한다")
        self._catalog = catalog
        self._ttl_seconds = ttl_seconds

    def _policy_pending_task_ids(self) -> set[str]:
        return {
            owner.task_id for owner in self._pending.values() if not owner.handle._completed
        } | {owner.task_id for owner in self._opening.values()}

    async def _invalidate_policy_task(self, task_id: str) -> None:
        """Task lock 안에서 승인 종결을 저장하고 Task 정지 ACK까지 알림을 미룬다."""
        await self.invalidate_task(
            task_id, Reason.POLICY_CHANGED, cause="invalidated", notify=False
        )

    def _fail_policy_change(self) -> None:
        """global halt 뒤 유휴·종결 중 핸들은 고정 실패로 깨운다. 실행 결과는 보존한다."""
        for approval_id, owner in tuple(self._pending.items()):
            if (
                not owner.handle._completed
                and approval_id not in self._inflight_tasks
                and not self._host.task_lock(owner.task_id).locked()
            ):
                self._discard_snapshot(approval_id)
                self._complete(
                    approval_id,
                    Denied(request_id=owner.request_id, reasons=(Reason.INTERNAL_ERROR,)),
                    "failed",
                )

    def human_channel(self, operator_id: str) -> HumanControl:
        """A의 신뢰 UI 제어 큐에만 전달할 capability. 모델 제안으로 만들지 않는다."""
        if type(operator_id) is not str or operator_id != self._approver_id:
            raise ApprovalConfigError("사전 등록 운영자만 승인 제어 채널을 받을 수 있다")
        return HumanControl(self, operator_id, self._channel_key)

    def _fail_system_stop(self, task_id: str, epoch: int) -> None:
        """실패한 제어를 성공 무효화로 알리지 않는다. 예약 실행은 자체 경계가 확정한다."""
        for approval_id, owner in tuple(self._pending.items()):
            if (
                owner.task_id == task_id
                and owner.epoch == epoch
                and not owner.handle._completed
                and approval_id not in self._inflight_tasks
                and not self._host.task_lock(task_id).locked()
            ):
                self._discard_snapshot(approval_id)
                self._complete(
                    approval_id,
                    Denied(request_id=owner.request_id, reasons=(Reason.INTERNAL_ERROR,)),
                    "failed",
                )

    async def open(
        self, req: ResolvedRequest, state: TaskState, decision: PolicyDecision
    ) -> ApprovalRecord:
        """Task lock 안에서 생성 중 관계까지 종료·지문 변경 입구와 결속한다."""
        req = ResolvedRequest.model_validate(req)
        if self._closed or req.request_id in self._opening:
            raise StoreError(Reason.APPROVAL_RACE)
        self._opening[req.request_id] = _OpeningOwner(
            req.task_id, req.epoch, req.request_id, req.tool.server_id, req.tool.tool_name
        )
        try:
            return await self._open(req, state, decision)
        finally:
            self._opening.pop(req.request_id, None)

    async def _open(
        self, req: ResolvedRequest, state: TaskState, decision: PolicyDecision
    ) -> ApprovalRecord:
        """WAITING 반환 버전을 사본에 고정한다. 중간 저장 실패는 예약 전 정지다.

        B1의 Task 전이·승인 생성은 별도 Store 메서드다. B6 구현에서 둘의
        원자 묶음은 합의 대상이며, 여기서는 실패를 정상 Pending으로 숨기지 않는다.
        """
        req = ResolvedRequest.model_validate(req)
        state = TaskState.model_validate(state)
        decision = PolicyDecision.model_validate(decision)
        if self._closed or req.request_id in self._seen_requests:
            raise StoreError(Reason.APPROVAL_RACE)
        if (
            decision.decision is not Decision.ASK
            or state.status is not TaskStatus.RUNNING
            or state.unclassified
            or decision.policy_version != state.policy_version
            or decision.security_version != state.security_version
            or req.task_id != state.task_id
            or req.epoch != state.epoch
            or not self._host.policy_matches(state)
        ):
            raise StoreError(Reason.APPROVAL_STALE)
        now, mono = self._times()
        waiting = await self._store.set_task_status(
            state.task_id,
            TaskStatus.WAITING_APPROVAL,
            expected_row_revision=state.row_revision,
            at_utc=now,
            reason=Reason.INTERNAL_TO_EXTERNAL,
        )
        if (
            waiting.status is not TaskStatus.WAITING_APPROVAL
            or self._security_context(waiting) != self._security_context(state)
            or self._host.effective_task_label(waiting) != self._host.effective_task_label(state)
            or waiting.security_version < state.security_version
        ):
            raise StoreError(Reason.APPROVAL_STALE)
        waiting_decision = decision.model_copy(
            update={"security_version": waiting.security_version}
        )
        snapshot = make_snapshot(
            req,
            waiting,
            waiting_decision,
            effective_label=self._host.effective_task_label(waiting),
            approver_id=self._approver_id,
            expires_at_utc=now + timedelta(seconds=self._ttl_seconds),
            monotonic_deadline=mono + self._ttl_seconds,
            hash_json=json_digest,
            canonicalize=canonical_json,
        )
        approval = ApprovalRecord(
            approval_id=self._approval_id_factory(),
            request_id=req.request_id,
            snapshot_hash=snapshot.hash,
            approver_id=self._approver_id,
            status=ApprovalStatus.PENDING,
            created_at_utc=now,
            expires_at_utc=snapshot.expires_at_utc,
        )
        if self._approval_task_id(approval.approval_id) is not None:
            raise StoreError(Reason.APPROVAL_RACE)
        await self._store.create_approval(approval)
        self._memory_snapshots[approval.approval_id] = snapshot
        self._register_pending(approval, snapshot)
        self._expiry_tasks[approval.approval_id] = asyncio.create_task(
            self._run_expiry(approval.approval_id)
        )
        return approval

    async def invalidate_task(
        self,
        task_id: str,
        reason: Reason,
        *,
        cause: PendingFinalCause | None = None,
        notify: bool = True,
    ) -> None:
        """호출자가 이미 잡은 lock 안에서 원문 사본과 살아 있는 승인을 폐기한다."""
        approval_ids = tuple(
            dict.fromkeys(
                [
                    approval_id
                    for approval_id, snap in self._memory_snapshots.items()
                    if snap.task_id == task_id
                ]
                + [
                    approval_id
                    for approval_id, owner in self._closing_approvals.items()
                    if owner.task_id == task_id
                ]
            )
        )
        for approval_id in approval_ids:
            self._discard_snapshot(approval_id)
        for approval_id in approval_ids:
            record = await self._load_approval(approval_id)
            if record is not None and record.status in _LIVE:
                await self._transition(record, self._invalid_status(record), reason)
            self._closing_approvals.pop(approval_id, None)
            self._causes[approval_id] = cause or self._invalidation_cause(reason)
            if notify:
                self._complete(approval_id, self._denied(record, reason))

    def _publish_task_invalidations(self, task_id: str, reason: Reason) -> None:
        for approval_id, owner in tuple(self._pending.items()):
            if (
                owner.task_id == task_id
                and approval_id not in self._memory_snapshots
                and approval_id not in self._inflight_tasks
            ):
                self._complete(approval_id, Denied(request_id=owner.request_id, reasons=(reason,)))

    def _discard_task_released(self, task_id: str, epoch: int) -> None:
        """완료된 미소비 본문만 폐기한다. 다른 Task·생애와 본문 없는 결과는 보존한다."""
        for owner in tuple(self._pending.values()):
            if owner.task_id == task_id and owner.epoch == epoch:
                owner.handle._discard_released()

    def _register_pending(self, record: ApprovalRecord, snapshot: ApprovalSnapshot) -> None:
        def consumed() -> None:
            self._results.pop(record.request_id, None)
            self._pending.pop(record.approval_id, None)
            self._causes.pop(record.approval_id, None)

        handle = PendingResultHandle(consumed)
        self._pending[record.approval_id] = _PendingOwner(
            task_id=snapshot.task_id,
            epoch=snapshot.request.epoch,
            request_id=record.request_id,
            approval_id=record.approval_id,
            server_id=snapshot.request.tool.server_id,
            tool_name=snapshot.request.tool.tool_name,
            handle=handle,
        )
        self._results[record.request_id] = handle
        self._seen_requests.add(record.request_id)

    def _complete(
        self, approval_id: str, outcome: Outcome, cause: PendingFinalCause | None = None
    ) -> None:
        owner = self._pending.get(approval_id)
        if owner is None or owner.handle._completed:
            self._causes.pop(approval_id, None)
            return
        if not isinstance(outcome, (Denied, Released, Withheld, OutcomeUnknown)):
            self._host.global_halt()
            outcome = Denied(request_id=owner.request_id, reasons=(Reason.INTERNAL_ERROR,))
            cause = "failed"
        elif isinstance(outcome, Denied) and outcome.request_id is None:
            outcome = outcome.model_copy(update={"request_id": owner.request_id})
        elif outcome.request_id != owner.request_id:
            self._host.global_halt()
            outcome = Denied(request_id=owner.request_id, reasons=(Reason.INTERNAL_ERROR,))
            cause = "failed"
        selected_cause = cause or self._causes.get(approval_id, "failed")
        if not isinstance(outcome, Denied):
            selected_cause = "executed"
        if (
            selected_cause == "invalidated"
            and isinstance(outcome, Denied)
            and Reason.POLICY_CHANGED in outcome.reasons
        ):
            self._policy_invalidations += 1
        owner.handle._complete(
            PendingFinal(
                task_id=owner.task_id,
                epoch=owner.epoch,
                request_id=owner.request_id,
                approval_id=owner.approval_id,
                cause=selected_cause,
                outcome=outcome,
            )
        )
        self._causes.pop(approval_id, None)

    @staticmethod
    def _invalidation_cause(reason: Reason) -> PendingFinalCause:
        if reason is Reason.TASK_NOT_RUNNING:
            return "cancelled"
        return "failed" if reason is Reason.INTERNAL_ERROR else "invalidated"

    def _cancel_expiry(self, approval_id: str) -> None:
        timer = self._expiry_tasks.pop(approval_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _expiry_delay(self, approval_id: str) -> float | None:
        # 원문 사본을 timer coroutine의 await 프레임에 보관하지 않는다.
        snapshot = self._memory_snapshots.get(approval_id)
        if snapshot is None:
            return None
        now, mono = self._times()
        return min(
            (snapshot.expires_at_utc - now).total_seconds(), snapshot.monotonic_deadline - mono
        )

    async def _run_expiry(self, approval_id: str) -> None:
        async def operation() -> None:
            while (delay := self._expiry_delay(approval_id)) is not None:
                if delay <= 0:
                    await self._expire_system(approval_id)
                    # lock 대기 중 UTC가 돌아오면 미확정 승인 감시를 계속한다.
                    continue
                # UTC 순방향 변경도 최대 1초 안에 재검사한다. monotonic은 연장하지 않는다.
                await self._sleep(min(delay, 1.0))

        async def on_error(exc: BaseException) -> None:
            if isinstance(exc, asyncio.CancelledError):
                if self._expiry_tasks.get(approval_id) is asyncio.current_task():
                    # 명시적 aclose 없이 loop가 끝난 경우도 원문·미완료 핸들을 남기지 않는다.
                    self._host.global_halt()
                    self._discard_snapshot(approval_id)
                    self._complete(
                        approval_id,
                        Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,)),
                        "failed",
                    )
                return
            owner = self._pending.get(approval_id)
            if owner is None:
                return
            lock = await self._acquire_task_lock(owner.task_id)
            if isinstance(lock, Denied):
                return
            try:

                async def failed() -> None:
                    raise exc

                await self._pre_boundary(failed, approval_id=approval_id, task_id=owner.task_id)
            finally:
                lock.release()

        def fallback(exc: BaseException) -> None:
            self._host.global_halt()
            self._discard_snapshot(approval_id)
            self._complete(
                approval_id, Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,)), "failed"
            )

        try:
            await fail_closed(operation, on_error=on_error, on_containment_error=fallback)
        finally:
            if self._expiry_tasks.get(approval_id) is asyncio.current_task():
                self._expiry_tasks.pop(approval_id, None)

    async def _expire_system(self, approval_id: str) -> None:
        """타이머 전용 경로. 사람 capability·operator 입력을 받거나 호출하지 않는다."""
        task_id = self._approval_task_id(approval_id)
        if task_id is None:
            return
        # 의도적인 timer.cancel은 바깥 _run_expiry 경계가 식별한다. 사람 명령의
        # lock 경계를 쓰면 정상 타이머 취소까지 전역 정지로 바뀌므로 직접 기다린다.
        lock = self._host.task_lock(task_id)
        await lock.acquire()

        async def operation() -> Denied:
            snapshot = self._memory_snapshots.get(approval_id)
            record = await self._load_approval(approval_id)
            if snapshot is None or record is None or record.status not in _LIVE:
                self._discard_snapshot(approval_id)
                return self._denied(record, Reason.APPROVAL_RACE)
            if (
                record.request_id != snapshot.request.request_id
                or record.snapshot_hash != snapshot.hash
                or record.approver_id != snapshot.approver_id
                or record.expires_at_utc != snapshot.expires_at_utc
            ):
                raise _ApprovalRecordMismatchError()
            if not self._expired(snapshot):
                return self._denied(record, Reason.APPROVAL_RACE)
            await self._finish(
                record, snapshot, ApprovalStatus.EXPIRED, Reason.APPROVAL_STALE, resume=True
            )
            return self._denied(record, Reason.APPROVAL_STALE)

        try:
            await self._pre_boundary(operation, approval_id=approval_id, task_id=task_id)
        finally:
            lock.release()

    async def invalidate_tool(self, server_id: str, tool_name: str) -> int:
        """C가 지문 변경 시 호출할 신뢰 입구. 소비 후 실행은 Pending으로 되돌리지 않는다."""
        try:
            SourceRule(server_id=server_id, tool_name=tool_name, prefix=(), label=Label.PUBLIC)
        except ValidationError:
            raise ApprovalConfigError("지문 무효화 대상이 유효하지 않다") from None
        await self._wait_openings(
            tuple(
                owner
                for owner in self._opening.values()
                if owner.server_id == server_id and owner.tool_name == tool_name
            )
        )
        owners = tuple(
            owner
            for owner in self._pending.values()
            if owner.server_id == server_id
            and owner.tool_name == tool_name
            and not owner.handle._completed
            and owner.approval_id not in self._inflight_tasks
        )
        for owner in owners:
            self._host.request_stop(owner.task_id)
        for owner in owners:
            await self._stop_pending_owner(owner, Reason.TOOL_FINGERPRINT_CHANGED, "invalidated")
        return sum(owner.handle._completed for owner in owners)

    async def _stop_pending_owner(
        self,
        owner: _PendingOwner,
        reason: Reason,
        cause: PendingFinalCause,
        *,
        halt_completed: bool = False,
    ) -> None:
        lock = await self._acquire_task_lock(owner.task_id)
        if isinstance(lock, Denied):
            return

        async def operation() -> None:
            if owner.handle._completed and not halt_completed:
                return
            if not owner.handle._completed:
                try:
                    state = TaskState.model_validate(await self._store.load_task(owner.task_id))
                except ValidationError:
                    raise _ApprovalTaskMismatchError() from None
                if state.task_id != owner.task_id or state.epoch != owner.epoch:
                    raise _ApprovalTaskMismatchError()
                await self.invalidate_task(owner.task_id, reason, cause=cause, notify=False)
            await self._host.halt_task(owner.task_id, reason, expected_epoch=owner.epoch)
            if owner.handle._completed:
                return
            self._publish_task_invalidations(owner.task_id, reason)

        try:
            await self._pre_boundary(
                operation, approval_id=owner.approval_id, task_id=owner.task_id
            )
        finally:
            lock.release()

    async def cancel_pending(self, task_id: str, *, expected_epoch: int) -> int:
        """세션 관리자 종료용. 일치하는 epoch의 Pending 관계가 있을 때만 정지한다."""
        if (
            type(task_id) is not str
            or not task_id
            or any(0xD800 <= ord(char) <= 0xDFFF for char in task_id)
            or type(expected_epoch) is not int
            or expected_epoch < 0
        ):
            raise ApprovalConfigError("취소할 Task 식별자가 유효하지 않다")
        await self._wait_openings(
            tuple(
                owner
                for owner in self._opening.values()
                if owner.task_id == task_id and owner.epoch == expected_epoch
            )
        )
        self._discard_task_released(task_id, expected_epoch)
        owners = tuple(
            owner
            for owner in self._pending.values()
            if owner.task_id == task_id
            and owner.epoch == expected_epoch
            and not owner.handle._completed
        )
        if owners:
            self._host.request_stop(task_id)
        for owner in owners:
            await self._stop_pending_owner(
                owner, Reason.TASK_NOT_RUNNING, "cancelled", halt_completed=True
            )
        self._discard_task_released(task_id, expected_epoch)
        return sum(owner.handle._completed for owner in owners)

    async def _wait_openings(self, owners: tuple[_OpeningOwner, ...]) -> None:
        for owner in owners:
            self._host.request_stop(owner.task_id)
        for owner in owners:
            lock = await self._acquire_task_lock(owner.task_id)
            if not isinstance(lock, Denied):
                lock.release()

    async def aclose(self) -> None:
        """신규 승인 거부·대기 승인 취소·타이머 종료·미소비 공개 본문 폐기.

        예약 후 실행의 Task lock은 결과 확정까지 기다린다. A는 transport 종료를
        함께 보장해야 하며, 종료 중 결과를 모델에 보내지 않고 epoch와 결속해 버린다.
        """
        self._closed = True
        await self._wait_openings(tuple(self._opening.values()))
        owners = tuple(self._pending.values())
        timers = tuple(self._expiry_tasks.values())
        for owner in owners:
            if not owner.handle._completed:
                self._host.request_stop(owner.task_id)
        for owner in owners:
            if not owner.handle._completed:
                await self._stop_pending_owner(
                    owner, Reason.TASK_NOT_RUNNING, "shutdown", halt_completed=True
                )
        for approval_id in tuple(self._expiry_tasks):
            self._cancel_expiry(approval_id)
        if timers:
            results = await asyncio.gather(*timers, return_exceptions=True)
            if any(
                isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
                for result in results
            ):
                self._host.global_halt()
        for owner in owners:
            owner.handle._discard_released()

    async def _review(self, approval_id: str, operator_id: str) -> ApprovalSnapshot | None:
        """살아 있는 승인 사본을 사람 채널에만 읽기 전용으로 전달한다.

        실패한 조회는 상태를 쓰거나 원문을 재구성하지 않는다. 마지막 await 뒤
        두 시계와 메모리 사본을 재확인한 뒤 동일한 불변 사본만 반환한다.
        """
        if operator_id != self._approver_id or type(approval_id) is not str or not approval_id:
            return None
        snapshot = self._memory_snapshots.get(approval_id)
        if snapshot is None:
            return None

        async def operation() -> ApprovalSnapshot | None:
            async with self._host.task_lock(snapshot.task_id):
                record = await self._load_approval(approval_id)
                if record is None:
                    return None
                if (
                    self._memory_snapshots.get(approval_id) is not snapshot
                    or record.status is not ApprovalStatus.PENDING
                    or record.approver_id != operator_id
                    or snapshot.approver_id != operator_id
                    or record.snapshot_hash != snapshot.hash
                    or record.request_id != snapshot.request.request_id
                    or record.expires_at_utc != snapshot.expires_at_utc
                    or self._expired(snapshot)
                ):
                    return None
                state = await self._load_snapshot_task(snapshot)
                self._host.check_stop(snapshot.task_id)
                if not self._host.policy_matches(state) or not self._task_matches(snapshot, state):
                    return None
                tool = snapshot.request.tool
                if self._catalog.resolve_tool(f"{tool.server_id}__{tool.tool_name}") != tool:
                    return None
                if self._memory_snapshots.get(approval_id) is not snapshot or self._expired(
                    snapshot
                ):
                    return None
                return snapshot

        async def on_error(exc: BaseException) -> None:
            if not isinstance(exc, (asyncio.CancelledError, ResolutionError)):
                self._host.global_halt()

        def fallback(exc: BaseException) -> None:
            self._host.global_halt()

        return await fail_closed(operation, on_error=on_error, on_containment_error=fallback)

    async def _approve(self, approval_id: str, operator_id: str) -> Outcome:
        task_id = self._approval_task_id(approval_id)
        if task_id is None:
            return await self._pre_boundary(
                lambda: self._unknown_approval(approval_id),
                approval_id=approval_id,
                task_id=None,
            )
        lock = await self._acquire_task_lock(task_id)
        if isinstance(lock, Denied):
            return lock
        try:
            prepared = await self._pre_boundary(
                lambda: self._prepare_approval(approval_id, operator_id),
                approval_id=approval_id,
                task_id=task_id,
            )
            if isinstance(prepared, Denied):
                return prepared
            # 소비 이후는 예약 후 경계다. fail_closed로 Denied를 만들지 않는다.
            self._inflight_tasks[approval_id] = task_id
            try:
                result = await self._host.dispatch_reserved(
                    prepared.request,
                    prepared.state,
                    expires_at_utc=prepared.expires_at_utc,
                    monotonic_deadline=prepared.monotonic_deadline,
                    on_cancelled=lambda outcome: self._complete(approval_id, outcome, "executed"),
                )
                self._complete(approval_id, result, "executed")
                return result
            finally:
                self._inflight_tasks.pop(approval_id, None)
        finally:
            lock.release()

    async def _prepare_approval(self, approval_id: str, operator_id: str) -> _Reserved | Denied:
        record = await self._load_approval(approval_id)
        snapshot = self._memory_snapshots.get(approval_id)
        if record is None or record.status not in _LIVE:
            self._memory_snapshots.pop(approval_id, None)
            return self._denied(record, Reason.APPROVAL_RACE)
        if snapshot is None:
            return self._denied(record, Reason.APPROVAL_CONTEXT_LOST)
        if (
            operator_id != self._approver_id
            or operator_id != record.approver_id
            or operator_id != snapshot.approver_id
        ):
            await self._finish(
                record, snapshot, self._invalid_status(record), Reason.APPROVAL_CONTEXT_LOST
            )
            await self._halt_snapshot_task(snapshot, Reason.APPROVAL_CONTEXT_LOST)
            return self._denied(record, Reason.APPROVAL_CONTEXT_LOST)
        if (
            record.request_id != snapshot.request.request_id
            or record.snapshot_hash != snapshot.hash
            or record.expires_at_utc != snapshot.expires_at_utc
        ):
            await self._finish(
                record, snapshot, self._invalid_status(record), Reason.APPROVAL_STALE
            )
            await self._halt_snapshot_task(snapshot, Reason.APPROVAL_STALE)
            return self._denied(record, Reason.APPROVAL_STALE)
        if self._expired(snapshot):
            await self._finish(
                record, snapshot, ApprovalStatus.EXPIRED, Reason.APPROVAL_STALE, resume=True
            )
            return self._denied(record, Reason.APPROVAL_STALE)
        state = await self._load_snapshot_task(snapshot)
        if not self._host.policy_matches(state):
            await self._halt_snapshot_task(snapshot, Reason.POLICY_CHANGED)
            await self._finish(
                record, snapshot, self._invalid_status(record), Reason.POLICY_CHANGED
            )
            return self._denied(record, Reason.POLICY_CHANGED)
        if not self._task_matches(snapshot, state):
            await self._finish(
                record, snapshot, self._invalid_status(record), Reason.APPROVAL_STALE
            )
            if state.status is TaskStatus.WAITING_APPROVAL:
                await self._halt_snapshot_task(snapshot, Reason.APPROVAL_STALE)
            return self._denied(record, Reason.APPROVAL_STALE)
        if record.status is ApprovalStatus.PENDING:
            record = await self._transition(record, ApprovalStatus.APPROVED, Reason.OK)
        if self._expired(snapshot):
            await self._finish(
                record, snapshot, ApprovalStatus.EXPIRED, Reason.APPROVAL_STALE, resume=True
            )
            return self._denied(record, Reason.APPROVAL_STALE)
        call = ProposedCall(
            tool_ref=f"{snapshot.request.tool.server_id}__{snapshot.request.tool.tool_name}",
            arguments=snapshot.request.arguments,
            model_call_id="approval_recheck",
        )
        try:
            req = self._catalog.resolve(
                state,
                call,
                request_id=snapshot.request.request_id,
                call_seq=snapshot.request.call_seq,
            )
            decision = await self._host.evaluate_request(req, state, phase="approval_recheck")
        except ResolutionError as exc:
            await self._finish(record, snapshot, ApprovalStatus.INVALIDATED, exc.reason)
            await self._halt_snapshot_task(snapshot, exc.reason)
            return self._denied(record, exc.reason)
        if decision.decision is not Decision.ASK:
            configuration_changed = any(reason in _CONFIG_REASONS for reason in decision.reasons)
            await self._finish(
                record,
                snapshot,
                ApprovalStatus.INVALIDATED,
                Reason.APPROVAL_STALE,
                resume=not configuration_changed,
            )
            if configuration_changed:
                await self._halt_snapshot_task(snapshot, Reason.APPROVAL_STALE)
            return self._denied(record, Reason.APPROVAL_STALE)
        if not self._host.policy_matches(state):
            await self._finish(record, snapshot, ApprovalStatus.INVALIDATED, Reason.POLICY_CHANGED)
            await self._halt_snapshot_task(snapshot, Reason.POLICY_CHANGED)
            return self._denied(record, Reason.POLICY_CHANGED)
        current = make_snapshot(
            req,
            state,
            decision,
            effective_label=self._host.effective_task_label(state),
            approver_id=self._approver_id,
            expires_at_utc=snapshot.expires_at_utc,
            monotonic_deadline=snapshot.monotonic_deadline,
            hash_json=json_digest,
            canonicalize=canonical_json,
        )
        if current.hash != record.snapshot_hash:
            await self._finish(
                record, snapshot, ApprovalStatus.INVALIDATED, Reason.APPROVAL_STALE, resume=True
            )
            return self._denied(record, Reason.APPROVAL_STALE)
        now, mono = self._times()
        if snapshot.expired(now_utc=now, now_monotonic=mono):
            await self._finish(
                record, snapshot, ApprovalStatus.EXPIRED, Reason.APPROVAL_STALE, resume=True
            )
            return self._denied(record, Reason.APPROVAL_STALE)
        self._host.check_stop(state.task_id)
        try:
            consumed = await self._store.consume_and_reserve(
                approval_id=record.approval_id,
                snapshot_hash=current.hash,
                request_id=req.request_id,
                task_id=state.task_id,
                expected_security_version=state.security_version,
                now_utc=approval_event_time(record, now),
            )
        except asyncio.CancelledError:
            # 소비/예약 커밋 ACK 불명은 Task 단위 취소로 축소하지 않는다.
            self._host.global_halt()
            raise
        if type(consumed) is not bool:
            raise StoreError(Reason.APPROVAL_RACE)
        if not consumed:
            refreshed = await self._load_approval(approval_id)
            if refreshed is not None and refreshed.status in _LIVE:
                await self._finish(
                    refreshed,
                    snapshot,
                    self._invalid_status(refreshed),
                    Reason.APPROVAL_RACE,
                    resume=True,
                )
            else:
                self._memory_snapshots.pop(approval_id, None)
            return self._denied(record, Reason.APPROVAL_RACE)
        # 예약 ACK 후에도 dispatch 위임까지 lock 소유 관계에 공백을 만들지 않는다.
        self._inflight_tasks[approval_id] = snapshot.task_id
        self._cancel_expiry(approval_id)
        self._memory_snapshots.pop(approval_id, None)
        return _Reserved(req, state, snapshot.expires_at_utc, snapshot.monotonic_deadline)

    async def _reject_or_expire(
        self, approval_id: str, operator_id: str, *, expire: bool
    ) -> Denied:
        task_id = self._approval_task_id(approval_id)

        async def operation() -> Denied:
            record = await self._load_approval(approval_id)
            snap = self._memory_snapshots.get(approval_id)
            if record is None or record.status not in _LIVE:
                self._memory_snapshots.pop(approval_id, None)
                return self._denied(record, Reason.APPROVAL_RACE)
            if operator_id != self._approver_id or record.approver_id != operator_id:
                if snap is not None:
                    await self._finish(
                        record, snap, self._invalid_status(record), Reason.APPROVAL_CONTEXT_LOST
                    )
                    await self._halt_snapshot_task(snap, Reason.APPROVAL_CONTEXT_LOST)
                return self._denied(record, Reason.APPROVAL_CONTEXT_LOST)
            if snap is None:
                return self._denied(record, Reason.APPROVAL_CONTEXT_LOST)
            if (
                record.request_id != snap.request.request_id
                or record.snapshot_hash != snap.hash
                or record.expires_at_utc != snap.expires_at_utc
            ):
                await self._finish(
                    record, snap, self._invalid_status(record), Reason.APPROVAL_STALE
                )
                await self._halt_snapshot_task(snap, Reason.APPROVAL_STALE)
                return self._denied(record, Reason.APPROVAL_STALE)
            if expire and not self._expired(snap):
                return self._denied(record, Reason.APPROVAL_RACE)
            if not expire and record.status is not ApprovalStatus.PENDING:
                return self._denied(record, Reason.APPROVAL_RACE)
            status = ApprovalStatus.EXPIRED if expire else ApprovalStatus.REJECTED
            await self._finish(record, snap, status, Reason.OK, resume=True)
            return self._denied(record, Reason.OK)

        if task_id is None:
            return await self._pre_boundary(
                lambda: self._unknown_approval(approval_id),
                approval_id=approval_id,
                task_id=None,
            )
        lock = await self._acquire_task_lock(task_id)
        if isinstance(lock, Denied):
            return lock
        try:
            return await self._pre_boundary(operation, approval_id=approval_id, task_id=task_id)
        finally:
            lock.release()

    def _has_task_context(self, task_id: str) -> bool:
        """직접 조회 부재와 이미 알려진 승인 관계의 유실을 구분한다."""
        return (
            task_id in self._inflight_tasks.values()
            or any(snapshot.task_id == task_id for snapshot in self._memory_snapshots.values())
            or any(owner.task_id == task_id for owner in self._closing_approvals.values())
        )

    def _approval_task_id(self, approval_id: str) -> str | None:
        snapshot = self._memory_snapshots.get(approval_id)
        if snapshot is not None:
            return snapshot.task_id
        owner = self._closing_approvals.get(approval_id)
        return self._inflight_tasks.get(approval_id) if owner is None else owner.task_id

    def _discard_snapshot(self, approval_id: str) -> None:
        self._cancel_expiry(approval_id)
        snapshot = self._memory_snapshots.get(approval_id)
        if snapshot is not None:
            self._closing_approvals[approval_id] = _ClosingApproval(
                snapshot.task_id, snapshot.request.epoch
            )
            self._memory_snapshots.pop(approval_id, None)

    async def _unknown_approval(self, approval_id: str) -> Denied:
        """Task 소유 관계를 추측하지 않고 재시작 후 행은 복구 처리에 남긴다."""
        record = await self._load_approval(approval_id)
        reason = (
            Reason.APPROVAL_CONTEXT_LOST
            if record is not None and record.status in _LIVE
            else Reason.APPROVAL_RACE
        )
        return self._denied(record, reason)

    async def _cancel(self, task_id: str, operator_id: str) -> Denied:
        if operator_id != self._approver_id:
            return Denied(request_id=None, reasons=(Reason.APPROVAL_CONTEXT_LOST,))
        if (
            type(task_id) is not str
            or not task_id
            or any(0xD800 <= ord(char) <= 0xDFFF for char in task_id)
        ):
            return Denied(request_id=None, reasons=(Reason.STATE_MISSING,))
        # 접근 규칙 재평가가 lock을 쥐고 있어도 인증된 정지 의도를 먼저 기록한다.
        self._host.request_stop(task_id)

        async def operation() -> Denied:
            if operator_id != self._approver_id:
                return Denied(request_id=None, reasons=(Reason.APPROVAL_CONTEXT_LOST,))
            try:
                loaded = await self._store.load_task(task_id)
            except StoreError as error:
                if (
                    type(error) is StoreError
                    and error.reason is Reason.STATE_MISSING
                    and not self._has_task_context(task_id)
                ):
                    return Denied(request_id=None, reasons=(Reason.STATE_MISSING,))
                raise
            try:
                state = TaskState.model_validate(loaded)
            except ValidationError:
                raise _ApprovalTaskMismatchError() from None
            if state.task_id != task_id:
                raise _ApprovalTaskMismatchError()
            await self.invalidate_task(task_id, Reason.TASK_NOT_RUNNING, notify=False)
            await self._host.halt_task(task_id, Reason.TASK_NOT_RUNNING)
            self._publish_task_invalidations(task_id, Reason.TASK_NOT_RUNNING)
            return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))

        lock = await self._acquire_task_lock(task_id)
        if isinstance(lock, Denied):
            return lock
        try:
            return await self._pre_boundary(operation, approval_id=None, task_id=task_id)
        finally:
            lock.release()

    async def _finish(
        self,
        record: ApprovalRecord,
        snapshot: ApprovalSnapshot,
        status: ApprovalStatus,
        reason: Reason,
        *,
        resume: bool = False,
    ) -> None:
        self._discard_snapshot(record.approval_id)
        await self._transition(record, status, reason)
        self._causes[record.approval_id] = (
            "expired"
            if status is ApprovalStatus.EXPIRED
            else "rejected"
            if status is ApprovalStatus.REJECTED
            else self._invalidation_cause(reason)
        )
        if resume:
            state = await self._load_snapshot_task(snapshot)
            if not self._host.policy_matches(state):
                await self._halt_snapshot_task(snapshot, Reason.POLICY_CHANGED)
            elif self._task_matches(snapshot, state):
                returned = await self._store.set_task_status(
                    state.task_id,
                    TaskStatus.RUNNING,
                    expected_row_revision=state.row_revision,
                    at_utc=self._times()[0],
                    reason=reason,
                )
                try:
                    checked_status_change(returned, state, TaskStatus.RUNNING)
                except StoreError:
                    raise _ApprovalTaskMismatchError() from None
        self._closing_approvals.pop(record.approval_id, None)

    async def _transition(
        self, record: ApprovalRecord, status: ApprovalStatus, reason: Reason
    ) -> ApprovalRecord:
        event_time = approval_event_time(record, self._times()[0])
        changed = await self._store.transition_approval(
            record.approval_id,
            status,
            expected_row_revision=record.row_revision,
            approver_id=self._approver_id,
            now_utc=event_time,
            reason=reason,
        )
        result = self._checked_approval(changed, record.approval_id)
        if (
            result.request_id != record.request_id
            or result.snapshot_hash != record.snapshot_hash
            or result.approver_id != record.approver_id
            or result.created_at_utc != record.created_at_utc
            or result.expires_at_utc != record.expires_at_utc
            or result.consumed_at_utc != record.consumed_at_utc
            or result.status is not status
            or result.row_revision != record.row_revision + 1
            or result.decided_at_utc is None
            or result.decided_at_utc < event_time
        ):
            raise _ApprovalRecordMismatchError()
        return result

    async def _pre_boundary[T](
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        approval_id: str | None,
        task_id: str | None,
    ) -> T | Denied:
        snapshot = None if approval_id is None else self._memory_snapshots.get(approval_id)

        async def on_error(exc: BaseException) -> Denied:
            if (
                isinstance(exc, ResolutionError)
                and exc.reason
                in {Reason.POLICY_CHANGED, *(Reason(item.value) for item in SystemStopReason)}
                and task_id is not None
                and snapshot is not None
            ):
                await self.invalidate_task(task_id, exc.reason, cause="invalidated", notify=False)
                await self._halt_snapshot_task(snapshot, exc.reason)
                self._publish_task_invalidations(task_id, exc.reason)
                return Denied(request_id=None, reasons=(exc.reason,))
            if approval_id is not None:
                self._causes[approval_id] = "failed"
            for known_id, owner in self._pending.items():
                if owner.task_id == task_id:
                    self._causes[known_id] = "failed"
            if isinstance(exc, _ApprovalRecordMismatchError):
                # Store가 오연결된 행을 반환했다. 같은 getter로 정리를 재시도하면
                # 다른 Task lock 없이 관계없는 승인을 변경할 수 있으므로 쓰지 않는다.
                self._host.global_halt()
                for known_id, snap in tuple(self._memory_snapshots.items()):
                    if known_id == approval_id or (task_id is not None and snap.task_id == task_id):
                        self._discard_snapshot(known_id)
                if task_id is None:
                    return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
                return await self._host.pre_dispatch_failure(task_id, exc)
            identity_mismatch = isinstance(exc, _ApprovalTaskMismatchError) or (
                approval_id is not None and snapshot is None
            )
            if identity_mismatch:
                self._host.global_halt()
            if task_id is None:
                if approval_id is not None:
                    self._discard_snapshot(approval_id)
                self._host.global_halt()
                return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
            await self.invalidate_task(task_id, Reason.INTERNAL_ERROR)
            if approval_id is not None:
                # _finish가 사본을 지운 직후 실패한 레코드도 종착 상태로 정리한다.
                record = await self._load_approval(approval_id)
                if record is not None and record.status in _LIVE:
                    await self._transition(
                        record, self._invalid_status(record), Reason.INTERNAL_ERROR
                    )
            if not identity_mismatch:
                if snapshot is None:
                    await self._host.halt_task(task_id, Reason.INTERNAL_ERROR)
                else:
                    await self._halt_snapshot_task(snapshot, Reason.INTERNAL_ERROR)
            return await self._host.pre_dispatch_failure(task_id, exc)

        def on_containment_error(exc: BaseException) -> Denied:
            self._host.global_halt()
            for known_id, owner in tuple(self._pending.items()):
                if (known_id == approval_id or owner.task_id == task_id) and (
                    known_id not in self._inflight_tasks
                ):
                    self._discard_snapshot(known_id)
                    self._complete(
                        known_id,
                        Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,)),
                        "failed",
                    )
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))

        result: T | Denied = await fail_closed(
            operation, on_error=on_error, on_containment_error=on_containment_error
        )
        if (
            isinstance(result, Denied)
            and approval_id is not None
            and approval_id not in self._memory_snapshots
            and approval_id not in self._inflight_tasks
        ):
            self._complete(approval_id, result)
        if isinstance(result, Denied) and task_id is not None:
            self._publish_task_invalidations(task_id, result.reasons[0])
        return result

    async def _acquire_task_lock(self, task_id: str) -> asyncio.Lock | Denied:
        async def acquire() -> asyncio.Lock:
            lock = self._host.task_lock(task_id)
            await lock.acquire()
            return lock

        async def on_error(exc: BaseException) -> asyncio.Lock | Denied:
            if isinstance(exc, asyncio.CancelledError):
                # 예약 전 제어 취소도 기존 소유자의 예약 후 처리가 끝난 뒤 정리한다.
                async with self._host.task_lock(task_id):
                    return await self._host.pre_dispatch_failure(task_id, exc)
            # 저장/기타 lock 오류는 기존 전역 정지 경계를 유지한다.
            self._host.global_halt()
            return await self._host.pre_dispatch_failure(task_id, exc)

        def fallback(exc: BaseException) -> asyncio.Lock | Denied:
            self._host.global_halt()
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))

        return await fail_closed(acquire, on_error=on_error, on_containment_error=fallback)

    async def _load_snapshot_task(self, snapshot: ApprovalSnapshot) -> TaskState:
        try:
            state = TaskState.model_validate(await self._store.load_task(snapshot.task_id))
        except ValidationError:
            raise _ApprovalTaskMismatchError() from None
        if state.task_id != snapshot.task_id or state.epoch != snapshot.request.epoch:
            raise _ApprovalTaskMismatchError()
        return state

    async def _load_approval(self, approval_id: str) -> ApprovalRecord | None:
        loaded = await self._store.get_approval(approval_id)
        return None if loaded is None else self._checked_approval(loaded, approval_id)

    @staticmethod
    def _checked_approval(loaded: ApprovalRecord, approval_id: str) -> ApprovalRecord:
        try:
            record = ApprovalRecord.model_validate(loaded)
        except ValidationError:
            raise _ApprovalRecordMismatchError() from None
        if record.approval_id != approval_id:
            raise _ApprovalRecordMismatchError()
        return record

    async def _halt_snapshot_task(self, snapshot: ApprovalSnapshot, reason: Reason) -> None:
        await self._host.halt_task(snapshot.task_id, reason, expected_epoch=snapshot.request.epoch)

    def _expired(self, snapshot: ApprovalSnapshot) -> bool:
        now, mono = self._times()
        return snapshot.expired(now_utc=now, now_monotonic=mono)

    def _times(self) -> tuple[datetime, float]:
        now, mono = self._utc_now(), self._monotonic()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ApprovalConfigError("승인 시각은 UTC-aware여야 한다")
        if type(mono) not in (float, int) or not math.isfinite(mono) or mono < 0:
            raise ApprovalConfigError("승인 monotonic 시각이 올바르지 않다")
        return now, float(mono)

    def _task_matches(self, snapshot: ApprovalSnapshot, state: TaskState) -> bool:
        return (
            state.status is TaskStatus.WAITING_APPROVAL
            and state.security_version == snapshot.state.security_version
            and self._security_context(state) == self._security_context(snapshot.state)
            and self._host.effective_task_label(state) == snapshot.effective_label
        )

    @staticmethod
    def _security_context(state: TaskState) -> tuple[object, ...]:
        return (
            state.task_id,
            state.epoch,
            state.profile_id,
            state.policy_version,
            state.high_watermark,
            state.unclassified,
            state.escalations,
        )

    @staticmethod
    def _invalid_status(record: ApprovalRecord) -> ApprovalStatus:
        return (
            ApprovalStatus.CANCELLED
            if record.status is ApprovalStatus.PENDING
            else ApprovalStatus.INVALIDATED
        )

    @staticmethod
    def _denied(record: ApprovalRecord | None, reason: Reason) -> Denied:
        return Denied(request_id=None if record is None else record.request_id, reasons=(reason,))


class HumanControl:
    """사전 등록 사람 제어 채널. A가 UI 제어 큐에서만 이 객체를 보관한다.

    로컬 Python 객체 접근은 운영자 인증 구현이 아니다. 신뢰된 단일 운영자
    전제에서 실행 API와 제어 API를 분리하는 capability이며 MCP 도구가 아니다.
    """

    def __init__(self, service: ApprovalService, operator_id: str, key: object) -> None:
        if key is not service._channel_key:
            raise ApprovalConfigError("승인 제어 채널은 신뢰 초기화 경로에서만 만든다")
        self._service = service
        self._operator_id = operator_id

    async def review(self, approval_id: str) -> ApprovalSnapshot | None:
        return await self._service._review(approval_id, self._operator_id)

    async def approve(self, approval_id: str) -> Outcome:
        return await self._service._approve(approval_id, self._operator_id)

    async def reject(self, approval_id: str) -> Denied:
        return await self._service._reject_or_expire(approval_id, self._operator_id, expire=False)

    async def expire(self, approval_id: str) -> Denied:
        return await self._service._reject_or_expire(approval_id, self._operator_id, expire=True)

    async def cancel(self, task_id: str) -> Denied:
        return await self._service._cancel(task_id, self._operator_id)

    async def emergency_halt(self, task_id: str) -> Denied:
        return await self._service._cancel(task_id, self._operator_id)
