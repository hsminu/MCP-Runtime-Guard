"""기술설계서 3.3절의 공통 모델 초안. 합의·동결 여부는 계약 문서에 기록한다."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from guard_app.domain.enums import (
    CommitOutcome,
    Decision,
    Label,
    RecoveryCategory,
    RegStatus,
    RequestStatus,
    StorePhase,
    TaskStatus,
    ToolKind,
    TrustTier,
)
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.domain.text import ReviewedDescription

__all__ = [
    "AccessResult",
    "Arguments",
    "Boundary",
    "BoundaryVerdict",
    "Escalation",
    "FailureAuditRecord",
    "Frozen",
    "FrozenJsonObject",
    "MAX_SAFE_SEQUENCE",
    "MigrationReport",
    "PolicyDecision",
    "PolicyInput",
    "ProposedCall",
    "RecoveryItem",
    "RecoveryItemPage",
    "RecoveryReport",
    "ResolvedRequest",
    "StrictDecision",
    "StrictLabel",
    "StoreExecutionState",
    "TaskState",
    "ToolSpec",
]

MAX_SAFE_SEQUENCE = 2**53 - 1

NonEmptyString = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _exact_json_enum_integer(value: object, info: ValidationInfo) -> object:
    if info.mode == "json" and type(value) is not int:
        raise ValueError("등급·판정의 JSON 값은 정확한 정수여야 한다")
    return value


StrictLabel = Annotated[Label, BeforeValidator(_exact_json_enum_integer)]
StrictDecision = Annotated[Decision, BeforeValidator(_exact_json_enum_integer)]


class Frozen(BaseModel):
    """불변·엄격한 모델. model_copy의 변경 값도 다시 검증한다."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def validate_utf8_strings(cls, value: object) -> object:
        if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("UTF-8로 표현할 수 없는 문자열은 허용하지 않는다")
        return value

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """기본 Pydantic 복사와 달리 업데이트를 전체 모델 검증에 통과시킨다."""
        values = self.model_dump(mode="python")
        if update is not None:
            values.update(update)
        return type(self).model_validate(values)


class StoreExecutionState(Frozen):
    """SQL 큐와 독립된 Store 실행 권한의 불변 snapshot(SEQ-02)."""

    run_id: str | None = Field(repr=False)
    phase: StorePhase
    execution_enabled: bool
    state_revision: NonNegativeInt
    reason: Reason | None
    commit_outcome: CommitOutcome | None
    last_committed_run_state: str | None = Field(repr=False)
    requires_restart: bool


class MigrationReport(Frozen):
    """경로·원문 없이 스키마 준비의 완료 사실만 전달한다."""

    from_version: NonNegativeInt
    to_version: Annotated[int, Field(ge=3, le=3)]
    backup_published: bool
    migrated: bool


class RecoveryReport(Frozen):
    """성공한 복구 TX의 원문 없는 결과(REC-04)."""

    schema_version: Annotated[int, Field(ge=3, le=3)]
    recovery_version: Annotated[int, Field(ge=1, le=1)]
    run_id: NonEmptyString = Field(repr=False)
    committed_at_utc: datetime
    changed_task_count: NonNegativeInt
    changed_request_count: NonNegativeInt
    changed_approval_count: NonNegativeInt
    unknown_outcome_count: NonNegativeInt
    quarantined_count: NonNegativeInt
    unresolved_count: Annotated[int, Field(ge=0, le=0)]

    @field_validator("committed_at_utc")
    @classmethod
    def validate_committed_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("복구 시각은 UTC여야 한다")
        return value


class RecoveryItem(Frozen):
    """격리된 요청의 검증된 저장 메타데이터(REC-02)."""

    run_id: NonEmptyString = Field(repr=False)
    task_id: NonEmptyString = Field(repr=False)
    request_id: NonEmptyString = Field(repr=False)
    original_status: RequestStatus
    category: RecoveryCategory
    at_utc: datetime
    server_id: NonEmptyString = Field(repr=False)
    tool_name: NonEmptyString = Field(repr=False)
    destination_id: NonEmptyString | None = Field(repr=False)
    kind: ToolKind | None
    epoch: Annotated[int, Field(ge=0, le=MAX_SAFE_SEQUENCE)] | None

    @field_validator("at_utc")
    @classmethod
    def validate_at_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("격리 시각은 UTC여야 한다")
        return value


class RecoveryItemPage(Frozen):
    """request_id 순서의 격리 항목과 다음 페이지 커서."""

    items: tuple[RecoveryItem, ...]
    next_after: NonEmptyString | None = Field(repr=False)


class FailureAuditRecord(Frozen):
    """원문 없이 실패 종류와 신뢰된 식별자만 전달하는 내구성 감사 입력."""

    task_id: NonEmptyString = Field(repr=False)
    request_id: NonEmptyString | None = Field(default=None, repr=False)
    phase: Literal["before_dispatch", "after_dispatch"]
    reason: Reason
    exception_type: str = Field(repr=False)
    at_utc: datetime
    run_id: NonEmptyString | None = Field(default=None, repr=False)
    epoch: Annotated[int, Field(ge=0, le=MAX_SAFE_SEQUENCE)] | None = None

    @field_validator("exception_type")
    @classmethod
    def validate_exception_type(cls, value: str) -> str:
        if not value.isascii() or not value.isidentifier() or len(value) > 64:
            raise ValueError("예외 형식은 64자 이하 ASCII 식별자여야 한다")
        return value

    @field_validator("at_utc")
    @classmethod
    def validate_at_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("감사 시각은 UTC여야 한다")
        return value


class ProposedCall(Frozen):
    """모델이 제안한 비신뢰 입력. Task·등급·승인자 필드는 없다."""

    tool_ref: NonEmptyString
    arguments: Arguments = Field(repr=False)
    model_call_id: NonEmptyString


class ToolSpec(Frozen):
    """등록 시 고정한 도구 정의. 종류는 원격 annotation에서 추론하지 않는다."""

    server_id: NonEmptyString
    tool_name: NonEmptyString
    kind: ToolKind
    input_schema: FrozenJsonObject = Field(repr=False)
    destination_arg: NonEmptyString | None
    resource_arg: NonEmptyString | None
    fingerprint: Sha256Digest
    # DESC-01 합의 전 제안. 신뢰된 사용자 검토 입력만, 원본 자동 대체 없음.
    reviewed_description: ReviewedDescription = Field(default="", repr=False)


class Boundary(Frozen):
    """등록된 수신 경계와 최고 수신 등급."""

    boundary_id: NonEmptyString
    tier: TrustTier
    max_label: StrictLabel

    @model_validator(mode="after")
    def validate_external_limit(self) -> Self:
        if self.tier is not TrustTier.INTERNAL and self.max_label > Label.INTERNAL:
            raise ValueError("외부 경계는 INTERNAL 이하의 수신 등급만 허용한다")
        return self


class ResolvedRequest(Frozen):
    """Guard가 신뢰 설정과 Task 상태에서 해석한 요청."""

    request_id: NonEmptyString
    task_id: NonEmptyString
    epoch: NonNegativeInt
    call_seq: NonNegativeInt
    tool: ToolSpec
    arguments: Arguments = Field(repr=False)
    resource_id: NonEmptyString | None
    server_boundary: Boundary
    destination: Boundary | None


class Escalation(Frozen):
    """READ 관측으로 등급이 올라간 근거."""

    at_seq: NonNegativeInt
    source_id: NonEmptyString
    from_label: StrictLabel
    to_label: StrictLabel


class TaskState(Frozen):
    """Task별 누적 등급과 보안·저장소 버전을 분리한다."""

    task_id: NonEmptyString
    epoch: NonNegativeInt
    profile_id: NonEmptyString
    policy_version: NonEmptyString
    high_watermark: StrictLabel = Label.PUBLIC
    unclassified: bool = False
    status: TaskStatus = TaskStatus.READY
    security_version: NonNegativeInt = 0
    escalations: tuple[Escalation, ...] = ()
    last_read_label: StrictLabel = Label.PUBLIC
    row_revision: NonNegativeInt = 0


class AccessResult(Frozen):
    """접근 규칙 판정 결과. reason 원문은 기본 repr에서 숨긴다."""

    allowed: bool
    reason: str = Field(repr=False)
    error: bool


class PolicyInput(Frozen):
    """시점과 신뢰 해석을 명시한 순수 정책 입력."""

    request: ResolvedRequest
    state: TaskState
    effective_label: StrictLabel
    registration_status: RegStatus
    registered_fingerprint: Sha256Digest
    access: AccessResult
    policy_version: NonEmptyString
    phase: Literal["submit", "approval_recheck"]

    @model_validator(mode="after")
    def validate_task_identity(self) -> Self:
        if self.request.task_id != self.state.task_id or self.request.epoch != self.state.epoch:
            raise ValueError("요청과 상태의 Task·epoch가 일치해야 한다")
        return self


class BoundaryVerdict(Frozen):
    """경계별 판정과 기계용 사유."""

    boundary_id: NonEmptyString
    decision: StrictDecision
    reason: Reason


class PolicyDecision(Frozen):
    """bool·None 대신 명시적 판정과 적용 버전을 반환한다."""

    decision: StrictDecision
    reasons: tuple[Reason, ...] = Field(min_length=1)
    verdicts: tuple[BoundaryVerdict, ...]
    policy_version: NonEmptyString
    security_version: NonNegativeInt
