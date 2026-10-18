"""Per-Task Codex session factory composed from A's Guard assembly (A88).

`TaskResources.handle` is what C's launcher hands over for one fresh app-server
(C2.12): it must offer `read_bytes`/`write_bytes`/`drain`. Anything else fails
closed before a session exists. The approval watchdog follows the Profile's
`approval_ttl_seconds` plus the MCP call bound and a margin (A80).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from importlib import metadata

from guard_app.adapters.codex_protocol import build_dynamic_tools
from guard_app.domain.models import TaskState
from guard_app.services.bridge import SessionOptions, SessionStartError
from guard_app.services.codex_prompt import BASE_INSTRUCTIONS
from guard_app.services.codex_session import CodexAppServerSession, CodexPipes, SessionLimits
from guard_app.services.guard_relay import ApprovalOutcomes, GuardToolRelay, TrustedTask
from guard_app.services.product_assembly import AssembledGuard
from guard_app.services.runner import CodexSession, CodexSessionError
from guard_app.services.session_preparation import TaskResources

WATCHDOG_MARGIN_S = 30.0


def app_version() -> str:
    try:
        return metadata.version("mcp-runtime-guard")
    except metadata.PackageNotFoundError:
        return "0.0.0+source"


def watchdog_seconds(assembled: AssembledGuard) -> float:
    return assembled.settings.approval_ttl_seconds + assembled.call_timeout_s + WATCHDOG_MARGIN_S


def codex_session_factory(
    assembled: AssembledGuard,
    approvals: ApprovalOutcomes,
    *,
    limits: SessionLimits | None = None,
) -> Callable[[TaskState, SessionOptions, TaskResources], Awaitable[CodexSession]]:
    plan = build_dynamic_tools(assembled.tool_views)
    bounded = replace(limits or SessionLimits(), call_watchdog_s=watchdog_seconds(assembled))
    version = app_version()

    async def factory(
        state: TaskState, options: SessionOptions, resources: TaskResources
    ) -> CodexSession:
        pipes = resources.handle
        if not isinstance(pipes, CodexPipes):
            raise SessionStartError("CODEX_SESSION_INVALID")
        relay = GuardToolRelay(TrustedTask(state.task_id, state.epoch), assembled.guard, approvals)
        session = CodexAppServerSession(
            pipes,
            task=TrustedTask(state.task_id, state.epoch),
            plan=plan,
            relay=relay.relay,
            model=options.model,
            base_instructions=BASE_INSTRUCTIONS,
            version=version,
            limits=bounded,
        )
        try:
            await session.start()
        except CodexSessionError:
            await session.aclose()
            raise SessionStartError("CODEX_START_FAILED") from None
        return session

    return factory
