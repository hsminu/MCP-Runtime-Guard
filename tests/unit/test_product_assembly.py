"""A86: Guard composition and synthetic tools/call counts (I1/I2/I6/I7/I10).

A unit checks, not C's independent T/S acceptance.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.mcp_transport import validated_stdio_stream_factory
from guard_app.core.access_rules import AccessRules
from guard_app.domain.contracts import (
    Denied,
    Pending,
    RegisteredServer,
    Released,
    ToolView,
    TrustedContext,
    Withheld,
)
from guard_app.domain.enums import ApprovalStatus, Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.models import Boundary, ProposedCall, ResolvedRequest, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.security.digest import tool_fingerprint
from guard_app.services.guard import FailureEvent
from guard_app.services.product_assembly import (
    AssembledGuard,
    LiveCheckedRegistry,
    LiveFingerprints,
    ProductAssemblyError,
    ProductInputs,
    assemble_guard,
)
from guard_app.storage.sqlite_store import SQLiteStore

from .product_folders import product_folders

READ_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}
READ_DEFINITION = {"name": "read", "description": "read a document", "inputSchema": READ_SCHEMA}
SEND_SCHEMA = {
    "type": "object",
    "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
    "required": ["to", "body"],
    "additionalProperties": False,
}
SEND_DEFINITION = {"name": "send", "description": "send a message", "inputSchema": SEND_SCHEMA}
LIST_DEFINITION = {"name": "list", "description": "list documents", "inputSchema": READ_SCHEMA}


class Peer:
    """In-memory MCP server bytes: initialize, tools/list and tools/call only."""

    def __init__(
        self, definitions: list[dict[str, object]], *, notify_on_list: bool = False
    ) -> None:
        self.definitions = definitions
        self.output = bytearray()
        self.available = asyncio.Event()
        self.calls: list[dict[str, object]] = []
        self.notify_on_list = notify_on_list

    def notify_tools_changed(self) -> None:
        # Params are optional; this carries one the Guard must never forward.
        notice = {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {"_meta": {"note": "secret-notice-text"}},
        }
        self.output.extend(json.dumps(notice).encode() + b"\n")
        self.available.set()

    async def read(self, size: int) -> bytes:
        while not self.output:
            self.available.clear()
            await self.available.wait()
        chunk = bytes(self.output[:size])
        del self.output[:size]
        return chunk

    async def write(self, frame: bytes) -> None:
        request = json.loads(frame)
        method = request.get("method")
        if method == "initialize":
            result: object = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "docs", "version": "1"},
            }
        elif method == "tools/list":
            if self.notify_on_list:
                self.notify_tools_changed()  # arrives before the listing it announces
            result = {"tools": self.definitions}
        elif method == "tools/call":
            self.calls.append(request["params"])
            arguments = request["params"]["arguments"]
            if "path" in arguments:
                # A resource READ echoes the resolved resource_id for classification.
                result = {
                    "content": [{"type": "text", "text": "public text"}],
                    "structuredContent": {"resource_id": arguments["path"]},
                }
            else:
                result = {"content": [{"type": "text", "text": "sent"}]}
        else:
            return
        self.output.extend(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode() + b"\n"
        )
        self.available.set()

    async def drain(self) -> None:
        pass


def spec(definition: dict[str, object], kind: ToolKind = ToolKind.READ) -> ToolSpec:
    """A registered tool whose fingerprint matches `definition` (C's Registry role)."""
    schema = definition["inputSchema"]
    assert isinstance(schema, dict)
    return ToolSpec(
        server_id="docs",
        tool_name=str(definition["name"]),
        kind=kind,
        input_schema=schema,
        resource_arg="path" if kind is ToolKind.READ else None,
        destination_arg="to" if kind is ToolKind.SEND else None,
        fingerprint=tool_fingerprint(definition),
    )


class Registry:
    def __init__(self, fingerprint: str, *extra: ToolSpec) -> None:
        tool = ToolSpec(
            server_id="docs",
            tool_name="read",
            kind=ToolKind.READ,
            input_schema=READ_SCHEMA,
            resource_arg="path",
            destination_arg=None,
            fingerprint=fingerprint,
        )
        self.record = RegisteredServer(
            server_id="docs",
            status=RegStatus.ACTIVE,
            boundary=Boundary(
                boundary_id="docs-boundary", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
            ),
            tools=(tool, *extra),
        )

    def rewrite(self, tool_name: str, fingerprint: str) -> None:
        """C re-registers one tool with a new fingerprint while the run is live."""
        tools = tuple(
            tool.model_copy(update={"fingerprint": fingerprint})
            if tool.tool_name == tool_name
            else tool
            for tool in self.record.tools
        )
        self.record = self.record.model_copy(update={"tools": tools})

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self.record if server_id == "docs" else None

    def status(self, server_id: str) -> RegStatus | None:
        return RegStatus.ACTIVE if server_id == "docs" else None

    def fingerprint(self, tool: ToolSpec) -> str | None:
        return tool.fingerprint


READ_SETTINGS = {"name": "read", "kind": "read", "resource_arg": "path"}
PUBLIC_SOURCE = {"server": "docs", "tool": "read", "prefix": ["public"], "label": "PUBLIC"}
INTERNAL_SOURCE = {"server": "docs", "tool": "read", "prefix": ["internal"], "label": "INTERNAL"}
# INTERNAL data to an approved external destination is ASK (5.1 matrix).
PARTNER = {"id": "partner", "tier": "approved_external", "max_label": "INTERNAL"}


def document(
    *,
    allow: bool = True,
    tools: tuple[dict[str, object], ...] = (READ_SETTINGS,),
    sources: tuple[dict[str, object], ...] = (PUBLIC_SOURCE,),
    destinations: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    profile: dict[str, object] = {"id": "unit", "servers": ["docs"], "sources": list(sources)}
    doc: dict[str, object] = {
        "profile": profile,
        "servers": [
            {
                "id": "docs",
                "transport": "stdio",
                "command": "C:/validated/by-c.exe",
                "tier": "internal",
                "max_label": "CONFIDENTIAL",
                "tools": list(tools),
            }
        ],
        "access_rules": {
            "allow": [["docs", tool["name"]] for tool in tools] if allow else [],
            "deny": [],
        },
    }
    if destinations:
        profile["destinations"] = [item["id"] for item in destinations]
        doc["destinations"] = list(destinations)
    return doc


def inputs(
    peer: Peer, registry: Registry, doc: dict[str, object], audits: list[FailureEvent] | None = None
) -> ProductInputs:
    sequence = iter(range(1, 1000))

    async def next_call_seq(_task_id: str) -> int:
        return next(sequence)

    async def failure_audit(event: FailureEvent) -> None:
        if audits is not None:
            audits.append(event)

    return ProductInputs(
        profile_document=doc,
        registry=registry,
        server_streams={"docs": validated_stdio_stream_factory(peer.read, peer.write, peer.drain)},
        next_call_seq=next_call_seq,
        failure_audit=failure_audit,
        operator_id="local-operator",
        resource_resolver=lambda _tool, value: value,
    )


@asynccontextmanager
async def closing_backend(backend):
    """Close an async backend on the owning loop, including assertion failures."""
    try:
        yield backend
    finally:
        assert backend.close is not None
        await backend.close()


async def submit_read(tmp_path, peer: Peer, registry: Registry, doc: dict[str, object]):
    outcome, views, view = await submit_call(tmp_path, peer, registry, doc)
    return outcome, views, view.mismatched


async def submit_call(
    tmp_path,
    peer: Peer,
    registry: Registry,
    doc: dict[str, object],
    *,
    tool_ref: str = "docs__read",
    before_submit: Callable[[], None] | None = None,
) -> tuple[object, tuple[ToolView, ...], LiveCheckedRegistry]:
    async with running(tmp_path, peer, registry, doc) as (assembled, _store):
        if before_submit is not None:
            before_submit()
        outcome = await assembled.guard.submit(
            "task-1",
            ProposedCall(tool_ref=tool_ref, arguments={"path": "public/a.txt"}, model_call_id="c1"),
            expected_epoch=1,
        )
        return outcome, assembled.tool_views, assembled.registry


@asynccontextmanager
async def running(
    tmp_path,
    peer: Peer,
    registry: Registry,
    doc: dict[str, object],
    audits: list[FailureEvent] | None = None,
) -> AsyncIterator[tuple[AssembledGuard, SQLiteStore]]:
    """Assembled Guard with RUNNING Task `task-1`; transport and Store closed after."""
    store = SQLiteStore(tmp_path / "assembly.db", audit_id_factory=lambda: f"audit-{uuid4().hex}")
    assembled = await assemble_guard(store, inputs(peer, registry, doc, audits))
    try:
        settings = assembled.settings
        state = TaskState(
            task_id="task-1",
            epoch=1,
            profile_id=settings.profile_id,
            policy_version=settings.policy_version,
            status=TaskStatus.RUNNING,
        )
        await store.create_task(state, created_at_utc=datetime.now(UTC))
        yield assembled, store
    finally:
        await assembled.aclose()
        store.close()


def test_allowed_registered_tool_is_released_through_guard_once(tmp_path) -> None:
    peer = Peer([READ_DEFINITION])
    outcome, views, mismatched = asyncio.run(
        submit_read(tmp_path, peer, Registry(tool_fingerprint(READ_DEFINITION)), document())
    )
    assert type(outcome) is Released
    assert outcome.label is Label.PUBLIC
    assert [view.tool_ref for view in views] == ["docs__read"]
    assert mismatched == frozenset()
    assert len(peer.calls) == 1


def test_missing_access_rule_is_denied_before_dispatch(tmp_path) -> None:
    peer = Peer([READ_DEFINITION])
    outcome, _views, _ = asyncio.run(
        submit_read(
            tmp_path, peer, Registry(tool_fingerprint(READ_DEFINITION)), document(allow=False)
        )
    )
    assert type(outcome) is Denied
    assert peer.calls == []


def test_live_definition_mismatch_hides_the_tool_and_blocks_dispatch(tmp_path, subtests):
    """I10: changed, absent, added, duplicate or sibling tool blocks the server."""
    cases = [
        (
            [{**READ_DEFINITION, "description": "changed by the server"}],
            False,
            "read",
            frozenset({("docs", "read")}),
        ),
        ([], False, "read", frozenset({("docs", "read")})),
        (
            [{**READ_DEFINITION, "description": "changed by the server"}, LIST_DEFINITION],
            True,
            "list",
            frozenset({("docs", "read")}),
        ),
        ([READ_DEFINITION, LIST_DEFINITION], False, "read", frozenset()),
        ([READ_DEFINITION, READ_DEFINITION], False, "read", None),
    ]
    for index, (live, sibling, name, mismatched) in enumerate(cases):
        with subtests.test(case=index):
            path = tmp_path / str(index)
            path.mkdir()
            peer = Peer(live)
            registry = Registry(
                tool_fingerprint(READ_DEFINITION), *((spec(LIST_DEFINITION),) if sibling else ())
            )
            doc = document(tools=(READ_SETTINGS, LIST_SETTINGS) if sibling else (READ_SETTINGS,))
            outcome, views, view = asyncio.run(
                submit_call(path, peer, registry, doc, tool_ref=f"docs__{name}")
            )
            assert views == ()
            assert type(outcome) is Denied
            assert outcome.reasons == (Reason.REG_NOT_ACTIVE,)
            assert peer.calls == []
            assert view.blocked_servers == frozenset({"docs"})
            assert view.status("docs") is RegStatus.PENDING
            if mismatched is not None:
                assert view.mismatched == mismatched


LIST_SETTINGS = {"name": "list", "kind": "read", "resource_arg": "path"}


@pytest.mark.asyncio
async def test_registration_rewritten_mid_run_no_longer_matches_the_live_definition(
    tmp_path,
) -> None:
    """I10: Guard compares with what this session received, not the newest record."""
    peer = Peer([READ_DEFINITION])
    registry = Registry(tool_fingerprint(READ_DEFINITION))
    live: list[str | None] = []

    def rewrite() -> None:
        registry.rewrite("read", "f" * 64)

    outcome, views, view = await submit_call(
        tmp_path, peer, registry, document(), before_submit=rewrite
    )
    live.append(view.fingerprint(registry.record.tools[0]))
    assert [v.tool_ref for v in views] == ["docs__read"]  # healthy at assembly
    assert type(outcome) is Denied
    assert outcome.reasons == (Reason.TOOL_FINGERPRINT_CHANGED,)
    assert peer.calls == []
    assert live == [tool_fingerprint(READ_DEFINITION)]
    assert view.blocked_servers == frozenset()


SEND_SETTINGS = {"name": "send", "kind": "send", "destination_arg": "to"}


def read_call(call_id: str, path: str = "public/a.txt") -> ProposedCall:
    return ProposedCall(tool_ref="docs__read", arguments={"path": path}, model_call_id=call_id)


async def eventually(condition: Callable[[], Awaitable[bool]]) -> None:
    """The MCP SDK handles a notice on its own receive task, so poll for it."""
    for _ in range(500):
        if await condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def list_change_seen(view: LiveCheckedRegistry) -> None:
    async def changed() -> bool:
        return bool(view.listing_changed)

    await eventually(changed)


def test_tools_list_changed_after_the_comparison_blocks_the_server_for_this_run(
    tmp_path, caplog
) -> None:
    """LIST-01: the server is PENDING for this run, so new calls are denied with
    REG_NOT_ACTIVE and no tool of it is offered. C's Registry is not written."""
    peer = Peer([READ_DEFINITION, LIST_DEFINITION])
    registry = Registry(tool_fingerprint(READ_DEFINITION), spec(LIST_DEFINITION))
    doc = document(tools=(READ_SETTINGS, LIST_SETTINGS))
    audits: list[FailureEvent] = []

    async def run():
        async with running(tmp_path, peer, registry, doc, audits) as (assembled, store):
            guard = assembled.guard
            before = await guard.submit("task-1", read_call("c1"), expected_epoch=1)
            peer.notify_tools_changed()
            await list_change_seen(assembled.registry)
            after = await guard.submit("task-1", read_call("c2"), expected_epoch=1)
            offered = assembled.offered_tool_views()
            state = await store.load_task("task-1")
            return before, after, offered, assembled.tool_views, assembled.registry, state

    with caplog.at_level(logging.DEBUG):
        before, after, offered, views, view, state = asyncio.run(run())
    assert type(before) is Released
    assert type(after) is Denied
    assert after.reasons == (Reason.REG_NOT_ACTIVE,)
    assert len(peer.calls) == 1
    # The existing reason is audited; the Task itself keeps running.
    assert [(event.phase, event.reason) for event in audits] == [
        ("before_dispatch", Reason.REG_NOT_ACTIVE)
    ]
    assert state.status is TaskStatus.RUNNING
    assert view.listing_changed == frozenset({"docs"})
    assert view.blocked_servers == frozenset({"docs"})
    assert view.status("docs") is RegStatus.PENDING
    # The definitions themselves were not compared again; they keep the live value.
    assert view.mismatched == frozenset()
    assert view.fingerprint(registry.record.tools[0]) == tool_fingerprint(READ_DEFINITION)
    assert [v.tool_ref for v in views] == ["docs__read", "docs__list"]
    assert offered == ()
    assert registry.record.status is RegStatus.ACTIVE
    assert "secret-notice-text" not in caplog.text


def test_tools_list_changed_before_the_first_listing_response_does_not_block(tmp_path) -> None:
    """LIST-01/A140: an initial notice precedes every received listing page."""
    peer = Peer([READ_DEFINITION], notify_on_list=True)
    outcome, views, view = asyncio.run(
        submit_call(tmp_path, peer, Registry(tool_fingerprint(READ_DEFINITION)), document())
    )
    assert type(outcome) is Released
    assert [v.tool_ref for v in views] == ["docs__read"]
    assert view.early_list_changes == 1
    assert view.listing_changed == frozenset()
    assert view.status("docs") is RegStatus.ACTIVE
    assert len(peer.calls) == 1


@pytest.mark.asyncio
async def test_tools_list_changed_invalidates_a_pending_approval(tmp_path) -> None:
    """LIST-01: an approval waiting when the notice arrives is invalidated on its
    recheck with REG_NOT_ACTIVE and its Task halts; the SEND is never dispatched."""
    peer = Peer([READ_DEFINITION, SEND_DEFINITION])
    registry = Registry(tool_fingerprint(READ_DEFINITION), spec(SEND_DEFINITION, ToolKind.SEND))
    doc = document(
        tools=(READ_SETTINGS, SEND_SETTINGS), sources=(INTERNAL_SOURCE,), destinations=(PARTNER,)
    )
    send = ProposedCall(
        tool_ref="docs__send", arguments={"to": "partner", "body": "hello"}, model_call_id="c2"
    )

    async with running(tmp_path, peer, registry, doc) as (assembled, store):
        guard = assembled.guard
        read = await guard.submit("task-1", read_call("c1", "internal/a.txt"), expected_epoch=1)
        pending = await guard.submit("task-1", send, expected_epoch=1)
        assert type(pending) is Pending
        peer.notify_tools_changed()
        await list_change_seen(assembled.registry)
        approved = await guard.human_control("local-operator").approve(pending.approval_id)
        record = await store.get_approval(pending.approval_id)
        state = await store.load_task("task-1")
    assert type(read) is Released
    assert type(approved) is Denied
    assert approved.reasons == (Reason.REG_NOT_ACTIVE,)
    assert record is not None
    assert record.status is ApprovalStatus.INVALIDATED
    assert [call["name"] for call in peer.calls] == ["read"]
    assert state.status is TaskStatus.HALTED


class DyingPeer(Peer):
    """A stdio server whose output ends (its process exited) after it has served."""

    def __init__(self, definitions: list[dict[str, object]], *, die_on_call: bool) -> None:
        super().__init__(definitions)
        self.dead = False
        self.die_on_call = die_on_call
        self.ended = asyncio.Event()

    def die(self) -> None:
        self.dead = True
        self.available.set()

    async def read(self, size: int) -> bytes:
        while not self.output:
            if self.dead:
                self.ended.set()
                return b""
            self.available.clear()
            await self.available.wait()
        return await super().read(size)

    async def write(self, frame: bytes) -> None:
        if self.dead:
            return  # nobody reads the pipe any more
        if self.die_on_call and self.calls:
            self.calls.append(json.loads(frame)["params"])
            self.die()  # exits with the call unanswered
            return
        await super().write(frame)


@pytest.mark.parametrize("die_on_call", [False, True])
@pytest.mark.asyncio
async def test_a_server_that_stops_serving_is_pending_for_the_rest_of_the_run(
    tmp_path, die_on_call
):
    """#61: the call that finds the dead session keeps its unknown outcome and
    halts its Task; every later call is denied with REG_NOT_ACTIVE before it is
    reserved, so no other Task is locked. C's Registry is not written."""
    peer = DyingPeer([READ_DEFINITION], die_on_call=die_on_call)
    registry = Registry(tool_fingerprint(READ_DEFINITION))
    audits: list[FailureEvent] = []

    async with running(tmp_path, peer, registry, document(), audits) as (assembled, store):
        guard = assembled.guard
        base = await store.load_task("task-1")
        for task_id in ("task-2", "task-3"):
            await store.create_task(
                base.model_copy(update={"task_id": task_id}), created_at_utc=datetime.now(UTC)
            )
        first = await guard.submit("task-1", read_call("c1"), expected_epoch=1)
        if not die_on_call:
            peer.die()
            await peer.ended.wait()
            # Let the SDK close the session first, so the next call finds it
            # closed rather than in flight; both end the same way.
            await asyncio.sleep(0.05)
        found = await guard.submit("task-2", read_call("c2"), expected_epoch=1)
        later = await guard.submit("task-3", read_call("c3"), expected_epoch=1)
        states = [(await store.load_task(t)).status for t in ("task-2", "task-3")]
        offered = assembled.offered_tool_views()
        view = assembled.registry
    assert type(first) is Released
    assert type(found) is Withheld
    assert found.reasons == (Reason.POST_DISPATCH_ERROR,)
    assert type(later) is Denied
    assert later.reasons == (Reason.REG_NOT_ACTIVE,)
    assert states == [TaskStatus.HALTED, TaskStatus.RUNNING]
    assert len(peer.calls) == (2 if die_on_call else 1)
    assert [(e.task_id, e.phase, e.reason) for e in audits if e.task_id == "task-3"] == [
        ("task-3", "before_dispatch", Reason.REG_NOT_ACTIVE)
    ]
    assert view.status("docs") is RegStatus.PENDING
    assert view.unavailable == frozenset({"docs"})
    assert offered == ()
    assert registry.record.status is RegStatus.ACTIVE


def test_close_settles_between_the_transport_and_guard(tmp_path, subtests):
    """NOTIFY-01, #58: transport -> optional stop settlement -> Guard."""

    async def check(with_settle, path):
        order = []
        with SQLiteStore(path, audit_id_factory=lambda: f"audit-{uuid4().hex}") as store:
            assembled = await assemble_guard(
                store,
                inputs(
                    Peer([READ_DEFINITION]), Registry(tool_fingerprint(READ_DEFINITION)), document()
                ),
            )
            close_transport, close_guard = assembled.transport.close, assembled.guard.aclose

            async def transport_closed():
                order.append("transport")
                await close_transport()

            async def guard_closed():
                order.append("guard")
                await close_guard()

            async def settle():
                order.append("settle")
                assert assembled.transport._closed

            assembled.transport.close = transport_closed
            assembled.guard.aclose = guard_closed
            if with_settle:
                await assembled.aclose(settle=settle)
                assert order == ["transport", "settle", "guard"]
            else:
                await assembled.aclose()
                assert order == ["transport", "guard"]

    for with_settle in (False, True):
        with subtests.test(settle=with_settle):
            asyncio.run(check(with_settle, tmp_path / f"{with_settle}.db"))


def test_a_server_failure_is_not_superseded_by_the_comparison() -> None:
    """#61: unlike an early list-change notice, a session that stopped serving
    before the live comparison finished keeps its server PENDING for this run."""
    view = LiveCheckedRegistry(Registry(tool_fingerprint(READ_DEFINITION)))
    view.server_failed("docs")
    assert view.status("docs") is RegStatus.PENDING
    live = {("docs", "read"): tool_fingerprint(READ_DEFINITION)}
    view.seal(LiveFingerprints(live, frozenset(), frozenset()))
    assert view.status("docs") is RegStatus.PENDING
    assert view.blocked_servers == view.unavailable == frozenset({"docs"})
    assert view.listing_changed == frozenset()


@pytest.mark.asyncio
async def test_unsupported_tool_schema_is_a_fixed_code_and_closes_the_transport(
    tmp_path, monkeypatch
) -> None:
    """A schema Catalog cannot serve fails closed as PROFILE_TOOL_INVALID."""
    from guard_app.services import product_assembly

    closed: list[bool] = []

    class RecordingTransport(product_assembly.McpTransport):
        async def close(self) -> None:
            closed.append(True)
            await super().close()

    monkeypatch.setattr(product_assembly, "McpTransport", RecordingTransport)
    array_read = {**READ_DEFINITION, "inputSchema": {"type": "array"}}
    peer = Peer([array_read])
    registry = Registry(tool_fingerprint(READ_DEFINITION))
    registry.record = registry.record.model_copy(update={"tools": (spec(array_read),)})

    store = SQLiteStore(tmp_path / "schema.db", audit_id_factory=lambda: "unit-audit")
    try:
        with pytest.raises(ProductAssemblyError, match="PROFILE_TOOL_INVALID") as raised:
            await assemble_guard(store, inputs(peer, registry, document()))
        assert raised.value.code == "PROFILE_TOOL_INVALID"
        assert raised.value.__cause__ is None
    finally:
        store.close()
    assert closed == [True]
    assert peer.calls == []


@pytest.mark.asyncio
async def test_a_blocked_server_whose_settings_are_ahead_of_its_registration_is_left_out(
    tmp_path,
) -> None:
    """A155: blocked OTHER registration versus READ settings offers no tools."""
    peer = Peer([READ_DEFINITION, READ_DEFINITION])  # a repeated name blocks the server
    registry = Registry(tool_fingerprint(READ_DEFINITION))
    other = registry.record.tools[0].model_copy(
        update={"kind": ToolKind.OTHER, "resource_arg": None}
    )
    registry.record = registry.record.model_copy(update={"tools": (other,)})

    store = SQLiteStore(tmp_path / "ahead.db", audit_id_factory=lambda: "unit-audit")
    try:
        assembled = await assemble_guard(store, inputs(peer, registry, document()))
        try:
            assert assembled.registry.blocked_servers == frozenset({"docs"})
            assert assembled.tool_views == ()
        finally:
            await assembled.aclose()
    finally:
        store.close()
    assert peer.calls == []


@pytest.mark.asyncio
async def test_invalid_profile_and_missing_server_stream_fail_closed(tmp_path) -> None:
    peer = Peer([READ_DEFINITION])
    registry = Registry(tool_fingerprint(READ_DEFINITION))

    store = SQLiteStore(tmp_path / "fail.db", audit_id_factory=lambda: "unit-audit")
    try:
        broken = inputs(peer, registry, {"profile": {"id": "x"}})
        with pytest.raises(ProductAssemblyError, match="PROFILE_INVALID"):
            await assemble_guard(store, broken)
        good = inputs(peer, registry, document())
        missing = ProductInputs(
            profile_document=good.profile_document,
            registry=registry,
            server_streams={},
            next_call_seq=good.next_call_seq,
            failure_audit=good.failure_audit,
            operator_id="local-operator",
        )
        with pytest.raises(ProductAssemblyError, match="PROFILE_SERVER_UNAVAILABLE"):
            await assemble_guard(store, missing)
    finally:
        store.close()
    assert peer.calls == []


def test_access_rules_checker_is_deny_by_default_and_binds_the_task() -> None:
    checker = AccessRulesChecker(AccessRules(allow=[["docs", "read"]], deny=[["docs", "send"]]))
    tool = Registry("a" * 64).record.tools[0]

    def request(task_id: str, name: str) -> ResolvedRequest:
        return ResolvedRequest(
            request_id="r1",
            task_id=task_id,
            epoch=1,
            call_seq=1,
            tool=tool.model_copy(update={"tool_name": name}),
            arguments={"path": "public/a.txt"},
            resource_id="public/a.txt",
            server_boundary=Registry("a" * 64).record.boundary,
            destination=None,
        )

    state = TaskState(task_id="task-1", epoch=1, profile_id="unit", policy_version="v")
    context = TrustedContext(state=state, operator_id="op")
    results = {
        name: checker.check_access(request("task-1", name), context)
        for name in ("read", "send", "other")
    }
    assert results["read"].allowed
    assert not results["read"].error
    assert not results["send"].allowed
    assert not results["send"].error
    assert not results["other"].allowed
    assert results["other"].reason == "ACCESS_RULE_MISSING"
    mismatch = checker.check_access(request("task-2", "read"), context)
    assert not mismatch.allowed
    assert mismatch.error
    with pytest.raises(ValueError, match="valid access rules"):
        AccessRulesChecker(AccessRules(allow="not-a-list"))


@pytest.mark.asyncio
async def test_product_bootstrap_with_inputs_connects_human_control_and_closes_transport(tmp_path):
    from guard_app.services.session_runtime import bootstrap_product

    peer = Peer([READ_DEFINITION])

    bindings = await bootstrap_product(
        product_folders(str(tmp_path / "product.db")),
        product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
    )
    async with closing_backend(bindings):
        assert bindings.human_control is not None
        assert bindings.discard_queued is not None
        assert bindings.session_blocker is not None
        assert bindings.session_blocker() == "CODEX_CONFIG_UNAVAILABLE"


@pytest.mark.asyncio
async def test_product_bootstrap_with_invalid_profile_closes_the_store(tmp_path):
    from guard_app.services.session_runtime import bootstrap_product

    peer = Peer([READ_DEFINITION])
    path = tmp_path / "invalid.db"

    with pytest.raises(ProductAssemblyError, match="PROFILE_INVALID"):
        await bootstrap_product(
            product_folders(str(path)),
            product=inputs(peer, Registry("a" * 64), {"profile": {"id": "x"}}),
        )
    with SQLiteStore(path, audit_id_factory=lambda: "probe"):
        pass  # the failed start released the database
    path.unlink()  # its connection closed too: Windows refuses to delete an open database


@pytest.mark.asyncio
async def test_a_failed_start_whose_store_close_is_unconfirmed_reports_cleanup(
    tmp_path, monkeypatch
):
    """#58: the Store's close only starts it. Unconfirmed, the bridge sees a cleanup failure."""
    from guard_app.services import product_startup, session_runtime
    from guard_app.services.bridge_types import BackendCleanupError

    class Late(SQLiteStore):
        async def wait_closed(self, timeout):  # noqa: ASYNC109 - the Store port's signature
            await super().wait_closed(timeout)
            return False

    monkeypatch.setattr(product_startup, "SQLiteStore", Late)
    peer = Peer([READ_DEFINITION])

    with pytest.raises(BackendCleanupError):
        await session_runtime.bootstrap_product(
            product_folders(str(tmp_path / "late.db")),
            product=inputs(peer, Registry("a" * 64), {"profile": {"id": "x"}}),
        )


@pytest.mark.asyncio
async def test_policy_view_is_built_from_the_profile_and_registry(tmp_path) -> None:
    from guard_app.services.policy_view import policy_view

    peer = Peer([READ_DEFINITION])

    store = SQLiteStore(tmp_path / "policy.db", audit_id_factory=lambda: f"a-{uuid4().hex}")
    assembled = await assemble_guard(
        store, inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document())
    )
    try:
        (view, settings) = policy_view(assembled), assembled.settings
    finally:
        await assembled.aclose()
        store.close()
    assert (view.profile_id, view.policy_version) == (settings.profile_id, settings.policy_version)
    assert view.allow == (("docs", "read"),)
    assert view.deny == ()
    assert [
        (s.server_id, [(t.name, t.kind, t.kind_fixed) for t in s.tools]) for s in view.servers
    ] == [("docs", [("read", "read", True)])]
    assert view.change_available is False
