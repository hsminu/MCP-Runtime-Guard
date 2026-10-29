"""Human folder selections for the built-in file server; C validates on registration."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .presentation import LABELS
from .widgets import KeyboardActionButton, SelectionBox, plain_label


class FolderChoice(QWidget):
    changed = Signal()

    def __init__(self, title: str, *, folders: FileServerFolders | None = None) -> None:
        super().__init__()
        self._folders = folders
        self.selected_path = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.controls = QHBoxLayout()
        layout.addLayout(self.controls)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setAccessibleName(title)
        self.browse = KeyboardActionButton(title)
        self.browse.setAccessibleName(title + " 선택")
        self.browse.clicked.connect(self._choose)
        self.controls.addWidget(self.path, 1)
        self.controls.addWidget(self.browse)
        self.label = SelectionBox()
        self.label.setAccessibleName(title + " 등급")
        self.set_labels(tuple(LABELS))
        self.controls.addWidget(self.label)
        self.error = plain_label(name="folderChoiceError")
        self.error.setAccessibleName(title + " 선택 오류")
        self.error.hide()
        layout.addWidget(self.error)
        self.path.textChanged.connect(lambda _value: self.changed.emit())
        self.label.currentIndexChanged.connect(lambda _index: self.changed.emit())

    def set_labels(self, values: tuple[str, ...]) -> None:
        selected = self.label.currentData()
        self.label.blockSignals(True)
        self.label.clear()
        self.label.addItem("등급 선택", None)
        for value in values:
            self.label.addItem(LABELS[value][0], value)
        self.label.setCurrentIndex(max(0, self.label.findData(selected)))
        self.label.setEnabled(bool(values))
        self.label.blockSignals(False)

    def _choose(self) -> None:
        folders = self._folders
        start = self.path.text() if folders is None else folders.root.path.text()
        selected = QFileDialog.getExistingDirectory(
            self, "공유 폴더 선택" if folders is None else "하위 폴더 선택", start
        )
        if not selected:
            return
        error = "" if folders is None else folders.selection_error(self, selected)
        self.error.setText(error)
        self.error.setVisible(bool(error))
        self.selected_path = "" if error else selected
        self.path.setToolTip(self.selected_path)
        if error:
            self.path.clear()
        elif folders is None:
            self.path.setText(selected)
        else:
            relative = Path(selected).relative_to(Path(start)).as_posix()
            self.path.setText("└ " + relative)
        self.changed.emit()


class FileServerFolders(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.notice = plain_label(
            "공유 폴더를 고르고 등급을 정하세요. "
            "더 높은 등급이 필요한 하위 폴더만 따로 추가하세요.",
            name="secondary",
        )
        layout.addWidget(self.notice)
        self.root = FolderChoice("공유 폴더")
        layout.addWidget(self.root)
        self.error = plain_label(name="fileFoldersError")
        self.error.setAccessibleName("공유 폴더 등록 입력 오류")
        self.error.hide()
        layout.addWidget(self.error)
        self.rows: list[FolderChoice] = []
        self._root_path = ""
        self._rows = QVBoxLayout()
        layout.addLayout(self._rows)
        self.add_button = KeyboardActionButton("하위 폴더 추가")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self.add_rule)
        layout.addWidget(self.add_button)
        self.root.changed.connect(self._root_changed)

    def _root_changed(self) -> None:
        root = self.root.path.text()
        if root != self._root_path:
            self._root_path = root
            for row in tuple(self.rows):
                self._remove(row)
        self.add_button.setEnabled(bool(root))
        for row in self.rows:
            row.set_labels(self._child_labels())
        self.changed.emit()

    def _child_labels(self) -> tuple[str, ...]:
        values = tuple(LABELS)
        label = self.root.label.currentData()
        return values[values.index(label) :] if label in values else ()

    def add_rule(self) -> FolderChoice:
        row = FolderChoice("하위 폴더", folders=self)
        self._rows.addWidget(row)
        row.set_labels(self._child_labels())
        remove = KeyboardActionButton("삭제")
        row.controls.addWidget(remove)
        remove.clicked.connect(lambda: self._remove(row))
        row.changed.connect(self.changed.emit)
        self.rows.append(row)
        self.changed.emit()
        return row

    def _remove(self, row: FolderChoice) -> None:
        self.rows.remove(row)
        self._rows.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.changed.emit()

    def selection_error(self, row: FolderChoice, selected: str) -> str:
        """Lexical convenience checks only; C validates actual filesystem paths."""
        root, candidate = Path(self.root.path.text()), Path(selected)
        if candidate == root or not candidate.is_relative_to(root) or ".." in candidate.parts:
            return "공유 폴더 안의 폴더를 고르세요."
        for other in self.rows:
            if other is row or not other.selected_path:
                continue
            path = Path(other.selected_path)
            if candidate.is_relative_to(path) or path.is_relative_to(candidate):
                return "이미 고른 하위 폴더와 겹침. 다른 폴더를 고르세요."
        return ""

    def ready(self) -> bool:
        return bool(
            self.root.path.text()
            and self.root.label.currentData()
            and all(row.selected_path and row.label.currentData() for row in self.rows)
        )

    def folder_labels(self) -> tuple[tuple[str, str], ...]:
        return tuple((row.selected_path, str(row.label.currentData())) for row in self.rows)
