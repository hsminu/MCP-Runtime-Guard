"""A152: I5/I9, T18/S05 public display selection and refresh regressions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

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

from .test_ui_icons_style import app as app
from .test_ui_icons_style import closing_widgets

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def test_approval_calculations_keep_status_time_and_literal_summary():
    row = ApprovalSummary(
        "id",
        "task",
        "request",
        "server",
        "<b>target</b>\nstatus",
        ApprovalStatus.PENDING,
        NOW,
        expires_at=NOW + timedelta(minutes=5),
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
    display = status_display(row, query, NOW + timedelta(minutes=6), narrow=True)
    assert display[:2] == ("승인 대기\n6분 대기", 42)
    assert "승인 유효 시간 지남" in display[2]
    assert "요청 시각:" in display[3]
    assert status_display(row, query, NOW, narrow=False)[:2] == ("승인 대기", 64)
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
