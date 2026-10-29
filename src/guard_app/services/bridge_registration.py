"""Human registration commands: B settings, C validation/Registry, no activation.

Confirmation tokens bind a backend-held settings snapshot to C's measured pin.
Only one outstanding confirmation is retained. A changed pin consumes it and
requires another human confirmation. Arguments never enter public projections.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from guard_app.domain.enums import RegStatus, ToolKind
from guard_app.domain.errors import DispatchError, StoreError
from guard_app.ports import RegistryStore
from guard_app.security.endpoints import ENDPOINT_REJECT_CODES, EndpointError, normalize_endpoint
from guard_app.security.ids import new_id
from guard_app.security.launcher import LaunchError, check_stdio_values, pin_executable
from guard_app.services.bridge_types import RegistrationRequest, RegistrationRow, RegistrationView
from guard_app.services.catalog import CatalogConfigError, ServerSettings, load_profile_settings
from guard_app.services.policy_view import PolicyServer, PolicyTool, PolicyView
from guard_app.services.registry import RegistryError, ServerRegistry, ToolListingView
from guard_app.services.session_admission import SessionRuntimeError
from guard_app.services.session_policy import TaskPolicy

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
        self._pending: tuple[str, ServerSettings, bool] | None = None
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
        configured = {s.id: s for s in load_profile_settings(document, self.registry).servers}
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
        if request.action in ("prepare", "recheck"):
            self._pending = None
            updating = request.action == "recheck"
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
                if request.transport == "stdio":
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
                if self.registry.entry(server.id) is not None or any(
                    s.id == server.id
                    for s in load_profile_settings(
                        self.policy.registration_document(), self.registry
                    ).servers
                ):
                    return RegistrationView("REG_SERVER_EXISTS")
            token = new_id("registration")
            self._pending = (token, server, updating)
            return self._preview(token, server, "REG_CONFIRM_REQUIRED")
        if (
            request.action != "confirm"
            or self._pending is None
            or request.token != self._pending[0]
        ):
            return RegistrationView("REG_CONFIRM_EXPIRED")
        _, server, updating = self._pending
        self._pending = None  # single-use, also on errors/cancellation
        if server.transport == "stdio":
            check_stdio_values(server.command, server.args, server.env_allow)
            pin = await pin_executable(server.command)
            if pin.size != server.command_size or pin.sha256 != server.command_sha256:
                server = server.model_copy(
                    update={"command_size": pin.size, "command_sha256": pin.sha256}
                )
                token = new_id("registration")
                self._pending = (token, server, updating)
                return self._preview(token, server, "REG_PIN_CHANGED")
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
            try:
                listing = await self.collect(server)
            except DispatchError as error:
                if error.exception_type not in _SERVER_START_FAILURES:
                    raise
                return RegistrationView("REG_SERVER_UNREACHABLE")  # nothing written
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
            server, self.registry, self.stop_sessions, updating=updating
        )
        return RegistrationView(
            "REG_PIN_SAVED" if updating else "REG_REGISTERED", rows=await self._rows()
        )

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
