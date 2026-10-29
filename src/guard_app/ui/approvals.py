"""Read-only, paged approval summaries from a trusted backend query.

This view never loads approval arguments or decides expiry. The future B/C
query adapter must authorize the operator, provide only public summary fields,
and apply the requested global order and snapshot/continuation policy.
"""

from __future__ import annotations

from datetime import UTC, datetime

import shiboken6
from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .approval_presentation import (
    _NARROW_STATUS_TEXT as _NARROW_STATUS_TEXT,
)
from .approval_presentation import (
    _STATUS_TEXT as _STATUS_TEXT,
)
from .approval_presentation import (
    PAGE_SIZE as PAGE_SIZE,
)
from .approval_presentation import (
    QUERY_TEXT_INVALID,
    QUERY_UNKNOWN,
    SERVER_FILTER_INVALID,
    selected_detail_text,
    status_display,
    summary_columns,
    valid_page,
    valid_query_text,
    valid_server_filter,
)
from .approval_presentation import (
    SOURCE_NOT_CONNECTED as SOURCE_NOT_CONNECTED,
)
from .approval_presentation import (
    ApprovalLane as ApprovalLane,
)
from .approval_presentation import (
    ApprovalPage as ApprovalPage,
)
from .approval_presentation import (
    ApprovalQuery as ApprovalQuery,
)
from .approval_presentation import (
    ApprovalSort as ApprovalSort,
)
from .approval_presentation import (
    ApprovalSummary as ApprovalSummary,
)
from .approval_presentation import (
    _bounded_request_age as _bounded_request_age,
)
from .approval_rows import ApprovalTable
from .models import LoadState
from .widgets import KeyboardActionButton, SelectionBox, StateMessage, plain_label


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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 40)
        layout.setSpacing(16)
        layout.addWidget(plain_label("승인 대기와 처리 이력", name="pageTitle"))
        layout.addWidget(
            plain_label(
                "승인 상태와 총건수는 백엔드 조회 결과임. 대기 시간 표시는 참고 정보임.",
                name="secondary",
            )
        )

        self.lane_tabs = QTabBar()
        self.lane_tabs.addTab("승인 대기")
        self.lane_tabs.addTab("처리 이력")
        self.lane_tabs.setAccessibleName("승인 대기와 처리 이력 선택")
        self.lane_tabs.currentChanged.connect(self._filters_changed)
        layout.addWidget(self.lane_tabs)

        # Attach before adding widgets: an orphan layout leaves them Python-owned (A178).
        self.filters = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        layout.addLayout(self.filters)
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

        self.state_message = StateMessage()
        layout.addWidget(self.state_message)
        self.table = ApprovalTable(0, 6)
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
        layout.addLayout(self.pager)
        self.count_label = plain_label("총건수 미제공 · 현재 페이지 0건", name="secondary")
        # The count takes the free width (see call_logs: it broke mid-phrase).
        self.pager.addWidget(self.count_label, 1)
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
            self.table.update_status(
                index,
                status_display(
                    row,
                    self._query,
                    self._display_now,
                    narrow=self._column_mode == "narrow",
                ),
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
        search = self.search.text().strip()
        server_id = self.server_filter.text()
        error = None
        if not valid_query_text(search):
            error = QUERY_TEXT_INVALID
        elif not valid_server_filter(server_id):
            error = SERVER_FILTER_INVALID
        if error is not None:
            # Invalid input is not sent or interpolated into the fixed message.
            self._query = None
            self._clear_rows()
            self._state = LoadState.ERROR
            self.state_message.set_message(error)
            self.state_message.setVisible(True)
            self._update_pager()
            return
        self._query = ApprovalQuery(
            token=self._sequence,
            lane=(
                ApprovalLane.PENDING if self.lane_tabs.currentIndex() == 0 else ApprovalLane.HISTORY
            ),
            search=search,
            server_id=server_id,
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
            self.table.insert_summary(index, summary_columns(row))
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
                self.state_message.set_message("검색·필터 결과가 없음")
            elif page.query.lane is ApprovalLane.PENDING:
                self.state_message.set_message("대기 중인 승인 요청이 없음")
            else:
                self.state_message.set_message("처리 이력이 없음")
        return True

    def set_error(self, query: ApprovalQuery) -> bool:
        """A failed current query cannot reuse old rows or imply zero results."""

        if query != self._query:
            return False
        self._clear_rows()
        self._state = LoadState.ERROR
        self.state_message.set_message(QUERY_UNKNOWN)
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

    _valid_page = staticmethod(valid_page)

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

    _selected_detail_text = staticmethod(selected_detail_text)
