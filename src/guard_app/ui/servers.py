"""Read-only Registry presentation limited to a public server projection."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QDialog,
    QFrame,
    QHeaderView,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import LoadState, ServerSummary, Surface
from .presentation import (
    REGISTRY_STATUSES,
    SESSION_STATUSES,
    TIERS,
    TRANSPORTS,
    fixed,
)
from .scan_status import ScanStatusCard, ScanStatusProjection
from .widgets import (
    KeyboardActionButton,
    SecurityLabel,
    StateMessage,
    plain_label,
    size_detail_for_viewport,
)


def _detail_field(title: str, value: str) -> QWidget:
    field = QWidget()
    field.setObjectName("infoField")
    layout = QVBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(plain_label(title, name="secondary"))
    value_label = plain_label(value)
    value_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    value_label.setMinimumWidth(0)
    value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(value_label)
    return field


class ServerDetailDialog(QDialog):
    """Displays only fields in ``ServerSummary``; no raw Registry config."""

    def __init__(self, server: ServerSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server_id = server.server_id
        self.setWindowTitle(f"MCP 서버 상세 · {server.server_id}")
        self.setModal(True)
        self.setMinimumSize(240, 200)
        size_detail_for_viewport(self, 700, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(14)
        outer.addWidget(plain_label("MCP 서버 등록 상세", name="pageTitle"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("detailBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        layout.addWidget(
            _detail_field("표시 이름", server.display_name or server.server_id)
        )
        layout.addWidget(_detail_field("서버 식별자 · server_id", server.server_id))
        self.scan_status_card = ScanStatusCard()
        layout.addWidget(self.scan_status_card)
        layout.addWidget(SecurityLabel(server.max_label))
        layout.addWidget(
            _detail_field("연결 방식 · transport", fixed(TRANSPORTS, server.transport))
        )
        layout.addWidget(_detail_field("신뢰 경계 · tier", fixed(TIERS, server.tier)))
        layout.addWidget(
            _detail_field(
                "Registry 등록 상태", fixed(REGISTRY_STATUSES, server.registry_status)
            )
        )
        layout.addWidget(
            _detail_field("세션 상태", fixed(SESSION_STATUSES, server.session_status))
        )
        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        close_button = KeyboardActionButton("닫기")
        close_button.clicked.connect(self.close)
        outer.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)


class ServerListPage(QWidget):
    """Filters only rows in the supplied Registry query result."""

    scan_snapshot_requested = Signal(str, int)
    _FULL_TABLE_WIDTH = 970
    _TINY_TABLE_WIDTH = 440
    _STACK_FILTER_WIDTH = 480

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._servers: tuple[ServerSummary, ...] = ()
        self._visible_servers: list[ServerSummary] = []
        self._state = LoadState.UNAVAILABLE
        self._detail_dialog: ServerDetailDialog | None = None
        self._detail_return_focus: QWidget | None = None
        self._scan_request_sequence = 0
        self._active_scan_token: int | None = None
        self._target_id: str | None = None
        self._compact_columns: tuple[bool, bool] | None = None
        self._stacked_filters: bool | None = None
        self._layout = QVBoxLayout(self)
        layout = self._layout
        layout.setContentsMargins(26, 28, 26, 42)
        layout.setSpacing(16)
        layout.addWidget(plain_label("SERVER REGISTRY", name="eyebrow"))
        layout.addWidget(plain_label("MCP 서버", name="pageTitle"))
        layout.addWidget(
            plain_label(
                "등록 상태, 세션 연결, 신뢰 경계와 최대 수신 등급을 각각 확인해.",
                name="secondary",
            )
        )
        self.state_message = StateMessage()
        layout.addWidget(self.state_message)

        filters = QVBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("서버 ID 또는 표시 이름 검색")
        self.search.setAccessibleName("현재 조회 결과에서 서버 검색")
        self.search.textChanged.connect(self._apply_filters)
        filters.addWidget(self.search)
        self._choices = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._choices.setSpacing(8)
        self.transport_filter = QComboBox()
        self.transport_filter.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.transport_filter.addItem("전체 transport", None)
        self.transport_filter.addItem("stdio", "stdio")
        self.transport_filter.addItem("Streamable HTTP", "streamable_http")
        self.transport_filter.setAccessibleName("전송 방식 필터")
        self.transport_filter.currentIndexChanged.connect(self._apply_filters)
        self._choices.addWidget(self.transport_filter)
        self.status_filter = QComboBox()
        self.status_filter.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.status_filter.addItem("전체 등록 상태", None)
        for status in ("active", "pending", "blocked"):
            self.status_filter.addItem(REGISTRY_STATUSES[status], status)
        self.status_filter.setAccessibleName("등록 상태 필터")
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        self._choices.addWidget(self.status_filter)
        filters.addLayout(self._choices)
        layout.addLayout(filters)
        layout.addWidget(
            plain_label(
                "필터는 현재 전달된 Registry 조회 결과에만 적용돼.", name="secondary"
            )
        )
        self.table_hint = plain_label(
            "좁은 화면에서는 연결 방식·신뢰 경계·세션을 서버 상세에서 확인해.",
            name="secondary",
        )
        layout.addWidget(self.table_hint)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("serverTable")
        self.table.setAccessibleName("MCP 서버 목록")
        self.table.setHorizontalHeaderLabels(
            [
                "서버 ID / 표시 이름",
                "transport",
                "신뢰 경계",
                "최대 수신",
                "등록 상태",
                "세션",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self.table.setColumnWidth(0, 250)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemActivated.connect(self._open_selected)
        layout.addWidget(self.table, 1)
        self.detail_button = KeyboardActionButton("선택한 서버 상세 보기")
        self.detail_button.clicked.connect(self._open_selected)
        self.detail_button.setEnabled(False)
        layout.addWidget(self.detail_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._set_filters_enabled(False)
        self._update_table_columns()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_table_columns)

    def _update_table_columns(self) -> None:
        width = self.table.width()
        compact = width < self._FULL_TABLE_WIDTH
        tiny = width < self._TINY_TABLE_WIDTH
        stacked = self.width() < self._STACK_FILTER_WIDTH
        if stacked != self._stacked_filters:
            self._stacked_filters = stacked
            self._choices.setDirection(
                QBoxLayout.Direction.TopToBottom
                if stacked
                else QBoxLayout.Direction.LeftToRight
            )
            margin = 12 if stacked else 26
            self._layout.setContentsMargins(
                margin, 16 if stacked else 28, margin, 24 if stacked else 42
            )
        if (compact, tiny) == self._compact_columns:
            return
        self._compact_columns = (compact, tiny)
        for column in (1, 2, 5):
            self.table.setColumnHidden(column, compact)
        self.table.setColumnHidden(3, tiny)
        first_header = self.table.horizontalHeaderItem(0)
        if first_header is not None:
            first_header.setText("서버 ID" if compact else "서버 ID / 표시 이름")
        header = self.table.horizontalHeader()
        if compact:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 250)
        self.table_hint.setText(
            "좁은 화면에서는 최대 수신 등급·연결 방식·신뢰 경계·세션을 서버 상세에서 확인해."
            if tiny
            else "좁은 화면에서는 연결 방식·신뢰 경계·세션을 서버 상세에서 확인해."
        )
        self.table_hint.setVisible(compact)

    def set_servers(self, surface: Surface[tuple[ServerSummary, ...]]) -> None:
        """The bridge supplies the Registry result; overview rows are not reused."""

        if self._detail_dialog is not None:
            self._detail_dialog.close()
            self._detail_dialog = None
            self._active_scan_token = None
        self._state = surface.state
        ready = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and surface.value is not None
        )
        self._servers = tuple(surface.value or ()) if ready else ()
        self._set_filters_enabled(ready)
        self.state_message.set_state(surface.state, as_of=surface.as_of)
        self._apply_filters()
        self.state_message.setVisible(
            not ready or not self._visible_servers or surface.state == LoadState.STALE
        )

    def _set_filters_enabled(self, enabled: bool) -> None:
        self.search.setEnabled(enabled)
        self.transport_filter.setEnabled(enabled)
        self.status_filter.setEnabled(enabled)

    def _apply_filters(self, *_args) -> None:
        previous_id = self._selected_id()
        term = self.search.text().strip().casefold()
        transport = self.transport_filter.currentData()
        status = self.status_filter.currentData()
        self._visible_servers = [
            server
            for server in self._servers
            if (
                not term
                or term in server.server_id.casefold()
                or term in server.display_name.casefold()
            )
            and (transport is None or server.transport == transport)
            and (status is None or server.registry_status == status)
        ]
        self.table.setRowCount(0)
        for row, server in enumerate(self._visible_servers):
            self.table.insertRow(row)
            label = f"{server.server_id}\n{server.display_name or server.server_id}"
            columns = (
                label,
                fixed(TRANSPORTS, server.transport),
                fixed(TIERS, server.tier),
                self._label_text(server.max_label),
                fixed(REGISTRY_STATUSES, server.registry_status),
                fixed(SESSION_STATUSES, server.session_status),
            )
            for column, value in enumerate(columns):
                item = QTableWidgetItem(value)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 62)
            if server.server_id == previous_id:
                self.table.selectRow(row)
        self._selection_changed()
        if self._target_id:
            self._select_visible(self._target_id)
        if self._state == LoadState.READY:
            self.state_message.setVisible(not self._visible_servers)
            if self._servers and not self._visible_servers:
                self.state_message.set_message("현재 조회 결과와 일치하는 서버가 없어")
            elif not self._servers:
                self.state_message.set_message("등록된 MCP 서버가 없어")

    @staticmethod
    def _label_text(label: str | None) -> str:
        return {
            "PUBLIC": "공개 · PUBLIC",
            "INTERNAL": "내부 · INTERNAL",
            "CONFIDENTIAL": "기밀 · CONFIDENTIAL",
        }.get(label or "", "미지정 · 미제공")

    def _selected_id(self) -> str | None:
        selected_rows = self.table.selectionModel().selectedRows()
        if len(selected_rows) != 1:
            return None
        row = selected_rows[0].row()
        return (
            self._visible_servers[row].server_id
            if 0 <= row < len(self._visible_servers)
            else None
        )

    def _selection_changed(self) -> None:
        self.detail_button.setEnabled(self._selected_id() is not None)

    def select_server(self, server_id: str) -> None:
        self._target_id = server_id
        self._select_visible(server_id)

    def set_scan_snapshot(
        self, server_id: str, token: int, surface: Surface[ScanStatusProjection]
    ) -> bool:
        """Apply only the reply for the currently open server detail query."""

        dialog = self._detail_dialog
        if (
            dialog is None
            or dialog.server_id != server_id
            or self._active_scan_token != token
        ):
            return False
        dialog.scan_status_card.set_snapshot(surface)
        return True

    def _select_visible(self, server_id: str) -> None:
        for row, server in enumerate(self._visible_servers):
            if server.server_id == server_id:
                self.table.selectRow(row)
                item = self.table.item(row, 0)
                if item is not None:
                    self.table.scrollToItem(item)
                self._target_id = None
                return

    def _open_selected(self, *_args) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if len(selected_rows) != 1:
            return
        row = selected_rows[0].row()
        if not (0 <= row < len(self._visible_servers)):
            return
        if self._detail_dialog is not None:
            self._detail_dialog.close()
        sender = self.sender()
        if sender is self.table:
            self._detail_return_focus = self.table
        elif sender is self.detail_button:
            self._detail_return_focus = self.detail_button
        else:
            focused = self.focusWidget()
            self._detail_return_focus = (
                focused
                if focused in (self.table, self.detail_button)
                else self.detail_button
            )
        dialog = ServerDetailDialog(self._visible_servers[row], self)
        dialog.finished.connect(
            lambda result, opened=dialog: self._detail_finished(opened, result)
        )
        self._detail_dialog = dialog
        dialog.open()
        self._scan_request_sequence += 1
        self._active_scan_token = self._scan_request_sequence
        self.scan_snapshot_requested.emit(dialog.server_id, self._active_scan_token)

    def _detail_finished(self, dialog: ServerDetailDialog, _result: int) -> None:
        if self._detail_dialog is not dialog:
            return
        self._detail_dialog = None
        self._active_scan_token = None
        QTimer.singleShot(0, self._restore_detail_focus)

    def _restore_detail_focus(self) -> None:
        if self._detail_dialog is not None or not self.isVisible():
            return
        target = self._detail_return_focus
        self._detail_return_focus = None
        if target is not None and target.isEnabled() and target.isVisibleTo(self):
            target.setFocus()
        elif self.search.isEnabled():
            self.search.setFocus()
