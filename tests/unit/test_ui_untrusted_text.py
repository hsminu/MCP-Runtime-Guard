"""신뢰하지 않는 텍스트 표시: 일반 텍스트·외부 요청 0(T18 계열), 비가시 문자, 길이 상한."""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel

from guard_app.ui.untrusted_text import (
    UNVERIFIED_NOTICE,
    UntrustedTextView,
    untrusted_display_text,
)


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


def test_markup_and_links_stay_literal_and_never_open_or_fetch(app, monkeypatch):
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
    python_opens = []
    view = UntrustedTextView("모델 답변", show_unverified_notice=True)
    try:
        for scheme in ("http", "https", "file", "javascript"):
            QDesktopServices.setUrlHandler(scheme, probe, "capture")
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: python_opens.append(url))
        base = f"http://127.0.0.1:{server.server_port}"
        payload = (
            f'<b>표지</b><img src="{base}/image"><a href="{base}/link">open</a> '
            f"[md]({base}/md) ![img]({base}/md-image) javascript:void(0)"
        )
        view.set_text(payload)
        view.show()
        app.processEvents()
        assert view.body.toPlainText() == payload
        assert view.body.isReadOnly()
        flags = view.body.textInteractionFlags()
        assert not flags & Qt.TextInteractionFlag.LinksAccessibleByMouse
        assert not flags & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        for label in view.findChildren(QLabel):
            assert label.textFormat() is Qt.TextFormat.PlainText
            assert not label.openExternalLinks()
        app.processEvents()
    finally:
        for scheme in ("http", "https", "file", "javascript"):
            QDesktopServices.unsetUrlHandler(scheme)
        view.close()
        server.shutdown()
        thread.join()
        server.server_close()
    assert requests == []
    assert probe.opened == []
    assert python_opens == []


def test_hidden_characters_are_visualized_but_real_line_breaks_remain():
    shown, omitted = untrusted_display_text("첫 줄​\n둘째‮줄\r\tend\\u200B")
    assert omitted == 0
    lines = shown.split("\n")
    assert lines == ["첫 줄\\u200B", "둘째\\u202E줄\\x0D\\tend\\\\u200B"]


def test_length_cap_counts_original_characters_and_reports_omission(app):
    value = "​" + "가" * 304
    shown, omitted = untrusted_display_text(value, max_chars=300)
    assert omitted == 5
    assert shown == "\\u200B" + "가" * 299

    view = UntrustedTextView("위협 설명", max_chars=300, show_unverified_notice=True)
    view.set_text(value)
    assert view.body.toPlainText() == shown
    assert not view.omission.isHidden()
    assert "5자" in view.omission.text()
    view.set_text("짧음")
    assert view.body.toPlainText() == "짧음"
    assert view.omission.isHidden()
    assert view.omission.text() == ""


def test_unverified_notice_is_opt_in(app):
    marked = UntrustedTextView("위협 설명", show_unverified_notice=True)
    plain = UntrustedTextView("서버 설명")
    assert marked.notice.text() == UNVERIFIED_NOTICE
    assert not marked.notice.isHidden()
    assert marked.body.accessibleDescription() == UNVERIFIED_NOTICE
    assert plain.notice.isHidden()


def test_clear_drops_the_document_copy(app):
    view = UntrustedTextView("모델 답변", max_chars=3)
    view.set_text("TEST_SECRET")
    view.clear()
    assert view.body.toPlainText() == ""
    assert view.omission.isHidden()
    assert view.omission.text() == ""


@pytest.mark.parametrize("value", [None, b"bytes", 3])
def test_non_text_input_is_rejected(value):
    with pytest.raises(TypeError):
        untrusted_display_text(value)


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_invalid_limits_are_rejected(app, limit):
    with pytest.raises(ValueError, match="limit"):
        untrusted_display_text("x", max_chars=limit)
    with pytest.raises(ValueError, match="limit"):
        UntrustedTextView("x", max_chars=limit)
