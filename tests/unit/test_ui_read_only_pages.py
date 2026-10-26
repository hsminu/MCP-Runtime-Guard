"""Qt composition checks for the A32/A33/A35 public read-only pages."""

from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from guard_app.domain.enums import ApprovalStatus
from guard_app.ui.approvals import ApprovalPage, ApprovalSummary
from guard_app.ui.call_logs import PublicCallDetail, PublicCallSummary
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.scan_status import ScanStatusProjection
from guard_app.ui.window import GuardWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


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
    assert window.approval_page.selected_summary == summary

    log_requests = []
    window.call_log_detail_requested.connect(log_requests.append)
    call = PublicCallSummary(
        call_id="call-1",
        server_id="server-1",
        tool_name="search",
        current_status="UNKNOWN_OUTCOME",
    )
    window.set_call_log_snapshot(Surface(LoadState.READY, (call,)))
    window.navigate("logs", "call-1")
    assert window.log_page.isVisibleTo(window)
    assert window.log_page.detail_button.isEnabled()
    window.log_page.detail_button.click()
    request = log_requests[-1]
    window.set_call_log_detail(request, Surface(LoadState.READY, PublicCallDetail(call)))
    dialog = window.log_page._detail_dialog
    assert dialog is not None
    assert "수신측 확인 필요" in dialog.body.toPlainText()
    dialog.close()
    window.close()


def test_server_scan_reply_is_bound_to_current_detail_opening(
    app: QApplication,
) -> None:
    window = GuardWindow()
    window.set_server_snapshot(Surface(LoadState.READY, (ServerSummary("server-1", "합성 서버"),)))
    requested = []
    window.scan_snapshot_requested.connect(
        lambda server_id, token: requested.append((server_id, token))
    )
    window.navigate("servers", "server-1")
    window.show()
    app.processEvents()
    window.server_page.detail_button.click()
    server_id, token = requested[-1]
    assert server_id == "server-1"
    snapshot = Surface(
        LoadState.READY,
        ScanStatusProjection(registry_status="pending", scan_state="completed"),
    )
    assert not window.set_scan_snapshot(server_id, token + 1, snapshot)
    assert window.set_scan_snapshot(server_id, token, snapshot)
    detail = window.server_page._detail_dialog
    assert detail is not None
    assert "검사 실행 완료" in detail.scan_status_card.scan_state.text()
    assert "PENDING" in detail.scan_status_card.registry_status.text()
    detail.close()
    app.processEvents()
    assert not window.set_scan_snapshot(server_id, token, snapshot)

    window.server_page.detail_button.click()
    _, new_token = requested[-1]
    assert new_token != token
    assert not window.set_scan_snapshot(server_id, token, snapshot)
    assert window.set_scan_snapshot(server_id, new_token, snapshot)
    detail = window.server_page._detail_dialog
    assert detail is not None
    detail.close()
    window.close()


def test_repeated_server_details_release_dialogs_and_reject_old_scan_reply(
    app: QApplication,
) -> None:
    window = GuardWindow()
    window.set_server_snapshot(Surface(LoadState.READY, (ServerSummary("server-1", "합성 서버"),)))
    requested: list[tuple[str, int]] = []
    window.scan_snapshot_requested.connect(
        lambda server_id, token: requested.append((server_id, token))
    )
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
    assert len({token for _, token in requested}) == 100
    assert len(window.server_page.findChildren(QDialog)) == 1
    window.server_page.detail_button.click()
    assert window.server_page._detail_dialog is not None
    snapshot = Surface(LoadState.READY, ScanStatusProjection(scan_state="completed"))
    assert not window.set_scan_snapshot("server-1", requested[0][1], snapshot)
    assert window.set_scan_snapshot("server-1", requested[-1][1], snapshot)
    reused = window.server_page._detail_dialog
    assert reused.scan_status_card.scan_state.text().startswith("검사 실행 완료")
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
    assert reused.scan_status_card.scan_state.text() == "미제공"
    assert not window.set_scan_snapshot("server-1", requested[-2][1], snapshot)
    reused.close()
    window.resize(360, 360)
    app.processEvents()
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
    assert "아직 연결되지 않았어" in page.state_message.message.text()
    assert "불러오는 중" not in page.state_message.message.text()
    window.set_approval_query_connected(True)
    page.refresh()
    assert len(approval_queries) == 1
    assert page.state is LoadState.LOADING
    window.set_approval_query_connected(False)
    assert page.state is LoadState.UNAVAILABLE
    assert page.current_query is None
    window.close()
