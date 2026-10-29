"""Destination editor; sends only immutable bridge commands."""

from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QLineEdit, QVBoxLayout, QWidget

from guard_app.services.bridge import DestinationEdit, PolicyDestination

from .presentation import TIER_NAMES
from .widgets import KeyboardActionButton, SelectionBox, plain_label

DESTINATION_NOTE = (
    "ID는 입력 그대로 정확히 비교됨. 앞뒤 공백·제어 문자 없이 256자 이하로 입력하세요.\n"
    "URL 형태 목적지는 지원하지 않음. 모델에게 사용할 목적지 ID를 대화로 알려 주세요."
)


class DestinationDialog(QDialog):
    def __init__(
        self,
        row: PolicyDestination | None,
        warning: str,
        *,
        deleting: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("보내는 곳(목적지) 삭제" if deleting else "보내는 곳(목적지) 설정")
        self._row, self._deleting = row, deleting
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(DESTINATION_NOTE))
        form = QFormLayout()
        layout.addLayout(form)
        self.identifier = QLineEdit()
        self.identifier.setAccessibleName("목적지 ID")
        if row is None:
            form.addRow("ID", self.identifier)
        else:
            self.identifier.setText(row.destination_id)
            self.identifier.setReadOnly(True)
            self.identifier.hide()
            form.addRow("ID", plain_label(row.destination_id))
        self.tier, self.label = SelectionBox(), SelectionBox()
        self.tier.addItem("경계 종류를 선택하세요", None)
        for key, title in TIER_NAMES.items():
            self.tier.addItem(title, key)
        self.selected = QCheckBox("이 Profile에서 사용")
        form.addRow("경계 종류", self.tier)
        form.addRow("최고 수신 등급", self.label)
        form.addRow(self.selected)
        self.tier.currentIndexChanged.connect(self._labels)
        self._labels()
        if row is not None:
            self.tier.setCurrentIndex(self.tier.findData(row.tier))
            self.label.setCurrentIndex(self.label.findData(row.max_label))
            self.selected.setChecked(row.selected)
        for control in (self.tier, self.label, self.selected):
            control.setEnabled(not deleting)
        layout.addWidget(plain_label(warning, name="runtimeMessage"))
        self.save_button = KeyboardActionButton(
            "Task 정지 안내 확인 · 삭제" if deleting else "Task 정지 안내 확인 · 저장"
        )
        self.save_button.clicked.connect(self.accept)
        layout.addWidget(self.save_button)
        close = KeyboardActionButton("취소")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.identifier.textChanged.connect(self._valid)
        self.tier.currentIndexChanged.connect(self._valid)
        self.label.currentIndexChanged.connect(self._valid)
        self._valid()

    def _labels(self) -> None:
        old = self.label.currentData()
        self.label.clear()
        self.label.addItem("등급을 선택하세요", None)
        values = (
            ("PUBLIC", "INTERNAL", "CONFIDENTIAL")
            if self.tier.currentData() == "internal"
            else ("PUBLIC", "INTERNAL")
        )
        for value in values:
            self.label.addItem(value, value)
        self.label.setCurrentIndex(max(0, self.label.findData(old)))

    def _valid(self) -> None:
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(
                self._deleting
                or bool(
                    self.identifier.text() and self.tier.currentData() and self.label.currentData()
                )
            )

    def selection(self) -> DestinationEdit:
        return DestinationEdit(
            "delete" if self._deleting else "update" if self._row else "add",
            self.identifier.text(),
            self.tier.currentData() or "",
            self.label.currentData() or "",
            self.selected.isChecked(),
        )
