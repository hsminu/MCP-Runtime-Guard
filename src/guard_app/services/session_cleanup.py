"""Owning what a Task launched until its cleanup is proven (10.4, I9).

A Codex session and C's TaskResources stay owned until the session is closed,
C confirms that the process exited and the Task folder is removed, each within
its limit. Any other outcome latches cleanup as unconfirmed for the rest of the
run: nothing is retried, and nothing is erased without that proof.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from guard_app.services.runner import CodexSession
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_preparation import SessionPreparation, TaskResources


@dataclass(frozen=True, slots=True)
class CleanupLimits:
    """How long each cleanup step may take before it counts as unconfirmed."""

    # A session's interrupt and aclose, and shutdown's wait for creators.
    session_s: float = 0.5
    # confirm_exit waits for C2.12 `LaunchedProcess.close()`: stdin EOF, up to 5 s
    # for Codex to exit, then the Job is terminated and seen empty (up to 5 s more).
    # Under the session bound a slow but successful exit would be reported
    # unconfirmed and block new Tasks until restart (PR #35 A review).
    confirm_exit_s: float = 12.0
    # cleanup_task deletes the Task folder in a worker thread (A103). A 20 MB SQLite
    # DB plus logs took about 10 ms on the development PC; the rest is margin for a
    # slow disk or a file scanner, so a successful delete is not reported
    # unconfirmed. Not measured on real Codex Task folders.
    task_folder_s: float = 5.0


class BackgroundWork:
    """Operations nobody awaits: kept referenced, their outcome consumed, never raised."""

    def __init__(self) -> None:
        self._operations: set[asyncio.Future[object]] = set()

    def start(self, operation: Awaitable[object]) -> asyncio.Future[object]:
        future = asyncio.ensure_future(operation)
        self._operations.add(future)
        future.add_done_callback(self._consume)
        return future

    def _consume(self, operation: asyncio.Future[object]) -> None:
        self._operations.discard(operation)
        if not operation.cancelled():
            operation.exception()


class SessionCleanup:
    """The sessions and C resources each Task owns, and their bounded cleanup.

    `on_unconfirmed(task_id)` runs when a Task's cleanup ends unproven, after
    admission has latched the refusal.
    """

    def __init__(
        self,
        preparation: SessionPreparation | None,
        admission: SessionAdmission,
        limits: CleanupLimits,
        work: BackgroundWork,
        on_unconfirmed: Callable[[str], None],
    ) -> None:
        self._preparation = preparation
        self._admission = admission
        self._limits = limits
        self._work = work
        self._on_unconfirmed = on_unconfirmed
        self._sessions: dict[str, CodexSession] = {}
        self._resources: dict[str, TaskResources] = {}
        self._started: set[str] = set()
        self._outcomes: dict[str, asyncio.Future[bool]] = {}

    @property
    def pending(self) -> frozenset[str]:
        """Tasks whose session or resources are still owned: cleanup not yet proven."""
        return frozenset(self._sessions.keys() | self._resources.keys())

    def claim_resources(self, task_id: str, resources: TaskResources) -> TaskResources:
        """Own C's receipt for this Task. A mismatched or shared one is never owned."""
        if (
            type(resources) is not TaskResources
            or resources.task_id != task_id
            or type(resources.owner_token) is not str
            or not resources.owner_token
            or len(resources.owner_token) > 256
            or resources.handle is None
            or any(
                resources.owner_token == owned.owner_token or resources.handle is owned.handle
                for owned in self._resources.values()
            )
        ):
            # A mismatched/shared receipt never grants ownership to erase it.
            # Its partial resource ownership is nevertheless unconfirmed;
            # reject additional allocations until the app is restarted.
            self._admission.mark_cleanup_unconfirmed()
            raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        self._resources[task_id] = resources
        return resources

    def claim_session(self, task_id: str, session: CodexSession) -> None:
        """Own the factory's session. One another Task owns, or without its methods, is invalid."""
        if any(session is existing for existing in self._sessions.values()):
            raise SessionRuntimeError("CODEX_SESSION_INVALID")
        self._sessions[task_id] = session
        if not all(
            callable(getattr(session, name, None)) for name in ("send", "interrupt", "aclose")
        ):
            raise SessionRuntimeError("CODEX_SESSION_INVALID")

    async def close(self, task_id: str, *, interrupt: bool = False) -> None:
        """Close the session, confirm the exit, then remove the Task folder.

        A concurrent close adopts the in-flight cleanup's result instead of
        reporting it unconfirmed or running it a second time. One that ended
        unconfirmed is never retried.
        """
        session = self._sessions.get(task_id)
        resources = self._resources.get(task_id)
        if session is None and resources is None:
            return
        running = self._outcomes.get(task_id)
        if running is not None:
            if not await asyncio.shield(running):
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            return
        if task_id in self._started:
            raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        self._started.add(task_id)
        outcome = asyncio.get_running_loop().create_future()
        self._outcomes[task_id] = outcome
        complete = False
        try:
            complete = await self._release(task_id, session, resources, interrupt=interrupt)
            if not complete:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            self._sessions.pop(task_id, None)
            self._started.discard(task_id)
        finally:
            self._outcomes.pop(task_id, None)
            outcome.set_result(complete)
            if not complete:
                self._admission.mark_cleanup_unconfirmed()
                self._on_unconfirmed(task_id)

    async def _release(
        self,
        task_id: str,
        session: CodexSession | None,
        resources: TaskResources | None,
        *,
        interrupt: bool,
    ) -> bool:
        closed = session is None
        if session is not None:
            interrupt_session = getattr(session, "interrupt", None)
            close_session = getattr(session, "aclose", None)
            try:
                if interrupt and callable(interrupt_session):
                    # interrupt is a protocol courtesy; that the process has exited
                    # is what confirm_exit proves. A lost or slow interrupt reply
                    # alone must not mark every future session as unsafe (A88 QA).
                    await self._bounded(interrupt_session, self._limits.session_s)
            finally:
                if callable(close_session):
                    closed = await self._bounded(close_session, self._limits.session_s)
        if resources is None:
            return closed
        preparation = self._preparation
        if preparation is None:
            return False
        confirmed = False

        async def confirm() -> None:
            nonlocal confirmed
            confirmed = await preparation.confirm_exit(resources) is True

        finished = await self._bounded(confirm, self._limits.confirm_exit_s)
        # No removal until termination is positively confirmed. If a
        # timed-out callback later returns True, this path stays closed.
        if not (closed and finished and confirmed):
            return False
        cleaned = await self._bounded(
            lambda: preparation.cleanup_task(resources), self._limits.task_folder_s
        )
        if cleaned:
            self._resources.pop(task_id, None)
        return cleaned

    async def _bounded(self, action: Callable[[], Awaitable[object]], timeout_s: float) -> bool:
        async def invoke() -> None:
            if await action() is not None:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")

        operation = self._work.start(invoke())
        try:
            done, _ = await asyncio.wait({operation}, timeout=timeout_s)
            return bool(done) and not operation.cancelled() and operation.exception() is None
        finally:
            if not operation.done():
                operation.cancel()
