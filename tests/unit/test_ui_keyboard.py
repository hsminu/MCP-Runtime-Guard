"""Real Qt key events for the currently available read-only UI paths."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from guard_app.domain.enums import ApprovalStatus
from guard_app.ui.approval_detail import ApprovalDetailDialog, ApprovalDetailValue
from guard_app.ui.approvals import ApprovalListPage, ApprovalPage, ApprovalSummary
from guard_app.ui.call_logs import CallLogPage, PublicCallSummary
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.server_add import ServerAddForm
from guard_app.ui.servers import ServerListPage
from guard_app.ui.widgets import KeyboardActionButton, PlainDetailDialog
from guard_app.ui.window import GuardWindow

# Absolute on this OS and ASCII-only; the form never touches the file.
SYNTHETIC_COMMAND = r"C:\synthetic-server.exe" if os.name == "nt" else "/synthetic-server.exe"


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def test_default_window_frame_fits_available_screen(app: QApplication) -> None:
    window = GuardWindow()
    window.show()
    app.processEvents()
    assert window.screen().availableGeometry().contains(window.frameGeometry())
    window.close()


def test_small_window_keeps_navigation_and_runtime_status_available(
    app: QApplication,
) -> None:
    window = GuardWindow()
    window.resize(320, 400)
    window.show()
    app.processEvents()
    try:
        assert window.width() == 320
        assert not window.sidebar.isVisible()
        assert window.menu_button.isVisible()
        assert window.runtime_identity.isVisible()
        assert window.runtime_identity.geometry().right() <= window.width()
        assert set(window._menu_actions) == set(window._nav_buttons)

        window.menu_button.setFocus()
        QTest.keyClick(window.menu_button, Qt.Key.Key_Return)
        app.processEvents()
        assert window._nav_menu.isVisible()
        window._nav_menu.setActiveAction(window._menu_actions["approvals"])
        QTest.keyClick(window._nav_menu, Qt.Key.Key_Return)
        app.processEvents()
        assert window._page == "approvals"
        assert window.width() == 320
        assert window.breadcrumb.text() == "승인 대기"

        window.resize(1100, 700)
        app.processEvents()
        assert window.sidebar.isVisible()
        assert not window.menu_button.isVisible()
        assert window._nav_buttons["approvals"].property("selected")
        window.resize(320, 400)
        app.processEvents()
        assert window.width() == 320
        assert window.menu_button.isVisible()
        window.resize(1100, 360)
        app.processEvents()
        assert not window.sidebar.isVisible()
        assert window.menu_button.isVisible()
    finally:
        window.close()


@pytest.fixture
def server_page(app: QApplication) -> ServerListPage:
    page = ServerListPage()
    page.resize(800, 600)
    page.show()
    page.set_servers(
        Surface(
            LoadState.READY,
            (
                ServerSummary("alpha", "Alpha"),
                ServerSummary("beta", "Beta"),
            ),
        )
    )
    app.processEvents()
    yield page
    page.close()
    app.processEvents()


def test_tab_and_shift_tab_reach_server_filters(server_page: ServerListPage) -> None:
    page = server_page
    page.search.setFocus()
    QTest.keyClick(page.search, Qt.Key.Key_Tab)
    assert page.focusWidget() is page.transport_filter
    QTest.keyClick(page.transport_filter, Qt.Key.Key_Tab)
    assert page.focusWidget() is page.status_filter
    QTest.keyClick(page.status_filter, Qt.Key.Key_Backtab)
    assert page.focusWidget() is page.transport_filter


def test_overview_metric_enter_navigates_to_server_page(app: QApplication) -> None:
    window = GuardWindow()
    window.show()
    app.processEvents()
    link = window.overview.metric_rows[0].findChild(QPushButton)
    assert link is not None
    link.setFocus()
    QTest.keyClick(link, Qt.Key.Key_Return)
    assert window._page == "servers"
    assert window.breadcrumb.text() == "MCP 서버"
    window.close()


def test_minimum_window_keeps_identity_label_and_status_visible(
    app: QApplication,
) -> None:
    window = GuardWindow()
    window.resize(780, 580)
    window.set_server_snapshot(
        Surface(
            LoadState.READY,
            (
                ServerSummary(
                    "beta-server",
                    "Beta Example",
                    "CONFIDENTIAL",
                    "internal",
                    "pending",
                    "disconnected",
                    "streamable_http",
                ),
            ),
        )
    )
    window.navigate("servers")
    window.show()
    app.processEvents()
    app.processEvents()
    table = window.server_page.table
    assert [table.isColumnHidden(i) for i in range(6)] == [
        False,
        True,
        True,
        False,
        False,
        True,
    ]
    assert table.horizontalHeaderItem(0).text() == "서버 ID"
    assert table.columnViewportPosition(4) + table.columnWidth(4) <= (table.viewport().width())
    assert table.horizontalScrollBar().maximum() == 0

    table.selectRow(0)
    QTest.keyClick(table, Qt.Key.Key_Return)
    detail = window.server_page._detail_dialog
    assert detail is not None
    labels = {label.text() for label in detail.findChildren(QLabel)}
    assert "Streamable HTTP" in labels
    assert "내부 · internal" in labels
    assert "연결 안 됨" in labels
    detail.close()

    window.resize(1260, 820)
    app.processEvents()
    app.processEvents()
    assert not any(table.isColumnHidden(i) for i in range(6))
    assert table.horizontalHeaderItem(0).text() == "서버 ID / 표시 이름"
    window.close()


def test_action_button_ignores_repeated_return(app: QApplication) -> None:
    button = KeyboardActionButton("합성 동작")
    clicks: list[None] = []
    button.clicked.connect(lambda: clicks.append(None))
    button.show()
    app.processEvents()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Return)
    QTest.keyClick(button, Qt.Key.Key_Enter)
    assert len(clicks) == 2

    repeated_press = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
        "",
        True,
    )
    QApplication.sendEvent(button, repeated_press)
    assert len(clicks) == 2
    button.close()


def test_table_arrow_enter_escape_keeps_selection_and_returns_focus(
    server_page: ServerListPage,
) -> None:
    page = server_page
    page.table.setFocus()
    page.table.setCurrentCell(0, 0)
    QTest.keyClick(page.table, Qt.Key.Key_Down)
    assert page._selected_id() == "beta"

    QTest.keyClick(page.table, Qt.Key.Key_Return)
    assert page._detail_dialog is not None
    assert "beta" in page._detail_dialog.windowTitle()
    QTest.keyClick(page._detail_dialog, Qt.Key.Key_Escape)
    QTest.qWait(10)
    assert page._detail_dialog is None
    assert page.focusWidget() is page.table
    assert page._selected_id() == "beta"


def test_space_opens_selected_detail_and_escape_returns_to_button(
    server_page: ServerListPage,
) -> None:
    page = server_page
    page.table.selectRow(0)
    page.detail_button.setFocus()
    QTest.keyClick(page.detail_button, Qt.Key.Key_Space)
    assert page._detail_dialog is not None
    assert "alpha" in page._detail_dialog.windowTitle()
    QTest.keyClick(page._detail_dialog, Qt.Key.Key_Escape)
    QTest.qWait(10)
    assert page._detail_dialog is None
    assert page.focusWidget() is page.detail_button


def test_cleared_selection_cannot_open_stale_current_row(
    server_page: ServerListPage,
) -> None:
    page = server_page
    page.table.selectRow(1)
    assert page._selected_id() == "beta"
    page.table.clearSelection()
    assert page.table.currentRow() == 1
    assert page._selected_id() is None
    assert not page.detail_button.isEnabled()
    QTest.keyClick(page.detail_button, Qt.Key.Key_Space)
    assert page._detail_dialog is None


def test_keyboard_filter_preserves_visible_selection_and_clears_missing_row(
    server_page: ServerListPage,
) -> None:
    page = server_page
    page.table.selectRow(1)
    page.search.setFocus()
    QTest.keyClicks(page.search, "be")
    assert page.table.rowCount() == 1
    assert page._selected_id() == "beta"
    assert page.detail_button.isEnabled()

    QTest.keyClicks(page.search, "x")
    assert page.table.rowCount() == 0
    assert page._selected_id() is None
    assert not page.detail_button.isEnabled()
    assert "일치하는 서버가 없음" in page.state_message.message.text()


def test_plain_detail_escape_returns_focus_to_opener(app: QApplication) -> None:
    host = QWidget()
    layout = QVBoxLayout(host)
    opener = QPushButton("상세 열기")
    layout.addWidget(opener)
    host.show()
    app.processEvents()
    opener.setFocus()

    detail = PlainDetailDialog(host)
    detail.show_detail(title="합성 상세", text="literal <b>text</b>", return_focus=opener)
    app.processEvents()
    assert detail.body.hasFocus()
    assert detail.body.toPlainText() == "literal <b>text</b>"
    QTest.keyClick(detail, Qt.Key.Key_Escape)
    QTest.qWait(10)
    assert not detail.isVisible()
    assert host.focusWidget() is opener
    host.close()


def test_plain_detail_tab_reaches_close_and_return_closes(app: QApplication) -> None:
    detail = PlainDetailDialog()
    detail.show_detail(title="합성 상세", text="긴 상세")
    app.processEvents()
    assert detail.body.hasFocus()
    QTest.keyClick(detail.body, Qt.Key.Key_Tab)
    close_button = detail.focusWidget()
    assert isinstance(close_button, KeyboardActionButton)
    assert close_button.text() == "닫기"
    QTest.keyClick(close_button, Qt.Key.Key_Return)
    assert not detail.isVisible()


def test_add_form_keyboard_reaches_fields_and_prepares_only_local_draft(
    app: QApplication,
) -> None:
    form = ServerAddForm()
    form.resize(780, 580)
    form.show()
    app.processEvents()
    try:
        form.display_name.setFocus()
        QTest.keyClicks(form.display_name, "Keyboard Server")
        QTest.keyClick(form.display_name, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.server_id
        assert form.server_id.text() == "keyboard-server"
        QTest.keyClick(form.server_id, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.transport
        QTest.keyClick(form.transport, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.command
        # QTest.keyClicks aborts the process (qasciikey.cpp ASSERT) on any
        # non-ASCII character, so never type a path derived from the checkout.
        QTest.keyClicks(form.command, SYNTHETIC_COMMAND)
        QTest.keyClick(form.command, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.browse_button
        QTest.keyClick(form.browse_button, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.args_json
        before = form.args_json.toPlainText()
        QTest.keyClick(form.args_json, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.env_allow
        assert form.args_json.toPlainText() == before
        QTest.keyClick(form.env_allow, Qt.Key.Key_Backtab)
        assert form.focusWidget() is form.args_json
        QTest.keyClick(form.args_json, Qt.Key.Key_Tab)
        QTest.keyClick(form.env_allow, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.tier
        QTest.keyClick(form.tier, Qt.Key.Key_Down)
        QTest.keyClick(form.tier, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.max_label
        QTest.keyClick(form.max_label, Qt.Key.Key_Down)
        assert form.prepare_button.isEnabled()
        QTest.keyClick(form.max_label, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.preview
        QTest.keyClick(form.preview, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.prepare_button
        QTest.keyClick(form.prepare_button, Qt.Key.Key_Return)
        assert form.validated_draft is not None
        assert form.validated_draft.server_id == "keyboard-server"
        assert not form.prepare_button.isEnabled()
    finally:
        form.close()


def test_add_form_keyboard_switches_transport_and_reaches_http_url(
    app: QApplication,
) -> None:
    form = ServerAddForm()
    form.resize(780, 580)
    form.show()
    app.processEvents()
    try:
        form.transport.setFocus()
        QTest.keyClick(form.transport, Qt.Key.Key_Down)
        assert form.transport.currentData() == "streamable_http"
        QTest.keyClick(form.transport, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.url
        QTest.keyClick(form.url, Qt.Key.Key_Backtab)
        assert form.focusWidget() is form.transport
        QTest.keyClick(form.transport, Qt.Key.Key_Up)
        assert form.transport.currentData() == "stdio"
        QTest.keyClick(form.transport, Qt.Key.Key_Tab)
        assert form.focusWidget() is form.command
    finally:
        form.close()


def test_approval_keyboard_filters_selection_and_pager(app: QApplication) -> None:
    page = ApprovalListPage()
    page.resize(780, 580)
    page.show()
    app.processEvents()
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)
    rows = tuple(
        ApprovalSummary(
            f"approval-{number:03}",
            f"task-{number:03}",
            f"request-{number:03}",
            "server-a",
            "합성 대상",
            ApprovalStatus.PENDING,
            now - timedelta(minutes=number),
        )
        for number in range(6, 0, -1)
    )
    requests = []
    page.query_requested.connect(requests.append)
    try:
        page.refresh()
        assert page.set_page(ApprovalPage(requests[-1], rows, 7), display_now=now)
        page.lane_tabs.setFocus()
        QTest.keyClick(page.lane_tabs, Qt.Key.Key_Tab)
        assert page.focusWidget() is page.search
        QTest.keyClicks(page.search, "task")
        QTest.keyClick(page.search, Qt.Key.Key_Tab)
        assert page.focusWidget() is page.server_filter
        assert requests[-1].search == "task"
        assert requests[-1].page == 1
        QTest.keyClicks(page.server_filter, "server-a")
        QTest.keyClick(page.server_filter, Qt.Key.Key_Tab)
        assert page.focusWidget() is page.sort
        assert requests[-1].server_id == "server-a"
        assert page.set_page(ApprovalPage(requests[-1], rows, 7), display_now=now)
        QTest.keyClick(page.sort, Qt.Key.Key_Tab)
        assert page.focusWidget() is page.table
        page.table.setCurrentCell(0, 0)
        QTest.keyClick(page.table, Qt.Key.Key_Down)
        assert page.selected_summary is rows[1]
        page.next_button.setFocus()
        QTest.keyClick(page.next_button, Qt.Key.Key_Return)
        assert requests[-1].page == 2
        assert page.selected_summary is None
        assert page.table.rowCount() == 0
        assert not page.next_button.isEnabled()
    finally:
        page.close()


def test_approval_keyboard_changes_lane_and_sort_without_action_buttons(
    app: QApplication,
) -> None:
    page = ApprovalListPage()
    page.resize(780, 580)
    page.show()
    app.processEvents()
    requests = []
    page.query_requested.connect(requests.append)
    try:
        page.lane_tabs.setFocus()
        QTest.keyClick(page.lane_tabs, Qt.Key.Key_Right)
        assert page.lane_tabs.currentIndex() == 1
        assert requests[-1].lane.value == "history"
        page.sort.setFocus()
        QTest.keyClick(page.sort, Qt.Key.Key_Down)
        assert page.sort.currentIndex() == 1
        assert requests[-1].sort.value == "newest"
        assert requests[-1].lane.value == "history"
        button_texts = {button.text() for button in page.findChildren(KeyboardActionButton)}
        assert "승인" not in button_texts
        assert "거절" not in button_texts
    finally:
        page.close()


def test_call_log_keyboard_row_detail_and_filter_focus(app: QApplication) -> None:
    page = CallLogPage()
    page.resize(780, 580)
    page.show()
    app.processEvents()
    rows = (
        PublicCallSummary("call-a", tool_name="alpha"),
        PublicCallSummary("call-b", tool_name="beta"),
    )
    requests = []
    page.detail_requested.connect(requests.append)
    try:
        page.set_calls(Surface(LoadState.READY, rows))
        page.search.setFocus()
        QTest.keyClick(page.search, Qt.Key.Key_Tab)
        assert page.focusWidget() is page.table
        page.table.setCurrentCell(0, 0)
        QTest.keyClick(page.table, Qt.Key.Key_Down)
        QTest.keyClick(page.table, Qt.Key.Key_Return)
        assert requests[-1].call_id == "call-b"
        detail = page._detail_dialog
        app.processEvents()
        assert detail is not None and detail.body.hasFocus()
        QTest.keyClick(detail.body, Qt.Key.Key_Escape)
        QTest.qWait(10)
        assert page._detail_dialog is None
        assert page.focusWidget() is page.table

        page.detail_button.setFocus()
        QTest.keyClick(page.detail_button, Qt.Key.Key_Space)
        assert requests[-1].call_id == "call-b"
        detail = page._detail_dialog
        assert detail is not None
        QTest.keyClick(detail.body, Qt.Key.Key_Escape)
        QTest.qWait(10)
        assert page.focusWidget() is page.detail_button

        page.search.setFocus()
        QTest.keyClicks(page.search, "alpha")
        assert page.table.rowCount() == 1
        assert not page.detail_button.isEnabled()
        assert page._detail_dialog is None
    finally:
        page.close()


def test_approval_detail_keyboard_traverses_both_plain_bodies_and_closes(
    app: QApplication,
) -> None:
    host = QWidget()
    layout = QVBoxLayout(host)
    opener = QPushButton("합성 승인 상세")
    layout.addWidget(opener)
    host.show()
    app.processEvents()
    opener.setFocus()
    dialog = ApprovalDetailDialog(host)
    try:
        dialog.show_value(
            ApprovalDetailValue("https://example.invalid/synthetic", b'{"safe":true}'),
            return_focus=opener,
        )
        app.processEvents()
        assert dialog.destination_body.hasFocus()
        QTest.keyClick(dialog.destination_body, Qt.Key.Key_Tab)
        assert dialog.focusWidget() is dialog.arguments_body
        QTest.keyClick(dialog.arguments_body, Qt.Key.Key_Backtab)
        assert dialog.focusWidget() is dialog.destination_body
        QTest.keyClick(dialog.destination_body, Qt.Key.Key_Tab)
        QTest.keyClick(dialog.arguments_body, Qt.Key.Key_Tab)
        close_button = dialog.focusWidget()
        assert isinstance(close_button, KeyboardActionButton)
        assert close_button.text() == "닫기"
        assert [button.text() for button in dialog.findChildren(KeyboardActionButton)] == ["닫기"]
        repeated_press = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
            "",
            True,
        )
        QApplication.sendEvent(close_button, repeated_press)
        assert dialog.isVisible()
        QTest.keyClick(close_button, Qt.Key.Key_Return)
        QTest.qWait(10)
        assert not dialog.isVisible()
        assert host.focusWidget() is opener
        assert dialog.destination_body.toPlainText() == ""
        assert dialog.arguments_body.toPlainText() == ""
    finally:
        dialog.close()
        host.close()
