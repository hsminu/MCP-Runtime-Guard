"""A88 end to end inside A: product bootstrap -> Codex session -> Guard -> MCP.

Real GuardService, Profile loading, access rules, SQLite Store and transport;
the Codex app-server and MCP server are in-memory fakes. Dispatch counts are the
MCP peer's tools/call receipts (I1, I8, I11, T21/T22 A part). C launcher, L2 and
the real codex.exe are not exercised.
"""

from __future__ import annotations

import asyncio

from guard_app.domain.enums import TaskStatus
from guard_app.security.digest import tool_fingerprint
from guard_app.services.bridge import SessionOptions
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
)
from guard_app.services.session_runtime import bootstrap_product

from .fake_codex_app_server import FakeAppServer
from .test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs

CONFIG = ConfigurationReceipt("a" * 64, "b" * 64, "c" * 64)


def preparation(servers: list[FakeAppServer], script) -> SessionPreparation:
    async def startup() -> StartupReceipt:
        return StartupReceipt(CONFIG, CONFIG, True)

    async def verify(expected: ConfigurationReceipt) -> ConfigurationReceipt:
        return expected

    async def prepare(task_id: str, expected: ConfigurationReceipt) -> TaskResources:
        server = FakeAppServer(script)
        servers.append(server)
        return TaskResources(task_id, expected, f"owner-{task_id}", server)

    async def confirm(_resources: TaskResources) -> bool:
        return True

    async def cleanup(_resources: TaskResources) -> None:
        pass

    return SessionPreparation(startup, verify, prepare, confirm, cleanup)


async def read_then_answer(server: FakeAppServer, turn_id: str, _text: str) -> None:
    server.last_reply = await server.tool_call(turn_id, "c1", "read", {"path": "public/a.txt"})  # type: ignore[attr-defined]
    await server.agent(turn_id, "summary of the document")
    server.complete(turn_id)


async def run_one_turn(tmp_path, script, *, allow: bool = True):
    peer = Peer([READ_DEFINITION])
    servers: list[FakeAppServer] = []
    bindings = await bootstrap_product(
        str(tmp_path / "flow.db"),
        preparation=preparation(servers, script),
        product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document(allow=allow)),
    )
    try:
        assert bindings.session_blocker is not None
        assert bindings.session_blocker() is None
        assert bindings.create_session is not None
        created = await bindings.create_session(SessionOptions())
        assert created.tools_available
        runner = bindings.runner_for_task(created.task_id)
        runner.add_user_message("summarize the public document")
        update = await asyncio.wait_for(runner.advance(), 5)
        assert bindings.snapshot is not None
        after = await bindings.snapshot(created.task_id)
        return update, after, peer, servers
    finally:
        assert bindings.close is not None
        await bindings.close()


def test_codex_tool_call_runs_through_guard_and_the_answer_returns(tmp_path) -> None:
    update, after, peer, [server] = asyncio.run(run_one_turn(tmp_path, read_then_answer))
    assert update.status == "complete"
    assert update.assistant_text == "summary of the document"
    assert len(peer.calls) == 1
    result = server.last_reply["result"]  # type: ignore[attr-defined]
    assert result["success"] is True
    assert result["contentItems"][0]["text"] == "public text"
    assert after.status is TaskStatus.RUNNING
    assert server.thread_params is not None
    [namespace] = server.thread_params["dynamicTools"]
    assert [tool["name"] for tool in namespace["tools"]] == ["read"]


def test_denied_call_never_reaches_the_mcp_server(tmp_path) -> None:
    update, _after, peer, [server] = asyncio.run(
        run_one_turn(tmp_path, read_then_answer, allow=False)
    )
    assert update.status == "complete"
    assert peer.calls == []
    assert server.last_reply["result"]["success"] is False  # type: ignore[attr-defined]


def test_l3_violation_halts_only_this_task_and_interrupts_codex(tmp_path) -> None:
    async def forbidden(server: FakeAppServer, turn_id: str, _text: str) -> None:
        server.item(turn_id, "commandExecution")
        await asyncio.Event().wait()

    update, after, peer, [server] = asyncio.run(run_one_turn(tmp_path, forbidden))
    assert update.status == "halted"
    assert after.status is TaskStatus.HALTED
    assert after.connection_code == "CODEX_L3_VIOLATION"
    assert server.interrupts == 1
    assert peer.calls == []


def test_fingerprint_mismatch_leaves_codex_without_tools(tmp_path) -> None:
    async def run():
        peer = Peer([{**READ_DEFINITION, "description": "changed"}])
        servers: list[FakeAppServer] = []
        bindings = await bootstrap_product(
            str(tmp_path / "mismatch.db"),
            preparation=preparation(servers, read_then_answer),
            product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
        )
        try:
            assert bindings.create_session is not None
            created = await bindings.create_session(SessionOptions())
            assert not created.tools_available
            assert servers[0].thread_params is not None
            assert servers[0].thread_params["dynamicTools"] == []
        finally:
            assert bindings.close is not None
            await bindings.close()

    asyncio.run(run())
