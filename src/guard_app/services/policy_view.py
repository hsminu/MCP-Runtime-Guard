"""Read-only policy view for the GUI (A91; #19 4번 화면은 A, 변경 명령은 B3.10).

Built once from the loaded Profile and C's registration records. It carries no
document text, paths, URLs or arguments: only identifiers, kinds and rule pairs.
Changing rules is B's policy command (B3.10); until it exists the view says so
and the GUI cannot save.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from guard_app.core.access_rules import normalize_access_rules
from guard_app.domain.enums import ToolKind
from guard_app.services.product_assembly import AssembledGuard

type AccessRuleSaver = Callable[[str, tuple[str, ...], tuple[str, ...]], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class PolicyTool:
    name: str
    kind: str
    kind_fixed: bool  # READ/SEND are checked by default at activation (PR #26)


@dataclass(frozen=True, slots=True)
class PolicyServer:
    server_id: str
    tools: tuple[PolicyTool, ...]


@dataclass(frozen=True, slots=True)
class PolicyView:
    profile_id: str
    policy_version: str
    approval_ttl_seconds: int
    taint_mode: str
    allow: tuple[tuple[str, str], ...]
    deny: tuple[tuple[str, str], ...]
    servers: tuple[PolicyServer, ...]
    change_available: bool = False


def policy_view(assembled: AssembledGuard, *, change_available: bool = False) -> PolicyView:
    loaded = assembled.loaded
    rules = normalize_access_rules(loaded.access_rules)
    servers = []
    for server_id in loaded.profile.server_ids:
        record = assembled.registry.registration(server_id)
        tools = () if record is None else record.tools
        servers.append(
            PolicyServer(
                server_id,
                tuple(
                    PolicyTool(
                        tool.tool_name,
                        tool.kind.value,
                        tool.kind in (ToolKind.READ, ToolKind.SEND),
                    )
                    for tool in tools
                ),
            )
        )
    settings = loaded.guard_settings
    return PolicyView(
        profile_id=settings.profile_id,
        policy_version=settings.policy_version,
        approval_ttl_seconds=settings.approval_ttl_seconds,
        taint_mode=settings.taint_mode,
        allow=tuple((pair[0], pair[1]) for pair in rules["allow"]),
        deny=tuple((pair[0], pair[1]) for pair in rules["deny"]),
        servers=tuple(servers),
        change_available=change_available,
    )
