"""Policy screen: read-only Profile and access rules, plus the activation allow list.

PR #26 (2026-10-18): when a server is activated, each tool gets an allow
checkbox; tools whose kind (READ/SEND) is fixed start checked. Saving is B's
policy command (B3.10) and changes `policy_version`, which stops running Tasks,
so the dialog says so. Without that command the dialog cannot save.
All values are identifiers shown as plain text.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QAction, QPainter, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.services.bridge import DestinationEdit, PolicyDestination, PolicyServer, PolicyView

from .destinations import DestinationDialog
from .presentation import TIER_NAMES
from .untrusted_text import untrusted_display_text
from .widgets import (
    FitStack,
    KeyboardActionButton,
    LabelPill,
    ResponsiveRow,
    SelectionBox,
    StatusPill,
    plain_label,
)

NO_PROFILE = "Guard 정책(Profile) 설정이 아직 연결되지 않음. 규칙을 표시할 수 없음."
SAVE_UNAVAILABLE = "규칙 저장 기능(정책 변경 명령)이 아직 연결되지 않아 저장할 수 없음."
STOP_WARNING = "저장하면 정책 버전이 바뀌어 실행 중인 Task가 모두 정지됨."
CHANGE_PENDING = "규칙을 저장하고 적용하는 중임."
# Fixed text per backend result code; never the backend's own wording.
CHANGE_RESULTS = {
    "POLICY_DESTINATION_INVALID": (
        "목적지 ID·경계 종류·수신 등급 또는 사용 설정이 올바르지 않아 저장하지 않음."
    ),
    "POLICY_READ": "목적지 목록을 불러옴.",
    "POLICY_CHANGED": "규칙을 저장하고 적용함. 이전 정책으로 실행 중이던 Task는 정지됨.",
    "POLICY_SAVE_FAILED": "설정을 저장하지 못해 적용하지 않음.",
    "POLICY_APPLY_FAILED": (
        "설정은 저장했지만 적용을 확정하지 못해 Guard를 정지함. 앱을 다시 시작하세요."
    ),
    "POLICY_SAVE_UNKNOWN": "설정 저장 결과를 확인하지 못해 Guard를 정지함. 앱을 다시 시작하세요.",
    "POLICY_SAVE_FILE_CHANGED": (
        "설정 파일이 앱 밖에서 바뀌었거나 읽을 수 없어 저장하지 않음. "
        "앱을 다시 시작해 파일을 다시 읽으세요."
    ),
    "POLICY_CHANGE_INVALID": "선택한 도구 목록이 현재 설정과 맞지 않아 저장하지 않음.",
    "POLICY_CHANGE_UNAVAILABLE": SAVE_UNAVAILABLE,
}
CHANGE_UNCONFIRMED = "규칙 변경 결과를 확인하지 못함. 정책 화면을 다시 확인하세요."
EXCLUDED_TITLE = "Codex에서 쓸 수 없는 도구"
EXCLUDED_NOTE = (
    "Codex는 서버 ID와 도구 이름에 영문·숫자·_·-만 받음. "
    "아래 도구는 모델에게 보이지 않고 실행할 수도 없음."
)
# Fixed text per reason code from the Codex tool plan (contracts 2026-10-18).
EXCLUDED_REASONS = {
    "NAMESPACE_NAME": (
        "서버 ID가 Codex 이름 규칙(영문·숫자·_·-, 64자 이하, mcp 접두 불가)에 맞지 않음"
    ),
    "NAMESPACE_RESERVED": "서버 ID가 Codex가 예약한 이름임",
    "TOOL_NAME": (
        "도구 이름이 Codex 이름 규칙(영문·숫자·_·-, 128자 이하, mcp 접두 불가)에 맞지 않음"
    ),
    "DUPLICATE": "같은 이름의 도구가 이미 있음",
}
EXCLUDED_UNKNOWN = "Codex 이름 규칙에 맞지 않음"


class _PolicyCellDelegate(QStyledItemDelegate):
    """Keep item text accessible, but let a cell widget paint it exactly once."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if self._table.cellWidget(index.row(), index.column()) is None:
            super().paint(painter, option, index)
            return
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        self._table.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, background, painter, self._table
        )

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        size = super().sizeHint(option, index)
        cell = self._table.cellWidget(index.row(), index.column())
        if cell is not None:
            cell.ensurePolished()
            content = cell.sizeHint().expandedTo(cell.minimumSizeHint())
            # Cell widgets sit inside the shared item's 10px/6px padding.
            # Reserve it on the 4px spacing grid, including the row separator.
            padded = content + QSize(24, 16)
            size = size.expandedTo(padded)
        return size


def _page_table(name: str, columns: list[str]) -> QTableWidget:
    """A table on the scrolling page: full width, every row shown, nothing cut off.

    Leading columns fit their text and the last one takes the slack, wrapping
    long text; rows grow to fit and the table grows with them instead of
    scrolling inside the page.
    """
    table = QTableWidget(0, len(columns))
    table.setItemDelegate(_PolicyCellDelegate(table))
    table.setAccessibleName(name)
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setWordWrap(True)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header = table.horizontalHeader()
    header.setMinimumSectionSize(120)
    for column in range(len(columns) - 1):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    return table


class ToolAllowDialog(QDialog):
    """Per-tool allow checkboxes for one server; READ/SEND tools start checked."""

    def __init__(
        self, server: PolicyServer, view: PolicyView, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"도구 허용 설정 · {_shown(server.server_id)}")
        self.server_id = server.server_id
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(f"MCP 서버 {_shown(server.server_id)}의 도구", name="cardTitle")
        )
        allowed = {tool for srv, tool in view.allow if srv == server.server_id}
        denied = {tool for srv, tool in view.deny if srv == server.server_id}
        self.boxes: dict[str, QCheckBox] = {}
        self._supported = {
            tool.name
            for tool in server.tools
            if tool.kind_fixed and tool.kind.upper() in ("READ", "SEND")
        }
        for tool in server.tools:
            supported = tool.name in self._supported
            kind = tool.kind.upper()
            title = f"{KINDS[kind]} · {kind}" if supported else "실행 불가"
            box = QCheckBox(f"{_shown(tool.name)} · {title}")
            if tool.name in allowed or tool.name in denied:
                box.setChecked(supported and tool.name in allowed and tool.name not in denied)
            else:
                box.setChecked(supported)
            # OTHER tools are never executable through Guard; shown, not chosen.
            box.setEnabled(view.change_available and supported)
            self.boxes[tool.name] = box
            layout.addWidget(box)
        self.warning = plain_label(
            STOP_WARNING if view.change_available else SAVE_UNAVAILABLE, name="runtimeMessage"
        )
        layout.addWidget(self.warning)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.save_button = KeyboardActionButton("저장")
        self.save_button.setEnabled(view.change_available)
        self.save_button.clicked.connect(self.accept)
        close = KeyboardActionButton("닫기")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.save_button)
        buttons.addWidget(close)

    def selection(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """READ/SEND tools only: B3.10 takes exactly one choice per supported tool."""
        boxes = {name: box for name, box in self.boxes.items() if name in self._supported}
        allowed = tuple(name for name, box in boxes.items() if box.isChecked())
        denied = tuple(name for name, box in boxes.items() if not box.isChecked())
        return allowed, denied


KINDS = {"READ": "읽기", "SEND": "전송", "OTHER": "기타"}
ACCESS_NOTE = "허용된 도구도 보안 등급과 목적지 정책에 따라 승인 요청이나 차단이 될 수 있음."
DESTINATION_MISSING = "정책이 연결되지 않아 목적지를 표시할 수 없음."
DESTINATION_FAILED = "목적지 목록을 불러오지 못함. 새로 고침을 다시 누르세요."
DESTINATION_EMPTY = "등록된 목적지가 없음. SEND 도구가 보낼 수신처를 ‘목적지 추가’로 등록하세요."
DESTINATION_UNAVAILABLE = "목적지 변경 기능이 연결되지 않아 추가할 수 없음."
FILTER_NOTE = "이 화면이 받은 정책 전체에 필터를 적용함. 정책은 한 번에 모두 불러옴."


def _shown(value: str) -> str:
    return untrusted_display_text(value)[0]


def _duration(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds}초"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return " ".join(
        f"{value}{unit}"
        for value, unit in ((hours, "시간"), (minutes, "분"), (seconds, "초"))
        if value
    )


def _pill(text: str, tone: str) -> StatusPill:
    pill = StatusPill()
    pill.set_status(text, tone)
    return pill


def _cell(*widgets: QWidget, fill_text: bool = False) -> QWidget:
    cell = QWidget()
    row = QHBoxLayout(cell)
    row.setContentsMargins(12, 8, 12, 8)
    row.setSpacing(8)
    for widget in widgets:
        stretch = 1 if fill_text and widget is widgets[-1] else 0
        row.addWidget(widget, stretch, Qt.AlignmentFlag.AlignVCenter)
    if not fill_text:
        row.addStretch(1)
    return cell


def _changed() -> QLabel:
    return plain_label("변경됨", name="policyChanged", wrap=False)


@dataclass(frozen=True)
class RuleRow:
    """Display metadata only; never used to authorize a tool call (I2/I6)."""

    server_id: str
    tool: str
    kind: str
    status: str
    overlap: bool


def _rule_rows(view: PolicyView | None) -> tuple[RuleRow, ...]:
    if view is None:
        return ()
    tools = {(s.server_id, t.name): t.kind.upper() for s in view.servers for t in s.tools}
    allowed, denied = set(view.allow), set(view.deny)
    return tuple(
        RuleRow(
            s,
            t,
            tools.get((s, t), ""),
            "deny" if (s, t) in denied else "allow" if (s, t) in allowed else "missing",
            (s, t) in allowed and (s, t) in denied,
        )
        for s, t in sorted(tools.keys() | allowed | denied)
    )


class PolicySummary(QWidget):
    """Compact label/value pairs that reflow without individual cards."""

    def __init__(self) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(10)
        self.values: dict[str, QLabel] = {}
        self.pairs: list[QWidget] = []
        for key, title in (
            ("profile", "프로필"),
            ("version", "정책 버전"),
            ("ttl", "승인 유효 시간"),
            ("taint", "등급 누적"),
            ("rules", "접근 규칙"),
            ("destinations", "목적지"),
        ):
            pair = QWidget()
            row = QHBoxLayout(pair)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(plain_label(title, name="secondary", wrap=False))
            value = plain_label("—", name="mono" if key == "version" else "policyValue", wrap=False)
            row.addWidget(value)
            row.addStretch(1)
            self.values[key] = value
            self.pairs.append(pair)
        self._reflow()

    def _reflow(self) -> None:
        columns = 3 if self.width() >= 780 else 2 if self.width() >= 540 else 1
        for pair in self.pairs:
            self.grid.removeWidget(pair)
        for index, pair in enumerate(self.pairs):
            self.grid.addWidget(pair, index // columns, index % columns)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reflow()


class PolicyPage(QWidget):
    allow_change_requested = Signal(str, tuple, tuple)
    destination_change_requested = Signal(object)
    destination_read_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: PolicyView | None = None
        self._rows: tuple[RuleRow, ...] = ()
        self._change_command: str | None = None
        self._request_kind = ""
        self._request_id = ""
        self._baseline: PolicyView | None = None
        self._received_view = False
        self._awaiting_success_view = False
        self._read_failed = False
        self._changed_rules: set[tuple[str, str]] = set()
        self._changed_destinations: set[str] = set()
        self.server_buttons: dict[str, QAction] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 40)
        self.content = QWidget()
        self.content.setMaximumWidth(960)
        outer.addWidget(self.content)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(plain_label("정책 · 경계", name="pageTitle"))
        self.summary_fields = PolicySummary()
        layout.addWidget(self.summary_fields)
        self.summary = plain_label(NO_PROFILE, name="secondary")
        self.profile_state = _cell(_pill("정책 미연결", "neutral"), self.summary, fill_text=True)
        layout.addWidget(self.profile_state)
        layout.addWidget(plain_label(ACCESS_NOTE, name="secondary"))
        self.notice = plain_label("", name="secondary")
        self.result_pill = _pill("", "neutral")
        self.result_area = _cell(self.result_pill, self.notice, fill_text=True)
        layout.addWidget(self.result_area)
        self.result_area.hide()
        self.progress = plain_label("", name="secondary")
        self.progress_area = _cell(_pill("처리 중", "live"), self.progress, fill_text=True)
        layout.addWidget(self.progress_area)
        self.progress_area.hide()
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.addTab("도구 접근 규칙")
        self.tabs.addTab("전송 목적지")
        self.tabs.setExpanding(False)
        layout.addWidget(self.tabs)
        self.stack = FitStack()
        layout.addWidget(self.stack)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.rules_tab, self.destination_tab = QWidget(), QWidget()
        self.stack.addWidget(self.rules_tab)
        self.stack.addWidget(self.destination_tab)
        rules_layout = QVBoxLayout(self.rules_tab)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        rules_layout.setSpacing(12)
        filters = ResponsiveRow(700)
        filters.setToolTip(FILTER_NOTE)
        self.server_filter, self.status_filter = SelectionBox(), SelectionBox()
        self.server_filter.setAccessibleName("서버 필터")
        self.status_filter.setAccessibleName("규칙 상태 필터")
        self.server_filter.setToolTip(FILTER_NOTE)
        self.status_filter.setToolTip(FILTER_NOTE)
        for title, value in (
            ("전체 상태", ""),
            ("허용", "allow"),
            ("거부", "deny"),
            ("규칙 없음", "missing"),
        ):
            self.status_filter.addItem(title, value)
        self.filter_count = plain_label("", name="secondary", wrap=False)
        self.allow_button = KeyboardActionButton("도구 허용 설정")
        self.allow_button.set_visual(icon="edit")
        self.allow_button.clicked.connect(self._choose_server)
        self.server_menu = QMenu(self)
        for widget in (self.server_filter, self.status_filter, self.filter_count):
            filters.add_widget(widget)
        filters.row.addStretch(1)
        filters.add_widget(self.allow_button)
        rules_layout.addWidget(filters)
        self.rules = _page_table("접근 규칙 목록", ["도구", "서버", "종류", "규칙"])
        rules_layout.addWidget(self.rules)
        self.server_filter.currentIndexChanged.connect(self._filter_rules)
        self.status_filter.currentIndexChanged.connect(self._filter_rules)
        self.excluded_title = plain_label(EXCLUDED_TITLE, name="sectionTitle")
        self.excluded_note = plain_label(EXCLUDED_NOTE, name="secondary")
        self.excluded = _page_table("Codex에서 쓸 수 없는 도구 목록", ["도구", "이유"])
        for excluded_widget in (self.excluded_title, self.excluded_note, self.excluded):
            rules_layout.addWidget(excluded_widget)
        rules_layout.addStretch(1)
        destinations_layout = QVBoxLayout(self.destination_tab)
        destinations_layout.setContentsMargins(0, 0, 0, 0)
        destinations_layout.setSpacing(12)
        destinations_layout.addWidget(
            plain_label(
                "SEND 도구가 데이터를 보내는 최종 수신처임. MCP 서버의 신뢰 경계와는 따로 검사됨.",
                name="secondary",
            )
        )
        actions = ResponsiveRow()
        self.add_destination = KeyboardActionButton("목적지 추가")
        self.add_destination.set_visual(icon="plus", variant="primary")
        self.add_destination.clicked.connect(lambda: self.open_destination_dialog())
        self.read_destinations = KeyboardActionButton("새로 고침")
        self.read_destinations.clicked.connect(self._read_destinations)
        actions.add_widget(self.add_destination)
        actions.add_widget(self.read_destinations)
        actions.row.addStretch(1)
        destinations_layout.addWidget(actions)
        self.destination_state = plain_label("", name="secondary")
        destinations_layout.addWidget(self.destination_state)
        self.retry_destinations = KeyboardActionButton("새로 고침")
        self.retry_destinations.clicked.connect(self._read_destinations)
        destinations_layout.addWidget(self.retry_destinations, 0, Qt.AlignmentFlag.AlignLeft)
        self.destinations = _page_table(
            "목적지 목록", ["목적지 ID", "신뢰 경계", "최대 수신 등급", "사용 여부", "동작"]
        )
        destinations_layout.addWidget(self.destinations)
        destinations_layout.addStretch(1)
        layout.addStretch(1)
        outer.addStretch(1)
        self.set_view(None)

    @property
    def view(self) -> PolicyView | None:
        return self._view

    def set_view(self, view: object) -> None:
        previous = self._view
        self._view = view if type(view) is PolicyView else None
        self._changed_rules.clear()
        self._changed_destinations.clear()
        if self._awaiting_success_view:
            self._mark_changes(previous)
            self._awaiting_success_view = False
        if self._change_command is not None:
            self._received_view = True
        self._read_failed = False
        self._rows = _rule_rows(self._view)
        self._set_summary()
        selected = self.server_filter.currentData()
        self.server_filter.blockSignals(True)
        self.server_filter.clear()
        self.server_filter.addItem("전체 서버", None)
        servers = {r.server_id for r in self._rows}
        if self._view:
            servers.update(s.server_id for s in self._view.servers)
        for server_id in sorted(servers):
            self.server_filter.addItem(_shown(server_id), server_id)
        self.server_filter.setCurrentIndex(max(0, self.server_filter.findData(selected)))
        self.server_filter.blockSignals(False)
        self.server_menu.clear()
        self.server_buttons.clear()
        if self._view:
            for server in self._view.servers:
                action = self.server_menu.addAction(_shown(server.server_id))
                action.triggered.connect(lambda _=False, s=server: self.open_allow_dialog(s))
                self.server_buttons[server.server_id] = action
        self._filter_rules()
        self._render_destinations()
        self._render_excluded()
        self._update_actions()

    def _set_summary(self) -> None:
        current = self._view
        self.profile_state.setVisible(current is None)
        values = self.summary_fields.values
        for label in values.values():
            label.setText("—")
            label.setToolTip("")
        suffix = "" if current is None else f" {len(self._rows)}"
        self.tabs.setTabText(0, f"도구 접근 규칙{suffix}")
        self.tabs.setTabText(
            1, "전송 목적지" + (f" {len(current.destinations)}" if current else "")
        )
        if current is None:
            self.summary.setText(NO_PROFILE)
            return
        counts = {
            status: sum(row.status == status for row in self._rows)
            for status in ("allow", "deny", "missing")
        }
        rules = f"허용 {counts['allow']} · 거부 {counts['deny']}"
        if counts["missing"]:
            # Default denials stay separate from explicit deny rules.
            rules += f" · 규칙 없음 {counts['missing']}"
        texts = {
            "profile": _shown(current.profile_id),
            "version": current.policy_version[:12],
            "ttl": _duration(current.approval_ttl_seconds),
            "taint": "최고 등급 유지"
            if current.taint_mode == "high_watermark"
            else _shown(current.taint_mode),
            "rules": rules,
            "destinations": (
                f"{len(current.destinations)}개 · "
                f"사용 {sum(d.selected for d in current.destinations)}개"
            ),
        }
        for key, text in texts.items():
            values[key].setText(text)
        values["version"].setToolTip(current.policy_version)
        values["taint"].setToolTip(current.taint_mode)

    def _filter_rules(self) -> None:
        server, status = self.server_filter.currentData(), self.status_filter.currentData()
        rows = [
            r
            for r in self._rows
            if (server is None or r.server_id == server) and (not status or r.status == status)
        ]
        self.rules.setRowCount(0)
        for index, row in enumerate(rows):
            self.rules.insertRow(index)
            title, tone = {
                "allow": ("허용", "ok"),
                "deny": ("거부", "deny"),
                "missing": ("규칙 없음 · 거부", "neutral"),
            }[row.status]
            kind = KINDS.get(row.kind, "미등록")
            if row.kind == "OTHER":
                kind += " · 실행 불가 종류"
            for column, text in enumerate((_shown(row.tool), _shown(row.server_id), kind, title)):
                self.rules.setItem(index, column, QTableWidgetItem(text))
            self.rules.setCellWidget(
                index, 0, _cell(plain_label(_shown(row.tool), name="policyTool", wrap=False))
            )
            self.rules.setCellWidget(
                index, 1, _cell(plain_label(_shown(row.server_id), name="secondary", wrap=False))
            )
            if row.kind == "OTHER":
                kind_item = self.rules.item(index, 2)
                if kind_item is not None:
                    kind_item.setToolTip("OTHER 종류 도구는 Guard로 실행할 수 없음")
            pill = _pill(title, tone)
            if row.overlap:
                pill.setToolTip("거부 규칙이 허용보다 우선함")
            elif row.status == "missing":
                pill.setToolTip("규칙이 없으면 기본으로 거부됨")
            widgets: list[QWidget] = [pill]
            if (row.server_id, row.tool) in self._changed_rules:
                widgets.append(_changed())
            self.rules.setCellWidget(index, 3, _cell(*widgets))
        self.filter_count.setText(f"{len(rows)}개 표시 · 전체 {len(self._rows)}개")
        self.rules.resizeRowsToContents()
        self._update_actions()

    def _render_destinations(self) -> None:
        self.destinations.setRowCount(0)
        current = self._view
        if current:
            for index, destination in enumerate(current.destinations):
                self.destinations.insertRow(index)
                label = plain_label(
                    _shown(destination.destination_id), name="policyDestination", wrap=False
                )
                self.destinations.setCellWidget(index, 0, label)
                for column, text in enumerate(
                    (
                        TIER_NAMES.get(destination.tier, "알 수 없음"),
                        destination.max_label,
                        "사용" if destination.selected else "사용 안 함",
                    ),
                    1,
                ):
                    self.destinations.setItem(index, column, QTableWidgetItem(text))
                self.destinations.setCellWidget(index, 2, _cell(LabelPill(destination.max_label)))
                self.destinations.setCellWidget(
                    index,
                    3,
                    _cell(
                        _pill(
                            "사용" if destination.selected else "사용 안 함",
                            "ok" if destination.selected else "neutral",
                        )
                    ),
                )
                buttons: list[QWidget] = []
                for title, deleting in (("변경", False), ("삭제", True)):
                    button = KeyboardActionButton(title)
                    button.set_visual(
                        icon="trash" if deleting else "edit",
                        variant="danger" if deleting else "default",
                    )
                    button.setEnabled(current.change_available and self._change_command is None)
                    button.setToolTip("" if current.change_available else SAVE_UNAVAILABLE)
                    button.clicked.connect(
                        lambda _=False, row=destination, remove=deleting: (
                            self.open_destination_dialog(row, deleting=remove)
                        )
                    )
                    buttons.append(button)
                if destination.destination_id in self._changed_destinations:
                    buttons.append(_changed())
                self.destinations.setCellWidget(index, 4, _cell(*buttons))
        self.destinations.resizeRowsToContents()
        self._destination_state()

    def _destination_state(self) -> None:
        current = self._view
        message = ""
        if current is None:
            message = DESTINATION_MISSING
        elif self._read_failed:
            message = DESTINATION_FAILED
        elif not current.destinations:
            message = DESTINATION_EMPTY if current.change_available else DESTINATION_UNAVAILABLE
        self.destination_state.setText(message)
        self.destination_state.setVisible(bool(message))
        self.destinations.setVisible(not message)
        self.retry_destinations.setVisible(current is not None and self._read_failed)

    def _render_excluded(self) -> None:
        self.excluded.setRowCount(0)
        current = self._view
        for index, (tool_ref, reason) in enumerate(current.codex_excluded if current else ()):
            shown, _ = untrusted_display_text(tool_ref, max_chars=200)
            self.excluded.insertRow(index)
            self.excluded.setItem(index, 0, QTableWidgetItem(shown))
            self.excluded.setItem(
                index, 1, QTableWidgetItem(EXCLUDED_REASONS.get(reason, EXCLUDED_UNKNOWN))
            )
        for widget in (self.excluded_title, self.excluded_note, self.excluded):
            widget.setVisible(bool(current and current.codex_excluded))

    def _update_actions(self) -> None:
        available = bool(self._view and self._view.change_available)
        idle = self._change_command is None
        self.add_destination.setEnabled(available and idle)
        self.add_destination.setToolTip("" if available else SAVE_UNAVAILABLE)
        server = self.server_filter.currentData()
        self.allow_button.setEnabled(
            available
            and idle
            and bool(self.server_buttons)
            and (server is None or server in self.server_buttons)
        )
        self.allow_button.setToolTip("" if available else SAVE_UNAVAILABLE)
        self.read_destinations.setEnabled(self._view is not None and idle)
        self.retry_destinations.setEnabled(self._view is not None and idle)
        for action in self.server_buttons.values():
            action.setEnabled(available and idle)

    def _choose_server(self) -> None:
        selected = self.server_filter.currentData()
        if selected in self.server_buttons:
            self.server_buttons[selected].trigger()
        else:
            self.server_menu.popup(
                self.allow_button.mapToGlobal(self.allow_button.rect().bottomLeft())
            )

    def _begin_request(self, kind: str, identifier: str = "") -> None:
        self._request_kind, self._request_id = kind, identifier
        self._baseline = self._view
        self._received_view = False
        self._awaiting_success_view = False

    def _read_destinations(self) -> None:
        if self._view is None or self._change_command is not None:
            return
        self._begin_request("read")
        self.destination_read_requested.emit()

    def set_change_pending(self, command_id: str) -> None:
        self._change_command = command_id
        identifier = _shown(self._request_id)
        message = CHANGE_PENDING
        if self._request_kind == "allow":
            message = f"{identifier} 도구 허용 규칙 저장·적용 중"
        elif self._request_kind in ("add", "update", "delete"):
            message = f"목적지 {identifier} 저장·적용 중"
        elif self._request_kind == "read":
            message = "목적지 목록 불러오는 중"
        self.progress.setText(message)
        self.progress_area.show()
        self.notice.setText(message)  # Legacy fixed-text accessor; result area stays hidden.
        self.result_area.hide()
        self._update_actions()
        self._render_destinations()

    def settle_change(self, command_id: str, code: str) -> None:
        """Only a matching backend result can confirm a change (T29, I6)."""
        if command_id != self._change_command:
            return
        self._change_command = None
        self.progress_area.hide()
        self.notice.setText(CHANGE_RESULTS.get(code, CHANGE_UNCONFIRMED))
        self.result_area.setVisible(code != "POLICY_READ")
        if code == "POLICY_CHANGED":
            self.result_pill.set_status("저장·적용됨", "ok")
            # The bridge currently publishes the view BEFORE the result event.
            # Also accept result-before-view without optimistic highlighting.
            if self._received_view:
                self._mark_changes(self._baseline)
            else:
                self._awaiting_success_view = True
        else:
            self._awaiting_success_view = False
            self._changed_rules.clear()
            self._changed_destinations.clear()
            unknown = code not in CHANGE_RESULTS or code in (
                "POLICY_SAVE_UNKNOWN",
                "POLICY_APPLY_FAILED",
            )
            self.result_pill.set_status("결과 미확인" if unknown else "저장 안 됨", "deny")
            if self._request_kind == "read" and code != "POLICY_READ":
                self._read_failed = True
        self._request_kind = ""
        self._request_id = ""
        self._filter_rules()
        self._render_destinations()
        self._update_actions()

    def _mark_changes(self, previous: PolicyView | None) -> None:
        if previous is None or self._view is None:
            return
        old = {(r.server_id, r.tool): r for r in _rule_rows(previous)}
        self._changed_rules = {
            (r.server_id, r.tool)
            for r in _rule_rows(self._view)
            if old.get((r.server_id, r.tool)) != r
        }
        destinations = {d.destination_id: d for d in previous.destinations}
        self._changed_destinations = {
            d.destination_id
            for d in self._view.destinations
            if destinations.get(d.destination_id) != d
        }

    def open_destination_dialog(
        self, row: PolicyDestination | None = None, *, deleting: bool = False
    ) -> DestinationDialog | None:
        if (
            self._view is None
            or not self._view.change_available
            or self._change_command is not None
        ):
            return None
        dialog = DestinationDialog(row, STOP_WARNING, deleting=deleting, parent=self)
        dialog.accepted.connect(lambda: self._save_destination(dialog.selection()))
        dialog.open()
        return dialog

    def _save_destination(self, edit: DestinationEdit) -> None:
        if (
            self._view is None
            or not self._view.change_available
            or self._change_command is not None
        ):
            return
        self._begin_request(edit.action, edit.destination_id)
        self.destination_change_requested.emit(edit)

    def open_allow_dialog(self, server: PolicyServer) -> ToolAllowDialog | None:
        view = self._view
        if view is None or self._change_command is not None:
            return None
        dialog = ToolAllowDialog(server, view, self)
        dialog.accepted.connect(lambda: self._save_allow(server.server_id, dialog))
        dialog.open()
        return dialog

    def _save_allow(self, server_id: str, dialog: ToolAllowDialog) -> None:
        if (
            self._view is None
            or not self._view.change_available
            or self._change_command is not None
        ):
            return
        self._begin_request("allow", server_id)
        self.allow_change_requested.emit(server_id, *dialog.selection())
