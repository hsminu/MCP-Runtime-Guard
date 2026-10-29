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

from .test_ui_icons_style import app as app
from .test_ui_icons_style import assert_key_focus, closing_widgets, resize_widget, show_widget

# Absolute on this OS and ASCII-only; the form never touches the file.
SYNTHETIC_COMMAND = r"C:\synthetic-server.exe" if os.name == "nt" else "/synthetic-server.exe"


def test_default_window_frame_fits_available_screen(app: QApplication) -> None:
    window = GuardWindow()
    window.show()
    app.processEvents()
    assert window.screen().availableGeometry().contains(window.frameGeometry())
    link = window.overview.metric_rows[0].findChild(QPushButton)
    assert link is not None
    link.setFocus()
    QTest.keyClick(link, Qt.Key.Key_Return)
    assert window._page == "servers"
    assert window.breadcrumb.text() == "MCP 서버"
    window.close()


def test_small_window_keeps_navigation_and_runtime_status_available(app: QApplication) -> None:
    window = GuardWindow()
    show_widget(app, window, 320, 400)
    with closing_widgets(window):
        assert window.width() == 320
        assert not window.sidebar.isVisible()
        assert window.menu_button.isVisible()
        assert window.runtime_identity.isVisible()
        assert window.runtime_identity.geometry().right() <= window.width()
        assert set(window._menu_actions) == set(window._nav_buttons) | {"results"}
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
        resize_widget(app, window, 1100, 700)
        assert window.sidebar.isVisible()
        assert not window.menu_button.isVisible()
        assert window._nav_buttons["approvals"].property("selected")
        resize_widget(app, window, 320, 400)
        assert window.width() == 320
        assert window.menu_button.isVisible()
        resize_widget(app, window, 1100, 360)
        assert not window.sidebar.isVisible()
        assert window.menu_button.isVisible()


@pytest.fixture
def server_page(app: QApplication) -> ServerListPage:
    page = ServerListPage()
    page.resize(800, 600)
    page.show()
    page.set_servers(
        Surface(LoadState.READY, (ServerSummary("alpha", "Alpha"), ServerSummary("beta", "Beta")))
    )
    app.processEvents()
    yield page
    page.close()
    app.processEvents()


def test_tab_and_shift_tab_reach_server_filters(server_page: ServerListPage) -> None:
    page = server_page
    page.search.setFocus()
    assert_key_focus(page, page.search, Qt.Key.Key_Tab, page.transport_filter)
    assert_key_focus(page, page.transport_filter, Qt.Key.Key_Tab, page.status_filter)
    assert_key_focus(page, page.status_filter, Qt.Key.Key_Backtab, page.transport_filter)
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
    page.table.selectRow(0)
    page.detail_button.setFocus()
    QTest.keyClick(page.detail_button, Qt.Key.Key_Space)
    assert page._detail_dialog is not None
    assert "alpha" in page._detail_dialog.windowTitle()
    QTest.keyClick(page._detail_dialog, Qt.Key.Key_Escape)
    QTest.qWait(10)
    assert page._detail_dialog is None
    assert page.focusWidget() is page.detail_button
    page.table.selectRow(1)
    assert page._selected_id() == "beta"
    page.table.clearSelection()
    assert page.table.currentRow() == 1
    assert page._selected_id() is None
    assert not page.detail_button.isEnabled()
    QTest.keyClick(page.detail_button, Qt.Key.Key_Space)
    assert page._detail_dialog is None
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


def test_minimum_window_keeps_identity_label_and_status_visible(app: QApplication) -> None:
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
    assert [table.isColumnHidden(i) for i in range(6)] == [False, True, True, False, False, True]
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
    resize_widget(app, window, 1260, 820)
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
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "", True
    )
    QApplication.sendEvent(button, repeated_press)
    assert len(clicks) == 2
    button.close()


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


def test_add_form_keyboard_reaches_fields_and_prepares_only_local_draft(app: QApplication) -> None:
    form = ServerAddForm()
    show_widget(app, form, 780, 580)
    with closing_widgets(form):
        form.display_name.setFocus()
        QTest.keyClicks(form.display_name, "Keyboard Server")
        assert_key_focus(form, form.display_name, Qt.Key.Key_Tab, form.server_id)
        assert form.server_id.text() == "keyboard-server"
        assert_key_focus(form, form.server_id, Qt.Key.Key_Tab, form.transport)
        assert_key_focus(form, form.transport, Qt.Key.Key_Tab, form.command)
        # QTest.keyClicks aborts the process (qasciikey.cpp ASSERT) on any
        # non-ASCII character, so never type a path derived from the checkout.
        QTest.keyClicks(form.command, SYNTHETIC_COMMAND)
        assert_key_focus(form, form.command, Qt.Key.Key_Tab, form.browse_button)
        assert_key_focus(form, form.browse_button, Qt.Key.Key_Tab, form.args_json)
        before = form.args_json.toPlainText()
        assert_key_focus(form, form.args_json, Qt.Key.Key_Tab, form.env_allow)
        assert form.args_json.toPlainText() == before
        assert_key_focus(form, form.env_allow, Qt.Key.Key_Backtab, form.args_json)
        QTest.keyClick(form.args_json, Qt.Key.Key_Tab)
        assert_key_focus(form, form.env_allow, Qt.Key.Key_Tab, form.tier)
        QTest.keyClick(form.tier, Qt.Key.Key_Down)
        assert_key_focus(form, form.tier, Qt.Key.Key_Tab, form.max_label)
        QTest.keyClick(form.max_label, Qt.Key.Key_Down)
        assert form.prepare_button.isEnabled()
        assert_key_focus(form, form.max_label, Qt.Key.Key_Tab, form.preview)
        assert_key_focus(form, form.preview, Qt.Key.Key_Tab, form.prepare_button)
        QTest.keyClick(form.prepare_button, Qt.Key.Key_Return)
        assert form.validated_draft is not None
        assert form.validated_draft.server_id == "keyboard-server"
        assert not form.prepare_button.isEnabled()


def test_add_form_keyboard_switches_transport_and_reaches_http_url(app: QApplication) -> None:
    form = ServerAddForm()
    show_widget(app, form, 780, 580)
    with closing_widgets(form):
        form.transport.setFocus()
        QTest.keyClick(form.transport, Qt.Key.Key_Down)
        assert form.transport.currentData() == "streamable_http"
        assert_key_focus(form, form.transport, Qt.Key.Key_Tab, form.url)
        assert_key_focus(form, form.url, Qt.Key.Key_Backtab, form.transport)
        QTest.keyClick(form.transport, Qt.Key.Key_Up)
        assert form.transport.currentData() == "stdio"
        assert_key_focus(form, form.transport, Qt.Key.Key_Tab, form.command)


def test_approval_keyboard_filters_selection_and_pager(app: QApplication) -> None:
    page = ApprovalListPage()
    show_widget(app, page, 780, 580)
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
    with closing_widgets(page):
        page.refresh()
        assert page.set_page(ApprovalPage(requests[-1], rows, 7), display_now=now)
        page.lane_tabs.setFocus()
        assert_key_focus(page, page.lane_tabs, Qt.Key.Key_Tab, page.search)
        QTest.keyClicks(page.search, "task")
        assert_key_focus(page, page.search, Qt.Key.Key_Tab, page.server_filter)
        assert requests[-1].search == "task"
        assert requests[-1].page == 1
        QTest.keyClicks(page.server_filter, "server-a")
        assert_key_focus(page, page.server_filter, Qt.Key.Key_Tab, page.sort)
        assert requests[-1].server_id == "server-a"
        assert page.set_page(ApprovalPage(requests[-1], rows, 7), display_now=now)
        assert_key_focus(page, page.sort, Qt.Key.Key_Tab, page.table)
        page.table.setCurrentCell(0, 0)
        QTest.keyClick(page.table, Qt.Key.Key_Down)
        assert page.selected_summary is rows[1]
        page.next_button.setFocus()
        QTest.keyClick(page.next_button, Qt.Key.Key_Return)
        assert requests[-1].page == 2
        assert page.selected_summary is None
        assert page.table.rowCount() == 0
        assert not page.next_button.isEnabled()


def test_approval_keyboard_changes_lane_and_sort_without_action_buttons(app: QApplication) -> None:
    page = ApprovalListPage()
    show_widget(app, page, 780, 580)
    requests = []
    page.query_requested.connect(requests.append)
    with closing_widgets(page):
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


def test_call_log_keyboard_row_detail_and_filter_focus(app: QApplication) -> None:
    page = CallLogPage()
    show_widget(app, page, 780, 580)
    rows = (
        PublicCallSummary("call-a", tool_name="alpha"),
        PublicCallSummary("call-b", tool_name="beta"),
    )
    requests = []
    page.detail_requested.connect(requests.append)
    with closing_widgets(page):
        page.set_calls(Surface(LoadState.READY, rows))
        page.search.setFocus()
        assert_key_focus(page, page.search, Qt.Key.Key_Tab, page.server_filter)
        page.table.setFocus()
        page.table.setCurrentCell(0, 0)
        QTest.keyClick(page.table, Qt.Key.Key_Down)
        QTest.keyClick(page.table, Qt.Key.Key_Return)
        QTest.qWait(250)
        assert requests[-1].call_id == "call-b"
        assert page.focusWidget() is page.table
        assert "불러오는 중" in page.detail_panel.message.text()
        previous = requests[-1]
        QTest.keyClick(page.table, Qt.Key.Key_Up)
        QTest.qWait(250)
        assert requests[-1].call_id == "call-a"
        assert requests[-1].token != previous.token
        page.search.setFocus()
        QTest.keyClicks(page.search, "beta")
        assert page.table.rowCount() == 1
        assert page._detail_request is None
        assert page.detail_panel.message.text() == "선택한 호출이 현재 결과에 없음"


def test_approval_detail_keyboard_traverses_both_plain_bodies_and_closes(app: QApplication) -> None:
    host = QWidget()
    layout = QVBoxLayout(host)
    opener = QPushButton("합성 승인 상세")
    layout.addWidget(opener)
    host.show()
    app.processEvents()
    opener.setFocus()
    dialog = ApprovalDetailDialog(host)
    with closing_widgets(dialog, host):
        dialog.show_value(
            ApprovalDetailValue("https://example.invalid/synthetic", b'{"safe":true}'),
            return_focus=opener,
        )
        app.processEvents()
        assert dialog.destination_body.hasFocus()
        assert_key_focus(dialog, dialog.destination_body, Qt.Key.Key_Tab, dialog.arguments_body)
        assert_key_focus(dialog, dialog.arguments_body, Qt.Key.Key_Backtab, dialog.destination_body)
        QTest.keyClick(dialog.destination_body, Qt.Key.Key_Tab)
        QTest.keyClick(dialog.arguments_body, Qt.Key.Key_Tab)
        close_button = dialog.focusWidget()
        assert isinstance(close_button, KeyboardActionButton)
        assert close_button.text() == "닫기"
        assert [button.text() for button in dialog.findChildren(KeyboardActionButton)] == ["닫기"]
        repeated_press = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "", True
        )
        QApplication.sendEvent(close_button, repeated_press)
        assert dialog.isVisible()
        QTest.keyClick(close_button, Qt.Key.Key_Return)
        QTest.qWait(10)
        assert not dialog.isVisible()
        assert host.focusWidget() is opener
        assert dialog.destination_body.toPlainText() == ""
        assert dialog.arguments_body.toPlainText() == ""
