"""Policy screen: read-only Profile and access rules, plus the activation allow list.

PR #26 (2026-10-18): when a server is activated, each tool gets an allow
checkbox; tools whose kind (READ/SEND) is fixed start checked. Saving is B's
policy command (B3.10) and changes `policy_version`, which stops running Tasks,
so the dialog says so. Without that command the dialog cannot save.
All values are identifiers shown as plain text.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.services.bridge import PolicyServer, PolicyView

from .widgets import KeyboardActionButton, plain_label

NO_PROFILE = "Guard 정책(Profile) 설정이 아직 연결되지 않았어. 규칙을 표시할 수 없어."
SAVE_UNAVAILABLE = "규칙 저장 기능(정책 변경 명령)이 아직 연결되지 않아 저장할 수 없어."
STOP_WARNING = "저장하면 정책 버전이 바뀌어 실행 중인 Task가 모두 정지돼."


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
        for tool in server.tools:
            box = QCheckBox(f"{tool.name} · {tool.kind}")
            if tool.name in allowed or tool.name in denied:
                box.setChecked(tool.name in allowed and tool.name not in denied)
            else:
                box.setChecked(tool.kind_fixed)
            box.setEnabled(view.change_available)
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
        allowed = tuple(name for name, box in self.boxes.items() if box.isChecked())
        denied = tuple(name for name, box in self.boxes.items() if not box.isChecked())
        return allowed, denied


class PolicyPage(QWidget):
    allow_change_requested = Signal(str, tuple, tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: PolicyView | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 28, 26, 42)
        layout.setSpacing(14)
        layout.addWidget(plain_label("POLICY", name="eyebrow"))
        layout.addWidget(plain_label("정책 · 경계", name="pageTitle"))
        self.summary = plain_label(NO_PROFILE, name="secondary")
        layout.addWidget(self.summary)
        self.rules = QTableWidget(0, 3)
        self.rules.setAccessibleName("접근 규칙 목록")
        self.rules.setHorizontalHeaderLabels(["MCP 서버", "도구", "규칙"])
        self.rules.verticalHeader().setVisible(False)
        self.rules.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.rules)
        self.server_area = QVBoxLayout()
        layout.addLayout(self.server_area)
        layout.addStretch(1)
        self.server_buttons: dict[str, KeyboardActionButton] = {}
        self.set_view(None)

    @property
    def view(self) -> PolicyView | None:
        return self._view

    def set_view(self, view: object) -> None:
        self._view = view if type(view) is PolicyView else None
        self.rules.setRowCount(0)
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
        for server in current.servers:
            button = KeyboardActionButton(f"{server.server_id} 도구 허용 설정")
            button.clicked.connect(lambda _=False, s=server: self.open_allow_dialog(s))
            self.server_area.addWidget(button)
            self.server_buttons[server.server_id] = button

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
