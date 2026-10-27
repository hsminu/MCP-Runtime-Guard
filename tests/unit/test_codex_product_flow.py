"""A88 end to end inside A: product bootstrap -> Codex session -> Guard -> MCP.

Real GuardService, Profile loading, access rules, SQLite Store and transport;
the Codex app-server and MCP server are in-memory fakes. Dispatch counts are the
MCP peer's tools/call receipts (I1, I8, I11, T21/T22 A part). C launcher, L2 and
the real codex.exe are not exercised.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from itertools import count

from guard_app.domain.contracts import Released
from guard_app.domain.enums import Label, TaskStatus, ToolKind
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
from .test_product_assembly import (
    INTERNAL_SOURCE,
    PARTNER,
    PUBLIC_SOURCE,
    READ_DEFINITION,
    READ_SETTINGS,
    SEND_DEFINITION,
    SEND_SETTINGS,
    Peer,
    Registry,
    document,
    eventually,
    inputs,
    spec,
)

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


def test_tools_list_changed_denies_calls_and_new_sessions_do_not_list_the_server(
    tmp_path,
) -> None:
    """LIST-01: Codex cannot drop tools from a running thread, so the session
    started before the notice keeps listing them but every call is denied. A
    session started after it does not list them."""

    async def run():
        peer = Peer([READ_DEFINITION])
        servers: list[FakeAppServer] = []
        bindings = await bootstrap_product(
            str(tmp_path / "changed.db"),
            preparation=preparation(servers, read_then_answer),
            product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
        )
        try:
            assert bindings.create_session is not None
            assert bindings.snapshot is not None
            first = await bindings.create_session(SessionOptions())
            assert first.tools_available
            snapshot = bindings.snapshot

            async def tools_gone() -> bool:
                return not (await snapshot(first.task_id)).tools_available

            peer.notify_tools_changed()
            await eventually(tools_gone)
            runner = bindings.runner_for_task(first.task_id)
            runner.add_user_message("summarize the public document")
            update = await asyncio.wait_for(runner.advance(), 5)
            second = await bindings.create_session(SessionOptions())
            return update, second, peer, servers
        finally:
            assert bindings.close is not None
            await bindings.close()

    update, second, peer, [before, after] = asyncio.run(run())
    assert before.thread_params is not None
    [namespace] = before.thread_params["dynamicTools"]
    assert [tool["name"] for tool in namespace["tools"]] == ["read"]
    assert update.status == "complete"
    assert before.last_reply["result"]["success"] is False  # type: ignore[attr-defined]
    assert peer.calls == []
    assert not second.tools_available
    assert after.thread_params is not None
    assert after.thread_params["dynamicTools"] == []


class TaskEvents:
    """The bridge's `task_events` callback: Task ids republished outside a command."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._changed = asyncio.Event()

    def __call__(self, task_id: str) -> None:
        self.seen.append(task_id)
        self._changed.set()

    async def reach(self, count: int) -> None:
        async with asyncio.timeout(5):
            while len(self.seen) < count:
                self._changed.clear()
                await self._changed.wait()


def test_ask_call_waits_with_its_approval_id_and_approval_completes_the_turn(tmp_path) -> None:
    """I3/I6: an INTERNAL read, then a SEND to an approved external destination
    is ASK (5.1). While the model turn waits, the Task projection carries the
    approval id; the human approval releases the result into the same turn."""

    async def read_then_send(server: FakeAppServer, turn_id: str, _text: str) -> None:
        server.first_reply = await server.tool_call(  # type: ignore[attr-defined]
            turn_id, "c1", "read", {"path": "internal/a.txt"}
        )
        server.last_reply = await server.tool_call(  # type: ignore[attr-defined]
            turn_id, "c2", "send", {"to": "partner", "body": "hello"}
        )
        await server.agent(turn_id, "sent it")
        server.complete(turn_id)

    async def run():
        peer = Peer([READ_DEFINITION, SEND_DEFINITION])
        registry = Registry(tool_fingerprint(READ_DEFINITION), spec(SEND_DEFINITION, ToolKind.SEND))
        doc = document(
            tools=(READ_SETTINGS, SEND_SETTINGS),
            sources=(INTERNAL_SOURCE,),
            destinations=(PARTNER,),
        )
        servers: list[FakeAppServer] = []
        bindings = await bootstrap_product(
            str(tmp_path / "ask.db"),
            preparation=preparation(servers, read_then_send),
            product=inputs(peer, registry, doc),
        )
        events = TaskEvents()
        try:
            assert bindings.task_events is not None
            bindings.task_events(events)
            assert bindings.create_session is not None
            assert bindings.snapshot is not None
            assert bindings.human_control is not None
            snapshot = bindings.snapshot
            created = await bindings.create_session(SessionOptions())
            task_id = created.task_id
            runner = bindings.runner_for_task(task_id)
            runner.add_user_message("send the internal note to the partner")
            advance = asyncio.ensure_future(runner.advance())
            await events.reach(1)  # the relay started waiting for a human
            during = await snapshot(task_id)
            approval_id = during.approval_id
            assert approval_id is not None
            assert not advance.done()  # the model turn itself is still waiting
            assert len(peer.calls) == 1  # only the READ; the SEND waits for a human
            started = list(events.seen)

            # NOTIFY-01: the model turn resumes from Guard's final result alone;
            # nothing forwards what the human approve call returned.
            outcome = await bindings.human_control.approve(approval_id)
            assert type(outcome) is Released
            update = await asyncio.wait_for(advance, 5)
            after = await snapshot(task_id)
            return update, during, after, peer, servers[0], started, events.seen, task_id
        finally:
            assert bindings.close is not None
            await bindings.close()

    update, during, after, peer, server, started, events, task_id = asyncio.run(run())
    assert during.status is TaskStatus.WAITING_APPROVAL
    assert during.label is Label.INTERNAL
    assert started == [task_id]  # the bridge hears the wait start
    assert update.status == "complete"
    assert update.assistant_text == "sent it"
    assert len(peer.calls) == 2
    assert peer.calls[1]["name"] == "send"
    assert server.first_reply["result"]["success"] is True  # type: ignore[attr-defined]
    reply = server.last_reply["result"]  # type: ignore[attr-defined]
    assert reply["success"] is True
    assert reply["contentItems"][0]["text"] == "sent"
    assert after.status is TaskStatus.RUNNING
    assert after.approval_id is None
    assert events == [task_id, task_id]  # wait start and end are republished


async def stop_session_during_guard_submit(tmp_path, *, resume_first: bool):
    """Park Task 1's Guard submit before reservation (in `next_call_seq`), stop
    its Codex session with an L3 item, then run a READ in a second Task either
    while that submit is still parked or after it has resumed and settled."""

    entered = asyncio.Event()
    release = asyncio.Event()
    sequence = count(1)

    async def gated_call_seq(_task_id: str) -> int:
        value = next(sequence)
        if value == 1:
            entered.set()
            await release.wait()
        return value

    servers: list[FakeAppServer] = []

    async def script(server: FakeAppServer, turn_id: str, text: str) -> None:
        if server is not servers[0]:
            await read_then_answer(server, turn_id, text)
            return
        asyncio.ensure_future(server.tool_call(turn_id, "c1", "read", {"path": "public/a.txt"}))
        await entered.wait()
        server.item(turn_id, "commandExecution")  # L3: this session stops
        await asyncio.Event().wait()

    peer = Peer([READ_DEFINITION])
    product = replace(
        inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
        next_call_seq=gated_call_seq,
    )
    bindings = await bootstrap_product(
        str(tmp_path / "shield.db"), preparation=preparation(servers, script), product=product
    )
    try:
        assert bindings.create_session is not None
        assert bindings.snapshot is not None
        first = await bindings.create_session(SessionOptions())
        runner = bindings.runner_for_task(first.task_id)
        runner.add_user_message("read it")
        stopped = await asyncio.wait_for(runner.advance(), 5)
        first_after = await bindings.snapshot(first.task_id)
        if resume_first:
            release.set()
            await asyncio.sleep(0.05)  # let the late submit finish inside Guard
        second = await bindings.create_session(SessionOptions())
        other = bindings.runner_for_task(second.task_id)
        other.add_user_message("summarize the public document")
        update = await asyncio.wait_for(other.advance(), 5)
        parked = not release.is_set()
        release.set()
        # STOP-01 commits HALTED once the parked submit leaves the Task lock.
        first_final = first_after
        for _ in range(200):
            first_final = await bindings.snapshot(first.task_id)
            if first_final.status is TaskStatus.HALTED:
                break
            await asyncio.sleep(0.005)
        return stopped, first_after, first_final, update, parked, peer, servers
    finally:
        release.set()
        assert bindings.close is not None
        await bindings.close()


def test_session_stop_during_guard_submit_does_not_halt_guard_for_other_tasks(tmp_path) -> None:
    """B7.2/I8: stopping one Codex session while its call is inside Guard, before
    reservation, no longer cancels Guard (shield), so Guard is not globally halted
    and another Task still runs a tool."""
    stopped, first_after, first_final, update, parked, peer, servers = asyncio.run(
        stop_session_during_guard_submit(tmp_path, resume_first=False)
    )
    assert stopped.status == "halted"
    # STOP-01: while Task 1's submit holds its Task lock, Guard has blocked its
    # dispatch but not yet written HALTED; the session is already unusable.
    assert first_after.status is TaskStatus.RUNNING
    assert first_after.connection_code == "CODEX_L3_VIOLATION"
    assert not first_after.model_ready
    assert not first_after.can_accept_message
    assert first_final.status is TaskStatus.HALTED
    assert parked  # Task 1's submit was still inside Guard during Task 2's call
    assert update.status == "complete"
    result = servers[1].last_reply["result"]  # type: ignore[attr-defined]
    assert result["success"] is True
    assert result["contentItems"][0]["text"] == "public text"
    assert len(peer.calls) == 1  # only the second Task's READ was dispatched


def test_resumed_submit_of_a_stopped_session_does_not_halt_guard(tmp_path) -> None:
    """B7.2/I8: the shielded submit must also end without a global halt."""
    _stopped, _first, _final, update, parked, peer, servers = asyncio.run(
        stop_session_during_guard_submit(tmp_path, resume_first=True)
    )
    assert not parked
    assert update.status == "complete"
    result = servers[1].last_reply["result"]  # type: ignore[attr-defined]
    assert result["success"] is True
    assert len(peer.calls) == 1


def test_l3_item_after_a_finished_turn_halts_the_task_in_store(tmp_path) -> None:
    """I11/T21: an L3 item with no send() waiting still stops the Task."""

    servers: list[FakeAppServer] = []

    async def run():
        peer = Peer([READ_DEFINITION])
        bindings = await bootstrap_product(
            str(tmp_path / "late.db"),
            preparation=preparation(servers, read_then_answer),
            product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
        )
        events = TaskEvents()
        try:
            assert bindings.task_events is not None
            bindings.task_events(events)
            assert bindings.create_session is not None
            assert bindings.snapshot is not None
            snapshot = bindings.snapshot
            created = await bindings.create_session(SessionOptions())
            task_id = created.task_id
            runner = bindings.runner_for_task(task_id)
            runner.add_user_message("summarize the public document")
            update = await asyncio.wait_for(runner.advance(), 5)
            before = await snapshot(task_id)
            servers[0].item("turn-1", "commandExecution")  # after the turn finished
            # Republished when the session closed and when Guard committed the stop.
            await events.reach(2)
            after = await snapshot(task_id)
            return update, before, after, events.seen, task_id
        finally:
            assert bindings.close is not None
            await bindings.close()

    update, before, after, events, task_id = asyncio.run(run())
    assert update.status == "complete"
    assert before.status is TaskStatus.RUNNING
    assert after.status is TaskStatus.HALTED
    assert after.connection_code == "CODEX_L3_VIOLATION"
    assert after.can_accept_message is False
    assert events == [task_id, task_id]


def test_tools_excluded_by_codex_name_rules_leave_codex_without_tools(tmp_path) -> None:
    """I11: Guard offers `docs__read.v2`, but Codex names cannot contain `.`. The
    session reports no tools, and a call for it is answered without Guard."""

    dotted = {**READ_DEFINITION, "name": "read.v2"}

    async def call_dotted(server: FakeAppServer, turn_id: str, _text: str) -> None:
        server.last_reply = await server.tool_call(  # type: ignore[attr-defined]
            turn_id, "c1", "read.v2", {"path": "public/a.txt"}
        )
        await server.agent(turn_id, "done")
        server.complete(turn_id)

    async def run():
        peer = Peer([dotted])
        registry = Registry(tool_fingerprint(READ_DEFINITION))
        registry.record = registry.record.model_copy(update={"tools": (spec(dotted),)})
        doc = document(
            tools=({**READ_SETTINGS, "name": "read.v2"},),
            sources=({**PUBLIC_SOURCE, "tool": "read.v2"},),
        )
        servers: list[FakeAppServer] = []
        bindings = await bootstrap_product(
            str(tmp_path / "names.db"),
            preparation=preparation(servers, call_dotted),
            product=inputs(peer, registry, doc),
        )
        try:
            assert bindings.create_session is not None
            created = await bindings.create_session(SessionOptions())
            runner = bindings.runner_for_task(created.task_id)
            runner.add_user_message("read it")
            update = await asyncio.wait_for(runner.advance(), 5)
            return created, update, peer, servers[0]
        finally:
            assert bindings.close is not None
            await bindings.close()

    created, update, peer, server = asyncio.run(run())
    assert created.tools_available is False
    assert server.thread_params is not None
    assert server.thread_params["dynamicTools"] == []
    assert update.status == "complete"
    assert server.last_reply["result"]["success"] is False  # type: ignore[attr-defined]
    assert peer.calls == []
