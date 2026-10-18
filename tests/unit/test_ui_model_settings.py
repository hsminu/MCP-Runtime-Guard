"""Model authentication remains with Codex, without collecting a model secret (I9/I11)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit

from guard_app.ui.model_settings import CodexLoginInfo
from guard_app.ui.window import GuardWindow


def test_codex_login_info_explains_both_login_methods_without_secret_fields():
    app = QApplication.instance() or QApplication([])
    form = CodexLoginInfo()
    text = " ".join(label.text() for label in form.findChildren(QLabel))
    assert "ChatGPT 기기 코드와 API 키" in text
    assert "Codex가 Windows 자격증명 관리자에 직접 저장" in text
    assert "gpt-6.1-sol" in text
    assert "준비 중" in text
    assert not form.findChildren(QLineEdit)
    app.processEvents()
    form.close()


def test_new_session_has_fixed_codex_model_and_settings_keep_only_scanner_key():
    app = QApplication.instance() or QApplication([])
    window = GuardWindow()
    view = window.sessions.current
    assert view.model.text() == "Codex · gpt-6.1-sol"
    assert not view.widget.findChildren(QComboBox)
    assert not view.widget.findChildren(QLineEdit)
    assert not window.codex_login_info.findChildren(QLineEdit)
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
