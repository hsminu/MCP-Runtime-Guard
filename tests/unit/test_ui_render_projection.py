"""A152: I5/I9, T18/S05 public display selection and refresh regressions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import Qt

from guard_app.domain.enums import ApprovalStatus
from guard_app.ui.approval_presentation import (
    selected_detail_text,
    status_display,
    summary_columns,
    valid_page,
)
from guard_app.ui.approvals import (
    ApprovalLane,
    ApprovalListPage,
    ApprovalPage,
    ApprovalQuery,
    ApprovalSort,
    ApprovalSummary,
)
from guard_app.ui.models import AttentionItem, LoadState, OverviewSnapshot, ServerSummary, Surface
from guard_app.ui.overview import OverviewPage, ServerRow
from guard_app.ui.overview_presentation import (
    connected_servers,
    priority_server_ids,
    unresolved_attention,
)

from .test_ui_icons_style import app as app
from .test_ui_icons_style import closing_widgets

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def test_attention_selection_and_server_priority_preserve_backend_order():
    first = AttentionItem("b", "approval", server_id="b")
    duplicate = replace(first, server_id="c")
    last = AttentionItem("a", "approval", server_id="a")
    done = AttentionItem("done", "approval", server_id="d", resolved=True)
    surface = Surface(LoadState.READY, (first, duplicate, done, last))
    assert unresolved_attention(surface) == (first, last)
    assert priority_server_ids(surface) == {"a", "b", "c"}
    servers = tuple(ServerSummary(key, key, session_status="connected") for key in "dcbaz")
    disconnected = ServerSummary("offline", "offline", registry_status="ACTIVE")
    assert connected_servers(Surface(LoadState.READY, (disconnected, *servers)), {"a", "b"}) == (
        servers[2],
        servers[3],
        servers[0],
        servers[1],
        servers[4],
    )
    assert unresolved_attention(replace(surface, state=LoadState.STALE)) == (first, last)
    assert priority_server_ids(replace(surface, state=LoadState.STALE)) == set()
    for state in (LoadState.ERROR, LoadState.LOADING, LoadState.UNAVAILABLE):
        assert unresolved_attention(replace(surface, state=state)) == ()
        assert connected_servers(Surface(state, servers), {"a"}) == ()


def test_attention_calculated_once_and_current_values_replace_old(app, monkeypatch):
    calls = []

    def counted(surface):
        calls.append(surface)
        return unresolved_attention(surface)

    page = OverviewPage()
    monkeypatch.setattr(page, "_unresolved_attention", counted)
    items = tuple(AttentionItem(str(i), "approval", task_id="old") for i in range(7))
    initial = OverviewSnapshot(attention=Surface(LoadState.READY, items))
    with closing_widgets(page):
        page.set_snapshot(initial)
        assert len(calls) == 1
        page._change_page("attention", 1)
        assert len(calls) == 1
        changed = replace(
            initial,
            attention=Surface(
                LoadState.READY, tuple(replace(item, task_id="new") for item in items)
            ),
        )
        page.set_snapshot(changed)
        assert len(calls) == 2
        assert "Task new" in page.attention_layout.itemAt(0).widget().facts.text()
        page.set_snapshot(changed)
        assert len(calls) == 2
        page.set_snapshot(replace(changed, attention=Surface(LoadState.ERROR, items)))
        assert page.attention_layout.count() == 0


def test_approval_calculations_keep_status_time_and_literal_summary():
    row = ApprovalSummary(
        "id", "task", "request", "server", "<b>target</b>\nstatus", ApprovalStatus.PENDING, NOW
    )
    query = ApprovalQuery(1, ApprovalLane.PENDING, "", "", ApprovalSort.OLDEST, 1)
    assert summary_columns(row)[:5] == (
        "id",
        "server",
        "task\nrequest",
        "<b>target</b>[U+000A]status",
        "",
    )
    detail = selected_detail_text(row)
    assert len(detail.splitlines()) == 6
    assert "<b>target</b>\\u000Astatus" in detail
    display = status_display(row, query, NOW + timedelta(minutes=6), 300, narrow=True)
    assert display[:2] == ("승인 대기\n6분 대기", 42)
    assert "승인 유효 시간 지남" in display[2]
    assert "요청 시각:" in display[3]
    assert status_display(row, query, NOW, 300, narrow=False)[:2] == ("승인 대기", 64)
    assert valid_page(ApprovalPage(query, (row,), 1))
    assert not valid_page(ApprovalPage(query, (replace(row, status=ApprovalStatus.APPROVED),), 1))
    second = replace(row, approval_id="zz")
    assert valid_page(ApprovalPage(query, (row, second), 2))
    assert not valid_page(ApprovalPage(query, (second, row), 2))


def test_same_approval_id_refreshes_cells_and_selection_detail(app):
    page = ApprovalListPage()
    page.refresh()
    query = page.current_query
    assert query is not None
    row = ApprovalSummary("id", "task", "request", "server", "old", ApprovalStatus.PENDING, NOW)
    with closing_widgets(page):
        assert page.set_page(ApprovalPage(query, (row,), 1), display_now=NOW)
        page.select_approval("id")
        updated = replace(row, destination_summary="<b>new</b>", row_revision=2)
        assert page.set_page(ApprovalPage(query, (updated,), 1), display_now=NOW)
        assert page.table.item(0, 3).text() == "<b>new</b>"
        assert page.selected_summary == updated
        assert "<b>new</b>" in page.selection_detail.toPlainText()


def test_extracted_server_row_keeps_untrusted_text_plain(app):
    row = ServerRow(ServerSummary("server", '<img src="https://bad.invalid/">'))
    assert row.title.textFormat() is Qt.TextFormat.PlainText
    assert row.title.text() == '<img src="https://bad.invalid/">'
    assert row.facts.textFormat() is Qt.TextFormat.PlainText
    row.close()
