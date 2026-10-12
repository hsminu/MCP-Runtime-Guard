"""A35 public call log UI boundaries; no C audit storage is simulated here."""

from __future__ import annotations

import csv
import io
import os
from datetime import UTC, datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from guard_app.ui.call_logs import (
    CallLogPage,
    DetailRequest,
    PublicCallDetail,
    PublicCallStage,
    PublicCallSummary,
    public_calls_csv,
)
from guard_app.ui.models import LoadState, Surface


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def test_list_distinguishes_unavailable_loading_error_and_empty(app) -> None:
    page = CallLogPage()
    assert "조회 연결 대기" in page.state_message.message.text()
    assert not page.csv_button.isEnabled()

    page.set_calls(Surface(LoadState.LOADING))
    assert "불러오는 중" in page.state_message.message.text()
    page.set_calls(Surface(LoadState.ERROR))
    assert "불러오지 못함" in page.state_message.message.text()
    page.set_calls(Surface(LoadState.READY, ()))
    assert "조회된 호출이 없어" in page.state_message.message.text()
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
        kind="SEND",
        boundary_tier="general_external",
        initial_decision="ASK",
        reason_code="POST_DISPATCH_ERROR",
        approval_status="APPROVED",
        current_status="UNKNOWN_OUTCOME",
        occurred_at=datetime(2026, 10, 11, 16, 30, tzinfo=UTC),
    )
    page.set_calls(Surface(LoadState.READY, (row,)))
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
    assert dialog.width() >= 900
    assert "불러오는 중" in dialog.body.toPlainText()

    page.set_detail(
        DetailRequest("different-call", requested[0].token),
        Surface(LoadState.READY, PublicCallDetail(row)),
    )
    assert "불러오는 중" in dialog.body.toPlainText()
    page.set_detail(
        requested[0],
        Surface(
            LoadState.READY,
            PublicCallDetail(
                row,
                (
                    PublicCallStage("DECIDED", "OCCURRED", row.occurred_at),
                    PublicCallStage("DISPATCHING", "OCCURRED"),
                    PublicCallStage("RELEASED", "NOT_RUN"),
                ),
            ),
        ),
    )
    shown = dialog.body.toPlainText()
    assert '<img src="https://example.invalid/leak">' in shown
    assert "2026년 10월 12일 01:30:00 KST" in shown
    assert "결과 불명 · 수신측 확인 필요" in shown
    assert "승인됨 · 실행 결과와 별개" in shown
    assert "결과 공개: 미실행" in shown
    assert "전송 예약: 미제공" in shown
    assert "전체 인자" in shown and "조회 계약 미제공" in shown
    dialog.close()
    assert page._detail_dialog is None
    page.close()


def test_reopened_same_call_ignores_previous_dialog_response(app) -> None:
    page = CallLogPage()
    row = PublicCallSummary("same-call")
    page.set_calls(Surface(LoadState.READY, (row,)))
    requests: list[DetailRequest] = []
    page.detail_requested.connect(requests.append)
    page.select_call("same-call")

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

    page.set_detail(first, Surface(LoadState.READY, PublicCallDetail(row)))
    assert "불러오는 중" in dialog.body.toPlainText()
    page.set_detail(second, Surface(LoadState.READY, PublicCallDetail(row)))
    assert "호출 ID: same-call" in dialog.body.toPlainText()
    dialog.close()


def test_csv_uses_only_public_fields_and_neutralizes_formula_prefixes(app) -> None:
    row = PublicCallSummary(
        call_id="  =1+1",
        request_id="-2+2",
        task_id="\r\n@task",
        server_id="\t+server",
        tool_name="\ufeff  =tool,quoted",
        kind="SEND",
        boundary_tier="general_external",
        initial_decision="ASK",
        reason_code="=raw-reason-must-not-echo",
        approval_status="APPROVED",
        current_status="UNKNOWN_OUTCOME",
        occurred_at=datetime(
            2026, 10, 11, 11, 30, tzinfo=timezone(timedelta(hours=-4))
        ),
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
    assert parsed[1][8] == "승인 필요"
    assert parsed[1][10] == "승인됨 · 실행 결과와 별개"
    assert parsed[1][11] == "결과 불명 · 수신측 확인 필요"
    assert "=raw-reason-must-not-echo" not in exported
    assert "do-not-export" not in exported

    naive = PublicCallSummary("naive", occurred_at=datetime(2026, 10, 12))
    assert list(csv.reader(io.StringIO(public_calls_csv((naive,)))))[1][0] == "미제공"


def test_csv_button_copies_only_visible_query_rows(app) -> None:
    page = CallLogPage()
    page.set_calls(
        Surface(
            LoadState.READY,
            (
                PublicCallSummary("one", tool_name="alpha"),
                PublicCallSummary("two", tool_name="beta"),
            ),
        )
    )
    page.search.setText("beta")
    page.csv_button.click()
    text = QApplication.clipboard().text()
    assert "two" in text
    assert "one" not in text
    page.set_calls(Surface(LoadState.STALE, (PublicCallSummary("old"),)))
    assert not page.csv_button.isEnabled()
