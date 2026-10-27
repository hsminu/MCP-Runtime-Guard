"""Whether the session runtime takes new sessions (10.4).

Every refusal is one fixed code from trusted A code. Once set, none is lifted
until the app restarts.
"""

from __future__ import annotations

import asyncio

from guard_app.services.bridge import SessionStartError


class SessionRuntimeError(SessionStartError):
    pass


class SessionAdmission:
    """The run-wide refusals, in the order the screen reports them.

    Shutdown, a cleanup that could not be proven, and a saved policy that Guard
    did not apply (A104) each refuse every later session.
    """

    def __init__(self) -> None:
        self._closed = False
        self._cleanup_unconfirmed = False
        self._restart_code: str | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def cleanup_unconfirmed(self) -> bool:
        return self._cleanup_unconfirmed

    @property
    def restart_code(self) -> str | None:
        return self._restart_code

    def close(self) -> None:
        self._closed = True

    def mark_cleanup_unconfirmed(self) -> None:
        self._cleanup_unconfirmed = True

    def require_restart(self, code: str) -> None:
        self._restart_code = code

    def refusal(self) -> str | None:
        if self._closed:
            return "SESSION_UNAVAILABLE"
        if self._cleanup_unconfirmed:
            return "CODEX_CLEANUP_UNCONFIRMED"
        return self._restart_code

    def check(self) -> None:
        """Raise the refusal. A creator that is being cancelled is refused too."""
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        refusal = self.refusal()
        if refusal is not None:
            raise SessionRuntimeError(refusal)
