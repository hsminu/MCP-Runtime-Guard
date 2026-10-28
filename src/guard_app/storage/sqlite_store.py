"""B6: 파일 SQLite, 현행 Store 12개 연산과 RegistryStore 제안 구현.

SQL 실행은 이 제품 모듈에만 둔다. 공유 B6 계약 확정·앱 연결·시작 복구는 별도다.
v1의 요청 결속 필드 한계 때문에 실행 권한은 같은 인스턴스에서만 확인한다.
호출자는 신뢰 설정으로 고른 파일 경로와 이미 존재하는 부모 디렉터리를 공급한다.
연결 생성·사용·종료는 같은 백엔드 스레드에서 해야 한다(I6/I9, S03 단위 부분).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, get_ident
from types import TracebackType
from typing import Literal

from guard_app.core.approval_time import approval_event_time
from guard_app.core.taint import merge
from guard_app.domain.contracts import (
    ApprovalRecord,
    FingerprintChange,
    RegisteredServer,
    RegistryEntry,
    ScanReport,
    StoredScanReport,
    TaskIdentity,
)
from guard_app.domain.enums import (
    ApprovalStatus,
    CommitOutcome,
    Decision,
    Label,
    RegStatus,
    RequestStatus,
    SessionEvent,
    StorePhase,
    SystemStopReason,
    TaskStatus,
    ToolKind,
    TrustTier,
)
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import (
    Boundary,
    Escalation,
    PolicyDecision,
    ResolvedRequest,
    StoreExecutionState,
    TaskState,
    ToolSpec,
)
from guard_app.domain.reasons import Reason
from guard_app.security.digest import jcs_canonicalize, json_digest
from guard_app.services.catalog_sources import valid_server_id
from guard_app.storage.sqlite_writer import SQLiteWriter
from guard_app.storage.startup_inventory import (
    _StartupInventory,
    _StoredApproval,
    _StoredRequest,
    _StoredTask,
)

SCHEMA_VERSION = 2
APPLICATION_ID = 0x4D435047  # MCPG. 기존 다른 앱의 DB를 자동 채택하지 않는다.
_MAX_SQLITE_INTEGER = (1 << 63) - 1
# 취소 경계는 각 공개 연산의 트랜잭션 유형으로 고정한다.
_METHOD_WRITES = {
    "list_registrations": False,
    "load_registration": False,
    "register_server": True,
    "record_scan_report": True,
    "activate_server": True,
    "record_fingerprint_change": True,
    "block_server": True,
    "request_review": True,
    "save_reviewed_descriptions": True,
    "create_task": True,
    "load_task": False,
    "set_task_status": True,
    "list_tasks": False,
    "record_session_event": True,
    "record_decision": True,
    "reserve_dispatch": True,
    "create_approval": True,
    "get_approval": False,
    "transition_approval": True,
    "consume_and_reserve": True,
    "commit_observation": True,
    "commit_send_outcome": True,
    "commit_request_failure": True,
}
# 전역 정지 뒤 새 실행 권한을 부여하는 작업만 차단한다.
# transition_approval은 APPROVED일 때만 이 표에 따른 차단 대상이다.
_EXECUTION_GATED_METHODS = frozenset(
    {
        "create_task",
        "record_decision",
        "reserve_dispatch",
        "create_approval",
        "consume_and_reserve",
        "transition_approval",
        "activate_server",
    }
)
# B6-06 제안 한계: UTF-8 정규화 후 크기. 등록 UI/검사 결과의 메모리·DB 폭주 제한.
_MAX_SCHEMA_BYTES = 256 * 1024
_MAX_TOOLS_BYTES = 8 * 1024 * 1024
_MAX_REPORT_BYTES = 1024 * 1024
_TASK_TRANSITIONS = {
    TaskStatus.READY: frozenset((TaskStatus.RUNNING, TaskStatus.HALTED)),
    TaskStatus.RUNNING: frozenset(
        (
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.HALTED,
            TaskStatus.UNKNOWN_OUTCOME,
            TaskStatus.CLOSED,
        )
    ),
    TaskStatus.WAITING_APPROVAL: frozenset((TaskStatus.RUNNING, TaskStatus.HALTED)),
    TaskStatus.HALTED: frozenset((TaskStatus.CLOSED,)),
    TaskStatus.UNKNOWN_OUTCOME: frozenset((TaskStatus.CLOSED,)),
    TaskStatus.CLOSED: frozenset(),
}
_TASK_AUDIT_EVENTS = {
    TaskStatus.RUNNING: "TASK_RUNNING",
    TaskStatus.WAITING_APPROVAL: "TASK_WAITING_APPROVAL",
    TaskStatus.HALTED: "HALTED",
    TaskStatus.UNKNOWN_OUTCOME: "TASK_UNKNOWN_OUTCOME",
    TaskStatus.CLOSED: "TASK_CLOSED",
}
_APPROVAL_TRANSITIONS = {
    ApprovalStatus.PENDING: frozenset(
        (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        )
    ),
    ApprovalStatus.APPROVED: frozenset((ApprovalStatus.EXPIRED, ApprovalStatus.INVALIDATED)),
}

# 정확한 기존 v1 문장을 이관 검증용으로 보존한다. Task/요청 열은 v2에서도 같다.
# STRICT·CHECK는 저장 형식을 제한하며 정책/상태 전이의 새 의미를 정의하지 않는다.
_SCHEMA_V1: tuple[tuple[str, str], ...] = (
    (
        "tasks",
        """CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY CHECK (length(task_id) > 0),
            epoch INTEGER NOT NULL CHECK (epoch >= 0),
            profile_id TEXT NOT NULL CHECK (length(profile_id) > 0),
            policy_version TEXT NOT NULL CHECK (length(policy_version) > 0),
            high_watermark INTEGER NOT NULL CHECK (high_watermark IN (0,1,2)),
            last_read_label INTEGER NOT NULL CHECK (last_read_label IN (0,1,2)),
            unclassified INTEGER NOT NULL CHECK (unclassified IN (0,1)),
            status TEXT NOT NULL CHECK (status IN
                ('ready','running','waiting_approval','halted','unknown_outcome','closed')),
            security_version INTEGER NOT NULL CHECK (security_version >= 0),
            row_revision INTEGER NOT NULL CHECK (row_revision >= 0),
            created_at_utc TEXT NOT NULL
        ) STRICT""",
    ),
    (
        "escalations",
        """CREATE TABLE escalations (
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            at_seq INTEGER NOT NULL CHECK (at_seq >= 0),
            source_id TEXT NOT NULL CHECK (length(source_id) > 0),
            from_label INTEGER NOT NULL CHECK (from_label IN (0,1,2)),
            to_label INTEGER NOT NULL CHECK (to_label IN (0,1,2) AND to_label > from_label),
            PRIMARY KEY (task_id, at_seq)
        ) STRICT""",
    ),
    (
        "requests",
        """CREATE TABLE requests (
            request_id TEXT PRIMARY KEY CHECK (length(request_id) > 0),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            call_seq INTEGER NOT NULL CHECK (call_seq >= 0),
            server_id TEXT NOT NULL CHECK (length(server_id) > 0),
            tool_name TEXT NOT NULL CHECK (length(tool_name) > 0),
            destination_id TEXT,
            arguments_digest TEXT NOT NULL CHECK
                (length(arguments_digest) = 64 AND arguments_digest NOT GLOB '*[^0-9a-f]*'),
            decision INTEGER CHECK (decision IN (0,1,2)),
            reasons TEXT,
            status TEXT NOT NULL CHECK (status IN ('DECIDED','WAITING_APPROVAL',
                'DISPATCHING','SUCCEEDED','WITHHELD','FAILED','UNKNOWN_OUTCOME','DENIED')),
            dispatch_reserved_at_utc TEXT,
            UNIQUE (task_id, call_seq)
        ) STRICT""",
    ),
    (
        "approvals",
        """CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY CHECK (length(approval_id) > 0),
            request_id TEXT NOT NULL UNIQUE REFERENCES requests(request_id),
            snapshot_hash TEXT NOT NULL CHECK
                (length(snapshot_hash) = 64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*'),
            approver_id TEXT NOT NULL CHECK (length(approver_id) > 0),
            status TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','CONSUMED',
                'REJECTED','EXPIRED','CANCELLED','INVALIDATED')),
            created_at_utc TEXT NOT NULL,
            expires_at_utc TEXT NOT NULL,
            decided_at_utc TEXT,
            consumed_at_utc TEXT,
            row_revision INTEGER NOT NULL CHECK (row_revision >= 0)
        ) STRICT""",
    ),
    (
        "audit_events",
        """CREATE TABLE audit_events (
            event_id TEXT PRIMARY KEY CHECK (length(event_id) > 0),
            request_id TEXT,
            task_id TEXT,
            event_type TEXT NOT NULL CHECK (length(event_type) > 0),
            reason_code TEXT,
            label INTEGER CHECK (label IN (0,1,2)),
            detail TEXT,
            at_utc TEXT NOT NULL
        ) STRICT""",
    ),
    (
        "registrations",
        """CREATE TABLE registrations (
            server_id TEXT PRIMARY KEY CHECK (length(server_id) > 0),
            status TEXT NOT NULL CHECK (status IN ('pending','active','blocked')),
            tier TEXT NOT NULL CHECK (tier IN
                ('internal','approved_external','general_external')),
            max_label INTEGER NOT NULL CHECK (max_label IN (0,1,2)),
            fingerprints TEXT NOT NULL,
            last_scan_id TEXT,
            reviewed_by TEXT,
            row_revision INTEGER NOT NULL CHECK (row_revision >= 0)
        ) STRICT""",
    ),
    (
        "scan_reports",
        """CREATE TABLE scan_reports (
            scan_id TEXT PRIMARY KEY CHECK (length(scan_id) > 0),
            server_id TEXT NOT NULL,
            report_json TEXT NOT NULL,
            scanned_at_utc TEXT NOT NULL
        ) STRICT""",
    ),
)


_SCHEMA = _SCHEMA_V1[:5] + (
    (
        "registrations",
        """CREATE TABLE registrations (
            server_id TEXT PRIMARY KEY CHECK (length(server_id) > 0),
            status TEXT NOT NULL CHECK (status IN ('pending','active','blocked')),
            boundary_id TEXT NOT NULL CHECK (length(boundary_id) > 0),
            tier TEXT NOT NULL CHECK (tier IN
                ('internal','approved_external','general_external')),
            max_label INTEGER NOT NULL CHECK (max_label IN (0,1,2)),
            last_scan_id TEXT REFERENCES scan_reports(scan_id),
            reviewed_scan_id TEXT REFERENCES scan_reports(scan_id),
            reviewed_by TEXT,
            row_revision INTEGER NOT NULL CHECK (row_revision >= 1),
            updated_at_utc TEXT NOT NULL
        ) STRICT""",
    ),
    (
        "registration_tools",
        """CREATE TABLE registration_tools (
            server_id TEXT NOT NULL REFERENCES registrations(server_id),
            tool_name TEXT NOT NULL CHECK (length(tool_name) > 0),
            tool_position INTEGER NOT NULL CHECK (tool_position >= 0),
            kind TEXT NOT NULL CHECK (kind IN ('read','send','other')),
            destination_arg TEXT,
            resource_arg TEXT,
            input_schema_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL CHECK
                (length(fingerprint) = 64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
            reviewed_description TEXT NOT NULL,
            PRIMARY KEY (server_id, tool_name),
            UNIQUE (server_id, tool_position)
        ) STRICT""",
    ),
    (
        "scan_reports",
        """CREATE TABLE scan_reports (
            scan_id TEXT PRIMARY KEY CHECK (length(scan_id) > 0),
            server_id TEXT NOT NULL REFERENCES registrations(server_id),
            report_json TEXT NOT NULL,
            scanned_at_utc TEXT NOT NULL,
            stored_at_utc TEXT NOT NULL
        ) STRICT""",
    ),
)


@dataclass(frozen=True, slots=True)
class _DecisionContext:
    """같은 인스턴스의 판정 결속. 인자·도구 정의·승인 원문은 보관하지 않는다."""

    state: TaskState
    call_seq: int
    server_id: str
    tool_name: str
    destination_id: str | None
    arguments_digest: str
    reasons_json: str
    decision: Decision
    kind: ToolKind
    fingerprint: str
    resource_id: str | None
    server_boundary: Boundary
    destination: Boundary | None


@dataclass(frozen=True, slots=True)
class _ApprovalContext:
    record: ApprovalRecord
    request: _DecisionContext
    waiting: TaskState


@dataclass(frozen=True, slots=True)
class _DispatchContext:
    request: _DecisionContext
    state: TaskState
    reserved_at: str


class _ConsumeConflictError(Exception):
    """소비 조건 충돌만 구분하며 트랜잭션 전체 rollback 뒤 False로 바꾼다."""


class SQLiteStore:
    """전용 daemon writer로 Task·판정·승인·응답 저장을 구현한다.

    파일 DB만 지원한다. 새 DB는 v2, 정확한 v1은 Registry 두 테이블이 비었을 때만
    v2로 원자 이관한다. RegistryStore도 같은 연결과 트랜잭션 경계를 사용한다.
    같은 앱/버전의 정확한 스키마만 다시 열며 다른/미래 버전은 거부한다.
    busy_timeout_ms=1000은 조정 가능한 잠금 대기 후보이며 전체 I/O 상한이 아니다.
    SQL/검증 오류와 writer 작업의 callback 실패는 원문·경로 없는 StoreError다.
    신뢰 audit_id_factory는 ID 또는 원문 없는 StoreError를 공급한다.
    정상 상태 전이의 보안 버전 유지 등은 검토용 구현 선택이며 B6 합의는 별도다.
    audit_id_factory와 utc_now는 writer 스레드에서 호출된다. 생성자는 연결과
    이관 준비를 동기 대기하며 busy_timeout 및 실제 I/O에 따라 지연될 수 있다.
    전역 정지 시 create_task·record_decision·reserve_dispatch·create_approval·
    consume_and_reserve·APPROVED 승인 전이·activate_server는 거부한다. 읽기,
    Task 상태·예약 후 결과 기록, 다른 승인 전이·세션 감사와 나머지 Registry
    쓰기는 허용한다. 연결 자체가 폐기되면 읽기와 정리도 모두 거부한다.
    READY의 run_id=None은 B6.8 회차 활성화 전 임시 상태다.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 1000,
        audit_id_factory: Callable[[], str] | None = None,
        utc_now: Callable[[], datetime] | None = None,
        max_pending_jobs: int = 1024,
    ) -> None:
        self._connection: sqlite3.Connection | None = None
        self._connection_close_ok = True
        self._state_lock = Lock()
        self._connection_faulted = False
        self._state = StoreExecutionState(
            run_id=None,
            phase=StorePhase.MIGRATING,
            execution_enabled=False,
            state_revision=0,
            reason=None,
            commit_outcome=None,
            last_committed_run_state=None,
            requires_restart=False,
        )
        self._owner_thread = 0
        self._audit_id_factory = audit_id_factory
        self._allowed_decisions: dict[str, _DecisionContext] = {}
        self._request_contexts: dict[str, _DecisionContext] = {}
        self._approval_contexts: dict[str, _ApprovalContext] = {}
        self._dispatch_contexts: dict[str, _DispatchContext] = {}
        self._utc_now = utc_now if utc_now is not None else lambda: datetime.now(UTC)
        if (
            not isinstance(path, (str, Path))
            or str(path) in ("", ":memory:")
            or type(busy_timeout_ms) is not int
            or not 0 <= busy_timeout_ms <= (1 << 31) - 1
            or (audit_id_factory is not None and not callable(audit_id_factory))
            or not callable(self._utc_now)
            or type(max_pending_jobs) is not int
            or max_pending_jobs < 1
        ):
            raise StoreError()
        self._writer = SQLiteWriter(
            lambda: self._open_connection(path, busy_timeout_ms),
            self._discard_connection,
            max_pending_jobs,
        )
        with self._state_lock:
            self._state = self._state.model_copy(
                update={"phase": StorePhase.READY, "execution_enabled": True}
            )

    def _open_connection(self, path: str | Path, busy_timeout_ms: int) -> None:
        self._owner_thread = get_ident()
        failed = False
        try:
            self._connection = sqlite3.connect(
                path,
                timeout=busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=True,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA synchronous=FULL")
        except (sqlite3.Error, OSError, ValueError, OverflowError, UnicodeError):
            failed = True
        if failed:
            self._discard_connection()
            raise StoreError()
        try:
            self._migrate()
            self._configure(busy_timeout_ms)
        except StoreError:
            failed = True
        if failed:
            self._discard_connection()
            raise StoreError()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """동기 사용만 지원한다. 이벤트 루프 안에서 이 컨텍스트 관리자를 쓰지 않는다."""
        self.close()
        self._writer.wait_closed_sync(5.0)

    def close(self) -> None:
        """접수를 즉시 닫고 writer가 진행 중 작업을 끝낸 후 연결을 닫는다."""
        with self._state_lock:
            reason = self._state.reason or Reason.INTERNAL_ERROR
        # writer 큐의 시작 판정과 접수 종료를 같은 Condition으로 직렬화한다.
        # 이미 dequeue된 작업은 완료하고, 대기 중 작업은 시작하지 않는다.
        self._writer.close(reason)
        with self._state_lock:
            if self._state.phase is StorePhase.READY:
                self._state = self._state.model_copy(
                    update={
                        "phase": StorePhase.CLOSED,
                        "execution_enabled": False,
                        "state_revision": self._state.state_revision + 1,
                    }
                )

    def execution_state(self) -> StoreExecutionState:
        """SQL 큐와 독립된 불변 실행 권한 snapshot을 즉시 반환한다."""
        with self._state_lock:
            return self._state

    def halt_execution(self, reason: Reason) -> None:
        """연결이 정상이면 정리 쓰기는 보존하고 새 실행 권한만 닫는다."""
        if not isinstance(reason, Reason):
            reason = Reason.INTERNAL_ERROR
        with self._state_lock:
            if self._state.phase in (StorePhase.FAULTED, StorePhase.CLOSED):
                return
            self._state = self._state.model_copy(
                update={
                    "phase": StorePhase.FAULTED,
                    "execution_enabled": False,
                    "state_revision": self._state.state_revision + 1,
                    "reason": reason,
                    "commit_outcome": None,
                    "requires_restart": True,
                }
            )

    def _connection_fault(self, outcome: CommitOutcome) -> None:
        with self._state_lock:
            previous = self._state
            self._connection_faulted = True
            if previous.phase is StorePhase.FAULTED and previous.commit_outcome is not None:
                return
            self._state = previous.model_copy(
                update={
                    "phase": StorePhase.FAULTED,
                    "execution_enabled": False,
                    "state_revision": previous.state_revision + int(previous.execution_enabled),
                    "reason": previous.reason or Reason.INTERNAL_ERROR,
                    "commit_outcome": outcome,
                    "requires_restart": True,
                }
            )

    def _check_execution_gate(self, *, grants_execution: bool, started: bool = False) -> None:
        with self._state_lock:
            state = self._state
            if (
                (state.phase is StorePhase.CLOSED and not started)
                or self._connection_faulted
                or (
                    grants_execution
                    and not state.execution_enabled
                    and not (started and state.phase is StorePhase.CLOSED)
                )
            ):
                raise StoreError(state.reason or Reason.INTERNAL_ERROR)

    async def wait_closed(self, timeout: float) -> bool:  # noqa: ASYNC109
        """writer의 연결 종료를 이벤트 루프를 막지 않고 기다린다."""
        return await self._writer.wait_closed(timeout)

    async def _submit[T](
        self, operation: Callable[[], T], *, method: str, grants_execution: bool | None = None
    ) -> T:
        if grants_execution is None:
            grants_execution = method in _EXECUTION_GATED_METHODS
        self._check_execution_gate(grants_execution=grants_execution)

        def execute() -> T:
            # 큐에 든 뒤 전역 정지 또는 연결 폐기가 된 작업도 시작하지 않는다.
            self._check_execution_gate(grants_execution=grants_execution, started=True)
            return _store_result(operation)

        return await self._writer.submit(execute, write=_METHOD_WRITES[method])

    def _run_on_writer[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """테스트 전용. 이벤트 루프 밖에서 writer 소유 연결을 사용한다."""
        return self._writer.submit_sync(lambda: operation(self._owned_connection()))

    async def _run_on_writer_async[T](
        self, operation: Callable[[sqlite3.Connection], T], *, write: bool = False
    ) -> T:
        """테스트 전용 비동기 writer 작업."""
        return await self._writer.submit(
            lambda: operation(self._owned_connection()), write=write, test_raw_exceptions=True
        )

    def _test_context_snapshot(
        self,
    ) -> tuple[
        dict[str, _DecisionContext],
        dict[str, _DecisionContext],
        dict[str, _ApprovalContext],
        dict[str, _DispatchContext],
    ]:
        """테스트 전용. 연결을 사용하지 않고 writer 소유 맥락 사본만 반환한다."""
        return self._writer.submit_sync(
            lambda: (
                self._allowed_decisions.copy(),
                self._request_contexts.copy(),
                self._approval_contexts.copy(),
                self._dispatch_contexts.copy(),
            )
        )

    async def list_registrations(self) -> tuple[RegistryEntry, ...]:
        """하나의 읽기 TX 사본. DB ACTIVE는 연결 후 지문 대조를 대체하지 않는다."""

        def read() -> tuple[RegistryEntry, ...]:
            with self._transaction(write=False) as connection:
                rows = connection.execute(
                    "SELECT server_id FROM registrations ORDER BY server_id"
                ).fetchall()
                return tuple(self._require_registration_locked(connection, row[0]) for row in rows)

        return await self._submit(read, method="list_registrations")

    async def load_registration(self, server_id: str) -> RegistryEntry | None:
        def read() -> RegistryEntry | None:
            _registry_input(lambda: _registry_server_id(server_id))
            with self._transaction(write=False) as connection:
                return self._load_registration_locked(connection, server_id)

        return await self._submit(read, method="load_registration")

    async def register_server(
        self, registration: RegisteredServer, *, at_utc: datetime
    ) -> RegistryEntry:
        def write() -> RegistryEntry:
            valid, timestamp = _registry_input(
                lambda: (_new_registration(registration), _utc_text(at_utc))
            )
            with self._transaction(write=True) as connection:
                if self._load_registration_locked(connection, valid.server_id) is not None:
                    raise StoreError(Reason.APPROVAL_RACE)
                return self._write_registry_locked(
                    connection, valid, previous=None, timestamp=timestamp, event="REGISTERED"
                )

        return await self._submit(write, method="register_server")

    async def record_scan_report(
        self, scan_id: str, report: ScanReport, *, at_utc: datetime
    ) -> RegistryEntry:
        def write() -> RegistryEntry:
            valid_id, valid, document, timestamp = _registry_input(
                lambda: (
                    _identifier(scan_id),
                    ScanReport.model_validate(report),
                    _scan_json(report),
                    _utc_text(at_utc),
                )
            )
            _registry_input(lambda: _registry_server_id(valid.server_id))
            with self._transaction(write=True) as connection:
                current = self._require_registration_locked(connection, valid.server_id)
                if (
                    connection.execute(
                        "SELECT 1 FROM scan_reports WHERE scan_id=?", (valid_id,)
                    ).fetchone()
                    is not None
                ):
                    raise StoreError(Reason.APPROVAL_RACE)
                connection.execute(
                    """INSERT INTO scan_reports
                    (scan_id,server_id,report_json,scanned_at_utc,stored_at_utc)
                    VALUES (?,?,?,?,?)""",
                    (
                        valid_id,
                        valid.server_id,
                        document,
                        _utc_text(valid.scanned_at_utc),
                        timestamp,
                    ),
                )
                return self._write_registry_locked(
                    connection,
                    current.registration,
                    previous=current,
                    timestamp=timestamp,
                    event="SCAN_RECORDED",
                    last_scan_id=valid_id,
                    reviewed_scan_id=current.reviewed_scan_id,
                    reviewed_by=current.reviewed_by,
                )

        return await self._submit(write, method="record_scan_report")

    async def activate_server(
        self,
        server_id: str,
        *,
        expected_row_revision: int,
        tool_fingerprints: Mapping[str, str],
        reviewed_by: str,
        at_utc: datetime,
        reviewed_scan_id: str | None = None,
    ) -> RegistryEntry:
        def write() -> RegistryEntry:
            timestamp, fingerprints = _registry_input(
                lambda: (
                    _registry_write_input(
                        server_id, at_utc, expected_row_revision=expected_row_revision
                    ),
                    _fingerprint_map(tool_fingerprints),
                )
            )
            _registry_input(lambda: _identifier(reviewed_by))
            if reviewed_scan_id is not None:
                _registry_input(lambda: _identifier(reviewed_scan_id))
            with self._transaction(write=True) as connection:
                current = self._require_registration_locked(connection, server_id)
                _registry_cas(current, expected_row_revision)
                if current.registration.status is not RegStatus.PENDING:
                    raise StoreError(Reason.REG_NOT_ACTIVE)
                stored = {tool.tool_name: tool.fingerprint for tool in current.registration.tools}
                if fingerprints != stored:
                    raise StoreError(Reason.TOOL_FINGERPRINT_CHANGED)
                if reviewed_scan_id is not None:
                    scan = self._load_scan_locked(connection, reviewed_scan_id)
                    if scan.report.server_id != server_id or set(
                        scan.report.fingerprint_set
                    ) != set(stored.values()):
                        raise StoreError(Reason.TOOL_FINGERPRINT_CHANGED)
                return self._write_registry_locked(
                    connection,
                    current.registration.model_copy(update={"status": RegStatus.ACTIVE}),
                    previous=current,
                    timestamp=timestamp,
                    event="ACTIVATED",
                    last_scan_id=current.last_scan_id,
                    reviewed_scan_id=reviewed_scan_id,
                    reviewed_by=reviewed_by,
                )

        return await self._submit(write, method="activate_server")

    async def record_fingerprint_change(
        self, server_id: str, tools: tuple[ToolSpec, ...], *, at_utc: datetime
    ) -> FingerprintChange:
        """C는 반환 즉시 동기 사본 교체 후 도구별 invalidate_tool을 호출한다.

        변경 없는 관측은 쓰기 없이 현재 저장 사본을 반환하므로 C는 캐시 교체를
        건너뛴다. 내부 await가 없고 트랜잭션 종료 후에만 반환한다. C의 연결
        직렬화/실패 게이트는 별도이며 기존 예약/소비의 S03 경쟁은 수정하지 않는다.
        """

        def write() -> FingerprintChange:
            timestamp, observed = _registry_input(
                lambda: (
                    _registry_write_input(server_id, at_utc),
                    _observed_tools(server_id, tools),
                )
            )
            with self._transaction(write=True) as connection:
                current = self._require_registration_locked(connection, server_id)
                old = {tool.tool_name: tool for tool in current.registration.tools}
                new = {tool.tool_name: tool for tool in observed}
                changed = tuple(
                    sorted(
                        name
                        for name in old.keys() | new.keys()
                        if name not in old
                        or name not in new
                        or _tool_definition(old[name]) != _tool_definition(new[name])
                    )
                )
                if not changed:
                    return FingerprintChange(entry=current, changed_tools=())
                updated = tuple(
                    tool.model_copy(
                        update={
                            "reviewed_description": ""
                            if tool.tool_name in changed
                            else old[tool.tool_name].reviewed_description
                        }
                    )
                    for tool in observed
                )
                status = current.registration.status
                if status is RegStatus.ACTIVE:
                    status = RegStatus.PENDING
                registration = current.registration.model_copy(
                    update={"status": status, "tools": updated}
                )
                result = self._write_registry_locked(
                    connection,
                    registration,
                    previous=current,
                    timestamp=timestamp,
                    event="FINGERPRINT_CHANGED",
                    reason=Reason.TOOL_FINGERPRINT_CHANGED,
                    last_scan_id=None,
                    reviewed_scan_id=None,
                    reviewed_by=None,
                    changed_count=len(changed),
                )
                return FingerprintChange(entry=result, changed_tools=changed)

        return await self._submit(write, method="record_fingerprint_change")

    async def block_server(
        self, server_id: str, *, expected_row_revision: int, at_utc: datetime
    ) -> RegistryEntry:
        return await self._submit(
            lambda: self._registry_transition(
                server_id, expected_row_revision, at_utc, RegStatus.BLOCKED
            ),
            method="block_server",
        )

    async def request_review(
        self, server_id: str, *, expected_row_revision: int, at_utc: datetime
    ) -> RegistryEntry:
        return await self._submit(
            lambda: self._registry_transition(
                server_id, expected_row_revision, at_utc, RegStatus.PENDING
            ),
            method="request_review",
        )

    def _registry_transition(
        self, server_id: str, revision: int, at_utc: datetime, status: RegStatus
    ) -> RegistryEntry:
        timestamp = _registry_input(
            lambda: _registry_write_input(server_id, at_utc, expected_row_revision=revision)
        )
        with self._transaction(write=True) as connection:
            current = self._require_registration_locked(connection, server_id)
            _registry_cas(current, revision)
            allowed = (
                (RegStatus.ACTIVE, RegStatus.PENDING)
                if status is RegStatus.BLOCKED
                else (RegStatus.ACTIVE, RegStatus.BLOCKED)
            )
            if current.registration.status not in allowed:
                raise StoreError(Reason.REG_NOT_ACTIVE)
            return self._write_registry_locked(
                connection,
                current.registration.model_copy(update={"status": status}),
                previous=current,
                timestamp=timestamp,
                event="BLOCKED" if status is RegStatus.BLOCKED else "REVIEW_REQUESTED",
                reason=Reason.REG_NOT_ACTIVE,
                last_scan_id=current.last_scan_id,
                # 재검토/차단 후에는 이전 활성화 확인을 현재 확인으로 표시하지 않는다.
            )

    async def save_reviewed_descriptions(
        self,
        server_id: str,
        descriptions: Mapping[str, str],
        *,
        expected_row_revision: int,
        at_utc: datetime,
    ) -> RegistryEntry:
        def write() -> RegistryEntry:
            timestamp, inputs = _registry_input(
                lambda: (
                    _registry_write_input(
                        server_id, at_utc, expected_row_revision=expected_row_revision
                    ),
                    _description_map(descriptions),
                )
            )
            with self._transaction(write=True) as connection:
                current = self._require_registration_locked(connection, server_id)
                _registry_cas(current, expected_row_revision)
                if current.registration.status is not RegStatus.PENDING:
                    raise StoreError(Reason.REG_NOT_ACTIVE)
                if inputs.keys() - {tool.tool_name for tool in current.registration.tools}:
                    raise StoreError(Reason.INVALID_ARGUMENTS)
                tools = _registry_input(
                    lambda: tuple(
                        tool.model_copy(update={"reviewed_description": inputs[tool.tool_name]})
                        if tool.tool_name in inputs
                        else tool
                        for tool in current.registration.tools
                    )
                )
                _registry_input(lambda: _tool_rows(tools))
                return self._write_registry_locked(
                    connection,
                    current.registration.model_copy(update={"tools": tools}),
                    previous=current,
                    timestamp=timestamp,
                    event="DESCRIPTIONS_REVIEWED",
                    last_scan_id=current.last_scan_id,
                )

        return await self._submit(write, method="save_reviewed_descriptions")

    def _load_scan_locked(self, connection: sqlite3.Connection, scan_id: str) -> StoredScanReport:
        row = connection.execute(
            "SELECT * FROM scan_reports WHERE scan_id=?", (scan_id,)
        ).fetchone()
        if row is None:
            raise StoreError(Reason.STATE_MISSING)
        document = _text(row["report_json"])
        _read_registry_json(document, _MAX_REPORT_BYTES)
        report = ScanReport.model_validate_json(document)
        if (
            _scan_json(report) != document
            or report.server_id != _registry_server_id(row["server_id"])
            or report.scanned_at_utc != _utc_value(row["scanned_at_utc"])
        ):
            raise StoreError()
        return StoredScanReport(
            scan_id=_identifier(row["scan_id"]),
            report=report,
            stored_at_utc=_utc_value(row["stored_at_utc"]),
        )

    def _load_registration_locked(
        self, connection: sqlite3.Connection, server_id: str
    ) -> RegistryEntry | None:
        row = connection.execute(
            "SELECT * FROM registrations WHERE server_id=?", (server_id,)
        ).fetchone()
        if row is None:
            return None
        _registry_server_id(row["server_id"])
        tool_rows = connection.execute(
            "SELECT * FROM registration_tools WHERE server_id=? ORDER BY tool_position",
            (server_id,),
        ).fetchall()
        tools: list[ToolSpec] = []
        for position, tool in enumerate(tool_rows):
            if _integer(tool["tool_position"]) != position:
                raise StoreError()
            tools.append(
                ToolSpec(
                    server_id=server_id,
                    tool_name=_identifier(tool["tool_name"]),
                    kind=ToolKind(_text(tool["kind"])),
                    destination_arg=tool["destination_arg"],
                    resource_arg=tool["resource_arg"],
                    input_schema=FrozenJsonObject(
                        _read_registry_json(tool["input_schema_json"], _MAX_SCHEMA_BYTES)
                    ),
                    fingerprint=_sha256(tool["fingerprint"]),
                    reviewed_description=_text(tool["reviewed_description"]),
                )
            )
        registration = RegisteredServer(
            server_id=server_id,
            status=RegStatus(_text(row["status"])),
            boundary=Boundary(
                boundary_id=_identifier(row["boundary_id"]),
                tier=TrustTier(_text(row["tier"])),
                max_label=Label(_integer(row["max_label"])),
            ),
            tools=tuple(tools),
            row_revision=_integer(row["row_revision"]),
        )
        _tool_rows(registration.tools)  # 합계 크기 제한과 저장 DTO 형식도 재검사한다.
        last_scan = (
            None
            if row["last_scan_id"] is None
            else self._load_scan_locked(connection, _identifier(row["last_scan_id"]))
        )
        reviewed_scan_id = row["reviewed_scan_id"]
        if reviewed_scan_id is not None:
            reviewed = self._load_scan_locked(connection, _identifier(reviewed_scan_id))
            if reviewed.report.server_id != server_id:
                raise StoreError()
        return RegistryEntry(
            registration=registration,
            last_scan=last_scan,
            reviewed_scan_id=reviewed_scan_id,
            reviewed_by=row["reviewed_by"],
            updated_at_utc=_utc_value(row["updated_at_utc"]),
        )

    def _require_registration_locked(
        self, connection: sqlite3.Connection, server_id: str
    ) -> RegistryEntry:
        result = self._load_registration_locked(connection, server_id)
        if result is None:
            raise StoreError(Reason.STATE_MISSING)
        return result

    def _write_registry_locked(
        self,
        connection: sqlite3.Connection,
        registration: RegisteredServer,
        *,
        previous: RegistryEntry | None,
        timestamp: str,
        event: str,
        reason: Reason | None = None,
        last_scan_id: str | None = None,
        reviewed_scan_id: str | None = None,
        reviewed_by: str | None = None,
        changed_count: int = 0,
    ) -> RegistryEntry:
        revision = _integer(1 if previous is None else previous.registration.row_revision + 1)
        values = (
            registration.status.value,
            registration.boundary.boundary_id,
            registration.boundary.tier.value,
            int(registration.boundary.max_label),
            last_scan_id,
            reviewed_scan_id,
            reviewed_by,
            revision,
            timestamp,
        )
        if previous is None:
            connection.execute(
                """INSERT INTO registrations (server_id,status,boundary_id,tier,max_label,
                last_scan_id,reviewed_scan_id,reviewed_by,row_revision,updated_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (registration.server_id, *values),
            )
        else:
            changed = connection.execute(
                """UPDATE registrations SET status=?,boundary_id=?,tier=?,max_label=?,
                last_scan_id=?,reviewed_scan_id=?,reviewed_by=?,row_revision=?,updated_at_utc=?
                WHERE server_id=? AND row_revision=?""",
                (*values, registration.server_id, previous.registration.row_revision),
            ).rowcount
            if changed != 1:
                raise StoreError(Reason.APPROVAL_RACE)
        connection.execute(
            "DELETE FROM registration_tools WHERE server_id=?", (registration.server_id,)
        )
        connection.executemany(
            """INSERT INTO registration_tools (server_id,tool_name,tool_position,kind,
            destination_arg,resource_arg,input_schema_json,fingerprint,reviewed_description)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            _tool_rows(registration.tools),
        )
        self._insert_registry_audit(
            connection,
            registration.server_id,
            revision,
            event,
            reason,
            timestamp,
            last_scan_id,
            len(registration.tools),
            changed_count,
        )
        # 검증된 사본까지 TX 안에서 만든다. COMMIT 실패이면 호출자에게 전달되지 않는다.
        return self._require_registration_locked(connection, registration.server_id)

    def _insert_registry_audit(
        self,
        connection: sqlite3.Connection,
        server_id: str,
        revision: int,
        event: str,
        reason: Reason | None,
        timestamp: str,
        scan_id: str | None,
        tool_count: int,
        changed_count: int,
    ) -> None:
        if self._audit_id_factory is None:
            raise StoreError()
        event_id = _identifier(self._audit_id_factory())
        detail = json.dumps(
            {
                "server_id": server_id,
                "row_revision": revision,
                "scan_id": scan_id,
                "tool_count": tool_count,
                "changed_count": changed_count,
            },
            separators=(",", ":"),
        )
        connection.execute(
            """INSERT INTO audit_events
            (event_id,request_id,task_id,event_type,reason_code,label,detail,at_utc)
            VALUES (?,NULL,NULL,?,?,NULL,?,?)""",
            (
                event_id,
                "REGISTRY_" + event,
                None if reason is None else reason.value,
                detail,
                timestamp,
            ),
        )

    async def create_task(self, state: TaskState, *, created_at_utc: datetime) -> None:
        """Task와 제공된 승격 근거를 함께 저장. 중복 ID는 기존 행을 변경하지 않는다."""
        await self._submit(lambda: self._create_task(state, created_at_utc), method="create_task")

    def _create_task(self, state: TaskState, created_at_utc: datetime) -> None:
        self._owned_connection()
        valid: TaskState | None = None
        timestamp: str | None = None
        try:
            valid = TaskState.model_validate(state)
            timestamp = _utc_text(created_at_utc)
            _check_task(valid)
        except (TypeError, ValueError, OverflowError):
            valid = None
        if valid is None or timestamp is None:
            raise StoreError()
        with self._transaction(write=True) as connection:
            connection.execute(
                """INSERT INTO tasks (task_id, epoch, profile_id, policy_version,
                    high_watermark, last_read_label, unclassified, status,
                    security_version, row_revision, created_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    valid.task_id,
                    valid.epoch,
                    valid.profile_id,
                    valid.policy_version,
                    int(valid.high_watermark),
                    int(valid.last_read_label),
                    int(valid.unclassified),
                    valid.status.value,
                    valid.security_version,
                    valid.row_revision,
                    timestamp,
                ),
            )
            connection.executemany(
                """INSERT INTO escalations (task_id, at_seq, source_id, from_label, to_label)
                    VALUES (?, ?, ?, ?, ?)""",
                (
                    (
                        valid.task_id,
                        item.at_seq,
                        item.source_id,
                        int(item.from_label),
                        int(item.to_label),
                    )
                    for item in valid.escalations
                ),
            )

    async def load_task(self, task_id: str) -> TaskState:
        """같은 읽기 트랜잭션에서 Task와 승격 근거를 조회. 없는 Task는 STATE_MISSING."""
        return await self._submit(lambda: self._load_task(task_id), method="load_task")

    def _load_task(self, task_id: str) -> TaskState:
        self._owned_connection()
        _identifier(task_id)
        with self._transaction(write=False) as connection:
            return self._load_task_locked(connection, task_id)

    def _load_task_locked(self, connection: sqlite3.Connection, task_id: str) -> TaskState:
        row = connection.execute(
            """SELECT task_id, epoch, profile_id, policy_version, high_watermark,
                    last_read_label, unclassified, status, security_version, row_revision,
                    created_at_utc FROM tasks WHERE task_id=?""",
            (task_id,),
        ).fetchone()
        if row is None:
            raise StoreError(Reason.STATE_MISSING)
        history = connection.execute(
            """SELECT at_seq, source_id, from_label, to_label FROM escalations
                    WHERE task_id=? ORDER BY at_seq""",
            (task_id,),
        ).fetchall()
        result = _decode_task(row, history)
        if result.task_id != task_id:
            raise StoreError()
        return result

    def _inspect_startup(self) -> _StartupInventory:
        """B 시작 복구 준비용 내부 조회. v2의 Task/요청만 읽고 실행 상태를 쓰지 않는다.

        이 반환은 준비/복구 성공 ACK가 아니다. 순번 배정·schema 이관·상태/epoch
        변경과 앱 bootstrap 연결은 SEQ/REC 합의 후다. SQL은 지정 모듈에만 둔다.
        """
        self._check_execution_gate(grants_execution=False)

        def read() -> _StartupInventory:
            self._check_execution_gate(grants_execution=False, started=True)
            return _store_result(self._inspect_startup_rows)

        return self._writer.submit_sync(read)

    def _inspect_startup_rows(self) -> _StartupInventory:
        connection = self._owned_connection()
        if connection.in_transaction:
            # 기존 쓰기 TX를 BEGIN 실패 처리로 rollback하지 않는다.
            raise StoreError()
        with self._transaction(write=False):
            version = _integer(connection.execute("PRAGMA user_version").fetchone()[0])
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            entries = connection.execute(
                "SELECT name, sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            ).fetchall()
            if (
                version != SCHEMA_VERSION
                or application != APPLICATION_ID
                or {row[0]: row[1] for row in entries} != dict(_SCHEMA)
            ):
                raise StoreError()
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise StoreError(Reason.STATE_MISSING)

            task_rows = connection.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
            history_rows = connection.execute(
                """SELECT task_id, at_seq, source_id, from_label, to_label
                FROM escalations ORDER BY task_id, at_seq"""
            ).fetchall()
            histories: dict[str, list[sqlite3.Row]] = {}
            for row in history_rows:
                histories.setdefault(_identifier(row["task_id"]), []).append(row)
            states: dict[str, TaskState] = {}
            for row in task_rows:
                task_id = _identifier(row["task_id"])
                if task_id in states:
                    raise StoreError(Reason.STATE_MISSING)
                states[task_id] = _decode_task(row, histories.get(task_id, []))
            if histories.keys() - states.keys():
                raise StoreError(Reason.STATE_MISSING)

            requests: dict[str, _StoredRequest] = {}
            request_sequences: set[tuple[str, int]] = set()
            request_maxima: dict[str, int] = {}
            for row in connection.execute("SELECT * FROM requests ORDER BY request_id").fetchall():
                stored = _decode_stored_request(row)
                sequence_key = (stored.task_id, stored.call_seq)
                if (
                    stored.task_id not in states
                    or stored.request_id in requests
                    or sequence_key in request_sequences
                ):
                    raise StoreError(Reason.STATE_MISSING)
                requests[stored.request_id] = stored
                request_sequences.add(sequence_key)
                request_maxima[stored.task_id] = max(
                    request_maxima.get(stored.task_id, stored.call_seq), stored.call_seq
                )

            approvals: list[_StoredApproval] = []
            approved_requests: set[str] = set()
            for row in connection.execute(
                "SELECT approval_id FROM approvals ORDER BY approval_id"
            ).fetchall():
                record = self._approval_locked(connection, _identifier(row["approval_id"]))
                if (
                    record is None
                    or record.request_id not in requests
                    or record.request_id in approved_requests
                ):
                    raise StoreError(Reason.STATE_MISSING)
                approved_requests.add(record.request_id)
                approvals.append(_StoredApproval(record, requests[record.request_id].task_id))

            return _StartupInventory(
                schema_version=version,
                tasks=tuple(
                    _StoredTask(
                        state=state,
                        request_sequence_max=request_maxima.get(task_id),
                        escalation_sequence_max=max(
                            (item.at_seq for item in state.escalations), default=None
                        ),
                    )
                    for task_id, state in states.items()
                ),
                requests=tuple(requests.values()),
                approvals=tuple(approvals),
            )

    async def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        expected_row_revision: int,
        at_utc: datetime,
        reason: Reason,
    ) -> TaskState:
        """Task revision CAS와 허용 전이·감사를 원자 저장. 실제 변경에서만 버전 증가."""
        return await self._submit(
            lambda: self._set_task_status(task_id, status, expected_row_revision, at_utc, reason),
            method="set_task_status",
        )

    def _set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        expected_revision: int,
        at_utc: datetime,
        reason: Reason,
    ) -> TaskState:
        _identifier(task_id)
        _integer(expected_revision)
        timestamp = _utc_text(at_utc)
        if not isinstance(status, TaskStatus) or not isinstance(reason, Reason):
            raise StoreError()
        with self._transaction(write=True) as connection:
            current = self._load_task_locked(connection, task_id)
            if current.row_revision != expected_revision:
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            if current.unclassified and status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                raise StoreError(Reason.TASK_UNCLASSIFIED)
            if status is current.status:
                return current
            if status not in _TASK_TRANSITIONS[current.status]:
                raise StoreError(Reason.TASK_NOT_RUNNING)
            # 3.4/7.3·현재 호출부의 검토용 선택. 정상 승인 대기는 security_version 유지.
            terminal = status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED)
            updated = current.model_copy(
                update={
                    "status": status,
                    "row_revision": _integer(current.row_revision + 1),
                    "security_version": _integer(current.security_version + int(terminal)),
                }
            )
            changed = connection.execute(
                """UPDATE tasks SET status=?,security_version=?,row_revision=?
                    WHERE task_id=? AND row_revision=? AND status=?""",
                (
                    status.value,
                    updated.security_version,
                    updated.row_revision,
                    task_id,
                    expected_revision,
                    current.status.value,
                ),
            ).rowcount
            if changed != 1:
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            self._insert_audit(
                connection,
                _TASK_AUDIT_EVENTS[status],
                task_id=task_id,
                request_id=None,
                reason=reason,
                timestamp=timestamp,
            )
        self._allowed_decisions = {
            key: value
            for key, value in self._allowed_decisions.items()
            if value.state.task_id != task_id
        }
        return updated

    async def list_tasks(self) -> tuple[TaskState, ...]:
        """정책 변경의 전체 Task 조회 공급자. 기존 Store 포트·스키마는 변경하지 않는다.

        Guard.apply_policy_change(task_source=store.list_tasks)로 연결한다.
        단일 읽기 트랜잭션에서 현재 Task를 복원하며 상태를 변경하지 않는다.
        """
        return await self._submit(self._list_tasks, method="list_tasks")

    async def record_session_event(
        self,
        identity: TaskIdentity,
        event: SessionEvent,
        *,
        at_utc: datetime,
        reason: SystemStopReason | None = None,
    ) -> None:
        """STOP-01 합의 전 제안: 사실 감사 ACK. 기존 스키마·Store 포트는 유지한다."""
        return await self._submit(
            lambda: self._record_session_event(identity, event, at_utc, reason),
            method="record_session_event",
        )

    def _record_session_event(
        self,
        identity: TaskIdentity,
        event: SessionEvent,
        at_utc: datetime,
        reason: SystemStopReason | None,
    ) -> None:
        if type(identity) is not TaskIdentity or type(event) is not SessionEvent:
            raise StoreError(Reason.INVALID_ARGUMENTS)
        identity = TaskIdentity.model_validate(identity)
        if (event is SessionEvent.STOPPED and type(reason) is not SystemStopReason) or (
            event is SessionEvent.REQUEST_USER_INPUT and reason is not None
        ):
            raise StoreError(Reason.INVALID_ARGUMENTS)
        timestamp = _utc_text(at_utc)
        with self._transaction(write=True) as connection:
            state = self._load_task_locked(connection, identity.task_id)
            if state.epoch != identity.epoch:
                raise StoreError(Reason.STATE_MISSING)
            if event is SessionEvent.STOPPED and state.status not in (
                TaskStatus.HALTED,
                TaskStatus.UNKNOWN_OUTCOME,
                TaskStatus.CLOSED,
            ):
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            self._insert_audit(
                connection,
                event.value,
                task_id=identity.task_id,
                request_id=None,
                reason=Reason.OK if reason is None else Reason(reason.value),
                timestamp=timestamp,
                session_epoch=identity.epoch,
            )

    def _list_tasks(self) -> tuple[TaskState, ...]:
        with self._transaction(write=False) as connection:
            rows = connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall()
            return tuple(self._load_task_locked(connection, row["task_id"]) for row in rows)

    async def record_decision(
        self,
        req: ResolvedRequest,
        decision: PolicyDecision,
        *,
        arguments_digest: str,
        at_utc: datetime,
    ) -> None:
        """신뢰 현재 Task와 판정 버전을 검사하고 요청·판정 감사만 원자 저장한다."""
        await self._submit(
            lambda: self._record_decision(req, decision, arguments_digest, at_utc),
            method="record_decision",
        )

    def _record_decision(
        self,
        req: ResolvedRequest,
        decision: PolicyDecision,
        arguments_digest: str,
        at_utc: datetime,
    ) -> None:
        req = ResolvedRequest.model_validate(req)
        decision = PolicyDecision.model_validate(decision)
        timestamp = _utc_text(at_utc)
        _integer(req.call_seq)
        _integer(req.epoch)
        if type(arguments_digest) is not str or arguments_digest != json_digest(
            req.arguments.to_dict()
        ):
            raise StoreError(Reason.INVALID_ARGUMENTS)
        reasons_json = json.dumps(
            [reason.value for reason in decision.reasons], separators=(",", ":")
        )
        destination_id = None if req.destination is None else req.destination.boundary_id
        with self._transaction(write=True) as connection:
            current = self._load_task_locked(connection, req.task_id)
            if current.status is not TaskStatus.RUNNING or current.unclassified:
                raise StoreError(Reason.TASK_NOT_RUNNING)
            if current.epoch != req.epoch:
                raise StoreError(Reason.STATE_MISSING)
            if (current.policy_version, current.security_version) != (
                decision.policy_version,
                decision.security_version,
            ):
                raise StoreError(Reason.POLICY_CHANGED)
            request_status = (
                RequestStatus.DENIED
                if decision.decision is Decision.DENY
                else RequestStatus.DECIDED
            )
            connection.execute(
                """INSERT INTO requests (request_id,task_id,call_seq,server_id,tool_name,
                    destination_id,arguments_digest,decision,reasons,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    req.request_id,
                    req.task_id,
                    req.call_seq,
                    req.tool.server_id,
                    req.tool.tool_name,
                    destination_id,
                    arguments_digest,
                    int(decision.decision),
                    reasons_json,
                    request_status.value,
                ),
            )
            self._insert_audit(
                connection,
                "DECIDED",
                task_id=req.task_id,
                request_id=req.request_id,
                reason=decision.reasons[0],
                timestamp=timestamp,
            )
        context = _DecisionContext(
            current,
            req.call_seq,
            req.tool.server_id,
            req.tool.tool_name,
            destination_id,
            arguments_digest,
            reasons_json,
            decision.decision,
            req.tool.kind,
            req.tool.fingerprint,
            req.resource_id,
            req.server_boundary,
            req.destination,
        )
        if decision.decision is not Decision.DENY:
            self._request_contexts[req.request_id] = context
        if decision.decision is Decision.ALLOW:
            self._allowed_decisions[req.request_id] = context

    async def reserve_dispatch(self, request_id: str, *, now_utc: datetime) -> None:
        """현 인스턴스의 검증된 ALLOW만 예약. DB 예약은 실제 도구 전송이 아니다."""
        await self._submit(
            lambda: self._reserve_dispatch(request_id, now_utc), method="reserve_dispatch"
        )

    def _reserve_dispatch(self, request_id: str, now_utc: datetime) -> None:
        _identifier(request_id)
        timestamp = _utc_text(now_utc)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                """SELECT task_id,call_seq,server_id,tool_name,destination_id,arguments_digest,
                    decision,reasons,status,dispatch_reserved_at_utc
                    FROM requests WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if row is None:
                raise StoreError(Reason.STATE_MISSING)
            allowed = self._allowed_decisions.get(request_id)
            if (
                allowed is None
                or row["status"] != RequestStatus.DECIDED.value
                or row["decision"] != int(Decision.ALLOW)
                or row["dispatch_reserved_at_utc"] is not None
                or allowed.kind not in (ToolKind.READ, ToolKind.SEND)
            ):
                raise StoreError(Reason.TASK_NOT_RUNNING)
            expected = (
                allowed.state.task_id,
                allowed.call_seq,
                allowed.server_id,
                allowed.tool_name,
                allowed.destination_id,
                allowed.arguments_digest,
                int(Decision.ALLOW),
                allowed.reasons_json,
            )
            actual = tuple(
                row[key]
                for key in (
                    "task_id",
                    "call_seq",
                    "server_id",
                    "tool_name",
                    "destination_id",
                    "arguments_digest",
                    "decision",
                    "reasons",
                )
            )
            if actual != expected:
                raise StoreError(Reason.STATE_MISSING)
            current = self._load_task_locked(connection, allowed.state.task_id)
            if (
                current != allowed.state
                or current.status is not TaskStatus.RUNNING
                or current.unclassified
            ):
                raise StoreError(Reason.POLICY_CHANGED)
            if (
                connection.execute(
                    """UPDATE requests SET status=?,dispatch_reserved_at_utc=?
                    WHERE request_id=? AND task_id=? AND status=? AND decision=?
                        AND dispatch_reserved_at_utc IS NULL""",
                    (
                        RequestStatus.DISPATCHING.value,
                        timestamp,
                        request_id,
                        current.task_id,
                        RequestStatus.DECIDED.value,
                        int(Decision.ALLOW),
                    ),
                ).rowcount
                != 1
            ):
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            self._insert_audit(
                connection,
                "DISPATCH_RESERVED",
                task_id=current.task_id,
                request_id=request_id,
                reason=Reason.OK,
                timestamp=timestamp,
            )
        self._allowed_decisions.pop(request_id, None)
        self._dispatch_contexts[request_id] = _DispatchContext(allowed, current, timestamp)

    async def create_approval(self, approval: ApprovalRecord) -> None:
        """PENDING 승인·ASK 요청 WAITING·감사를 함께 저장. Task 전이는 기존 별도 호출."""
        await self._submit(lambda: self._create_approval(approval), method="create_approval")

    def _create_approval(self, approval: ApprovalRecord) -> None:
        approval = ApprovalRecord.model_validate(approval)
        if approval.status is not ApprovalStatus.PENDING or approval.row_revision != 0:
            raise StoreError(Reason.APPROVAL_STALE)
        with self._transaction(write=True) as connection:
            context = self._request_contexts.get(approval.request_id)
            if (
                context is None
                or context.decision is not Decision.ASK
                or context.kind not in (ToolKind.READ, ToolKind.SEND)
            ):
                raise StoreError(Reason.APPROVAL_STALE)
            row = self._request_row_locked(connection, approval.request_id, context)
            if (
                row["status"] != RequestStatus.DECIDED.value
                or row["dispatch_reserved_at_utc"] is not None
            ):
                raise StoreError(Reason.APPROVAL_STALE)
            waiting = self._load_task_locked(connection, context.state.task_id)
            expected = context.state.model_copy(
                update={
                    "status": TaskStatus.WAITING_APPROVAL,
                    "row_revision": _integer(context.state.row_revision + 1),
                }
            )
            now = self._fresh_utc()
            if (
                waiting != expected
                or waiting.unclassified
                or not approval.created_at_utc <= now < approval.expires_at_utc
            ):
                raise StoreError(Reason.APPROVAL_STALE)
            connection.execute(
                """INSERT INTO approvals (approval_id,request_id,snapshot_hash,approver_id,status,
                    created_at_utc,expires_at_utc,decided_at_utc,consumed_at_utc,row_revision)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval.approval_id,
                    approval.request_id,
                    approval.snapshot_hash,
                    approval.approver_id,
                    approval.status.value,
                    _utc_text(approval.created_at_utc),
                    _utc_text(approval.expires_at_utc),
                    None,
                    None,
                    approval.row_revision,
                ),
            )
            self._request_status_locked(
                connection,
                approval.request_id,
                RequestStatus.DECIDED,
                RequestStatus.WAITING_APPROVAL,
            )
            self._insert_audit(
                connection,
                "APPROVAL_PENDING",
                task_id=waiting.task_id,
                request_id=approval.request_id,
                reason=Reason.OK,
                timestamp=_utc_text(now),
            )
            final_now = self._fresh_utc()
            if final_now >= approval.expires_at_utc or final_now < now:
                raise StoreError(Reason.APPROVAL_STALE)
        self._approval_contexts[approval.approval_id] = _ApprovalContext(approval, context, waiting)

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """검증된 승인 메타데이터 조회. 재개방 조회는 실행 결속/원문을 복원하지 않는다."""
        return await self._submit(lambda: self._get_approval(approval_id), method="get_approval")

    def _get_approval(self, approval_id: str) -> ApprovalRecord | None:
        _identifier(approval_id)
        with self._transaction(write=False) as connection:
            return self._approval_locked(connection, approval_id)

    def _approval_locked(
        self, connection: sqlite3.Connection, approval_id: str
    ) -> ApprovalRecord | None:
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        result = ApprovalRecord(
            approval_id=_identifier(row["approval_id"]),
            request_id=_identifier(row["request_id"]),
            snapshot_hash=_sha256(row["snapshot_hash"]),
            approver_id=_identifier(row["approver_id"]),
            status=ApprovalStatus(_text(row["status"])),
            created_at_utc=_utc_value(row["created_at_utc"]),
            expires_at_utc=_utc_value(row["expires_at_utc"]),
            decided_at_utc=None
            if row["decided_at_utc"] is None
            else _utc_value(row["decided_at_utc"]),
            consumed_at_utc=None
            if row["consumed_at_utc"] is None
            else _utc_value(row["consumed_at_utc"]),
            row_revision=_integer(row["row_revision"]),
        )
        if (
            result.approval_id != approval_id
            or connection.execute(
                """SELECT 1 FROM requests r JOIN tasks t ON t.task_id=r.task_id
                WHERE r.request_id=?""",
                (result.request_id,),
            ).fetchone()
            is None
        ):
            raise StoreError(Reason.STATE_MISSING)
        return result

    async def transition_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        expected_row_revision: int,
        approver_id: str,
        now_utc: datetime,
        reason: Reason,
    ) -> ApprovalRecord:
        """8.2 단방향 전이·revision/주체·관계를 검사. 승인 종결은 Task를 재개하지 않는다."""
        return await self._submit(
            lambda: self._transition_approval(
                approval_id,
                status,
                expected_row_revision,
                approver_id,
                now_utc,
                reason,
            ),
            method="transition_approval",
            grants_execution=status is ApprovalStatus.APPROVED,
        )

    def _transition_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        expected_revision: int,
        approver_id: str,
        now_utc: datetime,
        reason: Reason,
    ) -> ApprovalRecord:
        _identifier(approval_id)
        _identifier(approver_id)
        _integer(expected_revision)
        _utc_text(now_utc)
        if not isinstance(status, ApprovalStatus) or not isinstance(reason, Reason):
            raise StoreError()
        with self._transaction(write=True) as connection:
            record = self._approval_locked(connection, approval_id)
            context = self._approval_contexts.get(approval_id)
            if (
                record is None
                or context is None
                or record != context.record
                or record.row_revision != expected_revision
                or approver_id != record.approver_id
                or status not in _APPROVAL_TRANSITIONS.get(record.status, frozenset())
            ):
                raise StoreError(Reason.APPROVAL_RACE)
            row = self._request_row_locked(connection, record.request_id, context.request)
            current = self._load_task_locked(connection, context.waiting.task_id)
            if (
                row["status"] != RequestStatus.WAITING_APPROVAL.value
                or current.epoch != context.waiting.epoch
            ):
                raise StoreError(Reason.APPROVAL_STALE)
            now = max(now_utc, self._fresh_utc())
            if status is ApprovalStatus.APPROVED and (
                current != context.waiting or current.unclassified or now >= record.expires_at_utc
            ):
                raise StoreError(Reason.APPROVAL_STALE)
            event_time = approval_event_time(record, now)
            updated = record.model_copy(
                update={
                    "status": status,
                    "decided_at_utc": event_time,
                    "row_revision": _integer(record.row_revision + 1),
                }
            )
            if (
                connection.execute(
                    """UPDATE approvals SET status=?,decided_at_utc=?,row_revision=?
                    WHERE approval_id=? AND request_id=? AND row_revision=? AND status=?""",
                    (
                        status.value,
                        _utc_text(event_time),
                        updated.row_revision,
                        approval_id,
                        record.request_id,
                        expected_revision,
                        record.status.value,
                    ),
                ).rowcount
                != 1
            ):
                raise StoreError(Reason.APPROVAL_RACE)
            if status is not ApprovalStatus.APPROVED:
                self._request_status_locked(
                    connection,
                    record.request_id,
                    RequestStatus.WAITING_APPROVAL,
                    RequestStatus.DENIED,
                )
            self._insert_audit(
                connection,
                "APPROVAL_" + status.name,
                task_id=current.task_id,
                request_id=record.request_id,
                reason=reason,
                timestamp=_utc_text(event_time),
                system_expiry=(
                    status is ApprovalStatus.EXPIRED and reason is Reason.APPROVAL_STALE
                ),
            )
            if status is ApprovalStatus.APPROVED:
                final_now = max(now_utc, self._fresh_utc())
                if final_now >= record.expires_at_utc or final_now < now:
                    raise StoreError(Reason.APPROVAL_STALE)
        if status is ApprovalStatus.APPROVED:
            self._approval_contexts[approval_id] = _ApprovalContext(
                updated, context.request, context.waiting
            )
        else:
            self._approval_contexts.pop(approval_id, None)
            self._drop_request(record.request_id)
        return updated

    async def consume_and_reserve(
        self,
        *,
        approval_id: str,
        snapshot_hash: str,
        request_id: str,
        task_id: str,
        expected_security_version: int,
        now_utc: datetime,
    ) -> bool:
        """승인·요청·Task·감사를 원자 소비/예약. 조건 충돌은 전체 rollback 뒤 정확한 False."""
        return await self._submit(
            lambda: self._consume_and_reserve(
                approval_id,
                snapshot_hash,
                request_id,
                task_id,
                expected_security_version,
                now_utc,
            ),
            method="consume_and_reserve",
        )

    def _consume_and_reserve(
        self,
        approval_id: str,
        snapshot_hash: str,
        request_id: str,
        task_id: str,
        expected_security_version: int,
        now_utc: datetime,
    ) -> bool:
        _identifier(approval_id)
        _identifier(request_id)
        _identifier(task_id)
        _sha256(snapshot_hash)
        _integer(expected_security_version)
        _utc_text(now_utc)
        try:
            with self._transaction(write=True) as connection:
                record = self._approval_locked(connection, approval_id)
                context = self._approval_contexts.get(approval_id)
                if (
                    record is None
                    or context is None
                    or record != context.record
                    or record.status is not ApprovalStatus.APPROVED
                    or record.request_id != request_id
                    or record.snapshot_hash != snapshot_hash
                    or context.waiting.task_id != task_id
                ):
                    raise _ConsumeConflictError()
                row = self._request_row_locked(
                    connection, request_id, context.request, consuming=True
                )
                try:
                    current = self._load_task_locked(connection, task_id)
                except StoreError as error:
                    if error.reason is Reason.STATE_MISSING:
                        raise _ConsumeConflictError() from None
                    raise
                now = max(now_utc, self._fresh_utc())
                if (
                    row["status"] != RequestStatus.WAITING_APPROVAL.value
                    or row["dispatch_reserved_at_utc"] is not None
                    or current != context.waiting
                    or current.unclassified
                    or current.security_version != expected_security_version
                    or record.decided_at_utc is None
                    or not record.decided_at_utc <= now < record.expires_at_utc
                ):
                    raise _ConsumeConflictError()
                consumed = record.model_copy(
                    update={
                        "status": ApprovalStatus.CONSUMED,
                        "consumed_at_utc": now,
                        "row_revision": _integer(record.row_revision + 1),
                    }
                )
                if (
                    connection.execute(
                        """UPDATE approvals SET status=?,consumed_at_utc=?,row_revision=?
                        WHERE approval_id=? AND request_id=? AND status=? AND row_revision=?""",
                        (
                            consumed.status.value,
                            _utc_text(now),
                            consumed.row_revision,
                            approval_id,
                            request_id,
                            ApprovalStatus.APPROVED.value,
                            record.row_revision,
                        ),
                    ).rowcount
                    != 1
                ):
                    raise _ConsumeConflictError()
                self._request_status_locked(
                    connection,
                    request_id,
                    RequestStatus.WAITING_APPROVAL,
                    RequestStatus.DISPATCHING,
                    reserved_at=_utc_text(now),
                    consuming=True,
                )
                running = self._write_task_locked(
                    connection,
                    current,
                    current.model_copy(update={"status": TaskStatus.RUNNING}),
                    consuming=True,
                )
                self._insert_audit(
                    connection,
                    "CONSUMED",
                    task_id=task_id,
                    request_id=request_id,
                    reason=Reason.OK,
                    timestamp=_utc_text(now),
                )
                final_now = max(now_utc, self._fresh_utc())
                if final_now >= record.expires_at_utc or final_now < now:
                    raise _ConsumeConflictError()
        except _ConsumeConflictError:
            return False
        self._approval_contexts.pop(approval_id, None)
        self._dispatch_contexts[request_id] = _DispatchContext(
            context.request, running, _utc_text(now)
        )
        return True

    async def commit_observation(
        self,
        req: ResolvedRequest,
        label: Label | None,
        *,
        at_utc: datetime,
    ) -> TaskState:
        """READ 병합·승격·감사·요청 상태를 원자 커밋. 본문은 입력/저장하지 않는다."""
        return await self._submit(
            lambda: self._commit_observation(req, label, at_utc), method="commit_observation"
        )

    def _commit_observation(
        self, req: ResolvedRequest, label: Label | None, at_utc: datetime
    ) -> TaskState:
        req = ResolvedRequest.model_validate(req)
        timestamp = _utc_text(at_utc)
        if label is not None and not isinstance(label, Label):
            raise StoreError()
        with self._transaction(write=True) as connection:
            dispatched, current = self._dispatched_locked(connection, req)
            if dispatched.request.kind is not ToolKind.READ or current != dispatched.state:
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            merged = merge(
                current,
                label,
                req.resource_id or f"{req.tool.server_id}/{req.tool.tool_name}",
                req.call_seq,
            )
            updated = self._write_task_locked(connection, current, merged)
            for item in updated.escalations[len(current.escalations) :]:
                connection.execute(
                    """INSERT INTO escalations (task_id,at_seq,source_id,from_label,to_label)
                    VALUES (?,?,?,?,?)""",
                    (
                        req.task_id,
                        item.at_seq,
                        item.source_id,
                        int(item.from_label),
                        int(item.to_label),
                    ),
                )
            self._request_status_locked(
                connection,
                req.request_id,
                RequestStatus.DISPATCHING,
                RequestStatus.SUCCEEDED if label is not None else RequestStatus.WITHHELD,
            )
            self._insert_audit(
                connection,
                "OBSERVED",
                task_id=req.task_id,
                request_id=req.request_id,
                reason=Reason.OK if label is not None else Reason.RESULT_UNCLASSIFIED,
                timestamp=timestamp,
                label=label,
            )
        self._drop_request(req.request_id)
        return updated

    async def commit_send_outcome(self, req: ResolvedRequest, *, at_utc: datetime) -> None:
        """SEND 성공의 요청·감사만 커밋. Task 등급/상태/버전은 변경하지 않는다."""
        await self._submit(
            lambda: self._commit_send_outcome(req, at_utc), method="commit_send_outcome"
        )

    def _commit_send_outcome(self, req: ResolvedRequest, at_utc: datetime) -> None:
        req = ResolvedRequest.model_validate(req)
        timestamp = _utc_text(at_utc)
        with self._transaction(write=True) as connection:
            dispatched, current = self._dispatched_locked(connection, req)
            if dispatched.request.kind is not ToolKind.SEND or current != dispatched.state:
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            self._request_status_locked(
                connection, req.request_id, RequestStatus.DISPATCHING, RequestStatus.SUCCEEDED
            )
            self._insert_audit(
                connection,
                "SEND_COMPLETED",
                task_id=req.task_id,
                request_id=req.request_id,
                reason=Reason.OK,
                timestamp=timestamp,
            )
        self._drop_request(req.request_id)

    async def commit_request_failure(
        self,
        req: ResolvedRequest,
        *,
        request_status: Literal[
            RequestStatus.FAILED, RequestStatus.WITHHELD, RequestStatus.UNKNOWN_OUTCOME
        ],
        task_status: Literal[TaskStatus.RUNNING, TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME],
        reason: Reason,
        at_utc: datetime,
        exception_type: str | None = None,
    ) -> TaskState:
        """예약 후 실패의 종류별 상태 쌍·원자 감사. 종결 Task 재개와 등급 하락 금지."""
        return await self._submit(
            lambda: self._commit_request_failure(
                req,
                request_status,
                task_status,
                reason,
                at_utc,
                exception_type,
            ),
            method="commit_request_failure",
        )

    def _commit_request_failure(
        self,
        req: ResolvedRequest,
        request_status: RequestStatus,
        task_status: TaskStatus,
        reason: Reason,
        at_utc: datetime,
        exception_type: str | None,
    ) -> TaskState:
        req = ResolvedRequest.model_validate(req)
        timestamp = _utc_text(at_utc)
        if (
            not isinstance(reason, Reason)
            or not isinstance(request_status, RequestStatus)
            or not isinstance(task_status, TaskStatus)
        ):
            raise StoreError()
        if exception_type is not None and (
            type(exception_type) is not str
            or not exception_type.isidentifier()
            or not exception_type.isascii()
            or len(exception_type) > 128
        ):
            raise StoreError()
        allowed_pairs = {
            ToolKind.READ: {
                (RequestStatus.FAILED, TaskStatus.RUNNING),
                (RequestStatus.WITHHELD, TaskStatus.HALTED),
            },
            ToolKind.SEND: {(RequestStatus.UNKNOWN_OUTCOME, TaskStatus.UNKNOWN_OUTCOME)},
        }
        if (request_status, task_status) not in allowed_pairs.get(req.tool.kind, set()):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        with self._transaction(write=True) as connection:
            dispatched, current = self._dispatched_locked(connection, req)
            original = dispatched.state
            terminal = current.status in (
                TaskStatus.HALTED,
                TaskStatus.UNKNOWN_OUTCOME,
                TaskStatus.CLOSED,
            )
            if (terminal and current.status is not task_status) or (
                not terminal and current != original
            ):
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            if (
                current.profile_id != original.profile_id
                or current.policy_version != original.policy_version
                or current.high_watermark < original.high_watermark
                or current.security_version < original.security_version
                or (original.unclassified and not current.unclassified)
            ):
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            changed = current.status is not task_status
            candidate = current.model_copy(
                update={
                    "status": task_status,
                    "security_version": _integer(current.security_version + int(changed)),
                }
            )
            updated = self._write_task_locked(connection, current, candidate)
            self._request_status_locked(
                connection, req.request_id, RequestStatus.DISPATCHING, request_status
            )
            # 예외 메시지는 받지 않는다. 검증된 ASCII 타입명만 고정 JSON 필드로 저장한다.
            self._insert_audit(
                connection,
                "REQUEST_" + request_status.name,
                task_id=req.task_id,
                request_id=req.request_id,
                reason=reason,
                timestamp=timestamp,
                exception_type=exception_type,
            )
        self._drop_request(req.request_id)
        return updated

    def _fresh_utc(self) -> datetime:
        value = self._utc_now()
        _utc_text(value)
        return value

    def _request_row_locked(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        context: _DecisionContext,
        *,
        consuming: bool = False,
    ) -> sqlite3.Row:
        row: sqlite3.Row | None = connection.execute(
            "SELECT * FROM requests WHERE request_id=?", (request_id,)
        ).fetchone()
        expected = (
            context.state.task_id,
            context.call_seq,
            context.server_id,
            context.tool_name,
            context.destination_id,
            context.arguments_digest,
            int(context.decision),
            context.reasons_json,
        )
        if (
            row is None
            or tuple(
                row[key]
                for key in (
                    "task_id",
                    "call_seq",
                    "server_id",
                    "tool_name",
                    "destination_id",
                    "arguments_digest",
                    "decision",
                    "reasons",
                )
            )
            != expected
        ):
            if consuming:
                raise _ConsumeConflictError()
            raise StoreError(Reason.STATE_MISSING)
        return row

    def _dispatched_locked(
        self,
        connection: sqlite3.Connection,
        req: ResolvedRequest,
    ) -> tuple[_DispatchContext, TaskState]:
        dispatched = self._dispatch_contexts.get(req.request_id)
        if dispatched is None:
            raise StoreError(Reason.STATE_MISSING)
        context = dispatched.request
        if (
            req.task_id != context.state.task_id
            or req.epoch != context.state.epoch
            or req.call_seq != context.call_seq
            or req.tool.server_id != context.server_id
            or req.tool.tool_name != context.tool_name
            or req.tool.kind is not context.kind
            or req.tool.fingerprint != context.fingerprint
            or req.resource_id != context.resource_id
            or req.server_boundary != context.server_boundary
            or req.destination != context.destination
            or json_digest(req.arguments.to_dict()) != context.arguments_digest
        ):
            raise StoreError(Reason.STATE_MISSING)
        row = self._request_row_locked(connection, req.request_id, context)
        current = self._load_task_locked(connection, req.task_id)
        if (
            row["status"] != RequestStatus.DISPATCHING.value
            or row["dispatch_reserved_at_utc"] != dispatched.reserved_at
            or current.epoch != req.epoch
        ):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        return dispatched, current

    def _request_status_locked(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        previous: RequestStatus,
        target: RequestStatus,
        *,
        reserved_at: str | None = None,
        consuming: bool = False,
    ) -> None:
        if reserved_at is None:
            count = connection.execute(
                "UPDATE requests SET status=? WHERE request_id=? AND status=?",
                (target.value, request_id, previous.value),
            ).rowcount
        else:
            count = connection.execute(
                """UPDATE requests SET status=?,dispatch_reserved_at_utc=?
                WHERE request_id=? AND status=?""",
                (target.value, reserved_at, request_id, previous.value),
            ).rowcount
        if count != 1:
            if consuming:
                raise _ConsumeConflictError()
            raise StoreError(Reason.STATE_COMMIT_FAILED)

    def _write_task_locked(
        self,
        connection: sqlite3.Connection,
        old: TaskState,
        new: TaskState,
        *,
        consuming: bool = False,
    ) -> TaskState:
        if new == old:
            return old
        if (
            new.task_id != old.task_id
            or new.epoch != old.epoch
            or new.profile_id != old.profile_id
            or new.policy_version != old.policy_version
            or new.high_watermark < old.high_watermark
            or (old.unclassified and not new.unclassified)
            or new.security_version < old.security_version
        ):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        new = new.model_copy(update={"row_revision": _integer(old.row_revision + 1)})
        _check_task(new)
        if (
            connection.execute(
                """UPDATE tasks SET high_watermark=?,last_read_label=?,unclassified=?,
                    status=?,security_version=?,row_revision=?
                WHERE task_id=? AND epoch=? AND row_revision=?""",
                (
                    int(new.high_watermark),
                    int(new.last_read_label),
                    int(new.unclassified),
                    new.status.value,
                    _integer(new.security_version),
                    new.row_revision,
                    old.task_id,
                    old.epoch,
                    old.row_revision,
                ),
            ).rowcount
            != 1
        ):
            if consuming:
                raise _ConsumeConflictError()
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        return new

    def _drop_request(self, request_id: str) -> None:
        self._allowed_decisions.pop(request_id, None)
        self._request_contexts.pop(request_id, None)
        self._dispatch_contexts.pop(request_id, None)

    def _insert_audit(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        *,
        task_id: str,
        request_id: str | None,
        reason: Reason,
        timestamp: str,
        label: Label | None = None,
        exception_type: str | None = None,
        system_expiry: bool = False,
        session_epoch: int | None = None,
    ) -> None:
        if self._audit_id_factory is None:
            raise StoreError()
        event_id = _identifier(self._audit_id_factory())
        connection.execute(
            """INSERT INTO audit_events (event_id,request_id,task_id,event_type,reason_code,
                label,detail,at_utc) VALUES (?,?,?,?,?,?,?,?)""",
            (
                event_id,
                request_id,
                task_id,
                event_type,
                reason.value,
                None if label is None else int(label),
                json.dumps({"epoch": session_epoch}, separators=(",", ":"))
                if session_epoch is not None
                else json.dumps({"source": "guard_expiry"}, separators=(",", ":"))
                if system_expiry
                else None
                if exception_type is None
                else json.dumps({"exception_type": exception_type}, separators=(",", ":")),
                timestamp,
            ),
        )

    def _owned_connection(self) -> sqlite3.Connection:
        if self._connection is None or get_ident() != self._owner_thread:
            raise StoreError()
        return self._connection

    def _discard_connection(self) -> bool:
        connection, self._connection = self._connection, None
        self._allowed_decisions.clear()
        self._request_contexts.clear()
        self._approval_contexts.clear()
        self._dispatch_contexts.clear()
        if connection is None:
            return self._connection_close_ok
        try:
            connection.close()
        except sqlite3.Error:
            # 폐기 과정에서 연결 종료 실패를 잃으면 wait_closed가 거짓 ACK를 낸다.
            self._connection_close_ok = False
            return False
        return True

    def _rollback(self, connection: sqlite3.Connection) -> None:
        failed = False
        try:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        except sqlite3.Error:
            failed = True
        if failed:
            # COMMIT을 아직 보내지 않았으므로 rollback 실패여도 해당 작업은 미커밋이다.
            self._connection_fault(CommitOutcome.ROLLED_BACK)
            self._discard_connection()
            raise StoreError()

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection = self._owned_connection()
        failed = False
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        except sqlite3.Error:
            failed = True
        if failed:
            self._rollback(connection)
            raise StoreError()
        completed = False
        reason: Reason | None = None
        try:
            try:
                yield connection
            except StoreError as error:
                reason = error.reason
            except sqlite3.Error:
                reason = Reason.INTERNAL_ERROR
            else:
                completed = True
        finally:
            if not completed:
                self._rollback(connection)
        if reason is not None:
            raise StoreError(reason)
        try:
            connection.execute("COMMIT")
        except sqlite3.Error:
            failed = True
        if failed:
            # COMMIT 뒤 이미 TX가 없으면 실제 커밋인지 ACK 유실인지 구분할 수 없다.
            outcome = CommitOutcome.UNKNOWN
            # ROLLBACK도 실패하면 결과 불명으로 두고 연결을 버린다(Semgrep 예외, 약점 대장).
            try:  # nosemgrep: no-swallow
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                    if not connection.in_transaction:
                        outcome = CommitOutcome.ROLLED_BACK
            except sqlite3.Error:
                pass
            self._connection_fault(outcome)
            self._discard_connection()
            raise StoreError()

    def _migrate(self) -> None:
        with self._transaction(write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            entries = connection.execute(
                "SELECT name, sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            ).fetchall()
            if version == 0:
                if application != 0 or entries:
                    raise StoreError()
                for _, statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA application_id=0x4D435047")
                connection.execute("PRAGMA user_version=2")
            elif version == 1:
                if (
                    application != APPLICATION_ID
                    or {row[0]: row[1] for row in entries} != dict(_SCHEMA_V1)
                    or connection.execute("SELECT 1 FROM registrations LIMIT 1").fetchone()
                    is not None
                    or connection.execute("SELECT 1 FROM scan_reports LIMIT 1").fetchone()
                    is not None
                ):
                    raise StoreError()
                # BEGIN IMMEDIATE 안에서 검사·교체·버전 갱신. 다른 다섯 테이블은 보존.
                connection.execute("DROP TABLE scan_reports")
                connection.execute("DROP TABLE registrations")
                for _, statement in _SCHEMA[5:]:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=2")
            elif (
                version != SCHEMA_VERSION
                or application != APPLICATION_ID
                or {row[0]: row[1] for row in entries} != dict(_SCHEMA)
            ):
                raise StoreError()

    def _configure(self, busy_timeout_ms: int) -> None:
        connection = self._owned_connection()
        failed = False
        try:
            journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            failed = (journal, foreign_keys, synchronous, timeout) != (
                "wal",
                1,
                2,
                busy_timeout_ms,
            )
            failed = (
                failed
                or connection.isolation_level is not None
                or connection.autocommit != sqlite3.LEGACY_TRANSACTION_CONTROL
            )
        except sqlite3.Error:
            failed = True
        if failed:
            raise StoreError()


def _registry_input[T](operation: Callable[[], T]) -> T:
    """새 Registry 입력만 INVALID_ARGUMENTS로 번역. 기존 Store 오류 의미는 유지."""
    invalid = False
    try:
        result = operation()
    except (TypeError, ValueError, OverflowError):
        invalid = True
    if invalid:
        raise StoreError(Reason.INVALID_ARGUMENTS)
    return result


def _registry_server_id(value: object) -> str:
    result = _identifier(value)
    if not valid_server_id(result):
        raise ValueError("invalid registry server identifier")
    return result


def _registry_write_input(
    server_id: str, at_utc: datetime, *, expected_row_revision: int = 0
) -> str:
    _registry_server_id(server_id)
    _integer(expected_row_revision)
    return _utc_text(at_utc)


def _registry_cas(current: RegistryEntry, expected: int) -> None:
    if current.registration.row_revision != expected:
        raise StoreError(Reason.APPROVAL_RACE)


def _registry_json(value: object, maximum: int) -> str:
    encoded = jcs_canonicalize(value)
    if len(encoded) > maximum:
        raise ValueError("registry JSON exceeds storage limit")
    return encoded.decode("utf-8")


def _read_registry_json(value: object, maximum: int) -> object:
    document = _text(value)
    if len(document.encode("utf-8")) > maximum:
        raise StoreError()
    failed = False
    try:
        decoded = json.loads(document, parse_int=_registry_json_integer)
    except RecursionError:
        failed = True
    if failed:
        raise StoreError()
    if _registry_json(decoded, maximum) != document:
        raise StoreError()
    return decoded


def _registry_json_integer(value: str) -> int | float:
    """JCS는 1e20 같은 binary64도 정수 표기로 쓴다. 큰 정수 표기를 되읽는다.

    쓰기 입력의 unsafe Python int 거부는 그대로다. JCS 출력의 숫자만 binary64로
    복원하고 정규화 바이트 일치로 손상을 검사한다(파서의 기본 int 오해 방지).
    """
    integer = int(value)
    return integer if -(2**53 - 1) <= integer <= 2**53 - 1 else float(value)


def _tool_definition(tool: ToolSpec) -> dict[str, object]:
    return tool.model_dump(mode="json", exclude={"reviewed_description"})


def _tool_rows(tools: tuple[ToolSpec, ...]) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    total = 0
    for position, tool in enumerate(tools):
        _registry_server_id(tool.server_id)
        _sha256(tool.fingerprint)
        schema = _registry_json(tool.input_schema.to_dict(), _MAX_SCHEMA_BYTES)
        # 이름·종류·인자·스키마·지문·검토 설명을 포함한 전체 메타데이터 상한.
        total += len(jcs_canonicalize(tool.model_dump(mode="json")))
        if total > _MAX_TOOLS_BYTES:
            raise ValueError("registry tool metadata exceeds storage limit")
        rows.append(
            (
                tool.server_id,
                tool.tool_name,
                position,
                tool.kind.value,
                tool.destination_arg,
                tool.resource_arg,
                schema,
                tool.fingerprint,
                tool.reviewed_description,
            )
        )
    return tuple(rows)


def _new_registration(value: RegisteredServer) -> RegisteredServer:
    valid = RegisteredServer.model_validate(value)
    _registry_server_id(valid.server_id)
    if valid.status is not RegStatus.PENDING or valid.row_revision != 0:
        raise ValueError("new registration requires initial pending state")
    if any(tool.reviewed_description for tool in valid.tools):
        raise ValueError("reviewed descriptions require a separate trusted review")
    _tool_rows(valid.tools)
    return valid


def _observed_tools(server_id: str, values: tuple[ToolSpec, ...]) -> tuple[ToolSpec, ...]:
    if type(values) is not tuple:
        raise ValueError("observed tools require a tuple")
    tools = tuple(ToolSpec.model_validate(value) for value in values)
    if any(tool.server_id != server_id for tool in tools) or len(tools) != len(
        {tool.tool_name for tool in tools}
    ):
        raise ValueError("observed tools require unique names and matching server")
    _tool_rows(tools)
    return tools


def _scan_json(value: ScanReport) -> str:
    valid = ScanReport.model_validate(value)
    _registry_server_id(valid.server_id)
    if any(finding.description for finding in valid.findings):
        # C4.17 예외는 threat_summary뿐이다. SDK 원문 description은 저장하지 않는다.
        raise ValueError("raw finding descriptions cannot be persisted")
    return _registry_json(valid.model_dump(mode="json"), _MAX_REPORT_BYTES)


def _fingerprint_map(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("fingerprints require a mapping")
    return {_identifier(name): _sha256(fingerprint) for name, fingerprint in value.items()}


def _description_map(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("reviewed descriptions require a mapping")
    return {_identifier(name): _text(description) for name, description in value.items()}


def _store_result[T](operation: Callable[[], T]) -> T:
    """contextmanager에서 건너온 오류도 공개 경계 밖에서 원문 없는 오류로 다시 만든다."""
    reason: Reason | None = None
    try:
        result = operation()
    except StoreError as error:
        reason = error.reason
    except (TypeError, ValueError, OverflowError):
        reason = Reason.INTERNAL_ERROR
    if reason is not None:
        raise StoreError(reason)
    return result


def _utc_text(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return value.isoformat(timespec="microseconds")


def _utc_value(value: object) -> datetime:
    text = _text(value)
    parsed = datetime.fromisoformat(text)
    if _utc_text(parsed) != text:
        raise ValueError("invalid stored UTC timestamp")
    return parsed


def _sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("invalid snapshot digest")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SQLITE_INTEGER:
        raise ValueError("invalid stored integer")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or not value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("invalid identifier")
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("invalid stored text")
    return value


def _check_task(state: TaskState) -> None:
    for number in (state.epoch, state.security_version, state.row_revision):
        _integer(number)
    previous: Escalation | None = None
    for item in state.escalations:
        _integer(item.at_seq)
        if (
            item.to_label <= item.from_label
            or item.to_label > state.high_watermark
            or (
                previous is not None
                and (item.at_seq <= previous.at_seq or item.from_label != previous.to_label)
            )
        ):
            raise ValueError("invalid escalation history")
        previous = item


def _decode_stored_request(row: sqlite3.Row) -> _StoredRequest:
    """v1 문자열/정수/시각을 기존 타입으로 검사하고 상태 조합은 추정하지 않는다."""
    _identifier(row["server_id"])
    _identifier(row["tool_name"])
    if row["destination_id"] is not None:
        _identifier(row["destination_id"])
    _sha256(row["arguments_digest"])
    decision = None if row["decision"] is None else Decision(_integer(row["decision"]))
    if row["reasons"] is not None:
        try:
            reasons = json.loads(_text(row["reasons"]))
        except RecursionError:
            raise StoreError() from None
        if type(reasons) is not list or not reasons:
            raise StoreError()
        for reason in reasons:
            Reason(_text(reason))
    return _StoredRequest(
        request_id=_identifier(row["request_id"]),
        task_id=_identifier(row["task_id"]),
        call_seq=_integer(row["call_seq"]),
        status=RequestStatus(_text(row["status"])),
        decision=decision,
        dispatch_reserved_at_utc=None
        if row["dispatch_reserved_at_utc"] is None
        else _utc_value(row["dispatch_reserved_at_utc"]),
    )


def _decode_task(row: sqlite3.Row, history: list[sqlite3.Row]) -> TaskState:
    result: TaskState | None = None
    try:
        timestamp = _text(row["created_at_utc"])
        if _utc_text(datetime.fromisoformat(timestamp)) != timestamp:
            raise ValueError("invalid stored timestamp")
        unclassified = _integer(row["unclassified"])
        if unclassified not in (0, 1):
            raise ValueError("invalid stored flag")
        result = TaskState(
            task_id=_text(row["task_id"]),
            epoch=_integer(row["epoch"]),
            profile_id=_text(row["profile_id"]),
            policy_version=_text(row["policy_version"]),
            high_watermark=Label(_integer(row["high_watermark"])),
            last_read_label=Label(_integer(row["last_read_label"])),
            unclassified=bool(unclassified),
            status=TaskStatus(_text(row["status"])),
            security_version=_integer(row["security_version"]),
            row_revision=_integer(row["row_revision"]),
            escalations=tuple(
                Escalation(
                    at_seq=_integer(item["at_seq"]),
                    source_id=_text(item["source_id"]),
                    from_label=Label(_integer(item["from_label"])),
                    to_label=Label(_integer(item["to_label"])),
                )
                for item in history
            ),
        )
        _check_task(result)
    except (TypeError, ValueError, OverflowError):
        result = None
    if result is None:
        raise StoreError()
    return result
