"""Real Qt key events for the currently available read-only UI paths."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.servers import ServerListPage
from guard_app.ui.widgets import KeyboardActionButton, PlainDetailDialog
from guard_app.ui.window import GuardWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


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
    link = window.overview.metric_cards[0].findChild(QPushButton)
    assert link is not None
    link.setFocus()
    QTest.keyClick(link, Qt.Key.Key_Return)
    assert window._page == "servers"
    assert window.breadcrumb.text() == "Workspace / MCP 서버"
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
    assert table.columnViewportPosition(4) + table.columnWidth(4) <= (
        table.viewport().width()
    )
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
    assert "일치하는 서버가 없어" in page.state_message.message.text()


def test_plain_detail_escape_returns_focus_to_opener(app: QApplication) -> None:
    host = QWidget()
    layout = QVBoxLayout(host)
    opener = QPushButton("상세 열기")
    layout.addWidget(opener)
    host.show()
    app.processEvents()
    opener.setFocus()

    detail = PlainDetailDialog(host)
    detail.show_detail(
        title="합성 상세", text="literal <b>text</b>", return_focus=opener
    )
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
