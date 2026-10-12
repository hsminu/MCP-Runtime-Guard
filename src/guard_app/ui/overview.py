"""Read-only overview ordered by operator attention, never by tool volume."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
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
    REGISTRY_STATUSES,
    REQUEST_STATUSES,
    SESSION_STATUSES,
    TIERS,
    TRANSPORTS,
    fixed,
    format_kst,
)
from .widgets import KeyboardActionButton, SecurityLabel, StateMessage, plain_label


def _clear(layout: QVBoxLayout | QGridLayout) -> None:
    while item := layout.takeAt(0):
        if widget := item.widget():
            widget.setParent(None)
            widget.deleteLater()


def _section_header(title: str, subtitle: str) -> QWidget:
    header = QWidget()
    layout = QVBoxLayout(header)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    layout.addWidget(plain_label(title, name="sectionTitle"))
    layout.addWidget(plain_label(subtitle, name="secondary"))
    return header


def _flex_label(text: str, *, name: str | None = None) -> QWidget:
    label = plain_label(text, name=name)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setMinimumWidth(0)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _field(title: str, value: str) -> QWidget:
    row = QWidget()
    row.setObjectName("infoField")
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    layout.addWidget(plain_label(title, name="secondary"))
    layout.addWidget(_flex_label(value))
    return row


class MetricCard(QFrame):
    navigate_requested = Signal(str, str)

    def __init__(self, title: str, page: str, note: str) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._note = note
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        layout.addWidget(plain_label(title, name="cardTitle"))
        self.value = plain_label("—", name="metricValue")
        layout.addWidget(self.value)
        self.caption = plain_label("조회 연결 대기", name="secondary")
        layout.addWidget(self.caption)
        link = KeyboardActionButton(f"{page_label(page)} 보기 →")
        link.setObjectName("linkButton")
        link.setAccessibleName(f"{title}: {page_label(page)} 보기")
        link.clicked.connect(lambda: self.navigate_requested.emit(page, ""))
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)

    def set_count(
        self,
        value: int | None,
        *,
        ready: bool,
        detail: str | None = None,
        unavailable: str = "조회 연결 대기",
    ) -> None:
        valid = (
            ready
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        )
        self.value.setText(f"{value:,}건" if valid else "미제공")
        self.caption.setText(
            (detail or self._note) if ready else f"{self._note} · {unavailable}"
        )


def page_label(page: str) -> str:
    return PAGES.get(page, "관련 화면")


class ServerCard(QFrame):
    navigate_requested = Signal(str, str)

    def __init__(self, server: ServerSummary) -> None:
        super().__init__()
        self.setObjectName("serverCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(
            _flex_label(server.display_name or server.server_id, name="cardTitle")
        )
        layout.addWidget(_flex_label(server.server_id, name="mono"))
        layout.addWidget(SecurityLabel(server.max_label))
        layout.addWidget(_field("신뢰 경계 · tier", fixed(TIERS, server.tier)))
        layout.addWidget(
            _field("등록 상태", fixed(REGISTRY_STATUSES, server.registry_status))
        )
        layout.addWidget(
            _field("세션 상태", fixed(SESSION_STATUSES, server.session_status))
        )
        layout.addWidget(_field("연결 방식", fixed(TRANSPORTS, server.transport)))
        link = KeyboardActionButton("서버 상세 보기 →")
        self.link = link
        link.setObjectName("linkButton")
        link.setAccessibleName(f"{server.server_id} 서버 상세 보기")
        link.clicked.connect(
            lambda: self.navigate_requested.emit("servers", server.server_id)
        )
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)


class AttentionCard(QFrame):
    navigate_requested = Signal(str, str)

    def __init__(self, item: AttentionItem) -> None:
        super().__init__()
        self.setObjectName("attentionCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(8)
        layout.addWidget(
            plain_label(fixed(ATTENTION_KINDS, item.kind), name="cardTitle")
        )
        layout.addWidget(_field("사유", fixed(REASONS, item.reason_code)))
        related = []
        if item.server_id:
            related.append(f"MCP {item.server_id}")
        if item.task_id:
            related.append(f"Task {item.task_id}")
        if item.request_id:
            related.append(f"요청 {item.request_id}")
        layout.addWidget(
            _field("관련 항목", " · ".join(related) if related else MISSING)
        )
        layout.addWidget(_field("발생 시각", format_kst(item.occurred_at)))
        layout.addWidget(_field("대기 시작", format_kst(item.waiting_since)))
        actionable = item.action_available and item.target_page in PAGES
        link = KeyboardActionButton("상세 보기 →" if actionable else "대상 확인 필요")
        self.link = link
        link.setObjectName("linkButton")
        link.setEnabled(actionable)
        if actionable:
            link.clicked.connect(
                lambda: self.navigate_requested.emit(
                    item.target_page or "", item.target_id or ""
                )
            )
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)


class RecentCallCard(QFrame):
    navigate_requested = Signal(str, str)

    def __init__(self, call: RecentCall) -> None:
        super().__init__()
        self.setObjectName("recentCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(6)
        layout.addWidget(_flex_label(call.tool_name, name="cardTitle"))
        layout.addWidget(_field("호출 / MCP", f"{call.call_id} · {call.server_id}"))
        layout.addWidget(_field("일시", format_kst(call.occurred_at)))
        layout.addWidget(_field("최초 판정", fixed(DECISIONS, call.initial_decision)))
        layout.addWidget(
            _field("현재 상태", fixed(REQUEST_STATUSES, call.current_status))
        )
        link = KeyboardActionButton("호출 상세 보기 →")
        self.link = link
        link.setObjectName("linkButton")
        link.clicked.connect(lambda: self.navigate_requested.emit("logs", call.call_id))
        layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)


class FlowCard(QFrame):
    def __init__(self, bucket: FlowBucket, maximum: int) -> None:
        super().__init__()
        self.setObjectName("flowCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        start = plain_label(format_kst(bucket.starts_at), name="secondary")
        row.addWidget(start, 1)
        row.addWidget(plain_label(f"{bucket.call_count}건"))
        layout.addLayout(row)
        bar = QProgressBar()
        bar.setRange(0, max(1, maximum))
        bar.setValue(max(0, bucket.call_count))
        bar.setTextVisible(False)
        bar.setAccessibleName(
            f"{format_kst(bucket.starts_at)} 호출 {bucket.call_count}건"
        )
        layout.addWidget(bar)


class OverviewPage(QWidget):
    """One-way rendering of a bridge projection; emits navigation only."""

    navigate_requested = Signal(str, str)
    _PREVIEW_PAGE_SIZE = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("overviewPage")
        self._metric_columns = 0
        self._server_columns = 0
        self._server_cards: list[ServerCard] = []
        self._server_surface: Surface[tuple[ServerSummary, ...]] = Surface()
        self._server_card_limit = 0
        self._attention_surface: Surface[tuple[AttentionItem, ...]] = Surface()
        self._calls_surface: Surface[tuple[RecentCall, ...]] = Surface()
        self._attention_page = 0
        self._calls_page = 0
        self._priority_server_ids: set[str] = set()
        self._card_links: dict[KeyboardActionButton, tuple[str, str]] = {}
        self._focus_restore_serial = 0
        self._snapshot: OverviewSnapshot | None = None
        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(26, 28, 26, 42)
        outer.setSpacing(22)
        outer.addWidget(plain_label("OVERVIEW", name="eyebrow"))
        outer.addWidget(plain_label("MCP 운영 현황", name="pageTitle"))
        outer.addWidget(
            plain_label(
                "연결 상태와 지금 처리가 필요한 요청부터 확인해.", name="secondary"
            )
        )
        shortcut = QFrame()
        shortcut.setObjectName("attentionCard")
        shortcut_layout = QHBoxLayout(shortcut)
        shortcut_layout.setContentsMargins(12, 8, 12, 8)
        self.attention_shortcut_summary = plain_label(name="secondary")
        self.attention_shortcut_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        shortcut_layout.addWidget(self.attention_shortcut_summary, 1)
        self.attention_shortcut = KeyboardActionButton("확인 항목으로 이동 ↓")
        self.attention_shortcut.setObjectName("linkButton")
        self.attention_shortcut.clicked.connect(self._scroll_to_attention)
        shortcut_layout.addWidget(self.attention_shortcut)
        outer.addWidget(shortcut)

        self.metrics_grid = QGridLayout()
        self.metrics_grid.setSpacing(12)
        self.metric_cards = [
            MetricCard("활성 MCP 서버", "servers", "Registry ACTIVE로 확정된 서버 수"),
            MetricCard("승인 대기", "approvals", "현재 유효한 승인 요청 수"),
            MetricCard("차단된 호출", "logs", "지정한 감사 집계 기간의 차단 호출 수"),
        ]
        for card in self.metric_cards:
            card.navigate_requested.connect(self.navigate_requested)
        outer.addLayout(self.metrics_grid)
        self.metrics_state = plain_label("조회 연결 대기", name="secondary")
        outer.addWidget(self.metrics_state)

        outer.addWidget(
            _section_header(
                "연결된 MCP 서버", "세션 연결과 등록 ACTIVE는 서로 다른 상태야."
            )
        )
        self.servers_state = StateMessage()
        outer.addWidget(self.servers_state)
        self.servers_grid = QGridLayout()
        self.servers_grid.setSpacing(12)
        outer.addLayout(self.servers_grid)
        self.server_preview_note = plain_label(name="secondary")
        outer.addWidget(self.server_preview_note)
        self.all_servers_button = KeyboardActionButton("서버 목록으로 이동 →")
        self.all_servers_button.setObjectName("linkButton")
        self.all_servers_button.clicked.connect(
            lambda: self.navigate_requested.emit("servers", "")
        )
        outer.addWidget(self.all_servers_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.attention_header = _section_header(
            "지금 확인할 항목", "백엔드가 확인한 미해결 항목과 이동 대상을 보여줘."
        )
        outer.addWidget(self.attention_header)
        self.attention_state = StateMessage()
        outer.addWidget(self.attention_state)
        self.attention_layout = QVBoxLayout()
        self.attention_layout.setSpacing(10)
        outer.addLayout(self.attention_layout)
        self.attention_pager = self._make_pager("attention")
        outer.addWidget(self.attention_pager)

        outer.addWidget(
            _section_header("최근 도구 호출", "공개가 허용된 호출 메타데이터만 표시해.")
        )
        self.calls_state = StateMessage()
        outer.addWidget(self.calls_state)
        self.calls_layout = QVBoxLayout()
        self.calls_layout.setSpacing(10)
        outer.addLayout(self.calls_layout)
        self.calls_pager = self._make_pager("calls")
        outer.addWidget(self.calls_pager)

        outer.addWidget(
            _section_header(
                "호출 흐름", "참고용 집계 · 백엔드 시계열이 제공될 때만 표시해."
            )
        )
        self.flow_state = StateMessage()
        outer.addWidget(self.flow_state)
        self.flow_layout = QVBoxLayout()
        self.flow_layout.setSpacing(8)
        outer.addLayout(self.flow_layout)
        outer.addStretch(1)
        self.set_snapshot(OverviewSnapshot())

    def set_snapshot(self, snapshot: OverviewSnapshot) -> None:
        if self._snapshot == snapshot:
            return
        focused = self._focused_card_key()
        self._snapshot = snapshot
        self._card_links.clear()
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
            items = self._unresolved_attention(snapshot.attention)
            index = next(
                (i for i, item in enumerate(items) if item.item_id == focused[1]),
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
        self._reflow(force=True)
        self._schedule_card_focus_restore(focused)

    def _focused_card_key(self) -> tuple[str, str] | None:
        focused = QApplication.focusWidget()
        return (
            self._card_links.get(focused)
            if isinstance(focused, KeyboardActionButton)
            else None
        )

    def _restore_card_focus(self, key: tuple[str, str]) -> None:
        for link, candidate in self._card_links.items():
            if candidate == key and link.isEnabled() and self.isVisible():
                link.setFocus(Qt.FocusReason.OtherFocusReason)
                return

    def _schedule_card_focus_restore(self, key: tuple[str, str] | None) -> None:
        self._focus_restore_serial += 1
        if key is None:
            return
        serial = self._focus_restore_serial

        def restore() -> None:
            if serial != self._focus_restore_serial or not self.isVisible():
                return
            focused = QApplication.focusWidget()
            scroll = self._scroll_host()
            if focused is None or focused is scroll or self._focused_card_key() == key:
                self._restore_card_focus(key)

        QTimer.singleShot(0, restore)

    def _remove_card_links(self, kind: str) -> None:
        self._card_links = {
            link: key for link, key in self._card_links.items() if key[0] != kind
        }

    def _scroll_host(self) -> QScrollArea | None:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _scroll_to_attention(self) -> None:
        scroll = self._scroll_host()
        if scroll is not None:
            scroll.ensureWidgetVisible(self.attention_header, 0, 12)

    def _make_pager(self, kind: str) -> QWidget:
        pager = QWidget()
        layout = QHBoxLayout(pager)
        layout.setContentsMargins(0, 0, 0, 0)
        previous = KeyboardActionButton("이전")
        next_button = KeyboardActionButton("다음")
        note = plain_label(name="secondary")
        layout.addWidget(previous)
        layout.addWidget(note, 1)
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
            getattr(self, f"{kind}_page_note").setText(
                f"{count}개 중 {start}–{end}개 표시"
            )
        getattr(self, f"{kind}_previous").setEnabled(page > 0)
        getattr(self, f"{kind}_next").setEnabled(page < last)
        return page

    def _render_totals(self, surface: Surface[OverviewTotals]) -> None:
        ready = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and surface.value is not None
        )
        totals = (
            surface.value if ready and surface.value is not None else OverviewTotals()
        )
        unavailable = StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
        self.metric_cards[0].set_count(
            totals.active_servers, ready=ready, unavailable=unavailable
        )
        self.metric_cards[1].set_count(
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
        self.metric_cards[2].set_count(
            totals.blocked_calls,
            ready=ready,
            detail=f"집계 기간: {period}" if ready else None,
            unavailable=unavailable,
        )
        if surface.state == LoadState.READY:
            state_text = f"집계 기준 시각: {format_kst(surface.as_of)}"
        elif surface.state == LoadState.STALE:
            state_text = (
                f"이전 집계 · 다시 확인 필요 · 마지막 확인 {format_kst(surface.as_of)}"
            )
        else:
            state_text = StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
        self.metrics_state.setText(state_text)

    def _render_servers(self, surface: Surface[tuple[ServerSummary, ...]]) -> None:
        self._server_surface = surface
        self._remove_card_links("servers")
        _clear(self.servers_grid)
        self._server_cards.clear()
        self._server_card_limit = self._server_preview_limit()
        ready = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and surface.value is not None
        )
        connected = (
            tuple(
                server
                for server in surface.value or ()
                if server.session_status == "connected"
            )
            if ready
            else ()
        )
        # Keep the backend's ordering within each group. A related unresolved
        # item makes that server more useful than an unrelated ID-first preview.
        connected = tuple(
            server
            for server in connected
            if server.server_id in self._priority_server_ids
        ) + tuple(
            server
            for server in connected
            if server.server_id not in self._priority_server_ids
        )
        if ready:
            for server in connected[: self._server_card_limit]:
                card = ServerCard(server)
                card.navigate_requested.connect(self.navigate_requested)
                self._server_cards.append(card)
                self._card_links[card.link] = ("servers", server.server_id)
        self.server_preview_note.setVisible(len(connected) > len(self._server_cards))
        self.server_preview_note.setText(
            f"현황 조회 결과: 연결된 서버 {len(connected)}개 중 {len(self._server_cards)}개 표시"
            if len(connected) > len(self._server_cards)
            else ""
        )
        self.all_servers_button.setVisible(ready and bool(connected))
        show_message = (
            not ready or not self._server_cards or surface.state == LoadState.STALE
        )
        self.servers_state.setVisible(show_message)
        if show_message:
            if ready and not self._server_cards and surface.state == LoadState.READY:
                self.servers_state.set_message("연결된 MCP 서버가 없어")
            else:
                self.servers_state.set_state(surface.state, as_of=surface.as_of)

    def _render_attention(self, surface: Surface[tuple[AttentionItem, ...]]) -> None:
        self._attention_surface = surface
        self._remove_card_links("attention")
        _clear(self.attention_layout)
        ready = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and surface.value is not None
        )
        items = self._unresolved_attention(surface) if ready else ()
        self._attention_page = self._set_pager(
            "attention", len(items), self._attention_page
        )
        first = self._attention_page * self._PREVIEW_PAGE_SIZE
        for item in items[first : first + self._PREVIEW_PAGE_SIZE]:
            card = AttentionCard(item)
            card.navigate_requested.connect(self.navigate_requested)
            self.attention_layout.addWidget(card)
            self._card_links[card.link] = ("attention", item.item_id)
        if ready:
            qualifier = "이전 조회 · " if surface.state == LoadState.STALE else ""
            self.attention_shortcut_summary.setText(
                f"{qualifier}지금 확인할 미해결 항목 {len(items)}개"
            )
        else:
            self.attention_shortcut_summary.setText(
                f"확인 항목: {StateMessage._MESSAGES.get(surface.state, '상태 확인 필요')}"
            )
        self.attention_shortcut.setEnabled(ready)
        self.attention_state.setVisible(
            not ready or not items or surface.state == LoadState.STALE
        )
        if not items and ready and surface.state == LoadState.READY:
            self.attention_state.set_message("지금 확인할 미해결 항목이 없어")
        else:
            self.attention_state.set_state(surface.state, as_of=surface.as_of)

    def _render_calls(self, surface: Surface[tuple[RecentCall, ...]]) -> None:
        self._calls_surface = surface
        self._remove_card_links("calls")
        _clear(self.calls_layout)
        ready = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and surface.value is not None
        )
        calls = tuple(surface.value or ()) if ready else ()
        self._calls_page = self._set_pager("calls", len(calls), self._calls_page)
        first = self._calls_page * self._PREVIEW_PAGE_SIZE
        for call in calls[first : first + self._PREVIEW_PAGE_SIZE]:
            card = RecentCallCard(call)
            card.navigate_requested.connect(self.navigate_requested)
            self.calls_layout.addWidget(card)
            self._card_links[card.link] = ("calls", call.call_id)
        self.calls_state.setVisible(
            not ready or not calls or surface.state == LoadState.STALE
        )
        self.calls_state.set_state(surface.state, as_of=surface.as_of)

    def _render_flow(self, surface: Surface[tuple[FlowBucket, ...]]) -> None:
        _clear(self.flow_layout)
        ready = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and surface.value is not None
        )
        buckets = tuple(surface.value or ()) if ready else ()
        maximum = max((bucket.call_count for bucket in buckets), default=0)
        for bucket in buckets[:24]:
            self.flow_layout.addWidget(FlowCard(bucket, maximum))
        if len(buckets) > 24:
            self.flow_layout.addWidget(
                plain_label("최근 24개 구간만 표시해.", name="secondary")
            )
        self.flow_state.setVisible(
            not ready or not buckets or surface.state == LoadState.STALE
        )
        self.flow_state.set_state(surface.state, as_of=surface.as_of)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _server_preview_limit(self) -> int:
        margin = 12 if self.width() < 500 else 26
        content_width = self.width() - 2 * margin
        return 4 if content_width >= 1180 else 2 if content_width >= 610 else 1

    def _reflow(self, *, force: bool = False) -> None:
        compact = self.width() < 500
        server_rebuilt = False
        if self._server_preview_limit() != self._server_card_limit:
            focused = self._focused_card_key()
            self._render_servers(self._server_surface)
            server_rebuilt = True
            self._schedule_card_focus_restore(focused)
        margin = 12 if compact else 26
        self._outer_layout.setContentsMargins(
            margin, 20 if compact else 28, margin, 32 if compact else 42
        )
        width = self.width() - 2 * margin
        metric_columns = 3 if width >= 900 else 2 if width >= 610 else 1
        server_columns = 4 if width >= 1180 else 2 if width >= 610 else 1
        if force or metric_columns != self._metric_columns:
            self._metric_columns = metric_columns
            for index, card in enumerate(self.metric_cards):
                self.metrics_grid.addWidget(
                    card, index // metric_columns, index % metric_columns
                )
        if force or server_rebuilt or server_columns != self._server_columns:
            self._server_columns = server_columns
            for index, server_card in enumerate(self._server_cards):
                self.servers_grid.addWidget(
                    server_card, index // server_columns, index % server_columns
                )
