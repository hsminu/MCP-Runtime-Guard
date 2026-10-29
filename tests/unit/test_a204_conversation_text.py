"""A204: chat text display and thinking indicator (I7, T18)."""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import ConversationEvent
from guard_app.ui import session_transcript
from guard_app.ui.approval_detail import visible_review_text
from guard_app.ui.session_transcript import ANSWER_LABEL, STOPPED_ANSWER_LABEL, _Transcript
from guard_app.ui.untrusted_text import conversation_display_text

from .test_ui_session_manage import rename, row_for
from .test_ui_sessions import app as app
from .test_ui_sessions import session_view, snapshot
from .test_ui_sessions import window as window


@pytest.mark.parametrize(
    "text",
    [
        "ㅋㅋ",
        "ㅠㅠ",
        "…",
        "①",
        "⚠️",
        "3–5",
        "（괄호）",
        "１",
        "\xa0",
        r"C:\Users\a",
        "\t",
        "\n",
        "\u1100\u1161",
        "a\u0301",
        "\u2003",
    ],
)
def test_chat_characters_stay_literal(text):
    assert conversation_display_text(text) == text


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("\u200b", "⟦U+200B⟧"),
        ("\u202e", "⟦U+202E⟧"),
        ("\u0627", "⟦U+0627⟧"),
        ("\u05d0", "⟦U+05D0⟧"),
        ("\u0661", "⟦U+0661⟧"),
        ("\u3164", "⟦U+3164⟧"),
        ("\u115f", "⟦U+115F⟧"),
        ("\r", "⟦U+000D⟧"),
        ("\U000e0001", "⟦U+E0001⟧"),
        ("\ud800", "⟦U+D800⟧"),
        ("\ue000", "⟦U+E000⟧"),
        ("\u0378", "⟦U+0378⟧"),
        ("\u2028", "⟦U+2028⟧"),
        ("\u2029", "⟦U+2029⟧"),
        ("⟦U+200B⟧", "⟦U+27E6⟧U+200B⟧"),
        ("a\r\nb", "a\nb"),
    ],
)
def test_hidden_bidi_and_notation_opener(text, shown):
    assert conversation_display_text(text) == shown


class _StringSubclass(str):
    pass


@pytest.mark.parametrize("value", [None, 1, b"text", _StringSubclass("text")])
def test_chat_requires_exact_str(value):
    with pytest.raises(TypeError, match="untrusted text has invalid type"):
        conversation_display_text(value)


def test_approval_review_keeps_compatibility_jamo_annotations():
    assert visible_review_text("ㅋㅋ") == "ㅋ\\u314Bㅋ\\u314B"


def test_chat_widgets_copy_and_plain_text_keep_korean(window):
    _, _, bridge, view = session_view(window)
    user = "ㅋㅋ 안녕… ⚠️"
    answer = "경로는 C:\\Users\\a 입니다 ①\n\u202e\n나\nGuard · 가짜 화자"
    shown = "경로는 C:\\Users\\a 입니다 ①\n⟦U+202E⟧\n나\nGuard · 가짜 화자"
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", user, "c"))
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "assistant", answer, "c"))
    first, last = view.transcript.turns
    assert first.body.text() == user
    assert last.body.text() == shown
    labels = [label.text() for label in last.body.findChildren(QLabel)]
    assert shown in labels
    assert view.transcript.toPlainText() == (
        f"나\n│ {user}\n\n{ANSWER_LABEL}\n"
        + "\n".join("│ " + line for line in shown.split("\n"))
        + "\n"
    )
    last.copy.click()
    assert QApplication.clipboard().text() == shown


@pytest.mark.parametrize("manual", [False, True])
def test_korean_titles_and_hidden_title_display(window, manual):
    shell, _, bridge, view = session_view(window)
    if manual:
        rename(shell, view, "ㅋㅋ 질문")
    else:
        bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "ㅋㅋ 질문", "c"))
    assert view.heading.text() == "ㅋㅋ 질문"
    assert view.heading.toolTip() == "ㅋㅋ 질문"
    assert row_for(shell, view).toolTip().startswith("ㅋㅋ 질문\n")
    # Title derivation strips controls; exercise retained title rendering itself.
    shell.sessions._set_title(view, "ㅋㅋ\u200b 질문")
    assert view.heading.text() == "ㅋㅋ⟦U+200B⟧ 질문"
    assert view.heading.toolTip() == "ㅋㅋ⟦U+200B⟧ 질문"
    assert row_for(shell, view).toolTip().startswith("ㅋㅋ⟦U+200B⟧ 질문\n")


@pytest.fixture
def thinking_run(window, monkeypatch, app):
    shell, _, bridge, view = session_view(window, can_accept_message=True)
    monkeypatch.setattr(bridge, "request_message", lambda *args, **kwargs: None)
    now = [100.0]
    monkeypatch.setattr(session_transcript, "_clock", lambda: now[0])
    view.draft.setPlainText("ㅋㅋ 질문")
    view.send.click()
    command = view.pending_command
    assert command is not None
    assert view.transcript.is_empty()
    bridge.conversation_ready.emit(ConversationEvent("task-one", 1, "user", "ㅋㅋ 질문", command))
    app.processEvents()
    label = view.transcript.findChild(QLabel, "thinkingLabel")
    assert label.isVisible()
    assert label.text() == "생각 중 · 0초"
    assert label.accessibleName() == "모델 응답 기다리는 중"
    assert label.textFormat() is Qt.TextFormat.PlainText
    assert view.transcript._thinking_timer.isActive()
    assert view.transcript._thinking_timer.interval() == 1000
    assert len(view.transcript.turns) == 1
    assert "생각 중" not in view.transcript.toPlainText()
    return shell, bridge, view, command, label, now


def test_thinking_clock_other_epoch_and_first_piece(thinking_run):
    shell, bridge, view, command, label, now = thinking_run
    now[0] += 3
    view.transcript.set_thinking(True)
    shell.sessions._refresh_view(view)
    view.transcript._update_thinking_time()
    assert label.text() == "생각 중 · 3초"
    # Control: a piece of another epoch is still ignored (A90) and keeps the line.
    bridge.conversation_partial.emit(
        ConversationEvent("task-one", 2, "assistant", "무시할 조각", command)
    )
    assert view.partial is None
    assert len(view.transcript.turns) == 1
    assert label.isVisible()
    assert label.text() == "생각 중 · 3초"
    bridge.conversation_partial.emit(
        ConversationEvent("task-one", 1, "assistant", "ㅋㅋ…", command)
    )
    assert label.isHidden()
    assert not view.transcript._thinking_timer.isActive()
    assert label.text() == "생각 중 · 0초"
    assert view.transcript.turns[-1].body.text() == "ㅋㅋ…"
    shell.sessions._stop_partial(view)
    shell.sessions._refresh_view(view)
    assert label.isHidden()
    assert view.transcript.turns[-1].label == STOPPED_ANSWER_LABEL
    assert view.transcript.turns[-1].body.text() == "ㅋㅋ…"


@pytest.mark.parametrize("ending", ["final", "failed", "approval", "terminal", "stop", "clear"])
def test_thinking_stops_on_every_completion_path(thinking_run, ending):
    shell, bridge, view, command, label, _ = thinking_run
    if ending == "final":
        bridge.conversation_ready.emit(
            ConversationEvent("task-one", 1, "assistant", "최종 답", command)
        )
    elif ending == "failed":
        shell.sessions.settle_command(command, "BACKEND_UNCONFIRMED")
    elif ending == "approval":
        bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.WAITING_APPROVAL))
    elif ending == "terminal":
        bridge.task_changed.emit(replace(snapshot(), status=TaskStatus.HALTED))
    elif ending == "stop":
        shell.sessions._stop_partial(view)
    else:
        shell.sessions._clear_content(view)
    shell.sessions._refresh_view(view)
    assert label.isHidden()
    assert not view.transcript._thinking_timer.isActive()
    assert "생각 중" not in view.transcript.toPlainText()


def test_new_task_creation_has_no_thinking(window, monkeypatch):
    shell, _, bridge = window
    view = shell.sessions.current
    monkeypatch.setattr(bridge, "request_new_session", lambda *args, **kwargs: None)
    view.start.click()
    assert view.pending_command is not None
    assert view.snapshot is None
    assert view.transcript.findChild(QLabel, "thinkingLabel").isHidden()
    assert not view.transcript._thinking_timer.isActive()


@pytest.mark.parametrize("clear_method", ["clear", "setPlainText"])
def test_transcript_clear_resets_timer_and_thinking_is_not_a_turn(app, monkeypatch, clear_method):
    transcript = _Transcript()
    now = [0.0]
    monkeypatch.setattr(session_transcript, "_clock", lambda: now[0])
    try:
        transcript.set_thinking(True)
        assert transcript.is_empty()
        assert transcript.toPlainText() == ""
        now[0] = 3.0
        transcript._update_thinking_time()
        assert transcript._thinking.text() == "생각 중 · 3초"
        if clear_method == "clear":
            transcript.clear()
        else:
            transcript.setPlainText("replacement")
        assert transcript._thinking.isHidden()
        assert not transcript._thinking_timer.isActive()
        assert transcript._thinking.text() == "생각 중 · 0초"
        transcript.set_thinking(True)
        assert transcript._thinking.text() == "생각 중 · 0초"
    finally:
        transcript.clear()
        transcript.close()
        transcript.deleteLater()


def test_thinking_follows_bottom_and_stays_after_turns(app):
    transcript = _Transcript()
    transcript.resize(600, 280)
    transcript.show()
    try:
        for i in range(12):
            transcript.write_turn(ANSWER_LABEL, f"문단 {i}\n" * 4, None)
        app.processEvents()
        app.processEvents()
        bar = transcript.verticalScrollBar()
        assert bar.value() == bar.maximum()
        transcript.set_thinking(True)
        app.processEvents()
        app.processEvents()
        assert bar.value() == bar.maximum()
        transcript.write_turn(ANSWER_LABEL, "추가", None)
        column = transcript._column
        assert column.itemAt(column.count() - 3).widget() is transcript.turns[-1].widget
        assert column.itemAt(column.count() - 2).widget() is transcript._thinking
        assert column.itemAt(column.count() - 1).spacerItem() is not None
        bar.setValue(100)
        transcript.set_thinking(False)
        transcript.set_thinking(True)
        app.processEvents()
        assert bar.value() == 100
    finally:
        transcript.clear()
        transcript.close()
        transcript.deleteLater()
