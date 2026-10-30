"""A219: 현황·감사 기록 화면을 없앤 뒤 메뉴·스택·이동 경로에 남지 않는다."""

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from guard_app.ui.presentation import PAGES
from guard_app.ui.window import GuardWindow

from .test_ui_icons_style import app as app
from .test_ui_icons_style import closing_widgets, show_widget

REMOVED_PAGES = {"overview", "audit"}


def test_removed_pages_are_not_registered() -> None:
    assert REMOVED_PAGES.isdisjoint(PAGES)


@pytest.mark.parametrize(("width", "height"), [(1100, 750), (320, 400)])
def test_removed_pages_have_no_sidebar_menu_or_stack_entry(app, width, height) -> None:
    window = GuardWindow()
    with closing_widgets(window):
        show_widget(app, window, width, height)
        assert REMOVED_PAGES.isdisjoint(window._nav_buttons)
        assert REMOVED_PAGES.isdisjoint(window._menu_actions)
        assert set(window._page_indices) == set(PAGES)
        assert window.stack.count() == len(PAGES)
        labels = {button.text() for button in window.sidebar.findChildren(QPushButton)}
        labels.update(action.text() for action in window._nav_menu.actions())
        assert {"현황", "감사 기록"}.isdisjoint(labels)


def test_window_starts_on_conversation(app: QApplication) -> None:
    window = GuardWindow()
    with closing_widgets(window):
        window.show()
        app.processEvents()
        assert window._page == "runs"
        assert window.stack.currentIndex() == window._page_indices["runs"]
        assert window.sessions.isVisibleTo(window)
        assert window.breadcrumb.text() == PAGES["runs"]
        assert window._nav_buttons["runs"].property("selected")
        assert window._menu_actions["runs"].isChecked()


@pytest.mark.parametrize("removed", sorted(REMOVED_PAGES))
@pytest.mark.parametrize("current", ["runs", "logs", "approvals"])
def test_removed_navigation_leaves_current_page_and_target_unchanged(app, current, removed) -> None:
    window = GuardWindow()
    with closing_widgets(window):
        window.navigate(current, "existing-target")
        widget = window.stack.currentWidget()
        target = window._navigation_target
        navigations = []
        window.navigation_requested.connect(lambda *args: navigations.append(args))
        window.navigate(removed, "removed-target")
        assert window._page == current
        assert window.stack.currentWidget() is widget
        assert window._navigation_target == target
        assert window.breadcrumb.text() == PAGES[current]
        assert window._nav_buttons[current].property("selected")
        assert window._menu_actions[current].isChecked()
        assert navigations == []
