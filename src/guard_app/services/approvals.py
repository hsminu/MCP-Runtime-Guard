"""8장: 메모리 사본, 사람 전용 제어, 재평가와 원자적 승인 소비.

Store의 실제 원자성은 C 구현·계약 검증에 의존한다. 이 서비스는 SQL이나
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

from guard_app.core.snapshot import ApprovalSnapshot, make_snapshot
from guard_app.domain.contracts import ApprovalRecord, Denied, Outcome
from guard_app.domain.enums import ApprovalStatus, Decision, Label, TaskStatus
from guard_app.domain.errors import ResolutionError, StoreError
from guard_app.domain.models import PolicyDecision, ProposedCall, ResolvedRequest, TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.security.digest import canonical_json, json_digest
from guard_app.security.failclosed import fail_closed
from guard_app.services.catalog import Catalog


class ApprovalConfigError(ValueError):
    """설정 원문을 공개하지 않는 승인 설정 오류."""


class _ApprovalTaskMismatchError(StoreError):
    """승인 사본과 연결되지 않은 상태로 Task 행을 변경하지 않는다."""


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


_LIVE = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
_CONFIG_REASONS = (
    Reason.POLICY_CHANGED,
    Reason.TOOL_FINGERPRINT_CHANGED,
    Reason.REG_NOT_ACTIVE,
    Reason.UNKNOWN_SERVER,
    Reason.TOOL_UNSUPPORTED,
)


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
    ) -> None:
        if (
            type(approver_id) is not str
            or not approver_id
            or any(0xD800 <= ord(char) <= 0xDFFF for char in approver_id)
        ):
            raise ApprovalConfigError("사전 등록 승인자가 올바르지 않다")
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ApprovalConfigError("승인 유효 시간은 양의 정수여야 한다")
        if not all(callable(value) for value in (approval_id_factory, utc_now, monotonic)):
            raise ApprovalConfigError("승인 ID·시간 공급자가 올바르지 않다")
        self._store = store
        self._catalog = catalog
        self._host = host
        self._approver_id = approver_id
        self._approval_id_factory = approval_id_factory
        self._utc_now = utc_now
        self._monotonic = monotonic
        self._ttl_seconds = ttl_seconds
        self._memory_snapshots: dict[str, ApprovalSnapshot] = {}
        self._inflight_tasks: dict[str, str] = {}
        self._channel_key = object()

    def human_channel(self, operator_id: str) -> HumanControl:
        """A의 신뢰 UI 제어 큐에만 전달할 capability. 모델 제안으로 만들지 않는다."""
        if type(operator_id) is not str or operator_id != self._approver_id:
            raise ApprovalConfigError("사전 등록 운영자만 승인 제어 채널을 받을 수 있다")
        return HumanControl(self, operator_id, self._channel_key)

    async def open(
        self, req: ResolvedRequest, state: TaskState, decision: PolicyDecision
    ) -> ApprovalRecord:
        """WAITING 반환 버전을 사본에 고정한다. 중간 저장 실패는 예약 전 정지다.

        B1의 Task 전이·승인 생성은 별도 Store 메서드다. 실제 C 구현에서 둘의
        원자 묶음은 합의 대상이며, 여기서는 실패를 정상 Pending으로 숨기지 않는다.
        """
        req = ResolvedRequest.model_validate(req)
        state = TaskState.model_validate(state)
        decision = PolicyDecision.model_validate(decision)
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
        if approval.approval_id in self._memory_snapshots:
            raise StoreError(Reason.APPROVAL_RACE)
        await self._store.create_approval(approval)
        self._memory_snapshots[approval.approval_id] = snapshot
        return approval

    async def invalidate_task(self, task_id: str, reason: Reason) -> None:
        """호출자가 이미 잡은 lock 안에서 원문 사본과 살아 있는 승인을 폐기한다."""
        approval_ids = tuple(
            approval_id
            for approval_id, snap in self._memory_snapshots.items()
            if snap.task_id == task_id
        )
        for approval_id in approval_ids:
            self._memory_snapshots.pop(approval_id, None)
        for approval_id in approval_ids:
            record = await self._store.get_approval(approval_id)
            if record is not None and record.status in _LIVE:
                await self._transition(record, self._invalid_status(record), reason)

    async def _review(self, approval_id: str, operator_id: str) -> ApprovalSnapshot | None:
        """Human-only memory view; never rebuild arguments from audit storage."""
        if operator_id != self._approver_id:
            return None
        snapshot = self._memory_snapshots.get(approval_id)
        if snapshot is None:
            return None
        async with self._host.task_lock(snapshot.task_id):
            record = await self._store.get_approval(approval_id)
            if (
                self._memory_snapshots.get(approval_id) is not snapshot
                or record is None
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
            return snapshot

    async def _approve(self, approval_id: str, operator_id: str) -> Outcome:
        snapshot = self._memory_snapshots.get(approval_id)
        task_id = self._inflight_tasks.get(approval_id) if snapshot is None else snapshot.task_id
        if task_id is None:
            prepared = await self._pre_boundary(
                lambda: self._prepare_approval(approval_id, operator_id),
                approval_id=approval_id,
                task_id=None,
            )
            if isinstance(prepared, Denied):
                return prepared
            self._host.global_halt()
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
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
                return await self._host.dispatch_reserved(
                    prepared.request,
                    prepared.state,
                    expires_at_utc=prepared.expires_at_utc,
                    monotonic_deadline=prepared.monotonic_deadline,
                )
            finally:
                self._inflight_tasks.pop(approval_id, None)
        finally:
            lock.release()

    async def _prepare_approval(self, approval_id: str, operator_id: str) -> _Reserved | Denied:
        record = await self._store.get_approval(approval_id)
        snapshot = self._memory_snapshots.get(approval_id)
        if record is None or record.status not in _LIVE:
            self._memory_snapshots.pop(approval_id, None)
            return self._denied(record, Reason.APPROVAL_RACE)
        if snapshot is None:
            await self._transition(record, ApprovalStatus.EXPIRED, Reason.APPROVAL_CONTEXT_LOST)
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
        consumed = await self._store.consume_and_reserve(
            approval_id=record.approval_id,
            snapshot_hash=current.hash,
            request_id=req.request_id,
            task_id=state.task_id,
            expected_security_version=state.security_version,
            now_utc=now,
        )
        if type(consumed) is not bool:
            raise StoreError(Reason.APPROVAL_RACE)
        if not consumed:
            refreshed = await self._store.get_approval(approval_id)
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
        self._memory_snapshots.pop(approval_id, None)
        return _Reserved(req, state, snapshot.expires_at_utc, snapshot.monotonic_deadline)

    async def _reject_or_expire(
        self, approval_id: str, operator_id: str, *, expire: bool
    ) -> Denied:
        snapshot = self._memory_snapshots.get(approval_id)
        task_id = None if snapshot is None else snapshot.task_id

        async def operation() -> Denied:
            record = await self._store.get_approval(approval_id)
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
                else:
                    await self._transition(
                        record, self._invalid_status(record), Reason.APPROVAL_CONTEXT_LOST
                    )
                return self._denied(record, Reason.APPROVAL_CONTEXT_LOST)
            if snap is None:
                await self._transition(record, ApprovalStatus.EXPIRED, Reason.APPROVAL_CONTEXT_LOST)
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
            return await self._pre_boundary(operation, approval_id=approval_id, task_id=None)
        lock = await self._acquire_task_lock(task_id)
        if isinstance(lock, Denied):
            return lock
        try:
            return await self._pre_boundary(operation, approval_id=approval_id, task_id=task_id)
        finally:
            lock.release()

    async def _cancel(self, task_id: str, operator_id: str) -> Denied:
        if operator_id != self._approver_id:
            return Denied(request_id=None, reasons=(Reason.APPROVAL_CONTEXT_LOST,))
        # Record intent before acquiring the lock held by an AGT recheck.
        self._host.request_stop(task_id)

        async def operation() -> Denied:
            if operator_id != self._approver_id:
                return Denied(request_id=None, reasons=(Reason.APPROVAL_CONTEXT_LOST,))
            await self.invalidate_task(task_id, Reason.TASK_NOT_RUNNING)
            await self._host.halt_task(task_id, Reason.TASK_NOT_RUNNING)
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
        self._memory_snapshots.pop(record.approval_id, None)
        await self._transition(record, status, reason)
        if resume:
            state = await self._load_snapshot_task(snapshot)
            if not self._host.policy_matches(state):
                await self._halt_snapshot_task(snapshot, Reason.POLICY_CHANGED)
            elif self._task_matches(snapshot, state):
                await self._store.set_task_status(
                    state.task_id,
                    TaskStatus.RUNNING,
                    expected_row_revision=state.row_revision,
                    at_utc=self._times()[0],
                    reason=reason,
                )

    async def _transition(
        self, record: ApprovalRecord, status: ApprovalStatus, reason: Reason
    ) -> ApprovalRecord:
        return await self._store.transition_approval(
            record.approval_id,
            status,
            expected_row_revision=record.row_revision,
            approver_id=self._approver_id,
            now_utc=self._times()[0],
            reason=reason,
        )

    async def _pre_boundary[T](
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        approval_id: str | None,
        task_id: str | None,
    ) -> T | Denied:
        snapshot = None if approval_id is None else self._memory_snapshots.get(approval_id)

        async def on_error(exc: BaseException) -> Denied:
            identity_mismatch = isinstance(exc, _ApprovalTaskMismatchError) or (
                approval_id is not None and snapshot is None
            )
            if identity_mismatch:
                self._host.global_halt()
            if task_id is None:
                if approval_id is not None:
                    self._memory_snapshots.pop(approval_id, None)
                self._host.global_halt()
                return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
            await self.invalidate_task(task_id, Reason.INTERNAL_ERROR)
            if approval_id is not None:
                # _finish가 사본을 지운 직후 실패한 레코드도 종착 상태로 정리한다.
                record = await self._store.get_approval(approval_id)
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
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))

        return await fail_closed(
            operation, on_error=on_error, on_containment_error=on_containment_error
        )

    async def _acquire_task_lock(self, task_id: str) -> asyncio.Lock | Denied:
        async def acquire() -> asyncio.Lock:
            lock = self._host.task_lock(task_id)
            await lock.acquire()
            return lock

        async def on_error(exc: BaseException) -> asyncio.Lock | Denied:
            # 아직 lock을 갖지 않았으므로 진행 중 dispatch의 Task 행은 건드리지 않는다.
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
