"""Display invariants for the A28/A29 Qt overview (not a backend integration test)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from guard_app.domain.enums import TaskStatus
from guard_app.ui import (
    AttentionItem,
    FlowBucket,
    GuardWindow,
    LoadState,
    OverviewSnapshot,
    OverviewTotals,
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


def test_overview_puts_attention_before_server_inventory_at_narrow_width(app) -> None:
    window = GuardWindow()
    window.resize(320, 400)
    window.set_overview_snapshot(
        OverviewSnapshot(
            servers=Surface(
                LoadState.READY,
                (ServerSummary("server-alpha", "Alpha", session_status="connected"),),
            ),
            flow=Surface(LoadState.READY, (FlowBucket(datetime.now(UTC), 3),)),
        )
    )
    window.show()
    app.processEvents()
    try:
        layout = window.overview.layout()
        assert layout.indexOf(window.overview.attention_state) < layout.indexOf(
            window.overview.servers_state
        )
        assert window.overview._metric_columns == 1
        assert window.overview._server_columns == 1
        assert window.overview.attention_state.isVisibleTo(window.overview)
        assert window.stack.currentWidget().horizontalScrollBar().maximum() == 0
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
