"""A's Codex app-server session manager for one Task (10.4, I6-I9, I11, T21/T22).

One instance owns one fresh app-server's stdio for one trusted Task/epoch. The
pipes come from C's launcher (C2.12) through `TaskResources.handle`; this module
never launches or kills a process and never retries a write.

- start: `initialize` (experimentalApi) -> `initialized` -> `thread/start` with
  only Guard tools (`dynamicTools`), `environments=[]`, ephemeral, user reviewer.
- send: `turn/start`, collect the agent message until `turn/completed`.
- L3: an item type outside the allow list, an approval request or an unknown
  server request stops the Task (fixed code). `request_user_input` gets an empty
  answer and only its occurrence is counted.
- Tool calls: `item/tool/call` -> per-Task FIFO -> Guard relay, bounded by a
  watchdog. A stop decided while answering a call happens after the answer has
  been written.
- Failures surface as `CodexSessionError(code)`; the Runner stops only this Task.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from guard_app.adapters.codex_protocol import (
    CodexProtocolError,
    DynamicToolCall,
    DynamicToolsPlan,
    initialize_params,
    parse_dynamic_tool_call,
    thread_start_params,
    tool_call_response,
    turn_interrupt_params,
    turn_start_params,
)
from guard_app.adapters.codex_stream import CodexStreamChannel
from guard_app.adapters.jsonrpc_client import JsonRpcClient, JsonRpcError, JsonRpcRemoteError
from guard_app.core.model_messages import ModelMessageKind, fixed_model_message
from guard_app.services.guard_relay import RelayResult, TrustedTask
from guard_app.services.runner import CodexAuthenticationError, CodexSessionError
from guard_app.services.task_tool_calls import TaskToolCallQueue, ToolCallQueueError

L3_ALLOWED_ITEMS = frozenset(
    {"userMessage", "agentMessage", "reasoning", "contextCompaction", "dynamicToolCall"}
)
_DECLINED_APPROVALS = frozenset(
    {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}
)
_USAGE_LIMITS = frozenset({"usageLimitExceeded", "rateLimitExceeded"})
MAX_ANSWER_CHARS = 65_536


@runtime_checkable
class CodexPipes(Protocol):
    """Byte operations of C's launched app-server (stdin write, stdout read)."""

    async def read_bytes(self, limit: int) -> bytes: ...
    async def write_bytes(self, frame: bytes) -> None: ...
    async def drain(self) -> None: ...


@dataclass(slots=True)
class SessionObservations:
    """Counts C1.9 can assert on; no content, arguments or paths beyond config."""

    tool_calls_received: int = 0
    guard_submits: int = 0
    responses_by_call: dict[str, int] = field(default_factory=dict)
    interrupts_sent: int = 0
    user_input_requests: int = 0
    excluded_tools: tuple[str, ...] = ()
    dynamic_tool_refs: tuple[str, ...] = ()
    stop_code: str | None = None


@dataclass(frozen=True, slots=True)
class SessionLimits:
    request_timeout_s: float = 30.0
    max_frame_bytes: int = 8 * 1024 * 1024
    max_pending_requests: int = 16
    call_watchdog_s: float = 330.0


class CodexAppServerSession:
    def __init__(
        self,
        pipes: CodexPipes,
        *,
        task: TrustedTask,
        plan: DynamicToolsPlan,
        relay: Callable[[DynamicToolCall], Awaitable[RelayResult]],
        model: str,
        base_instructions: str,
        version: str,
        limits: SessionLimits | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> None:
        self._task = task
        self._plan = plan
        self._relay = relay
        self._model = model
        self._instructions = base_instructions
        self._version = version
        self._limits = limits or SessionLimits()
        self._on_partial = on_partial
        self.observations = SessionObservations(
            excluded_tools=plan.excluded, dynamic_tool_refs=tuple(sorted(plan.tool_refs))
        )
        self._channel = CodexStreamChannel(
            pipes.read_bytes,
            pipes.write_bytes,
            pipes.drain,
            max_frame_bytes=self._limits.max_frame_bytes,
        )
        self._client = JsonRpcClient(
            self._channel,
            request_timeout_s=self._limits.request_timeout_s,
            max_frame_bytes=self._limits.max_frame_bytes,
            max_pending=self._limits.max_pending_requests,
            on_notification=self._notification,
            on_server_request=self._server_request,
            version_field=False,
            on_reply_sent=self._reply_sent,
        )
        self._queue: TaskToolCallQueue[DynamicToolCall, RelayResult] = TaskToolCallQueue(
            task.task_id, self._handle_call
        )
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._turns: dict[str, dict[str, object]] = {}
        self._texts: dict[str, list[str]] = {}
        self._deltas: dict[str, list[str]] = {}
        self._streamed: dict[str, int] = {}
        self._finals: dict[str, list[str]] = {}
        self._answer_chars: dict[str, int] = {}
        self._changed = asyncio.Event()
        # Stops decided while answering, applied after that method's reply.
        self._deferred_stops: dict[str, str] = {}
        self._sending = False
        self._stop_handler: Callable[[str], None] | None = None
        self._stop_reported = False
        self._closed = False

    def set_stop_handler(self, handler: Callable[[str], None] | None) -> None:
        """Called once when the session stops while no send() is waiting."""
        self._stop_handler = handler

    @property
    def stop_code(self) -> str | None:
        return self.observations.stop_code

    async def start(self) -> None:
        self._client.start()
        try:
            await self._client.request("initialize", initialize_params(self._version))
            await self._client.notify("initialized")
            result = await self._client.request(
                "thread/start",
                thread_start_params(
                    model=self._model, base_instructions=self._instructions, plan=self._plan
                ),
            )
        except (JsonRpcError, JsonRpcRemoteError):
            raise CodexSessionError("CODEX_START_FAILED") from None
        thread = result.get("thread") if type(result) is dict else None
        thread_id = thread.get("id") if type(thread) is dict else None
        if type(thread_id) is not str or not thread_id:
            raise CodexSessionError("CODEX_START_FAILED")
        self._thread_id = thread_id

    async def send(self, text: str) -> str:
        self._sending = True
        try:
            return await self._send(text)
        finally:
            self._sending = False

    async def _send(self, text: str) -> str:
        thread_id = self._require_running()
        try:
            params = turn_start_params(thread_id, text)
        except CodexProtocolError:
            raise CodexSessionError("CODEX_INPUT_INVALID") from None
        try:
            result = await self._client.request("turn/start", params)
        except JsonRpcRemoteError:
            raise CodexSessionError("CODEX_TURN_FAILED") from None
        except JsonRpcError:
            raise CodexSessionError(self.stop_code or "CODEX_SESSION_FAILED") from None
        turn = result.get("turn") if type(result) is dict else None
        turn_id = turn.get("id") if type(turn) is dict else None
        if type(turn_id) is not str or not turn_id:
            raise CodexSessionError("CODEX_TURN_FAILED")
        self._turn_id = turn_id
        self._stream(turn_id)  # deltas may arrive before turn/start's answer
        while turn_id not in self._turns:
            if self.stop_code is not None:
                raise CodexSessionError(self.stop_code)
            if self._client.closed_reason is not None:
                raise CodexSessionError("CODEX_SESSION_FAILED")
            self._changed.clear()
            changed = asyncio.ensure_future(self._changed.wait())
            closed = asyncio.ensure_future(self._client.wait_closed())
            try:
                await asyncio.wait({changed, closed}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                changed.cancel()
                closed.cancel()
        if self.stop_code is not None:
            raise CodexSessionError(self.stop_code)
        return self._turn_answer(turn_id)

    async def interrupt(self) -> None:
        thread_id, turn_id = self._thread_id, self._turn_id
        if thread_id is None or turn_id is None or turn_id in self._turns:
            return
        if self._client.closed_reason is not None:
            # Nothing can be sent anymore; process exit is confirmed by the
            # launcher's confirm_exit, not by this protocol courtesy.
            return
        self.observations.interrupts_sent += 1
        try:
            await self._client.request("turn/interrupt", turn_interrupt_params(thread_id, turn_id))
        except (JsonRpcError, JsonRpcRemoteError):
            raise CodexSessionError("CODEX_INTERRUPT_UNCONFIRMED") from None

    async def aclose(self) -> None:
        self._closed = True
        try:
            await self._queue.aclose()
        except ToolCallQueueError:
            raise CodexSessionError("CODEX_CLEANUP_UNCONFIRMED") from None
        finally:
            await self._client.close()
            self._channel.close()
            self._wake()

    # Notifications arrive synchronously and in order (JsonRpcClient).
    def _notification(self, method: str, params: object) -> None:
        body = params if type(params) is dict else {}
        if method in ("item/started", "item/completed"):
            item = body.get("item")
            kind = item.get("type") if type(item) is dict else None
            if kind not in L3_ALLOWED_ITEMS:
                self._stop("CODEX_L3_VIOLATION")
                return
            if method == "item/completed" and kind == "agentMessage" and type(item) is dict:
                turn_id = body.get("turnId")
                answer = item.get("text")
                if type(turn_id) is str and type(answer) is str:
                    self._texts.setdefault(turn_id, []).append(answer)
                    if item.get("phase") == "final_answer":
                        self._finals.setdefault(turn_id, []).append(answer)
        elif method == "item/agentMessage/delta":
            turn_id, delta = body.get("turnId"), body.get("delta")
            if type(turn_id) is str and type(delta) is str:
                total = self._answer_chars.get(turn_id, 0) + len(delta)
                self._answer_chars[turn_id] = total
                if total > MAX_ANSWER_CHARS:
                    self._stop("CODEX_ANSWER_TOO_LARGE")
                    return
                self._deltas.setdefault(turn_id, []).append(delta)
                if turn_id == self._turn_id:
                    self._stream(turn_id)
        elif method == "turn/completed":
            turn = body.get("turn")
            turn_id = turn.get("id") if type(turn) is dict else None
            if type(turn) is dict and type(turn_id) is str:
                self._turns[turn_id] = turn
                self._wake()
        elif method == "account/rateLimits/updated":
            limits = body.get("rateLimits")
            if type(limits) is dict and limits.get("rateLimitReachedType"):
                self._stop("CODEX_USAGE_LIMIT")

    async def _server_request(self, method: str, params: object) -> object:
        if method == "item/tool/call":
            return await self._tool_call(params)
        if method in _DECLINED_APPROVALS:
            # Codex approvals are out of scope (L1/L3): decline, then stop.
            self._defer_stop(method, "CODEX_L3_VIOLATION")
            return {"decision": "decline"}
        if method == "item/tool/requestUserInput":
            self.observations.user_input_requests += 1
            return {"answers": {}}
        self._stop("CODEX_L3_VIOLATION")
        raise CodexProtocolError("CODEX_SERVER_REQUEST_REFUSED")

    async def _tool_call(self, params: object) -> object:
        self.observations.tool_calls_received += 1
        stopped = tool_call_response(*_as_texts(fixed_model_message(ModelMessageKind.STOPPED)))
        try:
            call = parse_dynamic_tool_call(params)
        except CodexProtocolError:
            self._defer_stop("item/tool/call", "CODEX_TOOL_CALL_INVALID")
            return stopped
        if call.thread_id != self._thread_id or self.stop_code is not None:
            self._defer_stop("item/tool/call", "CODEX_TOOL_CALL_INVALID")
            return stopped
        try:
            result = await self._queue.enqueue(call.call_id, call)
        except ToolCallQueueError:
            self._defer_stop("item/tool/call", "CODEX_TOOL_CALL_INVALID")
            return stopped
        counts = self.observations.responses_by_call
        counts[call.call_id] = counts.get(call.call_id, 0) + 1
        if result.stop_code is not None:
            self._defer_stop("item/tool/call", result.stop_code)
        return result.response

    async def _handle_call(self, _task_id: str, call: DynamicToolCall) -> RelayResult:
        stopped = tool_call_response(*_as_texts(fixed_model_message(ModelMessageKind.STOPPED)))
        if self.stop_code is not None:
            # Already queued calls never reach Guard once the session stopped.
            return RelayResult(stopped)
        self.observations.guard_submits += 1
        try:
            # The watchdog covers this call's own processing (incl. approval
            # wait), not the time it spent queued behind earlier calls (A80).
            async with asyncio.timeout(self._limits.call_watchdog_s):
                return await self._relay(call)
        except TimeoutError:
            return RelayResult(stopped, "CODEX_WATCHDOG_TIMEOUT")

    def _stream(self, turn_id: str) -> None:
        if self._on_partial is None:
            return
        deltas = self._deltas.get(turn_id, [])
        for delta in deltas[self._streamed.get(turn_id, 0) :]:
            self._on_partial(delta)
        self._streamed[turn_id] = len(deltas)

    def _defer_stop(self, method: str, code: str) -> None:
        self._deferred_stops.setdefault(method, code)

    def _reply_sent(self, method: str) -> None:
        code = self._deferred_stops.pop(method, None)
        if code is not None:
            self._stop(code)

    def _stop(self, code: str) -> None:
        if self.observations.stop_code is None:
            self.observations.stop_code = code
        self._wake()
        # With no send() to raise it, the owner must stop the Task itself.
        if not self._sending and not self._closed and not self._stop_reported:
            self._stop_reported = True
            if self._stop_handler is not None:
                self._stop_handler(self.observations.stop_code)

    def _wake(self) -> None:
        self._changed.set()

    def _require_running(self) -> str:
        if self._closed or self._thread_id is None:
            raise CodexSessionError("CODEX_SESSION_FAILED")
        if self.stop_code is not None:
            raise CodexSessionError(self.stop_code)
        return self._thread_id

    def _turn_answer(self, turn_id: str) -> str:
        turn = self._turns[turn_id]
        status = turn.get("status")
        if status == "completed":
            finals = self._finals.get(turn_id)
            texts = self._texts.get(turn_id)
            if finals:
                answer = "\n\n".join(finals)
            elif texts:
                answer = "\n\n".join(texts)
            else:
                answer = "".join(self._deltas.get(turn_id, []))
            if len(answer) > MAX_ANSWER_CHARS:
                raise CodexSessionError("CODEX_ANSWER_TOO_LARGE")
            return answer
        error = turn.get("error")
        info = error.get("codexErrorInfo") if type(error) is dict else None
        if info == "unauthorized":
            raise CodexAuthenticationError()
        if info in _USAGE_LIMITS:
            raise CodexSessionError("CODEX_USAGE_LIMIT")
        if status == "interrupted":
            raise CodexSessionError("CODEX_TURN_INTERRUPTED")
        raise CodexSessionError("CODEX_TURN_FAILED")


def _as_texts(message: tuple[bool, str]) -> tuple[bool, tuple[str, ...]]:
    success, text = message
    return success, (text,)
