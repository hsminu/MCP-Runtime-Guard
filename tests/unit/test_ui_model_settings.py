"""Model authentication remains with Codex; the Guard keeps no model secret (I9/I11).

An API key may be typed for Codex's own login (contracts 2026-10-18, A89).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit

from guard_app.ui.model_settings import CodexLoginInfo
from guard_app.ui.window import GuardWindow


def test_codex_login_info_explains_both_login_methods_with_one_masked_key_field():
    app = QApplication.instance() or QApplication([])
    form = CodexLoginInfo()
    text = " ".join(label.text() for label in form.findChildren(QLabel))
    assert "ChatGPT 기기 코드와 API 키" in text
    assert "Codex가 Windows 자격증명 관리자에 직접 저장" in text
    assert "gpt-6.1-sol" in text
    assert "준비 중" in text
    assert form.findChildren(QLineEdit) == [form.api_key_input]
    assert form.api_key_input.echoMode() == QLineEdit.EchoMode.Password
    app.processEvents()
    form.close()


def test_new_session_has_fixed_codex_model_and_settings_keep_only_scanner_key():
    app = QApplication.instance() or QApplication([])
    window = GuardWindow()
    view = window.sessions.current
    assert view.model.text() == "Codex · gpt-6.1-sol"
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
    assert form.status.text().endswith("끝 4자리 9876")
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
