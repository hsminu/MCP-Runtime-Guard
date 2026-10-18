"""Read-only, paged approval summaries from a trusted backend query.

This view never loads approval arguments or decides expiry. The future B/C
query adapter must authorize the operator, provide only public summary fields,
and apply the requested global order and snapshot/continuation policy.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import shiboken6
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import ApprovalStatus

from .models import LoadState
from .presentation import MISSING, format_kst, public_display_text
from .widgets import KeyboardActionButton, SelectionBox, StateMessage, plain_label


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
    row_revision: int | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalPage:
    """One backend page and its server-derived total, not a local row cache."""

    query: ApprovalQuery
    rows: tuple[ApprovalSummary, ...]
    total_count: int
    as_of: datetime | None = None


DEFAULT_APPROVAL_TTL_SECONDS = 300
SOURCE_NOT_CONNECTED = "승인 목록 조회가 아직 연결되지 않았어 · 승인 요청은 대화 화면에서 처리해."


class ApprovalListPage(QWidget):
    """Searches six-row backend pages without approval/denial controls.

    ``query_requested`` must be connected to an authorized backend bridge.
    Replies are accepted only through ``set_page`` with the exact query token.
    They are display snapshots, never a source of authority for a control call.
    """

    query_requested = Signal(object)
    summary_selected = Signal(object)
    _FULL_TABLE_WIDTH = 850
    _MEDIUM_TABLE_WIDTH = 560
    _STACK_CONTROLS_WIDTH = 690

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sequence = 0
        self._query: ApprovalQuery | None = None
        self._rows: tuple[ApprovalSummary, ...] = ()
        self._state = LoadState.UNAVAILABLE
        self._total_count = 0
        self._selected_id: str | None = None
        self._column_mode: str | None = None
        self._display_now: datetime | None = None
        self._source_connected = True
        # Profile approval TTL (GuardSettings); a PENDING row older than this
        # should already be expiring, so it is flagged for a fresh read.
        self._approval_ttl_seconds = DEFAULT_APPROVAL_TTL_SECONDS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 28, 26, 42)
        layout.setSpacing(14)
        layout.addWidget(plain_label("APPROVALS", name="eyebrow"))
        layout.addWidget(plain_label("승인 대기와 처리 이력", name="pageTitle"))
        layout.addWidget(
            plain_label(
                "승인 상태와 총건수는 백엔드 조회 결과야. 대기 시간 표시는 참고 정보야.",
                name="secondary",
            )
        )

        self.lane_tabs = QTabBar()
        self.lane_tabs.addTab("승인 대기")
        self.lane_tabs.addTab("처리 이력")
        self.lane_tabs.setAccessibleName("승인 대기와 처리 이력 선택")
        self.lane_tabs.currentChanged.connect(self._filters_changed)
        layout.addWidget(self.lane_tabs)

        self.filters = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.search = QLineEdit()
        self.search.setPlaceholderText("승인·Task·요청 ID 검색")
        self.search.setAccessibleName("승인 검색어")
        self.search.editingFinished.connect(self._filters_changed)
        self.filters.addWidget(self.search, 2)
        self.server_filter = QLineEdit()
        self.server_filter.setPlaceholderText("MCP 서버 ID")
        self.server_filter.setAccessibleName("MCP 서버 ID 필터")
        self.server_filter.editingFinished.connect(self._filters_changed)
        self.filters.addWidget(self.server_filter, 1)
        self.sort = SelectionBox()
        self.sort.addItem("오래된 순", ApprovalSort.OLDEST)
        self.sort.addItem("최신 순", ApprovalSort.NEWEST)
        self.sort.setAccessibleName("승인 목록 정렬")
        self.sort.currentIndexChanged.connect(self._filters_changed)
        self.filters.addWidget(self.sort)
        layout.addLayout(self.filters)

        self.state_message = StateMessage()
        layout.addWidget(self.state_message)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("approvalTable")
        self.table.setAccessibleName("승인 요청 요약 목록")
        self.table.setHorizontalHeaderLabels(
            [
                "승인 ID",
                "MCP 서버",
                "Task / 요청",
                "수신처 요약",
                "상태",
                "요청 / 처리 시각",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.viewport().installEventFilter(self)
        layout.addWidget(self.table, 1)

        self.selection_detail = QPlainTextEdit()
        self.selection_detail.setAccessibleName("선택한 승인 공개 요약")
        self.selection_detail.setReadOnly(True)
        self.selection_detail.setUndoRedoEnabled(False)
        self.selection_detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.selection_detail.setFixedHeight(116)
        self.selection_detail.hide()
        layout.addWidget(self.selection_detail)

        self.pager = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.count_label = plain_label("총건수 미제공 · 현재 페이지 0건", name="secondary")
        self.pager.addWidget(self.count_label)
        self.pager.addStretch(1)
        self.previous_button = KeyboardActionButton("이전")
        self.previous_button.clicked.connect(self.previous_page)
        self.pager.addWidget(self.previous_button)
        self.page_label = plain_label("— / —", name="secondary", wrap=False)
        self.pager.addWidget(self.page_label)
        self.next_button = KeyboardActionButton("다음")
        self.next_button.clicked.connect(self.next_page)
        self.pager.addWidget(self.next_button)
        self.refresh_button = KeyboardActionButton("새로고침")
        self.refresh_button.set_visual(icon="refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.pager.addWidget(self.refresh_button)
        layout.addLayout(self.pager)
        self._update_pager()
        self._update_table_columns()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.width() < self._STACK_CONTROLS_WIDTH
            else QBoxLayout.Direction.LeftToRight
        )
        if self.filters.direction() != direction:
            self.filters.setDirection(direction)
            self.pager.setDirection(direction)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.viewport() and event.type() is QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_table_columns)
        return super().eventFilter(watched, event)

    def _update_table_columns(self) -> None:
        """Fit visible columns to the real table viewport, including scrollbars."""

        if not shiboken6.isValid(self.table):
            return
        viewport_width = self.table.viewport().width()
        if viewport_width < 100:
            return
        mode = (
            "narrow"
            if viewport_width < self._MEDIUM_TABLE_WIDTH
            else "medium"
            if viewport_width < self._FULL_TABLE_WIDTH
            else "full"
        )
        if mode != self._column_mode:
            self._column_mode = mode
            visible = (
                {0, 1, 4}
                if mode == "narrow"
                else {0, 1, 4, 5}
                if mode == "medium"
                else set(range(6))
            )
            for column in range(6):
                self.table.setColumnHidden(column, column not in visible)
            status_header = self.table.horizontalHeaderItem(4)
            if status_header is not None:
                status_header.setText("상태 / 경과" if mode == "narrow" else "상태")
            self._update_status_cells()

        available = viewport_width - 2
        if mode == "narrow":
            first = available * 33 // 100
            second = available * 27 // 100
            widths = {0: first, 1: second, 4: available - first - second}
        elif mode == "medium":
            first = available * 20 // 100
            second = available * 19 // 100
            third = available * 35 // 100
            widths = {
                0: first,
                1: second,
                4: third,
                5: available - first - second - third,
            }
        else:
            fractions = (15, 13, 18, 16, 18)
            widths = {
                column: available * percent // 100 for column, percent in enumerate(fractions)
            }
            widths[5] = available - sum(widths.values())
        for column, width in widths.items():
            self.table.setColumnWidth(column, width)

    def _update_status_cells(self) -> None:
        for index, row in enumerate(self._rows):
            item = self.table.item(index, 4)
            if item is None:
                continue
            status = _STATUS_TEXT[row.status]
            now = self._display_now
            if (
                self._query is not None
                and self._query.lane is ApprovalLane.PENDING
                and now is not None
                and now.tzinfo is not None
                and now.utcoffset() is not None
                and now - row.created_at >= timedelta(seconds=self._approval_ttl_seconds)
            ):
                status += " · 승인 유효 시간 지남 · 상태 다시 확인(참고)"
            if self._column_mode == "narrow":
                age = _bounded_request_age(row.created_at, now) if now else None
                if row.status in (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED):
                    elapsed = "결과 별도 확인"
                elif age is None:
                    elapsed = "요청 시각 확인"
                elif row.status is ApprovalStatus.PENDING:
                    elapsed = f"{age} 대기"
                else:
                    elapsed = f"요청 {age} 전"
                item.setText(f"{_NARROW_STATUS_TEXT[row.status]}\n{elapsed}")
                self.table.setRowHeight(index, 42)
            else:
                item.setText(status)
                self.table.setRowHeight(index, 64)
            requested_at = format_kst(row.created_at)
            item.setToolTip(f"상태: {status}\n요청 시각: {requested_at}")
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"상태: {status}, 요청 시각: {requested_at}",
            )

    @property
    def current_query(self) -> ApprovalQuery | None:
        return self._query

    @property
    def state(self) -> LoadState:
        return self._state

    @property
    def selected_summary(self) -> ApprovalSummary | None:
        return next(
            (row for row in self._rows if row.approval_id == self._selected_id),
            None,
        )

    def set_approval_ttl_seconds(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 1:
            raise ValueError("positive approval TTL required")
        self._approval_ttl_seconds = seconds

    @property
    def source_connected(self) -> bool:
        return self._source_connected

    def set_source_connected(self, connected: bool) -> None:
        """Without a backend query source, never enter LOADING and wait forever."""

        self._source_connected = connected is True
        for control in (self.lane_tabs, self.search, self.server_filter, self.sort):
            control.setEnabled(self._source_connected)
        self.refresh_button.setEnabled(self._source_connected)
        if not self._source_connected:
            self._sequence += 1
            self._query = None
            self._clear_rows()
            self._state = LoadState.UNAVAILABLE
            self.state_message.set_message(SOURCE_NOT_CONNECTED)
            self.state_message.setVisible(True)
            self._update_pager()

    def refresh(self) -> None:
        """Read again; never resend an approval/denial command."""

        page = self._query.page if self._query is not None else 1
        self._request_page(page)

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

    def select_approval(self, approval_id: str) -> bool:
        """Select only a row already in this confirmed page, without fetching."""

        if self._state is LoadState.READY:
            for index, row in enumerate(self._rows):
                if row.approval_id == approval_id:
                    self.table.selectRow(index)
                    return True
        self.table.clearSelection()
        return False

    def _filters_changed(self, *_args: object) -> None:
        self._request_page(1)

    def _request_page(self, page: int) -> None:
        if not self._source_connected:
            return
        self._sequence += 1
        self._query = ApprovalQuery(
            token=self._sequence,
            lane=(
                ApprovalLane.PENDING if self.lane_tabs.currentIndex() == 0 else ApprovalLane.HISTORY
            ),
            search=self.search.text().strip(),
            server_id=self.server_filter.text().strip(),
            sort=ApprovalSort(self.sort.currentData()),
            page=page,
        )
        self._clear_rows()
        self._state = LoadState.LOADING
        self.state_message.set_state(self._state)
        self.state_message.setVisible(True)
        self._update_pager()
        self.query_requested.emit(self._query)

    def set_page(self, page: ApprovalPage, *, display_now: datetime | None = None) -> bool:
        """Apply only a current, structurally valid backend response."""

        if type(page) is not ApprovalPage or page.query != self._query:
            return False
        if not self._valid_page(page):
            self.set_error(page.query)
            return False
        last_page = max(1, (page.total_count + PAGE_SIZE - 1) // PAGE_SIZE)
        if page.query.page > last_page:
            self._request_page(last_page)
            return False

        previous_id = self._selected_id
        self._rows = page.rows
        self._total_count = page.total_count
        self._state = LoadState.READY
        self.table.setRowCount(0)
        now = display_now if display_now is not None else datetime.now(UTC)
        self._display_now = now
        for index, row in enumerate(page.rows):
            self.table.insertRow(index)
            columns = (
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
            for column, value in enumerate(columns):
                self.table.setItem(index, column, QTableWidgetItem(value))
            if row.approval_id == previous_id:
                self.table.selectRow(index)
        self._update_status_cells()
        if previous_id is not None and self.selected_summary is None:
            self._selected_id = None
            self.summary_selected.emit(None)
        self._update_pager()
        self.state_message.setVisible(not page.rows)
        if not page.rows:
            if page.query.search or page.query.server_id:
                self.state_message.set_message("검색·필터 결과가 없어")
            elif page.query.lane is ApprovalLane.PENDING:
                self.state_message.set_message("대기 중인 승인 요청이 없어")
            else:
                self.state_message.set_message("처리 이력이 없어")
        return True

    def set_error(self, query: ApprovalQuery) -> bool:
        """A failed current query cannot reuse old rows or imply zero results."""

        if query != self._query:
            return False
        self._clear_rows()
        self._state = LoadState.ERROR
        self.state_message.set_state(self._state)
        self.state_message.setVisible(True)
        self._update_pager()
        return True

    def _clear_rows(self) -> None:
        self.table.setRowCount(0)
        self._rows = ()
        self._total_count = 0
        self._display_now = None
        self.selection_detail.clear()
        self.selection_detail.hide()
        if self._selected_id is not None:
            self._selected_id = None
            self.summary_selected.emit(None)

    @staticmethod
    def _valid_page(page: ApprovalPage) -> bool:
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
                or (
                    (row.status is ApprovalStatus.PENDING)
                    != (page.query.lane is ApprovalLane.PENDING)
                )
            ):
                return False
            seen.add(row.approval_id)
        ordered = sorted(
            page.rows,
            key=lambda row: (row.created_at, row.approval_id),
            reverse=page.query.sort is ApprovalSort.NEWEST,
        )
        return list(page.rows) == ordered

    def _page_count(self) -> int:
        return max(1, (self._total_count + PAGE_SIZE - 1) // PAGE_SIZE)

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
        current_page = self._query.page if self._query is not None else 1
        self.previous_button.setEnabled(ready and current_page > 1)
        self.next_button.setEnabled(ready and current_page < self._page_count())

    def _selection_changed(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        selected = selected_rows[0].row() if len(selected_rows) == 1 else -1
        summary = self._rows[selected] if 0 <= selected < len(self._rows) else None
        new_id = summary.approval_id if summary is not None else None
        if new_id != self._selected_id:
            self._selected_id = new_id
            self.selection_detail.setVisible(summary is not None)
            self.selection_detail.setPlainText(
                "" if summary is None else self._selected_detail_text(summary)
            )
            self.summary_selected.emit(summary)

    @staticmethod
    def _selected_detail_text(row: ApprovalSummary) -> str:
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
                f"요청 시각: {format_kst(row.created_at)}",
                f"처리 시각: {format_kst(row.resolved_at)}",
            )
        )
