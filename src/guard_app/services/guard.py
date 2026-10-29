"""기술설계서 7장: Task lock 안에서 판정·예약·응답 커밋을 집행한다.

실제 ID·순번 공급, 오류 감사, Transport·Store는 구성 시 담당 구현을 주입한다.
이 서비스는 SDK·SQLite·파일·URL에 직접 접근하지 않는다.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from weakref import WeakValueDictionary, finalize

from pydantic import ValidationError

from guard_app.core.policy import effective_label, evaluate
from guard_app.domain.contracts import (
    Denied,
    Outcome,
    OutcomeUnknown,
    Pending,
    Released,
    TaskIdentity,
    TrustedContext,
    Withheld,
)
from guard_app.domain.enums import (
    Decision,
    Label,
    RegStatus,
    RequestStatus,
    SessionEvent,
    SystemStopReason,
    TaskStatus,
    ToolKind,
)
from guard_app.domain.errors import DispatchError, ResolutionError, StoreError, StoreIntegrityError
from guard_app.domain.models import (
    AccessResult,
    FailureAuditRecord,
    PolicyDecision,
    PolicyInput,
    ProposedCall,
    ResolvedRequest,
    TaskState,
)
from guard_app.domain.reasons import Reason
from guard_app.ports import (
    AccessChecker,
    FailureAuditStore,
    PendingResult,
    Registry,
    SessionAudit,
    Store,
    Transport,
)
from guard_app.security.digest import json_digest
from guard_app.security.failclosed import after_dispatch, fail_closed
from guard_app.services.approvals import ApprovalService, HumanControl, _ApprovalTaskMismatchError
from guard_app.services.catalog import Catalog, LoadedProfile, PreparedPolicyChange
from guard_app.services.response_gate import ResponseGate
from guard_app.services.state_checks import (
    checked_status_change,
    checked_task,
    preserve_task_context,
)

# Task 생애·입력에 한정된 순번 실패는 다른 Task의 실행 권한을 닫지 않는다.
_TASK_LOCAL_SEQUENCE_REASONS = frozenset(
    {
        Reason.TASK_NOT_RUNNING,
        Reason.TASK_UNCLASSIFIED,
        Reason.STATE_MISSING,
        Reason.INVALID_ARGUMENTS,
    }
)


@dataclass(frozen=True, slots=True)
class FailureEvent:
    """C의 내구성 감사 연결용 서비스 메타데이터. 본문·인자·예외 원문은 없다."""

    task_id: str
    request_id: str | None
    phase: Literal["before_dispatch", "after_dispatch"]
    reason: Reason
    exception_type: str
    at_utc: datetime | None = None


def durable_failure_audit(store: FailureAuditStore) -> Callable[[FailureEvent], Awaitable[None]]:
    """Guard의 UTC를 사용해 실패 이벤트를 원문 없는 내구성 감사로 옮긴다.

    기존 다섯 인자의 수동 이벤트도 만들 수 있지만 UTC가 없는 이벤트는 저장하지
    않는다. 시각을 추정하면 Guard의 신뢰된 시간 공급자와 다를 수 있기 때문이다.
    """

    async def persist(event: FailureEvent) -> None:
        if event.at_utc is None:
            raise StoreError(Reason.INVALID_ARGUMENTS)
        try:
            record = FailureAuditRecord(
                task_id=event.task_id,
                request_id=event.request_id,
                phase=event.phase,
                reason=event.reason,
                exception_type=event.exception_type,
                at_utc=event.at_utc,
                run_id=None,
                epoch=None,
            )
        except ValidationError:
            raise StoreError(Reason.INVALID_ARGUMENTS) from None
        await store.record_failure_audit(record)

    return persist


@dataclass(frozen=True, slots=True)
class _Allowed:
    request: ResolvedRequest
    state: TaskState


class GuardConfigError(ValueError):
    """고정 문구의 서비스 구성 오류."""


class PolicyChangeError(ValueError):
    """정책 적용의 고정 오류. 저장소·설정·예외 원문을 포함하지 않는다."""


class SystemControlError(ValueError):
    """시스템 정지·사실 감사 실패. 원문 없는 고정 오류만 반환한다."""


class _SessionIdentityMismatchError(SystemControlError):
    """없는/옛 세션의 제어는 현재 Task를 변경하지 않고 거부한다."""


type PolicyTaskSource = Callable[[], Awaitable[tuple[TaskState, ...]]]


@dataclass(frozen=True, slots=True)
class PolicyChanged:
    """UI 표시용 적용 ACK. 설정 원문·파일 경로·명령은 포함하지 않는다."""

    previous_policy_version: str
    policy_version: str
    profile_id: str
    changed: bool
    stopped_task_ids: tuple[str, ...]
    invalidated_approval_count: int
    applied_at_utc: datetime

    @property
    def stopped_task_count(self) -> int:
        return len(self.stopped_task_ids)


class _TaskStopSignal(asyncio.Event):
    """종결 ACK를 받은 정지 의도만 수명 종료 후 제거한다."""

    def __init__(self) -> None:
        super().__init__()
        self.terminal_epoch: int | None = None
        self.reason = Reason.TASK_NOT_RUNNING


class GuardService:
    """일반 도구 실행의 유일한 공개 입구는 submit이다.

    request_id_factory·approval_id_factory는 C의 ID 공급자이고 next_call_seq는
    기존 Task를 포함해 중복 없는 순번을 공급해야 한다. 기존 Store 초안에 이
    공급 포트가 없어 구성 callback으로 분리했다. 재시작 시 이를 0으로 초기화하면
    안 된다. failure_audit도 C가 내구성 저장을 연결해야 한다. on_global_halt는
    전역 정지로 처음 전환할 때 한 번 호출되며 동기로 실행되고 예외를 내지 않는다.
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
        approval_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_global_halt: Callable[[Reason], None] | None = None,
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
        check_access = getattr(access_checker, "check_access", None)
        if not callable(check_access) or inspect.iscoroutinefunction(check_access):
            raise GuardConfigError("동기 접근 판정기가 유효하지 않다")
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
        if on_global_halt is not None and (
            not callable(on_global_halt) or inspect.iscoroutinefunction(on_global_halt)
        ):
            raise GuardConfigError("Guard 전역 정지 함수가 유효하지 않다")
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
        self._max_result_bytes = max_result_bytes
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._global_halt = False
        self._on_global_halt = on_global_halt
        self._stop_requests: dict[str, _TaskStopSignal] = {}
        self._system_stop_requests: dict[tuple[str, int], _TaskStopSignal] = {}
        self._system_channel_key = object()
        self._policy_apply_lock = asyncio.Lock()
        self._policy_ready = asyncio.Event()
        self._policy_ready.set()
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
            sleep=approval_sleep,
        )

    def human_control(self, operator_id: str) -> HumanControl:
        """A의 인증된 제어 채널에만 전달한다. 모델 ToolView에 포함하지 않는다."""
        return self._approvals.human_channel(operator_id)

    def system_control(self, audit: SessionAudit) -> SystemControl:
        """STOP-01 합의 전 제안. 신뢰 구성 코드만 세션 관리자에 전달한다.

        audit는 내구성 SessionAudit이며 SQLiteStore를 그대로 연결할 수 있다.
        HumanControl·모델 도구에는 이 capability나 Guard 객체를 전달하지 않는다.
        Python 객체 분리이며 같은 프로세스의 임의 코드 실행을 격리하지는 않는다.
        """
        if not callable(getattr(audit, "record_session_event", None)):
            raise GuardConfigError("내구성 세션 감사가 필요하다")
        return SystemControl(self, audit, self._system_channel_key)

    async def _session_command(
        self,
        identity: TaskIdentity,
        audit: SessionAudit,
        event: SessionEvent,
        reason: SystemStopReason | None = None,
    ) -> TaskState:
        identity = _system_identity(identity)
        if event is SessionEvent.STOPPED:
            if type(reason) is not SystemStopReason:
                raise SystemControlError("시스템 정지 사유가 유효하지 않다")
            # epoch별 신호는 옛 세션의 늦은 정지가 새 Task 생애를 막지 않게 한다.
            signal = self._system_stop_requests.setdefault(
                (identity.task_id, identity.epoch), _TaskStopSignal()
            )
            if not signal.is_set():
                signal.reason = Reason(reason.value)
            signal.set()

        async def operation() -> TaskState:
            async with self._task_lock(identity.task_id):
                # 예약 후 격리 커밋이 실패했다면 Store가 아직 RUNNING일 수 있다.
                # 이 상태를 HALTED 성공 ACK로 덮어 SEND 불명을 숨기지 않는다.
                if self._global_halt:
                    raise StoreError(Reason.STATE_COMMIT_FAILED)
                try:
                    loaded = await self._store.load_task(identity.task_id)
                except StoreError as error:
                    if (
                        type(error) is StoreError
                        and error.reason is Reason.STATE_MISSING
                        and not self._approvals._has_task_context(identity.task_id)
                    ):
                        raise _SessionIdentityMismatchError("세션 Task를 확인할 수 없다") from None
                    raise
                state = TaskState.model_validate(loaded)
                if state.task_id != identity.task_id:
                    raise StoreError(Reason.STATE_MISSING)
                if state.epoch != identity.epoch:
                    raise _SessionIdentityMismatchError("세션 Task 생애가 일치하지 않는다")
                if event is SessionEvent.STOPPED:
                    code = Reason(reason.value) if reason is not None else Reason.INTERNAL_ERROR
                    await self._approvals.invalidate_task(
                        identity.task_id, code, cause="invalidated", notify=False
                    )
                    state = await self._halt_task(
                        identity.task_id, code, expected_epoch=identity.epoch
                    )
                await audit.record_session_event(
                    identity, event, at_utc=self._policy_time(), reason=reason
                )
                if self._global_halt:
                    raise StoreError(Reason.STATE_COMMIT_FAILED)
                if event is SessionEvent.STOPPED:
                    self._approvals._publish_task_invalidations(identity.task_id, code)
                    self._confirm_terminal_stop(state)
                return state

        async def on_error(exc: BaseException) -> None:
            if isinstance(exc, _SessionIdentityMismatchError):
                self._system_stop_requests.pop((identity.task_id, identity.epoch), None)
                return
            self._halt_globally(
                exc.reason if isinstance(exc, StoreError) else Reason.INTERNAL_ERROR
            )
            self._approvals._fail_system_stop(identity.task_id, identity.epoch)

        def fallback(exc: BaseException) -> None:
            self._halt_globally(
                exc.reason if isinstance(exc, StoreError) else Reason.INTERNAL_ERROR
            )
            self._approvals._fail_system_stop(identity.task_id, identity.epoch)

        result = await fail_closed(operation, on_error=on_error, on_containment_error=fallback)
        if result is None:
            raise SystemControlError("세션 제어를 확정하지 못했다") from None
        return result

    def pending_result(self, request_id: str) -> PendingResult:
        """합의 전 제안: A는 Pending의 최종 모델 응답에 이 단일 소비 경로만 쓴다."""
        return self._approvals.pending_result(request_id)

    async def invalidate_tool(self, server_id: str, tool_name: str) -> int:
        """합의 전 제안: C Registry 지문 변경의 승인 무효화 입구."""
        return await self._approvals.invalidate_tool(server_id, tool_name)

    async def cancel_pending(self, task_id: str, *, expected_epoch: int) -> int:
        """합의 전 제안: A 세션 종료의 대기 취소. 사람 채널을 사용하지 않는다."""
        return await self._approvals.cancel_pending(task_id, expected_epoch=expected_epoch)

    async def aclose(self) -> None:
        """앱 종료 시 A가 transport 종결과 함께 await한다. 새 submit은 즉시 거부한다."""
        self._halt_globally(Reason.GUARD_CLOSED)
        await self._approvals.aclose()

    async def apply_policy_change(
        self,
        change: PreparedPolicyChange,
        access_checker: AccessChecker,
        *,
        task_source: PolicyTaskSource,
    ) -> PolicyChanged:
        """신뢰 구성 코드 전용. 준비 → A/C의 문서 저장 → 저장 성공 후 적용 순서다.

        저장 실패면 호출하지 않는다. task_source는 이 Guard가 관할하는 전체 Task의
        최신 사본을 반환해야 한다(SQLiteStore.list_tasks 연결 가능). 기존 Store 포트는
        바꾸지 않는다. 적용 중 submit은 대기하고 기존 호출은 같은 Task lock으로 정리한다.
        성공 뒤 새 Task는 반환한 profile_id·policy_version으로 생성해야 한다.
        기록 실패는 global halt와 PolicyChangeError이며 성공 ACK를 반환하지 않는다.
        """
        check_access = getattr(access_checker, "check_access", None)
        if (
            type(change) is not PreparedPolicyChange
            or type(change.loaded_profile) is not LoadedProfile
            or not callable(check_access)
            or inspect.iscoroutinefunction(check_access)
            or not callable(task_source)
        ):
            raise PolicyChangeError("정책 적용 입력이 유효하지 않다")
        async with self._policy_apply_lock:
            if change.previous_policy_version != self._policy_version:
                raise PolicyChangeError("준비한 정책 기준 버전이 현재와 다르다")
            if not change.changed:
                return PolicyChanged(
                    self._policy_version,
                    self._policy_version,
                    self._profile_id,
                    False,
                    (),
                    0,
                    self._policy_time(),
                )
            if self._global_halt:
                raise PolicyChangeError("정지된 Guard에는 정책을 적용할 수 없다")
            self._policy_ready.clear()

            async def on_error(exc: BaseException) -> None:
                self._halt_globally(
                    exc.reason if isinstance(exc, StoreError) else Reason.INTERNAL_ERROR
                )
                self._approvals._fail_policy_change()

            def fallback(exc: BaseException) -> None:
                self._halt_globally(
                    exc.reason if isinstance(exc, StoreError) else Reason.INTERNAL_ERROR
                )
                self._approvals._fail_policy_change()

            try:
                result: PolicyChanged | None = await fail_closed(
                    lambda: self._apply_policy_change(change, access_checker, task_source),
                    on_error=on_error,
                    on_containment_error=fallback,
                )
            finally:
                self._policy_ready.set()
            if result is None:
                raise PolicyChangeError("정책 적용을 확정하지 못해 Guard가 정지되었다") from None
            return result

    def _policy_time(self) -> datetime:
        now = self._utc_now()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise PolicyChangeError("정책 적용 시각이 유효하지 않다")
        return now

    async def _apply_policy_change(
        self,
        change: PreparedPolicyChange,
        access_checker: AccessChecker,
        task_source: PolicyTaskSource,
    ) -> PolicyChanged:
        loaded = change.loaded_profile
        stopped: set[str] = set()
        invalidations_before = self._approvals._policy_invalidations
        observed: set[tuple[str, int, int, TaskStatus]] = set()
        while True:
            states = self._checked_policy_tasks(await task_source())
            pending_ids = self._approvals._policy_pending_task_ids()
            if not pending_ids <= {state.task_id for state in states}:
                raise StoreError(Reason.STATE_MISSING)
            candidates = tuple(
                state
                for state in states
                if (state.profile_id, state.policy_version)
                != (loaded.profile.profile_id, loaded.policy_version)
                and (
                    state.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
                    or state.task_id in pending_ids
                )
            )
            if not candidates:
                break
            keys = {
                (state.task_id, state.epoch, state.row_revision, state.status)
                for state in candidates
            }
            if observed & keys:
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            observed.update(keys)
            for state in candidates:
                if state.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                    self._request_stop(state.task_id, reason=Reason.POLICY_CHANGED)
            for state in candidates:
                async with self._task_lock(state.task_id):
                    current = checked_task(
                        await self._store.load_task(state.task_id),
                        task_id=state.task_id,
                        epoch=state.epoch,
                    )
                    await self._approvals._invalidate_policy_task(state.task_id)
                    if current.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                        await self._halt_task(
                            state.task_id, Reason.POLICY_CHANGED, expected_epoch=state.epoch
                        )
                    self._approvals._publish_task_invalidations(
                        state.task_id, Reason.POLICY_CHANGED
                    )
                    if state.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                        stopped.add(state.task_id)
            # lock 대기 중 생성된 옛 버전 Task도 새 조회에서 정리한다.
        if self._global_halt:
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        applied_at = self._policy_time()
        settings = loaded.guard_settings
        gate = ResponseGate(
            self._store,
            loaded.catalog,
            utc_now=self._utc_now,
            global_halt=self._halt_globally,
            max_result_bytes=self._max_result_bytes,
        )
        # await 없는 발행 구간. Task lock을 가진 기존 판정·응답은 위에서 모두 끝났다.
        self._approvals._replace_policy(loaded.catalog, settings.approval_ttl_seconds)
        self._catalog = loaded.catalog
        self._profile_id = settings.profile_id
        self._policy_version = settings.policy_version
        self._access_checker = access_checker
        self._mode = settings.taint_mode
        self._gate = gate
        return PolicyChanged(
            change.previous_policy_version,
            settings.policy_version,
            settings.profile_id,
            True,
            tuple(sorted(stopped)),
            self._approvals._policy_invalidations - invalidations_before,
            applied_at,
        )

    @staticmethod
    def _checked_policy_tasks(value: object) -> tuple[TaskState, ...]:
        if type(value) is not tuple:
            raise StoreError(Reason.STATE_MISSING)
        try:
            states = tuple(TaskState.model_validate(state) for state in value)
        except ValidationError:
            raise StoreError(Reason.STATE_MISSING) from None
        if len({state.task_id for state in states}) != len(states):
            raise StoreError(Reason.STATE_MISSING)
        return states

    async def submit(
        self, task_id: str, call: ProposedCall, *, expected_epoch: int | None = None
    ) -> Outcome:
        """I1·I4·I5·I7: 신뢰 호출자의 epoch를 lock 안에서 확인한다."""
        return await fail_closed(
            lambda: self._submit_locked(task_id, call, expected_epoch=expected_epoch),
            lambda exc: self._pre_dispatch_unlocked_failure(
                task_id, exc, expected_epoch=expected_epoch
            ),
            on_containment_error=self._pre_failure_fallback,
        )

    async def _submit_locked(
        self, task_id: str, call: ProposedCall, *, expected_epoch: int | None = None
    ) -> Outcome:
        if type(task_id) is not str or not task_id:
            raise ResolutionError(Reason.STATE_MISSING)
        if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch < 0):
            raise ResolutionError(Reason.STATE_MISSING)
        if self._global_halt:
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
        while True:
            await self._policy_ready.wait()
            async with self._task_lock(task_id):
                if not self._policy_ready.is_set():
                    continue
                decided = await fail_closed(
                    lambda: self._decide(task_id, call, expected_epoch=expected_epoch),
                    lambda exc: self._pre_dispatch_failure(
                        task_id, exc, expected_epoch=expected_epoch
                    ),
                    on_containment_error=self._pre_failure_fallback,
                )
                if not isinstance(decided, _Allowed):
                    return decided
                return await self._dispatch_reserved(decided.request, decided.state)

    def _task_lock(self, task_id: str) -> asyncio.Lock:
        lock = self._locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[task_id] = lock
            # 소유자·대기자·빌린 참조가 있는 동안 같은 lock이 살아 있다.
            # callback은 lock을 보관하지 않고 마지막 참조가 사라진 뒤만 실행한다.
            finalize(lock, self._cleanup_task_resources, task_id)
        return lock

    def _cleanup_task_resources(self, task_id: str) -> None:
        """I3/I6/I8: 실행 참조가 남거나 정지 저장이 불명이면 신호를 보존한다."""
        if self._locks.get(task_id) is not None or self._approvals._has_task_context(task_id):
            return
        signal = self._stop_requests.get(task_id)
        if signal is not None and (not signal.is_set() or signal.terminal_epoch is not None):
            self._stop_requests.pop(task_id, None)
        for key, system_signal in tuple(self._system_stop_requests.items()):
            if key[0] == task_id and system_signal.terminal_epoch is not None:
                self._system_stop_requests.pop(key, None)

    def _confirm_terminal_stop(self, state: TaskState) -> None:
        """검증한 Store 반환만 종결 근거로 삼는다. 캐시 정리로 재개하지 않는다."""
        if state.status not in (
            TaskStatus.HALTED,
            TaskStatus.UNKNOWN_OUTCOME,
            TaskStatus.CLOSED,
        ):
            return
        self._approvals._discard_task_released(state.task_id, state.epoch)
        signal = self._stop_requests.get(state.task_id)
        if signal is not None:
            signal.terminal_epoch = state.epoch
        system_signal = self._system_stop_requests.get((state.task_id, state.epoch))
        if system_signal is not None:
            system_signal.terminal_epoch = state.epoch
        self._cleanup_task_resources(state.task_id)

    def _request_stop(self, task_id: str, *, reason: Reason = Reason.TASK_NOT_RUNNING) -> None:
        """신뢰된 사람·구성 제어가 lock 대기 전에 정지 의도를 고정한다."""
        if type(task_id) is not str or not task_id:
            raise ResolutionError(Reason.STATE_MISSING)
        signal = self._stop_requests.setdefault(task_id, _TaskStopSignal())
        signal.reason = reason
        # 새 제어의 확인을 이전 제어의 종결 ACK로 대신하지 않는다.
        signal.terminal_epoch = None
        signal.set()

    def _system_stop_reason(self, task_id: str, epoch: int) -> Reason | None:
        signal = self._system_stop_requests.get((task_id, epoch))
        return signal.reason if signal is not None and signal.is_set() else None

    def _check_stop(self, task_id: str, *, epoch: int | None = None) -> None:
        if epoch is not None:
            reason = self._system_stop_reason(task_id, epoch)
            if reason is not None:
                raise ResolutionError(reason)
        event = self._stop_requests.get(task_id)
        if event is not None and event.is_set():
            raise ResolutionError(event.reason)

    def _policy_matches(self, state: TaskState) -> bool:
        return (
            not self._global_halt
            and state.profile_id == self._profile_id
            and state.policy_version == self._policy_version
        )

    async def _decide(
        self, task_id: str, call: ProposedCall, *, expected_epoch: int | None = None
    ) -> _Allowed | Denied | Pending:
        if self._global_halt:
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
        try:
            loaded = await self._store.load_task(task_id)
        except StoreError as error:
            # 최초 직접 조회의 정상 부재만 호출 단위 거부다. 승인 관계가 이미
            # 남아 있거나 뒤 단계에서 상태를 잃으면 저장 무결성 오류로 유지한다.
            if (
                type(error) is StoreError
                and error.reason is Reason.STATE_MISSING
                and not self._approvals._has_task_context(task_id)
            ):
                return Denied(request_id=None, reasons=(Reason.STATE_MISSING,))
            raise
        try:
            state = TaskState.model_validate(loaded)
        except ValidationError:
            raise StoreError(Reason.STATE_MISSING) from None
        if state.task_id != task_id:
            raise StoreError(Reason.STATE_MISSING)
        if expected_epoch is not None and state.epoch != expected_epoch:
            return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))
        self._check_stop(task_id, epoch=state.epoch)
        if self._global_halt:
            return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))
        if not self._policy_matches(state):
            if not self._approvals._has_task_context(task_id):
                await self._halt_task(task_id, Reason.POLICY_CHANGED)
                return Denied(request_id=None, reasons=(Reason.POLICY_CHANGED,))
            completed = False
            try:
                await self._approvals.invalidate_task(
                    task_id, Reason.POLICY_CHANGED, notify=False, policy_mismatch=True
                )
                await self._halt_task(task_id, Reason.POLICY_CHANGED)
                completed = True
            except StoreError as error:
                self._halt_globally(error.reason)
                raise
            finally:
                if not completed:
                    self._halt_globally()
                    self._approvals._fail_locked_task_pending(task_id)
            self._approvals._publish_task_invalidations(task_id, Reason.POLICY_CHANGED)
            return Denied(request_id=None, reasons=(Reason.POLICY_CHANGED,))
        if state.unclassified:
            return Denied(request_id=None, reasons=(Reason.TASK_UNCLASSIFIED,))
        if state.status is not TaskStatus.RUNNING:
            return Denied(request_id=None, reasons=(Reason.TASK_NOT_RUNNING,))
        call = ProposedCall.model_validate(call)
        request_id = self._request_id_factory()
        try:
            call_seq = await self._next_call_seq(task_id)
        except StoreError as error:
            if type(error) is not StoreError or error.reason not in _TASK_LOCAL_SEQUENCE_REASONS:
                raise
            if error.reason is Reason.INVALID_ARGUMENTS:
                # 현재 Task ID는 위에서 검증했으므로 B allocator의 이 사유는 상한이다.
                await self._halt_task(task_id, error.reason, expected_epoch=state.epoch)
            raise ResolutionError(error.reason) from None
        req = await self._catalog.aresolve(
            state,
            call,
            request_id=request_id,
            call_seq=call_seq,
        )
        decision = await self._evaluate_request(req, state, phase="submit")
        await self._store.record_decision(
            req,
            decision,
            arguments_digest=json_digest(req.arguments.to_dict()),
            at_utc=self._utc_now(),
        )
        self._check_stop(task_id, epoch=state.epoch)
        if decision.decision is Decision.DENY:
            return Denied(request_id=req.request_id, reasons=decision.reasons)
        if decision.decision is Decision.ASK:
            try:
                appr = await self._approvals.open(req, state, decision)
            except StoreIntegrityError:
                self._halt_globally()
                raise
            except StoreError as error:
                if type(error) is StoreError and error.reason in (
                    Reason.APPROVAL_RACE,
                    Reason.APPROVAL_STALE,
                ):
                    return Denied(request_id=req.request_id, reasons=(error.reason,))
                raise
            return Pending(
                request_id=req.request_id,
                approval_id=appr.approval_id,
                reasons=decision.reasons,
            )
        try:
            await self._store.reserve_dispatch(req.request_id, now_utc=self._utc_now())
        except asyncio.CancelledError:
            # 예약 ACK 대기 중 취소는 커밋 전/후를 구별할 수 없어 기존 전역 정지 유지.
            self._halt_globally()
            raise
        return _Allowed(req, state)

    async def _evaluate_request(
        self,
        req: ResolvedRequest,
        state: TaskState,
        *,
        phase: Literal["submit", "approval_recheck"],
    ) -> PolicyDecision:
        self._check_stop(req.task_id, epoch=req.epoch)
        if self._global_halt:
            raise ResolutionError(Reason.INTERNAL_ERROR)
        if not self._policy_matches(state):
            raise ResolutionError(Reason.POLICY_CHANGED)
        registration_status = self._registry.status(req.tool.server_id)
        fingerprint = self._registry.fingerprint(req.tool)
        # 접근 판정 전 로컬 전제조건과 경계 거부를 판정한다.
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
        raw_access: object = self._access_checker.check_access(
            req, TrustedContext(state=state, operator_id=self._operator_id)
        )
        if type(raw_access) is AccessResult:
            access = raw_access
        else:
            # 비동기 구현이 잘못 전달한 코루틴은 실행하지 않고 닫는다.
            if inspect.iscoroutine(raw_access):
                raw_access.close()
            access = AccessResult(allowed=False, reason="", error=True)
        self._check_stop(req.task_id, epoch=req.epoch)
        # 동기 판정 뒤에도 주입된 Registry의 최신 상태와 지문을 다시 확인한다.
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
        on_cancelled: Callable[[Outcome], None] | None = None,
    ) -> Outcome:
        # 예약 후 결과를 예약 전 래퍼로 되돌리지 않는다.
        return await after_dispatch(
            lambda: self._dispatch_and_commit(
                req, state, expires_at_utc=expires_at_utc, monotonic_deadline=monotonic_deadline
            ),
            lambda exc: self._contain_post_dispatch(req, exc, expected_state=state),
            on_containment_error=lambda exc: self._post_failure_fallback(req, exc),
            on_cancelled=on_cancelled,
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
        current = await self._catalog.aresolve(
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
        self._check_stop(req.task_id, epoch=req.epoch)
        raw = await self._transport.call(req)
        system_reason = self._system_stop_reason(req.task_id, req.epoch)
        if system_reason is not None:
            raise ResolutionError(system_reason)
        stop = self._stop_requests.get(req.task_id)
        if stop is not None and stop.is_set() and stop.reason is Reason.POLICY_CHANGED:
            # 정책 전환 중 전송이 끝난 raw는 gate에 넘기지 않는다. after_dispatch가
            # READ 미공개·HALTED / SEND UNKNOWN_OUTCOME을 기존 연산으로 확정한다.
            raise ResolutionError(Reason.POLICY_CHANGED)
        outcome = await self._gate.handle(req, raw, expected_state=state)
        system_reason = self._system_stop_reason(req.task_id, req.epoch)
        if system_reason is not None:
            # 이미 결과 커밋 ACK를 받은 SEND는 성공 여부가 알려져 있다. 재예약·불명
            # 덮어쓰기 없이 정지하며, READ를 포함한 본문은 여기서도 공개하지 않는다.
            await self._halt_task(req.task_id, system_reason, expected_epoch=req.epoch)
            if isinstance(outcome, (Released, Withheld)):
                return Withheld(request_id=req.request_id, reasons=(system_reason,))
        return outcome

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
            self._confirm_terminal_stop(state)
            return state
        halted = checked_status_change(
            await self._store.set_task_status(
                task_id,
                TaskStatus.HALTED,
                expected_row_revision=state.row_revision,
                at_utc=self._utc_now(),
                reason=reason,
            ),
            state,
            TaskStatus.HALTED,
        )
        self._confirm_terminal_stop(halted)
        return halted

    def _halt_globally(self, reason: Reason = Reason.INTERNAL_ERROR) -> None:
        if self._global_halt:
            return
        self._global_halt = True
        if self._on_global_halt is not None:
            self._on_global_halt(reason)

    async def _pre_dispatch_unlocked_failure(
        self, task_id: str, exc: BaseException, *, expected_epoch: int | None
    ) -> Denied:
        # policy/Task lock 대기에서 취소되면 아직 lock이 없다. 진행 중인 예약 후
        # 호출의 상태를 lock 밖에서 덮지 않고, 종결 후 같은 Task만 정리한다.
        if isinstance(exc, asyncio.CancelledError) and not self._global_halt:
            async with self._task_lock(task_id):
                return await self._pre_dispatch_failure(task_id, exc, expected_epoch=expected_epoch)
        return await self._pre_dispatch_failure(task_id, exc, expected_epoch=expected_epoch)

    async def _pre_dispatch_failure(
        self, task_id: str, exc: BaseException, *, expected_epoch: int | None = None
    ) -> Denied:
        if isinstance(exc, StoreError):
            self._halt_globally(exc.reason)
        reason = (
            exc.reason if isinstance(exc, (ResolutionError, StoreError)) else Reason.INTERNAL_ERROR
        )
        if isinstance(exc, asyncio.CancelledError) and not self._global_halt:
            state = TaskState.model_validate(await self._store.load_task(task_id))
            if state.task_id != task_id:
                raise StoreError(Reason.STATE_MISSING)
            # 옛 호출이 lock을 기다리던 동안 교체된 Task 생애를 정지시키지 않는다.
            if expected_epoch is None or state.epoch == expected_epoch:
                await self._halt_task(task_id, reason, expected_epoch=state.epoch)
        await self._failure_audit(
            FailureEvent(
                task_id,
                None,
                "before_dispatch",
                reason,
                _exception_type(exc),
                at_utc=self._utc_now(),
            )
        )
        return Denied(request_id=None, reasons=(reason,))

    def _pre_failure_fallback(self, exc: BaseException) -> Denied:
        self._halt_globally(exc.reason if isinstance(exc, StoreError) else Reason.INTERNAL_ERROR)
        return Denied(request_id=None, reasons=(Reason.INTERNAL_ERROR,))

    async def _contain_post_dispatch(
        self, req: ResolvedRequest, exc: BaseException, *, expected_state: TaskState
    ) -> Outcome:
        is_send = req.tool.kind is ToolKind.SEND
        reason = (
            exc.reason
            if isinstance(exc, ResolutionError)
            and exc.reason in {Reason(item.value) for item in SystemStopReason}
            else Reason.POST_DISPATCH_ERROR
        )
        # Task·요청·내구성 감사부터 커밋한다. 추가 감사 대기/실패가 정지를 늦추면 안 된다.
        contained = checked_task(
            await self._store.commit_request_failure(
                req,
                request_status=RequestStatus.UNKNOWN_OUTCOME if is_send else RequestStatus.WITHHELD,
                task_status=TaskStatus.UNKNOWN_OUTCOME if is_send else TaskStatus.HALTED,
                reason=reason,
                at_utc=self._utc_now(),
                exception_type=_exception_type(exc),
            ),
            task_id=req.task_id,
            epoch=req.epoch,
        )
        preserve_task_context(contained, expected_state, allow_last_read_change=not is_send)
        if (
            contained.task_id != req.task_id
            or contained.epoch != req.epoch
            or contained.status
            is not (TaskStatus.UNKNOWN_OUTCOME if is_send else TaskStatus.HALTED)
        ):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        self._confirm_terminal_stop(contained)
        # 성공 ACK와 반환 맥락을 확인한 뒤에만 별도 감사 연결을 호출한다.
        await self._failure_audit(
            FailureEvent(
                req.task_id,
                req.request_id,
                "after_dispatch",
                reason,
                _exception_type(exc),
                at_utc=self._utc_now(),
            )
        )
        if is_send:
            return OutcomeUnknown(request_id=req.request_id, reasons=(reason,))
        return Withheld(request_id=req.request_id, reasons=(reason,))

    def _post_failure_fallback(self, req: ResolvedRequest, exc: BaseException) -> Outcome:
        self._halt_globally(exc.reason if isinstance(exc, StoreError) else Reason.INTERNAL_ERROR)
        if req.tool.kind is ToolKind.SEND:
            return OutcomeUnknown(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))
        return Withheld(request_id=req.request_id, reasons=(Reason.POST_DISPATCH_ERROR,))


class _ApprovalHost:
    """승인 서비스에만 제공하는 Guard 실행 권한. 앱의 일반 실행 API가 아니다."""

    def __init__(self, guard: GuardService) -> None:
        self._guard = guard

    def task_lock(self, task_id: str) -> asyncio.Lock:
        return self._guard._task_lock(task_id)

    def request_stop(self, task_id: str) -> None:
        self._guard._request_stop(task_id)

    def check_stop(self, task_id: str) -> None:
        # 기존 내부 host 계약은 유지한다. Guard가 보관한 승인 생애로만 결속한다.
        for snapshot in self._guard._approvals._memory_snapshots.values():
            if snapshot.task_id == task_id:
                self._guard._check_stop(task_id, epoch=snapshot.request.epoch)
        self._guard._check_stop(task_id)

    def policy_matches(self, state: TaskState) -> bool:
        return self._guard._policy_matches(state)

    def policy_mismatch_reason(self) -> Reason:
        return Reason.INTERNAL_ERROR if self._guard._global_halt else Reason.POLICY_CHANGED

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
        on_cancelled: Callable[[Outcome], None] | None = None,
    ) -> Outcome:
        return await self._guard._dispatch_reserved(
            req,
            state,
            expires_at_utc=expires_at_utc,
            monotonic_deadline=monotonic_deadline,
            on_cancelled=on_cancelled,
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


def _system_identity(identity: TaskIdentity) -> TaskIdentity:
    if type(identity) is not TaskIdentity:
        raise SystemControlError("시스템 Task 식별자가 유효하지 않다")
    try:
        return TaskIdentity.model_validate(identity)
    except ValidationError:
        raise SystemControlError("시스템 Task 식별자가 유효하지 않다") from None


class SystemControl:
    """STOP-01 합의 전 제안. 신뢰 세션 관리자 전용이며 사람 채널과 별개다."""

    __slots__ = ("_guard", "_audit")

    def __init__(self, guard: GuardService, audit: SessionAudit, key: object) -> None:
        if key is not guard._system_channel_key:
            raise GuardConfigError("시스템 제어 채널은 신뢰 구성 경로에서만 만든다")
        self._guard = guard
        self._audit = audit

    async def stop_task(self, identity: TaskIdentity, reason: SystemStopReason) -> TaskState:
        """lock 대기 전 dispatch 차단. 감사 ACK까지 성공해야 커밋된 상태를 반환한다.

        A는 별도 제어 경로에서 실행하고 transport의 완료/중단도 보장해야 한다.
        예약 후 실행의 Task lock은 결과 확정을 기다리며 SEND 불명은 보존한다.
        반복 호출은 상태를 재전이하지 않고 정지 사실 감사만 추가한다.
        """
        return await self._guard._session_command(
            identity, self._audit, SessionEvent.STOPPED, reason
        )

    async def record_request_user_input(self, identity: TaskIdentity) -> None:
        """Task·epoch·UTC·발생 사실만 기록한다. 질문/응답 원문 입력은 없다."""
        await self._guard._session_command(identity, self._audit, SessionEvent.REQUEST_USER_INPUT)
