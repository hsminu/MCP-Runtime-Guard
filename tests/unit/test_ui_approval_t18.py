"""T18: 승인 상세·목록·실행 검토·승인 알림의 표시 필드와 외부 열기 계수."""

import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit

from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus, ToolKind
from guard_app.services.bridge import ApprovalReview, TaskProjection
from guard_app.services.bridge_commands import review_fields, review_header
from guard_app.ui.approval_detail import (
    ApprovalDetailDialog,
    ApprovalDetailValue,
    visible_review_text,
)
from guard_app.ui.approvals import ApprovalListPage, ApprovalPage, ApprovalSummary
from guard_app.ui.models import AttentionItem
from guard_app.ui.overview import AttentionRow
from guard_app.ui.runtime import RuntimePanel


class DisplayBridge(QObject):
    lifecycle_changed = Signal(object)
    command_changed = Signal(object)
    task_changed = Signal(object)
    review_ready = Signal(object)


class UrlProbe(QObject):
    def __init__(self):
        super().__init__()
        self.opened = []

    @Slot(QUrl)
    def capture(self, url):
        self.opened.append(url.toString())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_all_approval_fields_are_literal_and_never_open_links(app, monkeypatch):
    """T18: 모든 승인 표시 위젯의 일반 텍스트·링크 신호/열기/loopback 요청 0."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    probe = UrlProbe()
    schemes = ("http", "https", "file", "javascript")
    widgets = []
    python_opens, activated, hovered = [], [], []
    try:
        for registered in schemes:
            QDesktopServices.setUrlHandler(registered, probe, "capture")
            assert QDesktopServices.openUrl(QUrl(registered + ":/t18-probe"))
        assert len(probe.opened) == 4  # 네이티브 Qt openUrl 경로도 잡히는 프로브인지 확인.
        probe.opened.clear()
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: python_opens.append(url))
        base = f"http://127.0.0.1:{server.server_port}"
        for scheme in schemes:
            target = {
                "http": base + "/link",
                "https": "https://review.invalid/link",
                "file": "file:///T18-NO-SUCH-FILE",
                "javascript": "javascript:void(0)",
            }[scheme]
            payload = f'<b>표지</b><img src="{base}/image"><a href="{target}">open</a> {target}'
            now = datetime(2026, 10, 13, tzinfo=UTC)
            detail = ApprovalDetailDialog()
            widgets.append(detail)
            value = ApprovalDetailValue(payload, payload.encode())
            detail.show_value(value)
            assert detail.destination_body.toPlainText() == payload
            assert detail.arguments_body.toPlainText() == payload
            page = ApprovalListPage()
            widgets.append(page)
            page.resize(1100, 800)
            page.refresh()
            row = ApprovalSummary(
                approval_id=payload,
                task_id=payload,
                request_id=payload,
                server_id=payload,
                destination_summary=payload,
                status=ApprovalStatus.PENDING,
                created_at=now,
                reason_code=payload,
            )
            assert page.set_page(ApprovalPage(page.current_query, (row,), 1), display_now=now)
            page.table.selectRow(0)
            assert [page.table.item(0, c).text() for c in range(4)] == [
                payload,
                payload,
                payload + "\n" + payload,
                payload,
            ]
            assert page.selection_detail.toPlainText().count(payload) == 5
            assert page.table.item(0, 4).text() == "승인 대기"
            assert "2026년" in page.table.item(0, 5).text()
            for entry in (page.search, page.server_filter):
                entry.setText(payload)
                assert entry.text() == payload
            bridge = DisplayBridge()
            panel = RuntimePanel(bridge)
            widgets.append(panel)
            panel.set_snapshot(TaskProjection(payload, 1, TaskStatus.WAITING_APPROVAL, payload))
            panel._review_command = "review"
            boundary = SimpleNamespace(boundary_id=payload)
            approval = SimpleNamespace(
                request=SimpleNamespace(
                    tool=SimpleNamespace(tool_name=payload, server_id=payload, kind=ToolKind.SEND),
                    server_boundary=boundary,
                    destination=boundary,
                ),
                effective_label=Label.INTERNAL,
                expires_at_utc=now,
            )
            header = review_header(approval)
            fields = review_fields(approval)
            assert header.count(payload) == 4
            bridge.review_ready.emit(
                ApprovalReview("review", payload, payload, 1, header, payload.encode(), fields)
            )
            shown = panel.detail.toPlainText().split("\n")
            assert shown == [
                *(visible_review_text(f"{label}: {value}") for label, value in fields),
                "",
                "전체 인자:",
                visible_review_text(payload),
            ]
            # Four in the fields, one as the arguments; none split across lines.
            assert sum(line.count(visible_review_text(payload)) for line in shown) == 5
            assert payload in panel.summary.text()
            card = AttentionRow(
                AttentionItem(
                    "attention",
                    "approval",
                    server_id=payload,
                    task_id=payload,
                    request_id=payload,
                    reason_code=payload,
                    occurred_at=now,
                    waiting_since=now,
                )
            )
            widgets.append(card)
            texts = [label.text() for label in card.findChildren(QLabel)]
            assert any(text.count(payload) == 3 for text in texts)
            assert "미확인" in texts  # 임의 reason_code 원문은 고정 사유 문구로 대체.
            detail.close()  # 모달 해제 뒤 각 표면을 별도로 클릭한다.
            for widget in widgets:
                if widget is detail:
                    detail.show_value(value)
                else:
                    widget.show()
                app.processEvents()
                for label in widget.findChildren(QLabel):
                    assert label.textFormat() is Qt.TextFormat.PlainText
                    assert not label.openExternalLinks()
                    assert not label.textInteractionFlags() & (
                        Qt.TextInteractionFlag.LinksAccessibleByMouse
                        | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
                    )
                    label.linkActivated.connect(activated.append)
                    label.linkHovered.connect(hovered.append)
                    QTest.mouseMove(label, label.rect().center())
                    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
                for body in widget.findChildren(QPlainTextEdit):
                    text = body.toPlainText()
                    where = (type(widget).__name__, body.accessibleName())
                    assert "<b>" in text, where
                    assert "<img" in text, where
                    assert "<a href=" in text, where
                    html = body.document().toHtml()
                    assert "&lt;b&gt;" in html
                    assert "&lt;img" in html
                    assert "&lt;a href=" in html
                    block = body.document().begin()
                    while block.isValid():
                        it = block.begin()
                        while not it.atEnd():
                            fmt = it.fragment().charFormat()
                            assert not fmt.isAnchor()
                            assert not fmt.isImageFormat()
                            it += 1
                        block = block.next()
                    cursor = body.textCursor()
                    cursor.setPosition(text.find(target))
                    body.setTextCursor(cursor)
                    body.ensureCursorVisible()
                    QTest.mouseClick(
                        body.viewport(), Qt.MouseButton.LeftButton, pos=body.cursorRect().center()
                    )
                bodies = widget.findChildren(QPlainTextEdit)
                if bodies:
                    QTest.keyClick(bodies[-1], Qt.Key.Key_Return)
                if widget is page:
                    for column in range(4):
                        item = page.table.item(0, column)
                        QTest.mouseClick(
                            page.table.viewport(),
                            Qt.MouseButton.LeftButton,
                            pos=page.table.visualItemRect(item).center(),
                        )
                widget.hide()
            QTest.qWait(100)
            assert value.arguments_bytes == payload.encode()
            assert activated == hovered == python_opens == probe.opened == requests == []
            for widget in widgets:
                widget.close()
            widgets.clear()
    finally:
        for widget in widgets:
            widget.close()
        for registered in schemes:
            QDesktopServices.unsetUrlHandler(registered)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()


def test_approval_status_and_times_use_typed_fixed_presentation(app):
    """T18: 상태·만료·시각은 임의 HTML 문자열 대신 enum/시각의 고정 표시만 사용."""
    from dataclasses import replace

    for status in list(ApprovalStatus):
        page = ApprovalListPage()
        try:
            if status is ApprovalStatus.PENDING:
                page.refresh()
            else:
                page.lane_tabs.setCurrentIndex(1)
            now = datetime(2026, 10, 13, tzinfo=UTC)
            row = ApprovalSummary(
                "approval", "task", "request", "server", "destination", status, now
            )
            assert page.set_page(ApprovalPage(page.current_query, (row,), 1), display_now=now)
            page.table.selectRow(0)
            assert "상태:" in page.selection_detail.toPlainText()
            assert "2026년" in page.table.item(0, 5).text()
            for field in ("status", "created_at"):
                invalid = replace(row, **{field: '<b><a href="file:///no-file">status</a></b>'})
                assert not page.set_page(
                    ApprovalPage(page.current_query, (invalid,), 1), display_now=now
                )
                assert page.table.rowCount() == 0
                assert page.selection_detail.toPlainText() == ""
        finally:
            page.close()
