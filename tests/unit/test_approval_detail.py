"""Qt checks for the display-only part of A34 / T18."""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from guard_app.ui.approval_detail import (
    ApprovalDetailDialog,
    ApprovalDetailValue,
    visible_review_text,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


class UrlProbe(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.opened: list[QUrl] = []

    @Slot(QUrl)
    def capture(self, url: QUrl) -> None:
        self.opened.append(url)


def test_controls_bidi_and_confusables_have_unambiguous_visible_codes() -> None:
    """T18: 제어·혼동 문자 표시의 구분과 원문 보존."""
    source = "Aа\\u200B\u200b\u202e\x00\t\n한"
    rendered = visible_review_text(source)

    assert "Aа\\u0430" in rendered  # Latin A followed by Cyrillic small a.
    assert "\\\\u200B\\u200B" in rendered  # Literal escape differs from zero-width.
    assert "\\u202E" in rendered
    assert "\\x00" in rendered
    assert "\\t" in rendered
    assert "\\n\n" in rendered
    assert rendered.endswith("한")
    assert visible_review_text("분기 보고서 한글") == "분기 보고서 한글"
    assert "\u200b" not in rendered
    assert "\u202e" not in rendered
    assert source == "Aа\\u200B\u200b\u202e\x00\t\n한"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # Conjoining jamo compose a syllable pixel-identical to precomposed 홍.
        ('{"to":"\u1112\u1169\u11bc길동"}', '{"to":"\u1112\\u1112\u1169\\u1169\u11bc\\u11BC길동"}'),
        ("\ua960\ud7b0", "\ua960\\uA960\ud7b0\\uD7B0"),
        # Blank-looking letters: codepoint only, like other invisible text.
        ('{"to":"홍길동\u1160"}', '{"to":"홍길동\\u1160"}'),
        ('{"to":"홍길동\u115f"}', '{"to":"홍길동\\u115F"}'),
        ("a\u3164b\uffa0c", "a\\u3164b\\uFFA0c"),
        ('{"memo":"a\u2800b"}', '{"memo":"a\\u2800b"}'),
        # Separator lookalikes inside URLs keep glyph plus codepoint.
        (
            "https://bank.example.com\u2215login.evil.test",
            "https://bank.example.com\u2215\\u2215login.evil.test",
        ),
        ("https://a.com\u2044x", "https://a.com\u2044\\u2044x"),
        ("https://evil\u3002com", "https://evil\u3002\\u3002com"),
        ("https://evil\uff61com", "https://evil\uff61\\uFF61com"),
        ("https://a\u2010b.test", "https://a\u2010\\u2010b.test"),
    ],
)
def test_composed_and_punctuation_lookalikes_are_marked(source: str, expected: str) -> None:
    # QA 2026-10-13 Q11: these rendered identical to ordinary text, unmarked.
    assert visible_review_text(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        '{"file":"보고서_v2.pdf"}',
        '{"to":"홍길동@corp.example"}',
        "https://corp.example/분기/보고서",
    ],
)
def test_precomposed_korean_mixed_with_ascii_is_not_tagged(source: str) -> None:
    # QA 2026-10-13 Q11: every precomposed syllable was followed by \\uXXXX.
    assert visible_review_text(source) == source


def test_korean_token_still_marks_a_non_hangul_lookalike() -> None:
    # Cyrillic \u0430 next to Hangul is still a mixed-script lookalike of Latin a.
    assert visible_review_text("홍길동\u0430") == "홍길동\u0430\\u0430"


def test_cyrillic_hostname_is_visible_but_plain_korean_remains_readable():
    assert "\\u0430" in visible_review_text("https://аррӏе.com/분기")
    assert visible_review_text("분기 보고서") == "분기 보고서"


def test_markup_links_and_remote_images_remain_literal_plain_text(
    app: QApplication,
) -> None:
    """T18: HTML·링크·원격 이미지 일반 텍스트와 HTTPS 외부 열기 0."""
    probe = UrlProbe()
    QDesktopServices.setUrlHandler("https", probe, "capture(QUrl)")
    dialog = ApprovalDetailDialog()
    try:
        destination = 'https://review.invalid/a?<img src="https://review.invalid/pixel">'
        arguments = (
            b'{"body":"<a href=\\"https://review.invalid/approve\\">'
            b'approve</a><img src=\\"https://review.invalid/pixel\\">"}'
        )
        value = ApprovalDetailValue(destination, arguments)
        dialog.show_value(value)
        app.processEvents()

        assert isinstance(dialog.destination_body, QPlainTextEdit)
        assert isinstance(dialog.arguments_body, QPlainTextEdit)
        assert dialog.destination_body.isReadOnly()
        assert dialog.arguments_body.isReadOnly()
        assert destination in dialog.destination_body.toPlainText()
        assert "<a href=" in dialog.arguments_body.toPlainText()
        assert "<img src=" in dialog.arguments_body.toPlainText()
        assert "&lt;img" in dialog.destination_body.document().toHtml()
        assert "&lt;a" in dialog.arguments_body.document().toHtml()

        for body in (dialog.destination_body, dialog.arguments_body):
            cursor = body.textCursor()
            cursor.setPosition(body.toPlainText().find("https://") + 8)
            QTest.mouseClick(
                body.viewport(),
                Qt.MouseButton.LeftButton,
                pos=body.cursorRect(cursor).center(),
            )
        app.processEvents()
        assert probe.opened == []
        assert value.arguments_bytes == arguments
        assert value.destination == destination
        assert "review.invalid" not in repr(value)
    finally:
        dialog.close()
        QDesktopServices.unsetUrlHandler("https")


def test_markup_and_link_clicks_make_no_loopback_http_requests(
    app: QApplication,
) -> None:
    """T18: 상세 필드의 자동 이미지 요청·링크 클릭 loopback HTTP 0."""
    requests: list[str] = []

    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), RequestHandler)
    server_thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    server_thread.start()
    probe = UrlProbe()
    QDesktopServices.setUrlHandler("http", probe, "capture(QUrl)")
    dialog = ApprovalDetailDialog()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        destination = (
            f'<img src="{base_url}/destination-image">'
            f'<a href="{base_url}/destination-link">open</a>'
        )
        arguments = (
            f'{{"html":"<img src=\\"{base_url}/arguments-image\\">'
            f'<a href=\\"{base_url}/arguments-link\\">open</a>"}}'
        ).encode()
        dialog.show_value(ApprovalDetailValue(destination, arguments))
        app.processEvents()
        QTest.qWait(250)

        for body in (dialog.destination_body, dialog.arguments_body):
            assert isinstance(body, QPlainTextEdit)
            assert base_url in body.toPlainText()
            assert "&lt;img" in body.document().toHtml()
            assert "&lt;a" in body.document().toHtml()
            cursor = body.textCursor()
            cursor.setPosition(body.toPlainText().find(base_url) + 7)
            click_pos = body.cursorRect(cursor).center()
            assert body.viewport().rect().contains(click_pos)
            QTest.mouseClick(
                body.viewport(),
                Qt.MouseButton.LeftButton,
                pos=click_pos,
            )
        QTest.keyClick(dialog.arguments_body, Qt.Key.Key_Return)

        # Give Qt's event loop time to issue any deferred resource fetch.
        QTest.qWait(250)
    finally:
        dialog.close()
        app.processEvents()
        QDesktopServices.unsetUrlHandler("http")
        server.shutdown()
        server_thread.join(timeout=1)
        server.server_close()

    assert not server_thread.is_alive()
    assert requests == []
    assert probe.opened == []


def test_long_fields_are_complete_and_dialog_releases_its_copies(
    app: QApplication,
) -> None:
    """T18: 전체 필드 표시와 닫기 후 표시 사본 해제."""
    host = QWidget()
    layout = QVBoxLayout(host)
    opener = QPushButton("상세")
    layout.addWidget(opener)
    host.show()
    app.processEvents()
    opener.setFocus()

    destination = "target:" + "d" * 70_000 + ":destination-end"
    arguments = b'{"payload":"' + b"a" * 70_000 + b'","end":"args-end"}'
    value = ApprovalDetailValue(destination, arguments)
    dialog = ApprovalDetailDialog(host)
    try:
        dialog.show_value(value, return_focus=opener)
        app.processEvents()
        assert dialog.destination_body.hasFocus()
        assert dialog.destination_body.toPlainText() == destination
        assert dialog.arguments_body.toPlainText() == arguments.decode("utf-8")
        assert dialog.destination_body.toPlainText().endswith(":destination-end")
        assert dialog.arguments_body.toPlainText().endswith('"args-end"}')

        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        QTest.qWait(10)
        assert not dialog.isVisible()
        assert dialog.destination_body.toPlainText() == ""
        assert dialog.arguments_body.toPlainText() == ""
        assert host.focusWidget() is opener
        assert value.destination == destination
        assert value.arguments_bytes == arguments
    finally:
        dialog.close()
        host.close()


def test_invalid_utf8_never_leaves_stale_detail_visible(app: QApplication) -> None:
    dialog = ApprovalDetailDialog()
    try:
        dialog.show_value(ApprovalDetailValue("first", b'{"ok":true}'))
        app.processEvents()
        with pytest.raises(ValueError, match="not valid UTF-8"):
            dialog.show_value(ApprovalDetailValue("second", b"\xff"))
        assert not dialog.isVisible()
        assert dialog.destination_body.toPlainText() == ""
        assert dialog.arguments_body.toPlainText() == ""
    finally:
        dialog.close()


def test_short_owner_scrolls_both_complete_plain_fields(app: QApplication) -> None:
    """T18: 작은 화면에서도 전체 필드를 일반 텍스트로 유지."""
    owner = QWidget()
    owner.resize(320, 240)
    owner.show()
    destination = "https://review.invalid/" + "d" * 512 + "/destination-end"
    arguments = ("synthetic-line:" + "a" * 160 + "\n") * 60 + "args-end"
    detail = ApprovalDetailDialog(owner)
    try:
        detail.show_value(ApprovalDetailValue(destination, arguments.encode("utf-8")))
        app.processEvents()
        scroll = detail.findChild(QScrollArea, "approvalDetailScroll")
        assert scroll is not None
        assert detail.width() <= owner.width()
        assert detail.height() <= owner.height()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() > 0
        assert detail.destination_body.height() >= 128
        assert detail.arguments_body.height() >= 128
        assert detail.destination_body.toPlainText() == destination
        assert detail.arguments_body.toPlainText() == visible_review_text(arguments)
        assert detail.destination_body.horizontalScrollBar().maximum() > 0
        assert detail.arguments_body.verticalScrollBar().maximum() > 0

        QTest.keyClick(detail.destination_body, Qt.Key.Key_Tab)
        app.processEvents()
        assert detail.arguments_body.hasFocus()
        arguments_top = detail.arguments_body.mapTo(scroll.viewport(), QPoint(0, 0)).y()
        assert arguments_top < scroll.viewport().height()
        assert arguments_top + detail.arguments_body.height() > 0
        QTest.keyClick(detail.arguments_body, Qt.Key.Key_Backtab)
        app.processEvents()
        assert detail.destination_body.hasFocus()
        detail.close()
        assert detail.destination_body.toPlainText() == ""
        assert detail.arguments_body.toPlainText() == ""
    finally:
        detail.close()
        owner.close()
