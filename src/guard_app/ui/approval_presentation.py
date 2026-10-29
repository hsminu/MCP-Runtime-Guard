"""Approval query values and pure public display calculations."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from guard_app.domain.enums import ApprovalStatus
from guard_app.services.bridge import valid_server_id

from .presentation import MISSING, format_kst, public_display_text


class ApprovalLane(StrEnum):
    PENDING = "pending"
    HISTORY = "history"


class ApprovalSort(StrEnum):
    OLDEST = "oldest"
    NEWEST = "newest"


PAGE_SIZE = 6
_STATUS_TEXT = {
    ApprovalStatus.PENDING: "승인 대기",
    ApprovalStatus.APPROVED: "승인됨 · 실행 결과 별도 확인",
    ApprovalStatus.CONSUMED: "승인 소비됨 · 실행 결과 별도 확인",
    ApprovalStatus.REJECTED: "거절됨",
    ApprovalStatus.EXPIRED: "만료됨",
    ApprovalStatus.CANCELLED: "취소됨",
    ApprovalStatus.INVALIDATED: "무효화됨",
}
_NARROW_STATUS_TEXT = {
    ApprovalStatus.PENDING: "승인 대기",
    ApprovalStatus.APPROVED: "승인됨",
    ApprovalStatus.CONSUMED: "승인 소비됨",
    ApprovalStatus.REJECTED: "거절됨",
    ApprovalStatus.EXPIRED: "만료됨",
    ApprovalStatus.CANCELLED: "취소됨",
    ApprovalStatus.INVALIDATED: "무효화됨",
}


def _bounded_request_age(created_at: datetime, now: datetime) -> str | None:
    """A display hint only; never infer an age from an invalid or skewed clock."""

    if now.tzinfo is None or now.utcoffset() is None or now < created_at:
        return None
    elapsed_minutes = int((now - created_at).total_seconds() // 60)
    if elapsed_minutes >= 7 * 24 * 60:
        return "7일 이상"
    if elapsed_minutes >= 24 * 60:
        return f"{elapsed_minutes // (24 * 60)}일"
    if elapsed_minutes >= 60:
        return f"{elapsed_minutes // 60}시간"
    return f"{elapsed_minutes}분"


@dataclass(frozen=True, slots=True)
class ApprovalQuery:
    """One UI query intent; token rejects replies to older search/page intents."""

    token: int
    lane: ApprovalLane
    search: str
    server_id: str
    sort: ApprovalSort
    page: int
    page_size: int = PAGE_SIZE


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalSummary:
    """Backend-approved public projection. Never include raw arguments here."""

    approval_id: str
    task_id: str
    request_id: str
    server_id: str
    destination_summary: str
    status: ApprovalStatus
    created_at: datetime
    resolved_at: datetime | None = None
    reason_code: str | None = None
    # The Store row's revision, the expected value for a command from this list.
    row_revision: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalPage:
    """One backend page and its server-derived total, not a local row cache."""

    query: ApprovalQuery
    rows: tuple[ApprovalSummary, ...]
    total_count: int
    as_of: datetime | None = None


SOURCE_NOT_CONNECTED = "승인 목록 조회가 아직 연결되지 않음 · 승인 요청은 대화 화면에서 처리하세요."
# B6-05: a failed query is unknown, never an empty list.
QUERY_UNKNOWN = "불러오지 못함 · 항목이 없다는 뜻이 아님. 새로고침하세요."
QUERY_TEXT_INVALID = "검색어는 영문·숫자와 _ . : - 만 64자까지 입력하세요."
SERVER_FILTER_INVALID = "서버 ID에 밑줄 두 개 연속·끝 밑줄·제어 문자를 쓰지 마세요."
_QUERY_TEXT = re.compile(r"[A-Za-z0-9_.:-]{0,64}")


def valid_query_text(text: str) -> bool:
    """B's search pattern; checked before sending, sent unchanged (case kept)."""

    return type(text) is str and _QUERY_TEXT.fullmatch(text) is not None


def valid_server_filter(server_id: str) -> bool:
    """An empty filter means all servers; otherwise use B's unchanged ID rule."""

    return server_id == "" or valid_server_id(server_id)


def valid_page(page: ApprovalPage) -> bool:
    if (
        type(page.total_count) is not int
        or page.total_count < 0
        or type(page.query.page) is not int
        or page.query.page < 1
        or page.query.page_size != PAGE_SIZE
        or type(page.rows) is not tuple
    ):
        return False
    expected_rows = min(
        PAGE_SIZE,
        max(0, page.total_count - (page.query.page - 1) * PAGE_SIZE),
    )
    if len(page.rows) != expected_rows:
        return False
    seen: set[str] = set()
    for row in page.rows:
        if (
            type(row) is not ApprovalSummary
            or type(row.approval_id) is not str
            or not row.approval_id
            or row.approval_id in seen
            or type(row.task_id) is not str
            or type(row.request_id) is not str
            or type(row.server_id) is not str
            or type(row.destination_summary) is not str
            or not isinstance(row.status, ApprovalStatus)
            or type(row.created_at) is not datetime
            or row.created_at.tzinfo is None
            or row.created_at.utcoffset() is None
            or ((row.status is ApprovalStatus.PENDING) != (page.query.lane is ApprovalLane.PENDING))
        ):
            return False
        seen.add(row.approval_id)
    ordered = sorted(
        page.rows,
        key=lambda row: (row.created_at, row.approval_id),
        reverse=page.query.sort is ApprovalSort.NEWEST,
    )
    return list(page.rows) == ordered


def selected_detail_text(row: ApprovalSummary) -> str:
    def one_line(value: str) -> str:
        parts = []
        for char in value:
            if char == "\\":
                parts.append("\\\\")
            elif char != " " and (
                char.isspace()
                or unicodedata.category(char).startswith("C")
                or unicodedata.bidirectional(char) in {"R", "AL", "AN"}
            ):
                codepoint = ord(char)
                parts.append(
                    f"\\u{codepoint:04X}" if codepoint <= 0xFFFF else f"\\U{codepoint:08X}"
                )
            else:
                parts.append(char)
        return "".join(parts)

    return "\n".join(
        (
            f"승인 ID: {one_line(row.approval_id)} · 서버 ID: {one_line(row.server_id)}",
            f"상태: {_STATUS_TEXT[row.status]}",
            f"Task ID: {one_line(row.task_id)} · 요청 ID: {one_line(row.request_id)}",
            "수신처 요약: "
            f"{one_line(row.destination_summary) if row.destination_summary else MISSING}",
            f"요청 시각: {format_kst(row.created_at)} · 만료 시각: {format_kst(row.expires_at)}",
            f"처리 시각: {format_kst(row.resolved_at)}",
        )
    )


def summary_columns(row: ApprovalSummary) -> tuple[str, ...]:
    return (
        public_display_text(row.approval_id),
        public_display_text(row.server_id),
        "\n".join(
            (
                public_display_text(row.task_id),
                public_display_text(row.request_id),
            )
        ),
        public_display_text(row.destination_summary),
        "",
        f"{format_kst(row.created_at)}\n{format_kst(row.resolved_at)}",
    )


def status_display(
    row: ApprovalSummary,
    query: ApprovalQuery | None,
    now: datetime | None,
    *,
    narrow: bool,
) -> tuple[str, int, str, str]:
    status = _STATUS_TEXT[row.status]
    # The Store's own expiry, never a guess from a default TTL (#86).
    if (
        query is not None
        and query.lane is ApprovalLane.PENDING
        and now is not None
        and now.tzinfo is not None
        and now.utcoffset() is not None
        and row.expires_at is not None
        and row.expires_at.tzinfo is not None
        and now >= row.expires_at
    ):
        status += " · 승인 유효 시간 지남 · 상태 다시 확인(참고)"
    if narrow:
        age = _bounded_request_age(row.created_at, now) if now else None
        if row.status in (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED):
            elapsed = "결과 별도 확인"
        elif age is None:
            elapsed = "요청 시각 확인"
        elif row.status is ApprovalStatus.PENDING:
            elapsed = f"{age} 대기"
        else:
            elapsed = f"요청 {age} 전"
        text = f"{_NARROW_STATUS_TEXT[row.status]}\n{elapsed}"
        height = 42
    else:
        text = status
        height = 64
    requested_at = format_kst(row.created_at)
    return (
        text,
        height,
        f"상태: {status}\n요청 시각: {requested_at}",
        f"상태: {status}, 요청 시각: {requested_at}",
    )
