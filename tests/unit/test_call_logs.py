"""A35 public call log UI boundaries; no C audit storage is simulated here."""

from __future__ import annotations

import csv
import io
import unicodedata
from datetime import UTC, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from guard_app.domain.enums import ToolKind
from guard_app.domain.reasons import Reason
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
from guard_app.ui.presentation import REASONS
from guard_app.ui.store_views import call_list_query
from guard_app.ui.style import ui_font

from .test_bridge import app as app


def panel_text(panel):
    return "\n".join(label.text() for label in panel.findChildren(QLabel))


def request_detail(page):
    page._open_selected()
    QTest.qWait(250)


def hidden_columns(page):
    return [page.table.isColumnHidden(column) for column in range(6)]


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
    app.processEvents()
    item = page.table.item(0, 1)
    assert item is not None
    QTest.mouseClick(
        page.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=page.table.visualItemRect(item).center(),
    )
    QTest.qWait(250)
    assert len(requested) == 1
    assert requested[0].call_id == "call-1"
    dialog = page.detail_panel
    assert dialog is not None
    assert dialog.width() <= dialog.screen().availableGeometry().width()
    assert "불러오는 중" in panel_text(dialog)
    show_detail(page, DetailRequest("different-call", requested[0].token), row)
    assert "불러오는 중" in panel_text(dialog)
    show_detail(
        page,
        requested[0],
        row,
        PublicCallStage("DECIDED", "OCCURRED", row.occurred_at),
        PublicCallStage("DISPATCHING", "OCCURRED"),
        PublicCallStage("COMMITTED", "NOT_RUN"),
    )
    shown = panel_text(dialog)
    assert '<img src="https://example.invalid/leak">' in shown
    assert "2026년 10월 12일 01:30:00 KST" in shown
    assert "결과 불명 · 수신측 확인 필요" in shown
    assert "승인됨 · 실행 결과와 별개" in shown
    assert "결과 기록 · 성공 여부는 현재 실행 상태로 확인: 미실행" in shown
    assert "전송 예약: 미제공" in shown
    assert "본문·전체 인자·인증정보·예외 원문은 표시하지 않음." in shown
    page._clear_detail("호출을 선택하세요.")
    assert not page.detail_panel.fields.isVisible()
    page.close()


def test_reopened_same_call_ignores_previous_dialog_response(app) -> None:
    row = PublicCallSummary("same-call")
    page, requests = detail_page(row)
    request_detail(page)
    first = requests[-1]
    assert page.detail_panel is not None
    page._clear_detail("호출을 선택하세요.")
    page.table.clearSelection()
    page.select_call(row.call_id)
    request_detail(page)
    second = requests[-1]
    assert first.call_id == second.call_id
    assert first.token != second.token
    dialog = page.detail_panel
    assert dialog is not None
    show_detail(page, first, row)
    assert "불러오는 중" in panel_text(dialog)
    show_detail(page, second, row)
    assert "호출 ID: same-call" in panel_text(dialog)
    page._clear_detail("호출을 선택하세요.")


def test_repeated_call_details_release_dialogs_and_keep_tokens(app) -> None:
    row = PublicCallSummary("repeat")
    page, requests = detail_page(row)
    for _ in range(100):
        page._open_selected()
        page._detail_timer.stop()
        page._send_detail()
        dialog = page.detail_panel
        assert dialog is not None
        page._clear_detail("호출을 선택하세요.")
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len({request.token for request in requests}) == 100
    assert len(page.findChildren(type(page.detail_panel))) == 1
    request_detail(page)
    current = page.detail_panel
    assert current is not None
    show_detail(page, requests[0], row)
    assert "불러오는 중" in panel_text(current)
    page._clear_detail("호출을 선택하세요.")
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
    request_detail(page)
    dialog = page.detail_panel
    assert dialog is not None
    show_detail(page, requests[-1], row)
    shown = panel_text(dialog)
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
    assert "\n" not in dialog.title.text()
    # Exact first line: a raw "\n" inside the call ID would end it at "call".
    assert page.table.item(0, 5).text().split("\n")[0] == "call[U+000A]승인 상태: 승인됨"
    page._clear_detail("호출을 선택하세요.")
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
        _assert_escaped_cell(page.table.item(index, 1).text(), 2)
        _assert_escaped_cell(page.table.item(index, 5).text(), 1)
        assert "[U+202E]" in page.table.item(index, 5).text()

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
            "send[U+000A]신뢰-서버[U+200B][U+0009]\nevil[U+000A]신뢰-서버[U+2066] · 미확인"
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


def test_server_chosen_names_in_cell_tooltips_are_not_rich_text(app) -> None:
    """2026-10-30 C 점검: Qt는 툴팁 문자열의 태그를 서식으로 그린다(KISA 크로스사이트 스크립트)."""
    row = PublicCallSummary(
        "call-tag",
        server_id="server-a",
        tool_name='<b>approved</b><img src="x">',
        occurred_at=datetime(2026, 10, 30, tzinfo=UTC),
    )
    page = CallLogPage()
    ready_calls(page, row)
    tip = page.table.item(0, 1).toolTip()
    assert tip.startswith("<qt>")
    assert "<b>" not in tip
    assert "<img" not in tip
    assert "&lt;b&gt;approved&lt;/b&gt;" in tip


def test_narrow_medium_and_full_viewports_keep_important_columns(app) -> None:
    page = CallLogPage()
    ready_calls(
        page,
        PublicCallSummary(
            "call-long",
            tool_name="read",
            server_id="server-a",
            approval_status="APPROVED",
            current_status="UNKNOWN_OUTCOME",
            occurred_at=datetime(2026, 10, 11, 16, 30, tzinfo=UTC),
        ),
    )
    page.show()
    for width, hidden, orientation in (
        (380, [False, False, True, True, False, True], Qt.Orientation.Vertical),
        (600, [False, False, False, True, False, True], Qt.Orientation.Vertical),
        (1400, [False, False, False, False, False, True], Qt.Orientation.Horizontal),
    ):
        page.resize(width, 700)
        app.processEvents()
        app.processEvents()
        assert hidden_columns(page) == hidden
        assert page.splitter.orientation() == orientation
        assert page.table.item(0, 0).text() == "10-12\n01:30:00"
        assert "2026년 10월 12일" in page.table.item(0, 0).toolTip()
        assert "+09:00" in page.table.item(0, 0).toolTip()
        assert page.table.item(0, 4).text() == "결과 불명"
        assert "수신측 확인 필요" in page.table.item(0, 4).toolTip()
        if hidden[3]:
            assert "승인됨" in page.table.item(0, 4).toolTip()
    page.call_id_action.setChecked(True)
    assert not page.table.isColumnHidden(5)
    assert page.table.item(0, 5).text() == "call-long"
    page.close()


def test_narrow_keyboard_selection_detail_and_csv_remain_reachable(app) -> None:
    page = CallLogPage()
    page.resize(380, 700)
    page.show()
    page.activateWindow()
    ready_calls(page, PublicCallSummary("call-1", server_id="server-a"))
    requests = []
    page.detail_requested.connect(requests.append)
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Down)
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    QTest.qWait(250)
    assert len(requests) == 1 and requests[0].call_id == "call-1"
    assert "불러오는 중" in panel_text(page.detail_panel)
    page.csv_button.setFocus()
    QTest.keyClick(page.csv_button, Qt.Key.Key_Return)
    assert "call-1" in QApplication.clipboard().text()
    assert "server-a" in QApplication.clipboard().text()
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
        assert page.count_label.text() == "전체 120건 중 이 페이지 50건 · 필터 후 50건 표시"
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
        assert "불러오지 못함" in page.count_label.text()
        assert "0건" not in page.count_label.text()

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


# Not on request or approval rows: OK is no reason, and the Codex session
# reasons only choose the model's text (core.model_messages).
_NOT_ON_ROWS = {
    Reason.OK,
    Reason.SESSION_L3_VIOLATION,
    Reason.SESSION_WATCHDOG_EXCEEDED,
    Reason.SESSION_AUTH_FAILED,
    Reason.SESSION_AUTH_REFRESH_CONFLICT,
    Reason.SESSION_USAGE_LIMIT,
    Reason.SESSION_PROCESS_FAILED,
    Reason.SESSION_PROTOCOL_FAILED,
}


def test_every_reason_a_row_can_carry_has_its_own_text() -> None:
    """#89 added GUARD_CLOSED with no text, so the log showed a normal close as 미확인."""
    assert set(REASONS) == {reason.value for reason in set(Reason) - _NOT_ON_ROWS}
    row = PublicCallSummary("call-1", reason_code="GUARD_CLOSED", reason_codes=("GUARD_CLOSED",))
    assert "Guard 정상 종료로 실행하지 않음" in public_calls_csv((row,))


def test_settings_change_reason_has_definition_and_settings_text() -> None:
    assert REASONS["TOOL_FINGERPRINT_CHANGED"] == "도구 정의·설정 변경"


@pytest.mark.parametrize(
    ("column", "field", "code", "word", "tone"),
    [
        (2, "initial_decision", "ALLOW", "허용", "ok"),
        (2, "initial_decision", "ASK", "승인 필요", "attn"),
        (2, "initial_decision", "DENY", "차단", "deny"),
        (2, "initial_decision", None, "미확인", "neutral"),
        (3, "approval_status", "NOT_REQUIRED", "승인 불필요", "neutral"),
        (3, "approval_status", "PENDING", "승인 대기", "attn"),
        (3, "approval_status", "APPROVED", "승인됨", "ok"),
        (3, "approval_status", "CONSUMED", "승인 사용됨", "ok"),
        (3, "approval_status", "REJECTED", "거절됨", "deny"),
        (3, "approval_status", "EXPIRED", "만료됨", "neutral"),
        (3, "approval_status", "CANCELLED", "취소됨", "neutral"),
        (3, "approval_status", "INVALIDATED", "무효화됨", "neutral"),
        (3, "approval_status", None, "미확인", "neutral"),
        (4, "current_status", "DECIDED", "판정 완료", "neutral"),
        (4, "current_status", "WAITING_APPROVAL", "승인 대기", "attn"),
        (4, "current_status", "DISPATCHING", "실행 중", "live"),
        (4, "current_status", "SUCCEEDED", "완료", "ok"),
        (4, "current_status", "WITHHELD", "결과 미공개", "attn"),
        (4, "current_status", "FAILED", "실패", "deny"),
        (4, "current_status", "UNKNOWN_OUTCOME", "결과 불명", "attn"),
        (4, "current_status", "DENIED", "차단", "deny"),
        (4, "current_status", None, "미확인", "neutral"),
    ],
)
def test_three_stages_keep_accessible_words_and_separate_tones(
    app, column, field, code, word, tone
):
    """I5/I9, T18: public stage meanings never collapse into one success flag."""
    page = CallLogPage()
    row = PublicCallSummary("call", **{field: code})
    ready_calls(page, row)
    item = page.table.item(0, column)
    assert item.data(Qt.ItemDataRole.DisplayRole) == word
    assert item.data(Qt.ItemDataRole.UserRole) == tone
    page.select_call("call")
    request_detail(page)
    show_detail(page, page._detail_request, row)
    pill = page.detail_panel.pills[column - 2]
    assert pill.text() == word and pill.property("tone") == tone
    page.close()


def test_default_columns_short_names_and_manual_width_survive_resize(app):
    page = CallLogPage()
    ready_calls(
        page, PublicCallSummary("a", tool_name="read"), PublicCallSummary("b", tool_name="send")
    )
    page.resize(1260, 850)
    page.show()
    app.processEvents()
    assert hidden_columns(page) == [False, False, False, False, False, True]
    assert [page.table.horizontalHeaderItem(i).text() for i in range(5)] == [
        "발생 시각",
        "도구 / 서버",
        "최초 판정",
        "승인 상태",
        "현재 상태",
    ]
    metrics = QFontMetrics(ui_font(14, QFont.Weight.DemiBold))
    assert page.table.columnWidth(1) >= max(120, metrics.horizontalAdvance("send") + 24)
    header = page.table.horizontalHeader()
    assert header.sectionResizeMode(1) == header.ResizeMode.Interactive
    from PySide6.QtCore import QPoint

    edge = header.sectionViewportPosition(1) + header.sectionSize(1) - 1
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(edge, 10))
    QTest.mouseMove(header.viewport(), QPoint(edge + 40, 10))
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(edge + 40, 10))
    assert 1 in page._manual_widths
    resized = page.table.columnWidth(1)
    page.resize(1400, 850)
    app.processEvents()
    assert page.table.columnWidth(1) == resized
    page.call_id_action.trigger()
    assert not page.table.isColumnHidden(5)
    assert page.table.columnWidth(1) == resized
    page.close()


def test_selection_debounce_invalidates_late_reply_before_next_request(app):
    a, b = PublicCallSummary("a"), PublicCallSummary("b")
    page = CallLogPage()
    ready_calls(page, a, b)
    requests = []
    page.detail_requested.connect(requests.append)
    page.select_call("a")
    QTest.qWait(250)
    first = requests[-1]
    page.select_call("b")
    show_detail(page, first, a)
    assert "불러오는 중" in page.detail_panel.message.text()
    assert page.detail_panel.identifiers[0].text() == ""
    QTest.qWait(250)
    second = requests[-1]
    assert second.call_id == "b" and second.token != first.token
    show_detail(page, second, a)  # Matching token, wrong payload ID also refused.
    assert "불러오는 중" in page.detail_panel.message.text()
    show_detail(page, second, b)
    before = panel_text(page.detail_panel)
    show_detail(page, first, a)
    assert panel_text(page.detail_panel) == before
    ready_calls(page, a)
    assert page.detail_panel.message.text() == "선택한 호출이 현재 결과에 없음"
    show_detail(page, second, b)
    assert page.detail_panel.identifiers[0].text() == ""
    page.close()


def test_detail_states_plain_text_all_reasons_and_identifier_only_copy(app):
    row = PublicCallSummary(
        "<call>",
        request_id="request\n-id",
        task_id="task-id",
        reason_codes=("ACCESS_DENY", "ACCESS_ERROR"),
    )
    page, requests = detail_page(row)
    request_detail(page)
    request = requests[-1]
    for state, text in (
        (LoadState.LOADING, "불러오는 중"),
        (LoadState.ERROR, "불러오지 못함"),
        (LoadState.UNAVAILABLE, "미연결"),
    ):
        page.set_detail(request, Surface(state))
        assert text in page.detail_panel.message.text()
    page.set_detail(request, Surface(LoadState.READY))
    assert page.detail_panel.message.text() == "해당 호출 없음"
    page.set_detail(request, Surface(LoadState.STALE, PublicCallDetail(row)))
    assert "이전 조회 결과" in page.detail_panel.message.text()
    assert all(REASONS[r] in panel_text(page.detail_panel) for r in row.reason_codes)
    for label in page.detail_panel.findChildren(QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
        assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    for button, value in zip(
        page.detail_panel.copy_buttons, (row.call_id, row.request_id, row.task_id), strict=True
    ):
        button.click()
        assert QApplication.clipboard().text() == value
    for stages, text in (
        (None, "단계별 감사 조회 계약 미제공"),
        ((), "확인된 단계가 없음"),
        ((PublicCallStage("OBSERVED", "unknown"),), "상태 미확인"),
    ):
        page.set_detail(request, Surface(LoadState.READY, PublicCallDetail(row, stages)))
        assert text in page.detail_panel.stages.text()
    with pytest.raises(TypeError):
        PublicCallDetail(row, raw_result="private")
    page.close()


def test_server_filter_queries_store_and_invalid_server_never_emits(app):
    page = CallLogPage()
    queries = []
    page.query_requested.connect(queries.append)
    page.set_known_servers(["registered", "invalid__server"])
    page.set_source_connected(True)
    page.refresh()
    page.set_page(CallLogResult(queries[-1], (PublicCallSummary("call", server_id="observed"),), 1))
    assert {page.server_filter.itemData(i) for i in range(page.server_filter.count())} == {
        "",
        "registered",
        "invalid__server",
        "observed",
    }
    page.server_filter.setCurrentIndex(page.server_filter.findData("registered"))
    query = queries[-1]
    assert query.server_id == "registered" and query.page == 1
    backend = call_list_query(query)
    assert backend is not None and backend.server_id == "registered"
    count = len(queries)
    page.server_filter.setCurrentIndex(page.server_filter.findData("invalid__server"))
    assert len(queries) == count and page._state is LoadState.ERROR
    assert call_list_query(CallLogQuery(100, "", 1, server_id="invalid__server")) is None
    assert "0건" not in page.count_label.text()
    page.close()


@pytest.mark.parametrize("connected", [False, True])
def test_status_period_and_csv_are_local_to_received_page(app, connected):
    page = CallLogPage()
    queries = []
    page.query_requested.connect(queries.append)
    now = datetime.now(UTC)
    rows = (
        PublicCallSummary("recent", server_id="a", initial_decision="ALLOW", occurred_at=now),
        PublicCallSummary(
            "old", server_id="b", initial_decision="DENY", occurred_at=now - timedelta(days=8)
        ),
        PublicCallSummary("no-time", initial_decision="ALLOW"),
    )
    if connected:
        page.set_source_connected(True)
        page.refresh()
        assert "0건" not in page.count_label.text()
        page.set_page(CallLogResult(queries[-1], rows, 150))
    else:
        ready_calls(page, *rows)
    sent = len(queries)
    assert page.scope_label.text() == (
        "상태·기간은 현재 페이지에만 적용됨"
        if connected
        else "모든 필터는 현재 표시된 행에만 적용됨"
    )
    page.status_filter.setCurrentIndex(page.status_filter.findData("ALLOW"))
    assert page.table.rowCount() == 2
    for period in ("hour", "today", "week"):
        page.period_filter.setCurrentIndex(page.period_filter.findData(period))
        assert page.table.rowCount() == 1
        assert "시각 없음 1건 제외" in page.count_label.text()
    assert len(queries) == sent
    assert page.csv_button.text() == "표시된 1행 CSV 복사"
    page.csv_button.click()
    assert QApplication.clipboard().text() == public_calls_csv(rows[:1])
    assert page.copy_message.text() == "1행 복사함"
    QTest.qWait(2100)
    assert page.copy_message.text() == ""
    if connected:
        assert page.count_label.text().startswith("전체 150건 중 이 페이지 3건 · 필터 후 1건 표시")
        assert "전체 결과가 아님" in page.csv_button.toolTip()
    else:
        page.period_filter.setCurrentIndex(0)
        page.status_filter.setCurrentIndex(0)
        page.server_filter.setCurrentIndex(page.server_filter.findData("b"))
        assert page._visible_rows == [rows[1]] and not queries
    page.close()


def test_period_uses_kst_today_and_excludes_missing_or_naive_time(app, monkeypatch):
    from guard_app.ui import call_logs

    now = datetime(2026, 10, 29, 15, 30, tzinfo=UTC)  # Oct 30 00:30 KST

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(call_logs, "datetime", Clock)
    page = CallLogPage()
    ready_calls(
        page,
        PublicCallSummary("today", occurred_at=now),
        PublicCallSummary("yesterday", occurred_at=now - timedelta(minutes=45)),
        PublicCallSummary("week", occurred_at=now - timedelta(days=6)),
        PublicCallSummary("old", occurred_at=now - timedelta(days=8)),
        PublicCallSummary("missing"),
        PublicCallSummary("naive", occurred_at=now.replace(tzinfo=None)),
    )
    for period, expected in (
        ("hour", ["today", "yesterday"]),
        ("today", ["today"]),
        ("week", ["today", "yesterday", "week"]),
    ):
        page.period_filter.setCurrentIndex(page.period_filter.findData(period))
        assert [row.call_id for row in page._visible_rows] == expected
        assert "시각 없음 2건 제외" in page.count_label.text()
    page.close()


def test_fast_selection_coalesces_and_table_scroll_keeps_header(app):
    page = CallLogPage()
    page.resize(780, 850)
    ready_calls(
        page, *(PublicCallSummary(str(n), tool_name="read", kind="read") for n in range(50))
    )
    page.show()
    app.processEvents()
    requests = []
    page.detail_requested.connect(requests.append)
    page.select_call("0")
    page.select_call("1")
    page.select_call("2")
    assert not requests
    QTest.qWait(250)
    assert len(requests) == 1 and requests[0].call_id == "2"
    assert page.table.item(0, 1).text().endswith(" · 읽기")
    assert page.table.verticalScrollBar().maximum() > 0
    header = page.table.horizontalHeader()
    before = header.geometry()
    page.table.verticalScrollBar().setValue(page.table.verticalScrollBar().maximum())
    assert header.geometry() == before and header.isVisible()
    assert page.splitter.orientation() == Qt.Orientation.Vertical
    page.resize(1260, 850)
    app.processEvents()
    assert page.splitter.orientation() == Qt.Orientation.Horizontal
    page.close()
