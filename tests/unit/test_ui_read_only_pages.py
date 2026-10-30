"""Qt composition checks for the A32/A33/A35 public read-only pages."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from guard_app.domain.enums import ApprovalStatus, TaskStatus
from guard_app.services.bridge import RegistrationView, ScanState
from guard_app.ui.approvals import ApprovalPage, ApprovalSummary
from guard_app.ui.call_logs import PublicCallDetail, PublicCallSummary
from guard_app.ui.models import LoadState, RuntimeIdentity, RuntimeMode, ServerSummary, Surface
from guard_app.ui.presentation import format_kst
from guard_app.ui.widgets import PlainDetailDialog
from guard_app.ui.window import GuardWindow

from .test_a201_scan_report_ui import bridge as bridge
from .test_call_logs import panel_text
from .test_ui_icons_style import app as app
from .test_ui_icons_style import resize_widget


def test_window_routes_public_approval_and_log_queries(app: QApplication) -> None:
    window = GuardWindow()
    window.show()
    app.processEvents()
    approval_queries = []
    window.approval_query_requested.connect(approval_queries.append)
    window.set_approval_query_connected(True)
    window.navigate("approvals")
    assert window.approval_page.isVisibleTo(window)
    assert not window.log_page.isVisibleTo(window)
    window.approval_page.refresh_button.click()
    query = approval_queries[-1]
    summary = ApprovalSummary(
        approval_id="approval-1",
        task_id="task-1",
        request_id="request-1",
        server_id="server-1",
        destination_summary="합성 수신처",
        status=ApprovalStatus.PENDING,
        created_at=datetime(2026, 10, 12, 5, 0, tzinfo=UTC),
    )
    assert window.set_approval_page(ApprovalPage(query, (summary,), 1))
    window.navigate("approvals", "approval-1")
    # A212: opening the page reads again, and the target is selected from that response.
    assert window.approval_page.selected_summary is None
    assert window.set_approval_page(ApprovalPage(approval_queries[-1], (summary,), 1))
    assert window.approval_page.selected_summary == summary
    # An older response or a failure does not select a later target.
    window.navigate("approvals", "approval-1")
    stale, current = query, approval_queries[-1]
    assert not window.set_approval_page(ApprovalPage(stale, (summary,), 1))
    assert window.set_approval_error(current)
    window.approval_page.refresh()
    assert window.set_approval_page(ApprovalPage(approval_queries[-1], (summary,), 1))
    assert window.approval_page.selected_summary is None
    log_requests = []
    window.call_log_detail_requested.connect(log_requests.append)
    call = PublicCallSummary(
        call_id="call-1", server_id="server-1", tool_name="search", current_status="UNKNOWN_OUTCOME"
    )
    window.set_call_log_snapshot(Surface(LoadState.READY, (call,)))
    window.navigate("logs", "call-1")
    assert window.log_page.isVisibleTo(window)
    assert window.stack.currentWidget() is window.log_page
    QTest.qWait(250)
    request = log_requests[-1]
    window.set_call_log_detail(request, Surface(LoadState.READY, PublicCallDetail(call)))
    dialog = window.log_page.detail_panel
    assert dialog is not None
    assert "수신측 확인 필요" in panel_text(dialog)
    window.close()


def test_server_scan_reply_is_bound_to_current_detail_opening(app: QApplication, bridge) -> None:
    backend, requested = bridge
    window = GuardWindow()
    window.server_page.attach_activation(backend)
    window.set_server_snapshot(Surface(LoadState.READY, (ServerSummary("server-1", "합성 서버"),)))
    window.navigate("servers", "server-1")
    window.show()
    app.processEvents()
    window.server_page.detail_button.click()
    request, kw = requested[-1]
    command = kw["command_id"]
    assert request.server_id == "server-1"
    state = ScanState("server-1", run="done")
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", "wrong", server_id="server-1", scan=state)
    )
    detail = window.server_page._detail_dialog
    assert detail is not None
    assert "검사 결과 확인 중" in panel_text(detail.scan_report)
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", command, server_id="server-1", scan=state)
    )
    assert "검사 끝남" in panel_text(detail.scan_report)
    detail.close()
    app.processEvents()
    backend.scan_changed.emit(ScanState("server-1", run="running"))
    assert "검사 끝남" in panel_text(detail.scan_report)
    window.server_page.detail_button.click()
    new_command = requested[-1][1]["command_id"]
    assert new_command != command
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", command, server_id="server-1", scan=state)
    )
    assert "검사 결과 확인 중" in panel_text(detail.scan_report)
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", new_command, server_id="server-1", scan=state)
    )
    assert "검사 끝남" in panel_text(detail.scan_report)
    detail.close()
    window.close()


def test_repeated_server_details_release_dialogs_and_reject_old_scan_reply(
    app: QApplication,
    bridge,
) -> None:
    backend, requested = bridge
    window = GuardWindow()
    window.server_page.attach_activation(backend)
    window.set_server_snapshot(Surface(LoadState.READY, (ServerSummary("server-1", "합성 서버"),)))
    window.navigate("servers", "server-1")
    window.show()
    app.processEvents()
    for _ in range(100):
        window.server_page.detail_button.click()
        detail = window.server_page._detail_dialog
        assert detail is not None
        detail.close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len({kw["command_id"] for _, kw in requested}) == 100
    assert len(window.server_page.findChildren(QDialog)) == 1
    window.server_page.detail_button.click()
    reused = window.server_page._detail_dialog
    assert reused is not None
    state = ScanState("server-1", run="done")
    backend.registration_changed.emit(
        RegistrationView(
            "SCAN_STATUS", requested[0][1]["command_id"], server_id="server-1", scan=state
        )
    )
    assert "검사 결과 확인 중" in panel_text(reused.scan_report)
    backend.registration_changed.emit(
        RegistrationView(
            "SCAN_STATUS", requested[-1][1]["command_id"], server_id="server-1", scan=state
        )
    )
    assert "검사 끝남" in panel_text(reused.scan_report)
    reused.close()
    window.set_server_snapshot(
        Surface(
            LoadState.READY,
            (
                ServerSummary("server-1", "합성 서버"),
                ServerSummary("server-2", "새 서버", max_label="INTERNAL"),
            ),
        )
    )
    window.navigate("servers", "server-2")
    window.server_page.detail_button.click()
    assert window.server_page._detail_dialog is reused
    assert reused.server_id == "server-2"
    assert reused._display_value.text() == "새 서버"
    assert "INTERNAL" in reused.max_label_badge.value.text()
    backend.registration_changed.emit(
        RegistrationView(
            "SCAN_STATUS", requested[-2][1]["command_id"], server_id="server-1", scan=state
        )
    )
    assert "검사 결과 확인 중" in panel_text(reused.scan_report)
    reused.close()
    resize_widget(app, window, 360, 360)
    window.server_page.detail_button.click()
    assert window.server_page._detail_dialog is reused
    assert reused.width() <= max(240, window.width() - 24)
    assert reused.height() <= max(200, window.height() - 24)
    reused.close()
    window.close()


def test_product_window_without_approval_source_never_waits_forever(app: QApplication) -> None:
    window = GuardWindow()
    window.show()
    approval_queries = []
    window.approval_query_requested.connect(approval_queries.append)
    window.navigate("approvals")
    page = window.approval_page
    assert not page.source_connected
    assert not page.refresh_button.isEnabled()
    assert not page.search.isEnabled()
    page.refresh()
    page.lane_tabs.setCurrentIndex(1)
    assert approval_queries == []
    assert page.state is LoadState.UNAVAILABLE
    assert "아직 연결되지 않음" in page.state_message.message.text()
    assert "불러오는 중" not in page.state_message.message.text()
    window.set_approval_query_connected(True)
    page.refresh()
    assert len(approval_queries) == 1
    assert page.state is LoadState.LOADING
    window.set_approval_query_connected(False)
    assert page.state is LoadState.UNAVAILABLE
    assert page.current_query is None
    window.close()


def test_runtime_mode_is_unconfigured_until_trusted_bootstrap_sets_it(app) -> None:
    window = GuardWindow()
    assert window.runtime_identity.text() == "모델: 미설정"
    window.set_runtime_identity(RuntimeIdentity(model_mode=RuntimeMode.MOCK))
    assert window.runtime_identity.text() == "모델: mock"
    window.set_runtime_identity(RuntimeIdentity(model_mode=RuntimeMode.CODEX))
    assert window.runtime_identity.text() == "모델: Codex"
    window.navigate("runs")
    assert "상태 미확인" in window.task_status_card.status_label.text()
    window.set_task_status(TaskStatus.UNKNOWN_OUTCOME)
    assert "결과 불명" in window.task_status_card.status_label.text()
    assert "수신측" in window.task_status_card.detail_label.text()
    assert window.task_status_card.findChildren(QPushButton) == []
    window.navigate("server_add")
    assert window.breadcrumb.text() == "MCP 서버 추가"
    assert "Registry 등록은 아직 연결되지 않아" in window.server_add_form.status.text()
    assert window.server_add_form.validated_draft is None


def test_registry_list_uses_its_own_public_snapshot_and_filters(app) -> None:
    window = GuardWindow()
    public = ServerSummary(
        "public-docs", "공개 문서", "PUBLIC", "general_external", "active", "connected", "stdio"
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
    assert window.server_page.table.rowCount() == 0
    assert "조회 기능 미연결" in window.server_page.state_message.message.text()
    window.set_server_snapshot(Surface(LoadState.READY, (public, pending)))
    assert window.server_page.table.rowCount() == 2
    assert window.server_page.table.item(0, 3).text() == "공개"
    assert window.server_page.table.item(0, 3).toolTip() == "PUBLIC"
    assert window.server_page.table.item(1, 4).text() == "등록 검토 중"
    assert window.server_page.table.item(1, 4).toolTip() == "PENDING"
    assert window.server_page.table.item(1, 5).text() == "연결 안 됨"
    assert window.server_page.table.item(0, 0).toolTip() == ""
    window.server_page.search.setText("pending-send")
    assert window.server_page.table.rowCount() == 1
    window.server_page.transport_filter.setCurrentIndex(1)
    assert window.server_page.table.rowCount() == 0
    assert "일치하는 서버가 없음" in window.server_page.state_message.message.text()
    window.server_page.transport_filter.setCurrentIndex(0)
    window.navigate("servers", "pending-send")
    assert window.server_page._selected_id() == "pending-send"


def test_registry_detail_is_plain_text_and_stale_data_is_labeled(app) -> None:
    window = GuardWindow()
    server = ServerSummary(
        "safe-id", '<img src="https://example.invalid/secret">', None, None, "active", None, "stdio"
    )
    window.set_server_snapshot(Surface(LoadState.STALE, (server,)))
    assert window.server_page.state_message.isVisibleTo(window.server_page)
    assert "이전 조회 결과" in window.server_page.state_message.message.text()
    window.navigate("servers", "safe-id")
    window.server_page.detail_button.click()
    dialog = window.server_page._detail_dialog
    assert dialog is not None
    assert '<img src="https://example.invalid/secret">' in [
        label.text() for label in dialog.findChildren(QLabel)
    ]
    assert "미지정 · 미제공" in [label.text() for label in dialog.findChildren(QLabel)]
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
