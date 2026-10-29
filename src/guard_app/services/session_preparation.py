"""A-local injection receipts; these are not agreed B/C platform contracts.

C's eventual adapters supply observations and all process/path operations. A
only compares receipts and orders callbacks. A successful resource preparation
transfers ownership of its opaque handle; before returning a receipt (including
on failure/cancellation), the preparation callback owns partial-resource cleanup.
``confirm_exit`` must confirm absence/termination of this receipt's process even
when session creation failed without returning a session. No confirmation means
no folder deletion. It is bounded by ``CleanupLimits.confirm_exit_s``, not the
shorter session bound, so the launcher's own grace and kill waits fit (C2.12).
``cleanup_task`` is bounded by ``CleanupLimits.task_folder_s`` because the folder is
deleted in a worker thread (A103). Opaque handles must be unique across live
Task resources.
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
    l2_failures: tuple[str, ...] = ()


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


# C6.14 fixed diagnostics; unknown codes fail closed without echo.
L2_FAILURE_CODES = frozenset(
    {
        "L2_CODEX_EXITED",
        "L2_CONFIG_LAYERS_UNEXPECTED",
        "L2_CONFIG_MISMATCH",
        "L2_CONFIG_UNREADABLE",
        "L2_DEADLINE_EXCEEDED",
        "L2_DEADLINE_INVALID",
        "L2_EXIT_UNCONFIRMED",
        "L2_FEATURE_ENABLED",
        "L2_FOLDER_NOT_REMOVED",
        "L2_FORBIDDEN_NOT_REFUSED",
        "L2_ITEM_UNEXPECTED",
        "L2_MODEL_REQUEST_MISSING",
        "L2_MODEL_TRAFFIC_UNEXPECTED",
        "L2_PROBE_CALLS_UNEXPECTED",
        "L2_PROMPT_UNEXPECTED",
        "L2_PROTOCOL_INVALID",
        "L2_READER_STUCK",
        "L2_REMOTE_CONTROL_ENABLED",
        "L2_REQUEST_REJECTED",
        "L2_REQUEST_TIMEOUT",
        "L2_REQUIREMENTS_PRESENT",
        "L2_SERVER_REQUEST_UNEXPECTED",
        "L2_SESSION_FLAGS_UNEXPECTED",
        "L2_THREAD_PARAMS_UNSAFE",
        "L2_THREAD_START_INVALID",
        "L2_TOOLS_UNEXPECTED",
        "L2_TURN_NOT_COMPLETED",
        "L2_TURN_TIMEOUT",
    }
)
