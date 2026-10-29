"""Policy projection from the applied Profile, settings and registration records.

Only identifiers, boundary metadata, tool kinds and rule pairs cross to the UI.
The applied document also supplies registered but unselected destinations.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from guard_app.adapters.codex_protocol import build_dynamic_tools
from guard_app.core.access_rules import normalize_access_rules
from guard_app.domain.enums import ToolKind
from guard_app.domain.json_types import FrozenJsonObject
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
class PolicyDestination:
    destination_id: str
    tier: str
    max_label: str
    selected: bool


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
    # (server__tool, fixed reason code) left out of Codex by its name rules.
    codex_excluded: tuple[tuple[str, str], ...] = ()
    destinations: tuple[PolicyDestination, ...] = ()


def destination_rows(
    document: Mapping[str, object] | None, selected: tuple[str, ...]
) -> tuple[PolicyDestination, ...]:
    if document is None:
        return ()
    rows = FrozenJsonObject(document).to_dict().get("destinations", [])
    if not isinstance(rows, list):
        return ()
    result = []
    for row in rows:
        if isinstance(row, dict):
            identifier, tier, label = row.get("id"), row.get("tier"), row.get("max_label")
            if isinstance(identifier, str) and isinstance(tier, str) and isinstance(label, str):
                result.append(PolicyDestination(identifier, tier, label, identifier in selected))
    return tuple(result)


def policy_view(
    assembled: AssembledGuard,
    *,
    change_available: bool = False,
    document: Mapping[str, object] | None = None,
) -> PolicyView:
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
        destinations=destination_rows(document, loaded.profile.destination_ids),
        change_available=change_available,
        codex_excluded=tuple(
            (item.tool_ref, item.reason)
            for item in build_dynamic_tools(assembled.tool_views).exclusions
        ),
    )
