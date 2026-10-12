"""기술설계서 3.3절의 공통 모델 초안. 합의·동결 여부는 계약 문서에 기록한다."""

from __future__ import annotations

from collections.abc import Mapping
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

from guard_app.domain.enums import Decision, Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.reasons import Reason

__all__ = [
    "AccessResult",
    "Arguments",
    "Boundary",
    "BoundaryVerdict",
    "Escalation",
    "Frozen",
    "FrozenJsonObject",
    "PolicyDecision",
    "PolicyInput",
    "ProposedCall",
    "ResolvedRequest",
    "StrictDecision",
    "StrictLabel",
    "TaskState",
    "ToolSpec",
]

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
    """AGT 번역 결과. reason 원문은 기본 repr에서 숨긴다."""

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
