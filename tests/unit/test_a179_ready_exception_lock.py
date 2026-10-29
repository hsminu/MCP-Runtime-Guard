"""#101 B-ACCEPT-01 / A179: REC-01, REC-05, I6; T18/T41/T42 shutdown ownership."""

import gc
import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6 import QtCore
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from guard_app import __main__ as entry
from guard_app.security import paths
from guard_app.security.paths import AppPathError
from guard_app.services import bridge as bridge_module
from guard_app.services import codex_preparation, session_runtime
from guard_app.services.bridge import (
    BackendBridge,
    BackendCleanupUnconfirmedError,
    BridgeState,
    _ProcessStartGate,
)
from guard_app.services.codex_preparation import CodexProcessPreparation
from guard_app.ui import application, runtime, window

from .logging_state import preserve_logging_state  # noqa: F401
from .test_bridge import pump_until

pytestmark = pytest.mark.usefixtures("preserve_logging_state")


@pytest.fixture
def app(monkeypatch):
    app = QApplication.instance() or QApplication([])
    existing = set(app.topLevelWidgets())
    previous = app.quitOnLastWindowClosed()
    timers = []

    class TrackedTimer(QTimer):
        def __init__(self):
            super().__init__()
            timers.append(self)

    monkeypatch.setattr(QtCore, "QTimer", TrackedTimer)
    yield app
    # Unconfirmed exits retain widgets until process exit; this test reuses Qt.
    for timer in timers:
        timer.stop()
    assert threading.current_thread() is threading.main_thread()
    for widget in app.topLevelWidgets():
        if widget not in existing:
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.setQuitOnLastWindowClosed(previous)


def assert_claim_refused(root):
    other = CodexProcessPreparation(root=root)
    try:
        with pytest.raises(AppPathError, match="^APP_ALREADY_RUNNING$"):
            other.claim()
    finally:
        other.close()


def assert_claim_succeeds(root):
    other = CodexProcessPreparation(root=root)
    try:
        other.claim()
    finally:
        other.close()


@pytest.fixture
def product(monkeypatch, tmp_path, app):
    root = tmp_path / "app"
    owners, bridges, stores, releases, boots = [], [], [], [], []
    unblock, close_entered = threading.Event(), threading.Event()
    original_bootstrap = session_runtime.bootstrap_product

    class Owner(CodexProcessPreparation):
        def __init__(self, **kwargs):
            super().__init__(root=root, **kwargs)
            owners.append(self)

        def close(self):
            # Observe exactly when ownership is released, before close changes it.
            if stores:
                releases.append(
                    (
                        bridges[0].thread_done,
                        bridges[0].backend_alive,
                        stores[0]._writer._thread.is_alive(),
                        stores[0].execution_state().execution_enabled,
                    )
                )
            super().close()

        def preparation(self):
            return None  # No model, launcher, credentials or network.

        def account_access(self, **kwargs):
            return None

    class Bridge(BackendBridge):
        def __init__(self, *args, **kwargs):
            kwargs["shutdown_timeout"] = 0.1 if state.blocked else 3.0
            super().__init__(*args, **kwargs, start_gate=_ProcessStartGate())
            bridges.append(self)

    async def bootstrap(folders, **kwargs):
        boots.append(True)
        bindings = await original_bootstrap(folders, **kwargs)
        stores.append(bindings.close.__self__.store)
        if not state.blocked:
            return bindings

        async def close():
            # Stall the backend itself: its timeout cannot certify Store's end.
            close_entered.set()
            try:
                assert unblock.wait(10)
            finally:
                await bindings.close()

        return replace(bindings, close=close)

    state = SimpleNamespace(
        root=root,
        owners=owners,
        bridges=bridges,
        stores=stores,
        releases=releases,
        boots=boots,
        blocked=False,
        close_entered=close_entered,
    )
    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(codex_preparation, "CodexProcessPreparation", Owner)
    monkeypatch.setattr(bridge_module, "BackendBridge", Bridge)
    monkeypatch.setattr(session_runtime, "bootstrap_product", bootstrap)
    monkeypatch.setattr(entry, "CLOSE_OBSERVATION_S", 0.1)
    monkeypatch.setattr(sys, "argv", ["guard-app"])

    def exec_ready_exception():
        pump_until(app, lambda: bridges[0].state is BridgeState.READY)
        assert stores[0]._writer._thread.is_alive()
        assert stores[0].execution_state().execution_enabled
        assert_claim_refused(root)
        raise RuntimeError

    monkeypatch.setattr(app, "exec", exec_ready_exception)
    try:
        yield state
    finally:
        unblock.set()
        for bridge in bridges:
            bridge.close()
            assert bridge.wait_done(5)
            if bridge._host._thread is not None:
                bridge._host._thread.join(5)
            assert not bridge.backend_alive
        for store in stores:
            store.close()
            assert store._writer.wait_closed_sync(5)
            assert not store._writer._thread.is_alive()
            assert not store.execution_state().execution_enabled
        for owner in owners:
            owner.close()
        for bridge in bridges:
            bridge.deleteLater()


def test_ready_exception_confirmed_releases_only_after_store_end(product):
    """#101 B-ACCEPT-01: REC-01/REC-05 also hold on the exceptional exit."""
    with pytest.raises(RuntimeError):
        entry.main()
    assert product.boots == [True]
    assert product.releases == [(True, False, False, False)]
    assert_claim_succeeds(product.root)


def test_ready_exception_unconfirmed_keeps_lock_through_process_exit(monkeypatch, product):
    product.blocked = True
    exits = []

    class ProcessEndedError(Exception):
        pass

    def end(code):
        assert product.close_entered.is_set()
        assert not product.bridges[0].thread_done
        assert product.stores[0]._writer._thread.is_alive()
        assert_claim_refused(product.root)
        exits.append(code)
        raise ProcessEndedError

    monkeypatch.setattr(entry.os, "_exit", end)
    with pytest.raises(ProcessEndedError):
        entry.main()
    assert exits == [entry.EXIT_CLEANUP_UNCONFIRMED]
    assert product.releases == []
    assert_claim_refused(product.root)


def test_before_backend_start_exception_releases_lock(monkeypatch, product):
    class BrokenWindow:
        def __init__(self, **kwargs):
            raise RuntimeError

    monkeypatch.setattr(window, "GuardWindow", BrokenWindow)
    with pytest.raises(RuntimeError):
        entry.main()
    assert product.boots == []
    assert not product.bridges[0].backend_alive
    assert_claim_succeeds(product.root)


class Signal:
    def connect(self, callback):
        pass

    def disconnect(self, callback):
        pass


@pytest.mark.parametrize("phase", ["show", "start", "exec", "closing"])
@pytest.mark.parametrize(("done", "unconfirmed"), [(True, False), (False, False), (True, True)])
def test_exception_closes_and_observes_with_remaining_budget(
    monkeypatch, app, phase, done, unconfirmed
):
    calls, widgets = [], []
    original_error = RuntimeError()

    class Window(QWidget):
        def __init__(self, **kwargs):
            super().__init__()
            widgets.append(self)

        def show(self):
            if phase == "show":
                raise original_error
            super().show()

        def attach_runtime(self, panel):
            pass

        def set_runtime_context(self, context):
            pass

        def navigate(self, page):
            pass

        def clear_session_content(self):
            pass

    def start():
        calls.append("start")
        if phase == "start":
            raise original_error

    def run():
        if phase == "closing":
            widgets[0].close()
        raise original_error

    def wait(seconds):
        calls.append(("wait", seconds))
        return done

    bridge = SimpleNamespace(
        lifecycle_changed=Signal(),
        shutdown_timeout=3,
        cleanup_unconfirmed=unconfirmed,
        start=start,
        close=lambda: calls.append("close"),
        wait_done=wait,
    )
    fake_app = SimpleNamespace(aboutToQuit=Signal(), exec=run)
    monkeypatch.setattr(window, "GuardWindow", Window)
    monkeypatch.setattr(runtime, "RuntimePanel", lambda bridge: object())
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr(application.time, "monotonic", lambda: next(ticks))
    error = RuntimeError if done and not unconfirmed else BackendCleanupUnconfirmedError
    with pytest.raises(error) as caught:
        application.run_application(fake_app, bridge, context="unit", close_observation_s=5)
    if error is RuntimeError:
        assert caught.value is original_error
    else:
        assert caught.value.args == ()
    assert calls[-2:] == ["close", ("wait", 6.0 if phase == "closing" else 8.0)]


@pytest.mark.parametrize(("done", "unconfirmed"), [(True, False), (False, False), (True, True)])
def test_normal_return_preserves_exit_code_and_confirmation(monkeypatch, app, done, unconfirmed):
    calls = []
    monkeypatch.setattr(runtime, "RuntimePanel", lambda bridge: QWidget())

    class Window(QWidget):
        def __init__(self, **kwargs):
            super().__init__()

        def attach_runtime(self, panel):
            panel.setParent(self)

        def set_runtime_context(self, context):
            pass

        def navigate(self, page):
            pass

    monkeypatch.setattr(window, "GuardWindow", Window)
    bridge = SimpleNamespace(
        lifecycle_changed=Signal(),
        shutdown_timeout=3,
        cleanup_unconfirmed=unconfirmed,
        start=lambda: calls.append("start"),
        close=lambda: calls.append("close"),
        wait_done=lambda seconds: calls.append(("wait", seconds)) or done,
    )
    fake_app = SimpleNamespace(aboutToQuit=Signal(), exec=lambda: 7)
    assert application.run_application(fake_app, bridge, context="unit", close_observation_s=5) == (
        7,
        done and not unconfirmed,
    )
    assert calls == ["start", ("wait", 8.0)]


def test_demo_cleanup_exception_ends_process_without_owner(monkeypatch):
    exits = []

    def run(*args):
        assert args[1] is None
        raise BackendCleanupUnconfirmedError

    monkeypatch.setattr(sys, "argv", ["guard-app", "--demo"])
    monkeypatch.setattr(entry, "_run", run)
    monkeypatch.setattr(entry, "_end_unconfirmed", lambda: exits.append(True))
    assert entry.main() == entry.EXIT_CLEANUP_UNCONFIRMED
    assert exits == [True]
