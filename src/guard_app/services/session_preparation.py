"""A-local injection receipts; these are not agreed B/C platform contracts.

C's eventual adapters supply observations and all process/path operations. A
only compares receipts and orders callbacks. A successful resource preparation
transfers ownership of its opaque handle; before returning a receipt (including
on failure/cancellation), the preparation callback owns partial-resource cleanup.
``confirm_exit`` must confirm absence/termination of this receipt's process even
when session creation failed without returning a session. No confirmation means
no folder deletion. It is bounded by ``CONFIRM_EXIT_TIMEOUT_S``, not the shorter
cleanup bound, so the launcher's own grace and kill waits fit (C2.12). Opaque
handles must be unique across live Task resources.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ConfigurationReceipt:
    config_digest: str
    executable_digest: str
    model_digest: str


@dataclass(frozen=True, slots=True)
class StartupReceipt:
    configuration: ConfigurationReceipt
    l2_configuration: ConfigurationReceipt | None
    l2_passed: bool


@dataclass(frozen=True, slots=True)
class TaskResources:
    task_id: str
    configuration: ConfigurationReceipt
    owner_token: str = field(repr=False)
    handle: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class SessionPreparation:
    prepare_startup: Callable[[], Awaitable[StartupReceipt]]
    verify_configuration: Callable[[ConfigurationReceipt], Awaitable[ConfigurationReceipt]]
    prepare_task: Callable[[str, ConfigurationReceipt], Awaitable[TaskResources]]
    confirm_exit: Callable[[TaskResources], Awaitable[bool]]
    cleanup_task: Callable[[TaskResources], Awaitable[None]]


def valid_configuration(receipt: object) -> bool:
    return type(receipt) is ConfigurationReceipt and all(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in (receipt.config_digest, receipt.executable_digest, receipt.model_digest)
    )
