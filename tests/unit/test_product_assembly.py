"""A86: Profile loading, access rules and live fingerprints composed into Guard.

I1 (Guard-only dispatch), I2/I6 (deny by default), I7 (trusted ids), I10
(fingerprint change blocks the tool). Counts are the synthetic peer's tools/call
receipts; this is A's unit composition, not C's independent T/S acceptance.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.mcp_transport import validated_stdio_stream_factory
from guard_app.core.access_rules import AccessRules
from guard_app.domain.contracts import Denied, RegisteredServer, Released, TrustedContext
from guard_app.domain.enums import Label, RegStatus, TaskStatus, ToolKind, TrustTier
from guard_app.domain.models import Boundary, ProposedCall, ResolvedRequest, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.security.digest import tool_fingerprint
from guard_app.services.product_assembly import (
    ProductAssemblyError,
    ProductInputs,
    assemble_guard,
)
from guard_app.storage.sqlite_store import SQLiteStore

READ_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}
READ_DEFINITION = {"name": "read", "description": "read a document", "inputSchema": READ_SCHEMA}


class Peer:
    """In-memory MCP server bytes: initialize, tools/list and tools/call only."""

    def __init__(self, definitions: list[dict[str, object]]) -> None:
        self.definitions = definitions
        self.output = bytearray()
        self.available = asyncio.Event()
        self.calls: list[dict[str, object]] = []

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
            result = {"tools": self.definitions}
        elif method == "tools/call":
            self.calls.append(request["params"])
            # A resource READ echoes the resolved resource_id for classification.
            result = {
                "content": [{"type": "text", "text": "public text"}],
                "structuredContent": {"resource_id": request["params"]["arguments"]["path"]},
            }
        else:
            return
        self.output.extend(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode() + b"\n"
        )
        self.available.set()

    async def drain(self) -> None:
        pass


class Registry:
    def __init__(self, fingerprint: str) -> None:
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
            tools=(tool,),
        )

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self.record if server_id == "docs" else None

    def status(self, server_id: str) -> RegStatus | None:
        return RegStatus.ACTIVE if server_id == "docs" else None

    def fingerprint(self, tool: ToolSpec) -> str | None:
        return tool.fingerprint


def document(*, allow: bool = True) -> dict[str, object]:
    return {
        "profile": {
            "id": "unit",
            "servers": ["docs"],
            "sources": [
                {"server": "docs", "tool": "read", "prefix": ["public"], "label": "PUBLIC"}
            ],
        },
        "servers": [
            {
                "id": "docs",
                "transport": "stdio",
                "command": "C:/validated/by-c.exe",
                "tier": "internal",
                "max_label": "CONFIDENTIAL",
                "tools": [{"name": "read", "kind": "read", "resource_arg": "path"}],
            }
        ],
        "access_rules": {"allow": [["docs", "read"]] if allow else [], "deny": []},
    }


def inputs(peer: Peer, registry: Registry, doc: dict[str, object]) -> ProductInputs:
    sequence = iter(range(1, 1000))

    async def next_call_seq(_task_id: str) -> int:
        return next(sequence)

    async def failure_audit(_event: object) -> None:
        pass

    return ProductInputs(
        profile_document=doc,
        registry=registry,
        server_streams={"docs": validated_stdio_stream_factory(peer.read, peer.write, peer.drain)},
        next_call_seq=next_call_seq,
        failure_audit=failure_audit,  # type: ignore[arg-type]
        operator_id="local-operator",
        resource_resolver=lambda _tool, value: value,
    )


async def submit_read(tmp_path, peer: Peer, registry: Registry, doc: dict[str, object]):
    store = SQLiteStore(tmp_path / "assembly.db", audit_id_factory=lambda: f"audit-{uuid4().hex}")
    assembled = await assemble_guard(store, inputs(peer, registry, doc))
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
        outcome = await assembled.guard.submit(
            "task-1",
            ProposedCall(
                tool_ref="docs__read", arguments={"path": "public/a.txt"}, model_call_id="c1"
            ),
            expected_epoch=1,
        )
        return outcome, assembled.tool_views, assembled.registry.mismatched
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


@pytest.mark.parametrize(
    "live",
    [[{**READ_DEFINITION, "description": "changed by the server"}], []],
    ids=["changed", "absent"],
)
def test_live_definition_mismatch_hides_the_tool_and_blocks_dispatch(tmp_path, live) -> None:
    peer = Peer(live)
    outcome, views, mismatched = asyncio.run(
        submit_read(tmp_path, peer, Registry(tool_fingerprint(READ_DEFINITION)), document())
    )
    assert mismatched == frozenset({("docs", "read")})
    assert views == ()
    assert type(outcome) is Denied
    assert Reason.TOOL_FINGERPRINT_CHANGED in outcome.reasons
    assert peer.calls == []


def test_invalid_profile_and_missing_server_stream_fail_closed(tmp_path) -> None:
    peer = Peer([READ_DEFINITION])
    registry = Registry(tool_fingerprint(READ_DEFINITION))

    async def run() -> None:
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

    asyncio.run(run())
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
        name: asyncio.run(checker.check_access(request("task-1", name), context))
        for name in ("read", "send", "other")
    }
    assert results["read"].allowed
    assert not results["read"].error
    assert not results["send"].allowed
    assert not results["send"].error
    assert not results["other"].allowed
    assert results["other"].reason == "ACCESS_RULE_MISSING"
    mismatch = asyncio.run(checker.check_access(request("task-2", "read"), context))
    assert not mismatch.allowed
    assert mismatch.error
    with pytest.raises(ValueError, match="valid access rules"):
        AccessRulesChecker(AccessRules(allow="not-a-list"))


def test_product_bootstrap_with_inputs_connects_human_control_and_closes_transport(tmp_path):
    from guard_app.services.session_runtime import bootstrap_product

    peer = Peer([READ_DEFINITION])

    async def run() -> None:
        bindings = await bootstrap_product(
            str(tmp_path / "product.db"),
            product=inputs(peer, Registry(tool_fingerprint(READ_DEFINITION)), document()),
        )
        try:
            assert bindings.human_control is not None
            assert bindings.discard_queued is not None
            assert bindings.session_blocker is not None
            assert bindings.session_blocker() == "CODEX_CONFIG_UNAVAILABLE"
        finally:
            assert bindings.close is not None
            await bindings.close()

    asyncio.run(run())


def test_product_bootstrap_with_invalid_profile_closes_the_store(tmp_path):
    from guard_app.services.session_runtime import bootstrap_product

    peer = Peer([READ_DEFINITION])
    path = tmp_path / "invalid.db"

    async def run() -> None:
        with pytest.raises(ProductAssemblyError, match="PROFILE_INVALID"):
            await bootstrap_product(
                str(path),
                product=inputs(peer, Registry("a" * 64), {"profile": {"id": "x"}}),
            )

    asyncio.run(run())
    with SQLiteStore(path, audit_id_factory=lambda: "probe"):
        pass  # the failed start released the database
