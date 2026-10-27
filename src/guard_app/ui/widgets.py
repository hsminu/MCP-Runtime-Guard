"""Reusable plain-text widgets for trusted, display-only UI projections."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import shiboken6
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import line_icon
from .models import LoadState
from .presentation import LABELS, format_kst
from .style import TOKENS


def plain_label(text: str = "", *, name: str | None = None, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(False)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    label.setWordWrap(wrap)
    if name:
        label.setObjectName(name)
    return label


def table_row_height(table: QTableWidget, lines: int) -> int:
    """A row fits its lines of text plus the item padding, and no more."""
    table.ensurePolished()
    return lines * table.fontMetrics().lineSpacing() + 14


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


class SelectionBox(QComboBox):
    """Native keyboard/popup behavior with a platform-independent chevron."""

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(TOKENS["mute"] if self.isEnabled() else TOKENS["subtle"]), 1.5))
        x = 18 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else self.width() - 18
        y = self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5))
        painter.drawLine(QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5))
        painter.end()


class IconMark(QLabel):
    """A fixed icon painted over the label's styled box, in the current theme."""

    def __init__(self, icon: str, role: str, box: int, size: int, *, name: str) -> None:
        super().__init__()
        self.setObjectName(name)
        self.setFixedSize(box, box)
        self._icon = line_icon(icon, role)
        self._size = size

    def set_icon(self, icon: str, role: str) -> None:
        self._icon = line_icon(icon, role)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        side = self._size
        corner = QPoint((self.width() - side) // 2, (self.height() - side) // 2)
        self._icon.paint(painter, QRect(corner, QSize(side, side)))
        painter.end()


class StatusPill(QLabel):
    """A status in words inside a tinted pill, led by a dot in the same tone.

    Tones: live (running), attn (needs a person), deny (stopped or blocked),
    ok, neutral. The words always carry the meaning; color only repeats it."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("statusPill")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.set_status("", "neutral")

    def set_status(self, text: str, tone: str) -> None:
        self.setText(text)
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(self.foregroundRole()))
        painter.drawEllipse(QPointF(10, self.height() / 2), 3, 3)
        painter.end()


class KeyboardActionButton(QPushButton):
    """Activate focused actions with Return/Enter as well as Qt's Space key."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(16, 16))

    def set_visual(self, *, icon: str, variant: str = "default") -> None:
        self.setProperty("variant", variant)
        roles = {"primary": "on_ink", "soft": "ink", "danger": "danger"}
        self.setIcon(line_icon(icon, roles.get(variant, "body")))
        self.style().unpolish(self)
        self.style().polish(self)

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

    def __init__(
        self, label: str | None = None, parent: QWidget | None = None, *, show_code: bool = False
    ) -> None:
        super().__init__(parent)
        # Summaries show the Korean word; details also print the enum name.
        self._show_code = show_code
        self.setObjectName("securityBadge")
        self.setMinimumHeight(78)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        self.caption = plain_label("보안 등급 · 최대 수신", name="secondary")
        self.value = plain_label(name="securityValue")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        self.set_label(label)

    def set_label(self, label: str | None) -> None:
        korean, english, tone = LABELS.get(label or "", ("미지정", "미제공", "unknown"))
        self.value.setText(f"{korean} · {english}" if self._show_code else korean)
        self.value.setToolTip("" if self._show_code or label not in LABELS else english)
        self.value.setAccessibleName(f"보안 등급 최대 수신: {korean}, {english}")
        for widget in (self, self.value):
            widget.setProperty("labelLevel", tone)
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class StateMessage(QFrame):
    """Show loading, failure, stale, and empty as different states."""

    _MESSAGES: ClassVar[dict[LoadState, str]] = {
        LoadState.UNAVAILABLE: "조회 기능 미연결",
        LoadState.LOADING: "불러오는 중…",
        LoadState.ERROR: "불러오지 못함 · 새로고침 필요",
        LoadState.STALE: "이전 조회 결과 · 다시 확인 필요",
        LoadState.READY: "표시할 항목이 없음",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("placeholderCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.message = plain_label(name="stateMessage")
        layout.addWidget(self.message)
        self.set_state(LoadState.UNAVAILABLE)

    def set_state(self, state: LoadState, *, as_of: datetime | None = None) -> None:
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
        layout.setSpacing(16)
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
