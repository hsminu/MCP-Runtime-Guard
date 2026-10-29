"""Scanner API 키 입력: 가림, 저장 후 끝 4자리만, 키 비잔존, 저장 연결 전 저장 불가."""

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from guard_app.ui.scanner_key import (
    NOT_CONNECTED,
    SAVE_FAILED,
    SAVE_UNCONFIRMED,
    ScannerKeyForm,
)

KEY = "sk-proj-TEST_SECRET_MARKER_9z7Q"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _visible_texts(form):
    texts = [label.text() for label in form.findChildren(QLabel)]
    texts.extend(edit.text() for edit in form.findChildren(QLineEdit))
    texts.append(repr(form))
    return texts


def test_input_is_masked_without_context_menu(app):
    form = ScannerKeyForm(lambda _key: True)
    assert form.key_input.echoMode() is QLineEdit.EchoMode.Password
    assert form.key_input.contextMenuPolicy() is Qt.ContextMenuPolicy.NoContextMenu
    assert not form.key_input.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
    assert "자격증명 관리자" in form.guidance.text()
    assert "사용 한도" in form.guidance.text()
    form.key_input.setText("abcd")
    form.save_button.click()
    assert form.saved_suffix == ""
    assert "abcd" not in form.status.text()
    form.show()
    form.key_input.setText(KEY)
    form.hide()
    assert form.key_input.text() == ""
    called = []
    form = ScannerKeyForm(lambda key: called.append(key) or True)
    form.key_input.setText("   ")
    assert not form.save_button.isEnabled()
    QTest.keyClick(form.key_input, Qt.Key.Key_Return)
    assert called == []


def test_without_saver_the_key_cannot_be_saved_and_is_not_kept(app):
    form = ScannerKeyForm()
    form.key_input.setText(KEY)
    assert not form.save_button.isEnabled()
    assert form.status.text() == NOT_CONNECTED
    QTest.keyClick(form.key_input, Qt.Key.Key_Return)
    assert form.key_input.text() == ""
    assert form.saved_suffix == ""


def test_successful_save_keeps_only_the_last_four_characters(app, caplog):
    caplog.set_level(logging.DEBUG)
    received = []
    emitted = []
    form = ScannerKeyForm(lambda key: received.append(key) or True)
    form.key_saved.connect(lambda: emitted.append(True))
    form.key_input.setText(f"  {KEY}  ")
    assert form.save_button.isEnabled()
    form.save_button.click()
    assert received == [KEY]
    assert emitted == [True]
    assert form.key_input.text() == ""
    assert form.saved_suffix == "9z7Q"
    assert "9z7Q" in form.status.text()
    assert not any(KEY in text or "MARKER" in text for text in _visible_texts(form))
    assert KEY not in caplog.text


def test_failed_or_ambiguous_save_reports_fixed_text_and_clears_input(app):
    failures = []
    for result in [False, None, "yes", 1]:
        try:
            form = ScannerKeyForm(lambda _key, result=result: result)
            form.key_input.setText(KEY)
            form.save_button.click()
            assert form.status.text() == SAVE_FAILED
            assert form.key_input.text() == ""
            assert form.saved_suffix == ""
            assert not any(KEY in text for text in _visible_texts(form))
        except AssertionError as error:
            failures.append((result, str(error)))
    assert not failures, failures


def test_raising_saver_never_leaves_the_previous_success_on_screen(app):
    calls = []

    def saver(_key: str) -> bool:
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError("MARKER saver failure")
        return True

    form = ScannerKeyForm(saver)
    form.key_input.setText(KEY)
    form._save()
    assert form.saved_suffix == "9z7Q"
    form.key_input.setText("sk-other-WXYZ")
    with pytest.raises(RuntimeError):
        form._save()
    assert form.status.text() == SAVE_UNCONFIRMED
    assert form.saved_suffix == ""
    assert form.key_input.text() == ""
    assert not any("9z7Q" in text or "WXYZ" in text for text in _visible_texts(form))
