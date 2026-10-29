"""DESC-01 / B3.13: ephemeral server text and explicit reviewed descriptions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from guard_app.services.bridge import (
    BackendBridge,
    CommandEvent,
    CommandStage,
    DescriptionRow,
    RegistrationRequest,
    RegistrationView,
    new_command_id,
    reviewed_description_issue,
)

from .presentation import public_display_text
from .untrusted_text import UntrustedTextView
from .widgets import KeyboardActionButton, plain_label, size_detail_for_viewport

_PREVIEW_FAILED = "설명을 불러오지 못함. 창을 닫고 다시 여세요."
_UNKNOWN = "저장 결과를 확인하지 못함. 다시 저장하지 말고 앱을 다시 시작하세요."
_MESSAGES = {
    "REG_DESCRIPTIONS_SAVED": (
        "검토한 설명을 저장함. 이 서버를 활성화하고 앱을 다시 시작하면 새 세션부터 쓰임."
    ),
    "REG_DESCRIPTIONS_STALE": "서버 상태가 바뀌어 저장하지 않음. 창을 닫고 다시 여세요.",
    "REG_DESCRIPTION_INVALID": "저장할 수 없는 설명이 있음. 표시된 칸을 고친 뒤 저장하세요.",
    "REG_DESCRIPTIONS_INVALID": "저장할 수 없는 설명이 있음. 표시된 칸을 고친 뒤 저장하세요.",
    "REG_DESCRIPTIONS_UNAVAILABLE": "PENDING 서버만 설명을 검토할 수 있음.",
    "REG_STORE_UNKNOWN": "저장 결과 불명. 다시 저장하지 말고 앱을 다시 시작하세요.",
}


class DescriptionReviewDialog(QDialog):
    def __init__(
        self, bridge: BackendBridge, server_id: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._server_id = server_id
        self._command_id = ""
        self._pending_action = ""
        self._closed = False
        self._editable = False
        self._row_revision = 0
        self.edits: dict[str, QPlainTextEdit] = {}
        self._issues: dict[str, QLabel] = {}
        self._server_views: list[UntrustedTextView] = []
        self.setWindowTitle(f"도구 설명 검토 · {public_display_text(server_id)}")
        size_detail_for_viewport(self, 820, 680)
        layout = QVBoxLayout(self)
        for notice in (
            "모델에는 서버가 보낸 설명 대신 여기서 저장한 설명만 보냄. 비워 두면 설명 없이 보냄.",
            "저장한 설명은 이 서버를 활성화하고 앱을 다시 시작한 뒤 새 세션부터 쓰임. "
            "실행 중 세션은 바뀌지 않음.",
        ):
            layout.addWidget(plain_label(notice, name="secondary"))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        body = QWidget(scroll)
        self._rows = QVBoxLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.result_label = plain_label("", name="runtimeMessage")
        layout.addWidget(self.result_label)
        buttons_widget = QWidget(self)
        buttons = QHBoxLayout(buttons_widget)
        layout.addWidget(buttons_widget)
        self.save_button = KeyboardActionButton("저장")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        close = KeyboardActionButton("닫기")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        bridge.registration_changed.connect(self._registration)
        bridge.command_changed.connect(self._command)
        self.finished.connect(self._finish)
        self._send(RegistrationRequest("descriptions_preview", server_id))

    def _send(self, request: RegistrationRequest) -> None:
        self._command_id = new_command_id()
        self._pending_action = request.action
        self.save_button.setEnabled(False)
        self._bridge.request_registration(request, command_id=self._command_id)

    def _add_row(self, row: DescriptionRow) -> None:
        card = QWidget()
        layout = QVBoxLayout(card)
        self._rows.addWidget(card)
        name = public_display_text(row.name)
        layout.addWidget(plain_label(name, name="sectionTitle"))
        layout.addWidget(plain_label("서버가 보낸 설명"))
        if row.server_text is None:
            layout.addWidget(
                plain_label(
                    "이번 실행에서 받은 서버 설명 없음. 검사 결과 창에서 다시 검사하면 받음.",
                    name="secondary",
                )
            )
        elif row.server_text == "":
            layout.addWidget(plain_label("서버가 보낸 설명 없음", name="secondary"))
        else:
            view = UntrustedTextView(
                f"서버가 보낸 설명 · {name}", max_chars=4000, show_unverified_notice=True
            )
            layout.addWidget(view)
            # Server text is display-only, including the native copy/context-menu paths.
            view.body.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            view.body.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            view.set_text(row.server_text)
            self._server_views.append(view)
        layout.addWidget(plain_label("모델에 보낼 설명"))
        edit = QPlainTextEdit()
        edit.setAccessibleName(f"모델에 보낼 설명 · {name}")
        edit.setPlainText(row.reviewed)
        layout.addWidget(edit)
        issue = plain_label("", name="secondary")
        layout.addWidget(issue)
        self.edits[row.name] = edit
        self._issues[row.name] = issue
        edit.textChanged.connect(self._update)

    def _update(self) -> None:
        valid = True
        for name, edit in self.edits.items():
            value = edit.toPlainText()
            issue = reviewed_description_issue(value)
            message = (
                "2,000자를 넘음"
                if issue == "REG_DESCRIPTION_TOO_LONG"
                else "쓸 수 없는 문자가 있음(줄바꿈·탭 말고 제어 문자 등)"
                if issue
                else ""
            )
            self._issues[name].setText(
                f"{len(value):,}/2,000자" + (f" · {message}" if message else "")
            )
            valid = valid and issue is None
        self.save_button.setEnabled(
            valid and self._editable and not self._command_id and not self._closed
        )

    def _save(self) -> None:
        if not self.save_button.isEnabled():
            return
        self._send(
            RegistrationRequest(
                "descriptions_save",
                self._server_id,
                expected_row_revision=self._row_revision,
                descriptions=tuple((name, edit.toPlainText()) for name, edit in self.edits.items()),
            )
        )

    def _registration(self, view: RegistrationView) -> None:
        if self._closed or not self._command_id or view.command_id != self._command_id:
            return
        self._command_id = ""
        if view.code == "REG_DESCRIPTIONS_PREVIEW":
            self._row_revision = view.row_revision
            for row in view.descriptions:
                self._add_row(row)
            self._editable = True
        else:
            fallback = (
                _PREVIEW_FAILED if self._pending_action == "descriptions_preview" else _UNKNOWN
            )
            self.result_label.setText(_MESSAGES.get(view.code, fallback))
            if view.code not in ("REG_DESCRIPTION_INVALID", "REG_DESCRIPTIONS_INVALID"):
                self._editable = False
        self._update()

    def _command(self, event: CommandEvent) -> None:
        if (
            not self._closed
            and self._command_id
            and event.command_id == self._command_id
            and event.stage in (CommandStage.REJECTED, CommandStage.UNCONFIRMED)
        ):
            self._command_id = ""
            self._editable = False
            self.result_label.setText(
                _PREVIEW_FAILED if self._pending_action == "descriptions_preview" else _UNKNOWN
            )
            self._update()

    def _finish(self, _result: int) -> None:
        if self._closed:
            return
        self._closed = True
        self._bridge.registration_changed.disconnect(self._registration)
        self._bridge.command_changed.disconnect(self._command)
        for view in self._server_views:
            view.clear()
        for edit in self.edits.values():
            edit.clear()
            edit.document().clearUndoRedoStacks()
        self._update()
