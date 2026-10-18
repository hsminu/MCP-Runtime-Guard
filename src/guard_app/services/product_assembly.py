"""A composition of B's Profile loading, A's transport and access rules into Guard.

Every input here is trusted and supplied by the composition root: the parsed
Profile document (file format B3.11, path validation C), C's Registry, per-server
stream factories from C's launcher, and durable `next_call_seq`/`failure_audit`
(B-SQL-04 / C). Nothing is read from disk, launched or retried here.

Right after `tools/list` the live definitions are compared with the registered
fingerprints (A15/A16, FP-01 via B's `tool_fingerprint`). A mismatching or
missing tool is reported through the Registry view handed to Catalog/Guard, so
Guard denies it with TOOL_FINGERPRINT_CHANGED and it is left out of the model's
tool list. Moving the registration back to PENDING is C's Registry path (I10).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from uuid import uuid4

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.mcp_transport import McpTransport, StreamFactory, ToolListLimits
from guard_app.domain.contracts import RegisteredServer, ToolView
from guard_app.domain.enums import RegStatus
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ToolSpec
from guard_app.ports import Registry, Store
from guard_app.security.digest import JcsCanonicalizationError, tool_fingerprint
from guard_app.services.catalog import (
    CatalogConfigError,
    GuardSettings,
    LoadedProfile,
    ResourceResolver,
    load_profile_settings,
)
from guard_app.services.guard import FailureEvent, GuardService

# Never equal to a SHA-256 hex digest, so Catalog/Guard see a changed fingerprint.
_LIVE_MISMATCH = "live-definition-mismatch"


class ProductAssemblyError(RuntimeError):
    """Fixed code only; never a path, document value or exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ProductInputs:
    profile_document: Mapping[str, object]
    registry: Registry
    server_streams: Mapping[str, StreamFactory]
    next_call_seq: Callable[[str], Awaitable[int]]
    failure_audit: Callable[[FailureEvent], Awaitable[None]]
    operator_id: str
    resource_resolver: ResourceResolver | None = None
    tool_list_limits: ToolListLimits = field(default_factory=ToolListLimits)
    call_timeout_s: float = 30.0


class LiveCheckedRegistry:
    """C's Registry as seen through this run's live tools/list comparison."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._mismatched: frozenset[tuple[str, str]] = frozenset()
        self._sealed = False

    def seal(self, mismatched: frozenset[tuple[str, str]]) -> None:
        if self._sealed:
            raise RuntimeError("live fingerprint view already sealed")
        self._mismatched = mismatched
        self._sealed = True

    @property
    def mismatched(self) -> frozenset[tuple[str, str]]:
        return self._mismatched

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self._registry.registration(server_id)

    def status(self, server_id: str) -> RegStatus | None:
        return self._registry.status(server_id)

    def fingerprint(self, tool: ToolSpec) -> str | None:
        if not self._sealed:
            return None  # fail closed before the live comparison has finished
        if (tool.server_id, tool.tool_name) in self._mismatched:
            return _LIVE_MISMATCH
        return self._registry.fingerprint(tool)


def live_fingerprint_mismatches(
    registry: Registry, transport: McpTransport, server_ids: tuple[str, ...]
) -> frozenset[tuple[str, str]]:
    """Registered tools whose live definition is absent, incomplete or different."""

    mismatched: set[tuple[str, str]] = set()
    for server_id in server_ids:
        record = registry.registration(server_id)
        if record is None:
            continue
        listing = transport.tool_listing(server_id)
        live: dict[str, FrozenJsonObject] = {}
        if listing is not None and listing.complete:
            for listed in listing.tools:
                name = listed.get("name")
                if type(name) is str:
                    live[name] = listed
        for tool in record.tools:
            definition = live.get(tool.tool_name)
            try:
                matches = definition is not None and (
                    tool_fingerprint(definition.to_dict()) == tool.fingerprint
                )
            except JcsCanonicalizationError:
                matches = False
            if not matches:
                mismatched.add((server_id, tool.tool_name))
    return frozenset(mismatched)


@dataclass(slots=True, repr=False)
class AssembledGuard:
    loaded: LoadedProfile
    guard: GuardService
    transport: McpTransport
    registry: LiveCheckedRegistry
    tool_views: tuple[ToolView, ...]
    operator_id: str
    call_timeout_s: float = 30.0

    @property
    def settings(self) -> GuardSettings:
        return self.loaded.guard_settings

    async def aclose(self) -> None:
        await self.transport.close()


async def assemble_guard(store: Store, inputs: ProductInputs) -> AssembledGuard:
    """Load the Profile, start only its servers, compare fingerprints, build Guard."""

    if type(inputs) is not ProductInputs:
        raise ProductAssemblyError("PRODUCT_INPUTS_INVALID")
    registry = LiveCheckedRegistry(inputs.registry)
    try:
        loaded = load_profile_settings(
            inputs.profile_document, registry, resource_resolver=inputs.resource_resolver
        )
    except CatalogConfigError:
        raise ProductAssemblyError("PROFILE_INVALID") from None
    server_ids = loaded.profile.server_ids
    if any(server_id not in inputs.server_streams for server_id in server_ids):
        raise ProductAssemblyError("PROFILE_SERVER_UNAVAILABLE")
    transport = McpTransport(
        {server_id: inputs.server_streams[server_id] for server_id in server_ids},
        call_timeout_s=inputs.call_timeout_s,
        tool_list_limits=inputs.tool_list_limits,
    )
    adopted = False
    try:
        await transport.start()
        registry.seal(live_fingerprint_mismatches(inputs.registry, transport, server_ids))
        settings = loaded.guard_settings
        guard = GuardService(
            store,
            registry,
            loaded.catalog,
            transport,
            AccessRulesChecker(loaded.access_rules),
            profile_id=settings.profile_id,
            policy_version=settings.policy_version,
            operator_id=inputs.operator_id,
            request_id_factory=lambda: f"request-{uuid4().hex}",
            approval_id_factory=lambda: f"approval-{uuid4().hex}",
            next_call_seq=inputs.next_call_seq,
            failure_audit=inputs.failure_audit,
            monotonic=time.monotonic,
            approval_ttl_seconds=settings.approval_ttl_seconds,
            taint_mode=settings.taint_mode,
        )
        assembled = AssembledGuard(
            loaded,
            guard,
            transport,
            registry,
            loaded.catalog.tool_views(server_ids),
            inputs.operator_id,
            inputs.call_timeout_s,
        )
        adopted = True
        return assembled
    finally:
        if not adopted:
            await transport.close()
