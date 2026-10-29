"""A-local Codex session proposal; B/C agreement and process integration pending.

Only an explicitly injected, verified Codex session can run. This module never
launches a process, calls a model API, or substitutes for C's L1/L2 checks.
The synthetic regression loop lives exclusively in examples.legacy_runner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

type RunStatus = Literal[
    "complete",
    "continue",
    "pending",
    "denied",
    "withheld",
    "halted",
    "outcome_unknown",
    "model_error",
    "guard_error",
    "closed",
    "stopped",
]


@dataclass(frozen=True, slots=True)
class RunUpdate:
    """Ephemeral execution result; Store remains authoritative for Task state."""

    task_id: str
    epoch: int
    status: RunStatus
    approval_id: str | None = None
    assistant_text: str | None = field(default=None, repr=False)


class RunnerStateError(ValueError):
    def __init__(self) -> None:
        super().__init__("작업 실행 상태가 일치하지 않는다")


class CodexSessionError(RuntimeError):
    """A fixed stop code; never model, tool or exception text."""

    def __init__(self, code: str = "CODEX_SESSION_FAILED") -> None:
        self.code = (
            code if type(code) is str and code.startswith("CODEX_") else "CODEX_SESSION_FAILED"
        )
        super().__init__(self.code)


class CodexAuthenticationError(CodexSessionError):
    def __init__(self) -> None:
        super().__init__("CODEX_AUTH_FAILED")


class CodexSession(Protocol):
    """Trusted injected session, not a wire protocol or agreed B/C port.

    A composes this session from C's verified launcher, L1/L2 and cleanup
    capabilities. A owns account/read, Guard-only tool relay and L3 monitoring;
    C owns the process, path and log-cleanup primitives. The composed session
    must own a fresh app-server per Task. None of this live wiring exists here.
    """

    async def send(self, text: str, effort: str | None = None) -> str: ...
    async def interrupt(self) -> None: ...
    async def aclose(self) -> None: ...


@runtime_checkable
class TaskExecution(Protocol):
    """A-local UI bridge surface shared with the isolated synthetic demo."""

    task_id: str
    epoch: int

    @property
    def can_accept_message(self) -> bool: ...
    @property
    def can_advance(self) -> bool: ...
    @property
    def runtime_status(self) -> str: ...
    def add_user_message(self, content: str, effort: str | None = None) -> None: ...
    async def advance(self) -> RunUpdate: ...
    async def resume_approval(
        self, task_id: str, epoch: int, approval_id: str, outcome: object
    ) -> RunUpdate: ...
    def close(self) -> None: ...


class CodexTaskRunner:
    """One Task's input serialization and late-response invalidation."""

    def __init__(
        self,
        task_id: str,
        epoch: int,
        session: CodexSession,
        failure: Callable[[str], Awaitable[None]],
        execution_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self.task_id = task_id
        self.epoch = epoch
        self._session = session
        self._failure = failure
        self._execution_allowed = execution_allowed
        self._input: str | None = None
        self._effort: str | None = None
        self._closed = False
        self._status = "idle"
        self._lock = asyncio.Lock()

    @property
    def can_accept_message(self) -> bool:
        return not self._closed and self._input is None and not self._lock.locked()

    @property
    def can_advance(self) -> bool:
        return not self._closed and self._input is not None and not self._lock.locked()

    @property
    def runtime_status(self) -> str:
        return self._status

    def add_user_message(self, content: str, effort: str | None = None) -> None:
        """`effort` overrides the reasoning level from this turn on; None keeps it."""
        if not self.can_accept_message or type(content) is not str or not content.strip():
            raise RunnerStateError()
        self._input = content
        self._effort = effort

    async def advance(self) -> RunUpdate:
        async with self._lock:
            if self._closed or self._input is None:
                raise RunnerStateError()
            text, self._input = self._input, None
            effort, self._effort = self._effort, None
            self._status = "running"
            completed = failure_handled = False
            try:
                code, response = await self._send(text, effort)
                if self._closed:
                    return self._update("closed")
                if code is not None:
                    failure_handled = True
                    await self._failure(code)
                    return self._update("halted")
                completed = True
                return self._update("complete", response)
            finally:
                if not completed:
                    try:
                        if not failure_handled and not self._closed:
                            await self._failure("CODEX_SESSION_FAILED")
                    finally:
                        self.close()

    async def _send(self, text: str, effort: str | None) -> tuple[str | None, str]:
        """The session's answer, or the code that halts this Task.

        An authentication failure is a CodexSessionError with CODEX_AUTH_FAILED.
        Any other exception reaches advance(), which halts with CODEX_SESSION_FAILED.
        """
        try:
            if self._execution_allowed is not None and not self._execution_allowed():
                return "CODEX_STORE_HALTED", ""
            response = await self._session.send(text, effort)
            if self._execution_allowed is not None and not self._execution_allowed():
                return "CODEX_STORE_HALTED", ""
        except CodexSessionError as error:
            return error.code, ""
        if type(response) is not str or len(response) > 65_536:
            return "CODEX_SESSION_FAILED", ""
        return None, response

    async def resume_approval(
        self, task_id: str, epoch: int, approval_id: str, outcome: object
    ) -> RunUpdate:
        # B's final-result notification/Guard relay contract is not agreed.
        raise RunnerStateError()

    def close(self) -> None:
        self._closed = True
        self._input = None

    def _update(self, status: RunStatus, text: str | None = None) -> RunUpdate:
        self._status = status
        return RunUpdate(self.task_id, self.epoch, status, assistant_text=text)
