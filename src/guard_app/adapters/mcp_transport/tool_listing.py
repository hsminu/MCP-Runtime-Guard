"""tools/list collection within the decided Scanner operating limits."""

from __future__ import annotations

import asyncio
import math
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.types import ListToolsResult

from guard_app.adapters.mcp_ingress import IngressError, IngressGate
from guard_app.adapters.mcp_transport.sdk_session import ToolSession
from guard_app.domain.json_types import FrozenJsonObject

_MAX_TOOL_JSON_DEPTH = 64


@dataclass(frozen=True, slots=True)
class ToolListLimits:
    """tools/list collection bounds (contracts 2026-10-15 Scanner operating limits).

    Defaults are the decided values and stay configurable. Their meaning follows
    C's operating proposal v1 that the decision adopted: the Tool root object is
    depth 1; the 10 s initialization bound covers connecting and ``initialize``
    from the start of the wait; the 30 s collection bound runs from the start
    of initialization to the last page. The 1 MiB page and 8 MiB per-listing
    byte limits (contracts 2026-10-13) are measured before parsing, after
    framing/decompression. An actual SDK session requires an A ingress gate;
    legacy SDK-spawned stdio cannot collect. Session test doubles can exercise
    semantic collection independently and cannot prove raw ingress enforcement.
    """

    max_tools: int = 256
    max_pages: int = 32
    max_json_depth: int = 64
    page_timeout_s: float = 10.0
    collection_timeout_s: float = 30.0
    max_page_bytes: int = 1024 * 1024
    max_total_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        for count in (
            self.max_tools,
            self.max_pages,
            self.max_json_depth,
            self.max_page_bytes,
            self.max_total_bytes,
        ):
            if type(count) is not int or count < 1:
                raise ValueError("positive tools/list count limits required")
        if self.max_json_depth > _MAX_TOOL_JSON_DEPTH:
            # FrozenJsonObject itself stops at this depth; a higher setting would
            # be reported as malformed instead of too deep.
            raise ValueError("tools/list JSON depth limit cannot exceed 64")
        for timeout in (self.page_timeout_s, self.collection_timeout_s):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("positive finite tools/list timeouts required")


@dataclass(frozen=True, slots=True, repr=False)
class ToolListing:
    """One server's collected tool definitions, or why the listing is incomplete.

    ``tools`` holds every definition only when ``complete``. An incomplete
    listing keeps no partial definitions and is never "0 tools, done, safe";
    ``reason`` is a fixed code without server text. Definitions are untrusted
    server data for C's Registry/Scanner, not Guard policy input.
    """

    complete: bool
    tools: tuple[FrozenJsonObject, ...]
    reason: str | None

    def __repr__(self) -> str:
        count = len(self.tools)
        return f"ToolListing(complete={self.complete}, tools={count}, reason={self.reason})"


def _incomplete_listing(reason: str) -> ToolListing:
    return ToolListing(complete=False, tools=(), reason=reason)


def _json_depth_exceeds(value: object, limit: int) -> bool:
    """Whether container nesting is deeper than ``limit`` (the Tool object is 1)."""

    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if type(item) is dict:
            children: list[object] = list(cast("dict[str, object]", item).values())
        elif type(item) is list:
            children = cast("list[object]", item)
        else:
            continue
        if depth > limit:
            return True
        pending.extend((child, depth + 1) for child in children)
    return False


async def collect_tool_definitions(
    session: ToolSession, limits: ToolListLimits, *, deadline: float | None = None
) -> ToolListing:
    """Collect one listing; actual SDK sessions require pre-parse ingress.

    Pure session doubles can test the semantic limits without claiming a wire
    guarantee. One gate scope spans every page and is reset only after the
    collection finishes. A failed/abandoned real listing invalidates its
    connection so a late unbounded page cannot be parsed after scope exit.
    """
    gate = getattr(session, "listing_ingress", None)
    if isinstance(session, ClientSession) and not isinstance(gate, IngressGate):
        return _incomplete_listing("TOOLS_LIST_INGRESS_REQUIRED")
    if not isinstance(gate, IngressGate):
        return await _collect_tool_definitions(session, limits, deadline=deadline)
    try:
        gate.begin(limits.max_page_bytes, limits.max_total_bytes)
    except IngressError as error:
        return _incomplete_listing(error.reason)
    try:
        listing = await _collect_tool_definitions(session, limits, deadline=deadline)
        if gate.reason is not None:
            return _incomplete_listing(gate.reason)
        if listing.reason in {"TOOLS_LIST_TIMEOUT", "TOOLS_LIST_FAILED", "TOOLS_LIST_MALFORMED"}:
            gate.fail(listing.reason)
        return listing
    except IngressError as error:
        return _incomplete_listing(error.reason)
    except asyncio.CancelledError:
        with suppress(IngressError):
            gate.fail("TOOLS_LIST_CANCELLED")
        raise
    finally:
        gate.end()


async def _collect_tool_definitions(
    session: ToolSession, limits: ToolListLimits, *, deadline: float | None = None
) -> ToolListing:
    """Page through tools/list within ``limits`` and keep each Tool object whole.

    Each definition is the SDK's parse of the wire object with only the fields
    the server sent (``exclude_unset``), including unknown extra fields. Byte-
    exact originals are not available after SDK parsing. Exceeding any bound is
    an incomplete listing: definitions are never truncated or guessed. A cursor
    seen before or a tool name repeated within the listing also ends it as
    incomplete (C operating proposal v1). ``deadline`` is the loop time at
    which the whole collection ends; by default it is ``collection_timeout_s``
    from now.
    """

    if deadline is None:
        deadline = asyncio.get_running_loop().time() + limits.collection_timeout_s
    tools: list[FrozenJsonObject] = []
    names: set[str] = set()
    cursors: set[str] = set()
    cursor: str | None = None
    try:
        async with asyncio.timeout_at(deadline):
            for _page in range(limits.max_pages):
                async with asyncio.timeout(limits.page_timeout_s):
                    page = await session.list_tools(cursor)
                reason = _add_page(page, limits, tools, names)
                if reason is not None:
                    return _incomplete_listing(reason)
                cursor = page.nextCursor
                if cursor is None:
                    return ToolListing(complete=True, tools=tuple(tools), reason=None)
                if cursor in cursors:
                    return _incomplete_listing("TOOLS_LIST_CURSOR_REPEATED")
                cursors.add(cursor)
                # Let shutdown/control run between pages of a long listing.
                await asyncio.sleep(0)
            return _incomplete_listing("TOOLS_LIST_TOO_MANY_PAGES")
    except TimeoutError:
        return _incomplete_listing("TOOLS_LIST_TIMEOUT")
    except McpError:
        return _incomplete_listing("TOOLS_LIST_FAILED")
    except ValueError:
        # Pydantic ValidationError and FrozenJsonObject rejections are ValueError.
        return _incomplete_listing("TOOLS_LIST_MALFORMED")


def _add_page(
    page: object, limits: ToolListLimits, tools: list[FrozenJsonObject], names: set[str]
) -> str | None:
    """Add one page's definitions whole; the incomplete reason when it breaks a limit."""
    if not isinstance(page, ListToolsResult):
        return "TOOLS_LIST_MALFORMED"
    # Count every listed entry before any de-duplication.
    if len(tools) + len(page.tools) > limits.max_tools:
        return "TOOLS_LIST_TOO_MANY_TOOLS"
    for tool in page.tools:
        if tool.name in names:
            return "TOOLS_LIST_DUPLICATE_NAME"
        names.add(tool.name)
        definition = tool.model_dump(mode="json", by_alias=True, exclude_unset=True)
        if _json_depth_exceeds(definition, limits.max_json_depth):
            return "TOOL_DEFINITION_TOO_DEEP"
        tools.append(FrozenJsonObject(definition))
    return None
