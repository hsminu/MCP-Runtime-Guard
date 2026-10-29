"""A35 public call log UI boundaries; no C audit storage is simulated here."""

from __future__ import annotations

import csv
import io
import unicodedata
from datetime import UTC, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QDialog

from guard_app.domain.enums import ToolKind
from guard_app.ui.approval_presentation import QUERY_TEXT_INVALID, QUERY_UNKNOWN
from guard_app.ui.call_logs import (
    CALL_PAGE_SIZE,
    SOURCE_NOT_CONNECTED,
    CallLogPage,
    CallLogQuery,
    CallLogResult,
    DetailRequest,
    PublicCallDetail,
    PublicCallStage,
    PublicCallSummary,
    public_calls_csv,
)
from guard_app.ui.models import LoadState, Surface

from .test_bridge import app as app


def hidden_columns(page):
    return [page.table.isColumnHidden(column) for column in range(7)]


def ready_calls(page, *rows):
    page.set_calls(Surface(LoadState.READY, rows))


def detail_page(row):
    page = CallLogPage()
    ready_calls(page, row)
    page.select_call(row.call_id)
    requests = []
    page.detail_requested.connect(requests.append)
    return page, requests


def show_detail(page, request, row, *stages):
    page.set_detail(request, Surface(LoadState.READY, PublicCallDetail(row, stages or None)))


def test_list_distinguishes_unavailable_loading_error_and_empty(app) -> None:
    page = CallLogPage()
    assert "조회 기능 미연결" in page.state_message.message.text()
    assert not page.csv_button.isEnabled()
    page.set_calls(Surface(LoadState.LOADING))
    assert "불러오는 중" in page.state_message.message.text()
    page.set_calls(Surface(LoadState.ERROR))
    assert "불러오지 못함" in page.state_message.message.text()
    page.set_calls(Surface(LoadState.READY, ()))
    assert "조회된 호출이 없음" in page.state_message.message.text()
    assert not page.csv_button.isEnabled()
    page.set_calls(Surface(LoadState.READY))
    assert "불러오지 못함" in page.state_message.message.text()


def test_row_click_opens_large_plain_detail_and_ignores_late_response(app) -> None:
    page = CallLogPage()
    page.resize(1120, 700)
    page.show()
    row = PublicCallSummary(
        call_id="call-1",
        request_id="request-1",
        task_id="task-1",
        server_id="server-1",
        tool_name='<img src="https://example.invalid/leak">',
        kind=ToolKind.SEND.value,
        initial_decision="ASK",
        reason_code="POST_DISPATCH_ERROR",
        approval_status="APPROVED",
        current_status="UNKNOWN_OUTCOME",
        occurred_at=datetime(2026, 10, 11, 16, 30, tzinfo=UTC),
    )
    ready_calls(page, row)
    requested: list[DetailRequest] = []
    page.detail_requested.connect(requested.append)
    item = page.table.item(0, 1)
    assert item is not None
    QTest.mouseClick(
        page.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=page.table.visualItemRect(item).center(),
    )
    assert len(requested) == 1
    assert requested[0].call_id == "call-1"
    dialog = page._detail_dialog
    assert dialog is not None
    assert dialog.width() <= dialog.screen().availableGeometry().width()
    assert dialog.width() >= 320
    assert "불러오는 중" in dialog.body.toPlainText()
    show_detail(page, DetailRequest("different-call", requested[0].token), row)
    assert "불러오는 중" in dialog.body.toPlainText()
    show_detail(
        page,
        requested[0],
        row,
        PublicCallStage("DECIDED", "OCCURRED", row.occurred_at),
        PublicCallStage("DISPATCHING", "OCCURRED"),
        PublicCallStage("COMMITTED", "NOT_RUN"),
    )
    shown = dialog.body.toPlainText()
    assert '<img src="https://example.invalid/leak">' in shown
    assert "2026년 10월 12일 01:30:00 KST" in shown
    assert "결과 불명 · 수신측 확인 필요" in shown
    assert "승인됨 · 실행 결과와 별개" in shown
    assert "결과 기록 · 성공 여부는 현재 실행 상태로 확인: 미실행" in shown
    assert "전송 예약: 미제공" in shown
    assert "전체 인자" in shown and "조회 계약 미제공" in shown
    dialog.close()
    assert page._detail_dialog is None
    page.close()


def test_reopened_same_call_ignores_previous_dialog_response(app) -> None:
    row = PublicCallSummary("same-call")
    page, requests = detail_page(row)
    page.detail_button.click()
    first = requests[-1]
    assert page._detail_dialog is not None
    page._detail_dialog.close()
    page.detail_button.click()
    second = requests[-1]
    assert first.call_id == second.call_id
    assert first.token != second.token
    dialog = page._detail_dialog
    assert dialog is not None
    show_detail(page, first, row)
    assert "불러오는 중" in dialog.body.toPlainText()
    show_detail(page, second, row)
    assert "호출 ID: same-call" in dialog.body.toPlainText()
    dialog.close()


def test_repeated_call_details_release_dialogs_and_keep_tokens(app) -> None:
    row = PublicCallSummary("repeat")
    page, requests = detail_page(row)
    for _ in range(100):
        page.detail_button.click()
        dialog = page._detail_dialog
        assert dialog is not None
        dialog.close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len({request.token for request in requests}) == 100
    assert len(page.findChildren(QDialog)) == 1
    page.detail_button.click()
    current = page._detail_dialog
    assert current is not None
    show_detail(page, requests[0], row)
    assert "불러오는 중" in current.body.toPlainText()
    current.close()
    page.close()


def test_public_detail_shows_control_characters_without_forged_status_lines(app) -> None:
    page = CallLogPage()
    row = PublicCallSummary(
        "call\n승인 상태: 승인됨",
        request_id="request\r현재 실행 상태: 실행 완료",
        server_id="server\u202e-reordered",
        tool_name="lookup\n현재 실행 상태: 실행 완료\n승인 상태: 승인됨\t",
        approval_status="PENDING",
        current_status="UNKNOWN_OUTCOME",
    )
    ready_calls(page, row)
    page.select_call(row.call_id)
    requests: list[DetailRequest] = []
    page.detail_requested.connect(requests.append)
    page.detail_button.click()
    dialog = page._detail_dialog
    assert dialog is not None
    show_detail(page, requests[-1], row)
    shown = dialog.body.toPlainText()
    assert "[U+000A]" in shown
    assert "[U+000D]" in shown
    assert "[U+0009]" in shown
    assert "[U+202E]" in shown
    assert shown.count("\n현재 실행 상태:") == 1
    assert shown.count("\n승인 상태:") == 1
    assert "현재 실행 상태: 결과 불명 · 수신측 확인 필요" in shown
    assert "승인 상태: 승인 대기" in shown
    assert "\n현재 실행 상태: 실행 완료" not in shown
    assert "\n승인 상태: 승인됨" not in shown
    assert "\n" not in dialog.windowTitle()
    # Exact first line: a raw "\n" inside the call ID would end it at "call".
    assert page.table.item(0, 2).text().split("\n")[0] == "call[U+000A]승인 상태: 승인됨"
    dialog.close()
    page.close()


_RAW_HIDDEN = {"Cc", "Cf", "Cs", "Zl", "Zp"}


def _assert_escaped_cell(text: str, lines: int) -> None:
    """Only the UI's own separators may break a cell; no raw hidden chars."""
    parts = text.split("\n")
    assert len(parts) == lines, text
    for part in parts:
        raw = [f"U+{ord(c):04X}" for c in part if unicodedata.category(c) in _RAW_HIDDEN]
        assert not raw, (text, raw)


def test_table_cells_stay_escaped_after_filter_and_every_resize(app) -> None:
    # QA 2026-10-13 Q10: _update_table_columns(force=True) rewrote cells with
    # raw IDs after _apply_filter, undoing the escaped display text.
    row = PublicCallSummary(
        "call-1\u202e\r\n",
        request_id="req\u2028-1\u200b",
        server_id="evil\n신뢰-서버\u2066",
        tool_name="send\n신뢰-서버\u200b\t",
        current_status="UNKNOWN_OUTCOME",
        occurred_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
    other = PublicCallSummary("call-2", server_id="plain", tool_name="lookup")
    page = CallLogPage()
    ready_calls(page, row, other)
    page.show()

    def check(mode: str) -> None:
        app.processEvents()
        QCoreApplication.sendPostedEvents()
        app.processEvents()
        assert page._column_mode == mode
        index = next(i for i, r in enumerate(page._visible_rows) if r is row)
        _assert_escaped_cell(page.table.item(index, 1).text(), 1 if mode == "narrow" else 2)
        _assert_escaped_cell(page.table.item(index, 2).text(), 2 if mode == "full" else 1)
        assert "[U+202E]" in page.table.item(index, 2).text()

    try:
        for width, mode in ((1200, "full"), (540, "medium"), (320, "narrow")):
            page.resize(width, 700)
            check(mode)
            page.search.setText("call")  # _apply_filter -> forced column rewrite
            check(mode)
            page.search.setText("")
            check(mode)
        page.resize(1200, 700)
        check("full")
        assert page.table.item(0, 1).text() == (
            "send[U+000A]신뢰-서버[U+200B][U+0009]\nevil[U+000A]신뢰-서버[U+2066]"
        )
    finally:
        page.close()


def test_csv_uses_only_public_fields_and_neutralizes_formula_prefixes(app) -> None:
    row = PublicCallSummary(
        call_id="  =1+1",
        request_id="-2+2",
        task_id="\r\n@task",
        server_id="\t+server",
        tool_name="\ufeff  =tool,quoted",
        kind=ToolKind.SEND.value,
        initial_decision="ASK",
        reason_code="=raw-reason-must-not-echo",
        approval_status="APPROVED",
        current_status="UNKNOWN_OUTCOME",
        occurred_at=datetime(2026, 10, 11, 11, 30, tzinfo=timezone(timedelta(hours=-4))),
    )
    with pytest.raises(TypeError):
        PublicCallSummary(call_id="safe", raw_body="do-not-export")  # type: ignore[call-arg]
    exported = public_calls_csv((row,))
    parsed = list(csv.reader(io.StringIO(exported)))
    assert len(parsed) == 2
    assert parsed[1][0] == "2026-10-12T00:30:00+09:00"
    assert parsed[1][1] == "'  =1+1"
    assert parsed[1][2] == "'-2+2"
    assert parsed[1][3] == "'\r\n@task"
    assert parsed[1][4] == "'\t+server"
    assert parsed[1][5] == "'\ufeff  =tool,quoted"
    assert parsed[1][7] == "승인 필요"
    assert parsed[1][9] == "승인됨 · 실행 결과와 별개"
    assert parsed[1][10] == "결과 불명 · 수신측 확인 필요"
    assert "=raw-reason-must-not-echo" not in exported
    assert "do-not-export" not in exported
    naive = PublicCallSummary("naive", occurred_at=datetime(2026, 10, 12))
    assert list(csv.reader(io.StringIO(public_calls_csv((naive,)))))[1][0] == "미제공"


def test_csv_button_copies_only_visible_query_rows(app) -> None:
    page = CallLogPage()
    ready_calls(
        page,
        PublicCallSummary("one", tool_name="alpha"),
        PublicCallSummary("two", tool_name="beta"),
    )
    page.search.setText("beta")
    page.csv_button.click()
    text = QApplication.clipboard().text()
    assert "two" in text
    assert "one" not in text
    page.set_calls(Surface(LoadState.STALE, (PublicCallSummary("old"),)))
    assert not page.csv_button.isEnabled()


def test_narrow_medium_and_full_viewports_keep_important_columns(app) -> None:
    page = CallLogPage()
    page.set_calls(
        Surface(
            LoadState.READY,
            (
                PublicCallSummary(
                    "call-id-with-a-long-suffix",
                    request_id="request-id-with-a-long-suffix",
                    server_id="server-a",
                    tool_name="very-long-tool-name-that-must-not-expand-the-table",
                    current_status="UNKNOWN_OUTCOME",
                    occurred_at=datetime(2026, 10, 11, 16, 30, tzinfo=UTC),
                ),
            ),
        )
    )
    page.resize(320, 580)
    page.show()
    app.processEvents()
    assert page.width() == 320
    assert hidden_columns(page) == [False, True, False, True, True, True, False]
    assert page.table.horizontalScrollBar().maximum() == 0
    assert page.table.item(0, 0).text() == "2026-10-12\n01:30:00\nKST"
    assert page.table.item(0, 2).text() == "call-id-with-a-long-suffix"
    assert page.table.item(0, 6).text() == "결과 불명\n수신측 확인 필요"
    assert page._actions_layout.direction() == QBoxLayout.Direction.TopToBottom
    for control in (page.search, page.detail_button, page.csv_button):
        assert control.geometry().right() < page.width()
    page.resize(540, 580)
    app.processEvents()
    assert hidden_columns(page) == [False, False, False, True, True, True, False]
    assert page.table.item(0, 0).text() == "2026-10-12\n01:30:00\nKST"
    assert "server-a" in page.table.item(0, 1).text()
    assert page.table.horizontalScrollBar().maximum() == 0
    page.resize(1030, 700)
    app.processEvents()
    assert page.table.viewport().width() >= page._COMPACT_VIEWPORT_WIDTH
    assert not any(page.table.isColumnHidden(column) for column in range(7))
    assert "request-id-with-a-long-suffix" in page.table.item(0, 2).text()
    assert page.table.horizontalScrollBar().maximum() == 0
    assert page._actions_layout.direction() == QBoxLayout.Direction.LeftToRight
    full_id_width = page.table.columnWidth(2)
    page.resize(1200, 700)
    app.processEvents()
    assert page.table.columnWidth(2) > full_id_width
    assert page.table.horizontalScrollBar().maximum() == 0
    page.resize(320, 580)
    app.processEvents()
    assert page._actions_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert page.table.horizontalScrollBar().maximum() == 0
    page.close()


def test_narrow_keyboard_selection_detail_and_csv_remain_reachable(app) -> None:
    page = CallLogPage()
    page.resize(320, 580)
    page.show()
    page.activateWindow()
    app.processEvents()
    try:
        row = PublicCallSummary("call-1", server_id="server-a")
        ready_calls(page, row)
        requests: list[DetailRequest] = []
        page.detail_requested.connect(requests.append)
        page.table.setFocus()
        app.processEvents()
        QTest.keyClick(page.table, Qt.Key.Key_Down)
        QTest.keyClick(page.table, Qt.Key.Key_Space)
        assert page.detail_button.isEnabled()
        QTest.keyClick(page.table, Qt.Key.Key_Tab)
        assert page.detail_button.hasFocus()
        page.detail_button.setFocus()
        QTest.keyClick(page.detail_button, Qt.Key.Key_Return)
        assert len(requests) == 1
        assert requests[0].call_id == "call-1"
        assert page._detail_dialog is not None
        page._detail_dialog.close()
        page.csv_button.setFocus()
        QTest.keyClick(page.csv_button, Qt.Key.Key_Return)
        assert "call-1" in QApplication.clipboard().text()
        assert "server-a" in QApplication.clipboard().text()
    finally:
        page.close()


def test_store_pages_follow_the_current_query_and_refuse_text_b_would_refuse(app) -> None:
    """B6-05: with a query source the page asks B for each page and never filters locally."""
    page = CallLogPage()
    queries: list[CallLogQuery] = []
    page.query_requested.connect(queries.append)
    try:
        page.refresh()
        assert queries == []  # no source, no query
        page.set_source_connected(True)
        assert page.search.isEnabled() and not page.pager.isHidden()
        page.refresh()
        first = queries[-1]
        assert (first.search, first.page, first.page_size) == ("", 1, CALL_PAGE_SIZE)
        rows = tuple(PublicCallSummary(f"request-{n}") for n in range(CALL_PAGE_SIZE))
        assert page.set_page(CallLogResult(first, rows, 120, None))
        assert page.count_label.text() == "전체 120건 · 현재 페이지 50건"
        assert page.page_label.text() == "1 / 3"
        assert page.next_button.isEnabled() and not page.previous_button.isEnabled()
        page.next_page()
        second = queries[-1]
        assert second.page == 2 and second.token != first.token
        assert not page.set_page(CallLogResult(first, rows, 120, None))  # an older intent
        # The total shrank under page 2: the last page is asked for instead.
        assert not page.set_page(CallLogResult(second, (), 30, None))
        assert queries[-1].page == 1

        page.search.setText("request-1")  # shown as sent; B already searched the page
        page.search.editingFinished.emit()
        searched = queries[-1]
        assert searched.search == "request-1"
        assert page.set_page(CallLogResult(searched, rows[:2], 2, None))
        assert page.table.rowCount() == 2
        assert page.set_error(searched) is True
        assert page.state_message.message.text() == QUERY_UNKNOWN
        assert page.count_label.text() == "총건수 미제공 · 현재 페이지 0건"

        sent = len(queries)
        page.search.setText("요청 1")
        page.search.editingFinished.emit()
        assert len(queries) == sent
        assert page.current_query is None
        assert page.state_message.message.text() == QUERY_TEXT_INVALID
        assert page.search.isEnabled()  # so the text can be corrected

        page.set_source_connected(False)
        assert page.pager.isHidden() and page.current_query is None
        assert page.state_message.message.text() == SOURCE_NOT_CONNECTED
    finally:
        page.close()
