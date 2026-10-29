"""Human registration commands: B settings, C validation/Registry, no activation.

Confirmation tokens bind a backend-held settings snapshot to C's measured pin.
Only one outstanding confirmation is retained. A changed pin consumes it and
requires another human confirmation. Arguments never enter public projections.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError

from guard_app.domain.contracts import RegistryEntry
from guard_app.domain.enums import RegStatus, ToolKind
from guard_app.domain.errors import DispatchError, StoreError
from guard_app.ports import RegistryStore
from guard_app.security.endpoints import ENDPOINT_REJECT_CODES, EndpointError, normalize_endpoint
from guard_app.security.ids import new_id
from guard_app.security.launcher import LaunchError, check_stdio_values, pin_executable
from guard_app.security.paths import SafePathError, real_root, safe_path
from guard_app.servers.file_server import (
    FILE_SERVER_TOOL_SETTINGS,
    LIST_FOLDER,
    LIST_ROOT,
    READ_FILE,
    file_server_args,
    file_server_root,
)
from guard_app.services.bridge_types import RegistrationRequest, RegistrationRow, RegistrationView
from guard_app.services.catalog import CatalogConfigError, ServerSettings, load_profile_settings
from guard_app.services.policy_view import PolicyServer, PolicyTool, PolicyView
from guard_app.services.registry import (
    RegistryError,
    ServerRegistry,
    ToolListingView,
    listing_fingerprints,
)
from guard_app.services.session_admission import SessionRuntimeError
from guard_app.services.session_policy import TaskPolicy
from guard_app.services.session_tool_settings import tool_settings_rows

# The server never became ready and its teardown was confirmed (#83 C review 3.1).
# A teardown that could not be confirmed raises another DispatchError and stays
# unconfirmed.
_SERVER_START_FAILURES = frozenset({"SessionStartFailed", "SessionStartTimeout"})


def endpoint_input(value: str) -> tuple[str | None, str | None]:
    """Pure C validation for the form's hint; commands validate independently."""
    try:
        return normalize_endpoint(value).url, None
    except EndpointError as error:
        code = error.args[0] if error.args else None
        return None, code if code in ENDPOINT_REJECT_CODES else "URL_INVALID"


def file_registration_sources(request: RegistrationRequest) -> tuple[dict[str, object], ...]:
    """C-checked folder choices, converted to B source rules; no I/O in UI."""
    if (
        type(request.file_root) is not str
        or type(request.folder_labels) is not tuple
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(value) is not str for value in pair)
            for pair in request.folder_labels
        )
    ):
        raise SafePathError("PATH_INVALID")
    root = real_root(Path(request.file_root))
    if request.root_label not in (
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
    ) or request.list_root_label not in ("PUBLIC", "INTERNAL", "CONFIDENTIAL"):
        raise SafePathError("PATH_LABEL_INVALID")
    sources: list[dict[str, object]] = [
        {
            "server": request.server_id,
            "tool": LIST_ROOT,
            "prefix": [],
            "label": request.list_root_label,
        }
    ]
    rules: list[tuple[tuple[str, ...], str]] = [((), request.root_label)]
    seen: list[tuple[str, ...]] = []
    for folder, label in request.folder_labels:
        if label not in ("PUBLIC", "INTERNAL", "CONFIDENTIAL"):
            raise SafePathError("PATH_LABEL_INVALID")
        # relative_to is lexical only; all filesystem checks belong to C.
        try:
            relative = Path(folder).relative_to(root).as_posix()
        except ValueError:
            raise SafePathError("PATH_OUTSIDE_ROOT") from None
        checked = safe_path(root, relative)
        # real_root also requires a directory, not a regular file.
        checked = real_root(checked)
        parts = checked.relative_to(root).parts
        key = tuple(part.casefold() for part in parts)
        if any(key[: len(old)] == old or old[: len(key)] == key for old in seen):
            raise SafePathError("PATH_RULE_OVERLAP")
        seen.append(key)
        rules.append((parts, label))
    for parts, label in rules:
        for tool in (LIST_FOLDER, READ_FILE):
            sources.append(
                {"server": request.server_id, "tool": tool, "prefix": list(parts), "label": label}
            )
    return tuple(sources)


class RegistrationCommands:
    def __init__(
        self,
        registry: ServerRegistry,
        store: RegistryStore,
        policy: TaskPolicy,
        collect: Callable[[ServerSettings], Awaitable[ToolListingView]],
        stop_sessions: Callable[[tuple[str, ...]], Awaitable[None]],
        execution_check: Callable[[], None] = lambda: None,
        *,
        operator_id: str = "local-user",
    ) -> None:
        self.registry = registry
        self.store = store
        self.policy = policy
        self.collect = collect
        self.stop_sessions = stop_sessions
        self.execution_check = execution_check
        self._operator_id = operator_id
        self._lock = asyncio.Lock()
        # (token, server, "register" | "recheck" | "restore", file request)
        self._pending: tuple[str, ServerSettings, str, RegistrationRequest | None] | None = None
        # Unknown Registry writes are not retried in this run.
        self._unknown = False

    async def __call__(self, request: RegistrationRequest) -> RegistrationView:
        try:
            async with asyncio.timeout(90):
                async with self._lock:
                    return await self._request(request)
        except EndpointError as error:
            code = error.args[0] if error.args else None
            return RegistrationView(code if code in ENDPOINT_REJECT_CODES else "URL_INVALID")
        except (ValidationError, CatalogConfigError):
            return RegistrationView("REG_SETTINGS_INVALID")
        except SafePathError:
            return RegistrationView("REG_FOLDER_INVALID")
        except LaunchError:
            return RegistrationView("REG_EXECUTABLE_INVALID")
        except RegistryError as error:
            return RegistrationView(
                error.args[0]
                if error.args in (("REG_SERVER_EXISTS",), ("REG_SERVER_UNRECORDED",))
                else "REG_LISTING_INVALID"
            )
        except SessionRuntimeError as error:
            return RegistrationView(error.code)
        except StoreError:
            return RegistrationView("REG_STORE_UNKNOWN")
        except TimeoutError:
            return RegistrationView("REG_TIMEOUT")

    async def _rows(self) -> tuple[RegistrationRow, ...]:
        document = self.policy.registration_document()
        loaded = load_profile_settings(document, self.registry)
        configured = {s.id: s for s in loaded.servers}
        entries = await self.store.list_registrations()
        pins: dict[str, bool] = {}
        for server in configured.values():
            if server.transport != "stdio":
                continue
            try:
                current = await pin_executable(server.command)
            except LaunchError:
                pins[server.id] = False
            else:
                pins[server.id] = (
                    current.size == server.command_size and current.sha256 == server.command_sha256
                )
        return tuple(
            RegistrationRow(
                e.registration.server_id,
                e.registration.status.value,
                e.registration.boundary.tier.value,
                e.registration.boundary.max_label.name,
                configured[e.registration.server_id].transport
                if e.registration.server_id in configured
                else None,
                pins.get(e.registration.server_id, False),
                loaded.server_issue(e.registration.server_id),
                e.registration.server_id in configured,
            )
            for e in entries
        )

    async def _request(self, request: RegistrationRequest) -> RegistrationView:
        if request.action == "list":
            return RegistrationView("REG_LIST", rows=await self._rows())
        self.execution_check()
        self.policy.check_registration()
        if self._unknown:
            return RegistrationView("REG_STORE_UNKNOWN")
        if request.action == "tool_settings_preview":
            async with self.policy.lock:
                self.policy.check_registration()
                revision, rows = tool_settings_rows(
                    self.policy.registration_document(), self.registry, request.server_id
                )
                return RegistrationView(
                    "REG_TOOLS_PREVIEW",
                    row_revision=revision,
                    tool_settings=rows,
                    server_id=request.server_id,
                )
        if request.action == "tool_settings_save":
            await self.policy.save_tool_settings(
                request.server_id,
                request.expected_row_revision,
                request.tool_settings,
                self.registry,
                self.stop_sessions,
                self._review_unknown,
            )
            return RegistrationView(
                "REG_TOOLS_RESTART_REQUIRED",
                rows=await self._rows(),
                current_policy=self.policy.registration_policy_view(),
            )
        if request.action == "activation_preview":
            async with self.policy.lock:
                self.policy.check_registration()
                entry = self.registry.entry(request.server_id)
                loaded = load_profile_settings(self.policy.registration_document(), self.registry)
                server = next((s for s in loaded.servers if s.id == request.server_id), None)
                if entry is None or server is None:
                    return RegistrationView("REG_ACTIVATION_STALE")
                configured = {t.name: t for t in server.tools}
                tools = tuple(
                    PolicyTool(
                        t.tool_name,
                        t.kind.value,
                        t.kind in (ToolKind.READ, ToolKind.SEND)
                        and t.tool_name in configured
                        and configured[t.tool_name].kind == t.kind,
                    )
                    for t in entry.registration.tools
                )
                settings = loaded.guard_settings
                return RegistrationView(
                    "REG_ACTIVATION_PREVIEW",
                    row_revision=entry.registration.row_revision,
                    activation=PolicyView(
                        settings.profile_id,
                        settings.policy_version,
                        settings.approval_ttl_seconds,
                        settings.taint_mode,
                        (),
                        (),
                        (PolicyServer(server.id, tools),),
                        change_available=True,
                    ),
                )
        if request.action in ("activate", "block"):
            if (
                type(request.allowed) is not tuple
                or type(request.denied) is not tuple
                or any(type(name) is not str for name in (*request.allowed, *request.denied))
            ):
                return RegistrationView("POLICY_CHANGE_INVALID")
            await self.policy.set_server_active(
                request.server_id,
                request.expected_row_revision,
                request.allowed,
                request.denied,
                self.registry,
                self.stop_sessions,
                active=request.action == "activate",
                operator_id=self._operator_id,
            )
            return RegistrationView(
                "REG_ACTIVATION_RESTART_REQUIRED",
                rows=await self._rows(),
                current_policy=self.policy.registration_policy_view(),
            )
        if request.action in ("prepare", "prepare_file", "recheck"):
            self._pending = None
            updating = request.action == "recheck"
            restoring = request.restore is True and not updating
            file_request = request if request.action == "prepare_file" else None
            if file_request is not None:
                # Freeze the C-resolved root into the confirmation snapshot too.
                root = real_root(Path(file_request.file_root or ""))
                file_request = replace(file_request, file_root=str(root))
            if updating:
                loaded = load_profile_settings(self.policy.registration_document(), self.registry)
                server = next((s for s in loaded.servers if s.id == request.server_id), None)
                if (
                    server is None
                    or server.transport != "stdio"
                    or self.registry.entry(server.id) is None
                ):
                    return RegistrationView("REG_RECHECK_UNAVAILABLE")
                check_stdio_values(server.command, server.args, server.env_allow)
                pin = await pin_executable(server.command)
                server = server.model_copy(
                    update={"command_size": pin.size, "command_sha256": pin.sha256}
                )
            else:
                data: dict[str, object] = {
                    "id": request.server_id,
                    "transport": request.transport,
                    "tier": request.tier,
                    "max_label": request.max_label,
                    "tools": [],
                }
                if file_request is not None:
                    file_registration_sources(file_request)
                    command = sys.executable
                    args = file_server_args(Path(file_request.file_root or ""))
                    check_stdio_values(command, args, ())
                    pin = await pin_executable(command)
                    data.update(
                        transport="stdio",
                        command=command,
                        args=args,
                        env_allow=(),
                        tools=FILE_SERVER_TOOL_SETTINGS,
                        command_size=pin.size,
                        command_sha256=pin.sha256,
                    )
                elif request.transport == "stdio":
                    check_stdio_values(request.command, request.args, request.env_allow)
                    pin = await pin_executable(request.command)
                    data.update(
                        command=request.command,
                        args=request.args,
                        env_allow=request.env_allow,
                        command_size=pin.size,
                        command_sha256=pin.sha256,
                    )
                else:
                    data["url"] = normalize_endpoint(request.url).url
                server = ServerSettings.model_validate_json(json.dumps(data), strict=True)
                if restoring:
                    refused = self._restore_refusal(server, self.registry.entry(server.id))
                    if refused is not None:
                        return RegistrationView(refused)
                elif self.registry.entry(server.id) is not None or any(
                    s.id == server.id
                    for s in load_profile_settings(
                        self.policy.registration_document(), self.registry
                    ).servers
                ):
                    return RegistrationView("REG_SERVER_EXISTS")
            token = new_id("registration")
            mode = "recheck" if updating else "restore" if restoring else "register"
            self._pending = (token, server, mode, file_request)
            return self._preview(token, server, "REG_CONFIRM_REQUIRED")
        if (
            request.action != "confirm"
            or self._pending is None
            or request.token != self._pending[0]
        ):
            return RegistrationView("REG_CONFIRM_EXPIRED")
        _, server, mode, file_request = self._pending
        updating = mode == "recheck"
        self._pending = None  # single-use, also on errors/cancellation
        if server.transport == "stdio":
            check_stdio_values(server.command, server.args, server.env_allow)
            pin = await pin_executable(server.command)
            if pin.size != server.command_size or pin.sha256 != server.command_sha256:
                server = server.model_copy(
                    update={"command_size": pin.size, "command_sha256": pin.sha256}
                )
                token = new_id("registration")
                self._pending = (token, server, mode, file_request)
                return self._preview(token, server, "REG_PIN_CHANGED")
        if file_request is not None and real_root(
            Path(file_request.file_root or "")
        ) != file_server_root(server.args or ()):
            raise SafePathError("PATH_ROOT_INVALID")
        sources = file_registration_sources(file_request) if file_request is not None else ()
        if updating:
            current = await self.registry.refresh(server.id)
            if current is None:
                return RegistrationView("REG_RECHECK_UNAVAILABLE")
            written = False
            try:
                if current.registration.status is RegStatus.ACTIVE:
                    await self.registry.request_review(
                        server.id, expected_row_revision=current.registration.row_revision
                    )
                written = True
            finally:
                if not written:
                    self._unknown = True
        if not updating:
            restored = await self.registry.refresh(server.id) if mode == "restore" else None
            if mode == "restore":
                refused = self._restore_refusal(server, restored)
                if refused is not None:
                    return RegistrationView(refused)
            try:
                listing = await self.collect(server)
            except DispatchError as error:
                if error.exception_type not in _SERVER_START_FAILURES:
                    raise
                return RegistrationView("REG_SERVER_UNREACHABLE")  # nothing written
            if restored is not None:
                # Only the server the registration recorded gets its settings back.
                registered = {t.tool_name: t.fingerprint for t in restored.registration.tools}
                if listing_fingerprints(listing) != registered:
                    return RegistrationView("REG_RESTORE_TOOLS_DIFFER")  # nothing written
            else:
                written = False
                try:
                    await self.registry.register(server, listing)
                    written = True
                except RegistryError:
                    written = True  # fixed validation refusal, no uncertain store write
                    raise
                finally:
                    if not written:
                        self._unknown = True
        await self.policy.save_registration(
            server, self.registry, self.stop_sessions, updating=updating, sources=sources
        )
        codes = {"recheck": "REG_PIN_SAVED", "restore": "REG_SETTINGS_RESTORED"}
        return RegistrationView(codes.get(mode, "REG_REGISTERED"), rows=await self._rows())

    def _restore_refusal(self, server: ServerSettings, entry: RegistryEntry | None) -> str | None:
        """Why `server`'s settings record cannot be written back now; None when it can.

        Only a PENDING registration with no record in the settings file, and
        only with the boundary that registration holds (A128·A105).
        """
        loaded = load_profile_settings(self.policy.registration_document(), self.registry)
        if (
            entry is None
            or entry.registration.status is not RegStatus.PENDING
            or any(s.id == server.id for s in loaded.servers)
        ):
            return "REG_RESTORE_UNAVAILABLE"
        if server.as_boundary() != entry.registration.boundary:
            return "REG_RESTORE_BOUNDARY_DIFFERS"
        return None

    @staticmethod
    def _preview(token: str, server: ServerSettings, code: str) -> RegistrationView:
        return RegistrationView(
            code,
            token=token,
            command=server.command,
            size=server.command_size,
            sha256=server.command_sha256,
            url=server.url,
        )

    def _review_unknown(self) -> None:
        self._unknown = True
