"""H01: public IDs and names never reach a label or cell with raw hidden chars."""

from __future__ import annotations

import os
import unicodedata
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from guard_app.domain.enums import ApprovalStatus
from guard_app.ui.approvals import ApprovalListPage, ApprovalPage, ApprovalSummary
from guard_app.ui.models import (
    AttentionItem,
    LoadState,
    RecentCall,
    ServerSummary,
    Surface,
)
from guard_app.ui.overview import AttentionCard, RecentCallCard, ServerCard
from guard_app.ui.presentation import public_display_text
from guard_app.ui.servers import ServerDetailDialog, ServerListPage

_RAW_HIDDEN = {"Cc", "Cf", "Cs", "Zl", "Zp"}
_EVIL_ID = "docs\n신뢰-서버\u202e\u200b"
_EVIL_NAME = "Docs\u2028Internal\u2066"
NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def _raw(text: str) -> list[str]:
    return [f"U+{ord(c):04X}" for c in text if unicodedata.category(c) in _RAW_HIDDEN]


def _assert_widget_texts_escaped(widget: QWidget) -> None:
    texts = [label.text() for label in widget.findChildren(QLabel)]
    texts += [button.accessibleName() for button in widget.findChildren(QPushButton)]
    texts.append(widget.windowTitle())
    for text in texts:
        assert not _raw(text), (text, _raw(text))


def test_public_display_text_marks_hidden_and_blank_lookalike_chars() -> None:
    assert public_display_text(_EVIL_ID) == "docs[U+000A]신뢰-서버[U+202E][U+200B]"
    assert public_display_text("a\u3164b\u2800") == "a[U+3164]b[U+2800]"
    assert public_display_text("보고서_v2") == "보고서_v2"
    assert public_display_text("") == "미제공"


def test_overview_cards_escape_ids_and_names(app: QApplication) -> None:
    server = ServerCard(ServerSummary(_EVIL_ID, _EVIL_NAME))
    attention = AttentionCard(
        AttentionItem(
            "item", "approval", server_id=_EVIL_ID, task_id="task\r1", request_id="r\u202e"
        )
    )
    recent = RecentCallCard(RecentCall("call\n1", _EVIL_ID, "send\u200b\nmail"))
    try:
        for card in (server, attention, recent):
            _assert_widget_texts_escaped(card)
        labels = {label.text() for label in server.findChildren(QLabel)}
        assert public_display_text(_EVIL_ID) in labels
        assert public_display_text(_EVIL_NAME) in labels
        assert server.link.accessibleName().startswith(public_display_text(_EVIL_ID))
    finally:
        for card in (server, attention, recent):
            card.deleteLater()


def test_server_list_and_detail_escape_ids_and_names(app: QApplication) -> None:
    summary = ServerSummary(_EVIL_ID, _EVIL_NAME)
    page = ServerListPage()
    page.set_servers(Surface(LoadState.READY, (summary,)))
    dialog = ServerDetailDialog(summary)
    try:
        cell = page.table.item(0, 0).text()
        assert cell == f"{public_display_text(_EVIL_ID)}\n{public_display_text(_EVIL_NAME)}"
        _assert_widget_texts_escaped(dialog)
        dialog.set_server(ServerSummary(_EVIL_ID + "\t", _EVIL_NAME))
        _assert_widget_texts_escaped(dialog)
    finally:
        dialog.close()
        page.close()


def test_approval_table_cells_escape_ids_and_destination(app: QApplication) -> None:
    page = ApprovalListPage()
    requests = []
    page.query_requested.connect(requests.append)
    page.refresh()
    row = ApprovalSummary(
        approval_id="approval\n1",
        task_id="task\u202e1",
        request_id="request\u200b1",
        server_id=_EVIL_ID,
        destination_summary="https://evil\u2028.example",
        status=ApprovalStatus.PENDING,
        created_at=NOW - timedelta(minutes=1),
    )
    try:
        assert page.set_page(ApprovalPage(requests[-1], (row,), 1), display_now=NOW)
        cells = [page.table.item(0, column).text() for column in range(4)]
        assert cells[0] == "approval[U+000A]1"
        assert cells[1] == public_display_text(_EVIL_ID)
        assert cells[2] == "task[U+202E]1\nrequest[U+200B]1"
        assert cells[3] == "https://evil[U+2028].example"
        for cell in cells:
            assert not _raw(cell.replace("\n", "")), cell
    finally:
        page.close()
