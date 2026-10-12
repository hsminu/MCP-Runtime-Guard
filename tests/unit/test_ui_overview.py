"""Display invariants for the A28/A29 Qt overview (not a backend integration test)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from guard_app.domain.enums import TaskStatus
from guard_app.ui import (
    AttentionItem,
    FlowBucket,
    GuardWindow,
    LoadState,
    OverviewSnapshot,
    OverviewTotals,
    RecentCall,
    RuntimeIdentity,
    RuntimeMode,
    ServerSummary,
    Surface,
)
from guard_app.ui.presentation import format_kst
from guard_app.ui.widgets import PlainDetailDialog


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def test_missing_counts_and_failed_query_never_look_like_zero(app) -> None:
    window = GuardWindow()
    assert window.overview.metric_cards[0].value.text() == "미제공"
    assert "조회 연결 대기" in window.overview.metrics_state.text()

    window.set_overview_snapshot(
        OverviewSnapshot(totals=Surface(state=LoadState.ERROR))
    )
    assert window.overview.metric_cards[0].value.text() == "미제공"
    assert "불러오지 못함" in window.overview.metrics_state.text()

    window.set_overview_snapshot(
        OverviewSnapshot(
            totals=Surface(state=LoadState.READY, value=OverviewTotals(0, 0, 0))
        )
    )
    assert window.overview.metric_cards[0].value.text() == "0건"
    assert window.overview.metric_cards[1].value.text() == "0건"
    assert window.overview.metric_cards[2].value.text() == "0건"


def test_overview_order_and_server_preview_adapt_to_window_width(app) -> None:
    window = GuardWindow()
    window.resize(320, 400)
    window.set_overview_snapshot(
        OverviewSnapshot(
            servers=Surface(
                LoadState.READY,
                tuple(
                    ServerSummary(
                        f"server-{index:02d}",
                        f"Server {index}",
                        session_status="connected",
                    )
                    for index in range(12)
                ),
            ),
            flow=Surface(LoadState.READY, (FlowBucket(datetime.now(UTC), 3),)),
        )
    )
    window.show()
    app.processEvents()
    try:
        layout = window.overview.layout()
        assert (
            layout.indexOf(window.overview.metrics_state)
            < layout.indexOf(window.overview.servers_state)
            < layout.indexOf(window.overview.attention_state)
            < layout.indexOf(window.overview.calls_state)
            < layout.indexOf(window.overview.flow_state)
        )
        assert window.overview._metric_columns == 1
        assert window.overview._server_columns == 1
        assert len(window.overview._server_cards) == 1
        assert "12개 중 1개" in window.overview.server_preview_note.text()
        assert window.overview.server_preview_note.isVisibleTo(window.overview)
        assert window.overview.all_servers_button.isVisibleTo(window.overview)
        assert window.overview.attention_state.isVisibleTo(window.overview)
        assert window.stack.currentWidget().horizontalScrollBar().maximum() == 0

        for width, height, expected_cards in (
            (540, 580, 1),
            (900, 700, 2),
            (1100, 750, 2),
        ):
            window.resize(width, height)
            app.processEvents()
            assert len(window.overview._server_cards) == expected_cards
            assert len(window.overview._server_cards) == window.overview._server_columns
            assert (
                f"12개 중 {expected_cards}개"
                in window.overview.server_preview_note.text()
            )
            assert window.stack.currentWidget().horizontalScrollBar().maximum() == 0

        window.resize(320, 400)
        app.processEvents()
        assert len(window.overview._server_cards) == 1
        window.overview.all_servers_button.click()
        assert window._page == "servers"
    finally:
        window.close()


def test_small_overview_exposes_attention_shortcut_and_prioritizes_related_server(
    app,
) -> None:
    window = GuardWindow()
    window.resize(320, 360)
    window.set_overview_snapshot(
        OverviewSnapshot(
            servers=Surface(
                LoadState.READY,
                (
                    ServerSummary("alpha", "Alpha", session_status="connected"),
                    ServerSummary("zeta", "Zeta", session_status="connected"),
                ),
            ),
            attention=Surface(
                LoadState.READY,
                (AttentionItem("urgent-zeta", "outcome_unknown", server_id="zeta"),),
            ),
        )
    )
    window.show()
    app.processEvents()
    try:
        scroll = window.stack.currentWidget()
        assert isinstance(scroll, QScrollArea)
        assert window.overview.attention_shortcut.isVisibleTo(window)
        assert "1개" in window.overview.attention_shortcut_summary.text()
        assert window.overview._server_cards[0].link.accessibleName().startswith("zeta")
        assert scroll.horizontalScrollBar().maximum() == 0
        window.overview.attention_shortcut.click()
        app.processEvents()
        assert scroll.verticalScrollBar().value() > 0
        header_top = window.overview.attention_header.mapTo(
            scroll.viewport(), QPoint(0, 0)
        ).y()
        assert 0 <= header_top <= 20
        first_card = window.overview.attention_layout.itemAt(0).widget()
        card_top = first_card.mapTo(scroll.viewport(), QPoint(0, 0)).y()
        assert 0 <= card_top < scroll.viewport().height()
    finally:
        window.close()


def test_server_preview_keeps_keyboard_focus_when_columns_collapse(app) -> None:
    window = GuardWindow()
    window.resize(900, 700)
    window.set_overview_snapshot(
        OverviewSnapshot(
            servers=Surface(
                LoadState.READY,
                (
                    ServerSummary("alpha", "Alpha", session_status="connected"),
                    ServerSummary("beta", "Beta", session_status="connected"),
                ),
            )
        )
    )
    window.show()
    window.activateWindow()
    app.processEvents()
    try:
        before = window.overview._server_cards[0]
        before.link.setFocus()
        assert before.link.hasFocus()
        window.resize(320, 400)
        app.processEvents()
        after = window.overview._server_cards[0]
        assert after is not before
        assert after.link.hasFocus()
        assert window.stack.currentWidget().horizontalScrollBar().maximum() == 0
    finally:
        window.close()


def test_overview_pages_large_results_and_preserves_card_focus_on_refresh(
    app,
) -> None:
    window = GuardWindow()
    window.resize(320, 400)
    attention = tuple(
        AttentionItem(
            f"attention-{index}",
            "approval",
            target_page="approvals",
            target_id=f"attention-{index}",
            action_available=True,
        )
        for index in range(12)
    )
    calls = tuple(RecentCall(f"call-{index}", "server", "tool") for index in range(11))
    snapshot = OverviewSnapshot(
        attention=Surface(LoadState.READY, attention),
        recent_calls=Surface(LoadState.READY, calls),
    )
    window.set_overview_snapshot(snapshot)
    window.show()
    app.processEvents()
    try:
        page = window.overview
        assert page.attention_layout.count() == 5
        assert page.calls_layout.count() == 5
        assert "12개 중 1–5개" in page.attention_page_note.text()
        page.attention_next.click()
        assert "12개 중 6–10개" in page.attention_page_note.text()
        page.attention_next.click()
        assert page.attention_layout.count() == 2
        assert "12개 중 11–12개" in page.attention_page_note.text()
        page.calls_next.click()
        assert "11개 중 6–10개" in page.calls_page_note.text()

        page.attention_shortcut.click()
        app.processEvents()
        card = page.attention_layout.itemAt(0).widget()
        scroll = window.stack.currentWidget()
        assert isinstance(scroll, QScrollArea)
        scroll.ensureWidgetVisible(card.link)
        window.activateWindow()
        app.processEvents()
        card.link.setFocus()
        app.processEvents()
        assert card.link.hasFocus()
        window.set_overview_snapshot(snapshot)
        assert card.link.hasFocus()
        window.set_overview_snapshot(
            OverviewSnapshot(
                totals=Surface(LoadState.READY, OverviewTotals(1, 2, 3)),
                attention=snapshot.attention,
                recent_calls=snapshot.recent_calls,
            )
        )
        app.processEvents()
        refreshed = page.attention_layout.itemAt(0).widget()
        assert refreshed is not card
        assert refreshed.link.hasFocus()
        assert page.attention_layout.count() == 2
    finally:
        window.close()


def test_runtime_mode_is_unconfigured_until_trusted_bootstrap_sets_it(app) -> None:
    window = GuardWindow()
    assert window.runtime_identity.text() == "AGT: 미설정 · 모델: 미설정"
    window.set_runtime_identity(
        RuntimeIdentity(agt_mode=RuntimeMode.MOCK, model_mode=RuntimeMode.UNCONFIGURED)
    )
    assert window.runtime_identity.text() == "AGT: mock · 모델: 미설정"


def test_runs_page_only_displays_backend_status_without_controls(app) -> None:
    window = GuardWindow()
    window.navigate("runs")
    assert "상태 미확인" in window.task_status_card.status_label.text()

    window.set_task_status(TaskStatus.UNKNOWN_OUTCOME)
    assert "결과 불명" in window.task_status_card.status_label.text()
    assert "수신측" in window.task_status_card.detail_label.text()
    assert window.task_status_card.findChildren(QPushButton) == []


def test_server_add_navigation_is_local_draft_only(app) -> None:
    window = GuardWindow()
    window.navigate("server_add")
    assert window.breadcrumb.text() == "Workspace / MCP 추가 초안"
    assert (
        "Registry 등록은 아직 연결되지 않았어" in window.server_add_form.status.text()
    )
    assert window.server_add_form.validated_draft is None


def test_connected_server_is_not_inferred_from_active_registration(app) -> None:
    window = GuardWindow()
    active_but_disconnected = ServerSummary(
        "registry-active",
        "등록 활성 서버",
        "CONFIDENTIAL",
        "internal",
        "active",
        "disconnected",
        "stdio",
    )
    connected_pending = ServerSummary(
        "connected-pending",
        "세션 연결 서버",
        None,
        "general_external",
        "pending",
        "connected",
        "streamable_http",
    )
    window.set_overview_snapshot(
        OverviewSnapshot(
            totals=Surface(
                state=LoadState.READY, value=OverviewTotals(active_servers=1)
            ),
            servers=Surface(
                state=LoadState.READY,
                value=(active_but_disconnected, connected_pending),
            ),
        )
    )
    assert window.overview.metric_cards[0].value.text() == "1건"
    assert len(window.overview._server_cards) == 1
    card = window.overview._server_cards[0]
    texts = [
        label.text() for label in card.findChildren(type(window.overview.metrics_state))
    ]
    assert "미지정 · 미제공" in texts
    assert "PENDING · 등록 검토 중" in texts
    assert "연결됨" in texts
    assert "일반 외부 · general_external" in texts


def test_attention_deduplicates_and_never_enables_missing_target(app) -> None:
    window = GuardWindow()
    item = AttentionItem(
        "att-1",
        "outcome_unknown",
        "POST_DISPATCH_ERROR",
        server_id="server-1",
        request_id="request-1",
        target_page="logs",
        target_id="call-1",
        action_available=False,
    )
    window.set_overview_snapshot(
        OverviewSnapshot(
            attention=Surface(
                state=LoadState.READY,
                value=(item, item, AttentionItem("att-2", "approval", resolved=True)),
            )
        )
    )
    assert window.overview.attention_layout.count() == 1
    card = window.overview.attention_layout.itemAt(0).widget()
    assert card is not None
    links = card.findChildren(QPushButton)
    assert len(links) == 1 and not links[0].isEnabled()
    assert "수신 결과 확인" in [
        label.text() for label in card.findChildren(type(window.overview.metrics_state))
    ]


def test_navigation_only_emits_a_read_only_target(app) -> None:
    window = GuardWindow()
    destinations: list[tuple[str, str]] = []
    window.navigation_requested.connect(
        lambda page, key: destinations.append((page, key))
    )
    window.overview.metric_cards[1].findChild(QPushButton).click()
    assert destinations == [("approvals", "")]
    assert window.breadcrumb.text() == "Workspace / 승인 대기"


def test_sidebar_navigation_accepts_return_key(app) -> None:
    window = GuardWindow()
    window.resize(1100, 700)
    window.show()
    app.processEvents()
    assert window.sidebar.isVisible()
    button = window._nav_buttons["servers"]
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    assert window._page == "servers"
    window.close()


def test_registry_list_uses_its_own_public_snapshot_and_filters(app) -> None:
    window = GuardWindow()
    public = ServerSummary(
        "public-docs",
        "공개 문서",
        "PUBLIC",
        "general_external",
        "active",
        "connected",
        "stdio",
    )
    pending = ServerSummary(
        "pending-send",
        "외부 전송",
        "INTERNAL",
        "approved_external",
        "pending",
        "disconnected",
        "streamable_http",
    )
    window.set_overview_snapshot(
        OverviewSnapshot(servers=Surface(LoadState.READY, (public, pending)))
    )
    assert window.server_page.table.rowCount() == 0
    assert "조회 연결 대기" in window.server_page.state_message.message.text()

    window.set_server_snapshot(Surface(LoadState.READY, (public, pending)))
    assert window.server_page.table.rowCount() == 2
    assert window.server_page.table.item(0, 3).text() == "공개 · PUBLIC"
    assert window.server_page.table.item(1, 4).text() == "PENDING · 등록 검토 중"
    assert window.server_page.table.item(1, 5).text() == "연결 안 됨"
    assert window.server_page.table.item(0, 0).toolTip() == ""

    window.server_page.search.setText("pending-send")
    assert window.server_page.table.rowCount() == 1
    window.server_page.transport_filter.setCurrentIndex(1)
    assert window.server_page.table.rowCount() == 0
    assert "일치하는 서버가 없어" in window.server_page.state_message.message.text()
    window.server_page.transport_filter.setCurrentIndex(0)
    window.navigate("servers", "pending-send")
    assert window.server_page._selected_id() == "pending-send"


def test_registry_detail_is_plain_text_and_stale_data_is_labeled(app) -> None:
    window = GuardWindow()
    server = ServerSummary(
        "safe-id",
        '<img src="https://example.invalid/secret">',
        None,
        None,
        "active",
        None,
        "stdio",
    )
    window.set_server_snapshot(Surface(LoadState.STALE, (server,)))
    assert window.server_page.state_message.isVisibleTo(window.server_page)
    assert "이전 조회 결과" in window.server_page.state_message.message.text()
    window.navigate("servers", "safe-id")
    window.server_page.detail_button.click()
    dialog = window.server_page._detail_dialog
    assert dialog is not None
    assert '<img src="https://example.invalid/secret">' in [
        label.text()
        for label in dialog.findChildren(type(window.overview.metrics_state))
    ]
    assert "미지정 · 미제공" in [
        label.text()
        for label in dialog.findChildren(type(window.overview.metrics_state))
    ]
    dialog.close()


def test_kst_needs_aware_timestamp_and_plain_detail_keeps_markup_literal(app) -> None:
    assert format_kst(datetime(2026, 10, 11, 16, 30, tzinfo=UTC)) == (
        "2026년 10월 12일 01:30:00 KST"
    )
    assert format_kst(datetime(2026, 10, 12, 1, 30)) == "미제공"
    dialog = PlainDetailDialog()
    marker = '<img src="https://example.invalid/secret">'
    dialog.show_detail(title="합성 상세", text=marker)
    assert dialog.body.toPlainText() == marker
    dialog.close()
