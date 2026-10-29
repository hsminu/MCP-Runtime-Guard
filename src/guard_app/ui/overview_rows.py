"""Read-only overview ordered by operator attention, never by tool volume.

Every section is a list of hairline rows: a title, its facts on one wrapped
line and one link. Items needing a person come first, right under the counts.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QHelpEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .models import (
    AttentionItem,
    FlowBucket,
    RecentCall,
    ServerSummary,
)
from .presentation import (
    ATTENTION_KINDS,
    DECISIONS,
    MISSING,
    PAGES,
    REASONS,
    REGISTRY_NAMES,
    REQUEST_STATUSES,
    SESSION_STATUSES,
    TIER_NAMES,
    TRANSPORTS,
    fixed,
    format_kst,
    format_kst_short,
    public_display_text,
)
from .style import TOKENS
from .widgets import KeyboardActionButton, LabelPill, plain_label


def _flex_label(text: str, *, name: str | None = None) -> QLabel:
    label = plain_label(text, name=name)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setMinimumWidth(0)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _section(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout, QLabel]:
    section = QWidget()
    layout = QVBoxLayout(section)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    head = plain_label(title, name="sectionTitle")
    layout.addWidget(head)
    if subtitle:
        layout.addWidget(plain_label(subtitle, name="secondary"))
    return section, layout, head


def _short_time(value: datetime | None) -> str | None:
    """Rows leave a missing time out instead of printing 미제공 for each one."""
    text = format_kst_short(value)
    return None if text == MISSING else text


class MetricRow(QFrame):
    """One line of the overview's status list: what, how many, and what it counts.

    A list of rows instead of icon cards with a big number (the dashboard
    template that reads as generated)."""

    navigate_requested = Signal(str, str)

    def __init__(self, title: str, page: str, note: str) -> None:
        super().__init__()
        self.setObjectName("metricRow")
        self._note = note
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 12, 8)
        layout.setSpacing(16)
        layout.addWidget(plain_label(title, name="cardTitle", wrap=False))
        self.value = plain_label("—", name="metricValue", wrap=False)
        layout.addWidget(self.value)
        self.caption = _flex_label("조회 기능 미연결", name="secondary")
        layout.addWidget(self.caption, 1)
        link = KeyboardActionButton("보기")
        link.setObjectName("linkButton")
        link.setAccessibleName(f"{title}: {page_label(page)} 보기")
        link.clicked.connect(lambda: self.navigate_requested.emit(page, ""))
        layout.addWidget(link)

    def set_count(
        self,
        value: int | None,
        *,
        ready: bool,
        detail: str | None = None,
        unavailable: str = "조회 기능 미연결",
    ) -> None:
        valid = ready and isinstance(value, int) and not isinstance(value, bool) and value >= 0
        self.value.setText(f"{value:,}건" if valid else "미제공")
        self.value.setProperty("unavailable", not valid)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)
        self.caption.setText((detail or self._note) if ready else f"{self._note} · {unavailable}")


def page_label(page: str) -> str:
    return PAGES.get(page, "관련 화면")


class ListRow(QFrame):
    """A title line ending in one link, then the facts on one wrapped line."""

    navigate_requested = Signal(str, str)

    def __init__(
        self,
        title: str,
        facts: str,
        *,
        link_text: str,
        target: tuple[str, str] | None,
        lead: str = "",
        title_name: str | None = "cardTitle",
        badge: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("listRow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 12, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        layout.addLayout(top)
        top.setSpacing(8)
        if lead:
            top.addWidget(plain_label(lead, name="cardTitle", wrap=False))
        self.title = _flex_label(title, name=title_name)
        top.addWidget(self.title, 1)
        self.link = KeyboardActionButton(link_text)
        self.link.setObjectName("linkButton")
        self.link.setEnabled(target is not None)
        if target is not None:
            page, key = target
            self.link.clicked.connect(lambda: self.navigate_requested.emit(page, key))
        top.addWidget(self.link, 0, Qt.AlignmentFlag.AlignTop)
        meta = QHBoxLayout()
        layout.addLayout(meta)
        meta.setSpacing(8)
        if badge is not None:
            meta.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        self.facts = _flex_label(facts, name="secondary")
        meta.addWidget(self.facts, 1)


class ServerRow(ListRow):
    def __init__(self, server: ServerSummary) -> None:
        server_id = public_display_text(server.server_id)
        facts = " · ".join(
            (
                server_id,
                f"신뢰 경계 {fixed(TIER_NAMES, server.tier)}",
                fixed(REGISTRY_NAMES, server.registry_status),
                f"세션 {fixed(SESSION_STATUSES, server.session_status)}",
                fixed(TRANSPORTS, server.transport),
            )
        )
        super().__init__(
            public_display_text(server.display_name or server.server_id),
            facts,
            link_text="상세 보기",
            target=("servers", server.server_id),
            badge=LabelPill(server.max_label),
        )
        self.link.setAccessibleName(f"{server_id} 서버 상세 보기")


class AttentionRow(ListRow):
    def __init__(self, item: AttentionItem) -> None:
        facts: list[str] = []
        if item.server_id:
            facts.append(f"MCP {public_display_text(item.server_id)}")
        if item.task_id:
            facts.append(f"Task {public_display_text(item.task_id)}")
        if item.request_id:
            facts.append(f"요청 {public_display_text(item.request_id)}")
        if (occurred := _short_time(item.occurred_at)) is not None:
            facts.append(f"발생 {occurred}")
        if (waiting := _short_time(item.waiting_since)) is not None:
            facts.append(f"대기 시작 {waiting}")
        actionable = item.action_available and item.target_page in PAGES
        kind = fixed(ATTENTION_KINDS, item.kind)
        reason = fixed(REASONS, item.reason_code)
        super().__init__(
            "사유 미제공" if reason == MISSING else reason,
            " · ".join(facts) or "관련 항목 미제공",
            link_text="상세 보기" if actionable else "대상 확인 필요",
            target=(item.target_page or "", item.target_id or "") if actionable else None,
            lead=kind,
            title_name=None,
        )
        self.link.setAccessibleName(f"{kind} 상세 보기" if actionable else "대상 확인 필요")


class RecentCallRow(ListRow):
    def __init__(self, call: RecentCall) -> None:
        call_id = public_display_text(call.call_id)
        facts = [call_id, f"MCP {public_display_text(call.server_id)}"]
        if (when := _short_time(call.occurred_at)) is not None:
            facts.append(when)
        facts.append(f"최초 판정 {fixed(DECISIONS, call.initial_decision)}")
        facts.append(fixed(REQUEST_STATUSES, call.current_status))
        super().__init__(
            public_display_text(call.tool_name),
            " · ".join(facts),
            link_text="상세 보기",
            target=("logs", call.call_id),
        )
        self.link.setAccessibleName(f"{call_id} 호출 상세 보기")


class RowList(QFrame):
    """Rows separated by hairlines inside one bordered box; hidden when empty."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("rowList")
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.setVisible(False)

    def clear(self) -> None:
        while item := self.rows.takeAt(0):
            if widget := item.widget():
                widget.setParent(None)
                widget.deleteLater()
        self.setVisible(False)

    def add(self, row: ListRow) -> None:
        last = self.rows.itemAt(self.rows.count() - 1)
        if last is not None and (previous := last.widget()) is not None:
            previous.setProperty("last", False)
            previous.style().unpolish(previous)
            previous.style().polish(previous)
        row.setProperty("last", True)
        self.rows.addWidget(row)
        self.setVisible(True)


class FlowChart(QWidget):
    """Calls per period as thin columns: the shape at a glance, numbers on hover.

    Colors are palette roles read at paint time, so a theme switch repaints it.
    """

    _TOP = 20
    _BOTTOM = 20

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("flowChart")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(120)
        self.setMouseTracking(True)
        self.buckets: tuple[FlowBucket, ...] = ()

    def sizeHint(self) -> QSize:
        return QSize(480, 120)

    def set_buckets(self, buckets: tuple[FlowBucket, ...]) -> None:
        self.buckets = buckets
        counts = [max(0, bucket.call_count) for bucket in buckets]
        self.setAccessibleName(
            f"호출 흐름: {len(buckets)}개 구간, 합계 {sum(counts):,}건, "
            f"가장 많은 구간 {max(counts, default=0):,}건"
        )
        self.setAccessibleDescription(
            ", ".join(f"{self._bucket_text(bucket)}" for bucket in buckets)
        )
        self.update()

    @staticmethod
    def _bucket_text(bucket: FlowBucket) -> str:
        return f"{format_kst(bucket.starts_at)} {max(0, bucket.call_count):,}건"

    def _bars(self) -> list[tuple[QRectF, FlowBucket]]:
        if not self.buckets:
            return []
        peak = max(1, *(bucket.call_count for bucket in self.buckets))
        slot = self.width() / len(self.buckets)
        width = max(2.0, min(24.0, slot * 0.6))
        floor = self.height() - self._BOTTOM
        span = floor - self._TOP
        bars = []
        for index, bucket in enumerate(self.buckets):
            height = span * max(0, bucket.call_count) / peak
            left = index * slot + (slot - width) / 2
            bars.append((QRectF(left, floor - height, width, height), bucket))
        return bars

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        floor = self.height() - self._BOTTOM
        painter.setPen(QColor(TOKENS["hairline_strong"]))
        painter.drawLine(QPointF(0, floor + 0.5), QPointF(self.width(), floor + 0.5))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TOKENS["mute"]))
        for rect, _bucket in self._bars():
            painter.drawRoundedRect(rect, 2, 2)
        if self.buckets:
            painter.setPen(QColor(TOKENS["mute"]))
            peak = max(0, *(bucket.call_count for bucket in self.buckets))
            painter.drawText(QPointF(0, 12), f"최대 {peak:,}건")
            first = format_kst_short(self.buckets[0].starts_at)
            last = format_kst_short(self.buckets[-1].starts_at)
            bottom = QRectF(0, floor + 4, self.width(), self._BOTTOM - 4)
            painter.drawText(bottom, Qt.AlignmentFlag.AlignLeft, first)
            painter.drawText(bottom, Qt.AlignmentFlag.AlignRight, last)
        painter.end()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            slot = self.width() / len(self.buckets) if self.buckets else 0
            index = int(event.pos().x() // slot) if slot else -1
            if 0 <= index < len(self.buckets):
                bucket = self.buckets[index]
                QToolTip.showText(
                    event.globalPos(),
                    f"{format_kst_short(bucket.starts_at)} · {max(0, bucket.call_count):,}건",
                    self,
                )
            else:
                QToolTip.hideText()
            return True
        return super().event(event)
