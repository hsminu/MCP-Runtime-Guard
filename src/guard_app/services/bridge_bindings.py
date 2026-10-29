"""Ownership of the BackendBindings from bootstrap until their close returns.

Whatever bootstrap built must be closed exactly once, within a bounded time,
including a result that arrives after startup has already given up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from guard_app.services.approvals import HumanControl
from guard_app.services.bridge_types import BackendBindings, BackendCleanupError


def usable(bindings: BackendBindings) -> bool:
    """The bootstrap result has what the bridge calls, of the expected kind."""
    return (
        callable(bindings.runner_for_task)
        and (bindings.human_control is None or isinstance(bindings.human_control, HumanControl))
        and (bindings.human_control is not None or callable(bindings.stop_session))
        and (bindings.close is None or callable(bindings.close))
        and (bindings.discard_queued is None or callable(bindings.discard_queued))
    )


class BindingsOwner:
    """Backend loop only."""

    def __init__(
        self, close_timeout: float, consume: Callable[[asyncio.Future[Any]], None]
    ) -> None:
        self._close_timeout = close_timeout
        self._consume = consume
        self._current: BackendBindings | None = None

    @property
    def current(self) -> BackendBindings | None:
        return self._current

    def adopt(self, boot: asyncio.Task[BackendBindings]) -> bool:
        """Claim a completed result before any path can close its resources."""

        bindings = boot.result()
        if type(bindings) is not BackendBindings:
            return False
        self._current = bindings
        return True

    async def discard(self, boot: asyncio.Task[BackendBindings]) -> bool:
        """Reclaim a late success within the bounded startup cleanup window."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._close_timeout
        if not boot.done():
            boot.cancel()
            await asyncio.wait({boot}, timeout=self._close_timeout)
        if not boot.done():
            # The bootstrap still owns anything it created. Its own finally
            # must clean partial resources; this bridge cannot confirm that.
            return False
        if boot.cancelled():
            return True
        if boot.exception() is not None:
            return not isinstance(boot.exception(), BackendCleanupError)
        if not self.adopt(boot):
            return False
        return await self.close(limit_s=max(0.0, deadline - loop.time()))

    async def close(self, *, limit_s: float | None = None) -> bool:
        """Release the bindings; True only when their close returned in time."""
        bindings = self._current
        self._current = None
        if bindings is None or bindings.close is None:
            return True
        if not callable(bindings.close):
            return False

        close = bindings.close

        async def invoke_close() -> None:
            await close()

        cleanup = asyncio.create_task(invoke_close())
        cleanup.add_done_callback(self._consume)
        done, _ = await asyncio.wait(
            {cleanup}, timeout=self._close_timeout if limit_s is None else limit_s
        )
        if cleanup not in done:
            cleanup.cancel()
            return False
        return not cleanup.cancelled() and cleanup.exception() is None
