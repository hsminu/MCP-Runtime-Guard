"""A205: selection boxes ignore the mouse wheel; the page scrolls instead."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from guard_app.ui.call_logs import CallLogPage, CallLogQuery, CallLogResult, PublicCallSummary
from guard_app.ui.widgets import SelectionBox

from .test_call_logs import app as app

# GUI behavior under design 12.1/12.2; I1/T08 boundary stays unchanged.
# No dedicated T/S ID covers wheel navigation; these are A205 UI regressions.


def wheel_event(box: SelectionBox, delta: int) -> QWheelEvent:
    pos = box.rect().center()
    return QWheelEvent(
        QPointF(pos),
        QPointF(box.mapToGlobal(pos)),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("delta", [-120, 120])
def test_wheel_is_ignored_with_or_without_focus(app, focused, delta) -> None:
    box = SelectionBox()
    box.addItems(["First", "Second", "Third"])
    box.setCurrentIndex(0)
    changes = QSignalSpy(box.currentIndexChanged)
    try:
        box.show()
        box.activateWindow()
        app.processEvents()
        if focused:
            box.setFocus()
        else:
            box.clearFocus()
        app.processEvents()
        assert box.hasFocus() is focused
        assert box.focusPolicy() == Qt.FocusPolicy.StrongFocus
        event = wheel_event(box, delta)
        QApplication.sendEvent(box, event)
        assert box.currentIndex() == 0
        assert changes.count() == 0
        assert not event.isAccepted()
        assert box.hasFocus() is focused
    finally:
        box.close()


def test_keyboard_down_still_changes_selection(app) -> None:
    box = SelectionBox()
    box.addItems(["First", "Second", "Third"])
    changes = QSignalSpy(box.currentIndexChanged)
    try:
        box.show()
        box.setFocus()
        app.processEvents()
        QTest.keyClick(box, Qt.Key.Key_Down)
        assert box.currentIndex() == 1
        assert changes.count() == 1
    finally:
        box.close()


def test_wheel_over_selection_scrolls_parent_area(app) -> None:
    area = QScrollArea()
    content = QWidget()
    content.setMinimumHeight(2000)
    layout = QVBoxLayout(content)
    box = SelectionBox(content)
    box.addItems(["First", "Second", "Third"])
    layout.addWidget(box)
    layout.addStretch()
    area.setWidget(content)
    area.setWidgetResizable(True)
    area.resize(400, 300)
    changes = QSignalSpy(box.currentIndexChanged)
    try:
        area.show()
        app.processEvents()
        assert box.parentWidget() is content
        bar = area.verticalScrollBar()
        assert bar.maximum() > 0
        before = bar.value()
        # Direct QWidget sendEvent does not propagate wheels in this Qt build.
        # QTest delivers a native wheel through QWindow at the combo's position.
        pos = box.mapTo(area, box.rect().center())
        assert area.childAt(pos) is box
        window = area.windowHandle()
        assert window is not None
        QTest.wheelEvent(window, pos, QPoint(0, -120))
        app.processEvents()
        assert box.currentIndex() == 0
        assert changes.count() == 0
        assert bar.value() > before
    finally:
        area.close()


@pytest.mark.parametrize("filter_name", ["server_filter", "status_filter", "period_filter"])
def test_call_log_filters_ignore_wheel_without_query(app, filter_name) -> None:
    page = CallLogPage()
    queries: list[CallLogQuery] = []
    page.query_requested.connect(queries.append)
    try:
        page.set_source_connected(True)
        page.refresh()
        rows = (PublicCallSummary("call-1", server_id="server-1"),)
        assert page.set_page(CallLogResult(queries[-1], rows, 1, None))
        page.show()
        app.processEvents()
        for control in (page.server_filter, page.status_filter, page.period_filter):
            assert isinstance(control, SelectionBox)
        box = getattr(page, filter_name)
        assert box.isEnabled()
        assert box.count() > 1
        before = (box.currentIndex(), box.currentData())
        query_count = len(queries)
        assert query_count == 1
        changes = QSignalSpy(box.currentIndexChanged)
        event = wheel_event(box, -120)
        QApplication.sendEvent(box, event)
        app.processEvents()
        assert (box.currentIndex(), box.currentData()) == before
        assert changes.count() == 0
        assert len(queries) == query_count
        assert not event.isAccepted()
    finally:
        page.close()
