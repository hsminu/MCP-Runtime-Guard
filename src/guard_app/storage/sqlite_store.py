"""B6: 파일 SQLite와 현행 Store 12개 메서드의 B 단위 구현.

SQL 실행은 이 제품 모듈에만 둔다. 공유 B6 계약 확정·앱 연결·시작 복구는 별도다.
v1의 요청 결속 필드 한계 때문에 실행 권한은 같은 인스턴스에서만 확인한다.
호출자는 신뢰 설정으로 고른 파일 경로와 이미 존재하는 부모 디렉터리를 공급한다.
연결 생성·사용·종료는 같은 백엔드 스레드에서 해야 한다(I6/I9, S03 단위 부분).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import get_ident
from types import TracebackType
from typing import Literal

from guard_app.core.approval_time import approval_event_time
from guard_app.core.taint import merge
from guard_app.domain.contracts import ApprovalRecord
from guard_app.domain.enums import (
    ApprovalStatus,
    Decision,
    Label,
    RequestStatus,
    TaskStatus,
    ToolKind,
)
from guard_app.domain.errors import StoreError
from guard_app.domain.models import Boundary, Escalation, PolicyDecision, ResolvedRequest, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.digest import json_digest
from guard_app.storage.startup_inventory import (
    _StartupInventory,
    _StoredApproval,
    _StoredRequest,
    _StoredTask,
)

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4D435047  # MCPG. 기존 다른 앱의 DB를 자동 채택하지 않는다.
_MAX_SQLITE_INTEGER = (1 << 63) - 1
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

# 11.2절의 일곱 테이블. 새 B6 DTO/epoch/AGT 감사 필드는 합의 전 추가하지 않는다.
# STRICT·CHECK는 저장 형식을 제한하며 정책/상태 전이의 새 의미를 정의하지 않는다.
_SCHEMA: tuple[tuple[str, str], ...] = (
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
    """동기 단일 writer. 현행 Store의 Task·판정·승인·응답 저장을 구현한다.

    파일 DB만 지원하고 생성 시 빈 DB의 v0→v1 마이그레이션을 원자 수행한다.
    같은 앱/버전의 정확한 스키마만 다시 열며 다른/미래 버전은 거부한다.
    busy_timeout_ms=1000은 조정 가능한 잠금 대기 후보이며 전체 I/O 상한이 아니다.
    SQL/검증 오류는 원문·경로 없는 StoreError다. 신뢰 audit_id_factory는 ID 또는
    원문 없는 StoreError를 공급한다. 임의 callback 예외의 일반 변환기는 아니다.
    정상 상태 전이의 보안 버전 유지 등은 검토용 구현 선택이며 B6 합의는 별도다.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = 1000,
        audit_id_factory: Callable[[], str] | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection: sqlite3.Connection | None = None
        self._owner_thread = get_ident()
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
        ):
            raise StoreError()
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
        self._owned_connection()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """소유 스레드에서 연결을 닫는다. 반복 종료는 허용한다."""
        if get_ident() != self._owner_thread:
            raise StoreError()
        if not self._discard_connection():
            raise StoreError()

    async def create_task(self, state: TaskState, *, created_at_utc: datetime) -> None:
        """Task와 제공된 승격 근거를 함께 저장. 중복 ID는 기존 행을 변경하지 않는다."""
        _store_result(lambda: self._create_task(state, created_at_utc))

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
        return _store_result(lambda: self._load_task(task_id))

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
        """B 시작 복구 준비용 내부 조회. 기존 v1 데이터만 읽고 실행 상태를 쓰지 않는다.

        이 반환은 준비/복구 성공 ACK가 아니다. 순번 배정·schema 이관·상태/epoch
        변경과 앱 bootstrap 연결은 SEQ/REC 합의 후다. SQL은 지정 모듈에만 둔다.
        """
        return _store_result(self._inspect_startup_rows)

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
        return _store_result(
            lambda: self._set_task_status(task_id, status, expected_row_revision, at_utc, reason)
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

    async def record_decision(
        self,
        req: ResolvedRequest,
        decision: PolicyDecision,
        *,
        arguments_digest: str,
        at_utc: datetime,
    ) -> None:
        """신뢰 현재 Task와 판정 버전을 검사하고 요청·판정 감사만 원자 저장한다."""
        _store_result(lambda: self._record_decision(req, decision, arguments_digest, at_utc))

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
        _store_result(lambda: self._reserve_dispatch(request_id, now_utc))

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
        _store_result(lambda: self._create_approval(approval))

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
        return _store_result(lambda: self._get_approval(approval_id))

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
        return _store_result(
            lambda: self._transition_approval(
                approval_id,
                status,
                expected_row_revision,
                approver_id,
                now_utc,
                reason,
            )
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
        return _store_result(
            lambda: self._consume_and_reserve(
                approval_id,
                snapshot_hash,
                request_id,
                task_id,
                expected_security_version,
                now_utc,
            )
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
        return _store_result(lambda: self._commit_observation(req, label, at_utc))

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
        _store_result(lambda: self._commit_send_outcome(req, at_utc))

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
        return _store_result(
            lambda: self._commit_request_failure(
                req,
                request_status,
                task_status,
                reason,
                at_utc,
                exception_type,
            )
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
                None
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
            return True
        try:
            connection.close()
        except sqlite3.Error:
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
            # ACK 불명에서는 연결을 폐기한다. 새 공유 오류/Guard 연결은 B6 합의 후다.
            try:
                self._rollback(connection)
            finally:
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
                connection.execute("PRAGMA user_version=1")
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
