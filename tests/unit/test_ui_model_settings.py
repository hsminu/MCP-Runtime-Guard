"""Model authentication remains with Codex; the Guard keeps no model secret (I9/I11).

An API key may be typed for Codex's own login (contracts 2026-10-18, A89).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit

from guard_app.ui.model_settings import CodexLoginInfo
from guard_app.ui.window import GuardWindow

from .test_ui_icons_style import closing_widgets


def test_api_key_precedes_chatgpt_controls_and_is_the_default_guidance():
    """A148, I9/I11, T19/T43 UI support: layout never changes login ownership."""
    from PySide6.QtCore import QPoint

    from guard_app.services.bridge import AccountView

    app = QApplication.instance() or QApplication([])
    form = CodexLoginInfo()
    form.set_api_key_sender(lambda _: None)
    form.set_view(AccountView("SIGNED_OUT", browser_available=True))
    form.show()
    app.processEvents()
    with closing_widgets(form):
        key_bottom = form.api_key_input.mapTo(form, QPoint(0, form.api_key_input.height())).y()
        for button in (form.login_button, form.browser_button):
            assert key_bottom <= button.mapTo(form, QPoint(0, 0)).y()
            assert "ChatGPT" in button.text()
        text = " ".join(label.text() for label in form.findChildren(QLabel))
        assert "Codex 로그인" in text
        assert "기본 로그인은 API 키임. 아래에 API 키를 입력하세요." in text
        assert "보조 방법 · ChatGPT 로그인" in text
        assert "Codex가 처리함" in text


def test_both_chatgpt_buttons_still_route_account_commands():
    """A148, I11, T19: secondary login stays on the bridge account path."""
    from guard_app.services.bridge import AccountView, BackendBridge
    from guard_app.ui.runtime import RuntimePanel

    app = QApplication.instance() or QApplication([])

    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    calls = []
    bridge.request_account = lambda action, **kwargs: calls.append((action, kwargs)) or "c"
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    with closing_widgets(window, bridge):
        form = window.codex_login_info
        for button in (form.login_button, form.browser_button):
            form.set_view(AccountView("SIGNED_OUT", browser_available=True))
            assert button.isEnabled()
            button.click()
        assert calls == [("account_login_device", {}), ("account_login_browser", {})]
        assert window.windowTitle().startswith("MCP Runtime Guard")
        app.processEvents()


def test_saved_api_key_notice_does_not_claim_validation():
    """A149, I6/I9, T19/T43: both restored and newly saved keys are unverified."""
    from guard_app.services.bridge import AccountView

    app = QApplication.instance() or QApplication([])
    form = CodexLoginInfo()
    sent = []
    form.set_api_key_sender(sent.append)
    with closing_widgets(form):
        form.set_view(AccountView("API_KEY"))
        expected = "API 키를 저장함. 첫 요청에서 키를 확인함."
        assert form.status.text() == expected
        form.set_view(AccountView("SIGNED_OUT"))
        form.api_key_input.setText("sk-invalid-test-9876")
        form.api_key_button.click()
        assert sent == ["sk-invalid-test-9876"]
        form.set_view(AccountView("API_KEY"))
        assert form.status.text() == "API 키를 저장함 · 끝 4자리 9876. 첫 요청에서 키를 확인함."
        assert form.api_key_input.text() == ""
        assert "sk-invalid" not in form.status.text()
        app.processEvents()


def test_new_session_has_fixed_codex_model_and_settings_keep_only_scanner_key():
    app = QApplication.instance() or QApplication([])
    window = GuardWindow()
    view = window.sessions.current
    assert view.model.text() == "GPT-6.1-Sol · 추론 낮음"
    assert view.model.toolTip() == "Codex gpt-6.1-sol · effort low"
    assert not view.widget.findChildren(QComboBox)
    assert not view.widget.findChildren(QLineEdit)
    assert window.codex_login_info.findChildren(QLineEdit) == [
        window.codex_login_info.api_key_input
    ]
    assert window.scanner_key_form.key_input.echoMode() == QLineEdit.EchoMode.Password
    assert not hasattr(window, "model_credential_form")
    app.processEvents()
    window.close()


def test_session_model_matches_the_pinned_codex_allowlist():
    import json
    from pathlib import Path

    from guard_app.services.bridge import CODEX_MODEL_ID

    lock = Path(__file__).resolve().parents[2] / "config" / "codex" / "codex.lock.json"
    assert json.loads(lock.read_text(encoding="utf-8"))["allowed_models"] == [CODEX_MODEL_ID]


def test_api_key_goes_only_to_the_sender_and_leaves_just_four_characters():
    """A89: masked, sent once, cleared at once; only the last four characters stay."""
    from PySide6.QtCore import Qt

    from guard_app.services.bridge import AccountView

    app = QApplication.instance() or QApplication([])
    form = CodexLoginInfo()
    text = " ".join(label.text() for label in form.findChildren(QLabel))
    assert "기본 로그인은 API 키임" in text
    assert "자격증명은 Codex가 저장·관리함." in text
    assert "gpt-6.1-sol" in text
    assert "준비 중" in text
    assert form.findChildren(QLineEdit) == [form.api_key_input]
    assert form.api_key_input.echoMode() == QLineEdit.EchoMode.Password
    field = form.api_key_input
    assert field.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
    assert not field.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
    form.set_view(AccountView("SIGNED_OUT"))
    assert not field.isEnabled()  # no sender connected yet
    sent: list[str] = []
    form.set_api_key_sender(sent.append)
    assert field.isEnabled()
    assert not form.api_key_button.isEnabled()
    field.setText("  sk-unit-secret-9876  ")
    assert form.api_key_button.isEnabled()
    form.api_key_button.click()
    assert sent == ["sk-unit-secret-9876"]
    assert field.text() == ""
    assert not form.api_key_button.isEnabled()
    form.set_view(AccountView("API_KEY"))
    assert "끝 4자리 9876." in form.status.text()
    labels = " ".join(label.text() for label in form.findChildren(QLabel))
    assert "secret" not in labels
    assert "sk-unit" not in labels
    assert "secret" not in repr(form.view)
    # Signed in: no key can be typed until logout.
    assert not field.isEnabled()
    form.set_view(AccountView("SIGNED_OUT"))
    assert "9876" not in form.status.text()
    form.show()
    field.setText("sk-another-1234")
    form.hide()
    assert field.text() == ""
    for state in ("UNAVAILABLE", "PENDING_DEVICE", "CHATGPT"):
        form.set_view(AccountView(state, login_id="l" if state == "PENDING_DEVICE" else None))
        assert not field.isEnabled()
    field.setText("sk-late-0000")
    form._send_api_key()
    assert sent == ["sk-unit-secret-9876"]
    assert field.text() == ""
    app.processEvents()
    form.close()


def test_window_sends_the_api_key_as_a_codex_account_command():
    from guard_app.services.bridge import AccountView, BackendBridge, BridgeState, LifecycleEvent
    from guard_app.ui.runtime import RuntimePanel

    app = QApplication.instance() or QApplication([])

    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    calls: list[tuple[str, dict[str, object]]] = []
    bridge.request_account = lambda action, **kwargs: calls.append((action, kwargs)) or "c"
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.READY, "READY"))
    login = window.codex_login_info
    login.set_view(AccountView("SIGNED_OUT"))
    login.api_key_input.setText("sk-window-4321")
    login.api_key_button.click()
    assert calls == [("account_login_api_key", {"secret": "sk-window-4321"})]
    assert login.api_key_input.text() == ""
    app.processEvents()
    window.close()
    bridge.close()
