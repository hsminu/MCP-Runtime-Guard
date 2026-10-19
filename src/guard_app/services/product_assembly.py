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
    """C's Registry as seen through this run's live tools/list comparison.

    A server whose live listing is incomplete or differs from its registration
    in any tool is treated as PENDING for this run, so none of its tools run
    (I10; 9.1/10.1: a fingerprint change sends the registration back to
    PENDING). For other servers the fingerprint reported to Catalog/Guard is
    the live value, so a registration rewritten during the run no longer
    matches the definitions this session actually received.
    """

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._live: dict[tuple[str, str], str] = {}
        self._blocked: frozenset[str] = frozenset()
        self._mismatched: frozenset[tuple[str, str]] = frozenset()
        self._sealed = False

    def seal(self, view: LiveFingerprints) -> None:
        if self._sealed:
            raise RuntimeError("live fingerprint view already sealed")
        self._live = dict(view.live)
        self._blocked = view.blocked_servers
        self._mismatched = view.mismatched
        self._sealed = True

    @property
    def mismatched(self) -> frozenset[tuple[str, str]]:
        return self._mismatched

    @property
    def blocked_servers(self) -> frozenset[str]:
        return self._blocked

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self._registry.registration(server_id)

    def status(self, server_id: str) -> RegStatus | None:
        if self._sealed and server_id in self._blocked:
            return RegStatus.PENDING
        return self._registry.status(server_id)

    def fingerprint(self, tool: ToolSpec) -> str | None:
        if not self._sealed:
            return None  # fail closed before the live comparison has finished
        if tool.server_id in self._blocked:
            return _LIVE_MISMATCH
        return self._live.get((tool.server_id, tool.tool_name))


@dataclass(frozen=True, slots=True)
class LiveFingerprints:
    live: dict[tuple[str, str], str]
    mismatched: frozenset[tuple[str, str]]
    blocked_servers: frozenset[str]


def live_fingerprints(
    registry: Registry, transport: McpTransport, server_ids: tuple[str, ...]
) -> LiveFingerprints:
    """Live fingerprints per registered tool, and servers that must not run."""

    live: dict[tuple[str, str], str] = {}
    mismatched: set[tuple[str, str]] = set()
    blocked: set[str] = set()
    for server_id in server_ids:
        record = registry.registration(server_id)
        if record is None:
            continue
        listing = transport.tool_listing(server_id)
        definitions: dict[str, FrozenJsonObject] = {}
        if listing is not None and listing.complete:
            for listed in listing.tools:
                name = listed.get("name")
                if type(name) is str:
                    definitions[name] = listed
        else:
            blocked.add(server_id)
        for tool in record.tools:
            definition = definitions.get(tool.tool_name)
            value: str | None = None
            if definition is not None:
                try:
                    value = tool_fingerprint(definition.to_dict())
                except JcsCanonicalizationError:
                    value = None
            if value is None or value != tool.fingerprint:
                mismatched.add((server_id, tool.tool_name))
                blocked.add(server_id)
            else:
                live[(server_id, tool.tool_name)] = value
    return LiveFingerprints(live, frozenset(mismatched), frozenset(blocked))


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
        # NOTIFY-01: end transport work first so reserved calls settle, then Guard
        # refuses new approvals and drops unconsumed released text.
        try:
            await self.transport.close()
        finally:
            await self.guard.aclose()


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
        registry.seal(live_fingerprints(inputs.registry, transport, server_ids))
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
        try:
            tool_views = loaded.catalog.tool_views(server_ids)
        except CatalogConfigError:
            # One unsupported tool schema must not surface as a raw error.
            raise ProductAssemblyError("PROFILE_TOOL_INVALID") from None
        assembled = AssembledGuard(
            loaded,
            guard,
            transport,
            registry,
            tool_views,
            inputs.operator_id,
            inputs.call_timeout_s,
        )
        adopted = True
        return assembled
    finally:
        if not adopted:
            await transport.close()
