"""Session list row data and palette-based painting."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from .icons import line_icon
from .style import TOKENS, ui_font

_ROW_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True, slots=True)
class _SessionRow:
    title: str
    time: str
    status: str
    tone: str
    approval: bool = False


@dataclass(frozen=True, slots=True)
class _ActivityRow:
    active: bool = False


class _SessionRowDelegate(QStyledItemDelegate):
    """Draw a session row as status mark, title, time and status line.

    The item's display text stays "title\\nHH:MM · status" for search and
    assistive technology. Marks differ in shape as well as color; the selected
    row is a bordered card, as in Orca's worktree list.
    A compact activity subrow connects to the selected session with an elbow line."""

    _HEIGHT = 52
    menu_requested = Signal(str, object)

    @staticmethod
    def menu_rect(rect: QRect) -> QRect:
        """Shared paint/hit target in viewport coordinates."""
        return QRect(rect.right() - 31, rect.top() + 3, 28, 23)

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if (
            isinstance(index.data(_ROW_ROLE), _SessionRow)
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
            and self.menu_rect(option.rect).contains(event.position().toPoint())
            and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease)
        ):
            if event.type() == QEvent.Type.MouseButtonRelease:
                self.menu_requested.emit(
                    index.data(Qt.ItemDataRole.UserRole), event.globalPosition().toPoint()
                )
            return True
        return super().editorEvent(event, model, option, index)

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        height = 32 if isinstance(index.data(_ROW_ROLE), _ActivityRow) else self._HEIGHT
        return QSize(option.rect.width(), height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(_ROW_ROLE)
        if isinstance(row, _ActivityRow):
            state = option.state
            rect = QRectF(option.rect).adjusted(0.5, 1.5, -0.5, -1.5)
            emphasized = row.active or bool(state & QStyle.StateFlag.State_Selected)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(TOKENS["hairline_strong"]), 1.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            center = QPointF(rect.left() + 13, rect.center().y())
            painter.drawLine(QPointF(center.x(), option.rect.top()), center)
            painter.drawLine(center, QPointF(rect.left() + 24, center.y()))
            body = QRectF(rect.left() + 28, rect.top(), rect.width() - 28, rect.height())
            if emphasized:
                painter.setPen(QPen(QColor(TOKENS["selected_line"]), 1))
                painter.setBrush(QColor(TOKENS["selected"]))
                painter.drawRoundedRect(body, 6, 6)
            elif state & QStyle.StateFlag.State_MouseOver:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(TOKENS["hover"]))
                painter.drawRoundedRect(body, 6, 6)
            if state & QStyle.StateFlag.State_HasFocus:
                painter.setPen(QPen(QColor(TOKENS["primary"]), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(body, 6, 6)
            icon = QRectF(body.left() + 8, body.center().y() - 7.5, 15, 15)
            line_icon("panel", "primary_text" if emphasized else "mute").paint(
                painter, icon.toRect()
            )
            left = icon.right() + 6
            width = max(0, int(body.right() - 8 - left))
            font = ui_font(13, QFont.Weight.DemiBold if emphasized else QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(TOKENS["primary_text" if emphasized else "body"]))
            painter.drawText(
                QRectF(left, body.top(), width, body.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QFontMetrics(font).elidedText(
                    index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideRight, width
                ),
            )
            painter.restore()
            return
        if not isinstance(row, _SessionRow):
            super().paint(painter, option, index)
            return
        state = option.state
        rect = QRectF(option.rect).adjusted(0.5, 1.5, -0.5, -1.5)
        selected = bool(state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            painter.setPen(QPen(QColor(TOKENS["selected_line"]), 1))
            painter.setBrush(QColor(TOKENS["selected"]))
            painter.drawRoundedRect(rect, 6, 6)
            painter.fillRect(
                QRectF(rect.left(), rect.top() + 6, 3, rect.height() - 12),
                QColor(TOKENS["primary"]),
            )
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TOKENS["hover"]))
            painter.drawRoundedRect(rect, 6, 6)
        if state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(TOKENS["primary"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)
        self._paint_mark(painter, row.tone, QPointF(rect.left() + 13, rect.top() + 13))
        left = rect.left() + 26
        right = rect.right() - 8
        # Times are data: tabular mono, as in Paradev's workspace rows.
        time_font = ui_font(12, mono=True)
        time_width = QFontMetrics(time_font).horizontalAdvance(row.time)
        painter.setFont(time_font)
        painter.setPen(QColor(TOKENS["mute"]))
        if selected or state & QStyle.StateFlag.State_MouseOver:
            target = self.menu_rect(option.rect)
            time_width = target.width()
            line_icon("more", "mute").paint(painter, target.adjusted(5, 3, -5, -3))
        else:
            painter.drawText(
                QRectF(right - time_width, rect.top() + 4, time_width, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                row.time,
            )
        title_font = ui_font(14, QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        title_width = max(0, int(right - time_width - 8 - left))
        painter.setFont(title_font)
        painter.setPen(QColor(TOKENS["primary_text" if selected else "ink"]))
        painter.drawText(
            QRectF(left, rect.top() + 4, title_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(
                row.title, Qt.TextElideMode.ElideRight, title_width
            ),
        )
        status_font = ui_font(12)
        status_width = max(0, int(right - left))
        painter.setFont(status_font)
        if row.approval:
            tag_width = QFontMetrics(status_font).horizontalAdvance("승인 필요") + 12
            tag = QRectF(right - tag_width, rect.top() + 26, tag_width, 18)
            painter.setPen(QPen(QColor(TOKENS["warn_line"]), 1))
            painter.setBrush(QColor(TOKENS["warn_soft"]))
            painter.drawRoundedRect(tag, 4, 4)
            painter.setPen(QColor(TOKENS["warn"]))
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, "승인 필요")
            status_width = max(0, status_width - tag_width - 6)
        role = (
            "warn"
            if row.tone in ("approval", "attention")
            else "primary_text"
            if row.tone == "running"
            else "mute"
        )
        painter.setPen(QColor(TOKENS[role]))
        painter.drawText(
            QRectF(left, rect.top() + 26, status_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(status_font).elidedText(
                row.status, Qt.TextElideMode.ElideRight, status_width
            ),
        )
        painter.restore()

    @staticmethod
    def _paint_mark(painter: QPainter, tone: str, center: QPointF) -> None:
        x, y = center.x(), center.y()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(Qt.PenStyle.NoPen)
        if tone == "draft":
            pen = QPen(QColor(TOKENS["subtle"]), 1.3)
            pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.drawEllipse(center, 4, 4)
        elif tone == "running":
            # An open arc identifies work in progress.
            pen = QPen(QColor(TOKENS["primary_text"]), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(QRectF(x - 4.5, y - 4.5, 9, 9), 90 * 16, -270 * 16)
        elif tone == "idle":
            painter.setPen(QPen(QColor(TOKENS["ok"]), 1.6))
            painter.drawEllipse(center, 3.8, 3.8)
        elif tone in ("attention", "approval"):
            painter.setBrush(QColor(TOKENS["warn"]))
            painter.drawPolygon(
                [QPointF(x, y - 5), QPointF(x + 5, y), QPointF(x, y + 5), QPointF(x - 5, y)]
            )
        elif tone == "stopped":
            painter.setBrush(QColor(TOKENS["danger"]))
            painter.drawRoundedRect(QRectF(x - 4, y - 4, 8, 8), 1.5, 1.5)
        else:
            painter.setPen(QPen(QColor(TOKENS["subtle"]), 1.6))
            painter.drawLine(QPointF(x - 3, y), QPointF(x + 3, y))
