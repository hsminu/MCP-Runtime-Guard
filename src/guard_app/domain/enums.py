"""기술설계서 3.2·8.2·11.2절의 상태와 순서 계약."""

from enum import IntEnum, StrEnum


class Label(IntEnum):
    """자료의 기밀성 순서."""

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2


class Decision(IntEnum):
    """큰 값을 선택하면 가장 엄격한 판정이 된다."""

    ALLOW = 0
    ASK = 1
    DENY = 2


class TrustTier(StrEnum):
    """주소나 접속 방식과 분리된 운영자 신뢰 경계."""

    INTERNAL = "internal"
    APPROVED_EXTERNAL = "approved_external"
    GENERAL_EXTERNAL = "general_external"


class ToolKind(StrEnum):
    """등록된 설정이 정하는 도구 종류."""

    READ = "read"
    SEND = "send"
    OTHER = "other"


class RegStatus(StrEnum):
    """저장된 서버 등록 상태."""

    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"


class TaskStatus(StrEnum):
    """Task 실행과 복구 금지 상태."""

    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    HALTED = "halted"
    UNKNOWN_OUTCOME = "unknown_outcome"
    CLOSED = "closed"


class SystemStopReason(StrEnum):
    """STOP-01 합의 전 제안. 신뢰 세션 관리자가 고르는 정지 사유."""

    L3_VIOLATION = "SESSION_L3_VIOLATION"
    WATCHDOG_EXCEEDED = "SESSION_WATCHDOG_EXCEEDED"
    AUTH_FAILED = "SESSION_AUTH_FAILED"
    AUTH_REFRESH_CONFLICT = "SESSION_AUTH_REFRESH_CONFLICT"
    USAGE_LIMIT = "SESSION_USAGE_LIMIT"
    PROCESS_FAILED = "SESSION_PROCESS_FAILED"
    PROTOCOL_FAILED = "SESSION_PROTOCOL_FAILED"


class SessionEvent(StrEnum):
    """STOP-01 합의 전 제안. 원문 없는 세션 감사의 사실 종류."""

    STOPPED = "SESSION_STOPPED"
    REQUEST_USER_INPUT = "SESSION_REQUEST_USER_INPUT"


class ApprovalStatus(StrEnum):
    """승인 저장소의 단방향 상태 값."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"


class RequestStatus(StrEnum):
    """요청 예약과 결과 저장 상태."""

    DECIDED = "DECIDED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DISPATCHING = "DISPATCHING"
    SUCCEEDED = "SUCCEEDED"
    WITHHELD = "WITHHELD"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    DENIED = "DENIED"
