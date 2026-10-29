"""The Profile new Task rows take, and server access changes to it (B3.10, B3.12)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.domain.enums import RegStatus, TaskStatus
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import TaskState
from guard_app.services.catalog import (
    CatalogConfigError,
    GuardSettings,
    PreparedPolicyChange,
    ServerSettings,
    prepare_policy_change,
    prepare_server_access_change,
)
from guard_app.services.guard import PolicyChangeError, PolicyTaskSource
from guard_app.services.policy_view import PolicyView, policy_view
from guard_app.services.product_assembly import AssembledGuard
from guard_app.services.product_settings import (
    SaveOutcomeUnknownError,
    SettingsFileChangedError,
)
from guard_app.services.registry import ServerRegistry
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError

# Stores B's prepared policy document (CFG-01: product_settings.SettingsFileSaver).
# True only after the document is durably saved; apply follows only then. A save
# that may have happened raises SaveOutcomeUnknownError; a file changed outside
# the app raises SettingsFileChangedError.
type ProfileSaver = Callable[[PreparedPolicyChange], Awaitable[bool]]


def _server_access_change(
    document: Mapping[str, object],
    assembled: AssembledGuard,
    server_id: str,
    allowed: tuple[str, ...],
    denied: tuple[str, ...],
) -> PreparedPolicyChange:
    """B's prepared document with this server's tools allowed or denied (B3.10)."""
    try:
        return prepare_server_access_change(
            document,
            assembled.registry,
            server_id=server_id,
            tool_permissions={
                **{name: True for name in allowed},
                **{name: False for name in denied},
            },
        )
    except CatalogConfigError:
        raise SessionRuntimeError("POLICY_CHANGE_INVALID") from None


class TaskPolicy:
    """The Profile in force for new Task rows, and the changes applied to it.

    A change holds `lock` from saving until the sessions Guard stopped are
    closed. create_session writes its Task row under the same lock (A105), so
    no row is written under rules that are being replaced. Without a loaded
    Profile no Task is created; there is no placeholder policy (A79).
    """

    def __init__(
        self,
        settings: GuardSettings | None,
        admission: SessionAdmission,
        *,
        assembled: AssembledGuard | None = None,
        document: Mapping[str, object] | None = None,
        saver: ProfileSaver | None = None,
        task_source: PolicyTaskSource | None = None,
        missing_code: str = "GUARD_PROFILE_UNAVAILABLE",
    ) -> None:
        self.lock = asyncio.Lock()
        self._settings = settings
        self._missing_code = missing_code
        self._admission = admission
        self._assembled = assembled
        self._document = document
        self._saver = saver
        self._task_source = task_source

    @property
    def available(self) -> bool:
        return (
            self._assembled is not None
            and self._document is not None
            and self._saver is not None
            and self._task_source is not None
        )

    def blocker(self) -> str | None:
        """Why no Profile is in force: no settings file, an invalid one, or none connected."""
        return None if type(self._settings) is GuardSettings else self._missing_code

    def task_state(self, task_id: str) -> TaskState:
        """The first-epoch RUNNING row for a new Task under the Profile in force."""
        settings = self._settings
        if type(settings) is not GuardSettings:
            raise SessionRuntimeError(self._missing_code)
        return TaskState(
            task_id=task_id,
            epoch=1,
            profile_id=settings.profile_id,
            policy_version=settings.policy_version,
            status=TaskStatus.RUNNING,
        )

    async def change_server_access(
        self,
        server_id: str,
        allowed: tuple[str, ...],
        denied: tuple[str, ...],
        stop_sessions: Callable[[tuple[str, ...]], Awaitable[None]],
    ) -> PolicyView:
        """B3.10: prepare -> save -> apply; then stop the sessions Guard stopped.

        The saved document is the only new current document. If saving fails
        nothing is applied. If Guard refuses or fails to apply a saved change,
        Guard is closed to new tool calls, new sessions and further changes are
        refused, and this raises POLICY_APPLY_FAILED; a restart loads the saved
        file (A104, #33 B reply). A save whose outcome is unknown, or cancelled
        mid-write, is handled the same way with POLICY_SAVE_UNKNOWN: the file may
        hold either document (C2.14). A file changed outside the app is not
        written and raises POLICY_SAVE_FILE_CHANGED; Guard keeps the rules it
        runs, and only a restart reads the file again.
        """
        assembled, saver, task_source = self._assembled, self._saver, self._task_source
        if (
            assembled is None
            or self._document is None
            or saver is None
            or task_source is None
            or self._admission.closed
        ):
            raise SessionRuntimeError("POLICY_CHANGE_UNAVAILABLE")
        if set(allowed) & set(denied):
            raise SessionRuntimeError("POLICY_CHANGE_INVALID")
        async with self.lock:
            if self._admission.restart_code is not None:
                # Saving again would hide that Guard does not run the saved file.
                raise SessionRuntimeError(self._admission.restart_code)
            # Read under the lock: a change that waited here starts from the
            # document the change before it saved, not the one it replaced (A117).
            document = self._document
            change = _server_access_change(document, assembled, server_id, allowed, denied)
            if change.changed:
                try:
                    saved = await saver(change)
                except SettingsFileChangedError:
                    raise SessionRuntimeError("POLICY_SAVE_FILE_CHANGED") from None
                except SaveOutcomeUnknownError:
                    await self._require_restart(assembled, "POLICY_SAVE_UNKNOWN")
                    raise SessionRuntimeError("POLICY_SAVE_UNKNOWN") from None
                except asyncio.CancelledError:
                    # The write may still finish after the cancel. Guard sets its
                    # global stop before its first await, so a second cancel
                    # during the close still leaves it stopped.
                    await self._require_restart(assembled, "POLICY_SAVE_UNKNOWN")
                    raise
                if saved is not True:
                    raise SessionRuntimeError("POLICY_SAVE_FAILED")
                self._document = change.document
                await stop_sessions(await self._apply(assembled, change, task_source))
            return policy_view(assembled, change_available=True)

    def check_registration(self) -> None:
        self._admission.check()
        if self._saver is None:
            raise SessionRuntimeError("REG_SETTINGS_UNAVAILABLE")

    def registration_policy_view(self) -> PolicyView | None:
        if self._assembled is None:
            return None
        return policy_view(self._assembled, change_available=self._admission.refusal() is None)

    async def set_server_active(
        self,
        server_id: str,
        expected_row_revision: int,
        allowed: tuple[str, ...],
        denied: tuple[str, ...],
        registry: ServerRegistry,
        stop_sessions: Callable[[tuple[str, ...]], Awaitable[None]],
        *,
        active: bool,
        operator_id: str,
    ) -> None:
        """A105: serialize with policy edits and Task creation, including both writes.

        Activation saves/applies permissions while still PENDING, then activates.
        Blocking reverses the order: revoke Registry authority before saving.
        Any incomplete write sequence closes Guard; never retry it in this run.
        Transport membership is fixed at assembly, so success also needs restart.
        """
        async with self.lock:
            self.check_registration()
            entry = registry.entry(server_id)
            if (
                entry is None
                or type(expected_row_revision) is not int
                or entry.registration.row_revision != expected_row_revision
                or entry.registration.status
                not in ((RegStatus.PENDING,) if active else (RegStatus.PENDING, RegStatus.ACTIVE))
            ):
                raise SessionRuntimeError("REG_ACTIVATION_STALE")
            if set(allowed) & set(denied):
                raise SessionRuntimeError("POLICY_CHANGE_INVALID")
            current = self.registration_document()
            try:
                access = (
                    prepare_server_access_change(
                        current,
                        registry,
                        server_id=server_id,
                        tool_permissions={
                            **{name: True for name in allowed},
                            **{name: False for name in denied},
                        },
                    )
                    if active
                    else prepare_policy_change(current, current, registry)
                )
                document = access.document.to_dict()
                profile = document.get("profile")
                if not isinstance(profile, dict) or not isinstance(profile.get("servers"), list):
                    raise SessionRuntimeError("POLICY_CHANGE_INVALID")
                selected = profile["servers"]
                if active and server_id not in selected:
                    selected.append(server_id)
                if not active:
                    selected[:] = [s for s in selected if s != server_id]
                    profile["sources"] = [
                        source
                        for source in profile.get("sources", [])
                        if source["server"] != server_id
                    ]
                    rules = document.get("access_rules", {})
                    if isinstance(rules, dict):
                        document["access_rules"] = {
                            key: [pair for pair in rules.get(key, []) if pair[0] != server_id]
                            for key in ("allow", "deny")
                        }
                # Keep the assembled live-fingerprint gate in Guard's new Catalog.
                change = prepare_policy_change(
                    current,
                    document,
                    self._assembled.registry if self._assembled is not None else registry,
                )
            except CatalogConfigError:
                raise SessionRuntimeError("POLICY_CHANGE_INVALID") from None
            completed = False
            try:
                if not active:
                    await registry.block(server_id, expected_row_revision=expected_row_revision)
                if self._saver is None:
                    raise SessionRuntimeError("REG_SETTINGS_UNAVAILABLE")
                try:
                    saved = await self._saver(change)
                except SettingsFileChangedError:
                    raise SessionRuntimeError("POLICY_SAVE_FILE_CHANGED") from None
                except SaveOutcomeUnknownError:
                    raise SessionRuntimeError("POLICY_SAVE_UNKNOWN") from None
                if saved is not True:
                    raise SessionRuntimeError("POLICY_SAVE_FAILED")
                self._document = change.document
                if self._assembled is not None and self._task_source is not None:
                    await stop_sessions(
                        await self._apply(self._assembled, change, self._task_source)
                    )
                    if not change.changed:
                        # policy_version is a content hash: a re-activation may
                        # keep it. Still stop sessions before requiring restart.
                        control = self._assembled.guard.human_control(self._assembled.operator_id)
                        running = tuple(
                            state.task_id
                            for state in await self._task_source()
                            if state.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
                        )
                        for task_id in running:
                            await control.emergency_halt(task_id)
                        await stop_sessions(running)
                if active:
                    await registry.activate(
                        server_id,
                        expected_row_revision=expected_row_revision,
                        reviewed_by=operator_id,
                        reviewed_scan_id=None,
                    )
                completed = True
            finally:
                code = "REG_ACTIVATION_RESTART_REQUIRED" if completed else "REG_ACTIVATION_UNKNOWN"
                self._admission.require_restart(code)
                if self._assembled is not None:
                    await self._assembled.guard.aclose()

    def registration_document(self) -> Mapping[str, object]:
        if isinstance(self._document, FrozenJsonObject):
            return self._document.to_dict()
        # First registration selects no servers and grants no access.
        return (
            self._document
            if self._document is not None
            else {
                "profile": {"id": "local", "servers": []},
                "servers": [],
            }
        )

    def connect_registration_saver(self, saver: ProfileSaver) -> None:
        if self._saver is None:
            self._saver = saver

    async def save_registration(
        self,
        server: ServerSettings,
        registry: ServerRegistry,
        stop_sessions: Callable[[tuple[str, ...]], Awaitable[None]],
        *,
        updating: bool,
    ) -> None:
        async with self.lock:
            if self._saver is None or self._admission.closed:
                raise SessionRuntimeError("REG_SETTINGS_UNAVAILABLE")
            if self._admission.restart_code is not None:
                raise SessionRuntimeError(self._admission.restart_code)
            current = self.registration_document()
            document = FrozenJsonObject(current).to_dict()
            servers = document.get("servers")
            if not isinstance(servers, list):
                raise SessionRuntimeError("REG_SETTINGS_INVALID")
            record = server.model_dump(mode="json", exclude_none=True)
            record["max_label"] = server.max_label.name
            if updating:
                servers[:] = [
                    record if isinstance(s, dict) and s.get("id") == server.id else s
                    for s in servers
                ]
            else:
                if any(isinstance(s, dict) and s.get("id") == server.id for s in servers):
                    raise SessionRuntimeError("REG_SERVER_EXISTS")
                servers.append(record)
            change = prepare_policy_change(current, document, registry)
            completed = False
            try:
                try:
                    saved = await self._saver(change)
                except SettingsFileChangedError:
                    raise SessionRuntimeError("POLICY_SAVE_FILE_CHANGED") from None
                except SaveOutcomeUnknownError:
                    raise SessionRuntimeError("POLICY_SAVE_UNKNOWN") from None
                if saved is not True:
                    raise SessionRuntimeError("REG_SETTINGS_SAVE_FAILED")
                self._document = change.document
                if self._assembled is not None and self._task_source is not None:
                    await stop_sessions(
                        await self._apply(self._assembled, change, self._task_source)
                    )
                if updating:
                    self._admission.require_restart("REG_PIN_RESTART_REQUIRED")
                    if self._assembled is not None:
                        await self._assembled.guard.aclose()
                completed = True
            finally:
                # Registry may already be PENDING. Never retry a partially saved
                # registration or let new policy changes overwrite an unknown file.
                if not completed:
                    self._admission.require_restart("POLICY_SAVE_UNKNOWN")
                    if self._assembled is not None:
                        await self._assembled.guard.aclose()

    async def _apply(
        self,
        assembled: AssembledGuard,
        change: PreparedPolicyChange,
        task_source: PolicyTaskSource,
    ) -> tuple[str, ...]:
        """Apply a saved change; the Tasks Guard stopped for it."""
        try:
            result = await assembled.guard.apply_policy_change(
                change,
                AccessRulesChecker(change.loaded_profile.access_rules),
                task_source=task_source,
            )
        except PolicyChangeError:
            # The file holds the new rules; Guard keeps the old ones when it
            # refused before applying, and halted itself when applying failed.
            # Either way no call runs under rules the screen does not show.
            await self._require_restart(assembled, "POLICY_APPLY_FAILED")
            raise SessionRuntimeError("POLICY_APPLY_FAILED") from None
        # New Task rows and the policy screen follow the applied Profile.
        assembled.loaded = change.loaded_profile
        self._settings = change.loaded_profile.guard_settings
        return result.stopped_task_ids

    async def _require_restart(self, assembled: AssembledGuard, code: str) -> None:
        """Refuse new sessions and changes, and close Guard to new tool calls."""
        self._admission.require_restart(code)
        await assembled.guard.aclose()
