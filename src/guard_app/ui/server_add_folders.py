"""Human folder selections for the built-in file server; C validates on registration."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .presentation import LABELS
from .widgets import KeyboardActionButton, SelectionBox, plain_label


class FolderChoice(QWidget):
    changed = Signal()

    def __init__(self, title: str, *, choose: bool = True) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        self.controls = layout
        layout.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setAccessibleName(title)
        self.browse = KeyboardActionButton(title)
        self.browse.clicked.connect(self._choose)
        self.path.setVisible(choose)
        self.browse.setVisible(choose)
        layout.addWidget(self.path, 1)
        layout.addWidget(self.browse)
        self.label = SelectionBox()
        self.label.setAccessibleName(title + " 등급")
        self.label.addItem(title + " 등급 선택", None)
        for value, (name, _code, _tone) in LABELS.items():
            self.label.addItem(name, value)
        layout.addWidget(self.label)
        self.path.textChanged.connect(lambda _value: self.changed.emit())
        self.label.currentIndexChanged.connect(lambda _index: self.changed.emit())

    def _choose(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "출처 폴더 선택", self.path.text())
        if selected:
            # Keep the exact selected spelling; C checks/canonicalizes it later.
            self.path.setText(selected)


class FileServerFolders(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.notice = plain_label(
            "루트와 하위 폴더를 선택하고 등급을 정하세요. 폴더 이름은 상위 폴더 등급으로 보임. "
            "루트 목록(list_root)의 등급도 따로 선택하세요. "
            "하위 폴더 등급은 루트 등급보다 낮아지지 않음. 겹친 하위 폴더 규칙은 등록할 수 없음.",
            name="secondary",
        )
        layout.addWidget(self.notice)
        self.root = FolderChoice("루트 폴더")
        self.list_root = FolderChoice("루트 목록", choose=False)
        self.root.changed.connect(self.changed.emit)
        self.list_root.changed.connect(self.changed.emit)
        layout.addWidget(self.root)
        layout.addWidget(self.list_root)
        self.rows: list[FolderChoice] = []
        self._rows = QVBoxLayout()
        layout.addLayout(self._rows)
        self.add_button = KeyboardActionButton("하위 폴더 추가")
        self.add_button.clicked.connect(self.add_rule)
        layout.addWidget(self.add_button)

    def add_rule(self) -> FolderChoice:
        row = FolderChoice("하위 폴더")
        remove = KeyboardActionButton("삭제")
        row.controls.addWidget(remove)
        remove.clicked.connect(lambda: self._remove(row))
        row.changed.connect(self.changed.emit)
        self.rows.append(row)
        self._rows.addWidget(row)
        self.changed.emit()
        return row

    def _remove(self, row: FolderChoice) -> None:
        self.rows.remove(row)
        self._rows.removeWidget(row)
        row.deleteLater()
        self.changed.emit()

    def ready(self) -> bool:
        return bool(
            self.root.path.text()
            and self.root.label.currentData()
            and self.list_root.label.currentData()
            and all(row.path.text() and row.label.currentData() for row in self.rows)
        )

    def folder_labels(self) -> tuple[tuple[str, str], ...]:
        return tuple((row.path.text(), str(row.label.currentData())) for row in self.rows)
