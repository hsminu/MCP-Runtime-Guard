"""Qt checks for the display-only part of A34 / T18."""

from __future__ import annotations

import os

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
    source = "Aа\\u200B\u200b\u202e\x00\t\n한"
    rendered = visible_review_text(source)

    assert "Aа\\u0430" in rendered  # Latin A followed by Cyrillic small a.
    assert "\\\\u200B\\u200B" in rendered  # Literal escape differs from zero-width.
    assert "\\u202E" in rendered
    assert "\\x00" in rendered
    assert "\\t" in rendered
    assert "\\n\n" in rendered
    assert "한\\uD55C" in rendered
    assert "\u200b" not in rendered
    assert "\u202e" not in rendered
    assert source == "Aа\\u200B\u200b\u202e\x00\t\n한"


def test_markup_links_and_remote_images_remain_literal_plain_text(
    app: QApplication,
) -> None:
    probe = UrlProbe()
    QDesktopServices.setUrlHandler("https", probe, "capture(QUrl)")
    dialog = ApprovalDetailDialog()
    try:
        destination = (
            'https://review.invalid/a?<img src="https://review.invalid/pixel">'
        )
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


def test_long_fields_are_complete_and_dialog_releases_its_copies(
    app: QApplication,
) -> None:
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
