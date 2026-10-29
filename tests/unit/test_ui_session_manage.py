"""A188 memory-only session management: I1/I3/I4/I7/I8/I9, T18/T21.

Bridge calls are counted here; real dispatch/wire/Store effects belong to C's tests.
"""

from dataclasses import replace
from datetime import datetime

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton

from guard_app.domain.enums import Label, TaskStatus
from guard_app.services.bridge import (
    BridgeState,
    CommandEvent,
    CommandStage,
    ControlAction,
    ConversationEvent,
    LifecycleEvent,
    SessionCreated,
)
from guard_app.ui.session_list import _ROW_ROLE, _SessionRowDelegate
from guard_app.ui.untrusted_text import conversation_display_text, untrusted_display_text
from guard_app.ui.widgets import ConfirmDialog, plain_tooltip

from .test_ui_sessions import app as app
from .test_ui_sessions import snapshot
from .test_ui_sessions import window as window


def dialog_for(shell):
    return next(d for d in shell.sessions.findChildren(QDialog) if d.isVisible())


def rename(shell, view, text):
    shell.sessions.rename_session(view.key)
    dialog = dialog_for(shell)
    edit = dialog.findChild(QLineEdit)
    assert edit.selectedText() == view.title
    edit.setText(text)
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "저장").click()


def row_for(shell, view):
    return next(
        shell.sessions.list.item(i)
        for i in range(shell.sessions.list.count())
        if shell.sessions.list.item(i).data(Qt.ItemDataRole.UserRole) == view.key
    )


def control_calls(monkeypatch, bridge):
    calls = []
    for name in dir(bridge):
        if name.startswith("request_"):
            monkeypatch.setattr(
                bridge,
                name,
                lambda *args, _name=name, **kwargs: calls.append((_name, args, kwargs)),
            )
    return calls


def test_rename_preserves_scope_and_refreshes_all_titles(window, monkeypatch):
    shell, panel, bridge = window
    state = snapshot(label=Label.CONFIDENTIAL, unclassified=True, can_accept_message=True)
    bridge.task_changed.emit(state)
    view = shell.sessions.current
    view.pending_command = "in-flight"
    panel._commands["in-flight"] = "message"
    panel._command_scopes["in-flight"] = (state.task_id, state.epoch)
    calls = control_calls(monkeypatch, bridge)
    shell.navigate("results")
    rename(shell, view, "  바꾼\u200b   이름  ")
    assert view.title == view.heading.text() == shell.results_scope.text() == "바꾼 이름"
    assert view.title_source == "manual"
    assert "바꾼 이름" in row_for(shell, view).text()
    assert row_for(shell, view).toolTip().startswith("바꾼 이름\n")
    assert any(a.text() == view.title for a in shell._session_menu.actions())
    assert view.snapshot is state
    assert view.pending_command == "in-flight"
    assert panel._commands == {"in-flight": "message"}
    assert calls == []
    shell.sessions.search.setText("바꾼 이름")
    assert shell.sessions.list.count() == 1


def test_empty_rename_disabled_and_manual_draft_survives_adoption(window, monkeypatch):
    shell, _, bridge = window
    view = shell.sessions.current
    shell.sessions.rename_session(view.key)
    dialog = dialog_for(shell)
    edit = dialog.findChild(QLineEdit)
    edit.setText(" \u200b\x00 ")
    save = next(b for b in dialog.findChildren(QPushButton) if b.text() == "저장")
    assert not save.isEnabled()
    dialog.reject()
    rename(shell, view, "직접 정한 제목")
    monkeypatch.setattr(bridge, "request_new_session", lambda *args, **kwargs: None)
    view.start.click()
    bridge.session_created.emit(SessionCreated(view.pending_command, view.key, snapshot()))
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "자동 이름", "one"))
    assert view.title == "직접 정한 제목"


def test_auto_title_once_and_background_scope(window):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current
    shell.navigate("results")
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "assistant", "답변", "a"))
    assert not view.auto_title_set
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "# 첫 질문", "b"))
    assert view.title == view.heading.text() == shell.results_scope.text() == "첫 질문"
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "다른 질문", "c"))
    assert view.title == "첫 질문"
    shell.sessions.new_session()
    other = shell.sessions.current
    rename(shell, view, "<b>전체 제목</b>" + "가" * 60)
    shown = untrusted_display_text(view.title)[0]
    assert "&lt;b&gt;전체 제목&lt;/b&gt;" in row_for(shell, view).toolTip()
    assert view.heading.toolTip() == plain_tooltip(shown)
    assert shell.sessions.current is other
    assert shell.results_scope.text() == other.heading.text()


def test_draft_delete_empty_last_and_nonempty_confirmation(window, monkeypatch, app):
    shell, _, bridge = window
    calls = control_calls(monkeypatch, bridge)
    first = shell.sessions.current
    shell.sessions.delete_session(first.key)
    assert first.key in shell.sessions._removed_keys
    assert shell.sessions.current.key != first.key
    view = shell.sessions.current
    view.draft.setPlainText("작성 중")
    shell.sessions.delete_session(view.key)
    dialog = dialog_for(shell)
    assert dialog.body.text() == "작성 중인 초안이 지워짐. 이 세션을 삭제할까요?"
    app.processEvents()
    assert dialog.cancel.hasFocus()
    assert not dialog.confirm.autoDefault()
    assert not dialog.confirm.isDefault()
    # Return, including repeat, never activates a destructive confirmation.
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    repeat = QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "", True
    )
    dialog.keyPressEvent(repeat)
    assert view.key in shell.sessions.sessions
    dialog.cancel.click()
    assert view.draft.toPlainText() == "작성 중"
    shell.sessions.delete_session(view.key)
    dialog_for(shell).confirm.click()
    assert view.draft.toPlainText() == ""
    assert not view.draft.document().isUndoAvailable()
    assert view.key not in shell.sessions.sessions
    assert shell.sessions.current.snapshot is None
    assert calls == []


@pytest.mark.parametrize(
    "status", [TaskStatus.HALTED, TaskStatus.CLOSED, TaskStatus.UNKNOWN_OUTCOME]
)
def test_final_delete_and_late_events_do_not_resurrect(window, monkeypatch, status):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current
    bridge.task_changed.emit(replace(snapshot(), status=status))
    calls = control_calls(monkeypatch, bridge)
    shell.sessions.delete_session(view.key)
    dialog = dialog_for(shell)
    assert "감사 기록과 호출 로그는 그대로 남음" in dialog.body.text()
    assert ("수신측 확인" in dialog.body.text()) == (status is TaskStatus.UNKNOWN_OUTCOME)
    dialog.confirm.click()
    for state in (status, TaskStatus.RUNNING):
        bridge.task_changed.emit(replace(snapshot(), status=state))
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "늦은 원문", "late"))
    bridge.conversation_partial.emit(ConversationEvent("task-one", 1, "assistant", "늦음", "late"))
    assert view.key not in shell.sessions.sessions
    assert view.transcript.is_empty()
    assert calls == []


@pytest.mark.parametrize("pending", [False, True])
def test_unavailable_delete_menu_and_entrypoint(window, pending):
    shell, _, bridge = window
    view = shell.sessions.current
    if pending:
        view.pending_command = "starting"
        expected = "세션 시작 결과를 기다리는 중이라 지금은 삭제할 수 없음."
    else:
        bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.READY))
        view = shell.sessions.current
        expected = "실행 대기 중인 Task는 목록에서 지울 수 없음."
    shell.sessions.open_menu(view.key, QPoint(50, 50))
    action = shell.sessions._menu.actions()[1]
    assert not action.isEnabled()
    assert action.toolTip() == action.statusTip() == expected
    shell.sessions._menu.close()
    shell.sessions.delete_session(view.key)
    assert not shell.sessions._dialogs
    assert view.key in shell.sessions.sessions


@pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL])
def test_stop_then_delete_background_exactly_one_cancel(window, monkeypatch, status):
    shell, panel, bridge = window
    state = replace(snapshot(), epoch=3, status=status)
    bridge.task_changed.emit(state)
    view = shell.sessions.current
    view.draft.setPlainText("지울 초안")
    shell.sessions.new_session()
    selected = shell.sessions.current
    selected.draft.setPlainText("보존할 초안")
    message = panel.message.text()
    calls = control_calls(monkeypatch, bridge)
    listing = shell.sessions.list
    target = _SessionRowDelegate.menu_rect(listing.visualItemRect(row_for(shell, view))).center()
    QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=target)
    shell.sessions._menu.actions()[1].trigger()
    shell.sessions._menu.close()
    confirm = dialog_for(shell).confirm
    confirm.click()
    confirm.click()
    assert len(calls) == 1
    name, args, kwargs = calls[0]
    assert name == "request_control"
    assert args == (ControlAction.CANCEL, "task-one")
    assert kwargs == {"command_id": view.pending_delete, "epoch": 3}
    assert "중지 요청됨" in row_for(shell, view).text()
    assert shell.sessions.delete_disabled_reason(view.key)
    assert shell.sessions.current is selected
    assert selected.draft.toPlainText() == "보존할 초안"
    assert panel.message.text() == message
    command = view.pending_delete
    bridge.task_changed.emit(replace(state, status=TaskStatus.HALTED))
    assert view.key in shell.sessions.sessions  # Snapshot alone is not the command result.
    bridge.command_changed.emit(
        CommandEvent(
            command,
            "control",
            "cancel",
            "task-one",
            CommandStage.BACKEND_RETURNED,
            "CONTROL_DENIED",
        )
    )
    assert view.key not in shell.sessions.sessions
    bridge.task_changed.emit(replace(state, status=TaskStatus.HALTED))
    bridge.conversation_ready.emit(ConversationEvent("task-one", 3, "user", "늦음", "late"))
    assert view.key not in shell.sessions.sessions
    assert panel.message.text() == message
    assert selected.draft.toPlainText() == "보존할 초안"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "stage", [CommandStage.REJECTED, CommandStage.UNCONFIRMED, CommandStage.BACKEND_RETURNED]
)
def test_stop_not_confirmed_keeps_session_and_notice(window, monkeypatch, stage):
    shell, _, bridge = window
    bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.WAITING_APPROVAL))
    view = shell.sessions.current
    control_calls(monkeypatch, bridge)
    shell.sessions.delete_session(view.key)
    dialog_for(shell).confirm.click()
    bridge.command_changed.emit(
        CommandEvent(view.pending_delete, "control", "cancel", "task-one", stage, "NOT_READY")
    )
    assert view.key in shell.sessions.sessions
    assert view.pending_delete is None
    assert "작업 중지를 확인하지 못해 삭제하지 않음" in view.notice.text()
    assert not view.notice.isHidden()
    assert not shell.sessions.delete_disabled_reason(view.key)


@pytest.mark.parametrize("stopper", [None, lambda task, epoch: None])
def test_missing_stopper_keeps_session(window, stopper):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current
    shell.sessions.set_stopper(stopper)
    shell.sessions.delete_session(view.key)
    dialog_for(shell).confirm.click()
    assert "작업 중지를 요청하지 못해 삭제하지 않음" in view.notice.text()
    assert view.key in shell.sessions.sessions
    assert view.pending_delete is None


def test_synchronous_bridge_refusal_is_not_lost(window, monkeypatch):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current

    def refuse(action, task_id, *, command_id, epoch):
        bridge.command_changed.emit(
            CommandEvent(
                command_id, "control", "cancel", task_id, CommandStage.REJECTED, "NOT_READY"
            )
        )

    monkeypatch.setattr(bridge, "request_control", refuse)
    shell.sessions.delete_session(view.key)
    dialog_for(shell).confirm.click()
    assert view.pending_delete is None
    assert "작업 중지를 확인하지 못해 삭제하지 않음" in view.notice.text()


def test_stopper_only_accepts_ready_backend_and_running_scope(window, monkeypatch):
    _, panel, bridge = window
    calls = control_calls(monkeypatch, bridge)
    assert panel.stop_task("missing", 1) is None
    for status in (
        TaskStatus.READY,
        TaskStatus.HALTED,
        TaskStatus.UNKNOWN_OUTCOME,
        TaskStatus.CLOSED,
    ):
        bridge.task_changed.emit(replace(snapshot(), status=status))
        assert panel.stop_task("task-one", 1) is None
    bridge.task_changed.emit(snapshot())
    assert panel.stop_task("task-one", 2) is None
    bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.CLOSING, "CLOSING"))
    assert panel.stop_task("task-one", 1) is None
    assert calls == []


def test_delete_selects_most_recent_remaining_session(window):
    shell, _, _ = window
    old = shell.sessions.current
    shell.sessions.new_session()
    recent = shell.sessions.current
    shell.sessions.new_session()
    removed = shell.sessions.current
    old.updated = datetime(2020, 1, 1)
    recent.updated = datetime(2026, 1, 1)
    shell.sessions.delete_session(removed.key)
    assert shell.sessions.current is recent


def test_row_menu_mouse_keyboard_and_compact_actions(window, app):
    shell, _, bridge = window
    bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.WAITING_APPROVAL))
    view = shell.sessions.current
    listing = shell.sessions.list
    row = row_for(shell, view)
    assert row.data(_ROW_ROLE).approval
    assert row.toolTip().endswith("(WAITING_APPROVAL)")
    target = _SessionRowDelegate.menu_rect(listing.visualItemRect(row)).center()
    QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert shell.sessions._menu.isVisible()
    assert [a.text() for a in shell.sessions._menu.actions()] == ["이름 변경…\tF2", "삭제…\tDelete"]
    shell.sessions._menu.close()
    point = listing.visualItemRect(row_for(shell, view)).center()
    QApplication.sendEvent(
        listing.viewport(),
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, point, listing.viewport().mapToGlobal(point)
        ),
    )
    assert shell.sessions._menu.isVisible()
    shell.sessions._menu.close()
    for key, modifier in [
        (Qt.Key.Key_Menu, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier),
    ]:
        shell.activateWindow()
        listing.setFocus()
        app.processEvents()
        QTest.keyClick(listing, key, modifier)
        assert shell.sessions._menu.isVisible()
        shell.sessions._menu.close()
    for key, title in [(Qt.Key.Key_F2, "세션 이름 변경"), (Qt.Key.Key_Delete, "작업 중지 후 삭제")]:
        shell.activateWindow()
        listing.setFocus()
        app.processEvents()
        QTest.keyClick(listing, key)
        dialog = dialog_for(shell)
        assert dialog.windowTitle() == title
        dialog.reject()
    shell._populate_session_menu()
    actions = shell._nav_menu.actions()
    assert actions[-2].text().startswith("현재 세션 이름 변경…")
    actions[-2].trigger()
    assert dialog_for(shell).windowTitle() == "세션 이름 변경"
    dialog_for(shell).reject()
    actions[-1].trigger()
    assert isinstance(dialog_for(shell), ConfirmDialog)


def test_row_date_and_header_width(window, app):
    shell, _, _ = window
    view = shell.sessions.current
    view.updated = datetime(2020, 2, 3, 15, 16)
    shell.sessions._refresh_list()
    assert row_for(shell, view).data(_ROW_ROLE).time == "2/3"
    rename(shell, view, "가" * 80)
    shell.resize(500, 700)
    app.processEvents()
    assert view.heading.width() < view.heading.fontMetrics().horizontalAdvance(view.heading.text())
    assert view.heading.toolTip() == "가" * 80


def test_automatic_ellipsis_is_ui_punctuation_and_tooltip_keeps_full_display(window):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "가" * 40, "first"))
    assert view.heading.text() == "가" * 27 + "…"
    assert row_for(shell, view).data(_ROW_ROLE).title == view.heading.text()
    assert row_for(shell, view).toolTip().startswith(conversation_display_text(view.title))
