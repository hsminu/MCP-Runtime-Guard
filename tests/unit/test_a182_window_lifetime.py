"""A182 / I9, T19/S04 regression support: Qt lifetime under design 12.1.

Real bridge thread and real widgets; no Store, model, network or user app data.
No child enumeration: retain pages through the window's public attributes.
"""

import gc
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6 import QtCore
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BackendCleanupUnconfirmedError,
    BridgeState,
    _ProcessStartGate,
)
from guard_app.ui import application, window


def pump_until(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def observed(monkeypatch, capfd):
    app = QApplication.instance() or QApplication([])
    old_quit = app.quitOnLastWindowClosed()
    existing = set(app.topLevelWidgets())
    failures = []
    monkeypatch.setattr(sys, "excepthook", lambda *_args: failures.append("python"))
    monkeypatch.setattr(sys, "unraisablehook", lambda _args: failures.append("unraisable"))
    monkeypatch.setattr(threading, "excepthook", lambda _args: failures.append("thread"))

    def qt_message(_kind, _context, message):
        if message != "This plugin does not support propagateSizeHints()":
            failures.append("qt")

    old_handler = qInstallMessageHandler(qt_message)
    state = SimpleNamespace(
        app=app,
        blocked=False,
        unblock=threading.Event(),
        close_entered=threading.Event(),
        bridges=[],
        widgets=[],
        panels=[],
        timers=[],
        deletions=[],
        failures=failures,
    )

    class ObservedTimer(QTimer):
        def __init__(self):
            super().__init__()
            state.timers.append(self)

    monkeypatch.setattr(QtCore, "QTimer", ObservedTimer)
    original_window = window.GuardWindow

    class ObservedWindow(original_window):
        def attach_runtime(self, panel):
            state.panels.append(panel)
            super().attach_runtime(panel)

        def deleteLater(self):  # noqa: N802 - Qt API
            # Observe the last lifecycle's public UI state before C++ deletion.
            state.deletions.append(
                (
                    threading.current_thread() is threading.main_thread(),
                    state.panels[0].internal_code.text(),
                    self.sessions.current.draft.toPlainText(),
                )
            )
            super().deleteLater()

    monkeypatch.setattr(window, "GuardWindow", ObservedWindow)
    try:
        yield state
    finally:
        state.unblock.set()
        for bridge in state.bridges:
            bridge.close()
            assert bridge.wait_done(5)
            bridge._host._thread.join(5)
            assert not bridge.backend_alive
        for timer in state.timers:
            timer.stop()
        # Only after the blocked backend ends may the test dispose its window.
        QCoreApplication.sendPostedEvents()
        app.processEvents()
        for widget in app.topLevelWidgets():
            if widget not in existing:
                widget.deleteLater()
        for bridge in state.bridges:
            bridge.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        state.widgets.clear()
        state.panels.clear()
        state.bridges.clear()
        gc.collect()
        app.processEvents()
        app.setQuitOnLastWindowClosed(old_quit)
        qInstallMessageHandler(old_handler)
        assert not failures
        captured = capfd.readouterr()
        assert "Traceback" not in captured.err
        assert "RuntimeError" not in captured.err


def prepare(monkeypatch, state, mode):
    async def close():
        state.close_entered.set()
        if state.blocked:
            # Block the backend thread, including its own shutdown timeout.
            assert state.unblock.wait(10)

    async def stop_session(*_args):
        pass

    def runner_for_task(_task_id):
        raise AssertionError("UNEXPECTED_RUNNER")

    async def bootstrap():
        return BackendBindings(
            runner_for_task=runner_for_task,
            human_control=None,
            stop_session=stop_session,
            close=close,
        )

    class CountingBridge(BackendBridge):
        close_calls = 0

        def close(self):
            self.close_calls += 1
            super().close()

    bridge = CountingBridge(
        bootstrap,
        shutdown_timeout=0.1 if state.blocked else 3,
        start_gate=_ProcessStartGate(),
    )
    state.bridges.append(bridge)
    original_error = RuntimeError()
    native_exec = state.app.exec

    def ready():
        pump_until(
            state.app,
            lambda: (
                bridge.state is BridgeState.READY
                and state.panels[0].internal_code.text() == "내부 코드: READY"
            ),
        )
        matches = [w for w in state.app.topLevelWidgets() if isinstance(w, window.GuardWindow)]
        assert len(matches) == 1
        current = matches[0]
        state.widgets.extend([current, state.panels[0], current.approval_page, current.log_page])
        assert state.panels[0].internal_code.text() == "내부 코드: READY"
        return current

    def close_ready():
        current = ready()
        current.close()
        if not state.blocked:
            assert bridge.wait_done(5)
        # CLOSING was synchronous; CLOSED is still queued from the backend.
        assert state.panels[0].internal_code.text() != "내부 코드: CLOSED"
        # The final GuardWindow lifecycle callback must clear this public draft.
        current.sessions.current.draft.setPlainText("A182 synthetic pending draft")
        if mode == "native":
            state.app.quit()

    def execute():
        if mode == "exception":
            ready()
            raise original_error
        if mode == "native":
            watchdog = QTimer()
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(lambda: state.app.exit(99))
            watchdog.start(7000)
            QTimer.singleShot(0, close_ready)
            try:
                return native_exec()
            finally:
                watchdog.stop()
        close_ready()
        return 7 if state.blocked else 0

    monkeypatch.setattr(state.app, "exec", execute)
    return bridge, original_error


def assert_quit_disconnected(state, bridge):
    before = bridge.close_calls
    state.app.aboutToQuit.emit()
    assert bridge.close_calls == before


@pytest.mark.parametrize("collector", ["main", "worker"])
@pytest.mark.parametrize("mode", ["normal", "exception", "native"])
def test_confirmed_window_and_pages_deleted(monkeypatch, observed, mode, collector):
    bridge, original_error = prepare(monkeypatch, observed, mode)
    if mode == "exception":
        with pytest.raises(RuntimeError) as caught:
            application.run_application(
                observed.app, bridge, context="unit", close_observation_s=0.1
            )
        assert caught.value is original_error
        # Do not retain the application's frame through the exception traceback.
        original_error.__traceback__ = None
        del caught
    else:
        assert application.run_application(
            observed.app, bridge, context="unit", close_observation_s=0.1
        ) == (0, True)
    assert bridge.thread_done
    assert not bridge.cleanup_unconfirmed
    assert len(observed.widgets) == 4
    assert all(not shiboken6.isValid(widget) for widget in observed.widgets)
    assert not any(isinstance(w, window.GuardWindow) for w in observed.app.topLevelWidgets())
    assert all(not timer.isActive() for timer in observed.timers)
    assert observed.deletions == [(True, "내부 코드: CLOSED", "")]
    assert_quit_disconnected(observed, bridge)
    observed.widgets.clear()
    observed.panels.clear()
    if collector == "main":
        gc.collect()
    else:
        worker = threading.Thread(target=gc.collect, name="a182-collector")
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
    observed.app.processEvents()
    assert not observed.failures


@pytest.mark.parametrize("mode", ["exception", "normal"])
def test_unconfirmed_window_is_retained(monkeypatch, observed, mode):
    observed.blocked = True
    bridge, _original_error = prepare(monkeypatch, observed, mode)
    if mode == "exception":
        with pytest.raises(BackendCleanupUnconfirmedError) as caught:
            application.run_application(
                observed.app, bridge, context="unit", close_observation_s=0.1
            )
        assert caught.value.args == ()
    else:
        assert application.run_application(
            observed.app, bridge, context="unit", close_observation_s=0.1
        ) == (7, False)
    assert observed.close_entered.is_set()
    assert bridge.backend_alive
    assert not bridge.thread_done
    assert all(shiboken6.isValid(widget) for widget in observed.widgets)
    assert observed.widgets[0] in observed.app.topLevelWidgets()
    assert observed.deletions == []
    assert_quit_disconnected(observed, bridge)
