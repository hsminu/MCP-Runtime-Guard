"""A138: REC-01–05/SEQ-01–03, I1/I5/I6/I8/I9, T11/T19/T21/T29/T41/T42/T45.

Real SQLite startup and A boundary counters. C process/crash acceptance is separate.
"""

import asyncio
import sqlite3
import time
from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import (
    RecoveryCategory,
    RequestStatus,
    StorePrepareStep,
    StoreStartFailure,
    TaskStatus,
)
from guard_app.domain.errors import StorePrepareError
from guard_app.domain.models import RecoveryItem, RecoveryItemPage, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.paths import prepare_app_folders
from guard_app.services import session_runtime
from guard_app.services.bridge import BackendBridge, BridgeState, SessionOptions, _ProcessStartGate
from guard_app.services.bridge_types import BackendCleanupError
from guard_app.services.runner import CodexSessionError
from guard_app.services.session_execution import ExecutionGate
from guard_app.services.session_runtime import SessionRuntimeError, bootstrap_product
from guard_app.storage.sqlite_store import _SCHEMA_V2, APPLICATION_ID, SQLiteStore
from guard_app.ui.presentation import LIFECYCLE_MESSAGES
from guard_app.ui.recovery import RecoveryPanel

from .fake_codex_app_server import FakeAppServer
from .product_folders import product_folders
from .test_bridge import pump_until
from .test_codex_product_flow import preparation, read_then_answer
from .test_codex_session import FakeGuard, released, started
from .test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs, spec
from .test_session_runtime import Session, synthetic_preparation


@pytest.mark.asyncio
async def test_order_shared_deadline_activation_barrier_and_callback_isolation(
    tmp_path, monkeypatch
):
    events, deadlines, stores, sessions = [], [], [], []
    entered, release = asyncio.Event(), asyncio.Event()
    peer = Peer([READ_DEFINITION])
    original_assemble = session_runtime.assemble_guard
    original_load = session_runtime.load_settings_profile

    async def load(settings, store):
        events.append("settings")
        assert store.execution_state().phase.value == "RECOVERED"
        return await original_load(settings, store)

    class Observed(SQLiteStore):
        def __init__(self, *args, **kwargs):
            assert kwargs["startup"] == "explicit"
            super().__init__(*args, **kwargs)
            stores.append(self)

        async def prepare_schema(self, **kwargs):
            events.append("prepare")
            deadlines.append(kwargs["deadline"])
            return await super().prepare_schema(**kwargs)

        async def recover(self, **kwargs):
            events.append("recover")
            deadlines.append(kwargs["deadline"])
            assert kwargs["run_id"].startswith("run-")
            return await super().recover(**kwargs)

        async def activate_run(self):
            events.append("activate")
            entered.set()
            await release.wait()
            value = await super().activate_run()
            events.append("ack")
            return value

    async def assemble(store, product):
        events.append("assemble")
        assert not store.execution_state().execution_enabled
        assert product.next_call_seq == store.allocate_call_seq
        return await original_assemble(store, product)

    def step(value):
        assert isinstance(value, StorePrepareStep)
        events.append(value.value)
        raise RuntimeError("private-callback-text")

    monkeypatch.setattr(session_runtime, "SQLiteStore", Observed)
    monkeypatch.setattr(session_runtime, "assemble_guard", assemble)
    monkeypatch.setattr(session_runtime, "load_settings_profile", load)
    started = time.monotonic()
    async with asyncio.timeout(10):
        boot = asyncio.create_task(
            bootstrap_product(
                product_folders(tmp_path / "start.db"),
                on_step=step,
                preparation=preparation(sessions, read_then_answer),
                product=inputs(peer, Registry(spec(READ_DEFINITION).fingerprint), document()),
                settings=session_runtime.SettingsFile(object(), document(), None),
            )
        )
        await entered.wait()
        assert not boot.done()
        assert not sessions
        assert not peer.calls
        assert await stores[0].list_tasks() == ()
        release.set()
        bindings = await boot
        try:
            assert [item for item in events if not item.startswith("STORE_")] == [
                "prepare",
                "recover",
                "settings",
                "assemble",
                "activate",
                "ack",
            ]
            assert deadlines[0] == deadlines[1]
            assert started + 60 <= deadlines[0] <= time.monotonic() + 60
            assert "STORE_RECOVER" in events
            assert "STORE_CHECK" in events
            assert bindings.recovery_report is not None
            assert bindings.recovery_report.run_id == stores[0].execution_state().run_id
            assert bindings.create_session is not None
            task = await bindings.create_session(SessionOptions())
            assert await stores[0].allocate_call_seq(task.task_id) == 1
            assert await stores[0].allocate_call_seq(task.task_id) == 2
            stores[0].halt_execution(Reason.INTERNAL_ERROR)
            with pytest.raises(SessionRuntimeError, match="CODEX_STORE_HALTED"):
                await bindings.create_session(SessionOptions())
            assert len(sessions) == 1
        finally:
            await bindings.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare_schema", "recover", "activate_run"])
@pytest.mark.parametrize("confirmed", [True, False])
async def test_cancel_start_closes_writer_and_marks_unconfirmed(
    tmp_path, monkeypatch, phase, confirmed
):
    entered = asyncio.Event()
    stores = []

    class Observed(SQLiteStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            stores.append(self)

        async def wait_closed(self, timeout):  # noqa: ASYNC109
            assert timeout == 5.0
            assert await super().wait_closed(timeout)
            return confirmed

    async def parked(self, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Observed, phase, parked)
    monkeypatch.setattr(session_runtime, "SQLiteStore", Observed)
    async with asyncio.timeout(10):
        boot = asyncio.create_task(bootstrap_product(product_folders(tmp_path / "cancel.db")))
        await entered.wait()
        boot.cancel()
        with pytest.raises(asyncio.CancelledError if confirmed else BackendCleanupError):
            await boot
        assert not stores[0].execution_state().execution_enabled
        assert await SQLiteStore.wait_closed(stores[0], 1)


@pytest.mark.parametrize("code", list(StoreStartFailure))
def test_start_failure_bridge_and_fixed_ui_message(tmp_path, monkeypatch, code):
    app = QApplication.instance() or QApplication([])
    stores = []

    class Refused(SQLiteStore):
        async def prepare_schema(self, **kwargs):
            stores.append(self)
            raise StorePrepareError(code)

    monkeypatch.setattr(session_runtime, "SQLiteStore", Refused)
    bridge = BackendBridge(
        lambda: bootstrap_product(product_folders(tmp_path / "failure.db")),
        start_gate=_ProcessStartGate(),
    )
    events = []
    bridge.lifecycle_changed.connect(events.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.thread_done)
        app.processEvents()
        assert events[-1].state is BridgeState.FAILED
        assert events[-1].code == code.value
        assert not bridge.cleanup_unconfirmed
        assert "다시 시작하세요" in LIFECYCLE_MESSAGES[code.value]
        assert asyncio.run(stores[0].wait_closed(1))
    finally:
        bridge.close()
        assert bridge.wait_done(5)


@pytest.mark.asyncio
async def test_recovery_from_previous_run_and_v2_backup(tmp_path):
    folders = prepare_app_folders(tmp_path / "app")
    path = folders.db_path()
    # Exact legacy empty v2, then product migration through C's real backup target.
    with sqlite3.connect(path) as db:
        for _, statement in _SCHEMA_V2:
            db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=2")
    steps = []
    try:
        first = await bootstrap_product(folders, on_step=steps.append)
        await first.close()
        assert StorePrepareStep.STORE_BACKUP in steps
        assert StorePrepareStep.STORE_MIGRATE in steps
        assert list(folders.paths.backups.iterdir())
        # Persist an unfinished Task without launching any model/tool.
        store = SQLiteStore(path)
        try:
            await store.create_task(
                TaskState(
                    task_id="previous",
                    epoch=1,
                    profile_id="p",
                    policy_version="v",
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
        finally:
            store.close()
            assert await store.wait_closed(5)
        second = await bootstrap_product(folders)
        try:
            assert second.recovery_report.changed_task_count == 1
            app = QApplication.instance() or QApplication([])
            bridge = BackendBridge(lambda: None)
            panel = RecoveryPanel(bridge)
            panel.show_report(second.recovery_report)
            panel.show_page(second.recovery_page)
            assert "Task 1개를 정지함" in panel.summary.text()
            assert not panel.next_button.isEnabled()
            panel.close()
            app.processEvents()
        finally:
            await second.close()
    finally:
        folders.close()


@pytest.mark.asyncio
async def test_global_halt_blocks_turn_and_interrupts_all_sessions(tmp_path):
    sessions = []

    async def factory(*args):
        session = Session()
        sessions.append(session)
        return session

    async with asyncio.timeout(10):
        bindings = await bootstrap_product(
            product_folders(tmp_path / "halt.db"),
            session_factory=factory,
            preparation=synthetic_preparation(),
            product=inputs(
                Peer([READ_DEFINITION]), Registry(spec(READ_DEFINITION).fingerprint), document()
            ),
        )
        runtime = bindings.close.__self__
        try:
            tasks = [await bindings.create_session(SessionOptions()) for _ in range(2)]
            runner = bindings.runner_for_task(tasks[0].task_id)
            runner.add_user_message("queued")
            runtime._assembled.guard._halt_globally()
            assert not runtime.store.execution_state().execution_enabled
            update = await runner.advance()
            assert update.assistant_text is None
            assert sessions[0].messages == []
            with pytest.raises(SessionRuntimeError, match="CODEX_STORE_HALTED"):
                await bindings.create_session(SessionOptions())
            await runtime._work.wait(3)
            assert all(session.closed == 1 and session.interrupted == 1 for session in sessions)
            assert len(sessions) == 2
        finally:
            await bindings.close()


@pytest.mark.asyncio
async def test_revision_change_latches_even_if_enabled(tmp_path):
    store = SQLiteStore(tmp_path / "revision.db")
    state = store.execution_state()
    gate = ExecutionGate(lambda: state)
    try:
        assert gate.allowed()
        state = state.model_copy(update={"state_revision": state.state_revision + 1})
        assert not gate.allowed()
        state = store.execution_state()
        assert not gate.allowed()
    finally:
        store.close()
        assert await store.wait_closed(5)


@pytest.mark.asyncio
async def test_tool_response_and_turn_check_store_authority(tmp_path):
    store = SQLiteStore(tmp_path / "response.db")
    gate = ExecutionGate(store.execution_state)

    class HaltingGuard(FakeGuard):
        async def submit(self, *args, **kwargs):
            store.halt_execution(Reason.INTERNAL_ERROR)
            return released("private-result-must-not-escape")

    fake = FakeAppServer()
    session = await started(fake, HaltingGuard())
    session._execution_allowed = gate.allowed
    try:
        # Direct request processing holds the returned result until the final authority check.
        reply = await session._tool_call(
            {
                "threadId": session._thread_id,
                "turnId": "turn",
                "callId": "call",
                "namespace": "docs",
                "tool": "read",
                "arguments": {"path": "public/a.txt"},
            }
        )
        assert reply["success"] is False
        assert "private-result" not in repr(reply)
        with pytest.raises(CodexSessionError, match="CODEX_STORE_HALTED"):
            await session.send("new turn")
        assert "turn/start" not in fake.methods()
    finally:
        await session.aclose()
        store.close()
        assert await store.wait_closed(5)


@pytest.mark.asyncio
async def test_idle_revision_watch_closes_sessions(tmp_path):
    closed = asyncio.Event()

    class Watched(Session):
        async def aclose(self):
            await super().aclose()
            closed.set()

    session = Watched()

    async def factory(*args):
        return session

    async with asyncio.timeout(10):
        bindings = await bootstrap_product(
            product_folders(tmp_path / "watch.db"),
            session_factory=factory,
            preparation=synthetic_preparation(),
            product=inputs(
                Peer([READ_DEFINITION]), Registry(spec(READ_DEFINITION).fingerprint), document()
            ),
        )
        try:
            await bindings.create_session(SessionOptions())
            runtime = bindings.close.__self__
            runtime.store.halt_execution(Reason.INTERNAL_ERROR)
            await asyncio.wait_for(closed.wait(), 2)
            assert session.interrupted == 1
        finally:
            await bindings.close()


@pytest.mark.asyncio
async def test_recovery_pages_keep_older_items_and_100_limit(tmp_path, monkeypatch):
    calls = []

    class Paged(SQLiteStore):
        async def list_recovery_items(self, *, run_id=None, after=None, limit=100):
            calls.append((run_id, after, limit))
            return RecoveryItemPage(
                items=(
                    RecoveryItem(
                        run_id="earlier-run",
                        task_id="old-task",
                        request_id="old-request",
                        original_status=RequestStatus.UNKNOWN_OUTCOME,
                        category=RecoveryCategory.LEGACY_METADATA_MISSING,
                        at_utc=datetime.now(UTC),
                        server_id="stored-server",
                        tool_name="stored-tool",
                        destination_id="stored-destination",
                        kind=None,
                        epoch=None,
                    ),
                ),
                next_after="old-request" if after is None else None,
            )

    monkeypatch.setattr(session_runtime, "SQLiteStore", Paged)
    app = QApplication.instance() or QApplication([])
    bindings = await bootstrap_product(product_folders(tmp_path / "pages.db"))
    bridge = BackendBridge(lambda: None)
    panel = RecoveryPanel(bridge)
    try:
        panel.show_report(bindings.recovery_report)
        panel.show_page(bindings.recovery_page)
        assert panel.next_button.isEnabled()
        page = await bindings.recovery_next()
        panel.show_page(page)
        assert not panel.next_button.isEnabled()
        assert "stored-server" in panel.items.toPlainText()
        assert "stored-tool" in panel.items.toPlainText()
        assert "stored-destination" in panel.items.toPlainText()
        assert "미제공" in panel.items.toPlainText()
        assert calls == [
            (None, None, 100),
            (None, "old-request", 100),
        ]
        assert "stored-server" not in repr(page)
    finally:
        panel.close()
        app.processEvents()
        await bindings.close()


@pytest.mark.parametrize("code", list(StoreStartFailure))
def test_product_window_returns_start_failure_exit_code(monkeypatch, code):
    import argparse
    from types import SimpleNamespace

    from PySide6.QtCore import QTimer

    from guard_app import __main__ as entry
    from guard_app.services import bridge as bridge_module

    app = QApplication.instance() or QApplication([])
    previous = app.quitOnLastWindowClosed()
    seen = []

    async def refused(*args, **kwargs):
        raise StorePrepareError(code)

    class ObservedBridge(BackendBridge):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, start_gate=_ProcessStartGate())
            self.lifecycle_changed.connect(self.observe)

        def observe(self, event):
            if event.state is BridgeState.FAILED:
                seen.append(event.code)
                app.closeAllWindows()

    monkeypatch.setattr(session_runtime, "bootstrap_product", refused)
    monkeypatch.setattr(bridge_module, "BackendBridge", ObservedBridge)
    owner = SimpleNamespace(
        folders=object(),
        preparation=lambda: None,
        account_access=lambda **kw: None,
    )
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(app.quit)
    timeout.start(5000)
    try:
        result, confirmed = entry._run(argparse.Namespace(demo=False, store=None), owner)
        assert seen == [code.value]
        assert result == 1
        assert confirmed
    finally:
        timeout.stop()
        app.setQuitOnLastWindowClosed(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["turn", "reply"])
async def test_queued_wire_frame_rechecks_execution(tmp_path, monkeypatch, operation):
    from guard_app.adapters.jsonrpc_client import JsonRpcError

    store = SQLiteStore(tmp_path / "wire.db")
    fake = FakeAppServer()
    session = await started(fake, FakeGuard(released("withheld-at-wire")))
    session._execution_allowed = ExecutionGate(store.execution_state).allowed
    entered = asyncio.Event()
    original = session._client._send

    async def queued(message, **kwargs):
        entered.set()
        await original(message, **kwargs)

    monkeypatch.setattr(session._client, "_send", queued)
    await session._client._send_lock.acquire()
    async with asyncio.timeout(5):
        work = asyncio.create_task(
            session.send("queued")
            if operation == "turn"
            else session._client._answer(
                9999,
                "item/tool/call",
                {
                    "threadId": session._thread_id,
                    "turnId": "turn",
                    "callId": "call",
                    "namespace": "docs",
                    "tool": "read",
                    "arguments": {"path": "a"},
                },
            )
        )
        try:
            await entered.wait()
            store.halt_execution(Reason.INTERNAL_ERROR)
            session._client._send_lock.release()
            with pytest.raises((CodexSessionError, JsonRpcError)):
                await work
            assert "turn/start" not in fake.methods()
            assert 9999 not in fake.replies
        finally:
            if session._client._send_lock.locked():
                session._client._send_lock.release()
            await session.aclose()
            store.close()
            assert await store.wait_closed(5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "confirmed"), [("stdio", True), ("stdio", False), ("http", True)]
)
async def test_settings_build_real_guard_and_own_server_exit(
    tmp_path, monkeypatch, transport, confirmed
):
    from guard_app.adapters.mcp_transport.streams import validated_stdio_stream_factory
    from guard_app.security.ids import new_id
    from guard_app.services import product_assembly as assembly
    from guard_app.services.product_settings import SettingsFile

    from .test_product_settings import registered

    folders = prepare_app_folders(tmp_path / "app")
    doc = document()
    server = doc["servers"][0]
    if transport == "stdio":
        server.update(command_size=123, command_sha256="a" * 64)
    else:
        server.pop("command")
        server.update(transport="streamable_http", url="http://127.0.0.1:80/mcp")
    store = SQLiteStore(folders.db_path(), audit_id_factory=lambda: new_id("audit"))
    try:
        await registered(store, doc)
    finally:
        store.close()
        assert await store.wait_closed(5)
    peer = Peer([READ_DEFINITION])
    launched, closed, endpoints = [], [], []

    class Process:
        read_bytes = peer.read
        write_bytes = peer.write
        drain = peer.drain

        async def close(self, **kwargs):
            closed.append(kwargs)
            return confirmed

    async def launch(*args):
        launched.append(args)
        return Process()

    def http(endpoint):
        endpoints.append(endpoint)
        return validated_stdio_stream_factory(peer.read, peer.write, peer.drain)

    monkeypatch.setattr(assembly, "launch_mcp_server", launch)
    monkeypatch.setattr(assembly, "http_stream_factory", http)
    try:
        bindings = await bootstrap_product(folders, settings=SettingsFile(folders, doc, None))
        assert bindings.human_control is not None
        assert bindings.policy_access is not None
        assert bindings.policy is not None
        assert peer.calls == []  # listing only; no tool execution during startup
        if confirmed:
            await bindings.close()
        else:
            with pytest.raises(BackendCleanupError):
                await bindings.close()
        if transport == "stdio":
            assert len(launched) == len(closed) == 1
            assert launched[0][-1].size == 123
            assert launched[0][-1].sha256 == "a" * 64
        else:
            assert not launched
            assert endpoints == ["http://127.0.0.1/mcp"]
    finally:
        folders.close()
