"""Session list row data and palette-based painting."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from .style import TOKENS, ui_font

_ROW_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True, slots=True)
class _SessionRow:
    title: str
    time: str
    status: str
    tone: str


class _SessionRowDelegate(QStyledItemDelegate):
    """Draw a session row as status mark, title, time and status line.

    The item's display text stays "title\\nHH:MM · status" for search and
    assistive technology. Marks differ in shape as well as color; the selected
    row is a bordered card, as in Orca's worktree list."""

    _HEIGHT = 44

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(option.rect.width(), self._HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(_ROW_ROLE)
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
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TOKENS["hover"]))
            painter.drawRoundedRect(rect, 6, 6)
        if state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(TOKENS["line_hover"]), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)
        self._paint_mark(painter, row.tone, QPointF(rect.left() + 13, rect.top() + 13))
        left = rect.left() + 26
        right = rect.right() - 8
        # Times are data: tabular mono, as in Paradev's workspace rows.
        time_font = ui_font(11, mono=True)
        time_width = QFontMetrics(time_font).horizontalAdvance(row.time)
        painter.setFont(time_font)
        painter.setPen(QColor(TOKENS["mute"]))
        painter.drawText(
            QRectF(right - time_width, rect.top() + 4, time_width, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            row.time,
        )
        title_font = ui_font(13, QFont.Weight.Medium)
        title_width = max(0, int(right - time_width - 8 - left))
        painter.setFont(title_font)
        painter.setPen(QColor(TOKENS["ink"]))
        painter.drawText(
            QRectF(left, rect.top() + 4, title_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(title_font).elidedText(
                row.title, Qt.TextElideMode.ElideRight, title_width
            ),
        )
        status_font = ui_font(11)
        status_width = max(0, int(right - left))
        painter.setFont(status_font)
        # Only a pending approval colors its words; other states stay gray.
        painter.setPen(QColor(TOKENS["warn" if row.tone == "approval" else "mute"]))
        painter.drawText(
            QRectF(left, rect.top() + 22, status_width, 15),
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
            # An open ember arc reads as work in progress (Paradev's spinner glyph).
            pen = QPen(QColor(TOKENS["accent"]), 1.8)
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
