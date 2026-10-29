"""A composition of B's Profile loading, A's transport and access rules into Guard.

Every input here is trusted and supplied by the composition root: the parsed
Profile document (B3.11, path validation C), C's Registry, app folders for C's
launcher, and durable `next_call_seq`/`failure_audit` (B-SQL-04 / C). Explicit
stream injection is for tests. The stdio context owns launch and confirmed
shutdown; calls are never retried.

Right after `tools/list` the live definitions are compared with the registered
fingerprints (A15/A16, FP-01 via B's `tool_fingerprint`). A mismatching or
missing tool is reported through the Registry view handed to Catalog/Guard, so
Guard denies it with TOOL_FINGERPRINT_CHANGED and it is left out of the model's
tool list. Moving the registration back to PENDING is C's Registry path (I10):
when the Registry is C's `ServerRegistry`, each selected server's listing is
also handed to its `observe`, which records a change durably. A server whose
listing it cannot record or does not accept is blocked for this run too.

A `notifications/tools/list_changed` received after that server's first listing
response makes its
server PENDING for the rest of this run (LIST-01): new calls are denied with
REG_NOT_ACTIVE, pending approvals fail their recheck, and new Codex sessions do
not list its tools. Nothing is written to the Registry; the next connection
compares again. Only notices before that server's first listing response are
nonblocking. Waiting for another server or recording the comparison must not
discard a notice that makes an already received definition stale (A140).

A server whose session stops serving during the run (an HTTP request error, a
broken stream, a call past its watchdog) is PENDING for the rest of the run in
the same way (#61). There is no reconnection during a run, so later calls are
denied before they are reserved instead of each locking its Task with an
unknown outcome. A call already reserved when it failed keeps its outcome.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.mcp_transport import McpTransport, StreamFactory, ToolListLimits
from guard_app.adapters.mcp_transport.launcher_streams import launcher_stdio_stream_factory
from guard_app.adapters.mcp_transport.streams import http_stream_factory
from guard_app.domain.contracts import RegisteredServer, ToolView
from guard_app.domain.enums import RegStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ToolSpec
from guard_app.ports import Registry, Store
from guard_app.security.digest import JcsCanonicalizationError, tool_fingerprint
from guard_app.security.endpoints import EndpointError, normalize_endpoint
from guard_app.security.ids import new_id
from guard_app.security.launcher import ExecutablePin
from guard_app.security.paths import AppFolders, file_resource_resolver
from guard_app.servers.file_server import file_server_root
from guard_app.services.catalog import (
    CatalogConfigError,
    GuardSettings,
    LoadedProfile,
    ResourceResolver,
    ServerSettings,
    load_profile_settings,
)
from guard_app.services.guard import FailureEvent, GuardService
from guard_app.services.registry import RegistryError, ServerRegistry, ToolListingView

# Never equal to a SHA-256 hex digest, so Catalog/Guard see a changed fingerprint.
_LIVE_MISMATCH = "live-definition-mismatch"


class ProductAssemblyError(RuntimeError):
    """Fixed code only; never a path, document value or exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ProductInputs:
    """Trusted composition inputs. Stream injection is test/demo-only without folders."""

    profile_document: Mapping[str, object]
    registry: Registry
    next_call_seq: Callable[[str], Awaitable[int]]
    failure_audit: Callable[[FailureEvent], Awaitable[None]]
    operator_id: str
    # Explicit injection for unit/demo composition only. Product uses folders.
    server_streams: Mapping[str, StreamFactory] | None = None
    folders: AppFolders | None = None
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

    A server whose tool list changed after a listing response is PENDING too
    (LIST-01), and so is one whose session stopped serving (#61). Their
    fingerprints stay the live values, so REG_NOT_ACTIVE is the only reason
    Guard reports for them.
    """

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._live: dict[tuple[str, str], str] = {}
        self._blocked: frozenset[str] = frozenset()
        self._mismatched: frozenset[tuple[str, str]] = frozenset()
        self._changed: set[str] = set()
        self._received: set[str] = set()
        self._failed: set[str] = set()
        self._early_changes = 0
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
        return self._blocked | self._changed | self._failed

    @property
    def listing_changed(self) -> frozenset[str]:
        return frozenset(self._changed)

    @property
    def unavailable(self) -> frozenset[str]:
        """Servers that became PENDING during the run: no new session lists their tools."""
        return frozenset(self._changed | self._failed)

    @property
    def early_list_changes(self) -> int:
        return self._early_changes

    def tools_changed(self, server_id: str) -> None:
        """Record only the fact; the notification's params never reach here."""

        if not self._sealed and server_id not in self._received:
            self._early_changes += 1
            return
        self._changed.add(server_id)

    def listing_received(self, server_id: str) -> None:
        """A transport-owned request received a page; no definition becomes trusted here."""
        self._received.add(server_id)

    def server_failed(self, server_id: str) -> None:
        """Only the fact that the server's session stopped serving (#61); no error text."""

        self._failed.add(server_id)

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self._registry.registration(server_id)

    def status(self, server_id: str) -> RegStatus | None:
        if (
            server_id in self._failed
            or server_id in self._changed
            or (self._sealed and server_id in self._blocked)
        ):
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
    """Live fingerprints per registered tool, and servers that must not run.

    A server is blocked for this run when its listing is incomplete, a
    registered tool is missing or differs, or it lists a tool its registration
    does not have. Added and removed tools both count as a definition change,
    like B's `RegistryStore.record_fingerprint_change` (PR #29 B review 3).
    """

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
                if type(name) is not str or name in definitions:
                    # A nameless or repeated tool cannot be matched to one
                    # registration, so the listing is not trusted at all.
                    blocked.add(server_id)
                    continue
                definitions[name] = listed
        else:
            blocked.add(server_id)
        registered = {tool.tool_name for tool in record.tools}
        if any(name not in registered for name in definitions):
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


async def _record_listings(
    registry: Registry, servers: tuple[ServerSettings, ...], transport: McpTransport
) -> frozenset[str]:
    """C4.10: record each registered server's live listing; the servers that failed.

    No Guard exists yet, so there are no pending approvals to invalidate.
    """
    if not isinstance(registry, ServerRegistry):
        return frozenset()
    failed: set[str] = set()
    for server in servers:
        if registry.registration(server.id) is None:
            continue  # not registered: Guard denies its tools anyway
        listing = transport.tool_listing(server.id)
        if listing is None:
            failed.add(server.id)
            continue
        try:
            await registry.observe(server, listing)
        except (RegistryError, StoreError):
            # Incomplete listing, settings that differ from the registration,
            # or a write that failed: ServerRegistry keeps it PENDING as well.
            failed.add(server.id)
    return frozenset(failed)


@dataclass(slots=True, repr=False)
class AssembledGuard:
    loaded: LoadedProfile
    guard: GuardService
    transport: McpTransport
    registry: LiveCheckedRegistry
    tool_views: tuple[ToolView, ...]
    operator_id: str
    call_timeout_s: float = 30.0

    resource_resolver: ResourceResolver | None = None

    @property
    def settings(self) -> GuardSettings:
        return self.loaded.guard_settings

    def offers_tool(self, tool_ref: str) -> bool:
        """False once the tool's server changed its tool list (LIST-01) or
        stopped serving (#61) this run.

        Server IDs never contain "__", so the namespace prefix is the server ID.
        """

        return tool_ref.partition("__")[0] not in self.registry.unavailable

    def offered_tool_views(self) -> tuple[ToolView, ...]:
        """Tools a new Codex session may list."""

        return tuple(view for view in self.tool_views if self.offers_tool(view.tool_ref))

    async def aclose(self, *, settle: Callable[[], Awaitable[object]] | None = None) -> None:
        # NOTIFY-01: end transport work first so reserved calls settle, then Guard
        # refuses new approvals and drops unconsumed released text. `settle` runs
        # in between, while Guard still records (a session stop, STOP-01).
        try:
            await self.transport.close()
        finally:
            try:
                if settle is not None:
                    await settle()
            finally:
                await self.guard.aclose()


def _server_streams(loaded: LoadedProfile, inputs: ProductInputs) -> Mapping[str, StreamFactory]:
    """Product folders and test/demo stream injection are mutually exclusive.

    Only startable servers get a stream: one whose stored URL B's Profile check
    refuses is not started at all (#86).
    """
    if inputs.folders is not None and inputs.server_streams is not None:
        raise ProductAssemblyError("PRODUCT_INPUTS_INVALID")
    if inputs.folders is None:
        if inputs.server_streams is None:
            raise ProductAssemblyError("PROFILE_SERVER_UNAVAILABLE")
        return inputs.server_streams
    streams: dict[str, StreamFactory] = {}
    for server in loaded.startable_servers:
        if server.transport == "stdio":
            pin = (
                ExecutablePin(server.command_size, server.command_sha256)
                if server.command_size is not None and server.command_sha256 is not None
                else None
            )
            streams[server.id] = launcher_stdio_stream_factory(
                inputs.folders.server_folder(server.id),
                server.command,
                server.args,
                server.env_allow,
                pin,
            )
        else:
            if server.url is None:
                raise ProductAssemblyError("PROFILE_SERVER_UNAVAILABLE")
            try:
                endpoint = normalize_endpoint(server.url).url
            except EndpointError as error:
                code = error.args[0] if error.args else "URL_INVALID"
                streams[server.id] = partial(_RefusedStream, code)
                continue
            streams[server.id] = http_stream_factory(
                endpoint, read_timeout_s=max(60.0, inputs.call_timeout_s)
            )
    return streams


class _RefusedStream(AbstractAsyncContextManager[tuple[Any, ...]]):
    """A stored URL that C2.5 refuses (a hand-edited settings file, #83 C review 3.2).

    startable_servers already leaves such a server out (#86); this stays in case
    B's canonical check and C's normalization ever disagree. The connection
    fails on entry, so the product transport isolates this server like a stdio
    start failure instead of failing the whole assembly.
    """

    def __init__(self, code: str) -> None:
        self._code = code

    async def __aenter__(self) -> tuple[Any, ...]:
        raise EndpointError(self._code)

    async def __aexit__(self, *_: object) -> None:
        return None


def _file_server_root(server: ServerSettings) -> Path | None:
    # #94: match registration's Python; EXE-01 checks its fingerprint on every launch.
    if (
        server.transport != "stdio"
        or server.command is None
        or os.path.normcase(server.command) != os.path.normcase(sys.executable)
    ):
        return None
    return file_server_root(server.args or ())


async def assemble_guard(store: Store, inputs: ProductInputs) -> AssembledGuard:
    """Load the Profile, start only its servers, compare fingerprints, build Guard."""

    if type(inputs) is not ProductInputs:
        raise ProductAssemblyError("PRODUCT_INPUTS_INVALID")
    registry = LiveCheckedRegistry(inputs.registry)
    try:
        configured = load_profile_settings(inputs.profile_document, registry)
        roots = {
            server.id: root
            for server in configured.servers
            if (root := _file_server_root(server)) is not None
        }
        if roots:
            file_resolver = file_resource_resolver(roots)
            fallback = inputs.resource_resolver

            def resolve(tool: ToolSpec, path: str) -> str:
                if tool.server_id in roots or fallback is None:
                    return file_resolver(tool, path)
                return fallback(tool, path)

            inputs = replace(inputs, resource_resolver=resolve)
        loaded = load_profile_settings(
            inputs.profile_document, registry, resource_resolver=inputs.resource_resolver
        )
    except CatalogConfigError:
        raise ProductAssemblyError("PROFILE_INVALID") from None
    startable = loaded.startable_servers
    server_ids = tuple(server.id for server in startable)
    streams = _server_streams(loaded, inputs)
    if any(server_id not in streams for server_id in server_ids):
        raise ProductAssemblyError("PROFILE_SERVER_UNAVAILABLE")
    # The product must reach the Store-wide halt; only an injected composition may
    # run without it (#83 B review 1). Refused before any server connection.
    global_halt = getattr(store, "halt_execution", None)
    if inputs.folders is not None and not callable(global_halt):
        raise ProductAssemblyError("GLOBAL_HALT_UNAVAILABLE")
    transport = McpTransport(
        {server_id: streams[server_id] for server_id in server_ids},
        call_timeout_s=inputs.call_timeout_s,
        tool_list_limits=inputs.tool_list_limits,
        on_tools_changed=registry.tools_changed,
        on_server_failed=registry.server_failed,
        on_listing_received=registry.listing_received,
        isolate_start_failures=inputs.folders is not None,
    )
    adopted = False
    try:
        await transport.start()
        # Compare first: observe rewrites the registration it compares against.
        live = live_fingerprints(inputs.registry, transport, server_ids)
        unrecorded = await _record_listings(inputs.registry, startable, transport)
        blocked = live.blocked_servers | unrecorded
        registry.seal(LiveFingerprints(live.live, live.mismatched, blocked))
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
            request_id_factory=lambda: new_id("request"),
            approval_id_factory=lambda: new_id("approval"),
            next_call_seq=inputs.next_call_seq,
            failure_audit=inputs.failure_audit,
            on_global_halt=global_halt,
            monotonic=time.monotonic,
            approval_ttl_seconds=settings.approval_ttl_seconds,
            taint_mode=settings.taint_mode,
        )
        try:
            # Settings that differ from the registration (an A155 kind edit not
            # observed yet, or another boundary) hide only that server (#86).
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
            inputs.resource_resolver,
        )
        adopted = True
        return assembled
    finally:
        if not adopted:
            await transport.close()


async def collect_registration_listing(
    server: ServerSettings, folders: AppFolders
) -> ToolListingView:
    """Collect definitions only; this path has no tools/call operation (I1)."""
    if server.transport == "stdio":
        pin = (
            ExecutablePin(server.command_size, server.command_sha256)
            if server.command_size is not None and server.command_sha256 is not None
            else None
        )
        stream = launcher_stdio_stream_factory(
            folders.server_folder(server.id), server.command, server.args, server.env_allow, pin
        )
    else:
        stream = http_stream_factory(normalize_endpoint(server.url).url)
    transport = McpTransport({server.id: stream}, tool_list_limits=ToolListLimits())
    try:
        await transport.start()
        listing = transport.tool_listing(server.id)
        if listing is None or not listing.complete:
            raise RegistryError("REG_LISTING_INCOMPLETE")
        return listing
    finally:
        await transport.close()
