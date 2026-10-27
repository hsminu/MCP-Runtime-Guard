"""C's Codex preparation as the session runtime calls it (10.4, A101).

The app-wide startup receipt with its L2 evidence is taken once. Each Task then
rechecks the configuration against it before anything is launched. A callback
fault becomes CODEX_PREPARATION_FAILED without its text, and cancellation is
never swallowed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
    valid_configuration,
)


async def call_preparation[Result](action: Callable[[], Awaitable[Result]]) -> Result:
    """Translate callback faults without swallowing cancellation or raw text."""

    async def invoke() -> Result:
        return await action()

    operation = asyncio.create_task(invoke())
    try:
        return await operation
    finally:
        if operation.done() and not operation.cancelled() and operation.exception() is not None:
            raise SessionRuntimeError("CODEX_PREPARATION_FAILED") from None


def require_configuration(actual: object, expected: ConfigurationReceipt) -> None:
    """A Task's configuration must be well formed and the one startup checked."""
    if not valid_configuration(actual):
        raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
    if actual != expected:
        raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")


def startup_refusal(receipt: object) -> str | None:
    """Why an app-wide startup receipt refuses sessions; None when it passes."""
    if type(receipt) is not StartupReceipt or not valid_configuration(receipt.configuration):
        return "CODEX_PREPARATION_FAILED"
    if receipt.l2_configuration is None:
        return "CODEX_L2_UNAVAILABLE"
    if receipt.l2_passed is not True:
        return "CODEX_L2_FAILED"
    if not valid_configuration(receipt.l2_configuration):
        return "CODEX_PREPARATION_FAILED"
    if receipt.configuration != receipt.l2_configuration:
        return "CODEX_CONFIG_MISMATCH"
    return None


class StartupGate:
    """Runs C's app-wide preparation once and checks each Task against it.

    The first refusal stays the session blocker with its own code and answers
    every later try; startup is not retried within one run.
    """

    def __init__(self, preparation: SessionPreparation | None, admission: SessionAdmission) -> None:
        self._preparation = preparation
        self._admission = admission
        self._lock = asyncio.Lock()
        self._configuration: ConfigurationReceipt | None = None
        self._attempted = False
        self._code = "CODEX_PREPARATION_FAILED"

    def blocker(self) -> str | None:
        if type(self._preparation) is not SessionPreparation:
            return "CODEX_CONFIG_UNAVAILABLE"
        if self._attempted and self._configuration is None:
            return self._code
        return None

    async def check(self) -> ConfigurationReceipt:
        """The configuration startup confirmed; startup runs on the first call."""
        async with self._lock:
            self._admission.check()
            preparation = self._callbacks()
            if self._configuration is None:
                if self._attempted:
                    raise SessionRuntimeError(self._code)
                self._attempted = True
                receipt = await call_preparation(preparation.prepare_startup)
                self._admission.check()
                refusal = startup_refusal(receipt)
                if refusal is not None:
                    self._code = refusal
                    raise SessionRuntimeError(refusal)
                self._configuration = receipt.configuration
            return self._configuration

    async def verify(self, expected: ConfigurationReceipt) -> None:
        preparation = self._callbacks()
        actual = await call_preparation(lambda: preparation.verify_configuration(expected))
        self._admission.check()
        require_configuration(actual, expected)

    async def prepare_task(self, task_id: str, expected: ConfigurationReceipt) -> TaskResources:
        """C's receipt for the Task's resources, still to be checked before it is owned."""
        preparation = self._callbacks()
        return await call_preparation(lambda: preparation.prepare_task(task_id, expected))

    def _callbacks(self) -> SessionPreparation:
        preparation = self._preparation
        if type(preparation) is not SessionPreparation or not all(
            callable(callback)
            for callback in (
                preparation.prepare_startup,
                preparation.verify_configuration,
                preparation.prepare_task,
                preparation.confirm_exit,
                preparation.cleanup_task,
            )
        ):
            raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
        return preparation
