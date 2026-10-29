"""A206: server detail scan section and layout (I7)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QScrollArea

from guard_app.services.bridge import CommandEvent, CommandStage, RegistrationView, ScanState
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.scan_report import ScanReportPanel
from guard_app.ui.servers import ServerListPage
from guard_app.ui.style import style_sheet

from .test_a201_scan_report_ui import bridge as bridge
from .test_a201_scan_report_ui import report
from .test_a201_scan_report_ui import roots as roots
from .test_call_logs import panel_text
from .test_ui_icons_style import app as app
from .test_ui_icons_style import resize_widget, show_widget


def open_detail(app, roots, bridge=None):
    """I7/T18: only the registration bridge supplies advisory scan state."""
    page = ServerListPage()
    roots.append(page)
    if bridge is not None:
        page.attach_activation(bridge)
    page.set_servers(Surface(LoadState.READY, (ServerSummary("docs", "사내 문서"),)))
    show_widget(app, page, 800, 700)
    page.select_server("docs")
    page.detail_button.click()
    app.processEvents()
    return page, page._detail_dialog


def test_unconnected_detail_is_not_loading(app, roots):
    _, detail = open_detail(app, roots)
    assert "검사 기능이 연결되지 않음" in panel_text(detail.scan_report)
    assert "불러오는 중" not in panel_text(detail)


def test_request_and_report_are_bound_to_current_opening(app, roots, bridge):
    backend, requests = bridge
    page, detail = open_detail(app, roots, backend)
    assert len(requests) == 1
    request, kw = requests[0]
    command = kw["command_id"]
    assert (request.action, request.server_id) == ("scan_status", "docs")
    assert panel_text(detail.scan_report) == "검사 결과 확인 중"
    state = ScanState("docs", run="done", report=report())
    for other_command, other_server in (("other", "docs"), (command, "mail")):
        backend.registration_changed.emit(
            RegistrationView("SCAN_STATUS", other_command, server_id=other_server, scan=state)
        )
        assert panel_text(detail.scan_report) == "검사 결과 확인 중"
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", command, server_id="docs", scan=state)
    )
    text = panel_text(detail.scan_report)
    assert "검사 시각" in text
    assert "YARA: 완료" in text
    detail.close()
    backend.registration_changed.emit(
        RegistrationView("SCAN_UNAVAILABLE", command, server_id="docs")
    )
    backend.scan_changed.emit(ScanState("docs", run="running"))
    assert panel_text(detail.scan_report) == text
    page.detail_button.click()
    new_command = requests[-1][1]["command_id"]
    assert len(requests) == 2
    assert new_command != command
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", command, server_id="docs", scan=state)
    )
    backend.command_changed.emit(
        CommandEvent(command, "control", "registration", "docs", CommandStage.REJECTED, "FAILED")
    )
    assert panel_text(detail.scan_report) == "검사 결과 확인 중"
    backend.registration_changed.emit(
        RegistrationView("SCAN_STATUS", new_command, server_id="docs", scan=state)
    )
    assert "검사 시각" in panel_text(detail.scan_report)


@pytest.mark.parametrize("code", ["SCAN_UNAVAILABLE", "REG_UNAVAILABLE", "SCAN_STATUS"])
def test_missing_scan_is_explicit(app, roots, bridge, code):
    backend, requests = bridge
    _, detail = open_detail(app, roots, backend)
    backend.registration_changed.emit(
        RegistrationView(code, requests[-1][1]["command_id"], server_id="docs")
    )
    assert "검사 기능이 연결되지 않음" in panel_text(detail.scan_report)


def test_missing_registration_binding_has_no_server_id(app, roots, bridge):
    backend, requests = bridge
    _, detail = open_detail(app, roots, backend)
    command = requests[-1][1]["command_id"]
    backend.registration_changed.emit(
        RegistrationView("REG_UNAVAILABLE", command, server_id="mail")
    )
    assert panel_text(detail.scan_report) == "검사 결과 확인 중"
    # This is the actual bridge_commands response when bindings.registration is None.
    backend.registration_changed.emit(RegistrationView("REG_UNAVAILABLE", command))
    assert "검사 기능이 연결되지 않음" in panel_text(detail.scan_report)


@pytest.mark.parametrize("stage", [CommandStage.REJECTED, CommandStage.UNCONFIRMED])
def test_command_failure_ends_loading(app, roots, bridge, stage):
    backend, requests = bridge
    _, detail = open_detail(app, roots, backend)
    backend.command_changed.emit(
        CommandEvent("other", "control", "registration", "docs", stage, "FAILED")
    )
    assert panel_text(detail.scan_report) == "검사 결과 확인 중"
    backend.command_changed.emit(
        CommandEvent(
            requests[-1][1]["command_id"], "control", "registration", "docs", stage, "FAILED"
        )
    )
    assert panel_text(detail.scan_report) == "검사 결과를 확인하지 못함."


def test_unexpected_response_fails(app, roots, bridge):
    backend, requests = bridge
    _, detail = open_detail(app, roots, backend)
    backend.registration_changed.emit(
        RegistrationView(
            "UNEXPECTED", requests[-1][1]["command_id"], server_id="docs", scan=ScanState("docs")
        )
    )
    assert panel_text(detail.scan_report) == "검사 결과를 확인하지 못함."


def test_scan_events_update_only_the_open_server(app, roots, bridge):
    backend, requests = bridge
    _, detail = open_detail(app, roots, backend)
    backend.registration_changed.emit(
        RegistrationView(
            "SCAN_STATUS",
            requests[-1][1]["command_id"],
            server_id="docs",
            scan=ScanState("docs", outdated=True),
        )
    )
    text = panel_text(detail.scan_report)
    assert "저장된 검사 결과가 지금 도구와 다름" in text
    backend.scan_changed.emit(ScanState("mail", run="running"))
    assert panel_text(detail.scan_report) == text
    backend.scan_changed.emit(ScanState("docs", run="running"))
    assert "검사 중 · 최대 10분" in panel_text(detail.scan_report)
    backend.scan_changed.emit(ScanState("docs", run="done", report=report()))
    assert "검사 시각" in panel_text(detail.scan_report)


def test_panel_transient_messages_replace_report_content(app, roots):
    panel = ScanReportPanel()
    roots.append(panel)
    panel.set_state(ScanState("docs", run="done", report=report()))
    for update, expected in (
        (panel.set_loading, "검사 결과 확인 중"),
        (lambda: panel.set_failed("실패"), "실패"),
    ):
        update()
        labels = panel.findChildren(QLabel)
        assert len(labels) == 1
        assert (labels[0].text(), labels[0].objectName()) == (expected, "runtimeMessage")
    panel.set_state(None)
    assert "검사 기능이 연결되지 않음" in panel_text(panel)
    panel.clear()
    assert not panel.findChildren(QLabel)


def test_sections_reflow_without_horizontal_scroll(app, roots):
    _, detail = open_detail(app, roots)
    detail.setStyleSheet(style_sheet())
    detail.scan_report.set_state(ScanState("docs", run="done", report=report()))
    app.processEvents()
    scroll = detail.findChild(QScrollArea)
    assert scroll is not None
    headings = [
        label
        for label in detail.findChildren(QLabel)
        if label.objectName() == "sectionTitle" and label.parentWidget() is scroll.widget()
    ]
    assert [label.text() for label in headings] == ["상태", "연결 설정", "최근 검사"]
    positions = [label.mapTo(scroll.widget(), QPoint()).y() for label in headings]
    assert positions == sorted(positions)
    assert sum(label.text() == "Registry 등록 상태" for label in detail.findChildren(QLabel)) == 1
    field = detail.registry_status_field
    label = field.findChild(QLabel, "secondary")
    value = detail._registry_status_value
    resize_widget(app, detail, 600, 560)
    assert max(label.y(), value.y()) < min(label.y() + label.height(), value.y() + value.height())
    for width in (260, 240):
        resize_widget(app, detail, width, 560)
        app.processEvents()
        assert detail.width() == width
        assert label.y() + label.height() <= value.y()
        assert scroll.horizontalScrollBar().maximum() == 0


def test_pending_replies_after_close_do_not_change_cached_dialog(app, roots, bridge):
    backend, requests = bridge
    page, detail = open_detail(app, roots, backend)
    command = requests[-1][1]["command_id"]
    detail.close()
    assert page._detail_dialog is None
    backend.registration_changed.emit(
        RegistrationView(
            "SCAN_STATUS", command, server_id="docs", scan=ScanState("docs", run="done")
        )
    )
    backend.command_changed.emit(
        CommandEvent(command, "control", "registration", "docs", CommandStage.REJECTED, "FAILED")
    )
    assert panel_text(detail.scan_report) == "검사 결과 확인 중"
    page.detail_button.click()
    assert requests[-1][1]["command_id"] != command
    backend.registration_changed.emit(RegistrationView("REG_UNAVAILABLE", command))
    assert panel_text(detail.scan_report) == "검사 결과 확인 중"
