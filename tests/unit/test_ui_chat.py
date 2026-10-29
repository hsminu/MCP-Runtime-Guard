"""A191: I1/I3/I5/I7/I8/I9, T18/T21; UI command counts, no real dispatch."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import (
    QDragEnterEvent,
    QDropEvent,
    QInputMethodEvent,
    QKeyEvent,
    QTextCursor,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QTextBrowser

from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.queries import ApprovalListLane, ApprovalListPage, ApprovalRow, ListSort
from guard_app.domain.reasons import Reason
from guard_app.services.bridge import (
    CommandEvent,
    CommandStage,
    ControlAction,
    ConversationEvent,
    QueryReply,
)
from guard_app.ui.answer_blocks import parse_blocks
from guard_app.ui.answer_widgets import CodeText, CopyButton
from guard_app.ui.composer import ATTACHMENT_NOTICE
from guard_app.ui.session_projection import session_display
from guard_app.ui.session_transcript import (
    ANSWER_LABEL,
    PARTIAL_ANSWER_LABEL,
    USER_LABEL,
    _Transcript,
)
from guard_app.ui.sessions import APPROVAL_SUMMARY_UNAVAILABLE
from guard_app.ui.task_status import status_title
from guard_app.ui.untrusted_text import untrusted_display_text
from guard_app.ui.widgets import ConfirmDialog

from .test_ui_session_manage import rename
from .test_ui_sessions import app as app
from .test_ui_sessions import session_view, snapshot
from .test_ui_sessions import window as window


@pytest.mark.parametrize(
    "modifier", [Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier]
)
def test_send_keys_and_disabled_enter(window, modifier, monkeypatch):
    shell, _, bridge, view = session_view(window, can_accept_message=True)
    monkeypatch.setattr(bridge, "request_message", lambda *args, **kwargs: None)
    sent = []
    shell.sessions.message_requested.connect(lambda *args: sent.append(args))
    view.draft.setPlainText("첫 글")
    QTest.keyClick(view.draft, Qt.Key.Key_Return, modifier)
    assert len(sent) == 1
    assert sent[0][2] == "첫 글"
    assert not view.send.isEnabled()
    QTest.keyClick(view.draft, Qt.Key.Key_Return, modifier)
    assert len(sent) == 1
    assert view.draft.toPlainText() == "첫 글"


def test_shift_enter_ime_commit_and_repeat(window):
    shell, _, _, view = session_view(window, can_accept_message=True)
    sent = []
    shell.sessions.message_requested.connect(lambda *args: sent.append(args))
    QTest.keyClick(view.draft, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert view.draft.toPlainText() == "\n"
    assert not sent
    view.draft.clear()
    QApplication.sendEvent(view.draft, QInputMethodEvent("ㅎ", []))
    QTest.keyClick(view.draft, Qt.Key.Key_Return)
    assert not sent
    commit = QInputMethodEvent()
    commit.setCommitString("한")
    QApplication.sendEvent(view.draft, commit)
    QApplication.sendEvent(
        view.draft,
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
            "",
            True,
        ),
    )
    assert not sent
    QTest.keyClick(view.draft, Qt.Key.Key_Return)
    assert len(sent) == 1
    assert "한" in sent[0][2]


def test_eight_line_height_limit_and_mime(window, app):
    _, _, _, view = session_view(window, can_accept_message=True)
    view.draft.setPlainText("x\n" * 7 + "x")
    app.processEvents()
    height = view.draft.height()
    view.draft.setPlainText("x\n" * 60)
    app.processEvents()
    assert view.draft.height() == height
    assert view.draft.verticalScrollBar().maximum() > 0
    view.draft.clear()
    files = QMimeData()
    files.setUrls([QUrl.fromLocalFile("C:/synthetic.txt")])
    assert not view.draft.canInsertFromMimeData(files)
    view.draft.insertFromMimeData(files)
    assert view.notice.text() == ATTACHMENT_NOTICE
    assert not view.draft.toPlainText()
    view.input_notice = ""
    view.notice.clear()
    QApplication.clipboard().setMimeData(files)
    QTest.keyClick(view.draft, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert view.notice.text() == ATTACHMENT_NOTICE
    assert not view.draft.toPlainText()
    enter = QDragEnterEvent(
        QPointF(4, 4),
        Qt.DropAction.CopyAction,
        files,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.draft.dragEnterEvent(enter)
    assert not enter.isAccepted()
    drop = QDropEvent(
        QPointF(4, 4),
        Qt.DropAction.CopyAction,
        files,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.draft.dropEvent(drop)
    assert not drop.isAccepted()
    assert not view.draft.toPlainText()
    text = QMimeData()
    text.setText("<b>글</b>")
    text.setHtml("<b>서식</b>")
    view.draft.insertFromMimeData(text)
    assert view.draft.toPlainText() == "<b>글</b>"
    text.setUrls([QUrl.fromLocalFile("C:/synthetic.txt")])
    assert view.draft.canInsertFromMimeData(text)


def test_blocks_are_literal_and_keep_unclosed_code():
    blocks = parse_blocks(
        "문단 **굵게** __또 굵게__ `x` [a](b) ⟦U+202E⟧\n\n"
        "# 제목\n###### 작은 제목\n- 항목\n  1. 안쪽\n* 별\n+ 더하기\n"
        "> 인용\n| a | b |\n| - | - |\n---\n***\n"
        "```python\nx = '**그대로**'\n```\n~~~\n끝까지\n줄"
    )
    assert [b.kind for b in blocks] == [
        "paragraph",
        "heading",
        "heading",
        "list",
        "list",
        "list",
        "list",
        "quote",
        "table",
        "rule",
        "rule",
        "code",
        "code",
    ]
    assert blocks[0].text == "문단 굵게 또 굵게 `x` [a](b) ⟦U+202E⟧"
    assert (blocks[1].level, blocks[2].level) == (1, 6)
    assert (blocks[3].marker, blocks[4].marker, blocks[4].level) == ("•", "1.", 1)
    assert blocks[-2].language == "python"
    assert blocks[-2].text == "x = '**그대로**'"
    assert blocks[-1].text == "끝까지\n줄"
    assert parse_blocks("**열림")[0].text == "**열림"


def test_code_height_limit_horizontal_scroll_and_wheel(app):
    short = CodeText("short")
    twenty = CodeText("x\n" * 19 + "x")
    long = CodeText(("x" * 200 + "\n") * 40)
    try:
        for edit in (short, twenty, long):
            edit.resize(240, edit.height())
            edit.show()
        app.processEvents()
        assert long.height() == twenty.height()
        assert long.verticalScrollBar().maximum() > 0
        assert long.horizontalScrollBar().maximum() > 0
        assert short.verticalScrollBar().maximum() == 0
        for edit, accepted in ((short, False), (long, True)):
            wheel = QWheelEvent(
                QPointF(5, 5),
                QPointF(5, 5),
                QPoint(),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            edit.wheelEvent(wheel)
            assert wheel.isAccepted() is accepted
    finally:
        for edit in (short, twenty, long):
            edit.close()
            edit.deleteLater()


def test_plain_render_copy_and_stream_prefix_reuse(window, app):
    _, _, bridge, view = session_view(window)
    raw = "# 제목\n\n<b>HTML</b> [link](https://example.test)\n\n```py\nx\u202e\n```"
    event = ConversationEvent("task-one", 1, "assistant", raw, "stream")
    bridge.conversation_partial.emit(event)
    turn = view.transcript.turns[-1]
    shown = untrusted_display_text(raw)[0]
    assert turn.text == shown
    assert (
        view.transcript.toPlainText()
        == PARTIAL_ANSWER_LABEL + "\n" + "\n".join("│ " + line for line in shown.split("\n")) + "\n"
    )
    assert not turn.widget.findChildren(QTextBrowser)
    for label in turn.widget.findChildren(QLabel):
        assert label.textFormat() is Qt.TextFormat.PlainText
        assert not label.openExternalLinks()
        assert not label.textInteractionFlags() & (
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
    code = turn.widget.findChild(QPlainTextEdit, "codeText")
    code_button = next(b for b in turn.widget.findChildren(CopyButton) if b is not turn.copy)
    code_button.click()
    assert (
        QApplication.clipboard().text()
        == code.toPlainText()
        == untrusted_display_text("x\u202e")[0]
    )
    assert code_button.text() == "복사함"
    assert "작성 중인 내용임" in turn.copy.toolTip()
    turn.copy.click()
    assert QApplication.clipboard().text() == turn.text
    first = turn.body._widgets[0]
    bridge.conversation_partial.emit(replace(event, text=raw + "\n\n마지막"))
    assert turn.body._widgets[0] is first
    bridge.conversation_ready.emit(replace(event, text=raw + "\n\n완료"))
    assert "작성 중" not in turn.copy.toolTip()
    turn.copy.click()
    assert QApplication.clipboard().text() == turn.text
    QTest.qWait(1550)
    assert turn.copy.text() == "복사"
    view.transcript.write_turn(USER_LABEL, "사용자\\u202E", None)
    user = view.transcript.turns[-1]
    user.copy.click()
    assert QApplication.clipboard().text() == user.text
    assert user.copy.focusPolicy() != Qt.FocusPolicy.NoFocus


def test_scroll_stays_put_until_jump_and_follows_after(app):
    transcript = _Transcript()
    transcript.resize(600, 280)
    transcript.show()
    try:
        for i in range(24):
            transcript.write_turn(ANSWER_LABEL, f"문단 {i}\n" * 4, None)
        app.processEvents()
        app.processEvents()
        bar = transcript.verticalScrollBar()
        assert bar.value() == bar.maximum()
        bar.setValue(120)
        app.processEvents()
        assert transcript.jump.isVisible()
        assert transcript.jump.text() == "최신 메시지로 이동"
        transcript.write_turn(PARTIAL_ANSWER_LABEL, "새 내용\n" * 20, None)
        app.processEvents()
        assert bar.value() == 120
        assert transcript.jump.text() == "새 메시지 · 최신으로 이동"
        transcript.jump.click()
        app.processEvents()
        assert bar.value() == bar.maximum()
        assert not transcript.jump.isVisible()
        transcript.write_turn(ANSWER_LABEL, "추가\n" * 25, len(transcript.turns) - 1)
        app.processEvents()
        assert bar.value() == bar.maximum()
        bar.setValue(0)
        bar.setValue(bar.maximum())
        assert transcript.jump.isHidden()
    finally:
        transcript.close()
        transcript.deleteLater()


@pytest.mark.parametrize(
    ("state", "options", "expected"),
    [
        (None, {}, ("시작 전", "neutral")),
        (None, {"pending_command": "start"}, ("시작 확인 중", "live")),
        (None, {"command_code": "CODEX_SESSION_INVALID"}, ("연결 오류", "deny")),
        (snapshot(connection_code="CODEX_AUTH_FAILED"), {}, ("연결 오류", "deny")),
        (replace(snapshot(), status=TaskStatus.WAITING_APPROVAL), {}, ("승인 필요", "attn")),
        (
            replace(snapshot(), status=TaskStatus.HALTED),
            {},
            (status_title(TaskStatus.HALTED), "deny"),
        ),
        (
            replace(snapshot(), status=TaskStatus.CLOSED),
            {},
            (status_title(TaskStatus.CLOSED), "neutral"),
        ),
        (snapshot(runner_state="model_error"), {}, ("멈춤", "deny")),
        (snapshot(model_ready=False), {}, ("연결 필요", "attn")),
        (snapshot(), {"pending_command": "message"}, ("응답 기다리는 중", "live")),
        (snapshot(), {"busy_scopes": frozenset({("task-one", 1)})}, ("응답 기다리는 중", "live")),
        (snapshot(), {"ready": False}, ("준비 중", "neutral")),
        (snapshot(can_advance=True), {}, ("진행 대기", "attn")),
        (snapshot(can_accept_message=True), {}, ("입력 가능", "neutral")),
    ],
)
def test_status_words_and_tones(state, options, expected):
    arguments = dict(
        ready=True,
        busy_scopes=frozenset(),
        pending_command=None,
        command_notice="",
        session_blocker="",
        startup_notice="",
    )
    arguments.update(options)
    display = session_display(state, **arguments)
    assert (display.pill, display.tone) == expected
    if state is not None and not state.tools_available:
        assert display.note.startswith("도구 없는 대화 · ")


@pytest.mark.parametrize("terminal", [True, False])
def test_stop_confirmation_counts_cancel_and_waits_for_snapshot(window, monkeypatch, terminal):
    shell, panel, bridge, view = session_view(window, can_accept_message=True)
    controls = []
    monkeypatch.setattr(bridge, "request_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bridge, "request_control", lambda *args, **kwargs: controls.append((args, kwargs))
    )
    assert view.stop.isHidden()
    view.draft.setPlainText("진행")
    view.send.click()
    assert not view.stop.isHidden()
    assert view.send.isHidden()
    assert not view.stop.autoDefault()
    assert not view.stop.isDefault()
    view.stop.click()
    dialog = next(d for d in shell.sessions.findChildren(ConfirmDialog) if d.isVisible())
    assert "이미 전송된 쓰기와 외부 작업은 취소되지 않음." in dialog.body.text()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert not controls
    dialog.confirm.click()
    dialog.confirm.click()
    assert len(controls) == 1
    args, kwargs = controls[0]
    assert args == (ControlAction.CANCEL, "task-one")
    assert kwargs["epoch"] == 1
    assert view.notice.text() == "중지 요청됨 · 적용 여부 확인 중"
    command = kwargs["command_id"]
    if terminal:
        bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.HALTED))
        assert view.notice.text() == "중지 요청됨 · 적용 여부 확인 중"
    bridge.command_changed.emit(
        CommandEvent(
            command,
            "control",
            "cancel",
            "task-one",
            CommandStage.BACKEND_RETURNED,
            "CONTROL_APPLIED",
        )
    )
    assert view.notice.text() == (
        "작업 정지를 확인함. 이미 전송된 쓰기는 수신됐을 수 있음."
        if terminal
        else "작업 중지를 확인하지 못함. 상태를 확인하세요."
    )


def test_control_only_and_other_scope_do_not_offer_stop(window):
    shell, panel, _, view = session_view(window)
    panel._commands["review"] = ControlAction.REVIEW
    panel._command_scopes["review"] = ("task-one", 1)
    panel.state_changed.emit()
    assert view.stop.isHidden()
    shell.sessions.set_busy_scopes(frozenset({("another", 1)}))
    assert view.stop.isHidden()


def test_synchronous_stop_refusal_stays_unconfirmed(window, monkeypatch):
    shell, _, bridge, view = session_view(window, can_accept_message=True)
    view.pending_command = "in-flight"
    shell.sessions._refresh_view(view)
    calls = []

    def refuse(action, target, *, command_id, epoch):
        calls.append((action, target, epoch))
        bridge.command_changed.emit(
            CommandEvent(
                command_id, "control", "cancel", target, CommandStage.REJECTED, "NOT_READY"
            )
        )

    monkeypatch.setattr(bridge, "request_control", refuse)
    view.stop.click()
    dialog = next(d for d in shell.sessions.findChildren(ConfirmDialog) if d.isVisible())
    dialog.confirm.click()
    assert calls == [(ControlAction.CANCEL, "task-one", 1)]
    assert view.pending_stop is None
    assert view.notice.text() == "작업 중지를 확인하지 못함. 상태를 확인하세요."


def test_model_uses_applied_id_and_keeps_effort_editable(window):
    shell, _, bridge = window
    assert shell.sessions.current.model.toolTip().startswith("세션을 시작할 때 이 모델을 요청함")
    bridge.task_changed.emit(snapshot(model_mode="codex", model_id="actual-model"))
    picker = shell.sessions.current.model
    assert picker.text().startswith("actual-model")
    assert "요청: gpt-6.1-sol, 적용: actual-model" in picker.toolTip()
    assert "추론 수준은 다음 메시지부터 적용됨" in picker.toolTip()
    assert not any(a.isEnabled() for a in picker.model_actions.values())
    assert all(a.isEnabled() for a in picker.effort_actions.values())


def test_approval_reconnect_new_token_no_destination_and_no_id(window, monkeypatch):
    shell, _, bridge = window
    calls = []
    monkeypatch.setattr(bridge, "request_query", lambda *args: calls.append(args))
    bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.WAITING_APPROVAL))
    view = shell.sessions.current
    assert view.approval.isHidden()
    bridge.task_changed.emit(
        replace(snapshot(), status=TaskStatus.WAITING_APPROVAL, approval_id="approval-one")
    )
    first = view.approval_token
    assert view.approval_summary.text() == APPROVAL_SUMMARY_UNAVAILABLE
    shell.store_queries._set_available(True)
    assert view.approval_token < first < 0
    page = ApprovalListPage(
        rows=(approval_row().model_copy(update={"destination_id": None}),),
        total_count=1,
        as_of_utc=datetime(2026, 10, 30, tzinfo=UTC),
    )
    bridge.query_answered.emit(QueryReply("approvals", view.approval_token, page))
    assert "목적지 지정 없음" in view.approval_summary.text()
    shell.store_queries._set_available(False)
    assert view.approval_summary.text() == APPROVAL_SUMMARY_UNAVAILABLE


def approval_row(approval_id="approval-one"):
    return ApprovalRow(
        approval_id=approval_id,
        task_id="task-one",
        request_id="request-one",
        server_id="server\u202e",
        tool_name="<tool>",
        destination_id="destination",
        status=ApprovalStatus.PENDING,
        created_at_utc=datetime(2026, 10, 30, tzinfo=UTC),
        expires_at_utc=datetime(2026, 10, 30, 1, 23, tzinfo=UTC),
        decided_at_utc=None,
        reason_code=Reason.INTERNAL_TO_EXTERNAL,
        row_revision=0,
    )


@pytest.mark.parametrize("mode", ["exact", "other", "failed", "disconnected", "invalid"])
def test_approval_summary_separate_tokens_and_exact_id(window, monkeypatch, mode):
    shell, _, bridge = window
    calls = []
    monkeypatch.setattr(bridge, "request_query", lambda *args: calls.append(args))
    link = shell.store_queries
    link._set_available(mode != "disconnected")
    positive = shell.approval_page.current_query
    calls.clear()
    approval_id = "invalid id" if mode == "invalid" else "approval-one"
    bridge.task_changed.emit(
        replace(
            snapshot(label=Label.INTERNAL),
            status=TaskStatus.WAITING_APPROVAL,
            approval_id=approval_id,
        )
    )
    view = shell.sessions.current
    assert view.approval_token < 0
    assert view.approval_summary.text() == APPROVAL_SUMMARY_UNAVAILABLE
    if mode in ("invalid", "disconnected"):
        assert not calls
        return
    kind, token, query = calls[-1]
    assert kind == "approvals"
    assert token == view.approval_token
    assert (query.lane, query.search, query.server_id, query.sort, query.offset, query.limit) == (
        ApprovalListLane.PENDING,
        approval_id,
        None,
        ListSort.NEWEST,
        0,
        5,
    )
    row = approval_row("approval-other" if mode == "other" else "approval-one")
    page = ApprovalListPage(
        rows=(row,), total_count=1, as_of_utc=datetime(2026, 10, 30, tzinfo=UTC)
    )
    bridge.query_answered.emit(QueryReply("approvals", token, page, failed=mode == "failed"))
    assert shell.approval_page.current_query is positive
    if mode != "exact":
        assert view.approval_summary.text() == APPROVAL_SUMMARY_UNAVAILABLE
    else:
        text = view.approval_summary.text()
        assert all(
            part in text
            for part in (
                "<tool>",
                "server",
                "destination",
                "내부 자료의 외부 전송에 승인 필요",
                "KST 10:23",
            )
        )
        assert "\u202e" not in text
        assert "내부" in view.approval_grade.text()
        bridge.query_answered.emit(QueryReply("approvals", positive.token, None, failed=True))
        assert view.approval_summary.text() == text
        bridge.task_changed.emit(
            replace(snapshot(), status=TaskStatus.WAITING_APPROVAL, approval_id="approval-two")
        )
        bridge.query_answered.emit(QueryReply("approvals", token, page))
        assert view.approval_summary.text() == APPROVAL_SUMMARY_UNAVAILABLE


def test_shortcuts_edit_keys_and_draft_survive_other_session_changes(window, app):
    shell, _, _ = window
    shell.activateWindow()
    app.processEvents()
    first = shell.sessions.current
    first.draft.setPlainText("retained draft")
    cursor = first.draft.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    first.draft.setTextCursor(cursor)
    first.draft.setFocus()
    QTest.keyClick(first.draft, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
    second = shell.sessions.current
    assert second is not first
    rename(shell, second, "바꾼 이름")
    app.processEvents()
    shell.activateWindow()
    app.processEvents()
    shell.sessions.delete_session(second.key)
    assert shell.sessions.current is first
    assert first.draft.toPlainText() == "retained draft"
    assert (first.draft.textCursor().position(), first.draft.textCursor().anchor()) == (6, 2)
    shell.navigate("settings")
    QTest.keyClick(shell, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert shell._page == "runs"
    assert first.draft.hasFocus()
    QTest.keyClick(first.draft, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "tain"
    QApplication.clipboard().setText("paste")
    QTest.keyClick(first.draft, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert first.draft.toPlainText() == "repasteed draft"
    QTest.keyClick(first.draft, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert first.draft.toPlainText() == "retained draft"
    QTest.keyClick(first.draft, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert shell.sessions.search.hasFocus()
    shell.sessions.search.setText("search")
    shell.sessions.search.selectAll()
    QTest.keyClick(shell.sessions.search, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "search"
    shell.resize(500, 600)
    app.processEvents()
    QTest.keyClick(shell, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    assert shell._nav_menu.isVisible()
    shell._nav_menu.close()
