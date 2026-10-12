"""A09/A10 transport lifetime and command settlement (I1/I6/I8, T08/T36)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Self

import pytest
from mcp.types import CallToolResult, ResourceLink, TextContent

from guard_app.adapters import mcp_transport
from guard_app.adapters.mcp_transport import McpTransport
from guard_app.domain.enums import Label, ToolKind, TrustTier
from guard_app.domain.errors import DispatchError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Boundary, ResolvedRequest, ToolSpec


def _request(
    server_id: str = "server", *, kind: ToolKind = ToolKind.READ, seq: int = 0
) -> ResolvedRequest:
    return ResolvedRequest(
        request_id=f"request-{seq}",
        task_id="task",
        epoch=0,
        call_seq=seq,
        tool=ToolSpec(
            server_id=server_id,
            tool_name="tool",
            kind=kind,
            input_schema=FrozenJsonObject({"type": "object"}),
            destination_arg=None,
            resource_arg=None,
            fingerprint="a" * 64,
        ),
        arguments=Arguments({"value": seq}),
        resource_id=None,
        server_boundary=Boundary(
            boundary_id="boundary",
            tier=TrustTier.INTERNAL,
            max_label=Label.CONFIDENTIAL,
        ),
        destination=None,
    )


def _result(value: str = "ok") -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=value)])


class _Session:
    def __init__(self, actions: list[object] | None = None) -> None:
        self.actions = list(actions or [])
        self.events: list[tuple[str, asyncio.Task[Any] | None]] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.entered = asyncio.Event()
        self.first_call = asyncio.Event()
        self.release_first = asyncio.Event()
        self.block_first = False
        self.initialize_error: Exception | None = None
        self.initialize_release: asyncio.Event | None = None

    async def __aenter__(self) -> Self:
        self.events.append(("enter", asyncio.current_task()))
        self.entered.set()
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.events.append(("exit", asyncio.current_task()))

    async def initialize(self) -> None:
        self.events.append(("initialize", asyncio.current_task()))
        if self.initialize_release is not None:
            await self.initialize_release.wait()
        if self.initialize_error is not None:
            raise self.initialize_error

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.events.append(("call", asyncio.current_task()))
        self.calls.append((name, arguments))
        if len(self.calls) == 1:
            self.first_call.set()
            if self.block_first:
                await self.release_first.wait()
        action = self.actions.pop(0) if self.actions else _result()
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action()
        return action


def _streams(events: list[tuple[str, asyncio.Task[Any] | None]]) -> Callable:
    @asynccontextmanager
    async def open_streams():
        events.append(("streams-enter", asyncio.current_task()))
        yield (
            object(),
            object(),
            object(),
        )  # HTTP's optional third value is not a stream.
        events.append(("streams-exit", asyncio.current_task()))

    return open_streams


@pytest.mark.asyncio
async def test_server_session_lifetime_is_owned_by_one_task_per_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = [_Session(), _Session()]
    stream_events: list[list[tuple[str, asyncio.Task[Any] | None]]] = [[], []]
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: sessions.pop(0)
    )
    original = sessions.copy()
    transport = McpTransport(
        {"one": _streams(stream_events[0]), "two": _streams(stream_events[1])}
    )
    await transport.start()
    first, second = original
    assert (await transport.call(_request("one", seq=1))).payload is not None
    assert (await transport.call(_request("two", seq=2))).payload is not None
    await transport.close()
    for session, streams in zip(original, stream_events, strict=True):
        tasks = {task for _, task in session.events + streams}
        assert len(tasks) == 1
        assert [name for name, _ in session.events] == [
            "enter",
            "initialize",
            "call",
            "exit",
        ]
        assert [name for name, _ in streams] == ["streams-enter", "streams-exit"]
    assert first.events[0][1] is not second.events[0][1]
    assert first.calls == [("tool", {"value": 1})]
    assert second.calls == [("tool", {"value": 2})]


@pytest.mark.asyncio
async def test_not_ready_duplicate_start_startup_failure_and_closed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    release = asyncio.Event()
    session.initialize_release = release
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    with pytest.raises(DispatchError) as not_started:
        await transport.call(_request())
    assert not_started.value.exception_type == "WorkerNotReady"
    start = asyncio.create_task(transport.start())
    await session.entered.wait()
    with pytest.raises(DispatchError) as starting:
        await transport.call(_request())
    assert starting.value.exception_type == "WorkerNotReady"
    release.set()
    await start
    with pytest.raises(RuntimeError, match="already started"):
        await transport.start()
    await transport.close()
    with pytest.raises(DispatchError) as closed:
        await transport.call(_request())
    assert closed.value.exception_type == "WorkerClosed"

    failed_session = _Session()
    failed_session.initialize_error = ValueError("SECRET from server")
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: failed_session
    )
    failed = McpTransport({"server": _streams([])})
    with pytest.raises(DispatchError) as startup:
        await failed.start()
    assert startup.value.exception_type == "SessionStartFailed"
    assert "SECRET" not in str(startup.value)
    with pytest.raises(DispatchError):
        await failed.call(_request())
    await failed.close()


@pytest.mark.asyncio
async def test_startup_timeout_cancels_initialization_and_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    session.initialize_release = asyncio.Event()
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])}, startup_timeout_s=0.01)
    with pytest.raises(DispatchError) as timeout:
        await transport.start()
    assert timeout.value.exception_type == "SessionStartTimeout"
    assert session.calls == []
    assert [name for name, _ in session.events] == ["enter", "initialize", "exit"]
    assert len({task for _, task in session.events}) == 1
    with pytest.raises(DispatchError):
        await transport.call(_request(kind=ToolKind.SEND))


@pytest.mark.asyncio
async def test_close_during_initialization_cancels_owner_and_rejects_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    session.initialize_release = asyncio.Event()
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])}, startup_timeout_s=1)
    starting = asyncio.create_task(transport.start())
    await session.entered.wait()
    await transport.close()
    with pytest.raises(DispatchError) as closed_start:
        await starting
    assert closed_start.value.exception_type == "SessionStartFailed"
    assert session.calls == []
    assert [name for name, _ in session.events] == ["enter", "initialize", "exit"]


@pytest.mark.asyncio
async def test_command_error_and_conversion_error_do_not_kill_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadResult(CallToolResult):
        def model_dump(self, **_kwargs: object) -> object:
            raise ValueError("SECRET raw result")

    session = _Session(
        [
            _result("one"),
            ValueError("SECRET tool error"),
            BadResult(content=[]),
            _result("four"),
        ]
    )
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    await transport.start()
    assert (await transport.call(_request(seq=1))).payload["content"][0][
        "text"
    ] == "one"
    with pytest.raises(DispatchError) as tool_error:
        await transport.call(_request(seq=2))
    assert tool_error.value.exception_type == "ValueError"
    assert "SECRET" not in str(tool_error.value)
    with pytest.raises(DispatchError) as conversion_error:
        await transport.call(_request(seq=3))
    assert conversion_error.value.exception_type == "ValueError"
    assert (await transport.call(_request(seq=4))).payload["content"][0][
        "text"
    ] == "four"
    await transport.close()
    assert len(session.calls) == 4


@pytest.mark.asyncio
async def test_timeout_is_contained_without_claiming_no_data_and_worker_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session([_result("late"), _result("next")])
    session.block_first = True
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])}, call_timeout_s=0.01)
    await transport.start()
    with pytest.raises(DispatchError) as timeout:
        await transport.call(_request(kind=ToolKind.SEND))
    assert timeout.value.exception_type == "TimeoutError"
    assert (await transport.call(_request(seq=2))).payload is not None
    await transport.close()
    assert len(session.calls) == 2  # no automatic retry of the timed-out SEND


@pytest.mark.asyncio
async def test_cancellation_resistant_sdk_call_still_settles_its_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResistantSession(_Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            self.calls.append((name, arguments))
            self.first_call.set()
            try:
                await self.release_first.wait()
            except asyncio.CancelledError:
                await self.release_first.wait()
            return _result("late")

    session = ResistantSession()
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport(
        {"server": _streams([])}, call_timeout_s=0.01, close_timeout_s=0.2
    )
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND)))
    await session.first_call.wait()
    with pytest.raises(DispatchError) as timeout:
        await asyncio.wait_for(active, timeout=0.15)
    assert timeout.value.exception_type == "TimeoutError"
    session.release_first.set()
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_queue_waiter_is_skipped_but_active_call_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    session.block_first = True
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND, seq=1)))
    await session.first_call.wait()
    queued = asyncio.create_task(transport.call(_request(seq=2)))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    session.release_first.set()
    assert (await transport.call(_request(seq=3))).payload is not None
    await transport.close()
    assert session.calls == [("tool", {"value": 1}), ("tool", {"value": 3})]


@pytest.mark.asyncio
async def test_pending_limit_rejects_excess_without_wire_call_and_recovers_per_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _Session(), _Session()
    first.block_first = True
    sessions = iter((first, second))
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: next(sessions)
    )
    transport = McpTransport(
        {"first": _streams([]), "second": _streams([])}, max_pending_commands=1
    )
    await transport.start()

    active = asyncio.create_task(
        transport.call(_request("first", kind=ToolKind.SEND, seq=1))
    )
    await first.first_call.wait()
    queued = asyncio.create_task(transport.call(_request("first", seq=2)))
    await asyncio.sleep(0)  # Let the second request occupy the pending slot.

    with pytest.raises(DispatchError) as saturated:
        await asyncio.wait_for(
            transport.call(_request("first", kind=ToolKind.SEND, seq=3)),
            timeout=1,
        )
    assert saturated.value.exception_type == "WorkerQueueFull"
    assert first.calls == [("tool", {"value": 1})]
    assert (await transport.call(_request("second", seq=10))).payload is not None
    assert second.calls == [("tool", {"value": 10})]

    first.release_first.set()
    assert (await active).payload is not None
    assert (await queued).payload is not None
    assert (await transport.call(_request("first", seq=4))).payload is not None
    await transport.close()
    assert first.calls == [
        ("tool", {"value": 1}),
        ("tool", {"value": 2}),
        ("tool", {"value": 4}),
    ]


@pytest.mark.asyncio
async def test_close_rejects_waiting_call_and_finishes_active_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    session.block_first = True
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])}, max_pending_commands=1)
    await transport.start()
    active = asyncio.create_task(transport.call(_request(seq=1)))
    await session.first_call.wait()
    waiting = asyncio.create_task(transport.call(_request(seq=2)))
    await asyncio.sleep(0)
    closing = asyncio.create_task(transport.close())
    with pytest.raises(DispatchError) as rejected:
        await waiting
    assert rejected.value.exception_type == "WorkerClosed"
    session.release_first.set()
    assert (await active).payload is not None
    await closing
    assert len(session.calls) == 1
    with pytest.raises(DispatchError):
        await transport.call(_request(seq=3))


@pytest.mark.asyncio
async def test_close_rejects_calls_to_other_server_while_first_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _Session(), _Session()
    first.block_first = True
    sessions = iter((first, second))
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: next(sessions)
    )
    transport = McpTransport(
        {"first": _streams([]), "second": _streams([])}, close_timeout_s=1
    )
    await transport.start()
    active = asyncio.create_task(transport.call(_request("first", seq=1)))
    await first.first_call.wait()

    closing = asyncio.create_task(transport.close())
    await asyncio.sleep(0)
    assert not closing.done()
    with pytest.raises(DispatchError) as rejected:
        await transport.call(_request("second", seq=2))
    assert rejected.value.exception_type == "WorkerClosed"
    assert second.calls == []

    first.release_first.set()
    assert (await active).payload is not None
    await closing


@pytest.mark.asyncio
async def test_close_timeout_settles_active_command_without_waiting_for_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResistantSession(_Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            self.calls.append((name, arguments))
            self.first_call.set()
            try:
                await self.release_first.wait()
            except asyncio.CancelledError:
                await self.release_first.wait()
            return _result("late")

    session = ResistantSession()
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport(
        {"server": _streams([])}, call_timeout_s=1, close_timeout_s=0.01
    )
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND)))
    await session.first_call.wait()
    with pytest.raises(DispatchError) as close_error:
        await transport.close()
    assert close_error.value.exception_type == "WorkerCloseTimeout"
    with pytest.raises(DispatchError) as command_error:
        await active
    assert command_error.value.exception_type == "WorkerCloseTimeout"
    session.release_first.set()
    await asyncio.sleep(0)
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_broken_connection_fails_queued_calls_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(
        [ConnectionError("SECRET connection"), _result("should not run")]
    )
    session.block_first = True
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND, seq=1)))
    await session.first_call.wait()
    queued = asyncio.create_task(transport.call(_request(seq=2)))
    await asyncio.sleep(0)
    session.release_first.set()
    with pytest.raises(DispatchError) as broken:
        await active
    assert broken.value.exception_type == "ConnectionError"
    with pytest.raises(DispatchError) as rejected:
        await queued
    assert rejected.value.exception_type == "WorkerClosed"
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_sdk_error_payload_stays_raw_and_unknown_server_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(
        [
            CallToolResult(
                content=[TextContent(type="text", text="server text")], isError=True
            )
        ]
    )
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    await transport.start()
    with pytest.raises(DispatchError) as unknown:
        await transport.call(_request("unregistered"))
    assert unknown.value.exception_type == "UnknownServer"
    raw = await transport.call(_request())
    assert raw.failed is False
    assert raw.payload is not None
    assert raw.payload["isError"] is True
    assert raw.payload["content"][0]["text"] == "server text"
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_result_budget_rejects_many_empty_blocks_and_large_text_before_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dumps: list[str] = []

    class ObservedResult(CallToolResult):
        def model_dump(self, **kwargs: Any) -> dict[str, Any]:
            dumps.append("dump")
            return super().model_dump(**kwargs)

    monkeypatch.setattr(mcp_transport, "_MAX_RESULT_CONTENT_BLOCKS", 4)
    session = _Session(
        [
            ObservedResult(
                content=[TextContent(type="text", text="") for _ in range(5)]
            ),
            ObservedResult(content=[TextContent(type="text", text="S" * 257)]),
            _result("next"),
        ]
    )
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])}, max_result_copy_bytes=256)
    await transport.start()

    for request in (_request(seq=1), _request(kind=ToolKind.SEND, seq=2)):
        with pytest.raises(DispatchError) as rejected:
            await asyncio.wait_for(transport.call(request), timeout=1)
        assert rejected.value.exception_type == "ResultTooLarge"
    assert dumps == []  # Neither rejected result reached model_dump.
    assert (await asyncio.wait_for(transport.call(_request(seq=3)), timeout=1)).payload[
        "content"
    ][0]["text"] == "next"
    await transport.close()
    assert len(session.calls) == 3  # SEND is never retried.


@pytest.mark.asyncio
async def test_result_budget_covers_structured_content_and_nested_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dumps: list[str] = []

    class ObservedResult(CallToolResult):
        def model_dump(self, **kwargs: Any) -> dict[str, Any]:
            dumps.append("dump")
            return super().model_dump(**kwargs)

    monkeypatch.setattr(mcp_transport, "_MAX_RESULT_VALUE_NODES", 64)
    session = _Session(
        [
            ObservedResult(
                content=[TextContent(type="text", text="")],
                structuredContent={"payload": "X" * 257},
            ),
            ObservedResult(
                content=[TextContent(type="text", text="")],
                structuredContent={"items": [None] * 65},
            ),
            ObservedResult(
                content=[TextContent(type="text", text="")],
                _meta={"ignored_by_gate": "M" * 257},
            ),
            _result("ready"),
        ]
    )
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])}, max_result_copy_bytes=256)
    await transport.start()

    for seq in (1, 2, 3):
        with pytest.raises(DispatchError) as rejected:
            await asyncio.wait_for(transport.call(_request(seq=seq)), timeout=1)
        assert rejected.value.exception_type == "ResultTooLarge"
    assert dumps == []
    assert (await asyncio.wait_for(transport.call(_request(seq=4)), timeout=1)).payload[
        "content"
    ][0]["text"] == "ready"
    await transport.close()
    assert len(session.calls) == 4


@pytest.mark.asyncio
async def test_result_walk_yields_to_control_before_settling_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[bool] = []

    def observe_control() -> None:
        active = transport._workers["server"]._active
        observed.append(active is not None and not active.future.done())

    class ScheduledControlSession(_Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            self.calls.append((name, arguments))
            asyncio.get_running_loop().call_soon(observe_control)
            return CallToolResult(
                content=[TextContent(type="text", text="ok")],
                structuredContent={"items": [None] * 200},
            )

    session = ScheduledControlSession()
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    await transport.start()
    raw = await asyncio.wait_for(transport.call(_request()), timeout=1)
    assert raw.payload is not None
    assert observed == [True]
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_resource_url_is_rejected_without_dump_and_next_call_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dumps: list[str] = []

    class ObservedResult(CallToolResult):
        def model_dump(self, **kwargs: Any) -> dict[str, Any]:
            dumps.append("dump")
            return super().model_dump(**kwargs)

    session = _Session(
        [
            ObservedResult(
                content=[
                    ResourceLink(
                        type="resource_link",
                        uri="https://example.invalid/resource",
                        name="unsupported",
                    )
                ]
            ),
            _result("next"),
        ]
    )
    monkeypatch.setattr(
        mcp_transport, "ClientSession", lambda *_args, **_kwargs: session
    )
    transport = McpTransport({"server": _streams([])})
    await transport.start()
    with pytest.raises(DispatchError) as rejected:
        await transport.call(_request())
    assert rejected.value.exception_type == "TypeError"
    assert dumps == []
    assert (await transport.call(_request(seq=2))).payload["content"][0][
        "text"
    ] == "next"
    await transport.close()
    assert len(session.calls) == 2


@pytest.mark.parametrize("invalid_budget", (0, -1, True, 8 * 1024 * 1024 + 1))
def test_result_copy_budget_rejects_invalid_configuration(
    invalid_budget: object,
) -> None:
    with pytest.raises(ValueError, match="result copy budget"):
        McpTransport({}, max_result_copy_bytes=invalid_budget)


@pytest.mark.parametrize("invalid_limit", (0, -1, True, 1025, 1.5))
def test_pending_command_limit_rejects_invalid_configuration(
    invalid_limit: object,
) -> None:
    with pytest.raises(ValueError, match="pending command limit"):
        McpTransport({}, max_pending_commands=invalid_limit)
