"""B1 검토용 계약 DTO. 설계에 없는 상세 필드는 A·C 합의 전 초안이다.

SDK 결과는 JSON 사본으로 전달하며 UI 명령·이벤트와 provider 형식은 정하지 않는다.
UTC 문자열 대신 datetime, 결과 envelope와 모델 대화의 필드는 초안 선택이다.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from guard_app.domain.enums import ApprovalStatus, RegStatus, ScanErrorCode
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import Boundary, Frozen, ProposedCall, StrictLabel, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.domain.text import ScanRisk, ThreatSummary

type Identifier = Annotated[str, Field(min_length=1)]
type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type Reasons = Annotated[tuple[Reason, ...], Field(min_length=1)]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC-aware timestamp required")
    return value


class TaskIdentity(Frozen):
    """STOP-01 합의 전 제안. 신뢰된 세션과 결속한 Task 생애 식별자."""

    task_id: Identifier
    epoch: int = Field(ge=0)


class RawResult(Frozen):
    """A→응답 게이트 전용 격리 자료. repr 제외는 공개 권한을 뜻하지 않는다.

    payload는 SDK 결과의 JSON 사본이다. sent는 수신측 성공 계수가 아니라
    이미 전송됐을 가능성이다. failed는 바이트가 없는 알려진 전송 실패만 뜻한다.
    내용이 섞인 isError 결과는 failed=False로 분류해야 한다(6.2·9.2절).
    """

    payload: FrozenJsonObject | None = Field(default=None, repr=False)
    failed: bool = False
    sent: bool = True
    failure_reason: Reason | None = None

    @model_validator(mode="after")
    def check_failure_envelope(self) -> Self:
        if self.failed:
            if self.payload is not None or self.failure_reason is None:
                raise ValueError("known transport failure requires no payload and a reason")
        elif self.payload is None or self.failure_reason is not None or not self.sent:
            raise ValueError("received result requires a payload and no transport failure")
        return self


class ReleasedResult(Frozen):
    """형식·출처 검사와 상태 커밋 후에만 생성하는 공개 결과(6.3절).

    생성자 자체는 커밋 증명이 아니다. gate 밖에서 raw를 이 타입으로 바꾸지 않는다.
    SEND의 label=None은 Released에 있으며 미분류 READ와 구별한다.
    """

    text: str = Field(default="", repr=False)
    structured_content: FrozenJsonObject | None = Field(default=None, repr=False)
    is_error: bool = False


class Denied(Frozen):
    kind: Literal["denied"] = "denied"
    request_id: Identifier | None
    reasons: Reasons


class Pending(Frozen):
    kind: Literal["pending"] = "pending"
    request_id: Identifier
    approval_id: Identifier
    reasons: Reasons


class Released(Frozen):
    kind: Literal["released"] = "released"
    request_id: Identifier
    result: ReleasedResult = Field(repr=False)
    label: StrictLabel | None


class Withheld(Frozen):
    kind: Literal["withheld"] = "withheld"
    request_id: Identifier
    reasons: Reasons


class OutcomeUnknown(Frozen):
    kind: Literal["outcome_unknown"] = "outcome_unknown"
    request_id: Identifier
    reasons: Reasons


type Outcome = Annotated[
    Denied | Pending | Released | Withheld | OutcomeUnknown,
    Field(discriminator="kind"),
]


type FinalOutcome = Annotated[
    Denied | Released | Withheld | OutcomeUnknown,
    Field(discriminator="kind"),
]

type PendingFinalCause = Literal[
    "executed", "rejected", "expired", "cancelled", "invalidated", "failed", "shutdown"
]


class PendingFinal(Frozen):
    """NOTIFY-01 합의 전 제안: 한 Pending 요청의 확정 결과. 사람 명령 응답과 별개다.

    task_id·epoch는 Guard 사본에서 오며 model_call_id는 A가 별도로 결속한다.
    outcome은 gate가 확정한 기존 결과만 재사용하고 Pending은 허용하지 않는다.
    """

    task_id: Identifier
    epoch: int = Field(ge=0)
    request_id: Identifier
    approval_id: Identifier
    cause: PendingFinalCause
    outcome: FinalOutcome = Field(repr=False)

    @model_validator(mode="after")
    def check_request(self) -> Self:
        if self.outcome.request_id != self.request_id:
            raise ValueError("final outcome request does not match")
        if self.cause != "executed" and not isinstance(self.outcome, Denied):
            raise ValueError("non-execution final requires denial")
        return self


class TrustedContext(Frozen):
    """Guard가 Store와 사전 등록 운영자에서 구성한다. 모델 입력으로 만들지 않는다."""

    state: TaskState
    operator_id: Identifier


class ApprovalRecord(Frozen):
    """DB 승인 메타데이터만 담는다. 요청 원문·snapshot·monotonic deadline은 없다."""

    approval_id: Identifier
    request_id: Identifier
    snapshot_hash: Sha256
    approver_id: Identifier
    status: ApprovalStatus
    created_at_utc: datetime
    expires_at_utc: datetime
    decided_at_utc: datetime | None = None
    consumed_at_utc: datetime | None = None
    row_revision: int = Field(default=0, ge=0)

    @field_validator("created_at_utc", "expires_at_utc", "decided_at_utc", "consumed_at_utc")
    @classmethod
    def check_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_utc(value)

    @model_validator(mode="after")
    def check_times(self) -> Self:
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("approval expiry must follow creation")
        if self.decided_at_utc is not None and self.decided_at_utc < self.created_at_utc:
            raise ValueError("approval decision cannot precede creation")
        if self.status is ApprovalStatus.PENDING and self.decided_at_utc is not None:
            raise ValueError("pending approval cannot carry a decision time")
        if self.status in (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED) and (
            self.decided_at_utc is None or self.decided_at_utc >= self.expires_at_utc
        ):
            raise ValueError("approved record requires an unexpired decision time")
        if self.status is ApprovalStatus.CONSUMED:
            if self.consumed_at_utc is None or self.decided_at_utc is None:
                raise ValueError("consumed record requires decision and consumption times")
            if not self.decided_at_utc <= self.consumed_at_utc < self.expires_at_utc:
                raise ValueError("approval consumption time is outside its valid window")
        elif self.consumed_at_utc is not None:
            raise ValueError("only consumed approvals may carry consumption time")
        return self


class Finding(Frozen):
    """정의 metadata 검사 결과 초안. description은 기본 repr에 공개하지 않는다."""

    analyzer: Identifier
    code: Identifier
    severity: Identifier
    tool_fingerprint: Sha256 | None = None
    description: str = Field(default="", repr=False)
    # SCAN-01 합의 전 제안. severity는 SDK 값 그대로이며 아래 값으로 내리지 않는다.
    tool_name: Identifier | None = Field(default=None, repr=False)
    source: Literal["LLM", "YARA"] | None = None
    overall_risk: ScanRisk | None = Field(default=None, repr=False)
    threat_summary: ThreatSummary = Field(default="", repr=False)


class AnalyzerDiagnostic(Frozen):
    """SCAN-01 합의 전 제안. 실패/생략 사유이며 예외·SDK 원문 필드는 없다."""

    analyzer: Identifier
    code: ScanErrorCode
    tool_name: Identifier | None = Field(default=None, repr=False)
    tool_fingerprint: Sha256 | None = None


class ScanReport(Frozen):
    """SCAN-01 합의 전 제안. 자체 평가는 표시 전용이며 판정·정렬에 쓰지 않는다.

    새 필드는 기본값으로 생략할 수 있다. 옛 실패/생략 목록도 그대로 보존하며,
    빈 findings·비어 있는 진단 목록은 검사 완료나 안전의 근거가 아니다.
    """

    server_id: Identifier
    fingerprint_set: tuple[Sha256, ...]
    analyzers_required: tuple[Identifier, ...]
    analyzers_completed: tuple[Identifier, ...]
    analyzers_skipped: tuple[Identifier, ...]
    analyzers_failed: tuple[Identifier, ...]
    max_severity: Identifier | None
    findings: tuple[Finding, ...]
    scanned_at_utc: datetime
    scanner_version: Identifier
    overall_risk: ScanRisk | None = Field(default=None, repr=False)
    threat_summary: ThreatSummary = Field(default="", repr=False)
    analyzer_diagnostics: tuple[AnalyzerDiagnostic, ...] = ()

    @field_validator("scanned_at_utc")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def check_analyzer_inventory(self) -> Self:
        inventories = (
            self.fingerprint_set,
            self.analyzers_required,
            self.analyzers_completed,
            self.analyzers_skipped,
            self.analyzers_failed,
        )
        if any(len(items) != len(set(items)) for items in inventories):
            raise ValueError("scan inventory cannot contain duplicates")
        completed, skipped, failed = map(set, inventories[2:])
        if completed & skipped or completed & failed or skipped & failed:
            raise ValueError("analyzer outcomes must be disjoint")
        diagnostic_keys = tuple(
            (item.analyzer, item.tool_name, item.tool_fingerprint, item.code)
            for item in self.analyzer_diagnostics
        )
        if len(diagnostic_keys) != len(set(diagnostic_keys)):
            raise ValueError("analyzer diagnostics cannot contain duplicates")
        for item in self.analyzer_diagnostics:
            if item.analyzer not in skipped | failed:
                raise ValueError("analyzer diagnostic requires skipped or failed outcome")
            if (
                item.tool_fingerprint is not None
                and item.tool_fingerprint not in self.fingerprint_set
            ):
                raise ValueError("analyzer diagnostic fingerprint is outside the scan")
        return self


class RegisteredServer(Frozen):
    """저장된 등록 상태·지문에서 구성한 조회 DTO. 활성화 판정은 C Registry 소유다."""

    server_id: Identifier
    status: RegStatus
    boundary: Boundary
    tools: tuple[ToolSpec, ...]
    row_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_tool_ownership(self) -> Self:
        if any(tool.server_id != self.server_id for tool in self.tools):
            raise ValueError("registered tools must belong to the registered server")
        names = tuple(tool.tool_name for tool in self.tools)
        if len(names) != len(set(names)):
            raise ValueError("registered tool names must be unique within a server")
        return self


class ToolView(Frozen):
    """ACTIVE 등록 도구만으로 구성하는 모델용 view. 제어 명령은 포함하지 않는다."""

    tool_ref: Identifier
    input_schema: FrozenJsonObject = Field(repr=False)
    description: str = Field(repr=False)


class ConversationMessage(Frozen):
    """provider 중립 초안. tool content에는 gate의 공개 결과만 넣는다."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(default="", repr=False)
    calls: tuple[ProposedCall, ...] = Field(default=(), repr=False)
    model_call_id: Identifier | None = None


type Conversation = tuple[ConversationMessage, ...]


class ModelTurn(Frozen):
    text: str | None = Field(default=None, repr=False)
    calls: tuple[ProposedCall, ...] = Field(default=(), repr=False)
