"""Reusable plain-text widgets for trusted, display-only UI projections."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import shiboken6
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QToolButton,
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
        painter.setPen(
            QPen(QColor(TOKENS["mute"] if self.isEnabled() else TOKENS["disabled_text"]), 1.5)
        )
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
    """A status in words inside a tinted pill, led by a distinct shape.

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
        color = self.palette().color(self.foregroundRole())
        painter.setPen(QPen(color, 1.5))
        x, y = 10.0, self.height() / 2
        tone = self.property("tone")
        if tone == "ok":
            painter.drawLine(QPointF(x - 3, y), QPointF(x - 1, y + 3))
            painter.drawLine(QPointF(x - 1, y + 3), QPointF(x + 4, y - 3))
        elif tone == "attn":
            painter.setBrush(color)
            painter.drawPolygon(
                [QPointF(x, y - 4), QPointF(x + 4, y), QPointF(x, y + 4), QPointF(x - 4, y)]
            )
        elif tone == "deny":
            painter.drawLine(QPointF(x - 3, y - 3), QPointF(x + 3, y + 3))
            painter.drawLine(QPointF(x - 3, y + 3), QPointF(x + 3, y - 3))
        elif tone == "live":
            painter.drawArc(QRectF(x - 4, y - 4, 8, 8), 90 * 16, -270 * 16)
        else:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, y), 2, 2)
        painter.end()


class KeyboardActionButton(QPushButton):
    """Activate focused actions with Return/Enter as well as Qt's Space key."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(16, 16))

    def set_visual(self, *, icon: str, variant: str = "default") -> None:
        self.setProperty("variant", variant)
        roles = {"primary": "on_primary", "soft": "ink", "danger": "danger"}
        self.setIcon(line_icon(icon, roles.get(variant, "ink")))
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


LABELS_ROLES = frozenset(("public", "internal", "confidential"))


class LabelPill(QLabel):
    """The maximum receivable label as an outlined shield tag for list rows.

    The words name what it is ("최대 수신"), so it never reads as the trust
    boundary, which shares the word 내부."""

    def __init__(self, label: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("labelPill")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.set_label(label)

    def set_label(self, label: str | None, *, text: str | None = None) -> None:
        korean, english, tone = LABELS.get(label or "", ("미지정", "미제공", "unknown"))
        self.setText(text if text is not None else f"최대 수신 {korean}")
        self.setToolTip(english if label in LABELS else "")
        self.setAccessibleName(
            f"{text}, {english}"
            if text is not None
            else f"보안 등급 최대 수신: {korean}, {english}"
        )
        self.setProperty("labelLevel", tone)
        self.style().unpolish(self)
        self.style().polish(self)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        role = str(self.property("labelLevel"))
        line_icon("shield", role if role in LABELS_ROLES else "mute").paint(
            painter, QRect(6, (self.height() - 14) // 2, 14, 14)
        )
        painter.end()


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
        layout.setContentsMargins(32, 8, 12, 8)
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

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        role = str(self.property("labelLevel"))
        line_icon("shield", role if role in LABELS_ROLES else "mute").paint(
            painter, QRect(10, (self.height() - 16) // 2, 16, 16)
        )
        painter.end()


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


class Disclosure(QWidget):
    """One plain-text toggle and a body that occupies space only while open."""

    def __init__(self, title: str = "자세히", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._body: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.button = KeyboardActionButton()
        self.button.setObjectName("linkButton")
        self.button.setCheckable(True)
        self._layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        self.button.toggled.connect(self.set_open)
        self.set_open(False)

    def set_body(self, widget: QWidget) -> None:
        if widget is self._body:
            return
        if self._body is not None:
            self._return_focus()
            self._layout.removeWidget(self._body)
            self._body.hide()
            self._body.setParent(None)
        self._body = widget
        self._layout.addWidget(widget)
        widget.setVisible(self.is_open())
        self.updateGeometry()

    def _return_focus(self) -> None:
        focused = QApplication.focusWidget()
        if (
            self._body is not None
            and focused is not None
            and (focused is self._body or self._body.isAncestorOf(focused))
        ):
            self.button.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_open(self, opened: bool) -> None:
        if not opened:
            self._return_focus()
        self.button.blockSignals(True)
        self.button.setChecked(opened)
        self.button.blockSignals(False)
        self.button.setText(f"{self._title} {'▾' if opened else '▸'}")
        self.button.setAccessibleName(f"{self._title} {'접기' if opened else '펼치기'}")
        if self._body is not None:
            self._body.setVisible(opened)
        self.updateGeometry()

    def is_open(self) -> bool:
        return self.button.isChecked()


class ResponsiveRow(QWidget):
    """Reflow the same widgets without rebuilding them or moving their focus."""

    def __init__(self, breakpoint: int = 480, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._breakpoint = breakpoint
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.row.addWidget(widget, stretch)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.row.setDirection(
            QBoxLayout.Direction.TopToBottom
            if self.width() < self._breakpoint
            else QBoxLayout.Direction.LeftToRight
        )


class FitStack(QStackedWidget):
    """Only the current page contributes preferred and minimum geometry."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.currentChanged.connect(self._page_changed)

    def addWidget(self, widget: QWidget) -> int:
        index = super().addWidget(widget)
        self._page_changed(self.currentIndex())
        return index

    def _page_changed(self, _index: int) -> None:
        for index in range(self.count()):
            page = self.widget(index)
            if page is not None:
                policy = page.sizePolicy()
                policy.setVerticalPolicy(
                    QSizePolicy.Policy.Preferred
                    if page is self.currentWidget()
                    else QSizePolicy.Policy.Ignored
                )
                page.setSizePolicy(policy)
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.sizeHint() if page is not None else QSize(0, 0)

    def minimumSizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else QSize(0, 0)


class SecretLineEdit(QLineEdit):
    """Transient secret entry with explicit reveal and no clipboard export."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.visibility_action = self.addAction(
            line_icon("eye"), QLineEdit.ActionPosition.TrailingPosition
        )
        self.visibility_action.triggered.connect(self._toggle_visibility)
        self._set_revealed(False)

    def _set_revealed(self, revealed: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        title = "키 숨기기" if revealed else "키 표시"
        self.visibility_action.setText(title)
        self.visibility_action.setToolTip(title)
        self.visibility_action.setIcon(line_icon("eye_off" if revealed else "eye"))
        for button in self.findChildren(QToolButton):
            button.setAccessibleName(title)

    def _toggle_visibility(self) -> None:
        self._set_revealed(self.echoMode() == QLineEdit.EchoMode.Password)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy) or event.matches(
            QKeySequence.StandardKey.Cut
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def copy(self) -> None:
        """Programmatic use must obey the same no-export rule as shortcuts."""

    def cut(self) -> None:
        """Programmatic use must obey the same no-export rule as shortcuts."""

    def clear(self) -> None:
        self._set_revealed(False)
        super().clear()

    def hideEvent(self, event: QHideEvent) -> None:
        self._set_revealed(False)
        super().hideEvent(event)


class SectionCard(QFrame):
    """A section with a title, optional status, right actions and one body."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(16, 16, 16, 16)
        self.content.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = plain_label(title, name="cardTitle")
        header.addWidget(self.title)
        self.status_slot = QHBoxLayout()
        self.status_slot.setSpacing(8)
        header.addLayout(self.status_slot)
        header.addStretch(1)
        self.action_slot = QHBoxLayout()
        self.action_slot.setSpacing(8)
        header.addLayout(self.action_slot)
        self.content.addLayout(header)

    def set_status(self, widget: QWidget) -> None:
        while self.status_slot.count():
            item = self.status_slot.takeAt(0)
            previous = item.widget() if item is not None else None
            if previous is not None:
                previous.hide()
                previous.setParent(None)
        self.status_slot.addWidget(widget)
        widget.show()

    def add_action(self, widget: QWidget) -> None:
        self.action_slot.addWidget(widget)

    def add_widget(self, widget: QWidget) -> None:
        if isinstance(widget, SectionCard) or widget.findChildren(SectionCard):
            raise ValueError("SectionCard cannot contain another SectionCard")
        self.content.addWidget(widget)
