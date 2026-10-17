"""A09/A10 tools/list collection bounds (contracts 2026-10-15), I6·I10 groundwork."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from examples.http_spike_server import http_spike
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, ListToolsResult, Tool

from guard_app.adapters import mcp_transport
from guard_app.adapters.mcp_transport import (
    McpTransport,
    ServerWorker,
    ToolListing,
    ToolListLimits,
    collect_tool_definitions,
    http_stream_factory,
)
from guard_app.domain.errors import DispatchError

from .test_mcp_transport import _request, _Session, _streams


def _tool(name: str, **extra: Any) -> Tool:
    return Tool(name=name, inputSchema={"type": "object"}, **extra)


class _Pages:
    """Synthetic tools/list responder: each entry is a page, delay, or error."""

    def __init__(self, pages: list[object], *, delay: float = 0.0) -> None:
        self.pages = list(pages)
        self.delay = delay
        self.cursors: list[str | None] = []

    async def list_tools(self, cursor: str | None = None) -> object:
        self.cursors.append(cursor)
        if self.delay:
            await asyncio.sleep(self.delay)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def _nested(levels: int) -> dict[str, Any]:
    """A Tool whose definition has exactly ``levels`` container levels."""

    schema: dict[str, Any] = {}
    for _ in range(levels - 2):  # Tool object and inputSchema are two levels.
        schema = {"a": schema}
    return {"name": "deep", "inputSchema": schema or {}}


@pytest.mark.asyncio
async def test_complete_listing_keeps_every_sent_field_across_pages() -> None:
    session = _Pages(
        [
            ListToolsResult(
                tools=[_tool("a", description="d", **{"x-extra": 0.5})], nextCursor="c1"
            ),
            ListToolsResult(tools=[_tool("b")], nextCursor="c2"),
            ListToolsResult(tools=[_tool("c", annotations={"readOnlyHint": True})]),
        ]
    )
    listing = await collect_tool_definitions(session, ToolListLimits())
    assert listing.complete
    assert listing.reason is None
    assert session.cursors == [None, "c1", "c2"]
    first, second, third = (tool.to_dict() for tool in listing.tools)
    assert first == {
        "name": "a",
        "description": "d",
        "inputSchema": {"type": "object"},
        "x-extra": 0.5,
    }
    assert second == {"name": "b", "inputSchema": {"type": "object"}}
    assert third["annotations"] == {"readOnlyHint": True}
    assert "tools=3" in repr(listing)
    assert "inputSchema" not in repr(listing)


@pytest.mark.asyncio
async def test_too_many_tools_is_incomplete_without_partial_definitions() -> None:
    limits = ToolListLimits(max_tools=3)
    session = _Pages(
        [
            ListToolsResult(tools=[_tool("a"), _tool("b")], nextCursor="c1"),
            ListToolsResult(tools=[_tool("c"), _tool("d")], nextCursor="c2"),
        ]
    )
    listing = await collect_tool_definitions(session, limits)
    assert listing == ToolListing(complete=False, tools=(), reason="TOOLS_LIST_TOO_MANY_TOOLS")
    assert session.cursors == [None, "c1"]


@pytest.mark.asyncio
async def test_page_cap_stops_requests_and_never_reports_done() -> None:
    limits = ToolListLimits(max_pages=3)
    session = _Pages([ListToolsResult(tools=[], nextCursor=f"c{i}") for i in range(5)])
    listing = await collect_tool_definitions(session, limits)
    assert listing.reason == "TOOLS_LIST_TOO_MANY_PAGES"
    assert not listing.complete
    assert listing.tools == ()
    assert session.cursors == [None, "c0", "c1"]


@pytest.mark.asyncio
async def test_repeated_cursor_and_duplicate_name_stop_at_once() -> None:
    # C operating proposal v1: a seen cursor or a repeated tool name is incomplete.
    looping = _Pages(
        [
            ListToolsResult(tools=[_tool("a")], nextCursor="x"),
            ListToolsResult(tools=[_tool("b")], nextCursor="x"),
            ListToolsResult(tools=[_tool("c")]),
        ]
    )
    listing = await collect_tool_definitions(looping, ToolListLimits())
    assert listing == ToolListing(complete=False, tools=(), reason="TOOLS_LIST_CURSOR_REPEATED")
    assert looping.cursors == [None, "x"]
    duplicate = _Pages(
        [
            ListToolsResult(tools=[_tool("a")], nextCursor="c1"),
            ListToolsResult(tools=[_tool("a", description="later")]),
        ]
    )
    listing = await collect_tool_definitions(duplicate, ToolListLimits())
    assert listing == ToolListing(complete=False, tools=(), reason="TOOLS_LIST_DUPLICATE_NAME")


@pytest.mark.asyncio
async def test_default_limits_match_the_decided_values() -> None:
    limits = ToolListLimits()
    assert (limits.max_tools, limits.max_pages, limits.max_json_depth) == (256, 32, 64)
    assert (limits.page_timeout_s, limits.collection_timeout_s) == (10.0, 30.0)
    exact = _Pages([ListToolsResult(tools=[_tool(f"t{i}") for i in range(256)])])
    assert (await collect_tool_definitions(exact, limits)).complete
    over = _Pages([ListToolsResult(tools=[_tool(f"t{i}") for i in range(257)])])
    assert (await collect_tool_definitions(over, limits)).reason == "TOOLS_LIST_TOO_MANY_TOOLS"


@pytest.mark.asyncio
async def test_json_depth_limit_counts_the_tool_object_as_one_level() -> None:
    ok = _Pages([ListToolsResult(tools=[Tool.model_validate(_nested(64))])])
    assert (await collect_tool_definitions(ok, ToolListLimits())).complete
    deep = _Pages([ListToolsResult(tools=[Tool.model_validate(_nested(65))])])
    listing = await collect_tool_definitions(deep, ToolListLimits())
    assert listing.reason == "TOOL_DEFINITION_TOO_DEEP"
    assert listing.tools == ()


@pytest.mark.asyncio
async def test_page_and_collection_timeouts_are_incomplete() -> None:
    slow_page = _Pages([ListToolsResult(tools=[])], delay=0.2)
    listing = await collect_tool_definitions(slow_page, ToolListLimits(page_timeout_s=0.02))
    assert listing.reason == "TOOLS_LIST_TIMEOUT"

    many = _Pages([ListToolsResult(tools=[], nextCursor=f"n{i}") for i in range(32)], delay=0.03)
    limits = ToolListLimits(page_timeout_s=1.0, collection_timeout_s=0.1)
    listing = await collect_tool_definitions(many, limits)
    assert listing.reason == "TOOLS_LIST_TIMEOUT"
    assert len(many.cursors) < 32


@pytest.mark.asyncio
async def test_server_error_and_malformed_pages_are_fixed_codes() -> None:
    marker = "TEST_SECRET_SERVER_TEXT"
    failed = _Pages([McpError(ErrorData(code=-32603, message=marker))])
    listing = await collect_tool_definitions(failed, ToolListLimits())
    assert listing.reason == "TOOLS_LIST_FAILED"
    assert marker not in repr(listing)
    malformed = _Pages([{"tools": []}])
    assert (await collect_tool_definitions(malformed, ToolListLimits())).reason == (
        "TOOLS_LIST_MALFORMED"
    )
    surrogate = _Pages([ListToolsResult(tools=[_tool("\ud800")])])
    assert (await collect_tool_definitions(surrogate, ToolListLimits())).reason == (
        "TOOLS_LIST_MALFORMED"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tools": 0},
        {"max_pages": -1},
        {"max_json_depth": 1.5},
        {"max_json_depth": 65},
        {"page_timeout_s": 0},
        {"collection_timeout_s": float("inf")},
        {"max_tools": True},
    ],
)
def test_invalid_limits_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="tools/list"):
        ToolListLimits(**kwargs)  # type: ignore[arg-type]


class _ListingSession(_Session):
    def __init__(self, pages: list[object]) -> None:
        super().__init__()
        self.pages = _Pages(pages)

    async def list_tools(self, cursor: str | None = None) -> object:
        self.events.append(("list", asyncio.current_task()))
        return await self.pages.list_tools(cursor)


@pytest.mark.asyncio
async def test_worker_lists_after_initialize_and_before_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ListingSession([ListToolsResult(tools=[_tool("tool")])])
    monkeypatch.setattr(
        mcp_transport, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"one": _streams([])}, tool_list_limits=ToolListLimits())
    await transport.start()
    try:
        listing = transport.tool_listing("one")
        assert listing is not None
        assert listing.complete
        assert [tool["name"] for tool in listing.tools] == ["tool"]
        assert (await transport.call(_request("one", seq=1))).payload is not None
        assert [name for name, _ in session.events] == ["enter", "initialize", "list", "call"]
        assert transport.tool_listing("unknown") is None
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_incomplete_listing_is_reported_and_worker_still_serves_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ListingSession([McpError(ErrorData(code=-32601, message="no tools"))])
    monkeypatch.setattr(
        mcp_transport, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"one": _streams([])}, tool_list_limits=ToolListLimits())
    await transport.start()
    try:
        listing = transport.tool_listing("one")
        assert listing == ToolListing(complete=False, tools=(), reason="TOOLS_LIST_FAILED")
        assert (await transport.call(_request("one", seq=1))).payload is not None
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_collection_is_off_by_default_and_never_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ListingSession([])
    monkeypatch.setattr(
        mcp_transport, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"one": _streams([])})
    await transport.start()
    try:
        assert transport.tool_listing("one") is None
        assert "list" not in [name for name, _ in session.events]
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_initialize_keeps_its_own_bound_when_listing_is_enabled() -> None:
    session = _ListingSession([ListToolsResult(tools=[])])
    session.initialize_release = asyncio.Event()

    @asynccontextmanager
    async def open_session():
        async with session as entered:
            yield entered

    worker = ServerWorker(
        open_session,
        call_timeout_s=1.0,
        startup_timeout_s=0.05,
        close_timeout_s=1.0,
        max_result_copy_bytes=1024,
        tool_list_limits=ToolListLimits(collection_timeout_s=5.0),
    )
    with pytest.raises(DispatchError) as failed:
        await worker.start()
    assert failed.value.exception_type == "SessionStartFailed"
    assert worker.tool_listing is None
    assert "list" not in [name for name, _ in session.events]
    await worker.close()


def test_worker_rejects_wrong_limit_type() -> None:
    with pytest.raises(TypeError, match="tools/list"):
        ServerWorker(
            lambda: None,  # type: ignore[arg-type,return-value]
            call_timeout_s=1.0,
            startup_timeout_s=1.0,
            close_timeout_s=1.0,
            max_result_copy_bytes=1024,
            tool_list_limits={"max_tools": 1},  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_real_synthetic_http_server_listing_is_complete() -> None:
    async with http_spike() as spike:
        transport = McpTransport(
            {"spike": http_stream_factory(spike.endpoint, read_timeout_s=30.0)},
            tool_list_limits=ToolListLimits(),
        )
        await transport.start()
        try:
            listing = transport.tool_listing("spike")
        finally:
            await transport.close()
    assert listing is not None
    assert listing.complete
    assert sorted(tool["name"] for tool in listing.tools) == ["read", "send"]
    assert spike.counts["receiver_effect"] == 0


class _SlowStreams:
    """Stream factory whose open takes ``delay`` seconds (stdio spawn or HTTP connect)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def __call__(self):
        @asynccontextmanager
        async def open_streams():
            await asyncio.sleep(self.delay)
            yield (object(), object())

        return open_streams()


@pytest.mark.asyncio
async def test_initialization_bound_includes_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # C proposal v1: "서버 연결·initialize 완료까지. 대기 시작부터 계산".
    session = _ListingSession([ListToolsResult(tools=[])])
    monkeypatch.setattr(
        mcp_transport, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport(
        {"one": _SlowStreams(0.3)},
        startup_timeout_s=0.1,
        tool_list_limits=ToolListLimits(collection_timeout_s=5.0),
    )
    with pytest.raises(DispatchError) as failed:
        await transport.start()
    assert failed.value.exception_type == "SessionStartFailed"
    assert "initialize" not in [name for name, _ in session.events]


@pytest.mark.asyncio
async def test_collection_deadline_counts_from_initialization_start() -> None:
    # C proposal v1: "초기화 시작부터 모든 목록 페이지 수신 완료까지".
    session = _ListingSession([ListToolsResult(tools=[], nextCursor="c")])
    session.pages.delay = 0.1
    session.initialize_release = asyncio.Event()

    @asynccontextmanager
    async def open_session():
        async with session as entered:
            yield entered

    worker = ServerWorker(
        open_session,
        call_timeout_s=1.0,
        startup_timeout_s=1.0,
        close_timeout_s=1.0,
        max_result_copy_bytes=1024,
        tool_list_limits=ToolListLimits(page_timeout_s=1.0, collection_timeout_s=0.3),
    )
    starting = asyncio.create_task(worker.start())
    await asyncio.sleep(0.25)  # initialize uses most of the 0.3 s collection window
    session.initialize_release.set()
    await starting
    assert worker.tool_listing is not None
    assert worker.tool_listing.reason == "TOOLS_LIST_TIMEOUT"
    await worker.close()


@pytest.mark.asyncio
async def test_close_during_collection_is_a_clean_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ListingSession([ListToolsResult(tools=[])])
    session.pages.delay = 5.0
    monkeypatch.setattr(
        mcp_transport, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"one": _streams([])}, tool_list_limits=ToolListLimits())
    starting = asyncio.create_task(transport.start())
    for _ in range(50):
        if "list" in [name for name, _ in session.events]:
            break
        await asyncio.sleep(0.01)
    await transport.close()  # must not report WorkerOwnerFailed
    with pytest.raises(DispatchError):
        await starting
    assert [name for name, _ in session.events][-1] == "exit"
