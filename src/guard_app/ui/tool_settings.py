"""Operator tool metadata editor. All authority stays behind the bridge."""

from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QDialog, QFormLayout, QScrollArea, QVBoxLayout, QWidget

from guard_app.services.bridge import RegistrationView, ToolSettingsEdit

from .untrusted_text import untrusted_display_text
from .widgets import KeyboardActionButton, SelectionBox, plain_label, size_detail_for_viewport


class ToolSettingsDialog(QDialog):
    def __init__(self, view: RegistrationView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("도구 설정")
        self._view = view
        layout = QVBoxLayout(self)
        self.warning = plain_label(
            "저장하면 실행 중인 Task가 모두 정지됨. 앱을 다시 시작하세요.\n"
            "다시 시작한 뒤 서버 목록에서 활성화하세요. ACTIVE 서버도 다시 활성화해야 함.\n"
            "보내기는 목적지 인자만 설정함. 등록된 목적지가 없으면 전송이 거부됨."
        )
        layout.addWidget(self.warning)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        self.controls: dict[str, tuple[SelectionBox, SelectionBox, SelectionBox]] = {}
        for row in view.tool_settings:
            form.addRow(plain_label(untrusted_display_text(row.name)[0]))
            form.addRow(plain_label("서버 제공 이름 · 검증되지 않은 내용", name="secondary"))
            kind, label, destination = SelectionBox(), SelectionBox(), SelectionBox()
            for title, value in (("사용 안 함", "other"), ("읽기", "read"), ("보내기", "send")):
                kind.addItem(title, value)
            kind.setCurrentIndex(kind.findData(row.kind))
            if not row.string_arguments:
                model = kind.model()
                if isinstance(model, QStandardItemModel):
                    model.item(2).setEnabled(False)
            label.addItem("등급을 선택하세요", None)
            for value in ("PUBLIC", "INTERNAL", "CONFIDENTIAL"):
                label.addItem(value, value)
            label.setCurrentIndex(max(0, label.findData(row.label)))
            destination.addItem("목적지 인자를 선택하세요", None)
            for name in row.string_arguments:
                destination.addItem(untrusted_display_text(name)[0], name)
            destination.setCurrentIndex(max(0, destination.findData(row.destination_arg)))
            form.addRow("종류", kind)
            form.addRow("읽기 결과 등급", label)
            form.addRow("목적지 인자 · 서버 제공 이름", destination)
            if row.readonly:
                form.addRow(plain_label("자원형 READ · 이 화면에서 변경할 수 없음"))
            elif not row.string_arguments:
                form.addRow(plain_label("최상위 문자열 인자가 없어 보내기를 선택할 수 없음"))
            self.controls[row.name] = (kind, label, destination)
            for control in (kind, label, destination):
                control.currentIndexChanged.connect(self._update)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.save_button = KeyboardActionButton("Task 정지 및 재시작 안내 확인 · 저장")
        self.save_button.clicked.connect(self.accept)
        layout.addWidget(self.save_button)
        close = KeyboardActionButton("취소")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self._update()
        size_detail_for_viewport(self, 700, 600)

    def selection(self) -> tuple[ToolSettingsEdit, ...]:
        edits = []
        for row in self._view.tool_settings:
            if row.readonly:
                continue
            kind, label, destination = self.controls[row.name]
            edit = ToolSettingsEdit(
                row.name,
                kind.currentData(),
                label.currentData() if kind.currentData() == "read" else None,
                destination.currentData() if kind.currentData() == "send" else None,
            )
            before = ToolSettingsEdit(
                row.name,
                row.kind,
                row.label if row.kind == "read" else None,
                row.destination_arg if row.kind == "send" else None,
            )
            if edit != before:
                edits.append(edit)
        return tuple(edits)

    def _update(self, *_args: object) -> None:
        for row in self._view.tool_settings:
            kind, label, destination = self.controls[row.name]
            kind.setEnabled(not row.readonly)
            label.setEnabled(not row.readonly and kind.currentData() == "read")
            destination.setEnabled(not row.readonly and kind.currentData() == "send")
        if hasattr(self, "save_button"):
            edits = self.selection()
            self.save_button.setEnabled(
                bool(edits)
                and all(
                    (e.kind != "read" or e.label is not None)
                    and (e.kind != "send" or e.destination_arg is not None)
                    for e in edits
                )
            )
