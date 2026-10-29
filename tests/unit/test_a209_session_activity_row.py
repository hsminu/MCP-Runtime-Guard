"""A209 session-scoped navigation: I7, T21 UI scope; no backend execution.

T18 presentation regression is covered by plain row text and palette rendering.
These UI checks do not measure dispatch, wire traffic or receiver effects.
"""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QStyleOptionViewItem

from guard_app.ui.presentation import PAGES
from guard_app.ui.session_list import _ROW_ROLE, _ActivityRow, _SessionRow, _SessionRowDelegate
from guard_app.ui.style import current_theme
from guard_app.ui.widgets import plain_tooltip

from .test_ui_sessions import activity_row, click_activity_row, session_view, snapshot
from .test_ui_sessions import app as app
from .test_ui_sessions import window as window


def rows(shell):
    listing = shell.sessions.list
    return [listing.item(i) for i in range(listing.count())]


def session_row(shell, key):
    return next(item for item in rows(shell) if item.data(Qt.ItemDataRole.UserRole) == key)


def assert_current_activity(shell, active):
    listing = shell.sessions.list
    parent = session_row(shell, shell.sessions.current_key)
    child = activity_row(shell)
    assert listing.row(child) == listing.row(parent) + 1
    assert child.data(_ROW_ROLE) == _ActivityRow(active=active)
    assert listing.currentItem() is (child if active else parent)
    assert sum(isinstance(item.data(_ROW_ROLE), _ActivityRow) for item in rows(shell)) == 1


def test_sidebar_keeps_two_views_and_compact_menu_keeps_results(window):
    shell, _, _ = window
    assert set(shell._nav_buttons) & {"runs", "results", "approvals"} == {"runs", "approvals"}
    assert "results" not in shell._nav_buttons
    assert set(shell._menu_actions) == set(PAGES)


def test_only_current_session_has_an_activity_subrow(window):
    shell, _, _ = window
    shell.sessions.new_session()
    assert shell._page == "runs"
    assert sum(isinstance(item.data(_ROW_ROLE), _SessionRow) for item in rows(shell)) == 2
    assert_current_activity(shell, False)
    child = activity_row(shell)
    assert child.text() == PAGES["results"] == "결과 · 실행 활동"
    assert child.data(Qt.ItemDataRole.UserRole) is None
    assert child.toolTip() == plain_tooltip("이 세션의 결과와 실행 활동을 엶.")
    assert child.flags() & Qt.ItemFlag.ItemIsSelectable
    assert child.flags() & Qt.ItemFlag.ItemIsEnabled
    assert not shell.sessions.list.uniformItemSizes()


def test_mouse_click_opens_current_session_results_without_selecting_a_session(window, app):
    shell, _, _, view = session_view(window)
    selections = []
    shell.sessions.selection_changed.connect(selections.append)
    click_activity_row(shell)
    app.processEvents()
    assert shell._page == "results"
    assert shell.results_page.isVisible()
    assert shell.results_scope.text() == view.title
    assert shell.sessions.current_key == view.key
    assert selections == []
    assert_current_activity(shell, True)


def test_return_activates_activity_subrow(window, app):
    shell, _, _ = window
    key = shell.sessions.current_key
    listing = shell.sessions.list
    shell.activateWindow()
    listing.setFocus()
    listing.setCurrentItem(activity_row(shell))
    app.processEvents()
    assert listing.hasFocus()
    QTest.keyClick(listing, Qt.Key.Key_Return)
    app.processEvents()
    assert shell._page == "results"
    assert shell.sessions.current_key == key
    assert_current_activity(shell, True)


def test_other_session_click_keeps_results_and_moves_activity_subrow(window, app):
    shell, _, bridge, first = session_view(window)
    bridge.task_changed.emit(replace(snapshot(), task_id="task-two"))
    second = shell.sessions.sessions["task:task-two:1"]
    assert shell.sessions.current is first
    click_activity_row(shell)
    listing = shell.sessions.list
    QTest.mouseClick(
        listing.viewport(),
        Qt.MouseButton.LeftButton,
        pos=listing.visualItemRect(session_row(shell, second.key)).center(),
    )
    app.processEvents()
    assert shell._page == "results"
    assert shell.results_page.isVisible()
    assert shell.sessions.current is second
    assert shell.results_scope.text() == second.title
    assert_current_activity(shell, True)


@pytest.mark.parametrize("use_back", [True, False])
def test_return_to_conversation_selects_session_row(window, use_back):
    shell, _, _ = window
    click_activity_row(shell)
    if use_back:
        shell.back_to_conversation.click()
    else:
        shell.navigate("runs")
    assert shell._page == "runs"
    assert_current_activity(shell, False)


@pytest.mark.parametrize("active", [False, True])
def test_search_only_matches_session_text_and_hides_its_subrow_together(window, active):
    shell, _, _, view = session_view(window)
    shell.navigate("results" if active else "runs")
    shell.sessions.search.setText("새 세션")
    assert rows(shell)
    assert all(isinstance(item.data(_ROW_ROLE), _SessionRow) for item in rows(shell))
    assert shell.sessions.list.currentItem() is None
    shell.sessions.search.setText(view.title)
    assert len(rows(shell)) == 2
    assert_current_activity(shell, active)
    shell.sessions.search.setText("결과")
    assert rows(shell) == []
    shell.sessions.search.clear()
    assert_current_activity(shell, active)


def spy_session_actions(shell, monkeypatch):
    """Record session actions; an unknown key would make them silent no-ops."""
    calls = []
    for name in ("rename_session", "delete_session", "open_menu"):
        monkeypatch.setattr(
            shell.sessions, name, lambda *_args, _name=name, **_kwargs: calls.append(_name)
        )
    return calls


@pytest.mark.parametrize(
    ("key", "modifier"),
    [
        (Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier),
        (Qt.Key.Key_Menu, Qt.KeyboardModifier.NoModifier),
    ],
)
def test_activity_shortcuts_do_not_open_session_actions(window, app, monkeypatch, key, modifier):
    shell, _, _ = window
    listing = shell.sessions.list
    calls = spy_session_actions(shell, monkeypatch)
    shell.activateWindow()
    listing.setFocus()
    listing.setCurrentItem(activity_row(shell))
    app.processEvents()
    assert listing.hasFocus()
    QTest.keyClick(listing, key, modifier)
    app.processEvents()
    assert calls == []
    assert not shell.sessions._menu.isVisible()
    assert not any(dialog.isVisible() for dialog in shell.findChildren(QDialog))
    assert shell._page == "runs"


def test_activity_context_menu_and_more_hit_target_do_not_open_session_menu(
    window, app, monkeypatch
):
    shell, _, _ = window
    listing = shell.sessions.list
    calls = spy_session_actions(shell, monkeypatch)
    listing.itemDelegate().menu_requested.connect(lambda *_args: calls.append("delegate_menu"))
    listing.setCurrentItem(activity_row(shell))
    rect = listing.visualItemRect(activity_row(shell))
    point = rect.center()
    QApplication.sendEvent(
        listing.viewport(),
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, point, listing.viewport().mapToGlobal(point)
        ),
    )
    assert not shell.sessions._menu.isVisible()
    QTest.mouseClick(
        listing.viewport(),
        Qt.MouseButton.LeftButton,
        pos=_SessionRowDelegate.menu_rect(rect).center(),
    )
    app.processEvents()
    assert calls == []
    assert not shell.sessions._menu.isVisible()
    assert not any(dialog.isVisible() for dialog in shell.findChildren(QDialog))
    assert shell._page == "results"


def test_activity_and_session_rows_have_distinct_heights(window):
    shell, _, _ = window
    listing = shell.sessions.list
    delegate = listing.itemDelegate()
    for item in rows(shell):
        expected = 32 if isinstance(item.data(_ROW_ROLE), _ActivityRow) else 52
        assert (
            delegate.sizeHint(QStyleOptionViewItem(), listing.indexFromItem(item)).height()
            == expected
        )
        assert listing.visualItemRect(item).height() == expected


@pytest.mark.parametrize("invalid", [1, 0, None, "true"])
def test_activity_page_requires_exact_bool(window, invalid):
    shell, _, _ = window
    with pytest.raises(TypeError):
        shell.sessions.set_activity_page(invalid)
    assert_current_activity(shell, False)


def test_activity_page_refreshes_only_when_value_changes(window, monkeypatch):
    shell, _, _ = window
    refreshes = []
    monkeypatch.setattr(shell.sessions, "_refresh_list", lambda: refreshes.append(True))
    for active in (False, True, True, False, False):
        shell.sessions.set_activity_page(active)
    assert len(refreshes) == 2
    assert shell.sessions._activity_page is False


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_activity_row_paints_in_both_palettes(window, app, theme):
    shell, _, _ = window
    previous = current_theme()
    try:
        shell.set_theme(theme)
        for page in ("runs", "results"):
            shell.navigate(page)
            app.processEvents()
            assert not shell.sessions.list.grab().isNull()
    finally:
        shell.set_theme(previous)
