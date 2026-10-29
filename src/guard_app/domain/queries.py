"""B6-05: 화면 조회를 위한 원문 없는 불변 자료(합의 전 제안)."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator

from guard_app.domain.enums import ApprovalStatus, Decision, Label, RequestStatus, ToolKind
from guard_app.domain.models import Frozen, NonEmptyString, NonNegativeInt
from guard_app.domain.reasons import Reason


class ApprovalListLane(StrEnum):
    PENDING = "PENDING"
    HISTORY = "HISTORY"


class ListSort(StrEnum):
    NEWEST = "NEWEST"
    OLDEST = "OLDEST"


class _QueryModel(Frozen):
    """시간은 추측하거나 변환하지 않고 UTC로 확인한다."""

    @field_validator("*", mode="after")
    @classmethod
    def validate_utc(cls, value: object) -> object:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("조회 시각은 UTC여야 한다")
        return value


class ApprovalListQuery(_QueryModel):
    lane: ApprovalListLane
    search: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{0,64}$")] = Field(
        default="", repr=False
    )
    server_id: NonEmptyString | None = Field(default=None, repr=False)
    sort: ListSort
    offset: Annotated[int, Field(ge=0, le=10_000)]
    limit: Annotated[int, Field(ge=1, le=100)]


class ApprovalRow(_QueryModel):
    approval_id: NonEmptyString = Field(repr=False)
    task_id: NonEmptyString = Field(repr=False)
    request_id: NonEmptyString = Field(repr=False)
    server_id: NonEmptyString = Field(repr=False)
    tool_name: NonEmptyString = Field(repr=False)
    destination_id: NonEmptyString | None = Field(repr=False)
    status: ApprovalStatus
    created_at_utc: datetime
    expires_at_utc: datetime
    decided_at_utc: datetime | None
    reason_code: Reason | None
    row_revision: NonNegativeInt


class ApprovalListPage(_QueryModel):
    rows: tuple[ApprovalRow, ...]
    total_count: NonNegativeInt
    as_of_utc: datetime


class CallListQuery(_QueryModel):
    search: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{0,64}$")] = Field(
        default="", repr=False
    )
    task_id: NonEmptyString | None = Field(default=None, repr=False)
    server_id: NonEmptyString | None = Field(default=None, repr=False)
    sort: ListSort
    offset: Annotated[int, Field(ge=0, le=10_000)]
    limit: Annotated[int, Field(ge=1, le=100)]


class CallRow(_QueryModel):
    request_id: NonEmptyString = Field(repr=False)
    task_id: NonEmptyString = Field(repr=False)
    epoch: NonNegativeInt | None
    call_seq: NonNegativeInt
    server_id: NonEmptyString = Field(repr=False)
    tool_name: NonEmptyString = Field(repr=False)
    kind: ToolKind | None
    destination_id: NonEmptyString | None = Field(repr=False)
    decision: Decision | None
    reason_codes: tuple[Reason, ...]
    approval_id: NonEmptyString | None = Field(repr=False)
    approval_status: ApprovalStatus | None
    status: RequestStatus
    decided_at_utc: datetime | None
    row_revision: NonNegativeInt


class CallStage(_QueryModel):
    """COMMITTED는 성공·실패·결과 불명인 결과를 기록했다는 뜻이다.

    성공 여부는 CallRow.status로만 판단한다.
    """

    stage_code: Literal[
        "DECIDED", "WAITING_APPROVAL", "DISPATCHING", "OBSERVED", "COMMITTED", "WITHHELD"
    ]
    at_utc: datetime


class CallDetail(_QueryModel):
    """COMMITTED는 성공·실패·결과 불명인 결과를 기록했다는 뜻이다.

    성공 여부는 CallRow.status로만 판단한다.
    """

    row: CallRow
    observed_label: Label | None
    stages: tuple[CallStage, ...]


class CallListPage(_QueryModel):
    rows: tuple[CallRow, ...]
    total_count: NonNegativeInt
    as_of_utc: datetime
