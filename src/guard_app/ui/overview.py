"""Read-only overview ordered by operator attention, never by tool volume.

Every section is a list of hairline rows: a title, its facts on one wrapped
line and one link. Items needing a person come first, right under the counts.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QHelpEvent, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
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
    LoadState,
    OverviewSnapshot,
    OverviewTotals,
    RecentCall,
    ServerSummary,
    Surface,
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
from .widgets import KeyboardActionButton, LabelPill, StateMessage, plain_label


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
        layout.addLayout(top)
        meta = QHBoxLayout()
        meta.setSpacing(8)
        if badge is not None:
            meta.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        self.facts = _flex_label(facts, name="secondary")
        meta.addWidget(self.facts, 1)
        layout.addLayout(meta)


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


class OverviewPage(QWidget):
    """One-way rendering of a bridge projection; emits navigation only."""

    navigate_requested = Signal(str, str)
    _PREVIEW_PAGE_SIZE = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("overviewPage")
        self._server_rows: list[ServerRow] = []
        self._server_surface: Surface[tuple[ServerSummary, ...]] = Surface()
        self._attention_surface: Surface[tuple[AttentionItem, ...]] = Surface()
        self._calls_surface: Surface[tuple[RecentCall, ...]] = Surface()
        self._attention_page = 0
        self._calls_page = 0
        self._priority_server_ids: set[str] = set()
        self._row_links: dict[KeyboardActionButton, tuple[str, str]] = {}
        self._focus_restore_serial = 0
        self._snapshot: OverviewSnapshot | None = None
        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(24, 24, 24, 40)
        outer.setSpacing(24)
        top = QVBoxLayout()
        top.setSpacing(8)
        top.addWidget(plain_label("MCP 운영 현황", name="pageTitle"))
        self.metric_list = QFrame()
        self.metric_list.setObjectName("metricList")
        metric_layout = QVBoxLayout(self.metric_list)
        metric_layout.setContentsMargins(0, 0, 0, 0)
        metric_layout.setSpacing(0)
        self.metric_rows = [
            MetricRow("활성 MCP 서버", "servers", "Registry ACTIVE로 확정된 서버 수"),
            MetricRow("승인 대기", "approvals", "현재 유효한 승인 요청 수"),
            MetricRow("차단된 호출", "logs", "지정한 감사 집계 기간의 차단 호출 수"),
        ]
        for row in self.metric_rows:
            row.navigate_requested.connect(self.navigate_requested)
            metric_layout.addWidget(row)
        self.metric_rows[-1].setProperty("last", True)
        top.addWidget(self.metric_list)
        self.metrics_state = plain_label("조회 기능 미연결", name="secondary")
        top.addWidget(self.metrics_state)
        outer.addLayout(top)

        self.attention_section, attention, self.attention_title = _section("지금 확인할 항목")
        self.attention_state = StateMessage()
        attention.addWidget(self.attention_state)
        self.attention_list = RowList()
        self.attention_layout = self.attention_list.rows
        attention.addWidget(self.attention_list)
        self.attention_pager = self._make_pager("attention")
        attention.addWidget(self.attention_pager)
        outer.addWidget(self.attention_section)

        self.servers_section, servers, _ = _section(
            "연결된 MCP 서버", "세션 연결과 등록 ACTIVE는 서로 다른 상태임."
        )
        self.servers_state = StateMessage()
        servers.addWidget(self.servers_state)
        self.servers_list = RowList()
        servers.addWidget(self.servers_list)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.server_preview_note = plain_label(name="secondary")
        footer.addWidget(self.server_preview_note, 1)
        self.all_servers_button = KeyboardActionButton("서버 목록으로 이동 →")
        self.all_servers_button.setObjectName("linkButton")
        self.all_servers_button.clicked.connect(lambda: self.navigate_requested.emit("servers", ""))
        footer.addWidget(self.all_servers_button)
        servers.addLayout(footer)
        outer.addWidget(self.servers_section)

        self.calls_section, calls, _ = _section(
            "최근 도구 호출", "공개가 허용된 호출 메타데이터만 표시함."
        )
        self.calls_state = StateMessage()
        calls.addWidget(self.calls_state)
        self.calls_list = RowList()
        self.calls_layout = self.calls_list.rows
        calls.addWidget(self.calls_list)
        self.calls_pager = self._make_pager("calls")
        calls.addWidget(self.calls_pager)
        outer.addWidget(self.calls_section)

        self.flow_section, flow, _ = _section(
            "호출 흐름", "참고용 집계 · 백엔드 시계열이 제공될 때만 표시함."
        )
        self.flow_state = StateMessage()
        flow.addWidget(self.flow_state)
        self.flow_chart = FlowChart()
        flow.addWidget(self.flow_chart)
        self.flow_note = plain_label("최근 24개 구간만 표시함.", name="secondary")
        flow.addWidget(self.flow_note)
        outer.addWidget(self.flow_section)
        outer.addStretch(1)
        self.set_snapshot(OverviewSnapshot())

    def set_snapshot(self, snapshot: OverviewSnapshot) -> None:
        if self._snapshot == snapshot:
            return
        focused = self._focused_row_key()
        previous_items = self._unresolved_attention(self._attention_surface)
        previous_ids = {item.item_id for item in previous_items}
        new_items = self._unresolved_attention(snapshot.attention)
        changed_preview = (
            new_items[: self._PREVIEW_PAGE_SIZE] != previous_items[: self._PREVIEW_PAGE_SIZE]
        )
        has_new_item = any(item.item_id not in previous_ids for item in new_items)
        if (focused is None or focused[0] != "attention") and (changed_preview or has_new_item):
            self._attention_page = 0
        self._snapshot = snapshot
        self._row_links.clear()
        self._priority_server_ids = (
            {
                item.server_id
                for item in snapshot.attention.value or ()
                if not item.resolved and item.server_id
            }
            if snapshot.attention.state == LoadState.READY
            else set()
        )
        if focused is not None and focused[0] == "attention":
            index = next(
                (i for i, item in enumerate(new_items) if item.item_id == focused[1]),
                None,
            )
            if index is not None:
                self._attention_page = index // self._PREVIEW_PAGE_SIZE
        if focused is not None and focused[0] == "calls":
            calls = snapshot.recent_calls.value or ()
            index = next(
                (i for i, call in enumerate(calls) if call.call_id == focused[1]),
                None,
            )
            if index is not None:
                self._calls_page = index // self._PREVIEW_PAGE_SIZE
        self._render_totals(snapshot.totals)
        self._render_servers(snapshot.servers)
        self._render_attention(snapshot.attention)
        self._render_calls(snapshot.recent_calls)
        self._render_flow(snapshot.flow)
        self._schedule_row_focus_restore(focused)

    def _focused_row_key(self) -> tuple[str, str] | None:
        focused = QApplication.focusWidget()
        return self._row_links.get(focused) if isinstance(focused, KeyboardActionButton) else None

    def _restore_row_focus(self, key: tuple[str, str]) -> None:
        for link, candidate in self._row_links.items():
            if candidate == key and link.isEnabled() and self.isVisible():
                link.setFocus(Qt.FocusReason.OtherFocusReason)
                return

    def _schedule_row_focus_restore(self, key: tuple[str, str] | None) -> None:
        self._focus_restore_serial += 1
        if key is None:
            return
        serial = self._focus_restore_serial
        # Replacing a focused row can make Qt select the shell's Back button.
        # Restore the corresponding live row before the deferred layout pass.
        self._restore_row_focus(key)

        def restore() -> None:
            if serial != self._focus_restore_serial or not self.isVisible():
                return
            # New rows become visible only after layout. Qt may have parked
            # focus on a shell action while the old row was removed.
            self._restore_row_focus(key)

        QTimer.singleShot(0, restore)

    def _remove_row_links(self, kind: str) -> None:
        self._row_links = {link: key for link, key in self._row_links.items() if key[0] != kind}

    def _make_pager(self, kind: str) -> QWidget:
        pager = QWidget()
        layout = QHBoxLayout(pager)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        note = plain_label(name="secondary")
        previous = KeyboardActionButton("이전")
        next_button = KeyboardActionButton("다음")
        for button in (previous, next_button):
            button.setObjectName("linkButton")
        layout.addWidget(note, 1)
        layout.addWidget(previous)
        layout.addWidget(next_button)
        previous.clicked.connect(lambda: self._change_page(kind, -1))
        next_button.clicked.connect(lambda: self._change_page(kind, 1))
        setattr(self, f"{kind}_previous", previous)
        setattr(self, f"{kind}_next", next_button)
        setattr(self, f"{kind}_page_note", note)
        return pager

    def _change_page(self, kind: str, delta: int) -> None:
        if kind == "attention":
            self._attention_page += delta
            self._render_attention(self._attention_surface)
        else:
            self._calls_page += delta
            self._render_calls(self._calls_surface)

    @staticmethod
    def _unresolved_attention(
        surface: Surface[tuple[AttentionItem, ...]],
    ) -> tuple[AttentionItem, ...]:
        if surface.state not in (LoadState.READY, LoadState.STALE):
            return ()
        seen: set[str] = set()
        items: list[AttentionItem] = []
        for item in surface.value or ():
            if not item.resolved and item.item_id not in seen:
                seen.add(item.item_id)
                items.append(item)
        return tuple(items)

    def _set_pager(self, kind: str, count: int, page: int) -> int:
        last = max(0, (count - 1) // self._PREVIEW_PAGE_SIZE)
        page = max(0, min(page, last))
        pager = getattr(self, f"{kind}_pager")
        pager.setVisible(count > self._PREVIEW_PAGE_SIZE)
        if count > self._PREVIEW_PAGE_SIZE:
            start = page * self._PREVIEW_PAGE_SIZE + 1
            end = min(count, start + self._PREVIEW_PAGE_SIZE - 1)
            getattr(self, f"{kind}_page_note").setText(f"{count}개 중 {start}–{end}개 표시")
        getattr(self, f"{kind}_previous").setEnabled(page > 0)
        getattr(self, f"{kind}_next").setEnabled(page < last)
        return page

    def _render_totals(self, surface: Surface[OverviewTotals]) -> None:
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
        totals = surface.value if ready and surface.value is not None else OverviewTotals()
        unavailable = StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
        self.metric_rows[0].set_count(totals.active_servers, ready=ready, unavailable=unavailable)
        self.metric_rows[1].set_count(
            totals.pending_approvals, ready=ready, unavailable=unavailable
        )
        period = MISSING
        if totals.blocked_period_start and totals.blocked_period_end:
            start = format_kst(totals.blocked_period_start)
            end = format_kst(totals.blocked_period_end)
            if (
                start != MISSING
                and end != MISSING
                and totals.blocked_period_start <= totals.blocked_period_end
            ):
                period = f"{start} ~ {end}"
        self.metric_rows[2].set_count(
            totals.blocked_calls,
            ready=ready,
            detail=f"집계 기간: {period}" if ready else None,
            unavailable=unavailable,
        )
        if surface.state == LoadState.READY:
            state_text = f"집계 기준 시각: {format_kst(surface.as_of)}"
        elif surface.state == LoadState.STALE:
            state_text = f"이전 집계 · 다시 확인 필요 · 마지막 확인 {format_kst(surface.as_of)}"
        else:
            state_text = StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
        self.metrics_state.setText(state_text)

    def _render_servers(self, surface: Surface[tuple[ServerSummary, ...]]) -> None:
        self._server_surface = surface
        self._remove_row_links("servers")
        self.servers_list.clear()
        self._server_rows.clear()
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
        connected = (
            tuple(server for server in surface.value or () if server.session_status == "connected")
            if ready
            else ()
        )
        # Keep the backend's ordering within each group. A related unresolved
        # item makes that server more useful than an unrelated ID-first preview.
        connected = tuple(
            server for server in connected if server.server_id in self._priority_server_ids
        ) + tuple(
            server for server in connected if server.server_id not in self._priority_server_ids
        )
        if ready:
            for server in connected[: self._PREVIEW_PAGE_SIZE]:
                row = ServerRow(server)
                row.navigate_requested.connect(self.navigate_requested)
                self.servers_list.add(row)
                self._server_rows.append(row)
                self._row_links[row.link] = ("servers", server.server_id)
        hidden = len(connected) > len(self._server_rows)
        self.server_preview_note.setText(
            f"연결된 서버 {len(connected)}개 중 {len(self._server_rows)}개 표시" if hidden else ""
        )
        self.server_preview_note.setVisible(hidden)
        self.all_servers_button.setVisible(ready and bool(connected))
        show_message = not ready or not self._server_rows or surface.state == LoadState.STALE
        self.servers_state.setVisible(show_message)
        if show_message:
            if ready and not self._server_rows and surface.state == LoadState.READY:
                self.servers_state.set_message("연결된 MCP 서버가 없음")
            else:
                self.servers_state.set_state(surface.state, as_of=surface.as_of)

    def _render_attention(self, surface: Surface[tuple[AttentionItem, ...]]) -> None:
        self._attention_surface = surface
        self._remove_row_links("attention")
        self.attention_list.clear()
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
        items = self._unresolved_attention(surface) if ready else ()
        self._attention_page = self._set_pager("attention", len(items), self._attention_page)
        first = self._attention_page * self._PREVIEW_PAGE_SIZE
        for item in items[first : first + self._PREVIEW_PAGE_SIZE]:
            row = AttentionRow(item)
            row.navigate_requested.connect(self.navigate_requested)
            self.attention_list.add(row)
            self._row_links[row.link] = ("attention", item.item_id)
        qualifier = "이전 조회 · " if surface.state == LoadState.STALE else ""
        self.attention_title.setText(
            f"지금 확인할 항목 · {qualifier}미해결 {len(items)}개" if ready else "지금 확인할 항목"
        )
        self.attention_state.setVisible(not ready or not items or surface.state == LoadState.STALE)
        if not items and ready and surface.state == LoadState.READY:
            self.attention_state.set_message("지금 확인할 미해결 항목이 없음")
        else:
            self.attention_state.set_state(surface.state, as_of=surface.as_of)

    def _render_calls(self, surface: Surface[tuple[RecentCall, ...]]) -> None:
        self._calls_surface = surface
        self._remove_row_links("calls")
        self.calls_list.clear()
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
        calls = tuple(surface.value or ()) if ready else ()
        self._calls_page = self._set_pager("calls", len(calls), self._calls_page)
        first = self._calls_page * self._PREVIEW_PAGE_SIZE
        for call in calls[first : first + self._PREVIEW_PAGE_SIZE]:
            row = RecentCallRow(call)
            row.navigate_requested.connect(self.navigate_requested)
            self.calls_list.add(row)
            self._row_links[row.link] = ("calls", call.call_id)
        self.calls_state.setVisible(not ready or not calls or surface.state == LoadState.STALE)
        self.calls_state.set_state(surface.state, as_of=surface.as_of)

    def _render_flow(self, surface: Surface[tuple[FlowBucket, ...]]) -> None:
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
        buckets = tuple(surface.value or ()) if ready else ()
        self.flow_chart.set_buckets(buckets[:24])
        self.flow_chart.setVisible(bool(buckets))
        self.flow_note.setVisible(len(buckets) > 24)
        self.flow_state.setVisible(not ready or not buckets or surface.state == LoadState.STALE)
        self.flow_state.set_state(surface.state, as_of=surface.as_of)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = self.width() < 500
        margin = 12 if compact else 24
        self._outer_layout.setContentsMargins(
            margin, 20 if compact else 24, margin, 32 if compact else 40
        )
