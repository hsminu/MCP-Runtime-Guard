"""A09/A10 transport lifetime and command settlement (I1/I6/I8, T08/T36)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Self

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, CallToolResult, ErrorData, ResourceLink, TextContent

from guard_app.adapters import mcp_transport
from guard_app.adapters.mcp_transport import McpTransport, result_budget, sdk_session
from guard_app.domain.enums import Label, ToolKind, TrustTier
from guard_app.domain.errors import NOT_DISPATCHED, DispatchError
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Boundary, ResolvedRequest, ToolSpec


@contextmanager
def _dispatch_error(code):
    with pytest.raises(DispatchError) as caught:
        yield caught
    assert caught.value.exception_type == code


def _request(
    server_id: str = "server",
    *,
    kind: ToolKind = ToolKind.READ,
    seq: int = 0,
    task_id: str = "task",
) -> ResolvedRequest:
    return ResolvedRequest(
        request_id=f"request-{seq}",
        task_id=task_id,
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
            boundary_id="boundary", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
        ),
        destination=None,
    )


def _result(value: str = "ok") -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=value)])


class _Session:
    def __init__(self, actions: list[object] | None = None, *, blocked=False) -> None:
        self.actions = list(actions or [])
        self.events: list[tuple[str, asyncio.Task[Any] | None]] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.entered = asyncio.Event()
        self.first_call = asyncio.Event()
        self.release_first = asyncio.Event()
        self.block_first = blocked
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


class _ResistantSession(_Session):
    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        self.calls.append((name, arguments))
        self.first_call.set()
        try:
            await self.release_first.wait()
        except asyncio.CancelledError:
            await self.release_first.wait()
        return _result("late")


def _streams(
    events: list[tuple[str, asyncio.Task[Any] | None]], *, reader: object | None = None
) -> Callable:
    @asynccontextmanager
    async def open_streams():
        events.append(("streams-enter", asyncio.current_task()))
        yield (
            reader if reader is not None else object(),
            object(),
            object(),
        )  # HTTP's optional third value is not a stream.
        events.append(("streams-exit", asyncio.current_task()))

    return open_streams


def _transport(monkeypatch, session, *, server_id="server", **options):
    monkeypatch.setattr(sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session)
    return McpTransport({server_id: _streams([])}, **options)


@asynccontextmanager
async def _running(transport):
    try:
        await transport.start()
        yield transport
    finally:
        await transport.close()


def _paired_transport(monkeypatch, first, second, **options):
    sessions = iter((first, second))
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: next(sessions)
    )
    return McpTransport({"first": _streams([]), "second": _streams([])}, **options)


def _worker(session_factory, **options):
    settings = dict(
        call_timeout_s=1, startup_timeout_s=1, close_timeout_s=1, max_result_copy_bytes=512 * 1024
    )
    return mcp_transport.ServerWorker(session_factory, **(settings | options))


@pytest.mark.asyncio
async def test_server_session_lifetime_is_owned_by_one_task_per_server(monkeypatch):
    sessions = [_Session(), _Session()]
    stream_events: list[list[tuple[str, asyncio.Task[Any] | None]]] = [[], []]
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: sessions.pop(0)
    )
    original = sessions.copy()
    transport = McpTransport({"one": _streams(stream_events[0]), "two": _streams(stream_events[1])})
    await transport.start()
    first, second = original
    assert (await transport.call(_request("one", seq=1))).payload is not None
    assert (await transport.call(_request("two", seq=2))).payload is not None
    await transport.close()
    for session, streams in zip(original, stream_events, strict=True):
        tasks = {task for _, task in session.events + streams}
        assert len(tasks) == 1
        assert [name for name, _ in session.events] == ["enter", "initialize", "call", "exit"]
        assert [name for name, _ in streams] == ["streams-enter", "streams-exit"]
    assert first.events[0][1] is not second.events[0][1]
    assert first.calls == [("tool", {"value": 1})]
    assert second.calls == [("tool", {"value": 2})]


@pytest.mark.asyncio
async def test_startup_is_bounded_and_later_server_starts_while_first_is_pending(monkeypatch):
    sessions = {object(): _Session() for _ in range(5)}
    for session in sessions.values():
        session.initialize_release = asyncio.Event()
    readers = list(sessions)
    ordered = [sessions[reader] for reader in readers]
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda reader, *_args, **_kwargs: sessions[reader]
    )
    transport = McpTransport(
        {str(index): _streams([], reader=reader) for index, reader in enumerate(readers)},
        startup_timeout_s=1,
    )

    starting = asyncio.create_task(transport.start())
    await asyncio.gather(*(session.entered.wait() for session in ordered[:4]))
    await asyncio.sleep(0)
    assert not ordered[4].entered.is_set()  # At most four startups are scheduled.
    assert not starting.done()

    assert ordered[1].initialize_release is not None
    ordered[1].initialize_release.set()
    await asyncio.wait_for(ordered[4].entered.wait(), timeout=1)
    assert not ordered[0].initialize_release.is_set()
    assert not starting.done()

    for session in ordered:
        assert session.initialize_release is not None
        session.initialize_release.set()
    await asyncio.wait_for(starting, timeout=1)
    await transport.close()
    assert all(
        [name for name, _ in session.events] == ["enter", "initialize", "exit"]
        for session in ordered
    )


@pytest.mark.asyncio
async def test_startup_failure_cancels_other_initializers_and_closes_every_worker(monkeypatch):
    first, second = _Session(), _Session()
    first.initialize_release = asyncio.Event()
    second.initialize_release = asyncio.Event()
    second.initialize_error = ValueError("SECRET from server")
    first_reader, second_reader = object(), object()
    sessions = {first_reader: first, second_reader: second}
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda reader, *_args, **_kwargs: sessions[reader]
    )
    transport = McpTransport(
        {"first": _streams([], reader=first_reader), "second": _streams([], reader=second_reader)},
        startup_timeout_s=1,
    )

    starting = asyncio.create_task(transport.start())
    await asyncio.gather(first.entered.wait(), second.entered.wait())
    second.initialize_release.set()
    with _dispatch_error("SessionStartFailed") as failed:
        await asyncio.wait_for(starting, timeout=1)
    assert "SECRET" not in str(failed.value)
    assert first.calls == second.calls == []
    assert [name for name, _ in first.events] == ["enter", "initialize", "exit"]
    assert [name for name, _ in second.events] == ["enter", "initialize", "exit"]
    assert all(worker._stopped.is_set() for worker in transport._workers.values())
    await transport.close()  # Idempotent after failed startup.


@pytest.mark.asyncio
async def test_cancelled_start_closes_all_started_sessions(monkeypatch):
    first, second = _Session(), _Session()
    first.initialize_release = asyncio.Event()
    second.initialize_release = asyncio.Event()
    first_reader, second_reader = object(), object()
    sessions = {first_reader: first, second_reader: second}
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda reader, *_args, **_kwargs: sessions[reader]
    )
    transport = McpTransport(
        {"first": _streams([], reader=first_reader), "second": _streams([], reader=second_reader)},
        startup_timeout_s=1,
    )

    starting = asyncio.create_task(transport.start())
    await asyncio.gather(first.entered.wait(), second.entered.wait())
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(starting, timeout=1)
    assert [name for name, _ in first.events] == ["enter", "initialize", "exit"]
    assert [name for name, _ in second.events] == ["enter", "initialize", "exit"]
    assert all(worker._stopped.is_set() for worker in transport._workers.values())
    with _dispatch_error("WorkerClosed"):
        await transport.call(_request("first"))


@pytest.mark.asyncio
async def test_not_ready_duplicate_start_startup_failure_and_closed_call(monkeypatch):
    session = _Session()
    release = asyncio.Event()
    session.initialize_release = release
    transport = _transport(monkeypatch, session)
    with _dispatch_error("WorkerNotReady"):
        await transport.call(_request())
    start = asyncio.create_task(transport.start())
    await session.entered.wait()
    with _dispatch_error("WorkerNotReady"):
        await transport.call(_request())
    release.set()
    await start
    with pytest.raises(RuntimeError, match=r"already started"):
        await transport.start()
    await transport.close()
    with _dispatch_error("WorkerClosed"):
        await transport.call(_request())

    failed_session = _Session()
    failed_session.initialize_error = ValueError("SECRET from server")
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: failed_session
    )
    failed = McpTransport({"server": _streams([])})
    with _dispatch_error("SessionStartFailed") as startup:
        await failed.start()
    assert "SECRET" not in str(startup.value)
    with pytest.raises(DispatchError):
        await failed.call(_request())
    await failed.close()


@pytest.mark.asyncio
async def test_startup_timeout_cancels_initialization_and_never_dispatches(monkeypatch):
    session = _Session()
    session.initialize_release = asyncio.Event()
    transport = _transport(monkeypatch, session, startup_timeout_s=0.01)
    with _dispatch_error("SessionStartTimeout"):
        await transport.start()
    assert session.calls == []
    assert [name for name, _ in session.events] == ["enter", "initialize", "exit"]
    assert len({task for _, task in session.events}) == 1
    with pytest.raises(DispatchError):
        await transport.call(_request(kind=ToolKind.SEND))


@pytest.mark.asyncio
async def test_close_during_initialization_cancels_owner_and_rejects_start(monkeypatch):
    session = _Session()
    session.initialize_release = asyncio.Event()
    transport = _transport(monkeypatch, session, startup_timeout_s=1)
    starting = asyncio.create_task(transport.start())
    await session.entered.wait()
    await transport.close()
    with _dispatch_error("SessionStartFailed"):
        await starting
    assert session.calls == []
    assert [name for name, _ in session.events] == ["enter", "initialize", "exit"]


@pytest.mark.asyncio
async def test_command_error_and_conversion_error_do_not_kill_worker(monkeypatch):
    class BadResult(CallToolResult):
        def model_dump(self, **_kwargs: object) -> object:
            raise ValueError("SECRET raw result")

    session = _Session(
        [_result("one"), ValueError("SECRET tool error"), BadResult(content=[]), _result("four")]
    )
    transport = _transport(monkeypatch, session)
    await transport.start()
    assert (await transport.call(_request(seq=1))).payload["content"][0]["text"] == "one"
    with _dispatch_error("ValueError") as tool_error:
        await transport.call(_request(seq=2))
    assert "SECRET" not in str(tool_error.value)
    with _dispatch_error("ValueError"):
        await transport.call(_request(seq=3))
    assert (await transport.call(_request(seq=4))).payload["content"][0]["text"] == "four"
    await transport.close()
    assert len(session.calls) == 4


@pytest.mark.asyncio
async def test_cancellation_resistant_sdk_call_still_settles_its_future(monkeypatch):

    session = _ResistantSession()
    monkeypatch.setattr(sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session)
    failed: list[str] = []
    transport = McpTransport(
        {"server": _streams([])},
        call_timeout_s=0.01,
        close_timeout_s=0.2,
        on_server_failed=failed.append,
    )
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND)))
    await session.first_call.wait()
    with _dispatch_error("TimeoutError"):
        await asyncio.wait_for(active, timeout=0.15)
    # Past its watchdog the session no longer serves this run (#61).
    assert failed == ["server"]
    session.release_first.set()
    await transport.close()
    assert len(session.calls) == 1
    assert failed == ["server"]


@pytest.mark.asyncio
async def test_cancelled_queue_waiter_is_skipped_but_active_call_is_not_retried(monkeypatch):
    session = _Session(blocked=True)
    transport = _transport(monkeypatch, session)
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
async def test_pending_limit_rejects_excess_without_wire_call_and_recovers_per_server(monkeypatch):
    first, second = _Session(), _Session()
    first.block_first = True
    transport = _paired_transport(monkeypatch, first, second, max_pending_commands=1)
    await transport.start()

    active = asyncio.create_task(transport.call(_request("first", kind=ToolKind.SEND, seq=1)))
    await first.first_call.wait()
    queued = asyncio.create_task(transport.call(_request("first", seq=2)))
    await asyncio.sleep(0)  # Let the second request occupy the pending slot.

    with _dispatch_error("WorkerQueueFull"):
        await asyncio.wait_for(
            transport.call(_request("first", kind=ToolKind.SEND, seq=3)), timeout=1
        )
    assert first.calls == [("tool", {"value": 1})]
    assert (await transport.call(_request("second", seq=10))).payload is not None
    assert second.calls == [("tool", {"value": 10})]

    first.release_first.set()
    assert (await active).payload is not None
    assert (await queued).payload is not None
    assert (await transport.call(_request("first", seq=4))).payload is not None
    await transport.close()
    assert first.calls == [("tool", {"value": 1}), ("tool", {"value": 2}), ("tool", {"value": 4})]


@pytest.mark.asyncio
async def test_close_rejects_waiting_call_and_finishes_active_one(monkeypatch):
    session = _Session(blocked=True)
    transport = _transport(monkeypatch, session, max_pending_commands=1)
    await transport.start()
    active = asyncio.create_task(transport.call(_request(seq=1)))
    await session.first_call.wait()
    waiting = asyncio.create_task(transport.call(_request(seq=2)))
    await asyncio.sleep(0)
    closing = asyncio.create_task(transport.close())
    with _dispatch_error("WorkerClosed"):
        await waiting
    session.release_first.set()
    assert (await active).payload is not None
    await closing
    assert len(session.calls) == 1
    with pytest.raises(DispatchError):
        await transport.call(_request(seq=3))


@pytest.mark.asyncio
async def test_close_rejects_calls_to_other_server_while_first_drains(monkeypatch):
    first, second = _Session(), _Session()
    first.block_first = True
    transport = _paired_transport(monkeypatch, first, second, close_timeout_s=1)
    await transport.start()
    active = asyncio.create_task(transport.call(_request("first", seq=1)))
    await first.first_call.wait()

    closing = asyncio.create_task(transport.close())
    await asyncio.sleep(0)
    assert not closing.done()
    with _dispatch_error("WorkerClosed"):
        await transport.call(_request("second", seq=2))
    assert second.calls == []

    first.release_first.set()
    assert (await active).payload is not None
    await closing


@pytest.mark.asyncio
async def test_close_reports_session_exit_failure_without_disclosing_exception(monkeypatch):
    class ExitFailureSession(_Session):
        exit_count = 0

        async def __aexit__(self, *_args: object) -> None:
            self.exit_count += 1
            await super().__aexit__(*_args)
            raise RuntimeError("SECRET session teardown detail")

    session = ExitFailureSession()
    transport = _transport(monkeypatch, session)
    await transport.start()

    for _ in range(2):
        with _dispatch_error("SessionCloseFailed") as failure:
            await transport.close()
        assert "SECRET" not in str(failure.value)
    assert session.exit_count == 1
    assert transport._workers["server"]._state.name == "FAILED"


@pytest.mark.asyncio
async def test_close_reports_teardown_failure_after_initialize_failure():
    class InitExitFailureSession(_Session):
        async def __aexit__(self, *_args: object) -> None:
            await super().__aexit__(*_args)
            raise RuntimeError("SECRET teardown after initialize")

    session = InitExitFailureSession()
    session.initialize_error = ValueError("SECRET initialize")
    worker = _worker(lambda: session)

    with _dispatch_error("SessionStartFailed"):
        await worker.start()
    assert worker._session_entered
    assert worker._session_exit_attempted
    assert not worker._session_exited

    for _ in range(2):
        with _dispatch_error("SessionCloseFailed") as shutdown:
            await worker.close()
        assert "SECRET" not in str(shutdown.value)
    assert [name for name, _ in session.events] == ["enter", "initialize", "exit"]


def _connection_closed() -> McpError:
    return McpError(ErrorData(code=CONNECTION_CLOSED, message="Connection closed"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["stream ended", "error reply", "another error", "a copy"])
async def test_a_session_ended_in_initialize_closes_only_if_its_exit_added_nothing(case):
    """A158: the SDK and pipe task groups wrap initialize's own error once they finish."""

    class EndedSession(_Session):
        async def __aexit__(self, exc_type, exc, tb):
            await super().__aexit__(exc_type, exc, tb)
            leaves = [_connection_closed()] if case == "a copy" else [exc]
            if case == "another error":
                leaves.append(RuntimeError("SECRET pump failure"))
            raise ExceptionGroup("session", [ExceptionGroup("pipes", leaves)])

    session = EndedSession()
    session.initialize_error = (
        McpError(ErrorData(code=-32603, message="SECRET refusal"))
        if case == "error reply"
        else _connection_closed()
    )
    worker = _worker(lambda: session)
    with _dispatch_error("SessionStartFailed") as start:
        await worker.start()
    assert "SECRET" not in str(start.value)
    if case in ("stream ended", "error reply"):
        await worker.close()
        assert worker._session_exited
    else:
        with _dispatch_error("SessionCloseFailed") as shutdown:
            await worker.close()
        assert "SECRET" not in str(shutdown.value)
        assert not worker._session_exited
    assert [name for name, _ in session.events] == ["enter", "initialize", "exit"]


def test_only_a_group_of_the_body_error_itself_counts_as_a_completed_exit():
    body = _connection_closed()
    cancelled = asyncio.CancelledError()
    only_the_body = mcp_transport.worker._only_the_body_error
    assert only_the_body(ExceptionGroup("outer", [ExceptionGroup("inner", [body])]), body)
    assert not only_the_body(ExceptionGroup("outer", [body, OSError("other")]), body)
    assert not only_the_body(BaseExceptionGroup("outer", [cancelled]), cancelled)
    assert not only_the_body(ExceptionGroup("outer", [body]), None)


@pytest.mark.asyncio
async def test_an_owner_being_cancelled_keeps_the_ended_session_exit_unconfirmed():
    error = _connection_closed()

    class Context:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc_info):
            asyncio.current_task().cancel()
            raise ExceptionGroup("session", [error])

    seen = []

    async def owner():
        tracker = mcp_transport.worker._SessionExitTracker(Context())
        await tracker.__aenter__()
        try:
            await tracker.__aexit__(McpError, error, None)
        except ExceptionGroup:
            seen.append(("raised", tracker.exited))

    await asyncio.gather(asyncio.create_task(owner()), return_exceptions=True)
    assert seen == [("raised", False)]


@pytest.mark.asyncio
async def test_close_reports_owner_exit_failure_that_preceded_close(monkeypatch):
    class ExitFailureSession(_Session):
        async def __aexit__(self, *_args: object) -> None:
            await super().__aexit__(*_args)
            raise RuntimeError("SECRET background teardown detail")

    session = ExitFailureSession()
    transport = _transport(monkeypatch, session)
    await transport.start()
    owner = transport._workers["server"]._owner
    assert owner is not None
    owner.cancel()
    await transport._workers["server"]._stopped.wait()

    with _dispatch_error("SessionCloseFailed") as failure:
        await transport.close()
    assert "SECRET" not in str(failure.value)


@pytest.mark.asyncio
async def test_close_reports_unexpected_owner_cancellation_after_clean_exit(monkeypatch):
    session = _Session()
    transport = _transport(monkeypatch, session)
    await transport.start()
    owner = transport._workers["server"]._owner
    assert owner is not None
    owner.cancel()
    await transport._workers["server"]._stopped.wait()

    with _dispatch_error("WorkerOwnerFailed"):
        await transport.close()
    assert [name for name, _ in session.events][-1] == "exit"


@pytest.mark.asyncio
async def test_close_quiesces_all_workers_before_waiting_for_any_owner(monkeypatch):
    first, second = _Session(), _Session()
    first.block_first = second.block_first = True
    transport = _paired_transport(monkeypatch, first, second, close_timeout_s=1)
    await transport.start()
    active_first = asyncio.create_task(transport.call(_request("first", kind=ToolKind.SEND, seq=1)))
    active_second = asyncio.create_task(transport.call(_request("second", seq=2)))
    await asyncio.gather(first.first_call.wait(), second.first_call.wait())
    queued_send = asyncio.create_task(transport.call(_request("second", kind=ToolKind.SEND, seq=3)))
    await asyncio.sleep(0)

    closing = asyncio.create_task(transport.close())
    with _dispatch_error("WorkerClosed"):
        await asyncio.wait_for(queued_send, timeout=1)
    second.release_first.set()
    assert (await active_second).payload is not None
    assert len(second.calls) == 1  # The queued SEND never entered call_tool.
    assert not closing.done()  # The first active SEND is still unresolved.

    first.release_first.set()
    assert (await active_first).payload is not None
    await closing
    await transport.close()  # Successful shutdown remains idempotent.
    assert len(first.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_close_can_be_retried_after_owner_finishes(monkeypatch):
    session = _Session(blocked=True)
    transport = _transport(monkeypatch, session)
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND, seq=1)))
    await session.first_call.wait()

    closing = asyncio.create_task(transport.close())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with _dispatch_error("WorkerClosed"):
        await transport.call(_request(seq=2))

    session.release_first.set()
    assert (await active).payload is not None
    await transport.close()
    assert [name for name, _ in session.events][-1] == "exit"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_close_timeout_settles_active_command_without_waiting_for_sdk(monkeypatch):

    session = _ResistantSession()
    transport = _transport(monkeypatch, session, call_timeout_s=1, close_timeout_s=0.01)
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND)))
    await session.first_call.wait()
    with _dispatch_error("WorkerCloseTimeout"):
        await transport.close()
    with _dispatch_error("WorkerCloseTimeout"):
        await active
    session.release_first.set()
    await asyncio.sleep(0)
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_broken_connection_fails_queued_calls_without_retry(monkeypatch):
    session = _Session(
        [ConnectionError("SECRET connection"), _result("should not run")], blocked=True
    )
    transport = _transport(monkeypatch, session)
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND, seq=1)))
    await session.first_call.wait()
    queued = asyncio.create_task(transport.call(_request(seq=2)))
    await asyncio.sleep(0)
    session.release_first.set()
    with _dispatch_error("ConnectionError"):
        await active
    with _dispatch_error("WorkerClosed"):
        await queued
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_owner_cancelled_mid_call_settles_the_commands_still_queued(monkeypatch):
    """The SDK cancels the session owner when its stream task fails during a call.

    No stop path has emptied the queue then; the owner's own exit must settle
    the waiting commands rather than leave their callers waiting forever (I9).
    """

    class CancelledSession(_Session):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            await super().call_tool(name, arguments)
            raise asyncio.CancelledError

    session = CancelledSession(blocked=True)
    failed: list[str] = []
    transport = _transport(monkeypatch, session, on_server_failed=failed.append)
    await transport.start()
    active = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND, seq=1)))
    await session.first_call.wait()
    queued = asyncio.create_task(transport.call(_request(seq=2)))
    await asyncio.sleep(0)
    session.release_first.set()
    with _dispatch_error("WorkerInterrupted"):
        await asyncio.wait_for(active, 1)
    with _dispatch_error("WorkerClosed"):
        await asyncio.wait_for(queued, 1)
    with _dispatch_error("WorkerOwnerFailed"):
        await transport.close()
    assert len(session.calls) == 1  # the queued call never reached the server
    assert failed == ["server"]  # the session ended while serving (#61)


_CLOSED_BY_SDK = McpError(ErrorData(code=CONNECTION_CLOSED, message="Connection closed"))


@pytest.mark.asyncio
async def test_a_broken_session_is_reported_once_by_server_id_only(monkeypatch):
    async def check(monkeypatch, broken):
        """#61: the owner learns which server stopped serving, once, and nothing else."""
        first, second = _Session([broken]), _Session()
        sessions = iter((first, second))
        monkeypatch.setattr(
            sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: next(sessions)
        )
        failed: list[object] = []

        def record(*args: object) -> None:
            failed.append(args)

        transport = McpTransport(
            {"first": _streams([]), "second": _streams([])}, on_server_failed=record
        )
        await transport.start()
        with pytest.raises(DispatchError):
            await transport.call(_request("first", seq=1))
        assert failed == [("first",)]
        with _dispatch_error("WorkerClosed"):
            await transport.call(_request("first", seq=2))
        assert (await transport.call(_request("second", seq=3))).payload is not None
        await transport.close()
        assert failed == [("first",)]
        assert len(first.calls) == 1

    for broken in [ConnectionError("SECRET connection"), _CLOSED_BY_SDK]:
        with monkeypatch.context() as patch:
            await check(patch, broken)


@pytest.mark.asyncio
async def test_a_server_error_response_is_not_a_server_failure(monkeypatch):
    async def check(monkeypatch, error):
        session = _Session([error, _result("next")])
        failed: list[str] = []
        transport = _transport(monkeypatch, session, on_server_failed=failed.append)
        await transport.start()
        with _dispatch_error("McpError"):
            await transport.call(_request(seq=1))
        assert (await transport.call(_request(seq=2))).payload is not None
        await transport.close()
        assert failed == []

    for error in [
        McpError(ErrorData(code=CONNECTION_CLOSED, message="tool failed")),
        McpError(ErrorData(code=-32603, message="Connection closed")),
    ]:
        with monkeypatch.context() as patch:
            await check(patch, error)


@pytest.mark.asyncio
async def test_close_start_failure_and_a_contained_timeout_report_no_server_failure(monkeypatch):
    """Only a session that stops serving on its own is a server failure (#61)."""
    slow, refused = _Session([_result("late"), _result("next")]), _Session()
    slow.block_first = True
    refused.initialize_error = ValueError("SECRET initialize")
    sessions = iter((slow, refused))
    monkeypatch.setattr(
        sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: next(sessions)
    )
    failed: list[str] = []
    transport = McpTransport(
        {"slow": _streams([])}, call_timeout_s=0.25, on_server_failed=failed.append
    )
    await transport.start()
    with _dispatch_error("TimeoutError"):
        await transport.call(_request("slow", kind=ToolKind.SEND))
    assert (await transport.call(_request("slow", seq=2))).payload is not None
    await transport.close()

    assert len(slow.calls) == 2  # The timed-out SEND was not retried.

    not_started = McpTransport({"refused": _streams([])}, on_server_failed=failed.append)
    with pytest.raises(DispatchError):
        await not_started.start()
    await not_started.close()
    assert failed == []


@pytest.mark.asyncio
async def test_dispatch_gate_refuses_a_queued_call_before_any_byte(monkeypatch):
    """#120: 대기 중 서버 정의가 바뀌면 그 호출은 call_tool 없이 NOT_DISPATCHED로 끝난다."""
    session = _Session(blocked=True)
    refused: set[str] = set()
    transport = _transport(
        monkeypatch, session, dispatch_gate=lambda req: req.request_id not in refused
    )
    async with _running(transport):
        first = asyncio.create_task(transport.call(_request(seq=1)))
        await session.first_call.wait()
        second = asyncio.create_task(transport.call(_request(seq=2)))
        await asyncio.sleep(0)
        refused.add("request-2")  # 두 번째가 줄 서 있는 동안 정의가 바뀐다
        session.release_first.set()
        assert (await first).payload is not None
        with pytest.raises(DispatchError) as raised:
            await second
        assert raised.value.exception_type == NOT_DISPATCHED
        third = await transport.call(_request(seq=3))  # worker는 계속 동작한다
        assert third.payload is not None
    assert [arguments["value"] for _, arguments in session.calls] == [1, 3]


@pytest.mark.asyncio
async def test_a_failing_dispatch_gate_settles_every_waiter_without_sending(monkeypatch):
    """#122 A 리뷰: 확인 함수가 예외를 내도 꺼낸 호출과 남은 호출이 끝나고, 보낸 것은 없다."""

    def failing_gate(req: ResolvedRequest) -> bool:
        if req.request_id == "request-2":
            raise RuntimeError("gate failed")
        return True

    session = _Session(blocked=True)
    failed: list[str] = []
    transport = _transport(
        monkeypatch, session, dispatch_gate=failing_gate, on_server_failed=failed.append
    )
    await transport.start()
    first = asyncio.create_task(transport.call(_request(seq=1)))
    await session.first_call.wait()
    checked = asyncio.create_task(transport.call(_request(kind=ToolKind.SEND, seq=2)))
    queued = asyncio.create_task(transport.call(_request(seq=3)))
    await asyncio.sleep(0)
    session.release_first.set()
    assert (await asyncio.wait_for(first, 1)).payload is not None
    # 결과 불명 쪽으로 끝난다: SEND는 UNKNOWN_OUTCOME, READ는 미공개 정지(Guard).
    with _dispatch_error("WorkerInterrupted"):
        await asyncio.wait_for(checked, 1)
    with _dispatch_error("WorkerClosed"):
        await asyncio.wait_for(queued, 1)
    with _dispatch_error("WorkerOwnerFailed"):
        await transport.close()
    assert [arguments["value"] for _, arguments in session.calls] == [1]
    assert failed == ["server"]


@pytest.mark.asyncio
async def test_an_sdk_exception_named_like_the_not_dispatched_marker_is_not_the_marker(
    monkeypatch,
):
    """#122 A 리뷰: call_tool 안에서 난 예외는 이름이 같아도 "보내지 않음 확정"이 아니다(I8)."""
    lookalike = type(NOT_DISPATCHED, (Exception,), {})
    session = _Session([lookalike("raised inside call_tool")])
    transport = _transport(monkeypatch, session)
    async with _running(transport):
        with _dispatch_error("Exception"):
            await transport.call(_request(kind=ToolKind.SEND, seq=1))
    assert len(session.calls) == 1


def test_dispatch_gate_must_be_callable():
    with pytest.raises(TypeError, match="dispatch gate"):
        McpTransport({"server": _streams([])}, dispatch_gate=object())  # type: ignore[arg-type]


def test_server_failure_callbacks_must_be_callable():
    with pytest.raises(TypeError, match=r"server failed callback must be callable"):
        McpTransport({}, on_server_failed="not callable")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"failure callback must be callable"):
        _worker(lambda: _Session(), on_failed="not callable")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_sdk_error_payload_stays_raw_and_unknown_server_never_dispatches(monkeypatch):
    session = _Session(
        [CallToolResult(content=[TextContent(type="text", text="server text")], isError=True)]
    )
    transport = _transport(monkeypatch, session)
    await transport.start()
    with _dispatch_error("UnknownServer"):
        await transport.call(_request("unregistered"))
    raw = await transport.call(_request())
    assert raw.failed is False
    assert raw.payload is not None
    assert raw.payload["isError"] is True
    assert raw.payload["content"][0]["text"] == "server text"
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_result_budget_rejects_oversize_before_copy_and_worker_recovers(monkeypatch):
    dumps = []

    class ObservedResult(CallToolResult):
        def model_dump(self, **kwargs):
            dumps.append("dump")
            return super().model_dump(**kwargs)

    # Exercise independent byte/block/node limits, plus metadata, for I5/I8.
    for bound, rejected, final_text, kinds in (
        (
            "_MAX_RESULT_CONTENT_BLOCKS",
            [
                ObservedResult(content=[TextContent(type="text", text="") for _ in range(5)]),
                ObservedResult(content=[TextContent(type="text", text="S" * 257)]),
            ],
            "next",
            (ToolKind.READ, ToolKind.SEND),
        ),
        (
            "_MAX_RESULT_VALUE_NODES",
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
            ],
            "ready",
            (ToolKind.READ,) * 3,
        ),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(result_budget, bound, 4 if bound.endswith("BLOCKS") else 64)
            session = _Session([*rejected, _result(final_text)])
            transport = _transport(patch, session, max_result_copy_bytes=256)
            await transport.start()
            for seq, kind in enumerate(kinds, 1):
                with _dispatch_error("ResultTooLarge"):
                    await asyncio.wait_for(transport.call(_request(kind=kind, seq=seq)), 1)
            assert dumps == []
            result = await asyncio.wait_for(transport.call(_request(seq=len(kinds) + 1)), 1)
            assert result.payload["content"][0]["text"] == final_text
            await transport.close()
            assert len(session.calls) == len(kinds) + 1  # No SEND retry.


@pytest.mark.asyncio
async def test_result_content_block_count_is_a_bound_of_its_own(monkeypatch):
    """With room left in the byte budget, one content block too many is still refused."""
    monkeypatch.setattr(result_budget, "_MAX_RESULT_CONTENT_BLOCKS", 4)
    blocks = [TextContent(type="text", text="") for _ in range(5)]
    with pytest.raises(result_budget.ResultTooLarge):
        await result_budget.check_result_budget(
            CallToolResult(content=blocks), max_copy_bytes=1024 * 1024
        )
    await result_budget.check_result_budget(
        CallToolResult(content=blocks[:4]), max_copy_bytes=1024 * 1024
    )


@pytest.mark.asyncio
async def test_result_extra_fields_count_against_the_copy_budget():
    """Fields the SDK keeps outside its model (extra="allow") are copied too."""
    padded = CallToolResult(content=[], unexpected="E" * 1000)
    assert padded.model_extra == {"unexpected": "E" * 1000}
    with pytest.raises(result_budget.ResultTooLarge):
        await result_budget.check_result_budget(padded, max_copy_bytes=512)
    await result_budget.check_result_budget(CallToolResult(content=[]), max_copy_bytes=512)


@pytest.mark.asyncio
async def test_result_container_seen_twice_is_refused():
    """A shared or cyclic container would be copied again at every reference."""
    shared = {"value": "v"}
    twice = CallToolResult(content=[], structuredContent={"a": shared, "b": shared})
    assert twice.structuredContent is not None
    assert twice.structuredContent["a"] is twice.structuredContent["b"]
    with pytest.raises(result_budget.ResultTooLarge):
        await result_budget.check_result_budget(twice, max_copy_bytes=1024 * 1024)
    copies = CallToolResult(
        content=[], structuredContent={"a": {"value": "v"}, "b": {"value": "v"}}
    )
    await result_budget.check_result_budget(copies, max_copy_bytes=1024 * 1024)


@pytest.mark.asyncio
async def test_result_walk_yields_to_control_before_settling_command(monkeypatch):
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
    transport = _transport(monkeypatch, session)
    await transport.start()
    raw = await asyncio.wait_for(transport.call(_request()), timeout=1)
    assert raw.payload is not None
    assert observed == [True]
    await transport.close()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_resource_url_is_rejected_without_dump_and_next_call_survives(monkeypatch):
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
    transport = _transport(monkeypatch, session)
    await transport.start()
    with _dispatch_error("TypeError"):
        await transport.call(_request())
    assert dumps == []
    assert (await transport.call(_request(seq=2))).payload["content"][0]["text"] == "next"
    await transport.close()
    assert len(session.calls) == 2


@pytest.mark.parametrize("invalid_budget", (0, -1, True, 8 * 1024 * 1024 + 1))
def test_result_copy_budget_rejects_invalid_configuration(invalid_budget):
    with pytest.raises(ValueError, match=r"result copy budget"):
        McpTransport({}, max_result_copy_bytes=invalid_budget)


@pytest.mark.parametrize("invalid_limit", (0, -1, True, 1025, 1.5))
def test_pending_command_limit_rejects_invalid_configuration(invalid_limit):
    with pytest.raises(ValueError, match=r"pending command limit"):
        McpTransport({}, max_pending_commands=invalid_limit)


@pytest.mark.asyncio
async def test_discard_queued_keeps_stopped_task_send_off_wire_and_frees_slots(monkeypatch):
    """Q01 (I1/I6, T08): a SEND queued behind another Task never dispatches after stop."""
    session = _Session(blocked=True)
    monkeypatch.setattr(sdk_session, "_UnvalidatedClientSession", lambda *_args, **_kwargs: session)
    dispatches: list[str] = []
    transport = McpTransport(
        {"server": _streams([])}, max_pending_commands=3, on_dispatch=dispatches.append
    )
    await transport.start()
    worker = transport._workers["server"]

    slow_read = asyncio.create_task(transport.call(_request(task_id="B", seq=1)))
    await session.first_call.wait()
    send_a = asyncio.create_task(transport.call(_request(task_id="A", kind=ToolKind.SEND, seq=2)))
    other_read = asyncio.create_task(transport.call(_request(task_id="C", seq=3)))
    second_send_a = asyncio.create_task(
        transport.call(_request(task_id="A", kind=ToolKind.SEND, seq=4))
    )
    await asyncio.sleep(0)
    assert worker._queue.full()

    # Synchronous: the caller may settle the stop before its own first await.
    assert transport.discard_queued("A") == 2
    assert worker._queue.qsize() == 1
    for waiter in (send_a, second_send_a):
        with _dispatch_error("DispatchStopped"):
            await waiter
    # The active command already dispatched; it is neither settled nor counted.
    assert transport.discard_queued("B") == 0
    assert transport.discard_queued("A") == 0
    assert not slow_read.done() and not other_read.done()

    # Discarded commands released their slots: two new admissions fit again.
    refill = [asyncio.create_task(transport.call(_request(task_id="C", seq=seq))) for seq in (5, 6)]
    await asyncio.sleep(0)
    assert worker._queue.full()
    with _dispatch_error("WorkerQueueFull"):
        await transport.call(_request(task_id="C", seq=7))

    session.release_first.set()
    assert (await slow_read).payload is not None
    assert (await other_read).payload is not None
    for waiter in refill:
        assert (await waiter).payload is not None
    assert (await transport.call(_request(task_id="A", seq=8))).payload is not None
    await transport.close()
    assert [arguments["value"] for _, arguments in session.calls] == [1, 3, 5, 6, 8]
    assert dispatches == ["request-1", "request-3", "request-5", "request-6", "request-8"]


@pytest.mark.asyncio
async def test_discard_queued_drops_abandoned_items_and_rejects_non_string(monkeypatch):
    session = _Session(blocked=True)
    transport = _transport(monkeypatch, session)
    await transport.start()
    worker = transport._workers["server"]
    active = asyncio.create_task(transport.call(_request(task_id="B", seq=1)))
    await session.first_call.wait()
    abandoned = asyncio.create_task(
        transport.call(_request(task_id="A", kind=ToolKind.SEND, seq=2))
    )
    await asyncio.sleep(0)
    abandoned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoned
    with pytest.raises(TypeError):
        transport.discard_queued(None)  # type: ignore[arg-type]
    # An already-cancelled waiter is removed but is not a newly stopped command.
    assert transport.discard_queued("A") == 0
    assert worker._queue.empty()
    session.release_first.set()
    assert (await active).payload is not None
    await transport.close()
    assert transport.discard_queued("A") == 0
    assert [arguments["value"] for _, arguments in session.calls] == [1]


@pytest.mark.asyncio
async def test_sdk_session_call_skips_server_output_schema_validation(monkeypatch):
    """Q02 (I1/I9): no tools/list or server-schema $ref fetch in the call path."""
    import anyio
    import jsonschema
    from mcp.types import CallToolRequest

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("server output schema must not be evaluated")

    monkeypatch.setattr(jsonschema, "validate", forbidden)
    write_send, write_receive = anyio.create_memory_object_stream[object](1)
    read_send, read_receive = anyio.create_memory_object_stream[object](1)
    session = sdk_session._UnvalidatedClientSession(read_receive, write_send)  # type: ignore[arg-type]
    session._tool_output_schemas["tool"] = {"$ref": "http://127.0.0.1:9/schema"}
    sent: list[object] = []
    returned = CallToolResult(
        content=[TextContent(type="text", text="ok")], structuredContent={"untrusted": "value"}
    )

    async def send_request(request: object, result_type: object, **_kwargs: object) -> object:
        sent.append(request)
        assert result_type is CallToolResult
        return returned

    async def list_tools(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("tools/list must not be sent for a tool call")

    monkeypatch.setattr(session, "send_request", send_request)
    monkeypatch.setattr(session, "list_tools", list_tools)
    try:
        assert await session.call_tool("tool", {"value": 1}) is returned
        assert await session.call_tool("unlisted", {}) is returned
    finally:
        for stream in (write_send, write_receive, read_send, read_receive):
            stream.close()
    request = sent[0].root  # type: ignore[attr-defined]
    assert isinstance(request, CallToolRequest)
    assert request.params.name == "tool" and request.params.arguments == {"value": 1}
