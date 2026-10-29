"""Small and large viewport checks for locally rendered, read-only UI."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QFormLayout, QLabel, QScrollArea, QWidget

from guard_app.ui.approval_detail import ApprovalDetailDialog, ApprovalDetailValue
from guard_app.ui.models import (
    AttentionItem,
    LoadState,
    OverviewSnapshot,
    RecentCall,
    ServerSummary,
    Surface,
)
from guard_app.ui.server_add import ServerAddForm
from guard_app.ui.servers import ServerDetailDialog, ServerListPage
from guard_app.ui.widgets import PlainDetailDialog
from guard_app.ui.window import GuardWindow

from .test_ui_icons_style import app as app
from .test_ui_icons_style import closing_widgets, resize_widget, show_widget


def test_server_form_reflows_without_horizontal_page_scroll(app: QApplication) -> None:
    form = ServerAddForm()
    show_widget(app, form, 320, 420)
    assert form.width() == 320
    scroll = form.findChild(QScrollArea, "serverAddScroll")
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() > 0
    assert all(
        layout.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows for layout in form._forms
    )
    form.server_id.setText("synthetic-id")
    form.display_name.setText("Synthetic")
    form.command.setText(os.path.abspath("synthetic-executable.exe"))
    form.tier.setCurrentIndex(form.tier.findData("internal"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    assert form.prepare_button.isEnabled()
    form.prepare_button.setFocus()
    app.processEvents()
    assert form.prepare_button.hasFocus()
    resize_widget(app, form, 980, 760)
    assert all(
        layout.rowWrapPolicy() == QFormLayout.RowWrapPolicy.DontWrapRows for layout in form._forms
    )
    resize_widget(app, form, 320, 420)
    assert scroll.horizontalScrollBar().maximum() == 0
    form.close()


def test_server_list_keeps_identity_and_status_at_small_width(app: QApplication) -> None:
    page = ServerListPage()
    page.resize(320, 480)
    page.show()
    page.set_servers(
        Surface(
            LoadState.READY, (ServerSummary("long-server-id-with-identifying-suffix", "Synthetic"),)
        )
    )
    app.processEvents()
    assert page.width() == 320
    assert page._choices.direction() == QBoxLayout.Direction.TopToBottom, (
        page.width(),
        page.minimumSizeHint().width(),
        sorted(
            ((w.objectName(), w.minimumSizeHint().width()) for w in page.findChildren(QWidget)),
            key=lambda row: row[1],
            reverse=True,
        )[:12],
    )
    assert not page.table.isColumnHidden(0)
    assert not page.table.isColumnHidden(4)
    assert page.table.isColumnHidden(3)
    assert page.table.horizontalScrollBar().maximum() == 0
    page.table.selectRow(0)
    assert page.detail_button.isEnabled()
    page.detail_button.click()
    detail = page._detail_dialog
    assert detail is not None
    assert detail.width() <= page.width()
    detail.close()
    app.processEvents()
    page.activateWindow()
    page.table.setFocus()
    app.processEvents()
    QTest.keyClick(page.table, Qt.Key.Key_Tab)
    assert page.detail_button.hasFocus()
    resize_widget(app, page, 1100, 740)
    assert page._choices.direction() == QBoxLayout.Direction.LeftToRight
    assert all(not page.table.isColumnHidden(column) for column in range(6))
    page.close()


def test_detail_dialogs_fit_small_owner_and_keep_complete_values(app: QApplication) -> None:
    owner = QWidget()
    show_widget(app, owner, 320, 360)
    text = "synthetic-start\n" + ("synthetic-line\n" * 80) + "synthetic-end"
    plain = PlainDetailDialog(owner)
    plain.show_detail(title="합성 상세", text=text)
    app.processEvents()
    assert plain.width() <= owner.width()
    assert plain.height() <= owner.height()
    assert plain.body.verticalScrollBar().maximum() > 0
    assert plain.body.toPlainText().endswith("synthetic-end")
    plain.close()
    approval = ApprovalDetailDialog(owner)
    approval.show_value(ApprovalDetailValue("synthetic-destination", text.encode("utf-8")))
    app.processEvents()
    assert approval.width() <= owner.width()
    assert approval.height() <= owner.height()
    assert approval.arguments_body.verticalScrollBar().maximum() > 0
    assert approval.arguments_body.toPlainText().endswith("synthetic-end")
    approval.close()
    assert not approval.arguments_body.toPlainText()
    server_id = "synthetic-" + "s" * 100
    server = ServerDetailDialog(ServerSummary(server_id, "d" * 100), owner)
    server.open()
    app.processEvents()
    assert server.width() <= owner.width()
    assert server.height() <= owner.height()
    server_scroll = server.findChild(QScrollArea)
    assert server_scroll is not None
    assert server_scroll.horizontalScrollBar().maximum() == 0
    assert server_id in (label.text() for label in server_scroll.findChildren(QLabel))
    server.close()
    owner.close()


def test_server_detail_keeps_security_summary_above_scan_on_short_owner(app: QApplication) -> None:
    owner = QWidget()
    owner.resize(320, 360)
    owner.show()
    detail = ServerDetailDialog(
        ServerSummary(
            "synthetic-" + "s" * 100,
            "d" * 100,
            max_label="CONFIDENTIAL",
            tier="internal",
            registry_status="pending",
            session_status="disconnected",
        ),
        owner,
    )
    with closing_widgets(detail, owner):
        detail.open()
        app.processEvents()
        scroll = detail.findChild(QScrollArea)
        assert scroll is not None
        assert detail.width() <= owner.width()
        assert detail.height() <= owner.height()
        assert scroll.horizontalScrollBar().maximum() == 0
        summary = (
            detail.max_label_badge,
            detail.registry_status_field,
            detail.session_status_field,
            detail.tier_field,
        )
        positions = [widget.mapTo(scroll.viewport(), QPoint(0, 0)).y() for widget in summary]
        assert positions == sorted(positions)
        assert positions[0] >= 0
        assert detail.scan_report.mapTo(scroll.viewport(), QPoint(0, 0)).y() > (
            positions[-1] + summary[-1].height()
        )
        scroll.verticalScrollBar().setValue(detail.scan_report.y())
        app.processEvents()
        scan_top = detail.scan_report.mapTo(scroll.viewport(), QPoint(0, 0)).y()
        assert scan_top < scroll.viewport().height()
        assert scan_top + detail.scan_report.height() > 0


def test_long_public_ids_do_not_expand_small_overview(app: QApplication) -> None:
    long_id = "synthetic-" + "x" * 100
    window = GuardWindow()
    window.resize(320, 400)
    window.navigate("overview")
    window.set_overview_snapshot(
        OverviewSnapshot(
            servers=Surface(
                LoadState.READY, (ServerSummary(long_id, long_id, session_status="connected"),)
            ),
            attention=Surface(
                LoadState.READY,
                (
                    AttentionItem(
                        "synthetic-attention",
                        "outcome_unknown",
                        server_id=long_id,
                        target_page="servers",
                        target_id=long_id,
                        action_available=True,
                    ),
                ),
            ),
            recent_calls=Surface(LoadState.READY, (RecentCall(long_id, long_id, long_id),)),
        )
    )
    window.show()
    app.processEvents()
    with closing_widgets(window):
        scroll = window.stack.currentWidget()
        assert isinstance(scroll, QScrollArea)
        assert window.width() == 320
        assert scroll.horizontalScrollBar().maximum() == 0
        assert any(long_id in label.text() for label in window.overview.findChildren(QLabel))
