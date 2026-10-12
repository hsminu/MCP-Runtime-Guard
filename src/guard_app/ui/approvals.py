"""Read-only, paged approval summaries from a trusted backend query.

This view never loads approval arguments or decides expiry. The future B/C
query adapter must authorize the operator, provide only public summary fields,
and apply the requested global order and snapshot/continuation policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import ApprovalStatus

from .models import LoadState
from .presentation import MISSING, format_kst
from .widgets import KeyboardActionButton, StateMessage, plain_label


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


class ApprovalListPage(QWidget):
    """Searches six-row backend pages without approval/denial controls.

    ``query_requested`` must be connected to an authorized backend bridge.
    Replies are accepted only through ``set_page`` with the exact query token.
    They are display snapshots, never a source of authority for a control call.
    """

    query_requested = Signal(object)
    summary_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sequence = 0
        self._query: ApprovalQuery | None = None
        self._rows: tuple[ApprovalSummary, ...] = ()
        self._state = LoadState.UNAVAILABLE
        self._total_count = 0
        self._selected_id: str | None = None

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

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("승인·Task·요청 ID 검색")
        self.search.setAccessibleName("승인 검색어")
        self.search.editingFinished.connect(self._filters_changed)
        filters.addWidget(self.search, 2)
        self.server_filter = QLineEdit()
        self.server_filter.setPlaceholderText("MCP 서버 ID")
        self.server_filter.setAccessibleName("MCP 서버 ID 필터")
        self.server_filter.editingFinished.connect(self._filters_changed)
        filters.addWidget(self.server_filter, 1)
        self.sort = QComboBox()
        self.sort.addItem("오래된 순", ApprovalSort.OLDEST)
        self.sort.addItem("최신 순", ApprovalSort.NEWEST)
        self.sort.setAccessibleName("승인 목록 정렬")
        self.sort.currentIndexChanged.connect(self._filters_changed)
        filters.addWidget(self.sort)
        layout.addLayout(filters)

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
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        pager = QHBoxLayout()
        self.count_label = plain_label(
            "총건수 미제공 · 현재 페이지 0건", name="secondary"
        )
        pager.addWidget(self.count_label)
        pager.addStretch(1)
        self.previous_button = KeyboardActionButton("이전")
        self.previous_button.clicked.connect(self.previous_page)
        pager.addWidget(self.previous_button)
        self.page_label = plain_label("— / —", name="secondary", wrap=False)
        pager.addWidget(self.page_label)
        self.next_button = KeyboardActionButton("다음")
        self.next_button.clicked.connect(self.next_page)
        pager.addWidget(self.next_button)
        self.refresh_button = KeyboardActionButton("새로고침")
        self.refresh_button.clicked.connect(self.refresh)
        pager.addWidget(self.refresh_button)
        layout.addLayout(pager)
        self._update_pager()

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
        if (
            self._state is LoadState.READY
            and self._query is not None
            and self._query.page > 1
        ):
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
        self._sequence += 1
        self._query = ApprovalQuery(
            token=self._sequence,
            lane=(
                ApprovalLane.PENDING
                if self.lane_tabs.currentIndex() == 0
                else ApprovalLane.HISTORY
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

    def set_page(
        self, page: ApprovalPage, *, display_now: datetime | None = None
    ) -> bool:
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
        for index, row in enumerate(page.rows):
            self.table.insertRow(index)
            status = _STATUS_TEXT[row.status]
            if (
                page.query.lane is ApprovalLane.PENDING
                and now.tzinfo is not None
                and now - row.created_at >= timedelta(minutes=30)
            ):
                status += " · 30분 이상 대기(참고)"
            columns = (
                row.approval_id,
                row.server_id,
                f"{row.task_id}\n{row.request_id}",
                row.destination_summary or MISSING,
                status,
                f"{format_kst(row.created_at)}\n{format_kst(row.resolved_at)}",
            )
            for column, value in enumerate(columns):
                self.table.setItem(index, column, QTableWidgetItem(value))
            self.table.setRowHeight(index, 64)
            if row.approval_id == previous_id:
                self.table.selectRow(index)
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
        if ready:
            assert self._query is not None
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
            self.summary_selected.emit(summary)
