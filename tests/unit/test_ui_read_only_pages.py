"""Qt composition checks for the A32/A33/A35 public read-only pages."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from guard_app.domain.enums import ApprovalStatus
from guard_app.services.bridge import RegistrationView, ScanState
from guard_app.ui.approvals import ApprovalPage, ApprovalSummary
from guard_app.ui.call_logs import PublicCallDetail, PublicCallSummary
from guard_app.ui.models import LoadState, ServerSummary, Surface
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
