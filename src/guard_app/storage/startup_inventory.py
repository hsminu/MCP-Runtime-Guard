"""B 내부의 읽기 전용 v3 점검 자료. 복구 ACK·실행 권한·공유 DTO가 아니다.

SQL과 저장 상태의 검증은 sqlite_store가 수행한다. 빈 이력에 초기 순번을
부여하거나 next/seed를 계산하지 않으며, 미기록 kind/epoch를 추정하지 않는다.
"""

from dataclasses import dataclass
from datetime import datetime

from guard_app.domain.contracts import ApprovalRecord
from guard_app.domain.enums import Decision, RequestStatus, ToolKind
from guard_app.domain.models import TaskState


@dataclass(frozen=True, slots=True, repr=False)
class _StoredTask:
    state: TaskState
    request_sequence_max: int | None
    escalation_sequence_max: int | None

    @property
    def persisted_sequence_max(self) -> int | None:
        """관찰한 기록의 최댓값이며, 사라진 메모리 배정이나 다음 순번이 아니다."""
        observed = (
            value
            for value in (self.request_sequence_max, self.escalation_sequence_max)
            if value is not None
        )
        return max(observed, default=None)


@dataclass(frozen=True, slots=True, repr=False)
class _StoredRequest:
    request_id: str
    task_id: str
    call_seq: int
    status: RequestStatus
    decision: Decision | None
    dispatch_reserved_at_utc: datetime | None
    # legacy는 기록된 NULL을 그대로 둔다. Task/메모리로 대신 채우지 않는다.
    stored_epoch: int | None = None
    stored_kind: ToolKind | None = None
    stored_row_revision: int = 0


@dataclass(frozen=True, slots=True, repr=False)
class _StoredApproval:
    record: ApprovalRecord
    task_id: str


@dataclass(frozen=True, slots=True, repr=False)
class _StartupInventory:
    schema_version: int
    tasks: tuple[_StoredTask, ...]
    requests: tuple[_StoredRequest, ...]
    approvals: tuple[_StoredApproval, ...]
