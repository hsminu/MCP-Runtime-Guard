"""Screen demo: every management page answers with fixed fake rows, no backend."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from dataclasses import replace
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from examples import ui_demo

from guard_app.domain.enums import ApprovalStatus
from guard_app.services.bridge import BackendBindings, BackendBridge, RegistrationRequest
from guard_app.ui.approvals import PAGE_SIZE, ApprovalLane, ApprovalQuery, ApprovalSort
from guard_app.ui.window import GuardWindow

from .test_call_logs import panel_text
from .test_ui_icons_style import app as app
from .test_ui_icons_style import show_widget


@pytest.fixture
def demo(app, monkeypatch):
    monkeypatch.setattr(ui_demo, "REPLY_DELAY_MS", 0)
    monkeypatch.setattr(ui_demo, "DEVICE_LOGIN_MS", 0)

    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)

    def registration_reply(request, *, command_id):
        view = asyncio.run(ui_demo.registration(request))
        bridge.registration_changed.emit(replace(view, command_id=command_id))

    monkeypatch.setattr(bridge, "request_registration", registration_reply)
    window = GuardWindow()
    data = ui_demo.build(bridge, window)
    show_widget(app, window, 1280, 800)
    yield window, data
    window.clear_session_content()
    bridge.close()
    window.close()


def settle(app, done, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while not done() and time.monotonic() < deadline:
        app.processEvents()
    return done()


def query(lane, *, page=1, search="", server_id="", sort=ApprovalSort.OLDEST):
    return ApprovalQuery(1, lane, search, server_id, sort, page)


def test_demo_fills_every_management_page(app, demo) -> None:
    window, data = demo
    assert window.server_page.table.rowCount() == len(ui_demo.SERVERS)
    assert window.log_page.table.rowCount() == len(data.calls)
    assert window.policy_page.rules.rowCount() > 0
    assert window.policy_page.excluded.rowCount() == 1
    pending = sum(1 for row in data.approvals if row.status is ApprovalStatus.PENDING)
    assert settle(app, lambda: window.approval_page.table.rowCount() == pending)
    assert "로그인" in window.codex_login_info.status.text()


def test_approval_rows_follow_lane_search_server_sort_and_page() -> None:
    rows = ui_demo.approvals(datetime.now(UTC))
    history = [row for row in rows if row.status is not ApprovalStatus.PENDING]
    first = ui_demo.approval_page(query(ApprovalLane.HISTORY), rows)
    assert first.total_count == len(history)
    assert len(first.rows) == PAGE_SIZE
    last_page = (len(history) + PAGE_SIZE - 1) // PAGE_SIZE
    last = ui_demo.approval_page(query(ApprovalLane.HISTORY, page=last_page), rows)
    assert len(last.rows) == len(history) - (last_page - 1) * PAGE_SIZE
    drive = ui_demo.approval_page(query(ApprovalLane.PENDING, server_id="drive"), rows)
    assert [row.server_id for row in drive.rows] == ["drive"]
    found = ui_demo.approval_page(query(ApprovalLane.PENDING, search="DEMO-01"), rows)
    assert [row.approval_id for row in found.rows] == ["approval-demo-01"]
    newest = ui_demo.approval_page(query(ApprovalLane.PENDING, sort=ApprovalSort.NEWEST), rows)
    created = [row.created_at for row in newest.rows]
    assert created == sorted(created, reverse=True)


def test_call_stages_stop_where_the_status_stops() -> None:
    calls = {row.current_status: row for row in ui_demo.calls(datetime.now(UTC))}
    released = ui_demo.call_stages(calls["SUCCEEDED"])
    assert all(stage.state == "OCCURRED" for stage in released)
    assert released[-1].stage_code == "COMMITTED"
    assert "WAITING_APPROVAL" not in {stage.stage_code for stage in released}
    waiting = ui_demo.call_stages(calls["WAITING_APPROVAL"])
    assert [stage.state for stage in waiting[:3]] == ["OCCURRED", "OCCURRED", "NOT_RUN"]
    assert ui_demo.call_stages(calls["WITHHELD"])[-1].stage_code == "WITHHELD"


def test_details_answer_the_selected_call_and_open_server_dialog(app, demo) -> None:
    window, _ = demo
    window.navigate("logs")
    window.log_page.select_call("call-0001")
    panel = window.log_page.detail_panel
    assert settle(app, lambda: "최초 판정" in panel.stages.text())
    assert panel.identifiers[0].text() == "호출 ID: call-0001"
    window.navigate("servers")
    window.server_page.select_server("crm")
    window.server_page.detail_button.click()
    server = window.server_page._detail_dialog
    assert server is not None
    assert settle(
        app, lambda: "저장된 검사 결과가 지금 도구와 다름" in panel_text(server.scan_report)
    )
    server.close()


def test_policy_and_login_change_only_the_fake_rows(app, demo) -> None:
    window, data = demo
    window.policy_page.allow_change_requested.emit("mail", ("send", "list_folders"), ())
    assert settle(app, lambda: ("mail", "send") in data.policy.allow)
    assert ("mail", "send") not in data.policy.deny
    assert ("docs", "read") in data.policy.allow
    login = window.codex_login_info
    login.device_login_requested.emit()
    assert data.account.state == "PENDING_DEVICE"
    assert settle(app, lambda: data.account.state == "CHATGPT")
    login.logout_requested.emit()
    assert data.account.state == "SIGNED_OUT"


def test_demo_starts_the_synthetic_backend_with_or_without_agt_options() -> None:
    backend = ui_demo.synthetic_backend()
    assert inspect.iscoroutine(backend)
    backend.close()


@pytest.mark.asyncio
async def test_demo_bindings_answer_scan_status_only(monkeypatch):
    from examples import synthetic_app

    bindings = BackendBindings(runner_for_task=lambda _: None, human_control=None)

    async def bootstrap(scenario):
        assert scenario == "ask"
        return bindings

    monkeypatch.setattr(synthetic_app, "bootstrap", bootstrap)
    monkeypatch.setattr(ui_demo, "REPLY_DELAY_MS", 0)
    demo_bindings = await ui_demo.synthetic_backend()
    assert demo_bindings.registration is not None
    assert demo_bindings.runner_for_task is bindings.runner_for_task
    states = {}
    for server in ui_demo.SERVERS:
        view = await demo_bindings.registration(
            RegistrationRequest("scan_status", server.server_id)
        )
        assert view.code == "SCAN_STATUS"
        assert view.server_id == server.server_id
        states[server.server_id] = view.scan
    assert states["docs"].run == states["mail"].run == "done"
    assert states["docs"].report.max_severity == "LOW"
    assert states["mail"].report.max_severity == "MEDIUM"
    assert states["drive"].run == "running"
    assert states["crm"].outdated
    assert states["crm"].report is None
    assert states["wiki"].report is None
    assert states["wiki"].run == "none"
    other = await demo_bindings.registration(RegistrationRequest("activate", "docs"))
    assert other.code == "REG_UNAVAILABLE"
