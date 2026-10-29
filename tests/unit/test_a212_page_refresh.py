"""A212 / I3, I8, T12: navigation queries only, with no approval replay."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from guard_app.domain.queries import ApprovalListPage, CallListPage
from guard_app.services.bridge import BackendBridge, BridgeState, QueryReply
from guard_app.ui.call_logs import PublicCallSummary
from guard_app.ui.models import LoadState, Surface
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.window import GuardWindow

from .test_server_add import app as app
from .test_store_query_link import NOW, approval_row, call_row


@pytest.fixture
def screen(app, monkeypatch):
    def unused():
        raise AssertionError("No real backend, Codex or network")

    bridge = BackendBridge(unused)
    monkeypatch.setattr(BackendBridge, "state", property(lambda _self: BridgeState.READY))
    queries, controls = [], []
    monkeypatch.setattr(bridge, "request_query", lambda *args: queries.append(args))
    monkeypatch.setattr(bridge, "request_control", lambda *args, **kw: controls.append((args, kw)))
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    yield SimpleNamespace(window=window, bridge=bridge, queries=queries, controls=controls)
    window.clear_session_content()
    window.close()
    window.deleteLater()
    bridge.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def reply(screen, kind, token):
    page = (
        CallListPage(rows=(call_row(1), call_row(2)), total_count=2, as_of_utc=NOW)
        if kind == "calls"
        else ApprovalListPage(rows=(approval_row(2), approval_row(1)), total_count=2, as_of_utc=NOW)
    )
    screen.bridge.query_answered.emit(QueryReply(kind, token, page))


def connect(screen):
    screen.bridge.query_source_changed.emit(True)
    assert [query[0] for query in screen.queries] == ["approvals", "calls"]
    for kind, token, _query in tuple(screen.queries):
        reply(screen, kind, token)


def test_logs_refresh_each_navigation_including_same_page(screen):
    connect(screen)
    screen.window.navigate("runs")
    before = len(screen.queries)
    screen.window.navigate("logs")
    assert len(screen.queries) == before + 1
    assert screen.queries[-1][0] == "calls"
    first_token = screen.queries[-1][1]
    screen.window.navigate("logs")
    assert len(screen.queries) == before + 2
    assert screen.queries[-1][0] == "calls"
    assert screen.queries[-1][1] > first_token
    assert not screen.controls


def test_approvals_refresh_queries_only_without_approval_or_denial(screen):
    connect(screen)
    before = len(screen.queries)
    for index in range(2):
        screen.window.navigate("approvals")
        assert len(screen.queries) == before + index + 1
        assert screen.queries[-1][0] == "approvals"
        reply(screen, *screen.queries[-1][:2])
    assert not screen.controls


def test_disconnected_sources_never_send_queries(screen):
    for page in ("runs", "logs", "logs", "approvals", "approvals"):
        screen.window.navigate(page)
    assert not screen.queries
    connect(screen)
    screen.bridge.query_source_changed.emit(False)
    count = len(screen.queries)
    screen.window.navigate("logs")
    screen.window.navigate("approvals")
    assert len(screen.queries) == count
    assert not screen.controls


@pytest.mark.parametrize(
    ("page", "kind", "target"),
    [("logs", "calls", "request-002"), ("approvals", "approvals", "approval-002")],
)
@pytest.mark.parametrize("immediate", [False, True])
def test_navigation_selects_target_after_current_refresh_response(
    screen, monkeypatch, page, kind, target, immediate
):
    connect(screen)
    if immediate:

        def answer(query_kind, token, query):
            screen.queries.append((query_kind, token, query))
            reply(screen, query_kind, token)

        monkeypatch.setattr(screen.bridge, "request_query", answer)
    screen.window.navigate(page, target)
    assert screen.queries[-1][0] == kind
    if not immediate:
        reply(screen, *screen.queries[-1][:2])
    if page == "logs":
        assert screen.window.log_page._selected_call_id() == target
    else:
        assert screen.window.approval_page.selected_summary.approval_id == target
    assert not screen.controls


def test_old_response_cannot_select_an_old_navigation_target(screen):
    connect(screen)
    screen.window.navigate("logs", "request-001")
    old = screen.queries[-1]
    screen.window.navigate("logs", "request-002")
    current = screen.queries[-1]
    reply(screen, *old[:2])
    assert screen.window.log_page._selected_call_id() is None
    reply(screen, *current[:2])
    assert screen.window.log_page._selected_call_id() == "request-002"


def test_leaving_page_cancels_pending_target_selection(screen):
    connect(screen)
    screen.window.navigate("logs", "request-002")
    request = screen.queries[-1]
    screen.window.navigate("runs")
    reply(screen, *request[:2])
    assert screen.window.log_page._selected_call_id() is None


def test_unconnected_snapshot_target_still_selects_existing_call(screen):
    screen.window.set_call_log_snapshot(Surface(LoadState.READY, (PublicCallSummary("existing"),)))
    screen.window.navigate("logs", "existing")
    assert screen.window.log_page._selected_call_id() == "existing"
    assert not screen.queries


def test_other_pages_do_not_refresh_store_lists(screen):
    connect(screen)
    count = len(screen.queries)
    for page in ("overview", "audit", "servers", "runs", "results"):
        screen.window.navigate(page)
    assert len(screen.queries) == count


def test_other_list_response_does_not_take_the_navigation_target(screen):
    connect(screen)
    calls = screen.window.log_page.current_query
    screen.window.navigate("approvals", "approval-002")
    request = screen.queries[-1]
    reply(screen, "calls", calls.token)
    reply(screen, *request[:2])
    assert screen.window.approval_page.selected_summary.approval_id == "approval-002"
