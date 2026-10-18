"""Injected credential UI reports confirmed outcomes and drops input (I9)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from guard_app.services.bridge import CommandEvent, CommandStage, CredentialState
from guard_app.ui.model_settings import ModelCredentialForm


class FailedCredentialBridge(QObject):
    credential_changed = Signal(object)
    command_changed = Signal(object)

    def __init__(self, stage):
        super().__init__()
        self.stage = stage
        self.calls = 0

    def request_credential(self, action, value=None, *, command_id):
        self.calls += 1
        self.command_changed.emit(
            CommandEvent(
                command_id,
                "control",
                f"credential_{action}",
                "model-api",
                self.stage,
                "BACKEND_UNCONFIRMED",
            )
        )


@pytest.mark.parametrize("stage", [CommandStage.UNCONFIRMED, CommandStage.REJECTED])
def test_save_failure_is_not_left_as_pending_and_never_keeps_entered_value(stage):
    app = QApplication.instance() or QApplication([])
    bridge = FailedCredentialBridge(stage)
    form = ModelCredentialForm()
    form.bind(bridge)
    form.set_state(CredentialState(True))
    form.key_input.setText("unit-only-input")
    form.save_button.click()
    app.processEvents()
    assert bridge.calls == 1
    assert not form.key_input.text()
    assert not form._pending
    assert "확인하지 못했어" in form.status.text()
    assert "확인 중" not in form.status.text()
    assert "unit-only-input" not in form.status.text()
    form.close()


def test_unconnected_windows_credential_store_cannot_claim_or_perform_a_save():
    app = QApplication.instance() or QApplication([])
    form = ModelCredentialForm()
    form.key_input.setText("unit-only-input")
    assert not form.save_button.isEnabled()
    form._save()
    app.processEvents()
    assert not form.key_input.text()
    assert "저장하지 않았어" in form.status.text()
    form.close()
