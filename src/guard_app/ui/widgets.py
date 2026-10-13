"""Reusable plain-text widgets for trusted, display-only UI projections."""

from __future__ import annotations

from typing import ClassVar

import shiboken6
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .models import LoadState
from .presentation import LABELS, format_kst


def plain_label(text: str = "", *, name: str | None = None, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    label.setWordWrap(wrap)
    if name:
        label.setObjectName(name)
    return label


def size_detail_for_viewport(dialog: QDialog, width: int, height: int) -> None:
    """Choose an initial modal size that fits both its owner and the screen."""

    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        width = min(width, max(240, area.width() - 24))
        height = min(height, max(200, area.height() - 24))
    owner = dialog.parentWidget()
    if owner is not None and owner.window().isVisible():
        window = owner.window()
        width = min(width, max(240, window.width() - 24))
        height = min(height, max(200, window.height() - 24))
    dialog.resize(width, height)


class KeyboardActionButton(QPushButton):
    """Activate focused actions with Return/Enter as well as Qt's Space key."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat() and self.isEnabled():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyReleaseEvent(event)


class SecurityLabel(QFrame):
    """Maximum receivable label. It is distinct from tier and registry state."""

    def __init__(self, label: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("securityBadge")
        self.setMinimumHeight(78)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)
        self.caption = plain_label("보안 등급 · 최대 수신", name="secondary")
        self.value = plain_label(name="securityValue")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        self.set_label(label)

    def set_label(self, label: str | None) -> None:
        korean, english, tone = LABELS.get(label or "", ("미지정", "미제공", "unknown"))
        self.value.setText(f"{korean} · {english}")
        self.value.setAccessibleName(f"보안 등급 최대 수신: {korean}, {english}")
        for widget in (self, self.value):
            widget.setProperty("labelLevel", tone)
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class StateMessage(QFrame):
    """Show loading, failure, stale, and empty as different states."""

    _MESSAGES: ClassVar[dict[LoadState, str]] = {
        LoadState.UNAVAILABLE: "조회 연결 대기",
        LoadState.LOADING: "불러오는 중…",
        LoadState.ERROR: "불러오지 못함 · 새로고침 필요",
        LoadState.STALE: "이전 조회 결과 · 다시 확인 필요",
        LoadState.READY: "표시할 항목이 없어",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("placeholderCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.message = plain_label(name="stateMessage")
        layout.addWidget(self.message)
        self.set_state(LoadState.UNAVAILABLE)

    def set_state(self, state: LoadState, *, as_of=None) -> None:
        message = self._MESSAGES.get(state, "상태 확인 필요")
        if state == LoadState.STALE and as_of is not None:
            message += f" · 마지막 확인 {format_kst(as_of)}"
        self.set_message(message)

    def set_message(self, message: str) -> None:
        self.message.setText(message)
        self.setAccessibleName(message)


class PlainDetailDialog(QDialog):
    """Large selectable plain-text dialog for separately authorized details.

    The caller is responsible for limiting data to the appropriate approved
    projection. This widget never interprets markup or fetches remote content.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(240, 200)
        size_detail_for_viewport(self, 900, 650)
        self._return_focus: QWidget | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        self.heading = plain_label(name="pageTitle")
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setUndoRedoEnabled(False)
        self.body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        close_button = KeyboardActionButton("닫기")
        close_button.clicked.connect(self.close)
        layout.addWidget(self.heading)
        layout.addWidget(self.body, 1)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        self.finished.connect(self._restore_focus)

    def show_detail(self, *, title: str, text: str, return_focus: QWidget | None = None) -> None:
        self.setWindowTitle(title)
        self.heading.setText(title)
        self.body.setPlainText(text)
        self._return_focus = return_focus
        size_detail_for_viewport(self, 900, 650)
        self.open()
        self.body.setFocus()

    def _restore_focus(self, _result: int) -> None:
        target = self._return_focus
        self._return_focus = None
        if target is not None and shiboken6.isValid(target):

            def restore_if_closed() -> None:
                if shiboken6.isValid(self) and not self.isVisible() and shiboken6.isValid(target):
                    target.setFocus()

            QTimer.singleShot(0, restore_if_closed)
