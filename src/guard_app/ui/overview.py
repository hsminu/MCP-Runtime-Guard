"""Overview page orchestration and focus/paging state."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QWidget

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
from .overview_presentation import connected_servers, priority_server_ids, unresolved_attention
from .overview_rows import (
    AttentionRow as AttentionRow,
)
from .overview_rows import (
    FlowChart as FlowChart,
)
from .overview_rows import (
    ListRow as ListRow,
)
from .overview_rows import (
    MetricRow as MetricRow,
)
from .overview_rows import (
    RecentCallRow as RecentCallRow,
)
from .overview_rows import (
    RowList as RowList,
)
from .overview_rows import (
    ServerRow as ServerRow,
)
from .overview_rows import (
    _flex_label as _flex_label,
)
from .overview_rows import (
    _section as _section,
)
from .overview_rows import (
    _short_time as _short_time,
)
from .overview_rows import (
    page_label as page_label,
)
from .presentation import MISSING, format_kst
from .widgets import KeyboardActionButton, StateMessage, plain_label


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
        self._attention_items: tuple[AttentionItem, ...] = ()
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
        outer.addLayout(top)
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
        servers.addLayout(footer)
        footer.setSpacing(8)
        self.server_preview_note = plain_label(name="secondary")
        footer.addWidget(self.server_preview_note, 1)
        self.all_servers_button = KeyboardActionButton("서버 목록으로 이동 →")
        self.all_servers_button.setObjectName("linkButton")
        self.all_servers_button.clicked.connect(lambda: self.navigate_requested.emit("servers", ""))
        footer.addWidget(self.all_servers_button)
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
        previous_items = self._attention_items
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
        self._priority_server_ids = priority_server_ids(snapshot.attention)
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
        self._render_attention(snapshot.attention, items=new_items)
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

    _unresolved_attention = staticmethod(unresolved_attention)

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
        connected = connected_servers(surface, self._priority_server_ids)
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

    def _render_attention(
        self,
        surface: Surface[tuple[AttentionItem, ...]],
        *,
        items: tuple[AttentionItem, ...] | None = None,
    ) -> None:
        if items is None:
            items = (
                self._attention_items
                if surface == self._attention_surface
                else self._unresolved_attention(surface)
            )
        self._attention_items = items
        self._attention_surface = surface
        self._remove_row_links("attention")
        self.attention_list.clear()
        ready = surface.state in (LoadState.READY, LoadState.STALE) and surface.value is not None
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
