"""Read-only Registry presentation limited to a public server projection."""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QDialog,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.services.bridge import (
    BackendBridge,
    CommandEvent,
    CommandStage,
    RegistrationRequest,
    RegistrationView,
    ScanState,
    new_command_id,
)

from .models import LoadState, ServerSummary, Surface
from .policy import CHANGE_RESULTS, ToolAllowDialog
from .presentation import (
    LABELS,
    REGISTRY_NAMES,
    REGISTRY_STATUSES,
    SESSION_STATUSES,
    TIER_NAMES,
    TIERS,
    TRANSPORTS,
    fixed,
    fixed_code,
    public_display_text,
)
from .scan_report import (
    ScanReportPanel,
    ScanReviewDialog,
    activation_scan_text,
    attach_activation_scan,
)
from .tool_settings import ToolSettingsDialog
from .widgets import (
    KeyboardActionButton,
    ResponsiveRow,
    SecurityLabel,
    SelectionBox,
    StateMessage,
    plain_label,
    size_detail_for_viewport,
    table_row_height,
)


def _pin_status(server: ServerSummary) -> str:
    if server.executable_pinned is None:
        return "해당 없음 또는 미확인"
    return "저장 지문과 일치" if server.executable_pinned else "지문 재확인 필요"


def _needs_restore(server: ServerSummary) -> bool:
    return server.in_settings is False and server.registry_status == "pending"


def _settings_issue(server: ServerSummary) -> str:
    if server.in_settings is False:
        return (
            "설정 파일에 이 서버가 없음. 등록만 저장되고 설정 저장은 끝나지 않았음. "
            "서버 목록에서 설정 다시 저장을 누르세요."
            if _needs_restore(server)
            else "설정 파일에 이 서버가 없음."
        )
    # B's fixed issue codes only; an unknown one is never echoed (#86).
    issue = server.settings_issue
    if issue is None:
        return "확인된 문제 없음 · Profile에서 고른 서버만 확인함"
    if issue.startswith("URL_"):
        return "저장된 URL을 쓸 수 없음. 이 서버는 연결하지 않음."
    if issue == "REG_SETTINGS_MISMATCH":
        return "설정 파일과 등록 내용이 다름. 이 서버는 연결하지 않고 도구를 숨김."
    return "설정 확인 필요"


def _detail_field(title: str, value: str) -> tuple[QWidget, QLabel]:
    field = ResponsiveRow(breakpoint=400)
    field.setObjectName("infoField")
    field.row.setSpacing(2)
    label = plain_label(title, name="secondary")
    label.setFixedWidth(148)
    field.add_widget(label)
    value_label = plain_label(value)
    value_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    value_label.setMinimumWidth(0)
    value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    field.add_widget(value_label, 1)
    return field, value_label


class ServerDetailDialog(QDialog):
    """Displays only fields in ``ServerSummary``; no raw Registry config."""

    def __init__(self, server: ServerSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server_id = server.server_id
        self.setWindowTitle(f"MCP 서버 상세 · {public_display_text(server.server_id)}")
        self.setModal(True)
        self.setMinimumSize(240, 200)
        size_detail_for_viewport(self, 700, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("detailBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)
        self._display_value = plain_label(
            public_display_text(server.display_name or server.server_id), name="pageTitle"
        )
        self._server_id_value = plain_label(public_display_text(server.server_id), name="secondary")
        for value in (self._display_value, self._server_id_value):
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            value.setMinimumWidth(0)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(value)
        self._display_value.setAccessibleName("표시 이름")
        self._server_id_value.setAccessibleName("서버 식별자 · server_id")
        self.max_label_badge = SecurityLabel(server.max_label, show_code=True)
        layout.addWidget(self.max_label_badge)
        layout.addWidget(plain_label("상태", name="sectionTitle"))
        self.registry_status_field, self._registry_status_value = _detail_field(
            "Registry 등록 상태", fixed(REGISTRY_STATUSES, server.registry_status)
        )
        layout.addWidget(self.registry_status_field)
        self.session_status_field, self._session_status_value = _detail_field(
            "세션 상태", fixed(SESSION_STATUSES, server.session_status)
        )
        layout.addWidget(self.session_status_field)
        issue_field, self._issue_value = _detail_field("설정 확인", _settings_issue(server))
        layout.addWidget(issue_field)
        layout.addWidget(plain_label("연결 설정", name="sectionTitle"))
        transport_field, self._transport_value = _detail_field(
            "연결 방식 · transport", fixed(TRANSPORTS, server.transport)
        )
        layout.addWidget(transport_field)
        self.tier_field, self._tier_value = _detail_field(
            "신뢰 경계 · tier", fixed(TIERS, server.tier)
        )
        layout.addWidget(self.tier_field)
        pin_field, self._pin_value = _detail_field("실행 파일 지문", _pin_status(server))
        layout.addWidget(pin_field)
        layout.addWidget(plain_label("최근 검사", name="sectionTitle"))
        self.scan_report = ScanReportPanel()
        self.scan_report.setMinimumWidth(0)
        self.scan_report.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.scan_report)
        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        close_button = KeyboardActionButton("닫기")
        close_button.clicked.connect(self.close)
        outer.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

    def set_server(self, server: ServerSummary) -> None:
        """Refresh reused dialog fields before requesting a new scan status."""

        self.server_id = server.server_id
        self.setWindowTitle(f"MCP 서버 상세 · {public_display_text(server.server_id)}")
        self.max_label_badge.set_label(server.max_label)
        self._registry_status_value.setText(fixed(REGISTRY_STATUSES, server.registry_status))
        self._session_status_value.setText(fixed(SESSION_STATUSES, server.session_status))
        self._tier_value.setText(fixed(TIERS, server.tier))
        self._display_value.setText(public_display_text(server.display_name or server.server_id))
        self._server_id_value.setText(public_display_text(server.server_id))
        self._transport_value.setText(fixed(TRANSPORTS, server.transport))
        self._pin_value.setText(_pin_status(server))
        self._issue_value.setText(_settings_issue(server))
        self.scan_report.set_state(None)


class ServerListPage(QWidget):
    """Filters only rows in the supplied Registry query result."""

    # The ServerSummary of a registration whose settings record is to be written back.
    restore_requested = Signal(object)
    _FULL_TABLE_WIDTH = 970
    _TINY_TABLE_WIDTH = 440
    _STACK_FILTER_WIDTH = 480

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._servers: tuple[ServerSummary, ...] = ()
        self._activation_bridge: BackendBridge | None = None
        self._activation_command = ""
        self._activation_action = "activate"
        self._activation_dialog: ToolAllowDialog | None = None
        self._scan_dialog: ScanReviewDialog | None = None
        self._tools_dialog: ToolSettingsDialog | None = None
        self._visible_servers: list[ServerSummary] = []
        self._state = LoadState.UNAVAILABLE
        self._detail_dialog: ServerDetailDialog | None = None
        self._cached_detail_dialog: ServerDetailDialog | None = None
        self._detail_return_focus: QWidget | None = None
        self._detail_scan_command = ""
        self._target_id: str | None = None
        self._compact_columns: tuple[bool, bool] | None = None
        self._stacked_filters: bool | None = None
        self._layout = QVBoxLayout(self)
        layout = self._layout
        layout.setContentsMargins(24, 24, 24, 40)
        layout.setSpacing(16)
        layout.addWidget(plain_label("MCP 서버", name="pageTitle"))
        self.state_message = StateMessage()
        layout.addWidget(self.state_message)

        filters = QVBoxLayout()
        layout.addLayout(filters)
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("서버 ID 또는 표시 이름 검색")
        self.search.setAccessibleName("현재 조회 결과에서 서버 검색")
        self.search.textChanged.connect(self._apply_filters)
        filters.addWidget(self.search)
        self._choices = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        filters.addLayout(self._choices)
        self._choices.setSpacing(8)
        self.transport_filter = SelectionBox()
        self.transport_filter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.transport_filter.addItem("전체 transport", None)
        self.transport_filter.addItem("stdio", "stdio")
        self.transport_filter.addItem("Streamable HTTP", "streamable_http")
        self.transport_filter.setAccessibleName("전송 방식 필터")
        self.transport_filter.currentIndexChanged.connect(self._apply_filters)
        self._choices.addWidget(self.transport_filter)
        self.status_filter = SelectionBox()
        self.status_filter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.status_filter.addItem("전체 등록 상태", None)
        for status in ("active", "pending", "blocked"):
            self.status_filter.addItem(REGISTRY_NAMES[status], status)
        self.status_filter.setAccessibleName("등록 상태 필터")
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        self._choices.addWidget(self.status_filter)
        layout.addWidget(
            plain_label("필터는 현재 전달된 Registry 조회 결과에만 적용됨.", name="secondary")
        )
        self.table_hint = plain_label(
            "좁은 화면에서는 연결 방식·신뢰 경계·세션을 서버 상세에서 확인하세요.",
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
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 250)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemActivated.connect(self._open_selected)
        layout.addWidget(self.table, 1)
        self.detail_button = KeyboardActionButton("선택한 서버 상세 보기")
        self.detail_button.set_visual(icon="eye", variant="soft")
        self.detail_button.clicked.connect(self._open_selected)
        self.detail_button.setEnabled(False)
        layout.addWidget(self.detail_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.scan_button = KeyboardActionButton("선택한 서버 검사 결과·설명 검토")
        self.scan_button.clicked.connect(self._open_scan_review)
        layout.addWidget(self.scan_button)
        self.activate_button = KeyboardActionButton("선택한 서버 활성화")
        self.activate_button.clicked.connect(lambda: self._preview_activation("activate"))
        layout.addWidget(self.activate_button)
        self.block_button = KeyboardActionButton("선택한 서버 차단")
        self.block_button.clicked.connect(lambda: self._preview_activation("block"))
        layout.addWidget(self.block_button)
        self.activation_notice = plain_label("", name="runtimeMessage")
        self.tools_button = KeyboardActionButton("선택한 서버 도구 설정")
        self.tools_button.clicked.connect(self._preview_tools)
        layout.addWidget(self.tools_button)
        # A128·A105: a PENDING registration whose settings save did not finish.
        self.restore_button = KeyboardActionButton("선택한 서버 설정 다시 저장")
        self.restore_button.clicked.connect(self._restore_selected)
        layout.addWidget(self.restore_button)
        self.restore_hint = plain_label(
            "설정 파일에 없는 등록임. 같은 서버를 다시 입력하면 도구 목록이 등록과 같을 때만 "
            "설정 파일에 저장함. 등록 상태는 바뀌지 않음.",
            name="secondary",
        )
        layout.addWidget(self.restore_hint)
        layout.addWidget(self.activation_notice)
        self._selection_changed()
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
                QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
            )
            margin = 12 if stacked else 24
            self._layout.setContentsMargins(
                margin, 16 if stacked else 24, margin, 24 if stacked else 40
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
        # Wide tables end flush with the card: the last column takes the slack.
        header.setStretchLastSection(not compact)
        if compact:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 250)
        self.table_hint.setText(
            "좁은 화면에서는 최대 수신 등급·연결 방식·신뢰 경계·세션을 서버 상세에서 확인하세요."
            if tiny
            else "좁은 화면에서는 연결 방식·신뢰 경계·세션을 서버 상세에서 확인하세요."
        )
        self.table_hint.setVisible(compact)

    def set_servers(self, surface: Surface[tuple[ServerSummary, ...]]) -> None:
        """The bridge supplies the Registry result; overview rows are not reused."""

        if self._detail_dialog is not None:
            self._detail_dialog.close()
            self._detail_dialog = None
            self._detail_scan_command = ""
        self._state = surface.state
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
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

    def _apply_filters(self, *_args: object) -> None:
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
            label = (
                f"{public_display_text(server.server_id)}\n"
                f"{public_display_text(server.display_name or server.server_id)}"
            )
            columns = (
                (label, ""),
                (
                    fixed(TRANSPORTS, server.transport)
                    + (" · " + _pin_status(server) if server.executable_pinned is not None else ""),
                    "",
                ),
                (fixed(TIER_NAMES, server.tier), fixed_code(TIERS, server.tier)),
                (self._label_text(server.max_label), fixed_code(LABELS, server.max_label)),
                (
                    fixed(REGISTRY_NAMES, server.registry_status)
                    + (
                        " · 설정 없음"
                        if server.in_settings is False
                        else " · 설정 확인 필요"
                        if server.settings_issue is not None
                        else ""
                    ),
                    fixed_code(REGISTRY_STATUSES, server.registry_status).upper(),
                ),
                (fixed(SESSION_STATUSES, server.session_status), ""),
            )
            for column, (value, code) in enumerate(columns):
                item = QTableWidgetItem(value)
                item.setToolTip(code)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, table_row_height(self.table, 2))
            if server.server_id == previous_id:
                self.table.selectRow(row)
        self._selection_changed()
        if self._target_id:
            self._select_visible(self._target_id)
        if self._state == LoadState.READY:
            self.state_message.setVisible(not self._visible_servers)
            if self._servers and not self._visible_servers:
                self.state_message.set_message("현재 조회 결과와 일치하는 서버가 없음")
            elif not self._servers:
                self.state_message.set_message("등록된 MCP 서버가 없음")

    @staticmethod
    def _label_text(label: str | None) -> str:
        return LABELS[label][0] if label in LABELS else "미지정"

    def _selected_id(self) -> str | None:
        selected_rows = self.table.selectionModel().selectedRows()
        if len(selected_rows) != 1:
            return None
        row = selected_rows[0].row()
        return (
            self._visible_servers[row].server_id if 0 <= row < len(self._visible_servers) else None
        )

    def _selection_changed(self) -> None:
        self.detail_button.setEnabled(self._selected_id() is not None)
        selected = next((s for s in self._servers if s.server_id == self._selected_id()), None)
        ready = self._activation_bridge is not None and not self._activation_command
        # Activation and tool settings read the server's settings record.
        configured = selected is not None and selected.in_settings is not False
        self.activate_button.setEnabled(
            ready and configured and selected is not None and selected.registry_status == "pending"
        )
        self.scan_button.setEnabled(self.activate_button.isEnabled())
        self.block_button.setEnabled(
            ready and selected is not None and selected.registry_status in ("pending", "active")
        )
        self.tools_button.setEnabled(
            ready
            and configured
            and selected is not None
            and selected.registry_status in ("pending", "active")
        )
        restorable = ready and selected is not None and _needs_restore(selected)
        self.restore_button.setEnabled(restorable)
        self.restore_button.setVisible(restorable)
        self.restore_hint.setVisible(restorable)

    def _open_scan_review(self) -> None:
        server_id = self._selected_id()
        if server_id is not None and self._activation_bridge is not None:
            if self._scan_dialog is not None:
                self._scan_dialog.close()
            self._scan_dialog = ScanReviewDialog(self._activation_bridge, server_id, self)
            self._scan_dialog.open()

    def _restore_selected(self) -> None:
        selected = next((s for s in self._servers if s.server_id == self._selected_id()), None)
        if selected is not None and _needs_restore(selected):
            self.restore_requested.emit(selected)

    def attach_activation(self, bridge: BackendBridge) -> None:
        self._activation_bridge = bridge
        bridge.registration_changed.connect(self._activation_result)
        bridge.command_changed.connect(self._activation_command_result)
        bridge.registration_changed.connect(self._detail_registration)
        bridge.command_changed.connect(self._detail_command)
        bridge.scan_changed.connect(self._detail_scan)
        self._selection_changed()

    def _send_activation(self, request: RegistrationRequest) -> None:
        if self._activation_bridge is None:
            return
        self._activation_command = new_command_id()
        self.activation_notice.setText("서버 설정 처리 중임.")
        self._selection_changed()
        self._activation_bridge.request_registration(request, command_id=self._activation_command)

    def _preview_activation(self, action: str) -> None:
        server_id = self._selected_id()
        if server_id is None:
            return
        self._activation_action = action
        self._send_activation(RegistrationRequest("activation_preview", server_id))

    def _preview_tools(self) -> None:
        server_id = self._selected_id()
        if server_id is not None:
            self._send_activation(RegistrationRequest("tool_settings_preview", server_id))

    def _activation_command_result(self, event: CommandEvent) -> None:
        if event.command_id == self._activation_command and event.stage in (
            CommandStage.REJECTED,
            CommandStage.UNCONFIRMED,
        ):
            self._activation_result(RegistrationView("REG_ACTIVATION_UNKNOWN", event.command_id))

    def _activation_result(self, view: RegistrationView) -> None:
        if not self._activation_command or view.command_id != self._activation_command:
            return
        self._activation_command = ""
        self._selection_changed()
        if view.code == "REG_TOOLS_PREVIEW":
            tools_dialog = ToolSettingsDialog(view, self)
            self._tools_dialog = tools_dialog
            tools_dialog.accepted.connect(
                lambda: self._send_activation(
                    RegistrationRequest(
                        "tool_settings_save",
                        view.server_id,
                        expected_row_revision=view.row_revision,
                        tool_settings=tools_dialog.selection(),
                    )
                )
            )
            tools_dialog.open()
            self.activation_notice.setText("도구 설정을 확인하세요.")
            return
        if view.code == "REG_ACTIVATION_PREVIEW" and view.activation is not None:
            action = self._activation_action
            server = view.activation.servers[0]
            dialog = ToolAllowDialog(server, view.activation, self)
            self._activation_dialog = dialog
            dialog.setWindowTitle("서버 활성화 확인" if action == "activate" else "서버 차단 확인")
            reviewed_scan_id = (
                attach_activation_scan(dialog, dialog.warning, view.scan)
                if action == "activate"
                else None
            )
            dialog.warning.setText(
                activation_scan_text(view.scan) + "\n"
                "종류 미정 도구는 허용할 수 없음. 종류·출처·목적지 설정이 필요함.\n"
                "저장하면 실행 중인 Task가 모두 정지됨.\n"
                "다시 시작하면 연결됨. 저장 후 앱을 다시 시작하세요."
                if action == "activate"
                else (
                    "서버의 모든 도구를 차단함. 실행 중인 Task가 정지됨. "
                    "저장 후 앱을 다시 시작하세요."
                )
            )
            if action == "block":
                for box in dialog.boxes.values():
                    box.setChecked(False)
                    box.setEnabled(False)
            dialog.save_button.setText("활성화 확인" if action == "activate" else "차단 확인")
            dialog.accepted.connect(
                lambda: self._send_activation(
                    RegistrationRequest(
                        action,
                        server.server_id,
                        expected_row_revision=view.row_revision,
                        allowed=dialog.selection()[0],
                        denied=dialog.selection()[1],
                        reviewed_scan_id=reviewed_scan_id,
                    )
                )
            )
            dialog.open()
            self.activation_notice.setText("서버 설정을 확인하세요.")
            return
        messages = {
            **CHANGE_RESULTS,
            "REG_TOOLS_RESTART_REQUIRED": (
                "도구 설정 저장 완료 · Task 정지됨. 앱을 다시 시작한 뒤 서버 목록에서 활성화하세요."
            ),
            "REG_TOOLS_STALE": "서버 상태가 바뀌어 저장하지 않음. 목록을 다시 확인하세요.",
            "REG_TOOLS_INVALID": "도구 설정이 올바르지 않아 저장하지 않음. 입력을 다시 확인하세요.",
            # Unlike a policy-screen save, a failed activation write closes Guard.
            "POLICY_SAVE_FAILED": "설정을 저장하지 못해 Guard를 정지함. 앱을 다시 시작하세요.",
            "REG_ACTIVATION_RESTART_REQUIRED": (
                "서버 설정 저장 완료 · 다시 시작하면 연결됨. 앱을 다시 시작하세요."
            ),
            "REG_ACTIVATION_STALE": "서버 상태가 바뀌어 저장하지 않음. 목록을 다시 확인하세요.",
            "REG_SERVER_UNRECORDED": (
                "도구 대조 기록을 확인하지 못해 활성화하지 않음. 앱을 다시 시작하세요."
            ),
        }
        self.activation_notice.setText(
            messages.get(
                view.code,
                "서버 설정 결과를 확인하지 못함. 다시 저장하지 말고 앱을 다시 시작하세요.",
            )
        )

    def select_server(self, server_id: str) -> None:
        self._target_id = server_id
        self._select_visible(server_id)

    def _detail_registration(self, view: RegistrationView) -> None:
        dialog = self._detail_dialog
        if (
            dialog is None
            or not self._detail_scan_command
            or view.command_id != self._detail_scan_command
        ):
            return
        # The bridge's missing registration binding returns no server id.
        unavailable = view.code == "REG_UNAVAILABLE" and not view.server_id
        if view.server_id != dialog.server_id and not unavailable:
            return
        self._detail_scan_command = ""
        if view.code == "SCAN_STATUS" and view.scan is not None:
            dialog.scan_report.set_state(view.scan)
        elif view.code in ("SCAN_UNAVAILABLE", "REG_UNAVAILABLE") or view.scan is None:
            dialog.scan_report.set_state(None)
        else:
            dialog.scan_report.set_failed("검사 결과를 확인하지 못함.")

    def _detail_command(self, event: CommandEvent) -> None:
        if (
            self._detail_dialog is not None
            and self._detail_scan_command
            and event.command_id == self._detail_scan_command
            and event.stage in (CommandStage.REJECTED, CommandStage.UNCONFIRMED)
        ):
            self._detail_scan_command = ""
            self._detail_dialog.scan_report.set_failed("검사 결과를 확인하지 못함.")

    def _detail_scan(self, state: ScanState) -> None:
        dialog = self._detail_dialog
        if dialog is not None and state.server_id == dialog.server_id:
            dialog.scan_report.set_state(state)

    def _select_visible(self, server_id: str) -> None:
        for row, server in enumerate(self._visible_servers):
            if server.server_id == server_id:
                self.table.selectRow(row)
                item = self.table.item(row, 0)
                if item is not None:
                    self.table.scrollToItem(item)
                self._target_id = None
                return

    def _open_selected(self, *_args: object) -> None:
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
                focused if focused in (self.table, self.detail_button) else self.detail_button
            )
        dialog = self._cached_detail_dialog
        if dialog is None:
            dialog = ServerDetailDialog(self._visible_servers[row], self)
            dialog.finished.connect(
                lambda result, opened=dialog: self._detail_finished(opened, result)
            )
            self._cached_detail_dialog = dialog
        dialog.set_server(self._visible_servers[row])
        self._detail_dialog = dialog
        size_detail_for_viewport(dialog, 700, 560)
        dialog.open()
        if self._activation_bridge is None:
            dialog.scan_report.set_state(None)
        else:
            self._detail_scan_command = new_command_id()
            dialog.scan_report.set_loading()
            self._activation_bridge.request_registration(
                RegistrationRequest("scan_status", dialog.server_id),
                command_id=self._detail_scan_command,
            )

    def _detail_finished(self, dialog: ServerDetailDialog, _result: int) -> None:
        if self._detail_dialog is not dialog:
            return
        self._detail_dialog = None
        self._detail_scan_command = ""
        QTimer.singleShot(
            0, lambda: self._restore_detail_focus() if shiboken6.isValid(self) else None
        )

    def _restore_detail_focus(self) -> None:
        if self._detail_dialog is not None or not self.isVisible():
            return
        target = self._detail_return_focus
        self._detail_return_focus = None
        if target is not None and target.isEnabled() and target.isVisibleTo(self):
            target.setFocus()
        elif self.search.isEnabled():
            self.search.setFocus()
