"""Session routing and ephemeral text: I1/I3/I5/I7/I8/I9 and T18/T21."""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    ApprovalReview,
    BackendBridge,
    BridgeState,
    ConversationEvent,
    LifecycleEvent,
    SessionCreated,
    TaskProjection,
)
from guard_app.ui.presentation import PAGES
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.window import GuardWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    shell = GuardWindow()
    panel = RuntimePanel(bridge)
    shell.attach_runtime(panel)
    shell.resize(1100, 780)
    shell.show()
    bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.READY, "READY"))
    app.processEvents()
    yield shell, panel, bridge
    shell.clear_session_content()
    bridge.close()
    shell.close()


def snapshot(**kwargs):
    return TaskProjection("task-one", 1, TaskStatus.RUNNING, **kwargs)


def test_session_details_keep_draft_selection_and_both_scroll_positions(window, app):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot(can_accept_message=True))
    view = shell.sessions.current
    view.draft.setPlainText("초안 첫 줄\n" + "오래 보관할 입력\n" * 60)
    view.transcript.setPlainText("이전에 공개된 답변\n" * 180)
    cursor = view.draft.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    view.draft.setTextCursor(cursor)
    view.draft.setFocus()
    app.processEvents()
    view.transcript.verticalScrollBar().setValue(80)
    view.draft.verticalScrollBar().setValue(12)
    positions = (
        view.draft.textCursor().position(),
        view.draft.textCursor().anchor(),
        view.transcript.verticalScrollBar().value(),
        view.draft.verticalScrollBar().value(),
    )
    shell.navigate("results")
    app.processEvents()
    shell.back_to_conversation.click()
    app.processEvents()
    assert shell.sessions.current is view
    assert view.draft.toPlainText().startswith("초안 첫 줄")
    assert positions == (
        view.draft.textCursor().position(),
        view.draft.textCursor().anchor(),
        view.transcript.verticalScrollBar().value(),
        view.draft.verticalScrollBar().value(),
    )
    assert view.draft.hasFocus()


def test_new_draft_cannot_control_the_running_session_and_search_uses_real_rows(window):
    shell, panel, bridge = window
    bridge.task_changed.emit(snapshot(can_advance=True))
    active_key = shell.sessions.current_key
    assert panel.run_button.isEnabled()
    shell.new_session_button.click()
    draft = shell.sessions.current
    draft.draft.setPlainText("두 번째 초안")
    assert draft.snapshot is None
    assert not draft.send.isEnabled()
    assert not any(button.isEnabled() for button in panel.buttons)
    shell.sessions.search.setText("task-one")
    assert shell.sessions.list.count() == 1
    item = shell.sessions.list.item(0)
    assert item.data(Qt.ItemDataRole.UserRole) == active_key
    shell.sessions.select(active_key)
    assert panel.run_button.isEnabled()
    shell.sessions.select(draft.key)
    assert draft.draft.toPlainText() == "두 번째 초안"


def test_scope_change_discards_review_and_late_review_cannot_replace_it(window):
    shell, panel, bridge = window
    state = replace(snapshot(), status=TaskStatus.WAITING_APPROVAL, approval_id="approval-one")
    bridge.task_changed.emit(state)
    active_key = shell.sessions.current_key
    panel._review_command = "review-command"
    review = ApprovalReview("review-command", "approval-one", "task-one", 1, "fixed", b"{}")
    bridge.review_ready.emit(review)
    assert panel.approve_button.isEnabled()
    shell.new_session_button.click()
    assert not panel.detail.toPlainText()
    bridge.review_ready.emit(review)
    assert not panel.detail.toPlainText()
    assert not panel.approve_button.isEnabled()
    shell.sessions.select(active_key)
    assert not panel.approve_button.isEnabled()
    assert panel.review_button.isEnabled()


@pytest.mark.parametrize(
    "status", [TaskStatus.HALTED, TaskStatus.CLOSED, TaskStatus.UNKNOWN_OUTCOME]
)
def test_terminal_session_erases_text_and_rejects_late_conversation_and_running_state(
    window, status
):
    shell, panel, bridge = window
    state = snapshot(can_accept_message=True)
    bridge.task_changed.emit(state)
    view = shell.sessions.current
    view.draft.setPlainText("private-draft")
    bridge.conversation_ready.emit(
        ConversationEvent("task-one", 1, "assistant", "private-answer", "c")
    )
    assert "private-answer" in view.transcript.toPlainText()
    bridge.task_changed.emit(replace(state, status=status))
    bridge.conversation_ready.emit(
        ConversationEvent("task-one", 1, "assistant", "late-private", "late")
    )
    shell.sessions.set_snapshot(state)  # A stale projection cannot reopen this epoch.
    assert not view.transcript.toPlainText()
    assert not view.draft.toPlainText()
    assert view.draft.isReadOnly()
    assert view.snapshot.status is status
    assert not any(button.isEnabled() for button in panel.buttons)


def test_close_erases_all_drafts_and_late_text_is_not_rendered(window):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current
    view.draft.setPlainText("connected-private")
    shell.new_session_button.click()
    draft = shell.sessions.current
    draft.draft.setPlainText("draft-private")
    bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.CLOSED, "CLOSED"))
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "assistant", "late", "c"))
    assert all(not item.draft.toPlainText() for item in shell.sessions.sessions.values())
    assert not view.transcript.toPlainText()


def test_conversation_is_plain_text_and_routes_by_task_and_epoch_without_switching(window):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    view = shell.sessions.current
    shell.new_session_button.click()
    selected = shell.sessions.current
    bridge.conversation_ready.emit(ConversationEvent("wrong-task", 1, "assistant", "wrong", "x"))
    bridge.conversation_ready.emit(ConversationEvent("task-one", 2, "assistant", "wrong", "y"))
    event = ConversationEvent("task-one", 1, "assistant", '<a href="file:///tmp">x</a>\u202e', "c")
    bridge.conversation_ready.emit(event)
    bridge.conversation_ready.emit(event)
    assert shell.sessions.current is selected
    assert not selected.transcript.toPlainText()
    assert view.transcript.toPlainText().count('<a href="file:///tmp">') == 1
    assert "wrong" not in view.transcript.toPlainText()
    assert "\u202e" not in view.transcript.toPlainText()


def test_existing_feature_pages_and_preparation_forms_remain_reachable(window):
    shell, _, _ = window
    assert shell._page == "runs"
    assert set(shell._page_indices) == set(PAGES) == set(shell._nav_buttons)
    for page in PAGES:
        shell.navigate(page)
        assert shell.stack.currentIndex() == shell._page_indices[page]
    shell.navigate("server_add")
    assert shell.folder_rules_form.isVisibleTo(shell)
    assert shell.server_add_form.isVisibleTo(shell)
    shell.navigate("settings")
    assert shell.scanner_key_form.isVisibleTo(shell)
    assert not shell.scanner_key_form.save_button.isEnabled()


def test_projection_change_of_other_session_does_not_overwrite_selected_draft(window):
    shell, _, bridge = window
    bridge.task_changed.emit(snapshot())
    shell.new_session_button.click()
    view = shell.sessions.current
    view.draft.setPlainText("retained input")
    bridge.task_changed.emit(
        replace(snapshot(), status=TaskStatus.WAITING_APPROVAL, approval_id="a")
    )
    assert shell.sessions.current is view
    assert view.draft.toPlainText() == "retained input"


def test_selected_task_controls_all_status_identity_and_title_even_after_background_update(window):
    shell, panel, bridge = window
    shell.set_runtime_context("unit product")
    alpha = snapshot(model_mode="api")
    bridge.task_changed.emit(alpha)
    alpha_key = shell.sessions.current_key
    shell.new_session_button.click()
    draft = shell.sessions.current
    draft.pending_command = "new-beta"
    beta = replace(alpha, task_id="task-beta", model_mode="cli", model_ready=False)
    bridge.session_created.emit(SessionCreated("new-beta", draft.key, beta))
    bridge.task_changed.emit(beta)
    assert "CLI" in shell.runtime_identity.text()
    assert "cli" in shell.windowTitle()
    shell.sessions.select(alpha_key)
    bridge.task_changed.emit(replace(alpha, status=TaskStatus.HALTED))
    bridge.task_changed.emit(beta)
    assert panel.current.task_id == alpha.task_id
    assert shell.task_status_card.status is TaskStatus.HALTED
    assert "API" in shell.runtime_identity.text()
    assert "api" in shell.windowTitle()
    shell.sessions.select(draft.key)
    assert shell.task_status_card.status is TaskStatus.RUNNING
    assert "CLI" in shell.runtime_identity.text()


def test_task_adoption_keeps_friendly_title_and_reports_model_connection_needed(window):
    shell, _, bridge = window
    view = shell.sessions.current
    friendly = view.title.replace("새 세션", "세션", 1)
    view.pending_command = "created"
    state = replace(snapshot(), task_id="session-" + "a" * 32, model_ready=False)
    bridge.session_created.emit(SessionCreated("created", view.key, state))
    assert view.title == friendly
    assert all(
        state.task_id not in shell.sessions.list.item(index).text()
        for index in range(shell.sessions.list.count())
    )
    assert "연결 필요" in shell.sessions.list.currentItem().text()
    assert state.task_id in view.status.text()
    shell.new_session_button.click()
    second = shell.sessions.current
    second.pending_command = "created-second"
    bridge.session_created.emit(
        SessionCreated(
            "created-second",
            second.key,
            replace(state, task_id="session-other"),
        )
    )
    assert second.title != view.title
