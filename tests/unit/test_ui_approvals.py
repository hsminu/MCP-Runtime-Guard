"""A33 page boundaries and stale read-only approval presentation."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QPushButton, QScrollArea

from guard_app.domain.enums import ApprovalStatus
from guard_app.ui.approvals import (
    PAGE_SIZE,
    ApprovalLane,
    ApprovalListPage,
    ApprovalPage,
    ApprovalSort,
    ApprovalSummary,
)
from guard_app.ui.models import LoadState

NOW = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def summary(
    number: int,
    *,
    status: ApprovalStatus = ApprovalStatus.PENDING,
    created_at: datetime | None = None,
    destination: str = "내부 저장소",
) -> ApprovalSummary:
    return ApprovalSummary(
        approval_id=f"approval-{number:03}",
        task_id=f"task-{number:03}",
        request_id=f"request-{number:03}",
        server_id="server-a",
        destination_summary=destination,
        status=status,
        created_at=created_at or NOW - timedelta(minutes=number),
        resolved_at=NOW if status is not ApprovalStatus.PENDING else None,
    )


def result(
    page: ApprovalListPage,
    rows: tuple[ApprovalSummary, ...],
    total: int,
) -> ApprovalPage:
    query = page.current_query
    assert query is not None
    return ApprovalPage(query=query, rows=rows, total_count=total, as_of=NOW)


def test_empty_error_and_loading_are_not_reported_as_zero(app: QApplication) -> None:
    view = ApprovalListPage()
    requested = []
    view.query_requested.connect(requested.append)
    assert view.state is LoadState.UNAVAILABLE
    assert "미제공" in view.count_label.text()

    view.refresh()
    query = requested[-1]
    assert query.page == 1
    assert query.page_size == PAGE_SIZE
    assert query.lane is ApprovalLane.PENDING
    assert query.sort is ApprovalSort.OLDEST
    assert view.state is LoadState.LOADING
    assert "미제공" in view.count_label.text()
    assert view.set_error(query)
    assert view.state is LoadState.ERROR
    assert "불러오지 못함" in view.state_message.message.text()
    assert "미제공" in view.count_label.text()

    view.refresh()
    assert not view.set_error(query)  # previous response must not replace new query
    assert view.set_page(result(view, (), 0), display_now=NOW)
    assert view.state is LoadState.READY
    assert view.count_label.text() == "전체 0건 · 현재 페이지 0건"
    assert view.page_label.text() == "1 / 1"
    assert "대기 중인 승인 요청이 없어" in view.state_message.message.text()
    assert not view.previous_button.isEnabled()
    assert not view.next_button.isEnabled()


@pytest.mark.parametrize("total", [1, 6, 7, 122])
def test_page_counts_and_boundaries(app: QApplication, total: int) -> None:
    view = ApprovalListPage()
    view.refresh()
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    for page_number in range(1, pages + 1):
        query = view.current_query
        assert query is not None and query.page == page_number
        # Every page uses the backend's global oldest-first order.
        start = (page_number - 1) * PAGE_SIZE
        count = min(PAGE_SIZE, total - start)
        rows = tuple(
            summary(number, created_at=NOW + timedelta(seconds=number))
            for number in range(start, start + count)
        )
        assert view.set_page(result(view, rows, total), display_now=NOW)
        assert view.table.rowCount() == count
        assert view.count_label.text() == (f"전체 {total:,}건 · 현재 페이지 {count}건")
        assert view.page_label.text() == f"{page_number} / {pages}"
        assert view.previous_button.isEnabled() is (page_number > 1)
        assert view.next_button.isEnabled() is (page_number < pages)
        view.next_page()
    assert view.current_query is not None
    assert view.current_query.page == pages
    if pages > 1:
        view.previous_page()
        assert view.current_query.page == pages - 1


def test_stale_response_filter_change_and_disappearing_last_page(
    app: QApplication,
) -> None:
    view = ApprovalListPage()
    requests = []
    view.query_requested.connect(requests.append)
    view.refresh()
    old_query = requests[-1]
    view.search.setText("task-")
    view.search.editingFinished.emit()
    assert view.current_query is not None
    assert view.current_query.search == "task-"
    assert not view.set_page(ApprovalPage(old_query, (summary(1),), 1), display_now=NOW)
    assert view.table.rowCount() == 0
    first_six = tuple(summary(number) for number in range(6, 0, -1))
    assert view.set_page(result(view, first_six, 7), display_now=NOW)
    view.next_page()
    old_last = requests[-1]
    assert old_last.page == 2
    # A concurrent resolution shrank the result to one page.
    assert not view.set_page(ApprovalPage(old_last, (), 6), display_now=NOW)
    assert requests[-1].page == 1
    assert requests[-1].token > old_last.token
    assert view.state is LoadState.LOADING
    assert not view.set_error(old_last)


def test_pending_and_history_are_separate_and_selection_is_not_reassigned(
    app: QApplication,
) -> None:
    view = ApprovalListPage()
    selected = []
    view.summary_selected.connect(selected.append)
    view.refresh()
    initial = (summary(2), summary(1))
    assert view.set_page(result(view, initial, 2), display_now=NOW)
    view.table.selectRow(0)
    assert view.selected_summary == initial[0]

    view.refresh()
    assert selected[-1] is None
    assert view.selected_summary is None
    assert view.set_page(result(view, (summary(1),), 1), display_now=NOW)
    assert view.selected_summary is None
    assert view.table.currentRow() == -1

    view.lane_tabs.setCurrentIndex(1)
    assert view.current_query is not None
    assert view.current_query.lane is ApprovalLane.HISTORY
    assert view.table.rowCount() == 0
    history = summary(4, status=ApprovalStatus.REJECTED)
    assert view.set_page(result(view, (history,), 1), display_now=NOW)
    assert "거절됨" in view.table.item(0, 4).text()
    assert not view.set_page(result(view, (summary(5),), 1), display_now=NOW)
    assert view.state is LoadState.ERROR  # pending status in history is invalid


def test_clearing_selection_does_not_retain_current_row(app: QApplication) -> None:
    view = ApprovalListPage()
    selected = []
    view.summary_selected.connect(selected.append)
    view.refresh()
    rows = (summary(2), summary(1))
    assert view.set_page(result(view, rows, 2), display_now=NOW)
    view.table.selectRow(0)
    assert view.selected_summary == rows[0]
    assert selected[-1] == rows[0]

    view.table.clearSelection()
    assert view.table.currentRow() == 0  # Qt retains a current index.
    assert view.selected_summary is None
    assert selected[-1] is None


def test_target_selection_stays_within_loaded_page(app: QApplication) -> None:
    view = ApprovalListPage()
    requests = []
    view.query_requested.connect(requests.append)
    assert not view.select_approval("approval-001")
    view.refresh()
    rows = (summary(2), summary(1))
    assert view.set_page(result(view, rows, 2), display_now=NOW)
    assert view.select_approval("approval-001")
    assert view.selected_summary == rows[1]
    assert not view.select_approval("approval-999")
    assert view.selected_summary is None
    assert len(requests) == 1
    assert view.current_query is requests[0]


@pytest.mark.parametrize("rows", [(), (summary(1),)])
def test_incomplete_backend_page_is_error_not_empty_success(
    app: QApplication, rows: tuple[ApprovalSummary, ...]
) -> None:
    view = ApprovalListPage()
    view.refresh()
    assert not view.set_page(result(view, rows, 7), display_now=NOW)
    assert view.state is LoadState.ERROR
    assert view.table.rowCount() == 0
    assert "미제공" in view.count_label.text()
    assert "불러오지 못함" in view.state_message.message.text()
    assert "대기 중인 승인 요청이 없어" not in view.state_message.message.text()


def test_equal_time_ties_require_stable_id_order_and_no_raw_actions(
    app: QApplication,
) -> None:
    view = ApprovalListPage()
    view.refresh()
    rows = (
        summary(1, created_at=NOW),
        summary(2, created_at=NOW),
    )
    assert not view.set_page(result(view, tuple(reversed(rows)), 2), display_now=NOW)
    assert view.state is LoadState.ERROR
    view.refresh()
    assert view.set_page(result(view, rows, 2), display_now=NOW)
    assert view.table.item(0, 0).text() == "approval-001"
    assert not hasattr(rows[0], "arguments")
    assert not hasattr(rows[0], "arguments_bytes")
    buttons = [button.text() for button in view.findChildren(QPushButton)]
    assert "승인" not in buttons
    assert "거절" not in buttons


def test_filter_and_newest_sort_request_backend_wide_order(app: QApplication) -> None:
    view = ApprovalListPage()
    requests = []
    view.query_requested.connect(requests.append)
    view.refresh()
    view.server_filter.setText("server-a")
    view.server_filter.editingFinished.emit()
    view.sort.setCurrentIndex(1)
    newest = requests[-1]
    assert newest.server_id == "server-a"
    assert newest.sort is ApprovalSort.NEWEST
    rows = (
        summary(2, created_at=NOW),
        summary(1, created_at=NOW),
    )
    assert view.set_page(result(view, rows, 2), display_now=NOW)
    assert view.table.item(0, 0).text() == "approval-002"
    assert view.current_query is not None
    assert view.current_query.page == 1


@pytest.mark.parametrize(
    ("width", "visible_columns", "stacked"),
    [
        (320, (0, 1, 4), True),
        (540, (0, 1, 4), True),
        (760, (0, 1, 4, 5), False),
        (980, (0, 1, 2, 3, 4, 5), False),
    ],
)
def test_table_and_controls_fit_viewport_at_multiple_widths(
    app: QApplication,
    width: int,
    visible_columns: tuple[int, ...],
    stacked: bool,
) -> None:
    view = ApprovalListPage()
    view.resize(width, 580)
    view.show()
    try:
        view.refresh()
        rows = tuple(summary(number) for number in range(6, 0, -1))
        assert view.set_page(result(view, rows, 6), display_now=NOW)
        app.processEvents()
        QTest.qWait(20)
        app.processEvents()
        assert view.width() == width
        assert (
            tuple(i for i in range(6) if not view.table.isColumnHidden(i))
            == visible_columns
        )
        assert view.table.horizontalScrollBar().maximum() == 0
        direction = (
            QBoxLayout.Direction.TopToBottom
            if stacked
            else QBoxLayout.Direction.LeftToRight
        )
        assert view.filters.direction() == direction
        assert view.pager.direction() == direction
        for control in (
            view.search,
            view.server_filter,
            view.sort,
            view.previous_button,
            view.next_button,
            view.refresh_button,
        ):
            assert control.geometry().right() < width
    finally:
        view.close()


def test_narrow_keyboard_selection_shows_only_public_summary(app: QApplication) -> None:
    view = ApprovalListPage()
    view.resize(320, 580)
    view.show()
    try:
        view.refresh()
        row = summary(1)
        assert view.set_page(result(view, (row,), 1), display_now=NOW)
        view.table.setFocus()
        QTest.keyClick(view.table, Qt.Key.Key_Down)
        QTest.keyClick(view.table, Qt.Key.Key_Space)
        assert view.selected_summary == row
        assert view.selection_detail.isVisible()
        detail = view.selection_detail.toPlainText()
        assert row.approval_id in detail
        assert row.task_id in detail
        assert row.server_id in detail
        assert "요청 시각" in detail
        assert not hasattr(row, "arguments")
        view.table.setFocus()
        QTest.keyClick(view.table, Qt.Key.Key_Tab)
        assert not view.table.hasFocus()
    finally:
        view.close()


def test_public_summary_cannot_spoof_status_line(app: QApplication) -> None:
    view = ApprovalListPage()
    view.resize(320, 580)
    view.show()
    try:
        view.refresh()
        row = replace(
            summary(1, destination="대상\n상태: 승인됨\u202e"),
            server_id="server\r\n상태: 승인됨",
        )
        assert view.set_page(result(view, (row,), 1), display_now=NOW)
        view.table.selectRow(0)
        lines = view.selection_detail.toPlainText().splitlines()
        assert len(lines) == 6
        assert lines[1] == "상태: 승인 대기"
        assert "\\u000A" in lines[0]
        assert "\\u000A" in lines[3]
        assert "\\u202E" in lines[3]
        assert all(line != "상태: 승인됨" for line in lines)
    finally:
        view.close()


def test_scroll_host_can_shrink_from_full_to_narrow_without_page_overflow(
    app: QApplication,
) -> None:
    view = ApprovalListPage()
    host = QScrollArea()
    host.setWidgetResizable(True)
    host.setWidget(view)
    host.resize(1050, 650)
    host.show()
    try:
        app.processEvents()
        assert view.filters.direction() == QBoxLayout.Direction.LeftToRight
        host.resize(320, 580)
        app.processEvents()
        QTest.qWait(20)
        app.processEvents()
        assert view.filters.direction() == QBoxLayout.Direction.TopToBottom
        assert host.horizontalScrollBar().maximum() == 0
        assert view.table.horizontalScrollBar().maximum() == 0
    finally:
        host.close()


def test_waiting_hint_does_not_expire_or_interpret_markup(app: QApplication) -> None:
    view = ApprovalListPage()
    view.refresh()
    long_wait = summary(
        1,
        created_at=NOW - timedelta(minutes=31),
        destination='<img src="https://bad.invalid/pixel">',
    )
    assert view.set_page(result(view, (long_wait,), 1), display_now=NOW)
    assert "30분 이상 대기(참고)" in view.table.item(0, 4).text()
    assert "승인 대기" in view.table.item(0, 4).text()
    assert view.table.item(0, 3).text() == long_wait.destination_summary
    assert view.selected_summary is None


def test_320_by_400_approval_row_shows_bounded_age_with_identity_and_status(
    app: QApplication,
) -> None:
    view = ApprovalListPage()
    host = QScrollArea()
    host.setWidgetResizable(True)
    host.setWidget(view)
    host.resize(320, 400)
    host.show()
    try:
        view.refresh()
        row = summary(1, created_at=NOW - timedelta(minutes=31))
        assert view.set_page(result(view, (row,), 1), display_now=NOW)
        app.processEvents()
        QTest.qWait(20)
        app.processEvents()

        assert host.width() == 320 and host.height() == 400
        assert tuple(i for i in range(6) if not view.table.isColumnHidden(i)) == (
            0,
            1,
            4,
        )
        assert view.table.item(0, 0).text() == row.approval_id
        assert view.table.item(0, 1).text() == row.server_id
        status = view.table.item(0, 4)
        assert status.text() == "승인 대기\n31분 대기"
        assert "요청 시각: 2026년 10월 12일 13:29:00 KST" in status.toolTip()
        assert "요청 시각: 2026년 10월 12일 13:29:00 KST" in status.data(
            Qt.ItemDataRole.AccessibleTextRole
        )
        metrics = view.table.fontMetrics()
        assert view.table.viewport().height() >= 2 * metrics.lineSpacing() + 4
        assert view.table.columnWidth(4) >= max(
            metrics.horizontalAdvance(line) + 8 for line in status.text().splitlines()
        )
        assert view.table.horizontalScrollBar().maximum() == 0
        assert host.horizontalScrollBar().maximum() == 0

        host.resize(980, 700)
        app.processEvents()
        QTest.qWait(20)
        app.processEvents()
        assert "30분 이상 대기(참고)" in status.text()
        assert "31분 대기" not in status.text()
        assert view.table.horizontalScrollBar().maximum() == 0
    finally:
        host.close()


def test_narrow_row_does_not_invent_elapsed_time_from_future_request(
    app: QApplication,
) -> None:
    view = ApprovalListPage()
    view.resize(320, 580)
    view.show()
    try:
        view.refresh()
        future = summary(1, created_at=NOW + timedelta(minutes=5))
        assert view.set_page(result(view, (future,), 1), display_now=NOW)
        app.processEvents()
        status = view.table.item(0, 4)
        assert status.text() == "승인 대기\n요청 시각 확인"
        assert "2026년 10월 12일 14:05:00 KST" in status.toolTip()
        assert view.table.horizontalScrollBar().maximum() == 0

        view.refresh()
        missing = replace(summary(1), created_at=None)
        assert not view.set_page(result(view, (missing,), 1), display_now=NOW)
        assert view.state is LoadState.ERROR
        assert view.table.rowCount() == 0
    finally:
        view.close()


def test_narrow_approved_row_does_not_imply_tool_execution(app: QApplication) -> None:
    view = ApprovalListPage()
    view.resize(320, 400)
    view.show()
    try:
        view.lane_tabs.setCurrentIndex(1)
        row = summary(1, status=ApprovalStatus.APPROVED)
        assert view.set_page(result(view, (row,), 1), display_now=NOW)
        app.processEvents()
        status = view.table.item(0, 4)
        assert status.text() == "승인됨\n결과 별도 확인"
        assert "실행 결과 별도 확인" in status.toolTip()
        assert view.table.horizontalScrollBar().maximum() == 0
    finally:
        view.close()
