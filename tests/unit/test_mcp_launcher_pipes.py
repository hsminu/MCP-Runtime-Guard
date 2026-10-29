"""A09/A10/A15/A16: C launcher through Guard. I1/I6/I8/I10, T10/T14/T16.

A unit composition using a real stdlib MCP subprocess; C5.8 is independent.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from guard_app.adapters.mcp_transport import ToolListLimits, launcher_streams
from guard_app.domain.contracts import Denied, Released, Withheld
from guard_app.domain.enums import RegStatus, TaskStatus
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.security.launcher import ExecutablePin, LaunchError, pin_executable
from guard_app.security.paths import prepare_app_folders
from guard_app.services.product_assembly import assemble_guard
from guard_app.storage.sqlite_store import SQLiteStore

from .logging_state import preserve_logging_state  # noqa: F401
from .test_product_assembly import READ_DEFINITION, Peer, document, inputs
from .test_product_settings import registered

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C launcher is Windows only")

# No package import or inherited PYTHONPATH needed. The synthetic peer reports
# its tools/call receipt count in the content; never executes a real operation.
SERVER = """import json, sys
mode = sys.argv[1]
definition = json.loads(sys.argv[2])
calls = 0
for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if method == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name":"unit", "version":"1"}}
    elif method == "tools/list":
        if mode == "changed": definition["description"] = "changed"
        if mode == "oversize": definition["description"] = "x" * 2048
        result = {"tools": [definition]}
    elif method == "tools/call":
        calls += 1
        if mode == "crash": sys.exit(7)
        result = {"content": [{"type":"text", "text": "receipts=" + str(calls)}],
                  "structuredContent": {"resource_id":req["params"]["arguments"]["path"]}}
    else: continue
    print(json.dumps({"jsonrpc":"2.0", "id":req["id"], "result":result}), flush=True)
"""


@pytest.mark.parametrize(
    "mode", ["normal", "changed", "crash", "oversize", "unpinned", "mismatch", "mixed", "badurl"]
)
def test_real_launcher_guard_listing_call_and_exit(tmp_path, monkeypatch, mode):
    async def run():
        processes = []
        launch = launcher_streams.launch_mcp_server

        async def tracked(*args):
            process = await launch(*args)
            processes.append(process)
            return process

        monkeypatch.setattr(launcher_streams, "launch_mcp_server", tracked)
        folders = prepare_app_folders(tmp_path / "app")
        store = SQLiteStore(tmp_path / "guard.db", audit_id_factory=lambda: uuid4().hex)
        assembled = None
        try:
            command = str(Path(sys.base_prefix) / "python.exe")
            pin = await pin_executable(command)
            doc = document()
            doc["servers"][0].update(
                command=command,
                args=["-u", "-c", SERVER, mode, json.dumps(READ_DEFINITION)],
                command_size=pin.size,
                command_sha256=pin.sha256,
            )
            if mode == "unpinned":
                doc["servers"][0].pop("command_size")
                doc["servers"][0].pop("command_sha256")
            elif mode == "mismatch":
                doc["servers"][0]["command_sha256"] = "0" * 64
            registry = await registered(store, doc)
            if mode == "mixed":
                bad = {**doc["servers"][0], "id": "bad", "command_sha256": "0" * 64}
                doc["servers"].append(bad)
                doc["profile"]["servers"].append("bad")
            elif mode == "badurl":
                # A hand-edited URL that C2.5 refuses blocks only that server (#83 C review 3.2).
                remote = {
                    "id": "remote",
                    "transport": "streamable_http",
                    "url": "http://example.com/mcp",
                    "tier": "general_external",
                    "max_label": "PUBLIC",
                    "tools": [],
                }
                doc["servers"].append(remote)
                doc["profile"]["servers"].append("remote")
            observed = []
            observe = registry.observe

            async def tracked_observe(server, listing):
                observed.append(listing)
                return await observe(server, listing)

            monkeypatch.setattr(registry, "observe", tracked_observe)
            # Product composition uses folders, with no injected stream.
            fake = Peer([READ_DEFINITION])
            product = replace(inputs(fake, registry, doc), folders=folders, server_streams=None)
            if mode == "oversize":
                product = replace(product, tool_list_limits=ToolListLimits(max_page_bytes=1024))
            assembled = await assemble_guard(store, product)
            if mode in {"unpinned", "mismatch"}:
                assert observed == []
                assert assembled.tool_views == ()
                assert assembled.registry.status("docs") is RegStatus.PENDING
                return
            assert len(observed) == 1
            if mode == "oversize":
                assert not observed[0].complete
                assert observed[0].reason == "TOOLS_LIST_PAGE_TOO_LARGE"
                assert assembled.tool_views == ()
                assert assembled.registry.status("docs") is RegStatus.PENDING
                return
            assert observed[0].complete
            assert fake.calls == []
            state = TaskState(
                task_id="task-1",
                epoch=1,
                profile_id=assembled.settings.profile_id,
                policy_version=assembled.settings.policy_version,
                status=TaskStatus.RUNNING,
            )
            await store.create_task(state, created_at_utc=datetime.now(UTC))
            result = await assembled.guard.submit(
                "task-1",
                ProposedCall(
                    tool_ref="docs__read",
                    arguments={"path": "public/a.txt"},
                    model_call_id="call-1",
                ),
                expected_epoch=1,
            )
            if mode == "changed":
                assert isinstance(result, Denied)
                assert assembled.tool_views == ()
                assert registry.status("docs") is RegStatus.PENDING
                assert assembled.registry.mismatched == frozenset({("docs", "read")})
                reloaded = type(registry)(store)
                await reloaded.load()
                assert reloaded.status("docs") is RegStatus.PENDING
            elif mode == "crash":
                assert isinstance(result, Withheld)
                assert assembled.registry.status("docs") is RegStatus.PENDING
            else:
                assert isinstance(result, Released)
                assert "receipts=1" in result.result.text
                assert registry.status("docs") is RegStatus.ACTIVE
                if mode == "mixed":
                    assert assembled.registry.status("bad") is not RegStatus.ACTIVE
                if mode == "badurl":
                    assert assembled.transport.tool_listing("remote") is None
        finally:
            if assembled is not None:
                await assembled.aclose()
            assert len(processes) == (0 if mode in {"unpinned", "mismatch"} else 1)
            for process in processes:
                assert process.returncode is not None
                assert await process.close() is True
            store.close()
            folders.close()

    asyncio.run(asyncio.wait_for(run(), 25))


@pytest.mark.parametrize("confirmed", [True, False])
def test_launcher_close_confirmation_and_cancel(tmp_path, monkeypatch, confirmed):
    async def run():
        peer = Peer([])
        closing = asyncio.Event()
        finish = asyncio.Event()
        closed = asyncio.Event()
        calls = []

        class Process:
            read_bytes = peer.read
            write_bytes = peer.write
            drain = peer.drain

            async def close(self):
                async def shutdown():
                    closing.set()
                    await asyncio.wait_for(finish.wait(), 2)
                    closed.set()
                    return confirmed

                self.shutdown = asyncio.create_task(shutdown())
                return await asyncio.shield(self.shutdown)

        async def launch(*args):
            calls.append(args)
            return Process()

        monkeypatch.setattr(launcher_streams, "launch_mcp_server", launch)
        pin = ExecutablePin(1, "a" * 64)
        factory = launcher_streams.launcher_stdio_stream_factory(
            tmp_path, "C:/trusted.exe", ("arg",), ("ALLOWED",), pin
        )

        async def owner():
            async with factory():
                pass

        task = asyncio.create_task(owner())
        await asyncio.wait_for(closing.wait(), 2)
        if confirmed:
            # Cancelling the waiter cannot cancel C's shutdown; its own close
            # implementation shields the task that owns that shutdown.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            finish.set()
            await asyncio.wait_for(closed.wait(), 2)
        else:
            finish.set()
            with pytest.raises(LaunchError, match="LAUNCH_EXIT_UNCONFIRMED"):
                await asyncio.wait_for(task, 2)
        assert closed.is_set()
        assert calls == [(tmp_path, "C:/trusted.exe", ("arg",), ("ALLOWED",), pin)]

    asyncio.run(asyncio.wait_for(run(), 5))


def test_unconfirmed_startup_cleanup_is_not_a_server_local_refusal(tmp_path, monkeypatch):
    """I6: entering the SDK context can fail before its exit tracker is entered."""
    from guard_app.adapters.mcp_transport import McpTransport, sdk_session
    from guard_app.domain.errors import DispatchError

    async def run():
        class Process:
            async def read_bytes(self, _size):
                return b""

            async def write_bytes(self, _frame):
                pass

            async def drain(self):
                pass

            async def close(self):
                return False

        async def launch(*_args):
            return Process()

        monkeypatch.setattr(launcher_streams, "launch_mcp_server", launch)

        async def broken_enter(_self):
            raise RuntimeError("synthetic SDK context entry failure")

        monkeypatch.setattr(sdk_session._UnvalidatedClientSession, "__aenter__", broken_enter)
        factory = launcher_streams.launcher_stdio_stream_factory(tmp_path, None, (), (), None)
        transport = McpTransport(
            {"bad": factory}, tool_list_limits=ToolListLimits(), isolate_start_failures=True
        )
        with pytest.raises(DispatchError) as failed_start:
            await transport.start()
        assert failed_start.value.exception_type == "SessionCloseFailed"
        with pytest.raises(DispatchError) as failed_close:
            await transport.close()
        assert failed_close.value.exception_type == "SessionCloseFailed"

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.asyncio
async def test_product_rejects_folders_and_injected_streams(tmp_path):
    """I1/I6: reject ambiguous inputs before any server connection."""
    from guard_app.services.product_assembly import ProductAssemblyError

    from .test_product_assembly import Registry, spec

    folders = prepare_app_folders(tmp_path / "app")
    peer = Peer([READ_DEFINITION])
    product = replace(
        inputs(peer, Registry(spec(READ_DEFINITION).fingerprint), document()), folders=folders
    )
    try:
        with pytest.raises(ProductAssemblyError, match="PRODUCT_INPUTS_INVALID"):
            await assemble_guard(None, product)
        assert peer.calls == []
    finally:
        folders.close()


@pytest.mark.usefixtures("preserve_logging_state")
def test_main_settings_start_uses_launcher_folders(tmp_path, monkeypatch):
    """I1/EXE-01/REC-01: real main -> bootstrap -> settings assembly, no Task.

    main() installs C's log redaction process-wide (A146); without the fixture a
    later test in the same run sees its own log records withheld (#83 C review).
    """
    from PySide6.QtWidgets import QApplication

    from guard_app import __main__ as entry
    from guard_app.security import paths
    from guard_app.services.codex_preparation import CodexProcessPreparation

    root = tmp_path / "app"
    folders = prepare_app_folders(root)
    doc = document()
    doc["servers"][0].update(command_size=123, command_sha256="a" * 64)
    folders.paths.settings_file.write_text(
        json.dumps({"schema_version": 1, "settings": doc}), encoding="utf-8"
    )
    folders.close()
    peer = Peer([READ_DEFINITION])
    launched, closed = [], []

    class Process:
        read_bytes = peer.read
        write_bytes = peer.write
        drain = peer.drain

        async def close(self):
            closed.append(True)
            return True

    async def launch(*args):
        launched.append(args)
        return Process()

    class FinishedError(Exception):
        pass

    def bridge(bootstrap, **kwargs):
        async def run():
            async with asyncio.timeout(10):
                bindings = await bootstrap()
                try:
                    assert bindings.human_control is not None
                    assert len(launched) == 1
                    assert launched[0][0].parent == root / "servers"
                    assert launched[0][-1] == ExecutablePin(123, "a" * 64)
                    assert peer.calls == []
                finally:
                    await bindings.close()
                assert closed == [True]

        asyncio.run(run())
        raise FinishedError

    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(CodexProcessPreparation, "preparation", lambda self: None)
    monkeypatch.setattr(launcher_streams, "launch_mcp_server", launch)
    monkeypatch.setattr("guard_app.services.bridge.BackendBridge", bridge)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    app = QApplication.instance() or QApplication([])
    previous = app.quitOnLastWindowClosed()
    try:
        with pytest.raises(FinishedError):
            entry.main()
    finally:
        app.setQuitOnLastWindowClosed(previous)
    prepare_app_folders(root).close()
