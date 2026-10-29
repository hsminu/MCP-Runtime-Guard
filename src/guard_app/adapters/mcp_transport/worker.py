"""One server's session owner and its bounded command queue."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import Enum, auto
from types import TracebackType

import anyio
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from guard_app.adapters.mcp_ingress import IngressGate
from guard_app.adapters.mcp_transport.launcher_streams import launcher_teardown_failed
from guard_app.adapters.mcp_transport.result_budget import check_result_budget
from guard_app.adapters.mcp_transport.sdk_session import SessionFactory, ToolSession
from guard_app.adapters.mcp_transport.streams import http_teardown_completed
from guard_app.adapters.mcp_transport.tool_listing import (
    ToolListing,
    ToolListLimits,
    collect_tool_definitions,
)
from guard_app.domain.contracts import RawResult
from guard_app.domain.errors import DispatchError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ResolvedRequest


class _State(Enum):
    NEW = auto()
    STARTING = auto()
    READY = auto()
    CLOSING = auto()
    CLOSED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class _Command:
    req: ResolvedRequest
    future: asyncio.Future[RawResult]


class _SessionExitTracker:
    """Observe successful context exit even when the owner was cancelled.

    An HTTP session that died of a request error still tears down completely
    (`http_teardown_completed`), and so does a session whose start failed and
    whose exit re-raised only that failure (`_only_the_body_error`, A158). Those
    exits count as done and are not raised to the owner; the worker is then
    FAILED for the rest of the run like any session that ended on its own. They
    are never applied while the owner itself is being cancelled.
    """

    def __init__(self, context: AbstractAsyncContextManager[ToolSession]) -> None:
        self._context = context
        self.entered = False
        self.exit_attempted = False
        self.exited = False

    async def __aenter__(self) -> ToolSession:
        session = await self._context.__aenter__()
        self.entered = True
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self.exit_attempted = True
        try:
            suppressed = await self._context.__aexit__(exc_type, exc, tb)
        except BaseExceptionGroup as group:
            owner = asyncio.current_task()
            if owner is not None and owner.cancelling():
                raise
            if not http_teardown_completed(group) and not _only_the_body_error(group, exc):
                raise
            # HTTP: the body was cancelled by the SDK's own task group, which
            # has already taken that cancellation back. stdio: the group only
            # carries the body's own error. Nothing outside asked for either.
            self.exited = True
            return True
        self.exited = True
        return suppressed


_STOP = object()

# The ResponseGate's default text/structured limit is 256 KiB. Leave room for
# the SDK envelope while bounding the copy that precedes its semantic checks.
MAX_RESULT_COPY_BYTES = 512 * 1024
MAX_CONFIGURED_RESULT_COPY_BYTES = 8 * 1024 * 1024
DEFAULT_PENDING_COMMANDS = 64
MAX_CONFIGURED_PENDING_COMMANDS = 1024


def require_result_copy_budget(value: object) -> None:
    if type(value) is not int or value < 1 or value > MAX_CONFIGURED_RESULT_COPY_BYTES:
        raise ValueError("bounded positive result copy budget required")


def require_pending_limit(value: object) -> None:
    if type(value) is not int or value < 1 or value > MAX_CONFIGURED_PENDING_COMMANDS:
        raise ValueError("bounded positive pending command limit required")


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    # Task errors have already become fixed DispatchError values for waiters.
    # Retrieve the raw exception solely to suppress asyncio's default log.
    if not task.cancelled():
        task.exception()


def _consume_command_failure(future: asyncio.Future[RawResult]) -> None:
    # A caller can be cancelled after settlement but before awaiting the
    # sanitized exception. Do not let asyncio log even that abandoned item.
    if not future.cancelled():
        future.exception()


def _leaves_are(group: BaseExceptionGroup, error: BaseException) -> bool:
    return all(
        _leaves_are(item, error) if isinstance(item, BaseExceptionGroup) else item is error
        for item in group.exceptions
    )


def _only_the_body_error(group: BaseExceptionGroup, body: BaseException | None) -> bool:
    """A session context that ended re-raising only the body's own error (A158).

    A stdio server that exits during initialize ends the SDK's read stream, so
    initialize raises the fixed connection-closed error (`_session_broken`);
    one that answers initialize with an error raises that `McpError`. mcp
    1.29.0's session and the pipe adapter's task groups wrap that same object
    once all their tasks have finished, and both clean up in `finally`. The C
    launcher's shielded close runs in that exit and raises
    `LAUNCH_EXIT_UNCONFIRMED` in place of the group when it cannot confirm the
    process ended. A group whose every leaf is the body's error therefore means
    the exit added no failure of its own: the teardown is complete. A
    cancellation is never such an error.
    """
    return isinstance(body, Exception) and _leaves_are(group, body)


def _session_broken(exc: Exception) -> bool:
    """The call's failure means the session itself can serve nothing more.

    Besides a closed or broken stream: when its read stream ends, mcp 1.29.0's
    `BaseSession._receive_loop` answers every request still waiting with this
    fixed error, then closes its write stream. A server that sends the same
    code and message only stops its own session for this run.
    """
    if isinstance(exc, McpError):
        return exc.error.code == CONNECTION_CLOSED and exc.error.message == "Connection closed"
    return isinstance(
        exc,
        (
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
            anyio.EndOfStream,
            ConnectionError,
            ExceptionGroup,
        ),
    )


def _exception_name(exc: Exception) -> str:
    name = type(exc).__name__
    return name if name.isascii() and name.isidentifier() and len(name) <= 64 else "Exception"


class ServerWorker:
    """One long-lived task owns one server's stream and ClientSession.

    Only the Guard-facing `call` queues requests. Neither the session nor its
    streams are returned to callers. A cancelled caller abandons its future;
    an already running SDK call continues to a bounded outcome and is never
    re-enqueued. The Guard is responsible for Task status after cancellation.

    `on_failed` is called once, with nothing, when a session that was READY
    stops serving for any reason other than a requested close (#61): the
    owner can then refuse new calls before they are reserved.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        call_timeout_s: float,
        startup_timeout_s: float,
        close_timeout_s: float,
        max_result_copy_bytes: int,
        max_pending_commands: int = DEFAULT_PENDING_COMMANDS,
        on_dispatch: Callable[[str], None] | None = None,
        tool_list_limits: ToolListLimits | None = None,
        on_failed: Callable[[], None] | None = None,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session factory required")
        if on_failed is not None and not callable(on_failed):
            raise TypeError("failure callback must be callable")
        if tool_list_limits is not None and type(tool_list_limits) is not ToolListLimits:
            raise TypeError("tools/list limits have invalid type")
        for timeout in (call_timeout_s, startup_timeout_s, close_timeout_s):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("positive finite worker timeouts required")
        require_result_copy_budget(max_result_copy_bytes)
        require_pending_limit(max_pending_commands)
        self._session_factory = session_factory
        self._on_dispatch = on_dispatch
        self._on_failed = on_failed
        self._failure_reported = False
        self._tool_list_limits = tool_list_limits
        self._tool_listing: ToolListing | None = None
        self._call_timeout_s = float(call_timeout_s)
        self._startup_timeout_s = float(startup_timeout_s)
        self._close_timeout_s = float(close_timeout_s)
        self._max_result_copy_bytes = max_result_copy_bytes
        self._queue: asyncio.Queue[_Command | object] = asyncio.Queue(maxsize=max_pending_commands)
        self._state = _State.NEW
        self._owner: asyncio.Task[None] | None = None
        self._active: _Command | None = None
        self._ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self._unhealthy = False
        self._initialized = False
        self._session_entered = False
        self._session_exit_attempted = False
        self._session_exited = False
        self._owner_cancel_expected = False
        self._close_failed = False
        self._ingress_gate: IngressGate | None = None

    async def start(self) -> None:
        if self._state is not _State.NEW:
            raise RuntimeError("server worker already started")
        self._state = _State.STARTING
        self._owner = asyncio.create_task(self._run(), name="mcp-server-worker")
        self._owner.add_done_callback(_consume_task_exception)
        start_budget = self._startup_timeout_s
        if self._tool_list_limits is not None:
            # Both bounds run from the start in _run. The extra second lets an
            # expired listing report itself as incomplete before this outer
            # wait gives up on the start.
            start_budget = max(start_budget, self._tool_list_limits.collection_timeout_s) + 1.0
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=start_budget)
        except TimeoutError:
            self._state = _State.CLOSING
            self._owner_cancel_expected = True
            self._owner.cancel()
            raise DispatchError("SessionStartTimeout") from None
        if self._state is not _State.READY:
            raise DispatchError("SessionStartFailed")

    @property
    def tool_listing(self) -> ToolListing | None:
        """The startup tools/list result, or None when collection is not enabled."""

        return self._tool_listing

    async def call(self, req: ResolvedRequest) -> RawResult:
        if self._ingress_gate is not None and self._ingress_gate.reason is not None:
            self._quiesce_failed_ingress()
        if self._state is not _State.READY:
            name = (
                "WorkerNotReady" if self._state in (_State.NEW, _State.STARTING) else "WorkerClosed"
            )
            raise DispatchError(name)
        future: asyncio.Future[RawResult] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_Command(req=req, future=future))
        except asyncio.QueueFull:
            # No worker has received this command. Keep the queue bounded and
            # never await capacity after the Guard has reserved a request.
            future.cancel()
            raise DispatchError("WorkerQueueFull") from None
        future.add_done_callback(_consume_command_failure)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # A queued call is skipped. An active call still runs at most once;
            # cancelling its waiter cannot claim the remote side did nothing.
            future.cancel()
            raise

    async def close(self) -> None:
        self.request_close()
        await self.wait_closed()

    def request_close(self) -> None:
        """Stop admission and reject pending commands without awaiting I/O."""
        if self._state is _State.NEW:
            self._state = _State.CLOSED
            self._ready.set()
            self._stopped.set()
            return
        if self._state in (_State.STARTING, _State.READY):
            starting = self._state is _State.STARTING
            self._state = _State.CLOSING
            self._reject_queued("WorkerClosed")
            self._queue.put_nowait(_STOP)
            if starting and self._owner is not None:
                # Cancelling a start (initialize or tools/list) is this close's
                # own action, not an owner failure.
                self._owner_cancel_expected = True
                self._owner.cancel()

    async def wait_closed(self) -> None:
        """Confirm that the session owner finished, including its context exit."""
        if self._owner is None:
            return
        try:
            # _stopped is set in _run's finally, before the owner task has
            # published its final result. Collect that result without leaking
            # an SDK exception or cancelling it on the first close timeout.
            outcome = await asyncio.wait_for(
                asyncio.shield(asyncio.gather(self._owner, return_exceptions=True)),
                timeout=self._close_timeout_s,
            )
        except TimeoutError:
            # Even a cancellation-resistant SDK call cannot keep the Guard's
            # command future pending forever after shutdown has failed.
            if self._active is not None and not self._active.future.done():
                self._active.future.set_exception(DispatchError("WorkerCloseTimeout"))
            if self._owner is not None:
                self._owner_cancel_expected = True
                self._owner.cancel()
            raise DispatchError("WorkerCloseTimeout") from None
        failure = self._close_failure(outcome[0])
        if failure is not None:
            self._state = _State.FAILED
            raise DispatchError(failure) from None
        if self._state is _State.CLOSING:
            self._state = _State.CLOSED

    @property
    def _ingress_close_failed(self) -> bool:
        """The raw ingress could not be closed: the session's teardown is unproven."""
        gate = self._ingress_gate
        return gate is not None and gate.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED"

    def _close_failure(self, owner_error: object) -> str | None:
        """The fixed failure of a finished owner; None when it closed cleanly."""
        if launcher_teardown_failed(owner_error):
            return "SessionCloseFailed"
        owner_failed = isinstance(owner_error, BaseException) and (
            not isinstance(owner_error, asyncio.CancelledError) or not self._owner_cancel_expected
        )
        teardown_failed = self._session_exit_attempted and not self._session_exited
        teardown_missing = self._session_entered and not self._session_exit_attempted
        teardown_unproven = teardown_failed or teardown_missing
        if teardown_unproven or (self._initialized and owner_failed):
            self._close_failed = True
        if not self._close_failed:
            return None
        if teardown_unproven or self._ingress_close_failed:
            return "SessionCloseFailed"
        return "WorkerOwnerFailed"

    async def _run(self) -> None:
        tracker: _SessionExitTracker | None = None
        ingress_watch: asyncio.Task[None] | None = None
        limits = self._tool_list_limits
        started = asyncio.get_running_loop().time()
        # With tools/list collection on, the initialization bound covers
        # connecting and initialize from the start of the wait, and the
        # collection bound runs from the same start (C proposal v1).
        init_bound = None if limits is None else self._startup_timeout_s
        try:
            tracker = _SessionExitTracker(self._session_factory())
            async with asyncio.timeout(init_bound) as init_window:
                async with tracker as session:
                    if not await self._initialize(session, init_window, started):
                        return
                    if self._state is _State.STARTING:
                        self._state = _State.READY
                    if self._ingress_gate is not None:
                        ingress_watch = asyncio.create_task(self._watch_ingress())
                    self._ready.set()
                    await self._serve(session)
        finally:
            if ingress_watch is not None:
                ingress_watch.cancel()
                await asyncio.gather(ingress_watch, return_exceptions=True)
            self._settle_owner_exit(tracker)

    async def _initialize(
        self, session: ToolSession, init_window: asyncio.Timeout, started: float
    ) -> bool:
        """initialize, then the startup listing; False when the session must not be used."""
        candidate_gate = getattr(session, "listing_ingress", None)
        if isinstance(candidate_gate, IngressGate):
            self._ingress_gate = candidate_gate
        await session.initialize()
        init_window.reschedule(None)
        self._initialized = True
        limits = self._tool_list_limits
        if limits is None:
            return True
        # Before READY, so no Guard call reaches a session whose
        # listing is still running. An incomplete listing is a
        # warning for Registry/Scanner and does not stop the worker.
        self._tool_listing = await collect_tool_definitions(
            session, limits, deadline=started + limits.collection_timeout_s
        )
        gate = getattr(session, "listing_ingress", None)
        if (
            self._tool_listing.reason == "TOOLS_LIST_INGRESS_REQUIRED"
            or isinstance(gate, IngressGate)
            and gate.reason is not None
        ):
            # Preserve the fixed incomplete reason for Registry,
            # but never advertise a dead/gateless session READY.
            self._state = _State.FAILED
            return False
        return True

    async def _serve(self, session: ToolSession) -> None:
        """Run queued commands one at a time until the stop marker."""
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            if not isinstance(item, _Command) or item.future.done():
                continue
            self._active = item
            try:
                await self._execute(session, item)
            finally:
                if not item.future.done():
                    item.future.set_exception(DispatchError("WorkerInterrupted"))
                self._active = None

    def _settle_owner_exit(self, tracker: _SessionExitTracker | None) -> None:
        """Record how the session context ended, then release every waiter."""
        if self._ingress_close_failed:
            self._close_failed = True
        if tracker is not None:
            self._session_entered = tracker.entered
            self._session_exit_attempted = tracker.exit_attempted
            self._session_exited = tracker.exited
        serving = self._state is _State.READY
        self._state = (
            _State.FAILED
            if self._unhealthy or self._state in (_State.STARTING, _State.READY)
            else self._state
        )
        self._ready.set()
        self._reject_queued("WorkerClosed")
        self._stopped.set()
        if serving:
            # The session ended on its own while serving (an HTTP request error,
            # a closed stream); a requested close leaves it CLOSING first.
            self._report_failed()

    def _report_failed(self) -> None:
        if self._failure_reported:
            return
        self._failure_reported = True
        if self._on_failed is not None:
            self._on_failed()

    def _quiesce_failed_ingress(self) -> None:
        gate = self._ingress_gate
        if gate is None or gate.reason is None:
            return
        if gate.reason == "TOOLS_LIST_INGRESS_CLOSE_FAILED":
            self._close_failed = True
        if self._state is _State.READY:
            self._stop_unhealthy()

    async def _watch_ingress(self) -> None:
        gate = self._ingress_gate
        if gate is None:
            return
        await gate.failed.wait()
        self._quiesce_failed_ingress()

    async def _execute(self, session: ToolSession, cmd: _Command) -> None:
        """The one broad exception boundary for per-command SDK failures."""
        watchdog = asyncio.get_running_loop().call_later(
            self._call_timeout_s + 0.05, self._force_settle_timeout, cmd
        )
        try:
            try:
                async with asyncio.timeout(self._call_timeout_s):
                    if self._on_dispatch is not None:
                        self._on_dispatch(cmd.req.request_id)
                    result = await session.call_tool(
                        cmd.req.tool.tool_name, cmd.req.arguments.to_dict()
                    )
                    await check_result_budget(result, max_copy_bytes=self._max_result_copy_bytes)
                # This is a private, untrusted JSON copy for B's ResponseGate.
                payload = FrozenJsonObject(
                    result.model_dump(mode="json", by_alias=True, exclude_none=True)
                )
                raw = RawResult(payload=payload)
            except TimeoutError:
                # SDK timeout does not prove that no partial bytes arrived.
                # The Guard must contain READ and mark SEND outcome unknown.
                if not cmd.future.done():
                    cmd.future.set_exception(DispatchError("TimeoutError"))
                return
            except Exception as exc:  # noqa: BLE001 - designated ServerWorker._execute boundary  # nosemgrep: broad-except-only-in-allowed
                if _session_broken(exc):
                    self._stop_unhealthy()
                if not cmd.future.done():
                    cmd.future.set_exception(DispatchError(_exception_name(exc)))
                return
            if not cmd.future.done():
                cmd.future.set_result(raw)
        finally:
            watchdog.cancel()

    def _force_settle_timeout(self, cmd: _Command) -> None:
        if self._active is not cmd:
            return
        self._stop_unhealthy()
        if not cmd.future.done():
            cmd.future.set_exception(DispatchError("TimeoutError"))
        if self._owner is not None:
            self._owner_cancel_expected = True
            self._owner.cancel()

    def discard_queued(self, task_id: str) -> int:
        """Settle one Task's not-yet-started commands without awaiting.

        The Guard checks its stop record only before enqueueing. A human
        cancel/emergency halt recorded later must still keep a queued SEND off
        the wire. Matching commands leave the bounded queue, so their slots are
        free again, and fail with a fixed pre-wire name. The active command has
        already reached ``on_dispatch`` and is left to its bounded outcome.
        """
        if type(task_id) is not str:
            raise TypeError("task ID string required")
        kept: list[_Command | object] = []
        discarded = 0
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if isinstance(item, _Command) and item.req.task_id == task_id:
                if not item.future.done():
                    item.future.set_exception(DispatchError("DispatchStopped"))
                    discarded += 1
                continue
            kept.append(item)
        # Only removed items were not re-queued, so this cannot exceed capacity.
        # A getter woken by an earlier put rechecks emptiness before taking one.
        for item in kept:
            self._queue.put_nowait(item)
        return discarded

    def _stop_unhealthy(self) -> None:
        """A broken session: refuse new commands, reject queued ones, stop the owner loop."""
        serving = self._state is _State.READY
        self._unhealthy = True
        self._state = _State.CLOSING
        self._reject_queued("WorkerClosed")
        self._queue.put_nowait(_STOP)
        if serving:
            self._report_failed()

    def _reject_queued(self, name: str) -> None:
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if isinstance(item, _Command) and not item.future.done():
                item.future.set_exception(DispatchError(name))
