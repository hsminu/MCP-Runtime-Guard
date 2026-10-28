"""Policy screen: read-only Profile and access rules, plus the activation allow list.

PR #26 (2026-10-18): when a server is activated, each tool gets an allow
checkbox; tools whose kind (READ/SEND) is fixed start checked. Saving is B's
policy command (B3.10) and changes `policy_version`, which stops running Tasks,
so the dialog says so. Without that command the dialog cannot save.
All values are identifiers shown as plain text.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.services.bridge import PolicyServer, PolicyView

from .untrusted_text import untrusted_display_text
from .widgets import KeyboardActionButton, plain_label

NO_PROFILE = "Guard 정책(Profile) 설정이 아직 연결되지 않음. 규칙을 표시할 수 없음."
SAVE_UNAVAILABLE = "규칙 저장 기능(정책 변경 명령)이 아직 연결되지 않아 저장할 수 없음."
STOP_WARNING = "저장하면 정책 버전이 바뀌어 실행 중인 Task가 모두 정지됨."
CHANGE_PENDING = "규칙을 저장하고 적용하는 중임."
# Fixed text per backend result code; never the backend's own wording.
CHANGE_RESULTS = {
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


def _page_table(name: str, columns: list[str]) -> QTableWidget:
    """A table on the scrolling page: full width, every row shown, nothing cut off.

    Leading columns fit their text and the last one takes the slack, wrapping
    long text; rows grow to fit and the table grows with them instead of
    scrolling inside the page.
    """
    table = QTableWidget(0, len(columns))
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
        self.setWindowTitle(f"도구 허용 설정 · {server.server_id}")
        self.server_id = server.server_id
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(f"MCP 서버 {server.server_id}의 도구", name="cardTitle"))
        allowed = {tool for srv, tool in view.allow if srv == server.server_id}
        denied = {tool for srv, tool in view.deny if srv == server.server_id}
        self.boxes: dict[str, QCheckBox] = {}
        self._supported = {tool.name for tool in server.tools if tool.kind_fixed}
        for tool in server.tools:
            box = QCheckBox(f"{tool.name} · {tool.kind}")
            if tool.name in allowed or tool.name in denied:
                box.setChecked(tool.name in allowed and tool.name not in denied)
            else:
                box.setChecked(tool.kind_fixed)
            # OTHER tools are never executable through Guard; shown, not chosen.
            box.setEnabled(view.change_available and tool.kind_fixed)
            self.boxes[tool.name] = box
            layout.addWidget(box)
        self.warning = plain_label(
            STOP_WARNING if view.change_available else SAVE_UNAVAILABLE, name="runtimeMessage"
        )
        layout.addWidget(self.warning)
        buttons = QHBoxLayout()
        self.save_button = KeyboardActionButton("저장")
        self.save_button.setEnabled(view.change_available)
        self.save_button.clicked.connect(self.accept)
        close = KeyboardActionButton("닫기")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.save_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def selection(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """READ/SEND tools only: B3.10 takes exactly one choice per supported tool."""
        boxes = {name: box for name, box in self.boxes.items() if name in self._supported}
        allowed = tuple(name for name, box in boxes.items() if box.isChecked())
        denied = tuple(name for name, box in boxes.items() if not box.isChecked())
        return allowed, denied


class PolicyPage(QWidget):
    allow_change_requested = Signal(str, tuple, tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: PolicyView | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 40)
        layout.setSpacing(16)
        layout.addWidget(plain_label("정책 · 경계", name="pageTitle"))
        self.summary = plain_label(NO_PROFILE, name="secondary")
        layout.addWidget(self.summary)
        self.notice = plain_label("", name="secondary")
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        self._change_command: str | None = None
        self.rules = _page_table("접근 규칙 목록", ["MCP 서버", "도구", "규칙"])
        layout.addWidget(self.rules)
        self.server_area = QVBoxLayout()
        layout.addLayout(self.server_area)
        self.excluded_title = plain_label(EXCLUDED_TITLE, name="sectionTitle")
        self.excluded_note = plain_label(EXCLUDED_NOTE, name="secondary")
        self.excluded = _page_table("Codex에서 쓸 수 없는 도구 목록", ["도구", "이유"])
        for widget in (self.excluded_title, self.excluded_note, self.excluded):
            widget.setVisible(False)
            layout.addWidget(widget)
        layout.addStretch(1)
        self.server_buttons: dict[str, KeyboardActionButton] = {}
        self.set_view(None)

    @property
    def view(self) -> PolicyView | None:
        return self._view

    def set_view(self, view: object) -> None:
        self._view = view if type(view) is PolicyView else None
        self.rules.setRowCount(0)
        self.excluded.setRowCount(0)
        for widget in (self.excluded_title, self.excluded_note, self.excluded):
            widget.setVisible(False)
        for button in self.server_buttons.values():
            button.setParent(None)
        self.server_buttons.clear()
        current = self._view
        if current is None:
            self.summary.setText(NO_PROFILE)
            return
        self.summary.setText(
            f"Profile {current.profile_id} · 정책 버전 {current.policy_version[:12]} · "
            f"승인 유효 시간 {current.approval_ttl_seconds}초 · 등급 누적 {current.taint_mode}"
        )
        rows = [(s, t, "허용") for s, t in current.allow] + [
            (s, t, "거부") for s, t in current.deny
        ]
        for index, (server_id, tool, verdict) in enumerate(rows):
            self.rules.insertRow(index)
            for column, value in enumerate((server_id, tool, verdict)):
                self.rules.setItem(index, column, QTableWidgetItem(value))
        for index, (tool_ref, reason) in enumerate(current.codex_excluded):
            # The name broke Codex's rules, so it may hold controls or lookalikes.
            shown, _ = untrusted_display_text(tool_ref, max_chars=200)
            self.excluded.insertRow(index)
            self.excluded.setItem(index, 0, QTableWidgetItem(shown))
            self.excluded.setItem(
                index, 1, QTableWidgetItem(EXCLUDED_REASONS.get(reason, EXCLUDED_UNKNOWN))
            )
        has_excluded = bool(current.codex_excluded)
        for widget in (self.excluded_title, self.excluded_note, self.excluded):
            widget.setVisible(has_excluded)
        for server in current.servers:
            button = KeyboardActionButton(f"{server.server_id} 도구 허용 설정")
            button.clicked.connect(lambda _=False, s=server: self.open_allow_dialog(s))
            self.server_area.addWidget(button)
            self.server_buttons[server.server_id] = button

    def set_change_pending(self, command_id: str) -> None:
        self._change_command = command_id
        self.notice.setText(CHANGE_PENDING)
        self.notice.setVisible(True)

    def settle_change(self, command_id: str, code: str) -> None:
        """Show the result of this page's own change command only."""
        if command_id != self._change_command:
            return
        self._change_command = None
        self.notice.setText(CHANGE_RESULTS.get(code, CHANGE_UNCONFIRMED))
        self.notice.setVisible(True)

    def open_allow_dialog(self, server: PolicyServer) -> ToolAllowDialog | None:
        view = self._view
        if view is None:
            return None
        dialog = ToolAllowDialog(server, view, self)
        dialog.accepted.connect(
            lambda: self.allow_change_requested.emit(server.server_id, *dialog.selection())
        )
        dialog.open()
        return dialog
