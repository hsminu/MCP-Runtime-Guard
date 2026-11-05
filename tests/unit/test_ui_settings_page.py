"""A189: I6/I9/I11, T18/T19/T43 UI support; no real credentials or dispatch."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QScrollArea

from guard_app.services.bridge import AccountView, CommandEvent, CommandStage
from guard_app.ui.model_settings import API_KEY_SENT, UNAVAILABLE, CodexLoginInfo
from guard_app.ui.presentation import (
    CODEX_LOGIN_NOTICES,
    CODEX_LOGIN_TEXT,
    SETTINGS_FILE_NOT_READ,
    SETTINGS_FILE_PENDING,
    SETTINGS_FILE_STATUS,
)
from guard_app.ui.scanner_key import NOT_CONNECTED, SAVE_FAILED, ScannerKeyForm
from guard_app.ui.widgets import Disclosure, SectionCard
from guard_app.ui.window import GuardWindow

from .test_ui_icons_style import app as app
from .test_ui_icons_style import closing_widgets


@pytest.mark.parametrize(
    ("state", "text", "tone"),
    [
        ("UNKNOWN", "확인 전", "neutral"),
        ("SIGNED_OUT", "로그인 안 됨", "neutral"),
        ("API_KEY", "API 키 저장됨", "neutral"),
        ("CHATGPT", "ChatGPT 로그인됨", "ok"),
        ("OTHER", "로그인됨(다른 방식)", "ok"),
        ("PENDING_DEVICE", "로그인 진행 중", "live"),
        ("PENDING_BROWSER", "로그인 진행 중", "live"),
        ("LOGIN_NOT_COMPLETED", "로그인 미완료", "attn"),
        ("FAILED", "요청 실패", "deny"),
        ("UNAVAILABLE", "사용 불가", "neutral"),
    ],
)
def test_account_state_controls_and_pills(app, state, text, tone):
    form = CodexLoginInfo()
    form.set_api_key_sender(lambda _: None)
    form.set_view(
        AccountView(
            state,
            browser_available=True,
            login_id="synthetic-login",
            verification_url="https://example.test/login",
            user_code="FAKE-CODE",
        )
    )
    form.show()
    app.processEvents()
    with closing_widgets(form):
        signed_in = state in ("API_KEY", "CHATGPT", "OTHER")
        pending = state in ("PENDING_DEVICE", "PENDING_BROWSER")
        before = not signed_in and not pending and state != "UNAVAILABLE"
        assert form.status_pill.text() == text
        assert form.status_pill.property("tone") == tone
        for widget in (
            form.api_key_input,
            form.api_key_button,
            form.login_button,
            form.browser_button,
        ):
            assert widget.isVisible() == before
        assert form.logout_button.isVisible() == signed_in
        assert form.read_button.isVisible() == (state != "UNAVAILABLE" and not pending)
        assert form.cancel_button.isVisible() == pending
        assert form.device.isVisible() == (state == "PENDING_DEVICE")
        if state == "PENDING_DEVICE":
            assert "https://example.test/login" in form.device.text()
            assert "FAKE-CODE" in form.device.text()
            assert form.device.objectName() == "mono"
            assert form.device.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        if state == "UNAVAILABLE":
            assert form.status.text() == UNAVAILABLE


def test_codex_input_progress_and_result_keep_no_secret(app, caplog):
    form = CodexLoginInfo()
    observed = []

    def sender(key):
        observed.append((len(key), form.api_key_input.text(), form.input_status.text()))

    form.set_api_key_sender(sender)
    form.set_view(AccountView("SIGNED_OUT"))
    form.show()
    with closing_widgets(form):
        assert form.input_status.isHidden()
        form.api_key_input.setText("synthetic-secret-abcd")
        assert form.input_status.text() == "입력됨 · 아직 보내지 않음"
        assert form.status_pill.text() == "로그인 안 됨"
        form.api_key_button.click()
        assert observed == [(21, "", API_KEY_SENT)]
        assert form.input_status.text() == API_KEY_SENT
        assert form.input_status.isVisible()
        assert not form.api_key_button.isEnabled()
        form.set_view(AccountView("API_KEY"))
        assert form.input_status.isHidden()
        assert form.status_pill.property("tone") == "neutral"
        assert "abcd" in form.status.text()
        assert "synthetic-secret" not in repr(vars(form))
        assert "synthetic-secret" not in caplog.text


def test_notices_browser_availability_and_cleanup_retry(app):
    form = CodexLoginInfo()
    form.show()
    with closing_widgets(form):
        form.set_view(AccountView("SIGNED_OUT", browser_available=False))
        assert form.browser_button.isHidden()
        assert not form.browser_hint.isVisible()
        form.details.set_open(True)
        assert form.browser_hint.isVisible()
        assert form.browser_hint.text() == CODEX_LOGIN_TEXT["browser_unavailable"]
        form.set_view(AccountView("FAILED", notice="BROWSER_OPEN_FAILED"))
        assert form.status.text() == CODEX_LOGIN_NOTICES["BROWSER_OPEN_FAILED"]
        form.set_view(AccountView("SIGNED_OUT", notice="LOGOUT_CLEANUP_FAILED"))
        assert form.logout_button.isVisible()
        assert form.logout_button.isEnabled()
        assert "로그아웃을 다시 누르세요" in form.status.text()
        form.set_view(AccountView("SIGNED_OUT"))
        form.account_command_changed(
            CommandEvent(
                "synthetic-command",
                "control",
                "account_login_api_key",
                "",
                CommandStage.REJECTED,
                "X",
            )
        )
        assert form.status.isVisible()


def test_account_transition_clears_hidden_key_and_cancel_keeps_login_id(app):
    form = CodexLoginInfo()
    form.set_api_key_sender(lambda _: None)
    cancelled = []
    form.cancel_requested.connect(cancelled.append)
    form.show()
    with closing_widgets(form):
        form.set_view(AccountView("SIGNED_OUT"))
        form.api_key_input.setText("synthetic-not-sent-key")
        form.set_view(AccountView("PENDING_DEVICE", login_id="synthetic-login"))
        assert form.api_key_input.text() == ""
        assert form.input_status.isHidden()
        form.cancel_button.click()
        form.cancel_button.click()
        assert cancelled == ["synthetic-login"]
        form.set_view(AccountView("SIGNED_OUT"))
        form.api_key_input.setText("synthetic-unconfirmed-key")
        form.api_key_button.click()
        form.account_command_changed(
            CommandEvent(
                "synthetic-command",
                "control",
                "account_login_api_key",
                "",
                CommandStage.UNCONFIRMED,
                "X",
            )
        )
        assert form.status.text() == CODEX_LOGIN_NOTICES["LOGIN_REQUEST_UNCONFIRMED"]
        assert form.input_status.isHidden()
        assert form.status_pill.property("tone") == "deny"
        assert form.api_key_input.isEnabled()
        assert form.api_key_input.text() == ""


@pytest.mark.parametrize("tail", [None, "abcd", "full-secret-value", "키키키키", "a\nbc"])
def test_scanner_restored_status_is_independent_of_input(app, tail):
    form = ScannerKeyForm(lambda _: True, tail=lambda: tail)
    form.show()
    with closing_widgets(form):
        expected = "저장됨 · 끝 4자리 abcd" if tail == "abcd" else "저장된 키 없음"
        assert form.status_pill.text() == expected
        assert form.key_input.text() == ""
        assert form.status.isHidden()
        assert not form.save_button.isEnabled()
        form.key_input.setText("synthetic-new-key")
        assert form.status.text() == "입력됨 · 저장 전"
        assert form.status_pill.text() == expected
        assert form.save_button.text() == ("새 키로 교체" if tail == "abcd" else "저장")


@pytest.mark.parametrize("success", [True, False])
def test_scanner_saving_then_result(app, success):
    observed = []

    def saver(_key):
        observed.append((form.status.text(), form.key_input.text(), form.save_button.isEnabled()))
        return success

    form = ScannerKeyForm(saver, tail=lambda: "old1")
    form.show()
    with closing_widgets(form):
        form.key_input.setText("synthetic-new-abcd")
        form.save_button.click()
        assert observed == [("저장 중…", "", False)]
        assert form.status_pill.text() == ("저장됨 · 끝 4자리 abcd" if success else "저장 실패")
        assert form.status_pill.property("tone") == ("ok" if success else "deny")
        assert form.status.text() == ("저장됨 · 끝 4자리 abcd" if success else SAVE_FAILED)
        assert form.key_input.text() == ""
        assert "old1" not in " ".join(label.text() for label in form.findChildren(QLabel))


def test_scanner_disconnected_has_disabled_entry_and_fixed_reason(app):
    form = ScannerKeyForm(tail=lambda: "abcd")
    with closing_widgets(form):
        assert form.status_pill.text() == "저장 기능 미연결"
        assert form.status_pill.property("tone") == "neutral"
        assert not form.key_input.isEnabled()
        assert not form.save_button.isEnabled()
        assert form.status.text() == NOT_CONNECTED


@pytest.mark.parametrize("kind", ["codex", "scanner"])
def test_settings_secret_reveal_cannot_export_to_clipboard(app, kind):
    window = GuardWindow(scanner_key_saver=lambda _: True)
    window.codex_login_info.set_api_key_sender(lambda _: None)
    window.codex_login_info.set_view(AccountView("SIGNED_OUT"))
    window.navigate("settings")
    window.show()
    app.processEvents()
    with closing_widgets(window):
        field = (
            window.codex_login_info.api_key_input
            if kind == "codex"
            else window.scanner_key_form.key_input
        )
        field.setText("synthetic-clipboard-secret")
        field.visibility_action.trigger()
        field.selectAll()
        app.clipboard().setText("clipboard-sentinel")
        for key in (Qt.Key.Key_C, Qt.Key.Key_X):
            QTest.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
            assert app.clipboard().text() == "clipboard-sentinel"
        QTest.keyClick(field, Qt.Key.Key_Insert, Qt.KeyboardModifier.ControlModifier)
        assert app.clipboard().text() == "clipboard-sentinel"
        window.navigate("policy")
        assert field.text() == ""


@pytest.mark.parametrize(
    ("code", "text", "tone"),
    [
        (None, "시연 · 읽지 않음", "neutral"),
        ("SETTINGS_LOADED", "정책 확인됨", "ok"),
        ("SETTINGS_REGISTRATION_SAVED", "등록 저장됨", "ok"),
        ("SETTINGS_FILE_DEFAULT", "기본 정책 사용 중", "ok"),
        ("SETTINGS_PROFILE_INVALID", "정책 오류", "deny"),
        ("REGISTRY_UNAVAILABLE", "등록 기록 오류", "deny"),
        ("UNKNOWN", "상태 미확인", "neutral"),
        ([], "상태 미확인", "neutral"),
    ],
)
def test_settings_status_preserves_copy_and_navigation(app, code, text, tone):
    window = GuardWindow()
    with closing_widgets(window):
        assert window.settings_page.settings_pill.text() == "확인 전"
        assert window.settings_file_status.text() == SETTINGS_FILE_PENDING
        window.navigate("runs")
        window.set_settings_status(code)
        assert window.settings_page.settings_pill.text() == text
        assert window.settings_page.settings_pill.property("tone") == tone
        expected = (
            SETTINGS_FILE_NOT_READ
            if code is None
            else (
                SETTINGS_FILE_STATUS.get(code, "설정 파일 상태를 확인하지 못함.")
                if isinstance(code, str)
                else "설정 파일 상태를 확인하지 못함."
            )
        )
        assert window.settings_file_status.text() == expected
        assert window._page == "runs"  # A184: no status moves the page
        window.navigate("policy")
        window.set_settings_status("SETTINGS_FILE_DEFAULT")
        assert window._page == "policy"
        window.navigate("settings")
        window.settings_page.policy_button.click()
        assert window._page == "policy"


def test_three_cards_width_reflow_and_no_initial_validation_errors(app):
    window = GuardWindow(scanner_key_saver=lambda _: True)
    page = window.settings_page
    window.codex_login_info.set_api_key_sender(lambda _: None)
    window.codex_login_info.set_view(AccountView("SIGNED_OUT", browser_available=True))
    window.navigate("settings")
    window.resize(1260, 850)
    window.show()
    with closing_widgets(window):
        for _ in range(3):
            app.processEvents()
        cards = page.findChildren(SectionCard)
        assert len(cards) == 3
        assert all(not card.findChildren(SectionCard) for card in cards)
        assert page.content.maximumWidth() == 760
        assert page.content.width() <= 760
        assert page.content.x() == 24
        assert page.content.layout().spacing() == 16
        assert all(not widget.is_open() for widget in page.findChildren(Disclosure))
        assert not any(
            label.isVisible() and label.property("validationError")
            for label in page.findChildren(QLabel)
        )
        rows = [
            (window.codex_login_info.api_key_input, window.codex_login_info.api_key_button),
            (window.scanner_key_form.key_input, window.scanner_key_form.save_button),
        ]
        for field, button in rows:
            assert 280 <= field.width() <= 560
            assert field.mapTo(page, QPoint()).y() == button.mapTo(page, QPoint()).y()
        window.resize(520, 850)
        QTest.qWait(30)
        assert window.width() == 520
        scroll = window.stack.currentWidget()
        assert isinstance(scroll, QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        for field, button in rows:
            assert 280 <= field.width() <= 560
            assert (
                button.mapTo(page, QPoint()).y() >= field.mapTo(page, QPoint(0, field.height())).y()
            )


@pytest.mark.parametrize("smaller", ["button", "field"])
def test_key_rows_keep_field_and_button_one_height_whatever_the_font(app, smaller):
    """A222: CI's fonts made the button 2px shorter, so it sat 1px lower (354 vs 355)."""
    window = GuardWindow(scanner_key_saver=lambda _: True)
    page = window.settings_page
    window.codex_login_info.set_api_key_sender(lambda _: None)
    window.codex_login_info.set_view(AccountView("SIGNED_OUT", browser_available=True))
    window.navigate("settings")
    rows = [
        (window.codex_login_info.api_key_input, window.codex_login_info.api_key_button),
        (window.scanner_key_form.key_input, window.scanner_key_form.save_button),
    ]
    for field, button in rows:
        # Another font's metrics, forced: one control becomes a few pixels shorter.
        (button if smaller == "button" else field).setStyleSheet("font-size: 8px; padding: 1px;")
    window.resize(1260, 850)
    window.show()
    with closing_widgets(window):
        for _ in range(3):
            app.processEvents()
        for field, button in rows:
            assert abs(field.sizeHint().height() - button.sizeHint().height()) >= 2
            assert field.mapTo(page, QPoint()).y() == button.mapTo(page, QPoint()).y()
            assert field.height() == button.height()
