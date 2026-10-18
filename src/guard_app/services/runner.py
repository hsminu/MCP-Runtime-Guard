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
    def __init__(self) -> None:
        super().__init__("CODEX_SESSION_FAILED")


class CodexAuthenticationError(CodexSessionError):
    def __init__(self) -> None:
        RuntimeError.__init__(self, "CODEX_AUTH_FAILED")


class CodexSession(Protocol):
    """Trusted injected session, not a wire protocol or agreed B/C port.

    The implementation must own a fresh, hash-checked app-server per Task,
    account/read, Guard-only tool relay, L3 monitoring and Task log cleanup.
    None of these process/security capabilities is implemented here.
    """

    async def send(self, text: str) -> str: ...
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
    def add_user_message(self, content: str) -> None: ...
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
    ) -> None:
        self.task_id = task_id
        self.epoch = epoch
        self._session = session
        self._failure = failure
        self._input: str | None = None
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

    def add_user_message(self, content: str) -> None:
        if not self.can_accept_message or type(content) is not str or not content.strip():
            raise RunnerStateError()
        self._input = content

    async def advance(self) -> RunUpdate:
        async with self._lock:
            if self._closed or self._input is None:
                raise RunnerStateError()
            text, self._input = self._input, None
            self._status = "running"
            completed = failure_handled = False
            try:
                try:
                    response = await self._session.send(text)
                except CodexAuthenticationError:
                    if self._closed:
                        return self._update("closed")
                    failure_handled = True
                    await self._failure("CODEX_AUTH_FAILED")
                    return self._update("halted")
                except CodexSessionError:
                    if self._closed:
                        return self._update("closed")
                    failure_handled = True
                    await self._failure("CODEX_SESSION_FAILED")
                    return self._update("halted")
                if self._closed:
                    return self._update("closed")
                if type(response) is not str or len(response) > 65_536:
                    failure_handled = True
                    await self._failure("CODEX_SESSION_FAILED")
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
