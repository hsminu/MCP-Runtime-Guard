"""Read-only call log presentation from a bridge-approved public projection.

B's Store queries (B6-05) supply these values through the bridge; without a
query source the page shows whatever ``set_calls`` supplied. This module never
reads audit storage, accepts a tool body, or infers a security decision from a
missing row. CSV is deliberately limited to the visible public summary fields.
The boundary tier at decision time is not stored, so no tier is shown (#86).
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import shiboken6
from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import ToolKind

from .approval_presentation import QUERY_TEXT_INVALID, QUERY_UNKNOWN, valid_query_text
from .models import LoadState, Surface
from .presentation import (
    DECISIONS,
    MISSING,
    REASONS,
    REQUEST_STATUSES,
    fixed,
    format_kst,
)
from .presentation import public_display_text as _public_display_text
from .widgets import (
    KeyboardActionButton,
    PlainDetailDialog,
    StateMessage,
    plain_label,
    table_row_height,
)

_KST = timezone(timedelta(hours=9), name="KST")
_KINDS = {ToolKind.READ.value: "읽기 · READ", ToolKind.SEND.value: "전송 · SEND"}
_APPROVAL = {
    "NOT_REQUIRED": "승인 불필요",
    "PENDING": "승인 대기",
    "APPROVED": "승인됨 · 실행 결과와 별개",
    "CONSUMED": "승인 소비됨 · 실행 결과와 별개",
    "REJECTED": "거절됨",
    "EXPIRED": "만료됨",
    "CANCELLED": "취소됨",
    "INVALIDATED": "무효화됨",
}
# B6-05 stages from the audit events. The Store cannot tell whether a reserved
# call reached the server, and success or release shows in the current status.
_STAGES = {
    "DECIDED": "최초 판정",
    "WAITING_APPROVAL": "승인 대기",
    "DISPATCHING": "전송 예약",
    "OBSERVED": "결과 수신",
    "COMMITTED": "결과 기록 · 성공 여부는 현재 실행 상태로 확인",
    "WITHHELD": "결과 미공개",
}
CALL_PAGE_SIZE = 50
SOURCE_NOT_CONNECTED = "호출 로그 조회가 아직 연결되지 않음."


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallSummary:
    """Closed UI allowlist; the bridge must exclude raw data before creating it."""

    call_id: str
    request_id: str | None = None
    task_id: str | None = None
    server_id: str | None = None
    tool_name: str | None = None
    kind: str | None = None
    initial_decision: str | None = None
    reason_code: str | None = None
    approval_status: str | None = None
    current_status: str | None = None
    occurred_at: datetime | None = None
    # Every recorded reason; reason_code is the first for the table and CSV.
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallStage:
    """A backend-supplied stage; NOT_RUN is distinct from an unknown timestamp."""

    stage_code: str
    state: str
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallDetail:
    """Public summary plus confirmed stages, with no payload or exception text."""

    summary: PublicCallSummary
    stages: tuple[PublicCallStage, ...] | None = None


@dataclass(frozen=True, slots=True)
class DetailRequest:
    """One dialog opening; a later opening of the same call gets a new token."""

    call_id: str
    token: int


@dataclass(frozen=True, slots=True)
class CallLogQuery:
    """One list query intent; the token drops replies to older search/page intents."""

    token: int
    search: str
    page: int
    page_size: int = CALL_PAGE_SIZE


@dataclass(frozen=True, slots=True, repr=False)
class CallLogResult:
    """One backend page and its Store-counted total, not a local row cache."""

    query: CallLogQuery
    rows: tuple[PublicCallSummary, ...]
    total_count: int
    as_of: datetime | None = None


def _iso_kst(value: datetime | None) -> str:
    """CSV uses an offset-bearing ISO value; a naive date has no guessed zone."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).isoformat(timespec="seconds")


def _short_time(value: datetime | None) -> str:
    """Keep date, seconds, and timezone readable in a narrow column."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).strftime("%Y-%m-%d\n%H:%M:%S\nKST")


def _safe_csv_cell(value: str) -> str:
    """Keep spreadsheet importers from treating external text as a formula."""

    index = 0
    while index < len(value):
        char = value[index]
        if char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}:
            index += 1
        else:
            break
    if (index < len(value) and value[index] in "=+-@") or (
        value and (value[0].isspace() or unicodedata.category(value[0]) in {"Cc", "Cf"})
    ):
        return "'" + value
    return value


def _cell_texts(row: PublicCallSummary, mode: str | None) -> tuple[str, ...]:
    """Every table write uses these escaped texts; raw IDs never reach a cell."""

    tool = _public_display_text(row.tool_name)
    server = _public_display_text(row.server_id)
    call = _public_display_text(row.call_id)
    request = _public_display_text(row.request_id)
    full = mode not in ("narrow", "medium")
    return (
        format_kst(row.occurred_at) if full else _short_time(row.occurred_at),
        server if mode == "narrow" else f"{tool}\n{server}",
        f"{call}\n{request}" if full else call,
        fixed(_KINDS, row.kind),
        fixed(DECISIONS, row.initial_decision),
        fixed(_APPROVAL, row.approval_status),
        "결과 불명\n수신측 확인 필요"
        if not full and row.current_status == "UNKNOWN_OUTCOME"
        else fixed(REQUEST_STATUSES, row.current_status),
    )


def public_calls_csv(rows: Sequence[PublicCallSummary]) -> str:
    """Export only the closed public fields of the supplied, already queried rows."""

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(
        (
            "발생 시각 (ISO 8601)",
            "호출 ID",
            "요청 ID",
            "Task ID",
            "서버 ID",
            "도구 이름",
            "종류",
            "최초 판정",
            "판정 사유",
            "승인 상태",
            "현재 실행 상태",
        )
    )
    for row in rows:
        if type(row) is not PublicCallSummary:
            raise TypeError("call log row must be a public summary")
        writer.writerow(
            tuple(
                _safe_csv_cell(value)
                for value in (
                    _iso_kst(row.occurred_at),
                    row.call_id,
                    row.request_id or MISSING,
                    row.task_id or MISSING,
                    row.server_id or MISSING,
                    row.tool_name or MISSING,
                    fixed(_KINDS, row.kind),
                    fixed(DECISIONS, row.initial_decision),
                    fixed(REASONS, row.reason_code),
                    fixed(_APPROVAL, row.approval_status),
                    fixed(REQUEST_STATUSES, row.current_status),
                )
            )
        )
    return stream.getvalue()


def _detail_text(detail: PublicCallDetail) -> str:
    row = detail.summary
    lines = [
        f"호출 ID: {_public_display_text(row.call_id)}",
        f"요청 ID: {_public_display_text(row.request_id)}",
        f"Task ID: {_public_display_text(row.task_id)}",
        f"서버 ID: {_public_display_text(row.server_id)}",
        f"도구 이름: {_public_display_text(row.tool_name)}",
        f"종류: {fixed(_KINDS, row.kind)}",
        f"발생 시각: {format_kst(row.occurred_at)}",
        f"최초 판정: {fixed(DECISIONS, row.initial_decision)}",
        "판정 사유: "
        + ", ".join(fixed(REASONS, code) for code in (row.reason_codes or (row.reason_code,))),
        f"승인 상태: {fixed(_APPROVAL, row.approval_status)}",
        f"현재 실행 상태: {fixed(REQUEST_STATUSES, row.current_status)}",
        "",
        "처리 순서",
    ]
    if detail.stages is None:
        lines.append("단계별 감사 조회 계약 미제공")
    elif not detail.stages:
        lines.append("확인된 단계가 없음")
    else:
        for stage in detail.stages:
            name = _STAGES.get(stage.stage_code, "미확인 단계")
            if stage.state == "NOT_RUN":
                when = "미실행"
            elif stage.state == "OCCURRED":
                when = format_kst(stage.occurred_at)
            else:
                when = "상태 미확인"
            lines.append(f"{name}: {when}")
    lines.extend(
        (
            "",
            "안전한 입출력 요약: 조회 계약 미제공",
            "추가 구조화 메타데이터: 조회 계약 미제공",
            "본문·전체 인자·인증정보·예외 원문은 표시하지 않음.",
        )
    )
    return "\n".join(lines)


class CallLogPage(QWidget):
    """Full-width log list and large plain-text dialog, without backend access.

    With a query source (``set_source_connected``) the search and pages are
    Store queries sent as ``query_requested``; replies are accepted only
    through ``set_page`` with the exact query. Without one, ``set_calls``
    supplies the rows and the search filters them locally.
    """

    detail_requested = Signal(object)
    query_requested = Signal(object)
    _COMPACT_VIEWPORT_WIDTH = 760
    _NARROW_VIEWPORT_WIDTH = 460
    _STACK_ACTIONS_WIDTH = 580

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[PublicCallSummary, ...] = ()
        self._visible_rows: list[PublicCallSummary] = []
        self._state = LoadState.UNAVAILABLE
        self._detail_dialog: PlainDetailDialog | None = None
        self._cached_detail_dialog: PlainDetailDialog | None = None
        self._detail_request: DetailRequest | None = None
        self._detail_token = 0
        self._column_mode: str | None = None
        self._source_connected = False
        self._sequence = 0
        self._query: CallLogQuery | None = None
        self._total_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 40)
        layout.setSpacing(16)
        layout.addWidget(plain_label("도구 호출 로그", name="pageTitle"))
        layout.addWidget(
            plain_label(
                "감사 조회에서 공개가 허용된 식별자와 처리 상태만 표시함.",
                name="secondary",
            )
        )
        self.state_message = StateMessage()
        layout.addWidget(self.state_message)
        self.search = QLineEdit()
        self.search.setAccessibleName("현재 조회 결과에서 호출 검색")
        self.search.setPlaceholderText("호출·요청·Task·서버 ID 또는 도구 이름 검색")
        self.search.textChanged.connect(self._search_edited)
        self.search.editingFinished.connect(self._search_finished)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("callLogTable")
        self.table.setAccessibleName("공개 호출 로그 목록")
        self.table.setHorizontalHeaderLabels(
            (
                "발생 시각",
                "도구 / 서버",
                "호출 / 요청",
                "종류",
                "최초 판정",
                "승인",
                "현재 상태",
            )
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.viewport().installEventFilter(self)
        self.table.itemClicked.connect(self._open_selected)
        self.table.itemActivated.connect(self._open_selected)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        # Store pages; shown only with a query source.
        self.pager = QWidget()
        pager = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.pager)
        pager.setContentsMargins(0, 0, 0, 0)
        self.count_label = plain_label("총건수 미제공 · 현재 페이지 0건", name="secondary")
        # The count takes the free width; a wrapping label beside a stretch got
        # only its narrow hint and broke mid-phrase.
        pager.addWidget(self.count_label, 1)
        self.previous_button = KeyboardActionButton("이전")
        self.previous_button.clicked.connect(self.previous_page)
        pager.addWidget(self.previous_button)
        self.page_label = plain_label("— / —", name="secondary", wrap=False)
        pager.addWidget(self.page_label)
        self.next_button = KeyboardActionButton("다음")
        self.next_button.clicked.connect(self.next_page)
        pager.addWidget(self.next_button)
        self.refresh_button = KeyboardActionButton("새로고침")
        self.refresh_button.set_visual(icon="refresh")
        self.refresh_button.clicked.connect(self.refresh)
        pager.addWidget(self.refresh_button)
        self.pager.hide()
        layout.addWidget(self.pager)

        self._actions_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        layout.addLayout(self._actions_layout)
        self.detail_button = KeyboardActionButton("선택한 호출 상세 보기")
        self.detail_button.set_visual(icon="eye", variant="soft")
        self.detail_button.clicked.connect(self._open_selected)
        self._actions_layout.addWidget(self.detail_button)
        self.csv_button = KeyboardActionButton("현재 표시 행 CSV 복사")
        self.csv_button.clicked.connect(self._copy_csv)
        self._actions_layout.addWidget(self.csv_button)
        self._actions_layout.addStretch(1)
        layout.addWidget(
            plain_label(
                "CSV에는 현재 조회·필터 결과의 공개 메타데이터만 들어감. "
                "파일 저장 경로는 감사 조회·파일 계약 확정 뒤 연결 예정.",
                name="secondary",
            )
        )
        self._set_controls_enabled(False)
        self._update_table_columns(force=True)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.width() < self._STACK_ACTIONS_WIDTH
            else QBoxLayout.Direction.LeftToRight
        )
        if self._actions_layout.direction() != direction:
            self._actions_layout.setDirection(direction)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_table_columns)
        return super().eventFilter(watched, event)

    def _update_table_columns(self, *, force: bool = False) -> None:
        """Keep time, call ID, and outcome visible; add server when it fits."""

        if not shiboken6.isValid(self.table):
            return
        viewport_width = self.table.viewport().width()
        if viewport_width < 100:
            return
        mode = (
            "narrow"
            if viewport_width < self._NARROW_VIEWPORT_WIDTH
            else "medium"
            if viewport_width < self._COMPACT_VIEWPORT_WIDTH
            else "full"
        )
        mode_changed = mode != self._column_mode
        self._column_mode = mode
        header = self.table.horizontalHeader()
        if mode_changed:
            visible = (
                {0, 2, 6}
                if mode == "narrow"
                else {0, 1, 2, 6}
                if mode == "medium"
                else set(range(7))
            )
            for column in range(7):
                self.table.setColumnHidden(column, column not in visible)
            server_header = self.table.horizontalHeaderItem(1)
            if server_header is not None:
                server_header.setText("서버 ID" if mode == "narrow" else "도구 / 서버")
            id_header = self.table.horizontalHeaderItem(2)
            if id_header is not None:
                id_header.setText("호출 ID" if mode != "full" else "호출 / 요청")
            header.setMinimumSectionSize(20)
            for column in range(7):
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        available = viewport_width - 2
        if mode == "narrow":
            first = available * 42 // 100
            second = available * 20 // 100
            widths = {0: first, 2: second, 6: available - first - second}
        elif mode == "medium":
            first = available * 21 // 100
            second = available * 21 // 100
            third = available * 31 // 100
            widths = {
                0: first,
                1: second,
                2: third,
                6: available - first - second - third,
            }
        else:
            proportions = (16, 16, 20, 8, 11, 12)
            widths = {
                column: available * percent // 100 for column, percent in enumerate(proportions)
            }
            widths[6] = available - sum(widths.values())
        for column, width in widths.items():
            self.table.setColumnWidth(column, width)

        if not (mode_changed or force):
            return
        for index, row in enumerate(self._visible_rows):
            self.table.setRowHeight(index, table_row_height(self.table, 2 if mode == "full" else 3))
            for column, value in enumerate(_cell_texts(row, mode)):
                item = self.table.item(index, column)
                if item is not None:
                    item.setText(value)

    def set_calls(self, surface: Surface[tuple[PublicCallSummary, ...]]) -> None:
        """Replace only the supplied audit page; never invent totals or results."""

        selected = self._selected_call_id()
        scroll = self.table.verticalScrollBar().value()
        self._state = surface.state
        valid = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and type(surface.value) is tuple
            and all(type(row) is PublicCallSummary for row in surface.value)
        )
        if surface.state in (LoadState.READY, LoadState.STALE) and not valid:
            self._state = LoadState.ERROR
        self._rows = surface.value if valid and surface.value is not None else ()
        self.state_message.set_state(self._state, as_of=surface.as_of)
        self._set_controls_enabled(valid)
        self._apply_filter()
        if selected:
            self.select_call(selected)
        self.table.verticalScrollBar().setValue(scroll)
        if (
            self._detail_dialog is not None
            and self._detail_request is not None
            and not any(row.call_id == self._detail_request.call_id for row in self._rows)
        ):
            self._detail_dialog.close()

    @property
    def current_query(self) -> CallLogQuery | None:
        return self._query

    @property
    def source_connected(self) -> bool:
        return self._source_connected

    def set_source_connected(self, connected: bool) -> None:
        """Switch between Store pages and supplied rows; never wait without a source."""

        self._source_connected = connected is True
        self._sequence += 1
        self._query = None
        self._total_count = 0
        self.pager.setVisible(self._source_connected)
        if self._source_connected:
            self.search.setAccessibleName("호출 로그 검색")
            self.search.setPlaceholderText(
                "요청·Task·서버 ID 또는 도구 이름의 앞부분 검색 · 대소문자 구분"
            )
        else:
            self.search.setAccessibleName("현재 조회 결과에서 호출 검색")
            self.search.setPlaceholderText("호출·요청·Task·서버 ID 또는 도구 이름 검색")
        if not self._source_connected:
            self.set_calls(Surface(LoadState.UNAVAILABLE))
            self.state_message.set_message(SOURCE_NOT_CONNECTED)
        self._set_controls_enabled(False)
        self.search.setEnabled(self._source_connected)
        self._update_pager()

    def refresh(self) -> None:
        """Read again from the Store."""

        self._request_page(self._query.page if self._query is not None else 1)

    def next_page(self) -> None:
        if (
            self._state is LoadState.READY
            and self._query is not None
            and self._query.page < self._page_count()
        ):
            self._request_page(self._query.page + 1)

    def previous_page(self) -> None:
        if self._state is LoadState.READY and self._query is not None and self._query.page > 1:
            self._request_page(self._query.page - 1)

    def _search_edited(self, *_args: object) -> None:
        if not self._source_connected:
            self._apply_filter()

    def _search_finished(self) -> None:
        # editingFinished also fires on focus loss; an unchanged search is no new query.
        if self._source_connected and (
            self._query is None or self._query.search != self.search.text().strip()
        ):
            self._request_page(1)

    def _request_page(self, page: int) -> None:
        if not self._source_connected:
            return
        self._sequence += 1
        search = self.search.text().strip()
        self._rows = ()
        self._total_count = 0
        if not valid_query_text(search):
            # Not sent: B accepts only this pattern, and the text stays as typed.
            self._query = None
            self._show_query_state(LoadState.ERROR, QUERY_TEXT_INVALID)
            return
        self._query = CallLogQuery(self._sequence, search, page)
        self._show_query_state(LoadState.LOADING, None)
        self.query_requested.emit(self._query)

    def set_page(self, result: CallLogResult) -> bool:
        """Apply only a current, structurally valid backend page."""

        if type(result) is not CallLogResult or result.query != self._query:
            return False
        query = result.query
        if (
            type(result.total_count) is not int
            or result.total_count < 0
            or type(result.rows) is not tuple
            or len(result.rows) > query.page_size
            or not all(type(row) is PublicCallSummary for row in result.rows)
        ):
            self.set_error(query)
            return False
        last_page = max(1, (result.total_count + query.page_size - 1) // query.page_size)
        if query.page > last_page:
            self._request_page(last_page)
            return False
        selected = self._selected_call_id()
        self._rows = result.rows
        self._total_count = result.total_count
        self._state = LoadState.READY
        self.state_message.set_state(self._state, as_of=result.as_of)
        self._set_controls_enabled(True)
        self._apply_filter()
        if selected:
            self.select_call(selected)
        self._update_pager()
        return True

    def set_error(self, query: CallLogQuery) -> bool:
        """A failed current query cannot reuse old rows or imply zero results."""

        if query != self._query:
            return False
        self._rows = ()
        self._total_count = 0
        self._show_query_state(LoadState.ERROR, QUERY_UNKNOWN)
        return True

    def _show_query_state(self, state: LoadState, message: str | None) -> None:
        self._state = state
        self.state_message.set_state(state)
        if message is not None:
            self.state_message.set_message(message)
        self._set_controls_enabled(False)
        self._apply_filter()
        self._update_pager()

    def _page_count(self) -> int:
        size = self._query.page_size if self._query is not None else CALL_PAGE_SIZE
        return max(1, (self._total_count + size - 1) // size)

    def _update_pager(self) -> None:
        ready = self._state is LoadState.READY and self._query is not None
        if ready and self._query is not None:
            self.count_label.setText(
                f"전체 {self._total_count:,}건 · 현재 페이지 {len(self._rows)}건"
            )
            self.page_label.setText(f"{self._query.page} / {self._page_count()}")
        else:
            self.count_label.setText("총건수 미제공 · 현재 페이지 0건")
            self.page_label.setText("— / —")
        page = self._query.page if self._query is not None else 1
        self.previous_button.setEnabled(ready and page > 1)
        self.next_button.setEnabled(ready and page < self._page_count())
        self.refresh_button.setEnabled(self._source_connected)

    def select_call(self, call_id: str) -> None:
        """Select a backend-supplied target if it exists in this query result."""

        for index, row in enumerate(self._visible_rows):
            if row.call_id == call_id:
                self.table.selectRow(index)
                item = self.table.item(index, 0)
                if item is not None:
                    self.table.scrollToItem(item)
                return

    def set_detail(self, request: DetailRequest, surface: Surface[PublicCallDetail]) -> None:
        """Apply only the response for this exact dialog opening and call ID."""

        dialog = self._detail_dialog
        if dialog is None or type(request) is not DetailRequest or request != self._detail_request:
            return
        if surface.state in (LoadState.READY, LoadState.STALE):
            value = surface.value
            if type(value) is PublicCallDetail and value.summary.call_id == request.call_id:
                text = _detail_text(value)
                if surface.state == LoadState.STALE:
                    text = "이전 조회 결과 · 다시 확인 필요\n\n" + text
            else:
                text = "호출 상세를 확인할 수 없음"
        else:
            text = StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
        dialog.body.setPlainText(text)

    def _set_controls_enabled(self, enabled: bool) -> None:
        # With a query source the search stays usable, so a refused or failed
        # query can be corrected and sent again.
        self.search.setEnabled(enabled or self._source_connected)
        self.table.setEnabled(enabled)
        self.csv_button.setEnabled(
            enabled and self._state == LoadState.READY and bool(self._visible_rows)
        )
        self.detail_button.setEnabled(enabled and self._selected_call_id() is not None)

    def _apply_filter(self, *_args: object) -> None:
        selected = self._selected_call_id()
        # Store pages are already searched by B (prefix, case kept).
        term = "" if self._source_connected else self.search.text().strip().casefold()
        self._visible_rows = [
            row
            for row in self._rows
            if not term
            or term
            in " ".join(
                value or ""
                for value in (
                    row.call_id,
                    row.request_id,
                    row.task_id,
                    row.server_id,
                    row.tool_name,
                )
            ).casefold()
        ]
        self.table.setRowCount(0)
        for index, row in enumerate(self._visible_rows):
            self.table.insertRow(index)
            for column, value in enumerate(_cell_texts(row, self._column_mode)):
                self.table.setItem(index, column, QTableWidgetItem(value))
            self.table.setRowHeight(index, table_row_height(self.table, 2))
            if row.call_id == selected:
                self.table.selectRow(index)
        if self._state == LoadState.READY:
            if not self._rows and self._query is not None and self._query.search:
                self.state_message.set_message("검색 결과가 없음")
            elif not self._rows:
                self.state_message.set_message("조회된 호출이 없음")
            elif not self._visible_rows:
                self.state_message.set_message("현재 조회 결과와 일치하는 호출이 없음")
        self.state_message.setVisible(self._state != LoadState.READY or not self._visible_rows)
        self._update_table_columns(force=True)
        self._selection_changed()

    def _selected_call_id(self) -> str | None:
        selected = self.table.selectionModel().selectedRows()
        if len(selected) != 1:
            return None
        index = selected[0].row()
        return self._visible_rows[index].call_id if 0 <= index < len(self._visible_rows) else None

    def _selection_changed(self) -> None:
        self.detail_button.setEnabled(self._selected_call_id() is not None)
        self.csv_button.setEnabled(self._state == LoadState.READY and bool(self._visible_rows))

    def _open_selected(self, *_args: object) -> None:
        call_id = self._selected_call_id()
        if call_id is None:
            return
        if self._detail_dialog is not None:
            self._detail_dialog.close()
        sender = self.sender()
        return_focus = self.table if sender is self.table else self.detail_button
        dialog = self._cached_detail_dialog
        if dialog is None:
            dialog = PlainDetailDialog(self)
            dialog.finished.connect(
                lambda result, opened=dialog: self._detail_closed(opened, result)
            )
            self._cached_detail_dialog = dialog
        self._detail_token += 1
        request = DetailRequest(call_id, self._detail_token)
        self._detail_dialog = dialog
        self._detail_request = request
        dialog.show_detail(
            title=f"호출 상세 · {_public_display_text(call_id)}",
            text="호출 상세 불러오는 중…",
            return_focus=return_focus,
        )
        self.detail_requested.emit(request)

    def _detail_closed(self, dialog: PlainDetailDialog, _result: int) -> None:
        dialog.body.clear()
        if self._detail_dialog is dialog:
            self._detail_dialog = None
            self._detail_request = None

    def _copy_csv(self) -> None:
        if self._state != LoadState.READY or not self._visible_rows:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(public_calls_csv(self._visible_rows))
