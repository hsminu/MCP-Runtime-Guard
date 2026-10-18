"""Masked model-key entry connected only to backend credential operations."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from guard_app.services.bridge import BackendBridge, CommandEvent, CommandStage, CredentialState

from .widgets import KeyboardActionButton, plain_label


class ModelCredentialForm(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge: BackendBridge | None = None
        self._available = False
        self._pending: set[str] = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(plain_label("모델 API 키", name="sectionTitle"))
        layout.addWidget(
            plain_label(
                "직접 준비한 키를 입력해. Windows 자격증명 관리자 연결만 사용하고 "
                "설정 파일이나 .env에 저장하지 않아. 키 변경은 새 세션부터 적용돼. "
                "사용 중인 API 세션을 정지한 뒤 키를 삭제할 수 있어. Scanner 키와는 별도야.",
                name="secondary",
            )
        )
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setAccessibleName("모델 API 키 입력")
        self.key_input.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.key_input.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.key_input.setMaxLength(8192)
        layout.addWidget(self.key_input)
        self.save_button = KeyboardActionButton("모델 키 저장")
        self.delete_button = KeyboardActionButton("저장된 모델 키 삭제")
        self.refresh_button = KeyboardActionButton("저장 상태 확인")
        for button in (self.save_button, self.delete_button, self.refresh_button):
            layout.addWidget(button)
        self.status = plain_label(name="runtimeMessage")
        layout.addWidget(self.status)
        self.save_button.clicked.connect(self._save)
        self.delete_button.clicked.connect(lambda: self._request("delete"))
        self.refresh_button.clicked.connect(lambda: self._request("status"))
        self.key_input.textChanged.connect(self._refresh)
        self.set_state(CredentialState(False))

    def bind(self, bridge: BackendBridge) -> None:
        self._bridge = bridge
        bridge.credential_changed.connect(self.set_state)
        bridge.command_changed.connect(self._command)
        self._refresh()

    def set_state(self, state: CredentialState) -> None:
        self._available = state.available
        if state.code == "CREDENTIAL_IN_USE":
            self.status.setText(
                "사용 중인 API 세션이 있어 삭제하지 않았어. 먼저 해당 세션을 정지해."
            )
        elif not state.available:
            self.status.setText(
                "Windows 자격증명 저장이 아직 연결되지 않아 모델 키를 저장하거나 읽을 수 없어."
            )
        elif state.stored:
            suffix = f" · 끝 4자리 {state.suffix}" if state.suffix else ""
            self.status.setText(f"Windows 자격증명 관리자에서 저장된 키를 확인했어{suffix}.")
        else:
            self.status.setText("Windows 자격증명 관리자에 저장된 모델 키가 없어.")
        self.delete_button.setEnabled(self._bridge is not None and state.available and state.stored)
        self._refresh()

    def _refresh(self) -> None:
        self.save_button.setEnabled(
            self._bridge is not None and self._available and bool(self.key_input.text().strip())
        )
        self.refresh_button.setEnabled(self._bridge is not None)
        if not self._available:
            self.delete_button.setEnabled(False)

    def _save(self) -> None:
        value = self.key_input.text()
        self.key_input.clear()
        if self._bridge is None or not self._available or not value.strip():
            self.status.setText("자격증명 저장 기능이 연결되지 않아 키를 저장하지 않았어.")
            return
        self.status.setText("저장 요청됨 · 백엔드 확인 중")
        try:
            self._send_request("save", value)
        finally:
            del value

    def _request(self, action: str) -> None:
        self.key_input.clear()
        if self._bridge is not None:
            self._send_request(action)

    def _send_request(self, action: str, value: str | None = None) -> None:
        if self._bridge is None:
            return
        command = uuid4().hex
        self._pending.add(command)
        self._bridge.request_credential(action, value, command_id=command)

    def _command(self, event: CommandEvent) -> None:
        if event.command_id not in self._pending:
            return
        if event.stage in (CommandStage.REJECTED, CommandStage.UNCONFIRMED):
            self.status.setText("자격증명 변경을 확인하지 못했어. 저장 상태를 다시 확인해.")
        if event.stage in (
            CommandStage.REJECTED,
            CommandStage.UNCONFIRMED,
            CommandStage.BACKEND_RETURNED,
        ):
            self._pending.discard(event.command_id)

    def hideEvent(self, event: QHideEvent) -> None:
        self.key_input.clear()
        super().hideEvent(event)
