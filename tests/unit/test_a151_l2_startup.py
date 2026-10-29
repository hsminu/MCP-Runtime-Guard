"""A151: I6/I9/I11, T22/T39/T40. A wiring only; no actual codex.exe."""

import asyncio
import threading
from contextlib import closing
from dataclasses import replace

import pytest

from guard_app.security.codex_home import load_codex_pins
from guard_app.security.codex_l2 import L2Report, probe_tools
from guard_app.services import codex_preparation as module
from guard_app.services.bridge import BackendBridge, BridgeState, SessionOptions, _ProcessStartGate
from guard_app.services.bridge_types import BackendCleanupError
from guard_app.services.session_runtime import SessionRuntimeError, bootstrap_product
from guard_app.services.session_startup import startup_refusal
from guard_app.ui.presentation import LIFECYCLE_MESSAGES, session_blocker_notice

from .test_a138_product_startup import runtime_context
from .test_bridge import app as app
from .test_bridge import pump_until
from .test_codex_preparation import pin as pin
from .test_codex_preparation import preparation
from .test_session_runtime import make_runtime, synthetic_preparation


def report(launcher, *, failures=(), changed=None):
    pins = load_codex_pins(launcher.source_dir)
    value = L2Report(
        not failures,
        failures,
        launcher.home_digest,
        launcher.executable_sha256,
        dict(pins.home_sha256)["model_catalog.json"],
    )
    return replace(value, **{changed: "f" * 64}) if changed else value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("pass", None),
        ("fail", "L2_TOOLS_UNEXPECTED"),
        ("deadline", "L2_DEADLINE_EXCEEDED"),
        ("exception", "CODEX_L2_FAILED"),
        ("home_digest", "CODEX_CONFIG_MISMATCH"),
        ("executable_sha256", "CODEX_CONFIG_MISMATCH"),
        ("model_sha256", "CODEX_CONFIG_MISMATCH"),
    ],
)
async def test_receipt_and_runtime_gate(tmp_path, pin, monkeypatch, mode, code):
    calls = []

    async def run(launcher, folders, **kwargs):
        calls.append((launcher, folders, kwargs))
        if mode == "exception":
            raise ValueError("private token/path must not escape")
        failures = {"fail": ("L2_TOOLS_UNEXPECTED",), "deadline": ("L2_DEADLINE_EXCEEDED",)}
        return report(
            launcher,
            failures=failures.get(mode, ()),
            changed=mode if mode.endswith("digest") or mode.endswith("sha256") else None,
        )

    monkeypatch.setattr(module, "run_l2", run)
    owner = preparation(tmp_path, pin)
    # Only L1/L2 startup is real here. Task process operations are counted fakes.
    callbacks = replace(
        synthetic_preparation(), prepare_startup=owner.preparation().prepare_startup
    )
    with closing(owner):
        async with runtime_context(tmp_path, preparation=callbacks) as (runtime, sessions):
            async with asyncio.timeout(5):
                await runtime.prepare_startup()
                await runtime.prepare_startup()
                assert runtime.creation_blocker() == code
                if code is None:
                    await runtime.create_session(SessionOptions())
                    assert len(sessions) == 1
                else:
                    for _ in range(2):
                        with pytest.raises(SessionRuntimeError, match=f"^{code}$"):
                            await runtime.create_session(SessionOptions())
                    assert sessions == []
                    assert (
                        "시작 검사" in session_blocker_notice(code)
                        or code == "CODEX_CONFIG_MISMATCH"
                    )
                assert len(calls) == 1
                assert calls[0][0] is owner._launcher
                assert calls[0][1] is owner.folders
                assert calls[0][2]["deadline_s"] == module.L2_DEADLINE_S


@pytest.mark.asyncio
async def test_outer_timeout_waits_for_cancel_cleanup(tmp_path, pin, monkeypatch):
    cleaned, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def run(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release.wait()
            cleaned.set()

    monkeypatch.setattr(module, "run_l2", run)
    monkeypatch.setattr(module, "L2_TOTAL_TIMEOUT_S", 0.01)
    with closing(preparation(tmp_path, pin)) as owner:
        operation = asyncio.create_task(owner.preparation().prepare_startup())
        try:
            async with asyncio.timeout(5):
                await cancelling.wait()
                assert not operation.done()
                release.set()
                receipt = await operation
            assert cleaned.is_set()
            assert startup_refusal(receipt) == "CODEX_L2_FAILED"
        finally:
            release.set()
            done, _ = await asyncio.wait({operation}, timeout=1)
            if done and not operation.cancelled():
                operation.exception()


@pytest.mark.asyncio
async def test_unconfirmed_cleanup_is_not_a_normal_l2_failure(tmp_path, pin, monkeypatch):
    release = asyncio.Event()
    pending = []

    async def run(*args, **kwargs):
        pending.append(asyncio.current_task())
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()

    monkeypatch.setattr(module, "run_l2", run)
    monkeypatch.setattr(module, "L2_TOTAL_TIMEOUT_S", 0.01)
    monkeypatch.setattr(module, "L2_CANCEL_TIMEOUT_S", 0.01)
    owner = preparation(tmp_path, pin)
    runtime, _ = make_runtime(tmp_path, preparation=owner.preparation())
    try:
        async with asyncio.timeout(5):
            with pytest.raises(BackendCleanupError):
                await runtime.prepare_startup()
    finally:
        release.set()
        await asyncio.wait(pending, timeout=1)
        await runtime.close()
        owner.close()


@pytest.mark.asyncio
async def test_l2_uses_product_thread_start_arguments():
    from guard_app.adapters.codex_protocol import DynamicToolsPlan
    from guard_app.services import codex_session
    from guard_app.services.bridge import CODEX_MODEL_ID
    from guard_app.services.codex_prompt import BASE_INSTRUCTIONS
    from guard_app.services.guard_relay import TrustedTask

    from .fake_codex_app_server import FakeAppServer

    assert module.thread_start_params is codex_session.thread_start_params
    tools = probe_tools()
    fake = FakeAppServer()

    async def relay(call):
        pytest.fail("startup must not call Guard")

    session = codex_session.CodexAppServerSession(
        fake,
        task=TrustedTask("unit", 0),
        plan=DynamicToolsPlan(tools, (), frozenset()),
        relay=relay,
        model=CODEX_MODEL_ID,
        base_instructions=BASE_INSTRUCTIONS,
        version="1",
    )
    try:
        async with asyncio.timeout(5):
            await session.start()
            assert module.l2_thread_params(tools) == fake.thread_params
    finally:
        await session.aclose()


@pytest.mark.parametrize("cancel", [False, True])
def test_bridge_progress_activation_and_shutdown(tmp_path, pin, monkeypatch, app, cancel):
    from PySide6.QtCore import QTimer

    from guard_app.services import product_startup

    entered, cancelling, cleaned = threading.Event(), threading.Event(), threading.Event()
    control, activated, events, ticks, threads = [], [], [], [], []
    startup_settled = []
    original = product_startup.SQLiteStore.activate_run

    async def activate(store):
        assert cleaned.is_set()
        activated.append(True)
        return await original(store)

    async def run(launcher, folders, **kwargs):
        release, cleanup_release = asyncio.Event(), asyncio.Event()
        control.append((asyncio.get_running_loop(), release, cleanup_release))
        threads.append(threading.get_ident())
        entered.set()
        try:
            await release.wait()
            return report(launcher)
        finally:
            if cancel:
                cancelling.set()
                await cleanup_release.wait()
            cleaned.set()

    monkeypatch.setattr(module, "run_l2", run)
    monkeypatch.setattr(product_startup.SQLiteStore, "activate_run", activate)
    with closing(preparation(tmp_path, pin)) as owner:
        callbacks = owner.preparation()

        async def observed_startup():
            try:
                return await callbacks.prepare_startup()
            finally:
                startup_settled.append(cleaned.is_set())

        bridge = BackendBridge(
            lambda: bootstrap_product(
                owner.folders, preparation=replace(callbacks, prepare_startup=observed_startup)
            ),
            startup_timeout=module.PRODUCT_STARTUP_TIMEOUT_S,
            shutdown_timeout=module.PRODUCT_SHUTDOWN_TIMEOUT_S,
            start_gate=_ProcessStartGate(),
        )
        owner.set_progress_listener(bridge.codex_progress)
        bridge.lifecycle_changed.connect(events.append)
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(1)
        try:
            bridge.start()
            pump_until(
                app,
                lambda: (
                    entered.is_set()
                    and ticks
                    and any(event.code == "CODEX_L2_CHECK" for event in events)
                ),
                timeout=5,
            )
            assert activated == []
            assert threads == [bridge._host._thread.ident]
            assert threads[0] != threading.get_ident()
            assert "검사 중" in LIFECYCLE_MESSAGES["CODEX_L2_CHECK"]
            loop, release, cleanup_release = control[0]
            if cancel:
                bridge.close()
                pump_until(app, cancelling.is_set, timeout=5)
                assert not bridge._host._thread_done.is_set()
                assert not cleaned.is_set()
                loop.call_soon_threadsafe(cleanup_release.set)
            else:
                loop.call_soon_threadsafe(release.set)
                pump_until(app, lambda: bridge.state is BridgeState.READY, timeout=5)
                assert activated == [True]
                bridge.close()
            pump_until(app, bridge._host._thread_done.is_set, timeout=5)
            assert cleaned.is_set()
            assert startup_settled == [True]
            assert bridge.state is BridgeState.CLOSED
            assert bridge._host._state_code == "CLOSED"
            assert activated == ([] if cancel else [True])
        finally:
            timer.stop()
            if control and not bridge._host._thread_done.is_set():
                loop, release, cleanup_release = control[0]
                loop.call_soon_threadsafe(release.set)
                loop.call_soon_threadsafe(cleanup_release.set)
                bridge.close()
                pump_until(app, bridge._host._thread_done.is_set, timeout=5)


def test_startup_budget_covers_one_window_of_server_starts():
    """#83 C review 3.3: the bridge start budget names server assembly next to Store and L2."""
    import inspect

    from guard_app.adapters.mcp_transport import McpTransport, ToolListLimits
    from guard_app.security.launcher import LaunchedProcess

    start = inspect.signature(McpTransport).parameters["startup_timeout_s"].default
    close = inspect.signature(LaunchedProcess.close).parameters
    window = max(start, ToolListLimits().collection_timeout_s) + 1.0
    assert window + close["grace_s"].default + close["kill_s"].default <= module.SERVER_ASSEMBLY_S
    assert module.PRODUCT_STARTUP_TIMEOUT_S == (
        module.STORE_STARTUP_S
        + module.SERVER_ASSEMBLY_S
        + module.L2_TOTAL_TIMEOUT_S
        + module.L2_CANCEL_TIMEOUT_S
        + 10.0
    )


def test_startup_progress_shows_on_the_default_session_screen(app):
    """Real-run follow-up: the L2 check was visible only in the activity panel."""
    from guard_app.services.bridge import LifecycleEvent
    from guard_app.ui.runtime import RuntimePanel
    from guard_app.ui.window import GuardWindow

    bridge = BackendBridge(lambda: None)
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    notice = window.sessions.current.notice
    try:
        idle = notice.text()
        for code in ("STORE_CHECK", "CODEX_L2_CHECK"):
            bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.STARTING, code))
            assert notice.text() == LIFECYCLE_MESSAGES[code]
            assert notice.isVisibleTo(window)
        bridge.lifecycle_changed.emit(LifecycleEvent(BridgeState.READY, "READY"))
        assert notice.text() == idle
    finally:
        window.close()
        app.processEvents()
