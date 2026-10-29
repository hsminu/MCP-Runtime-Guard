"""Product startup: Store recovery, Guard composition and runtime publication."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from guard_app.domain.enums import StorePrepareStep
from guard_app.domain.errors import StoreError
from guard_app.domain.models import RecoveryItemPage
from guard_app.security.ids import new_id
from guard_app.security.paths import AppFolders
from guard_app.services.account_runtime import AccountAccess
from guard_app.services.bridge_types import (
    BackendBindings,
    BackendCleanupError,
)
from guard_app.services.catalog import GuardSettings, ServerSettings
from guard_app.services.codex_composition import codex_session_factory
from guard_app.services.guard import durable_failure_audit
from guard_app.services.guard_relay import ApprovalOutcomes
from guard_app.services.product_assembly import (
    AssembledGuard,
    ProductInputs,
    assemble_guard,
    collect_registration_listing,
)
from guard_app.services.product_settings import (
    SETTINGS_LOADED,
    SettingsFile,
    SettingsFileSaver,
    load_settings_profile,
)
from guard_app.services.registration_scans import RegistrationScans
from guard_app.services.registry import ServerRegistry, ToolListingView
from guard_app.services.scanner import ScanService, cisco_connector
from guard_app.services.session_cleanup import BackgroundWork, CleanupLimits
from guard_app.services.session_execution import ExecutionGate
from guard_app.services.session_policy import ProfileSaver
from guard_app.services.session_preparation import (
    SessionPreparation,
)
from guard_app.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from guard_app.services.session_runtime import CodexSessionFactory


async def _close_failed_start(store: SQLiteStore) -> None:
    """A start that fails before the runtime owns its Store closes it here.

    Unconfirmed, the bridge reports startup cleanup unconfirmed (#58, A134).
    """
    store.close()
    if not await store.wait_closed(CleanupLimits().store_close_s):
        raise BackendCleanupError


def _registration_scans(
    registry: ServerRegistry,
    collect: Callable[[ServerSettings], Awaitable[ToolListingView]],
) -> RegistrationScans:
    return RegistrationScans(registry, ScanService(registry, cisco_connector()), collect)


async def bootstrap_product(
    folders: AppFolders,
    *,
    session_factory: CodexSessionFactory | None = None,
    preparation: SessionPreparation | None = None,
    product: ProductInputs | None = None,
    profile_saver: ProfileSaver | None = None,
    settings: SettingsFile | None = None,
    account: AccountAccess | None = None,
    on_step: Callable[[StorePrepareStep], None] | None = None,
) -> BackendBindings:
    """REC-01: no bindings or session before the activation commit ACK.

    ``product`` is an explicit test/demo composition override. Its injected
    streams require absent ProductInputs.folders. Normal product startup builds
    ProductInputs from settings with the claimed folders and C launcher.
    """
    # Local import keeps the compatibility re-export free of initialization cycles.
    from guard_app.services.session_runtime import SessionRuntime

    deadline = time.monotonic() + 60.0
    run_id = new_id("run")
    store = SQLiteStore(
        folders.db_path(), startup="explicit", audit_id_factory=lambda: new_id("audit")
    )
    assembled: AssembledGuard | None = None
    runtime: SessionRuntime | None = None
    adopted = False
    scans: RegistrationScans | None = None
    progress = BackgroundWork()

    def step(value: StorePrepareStep) -> None:
        # Callback exceptions are consumed from Task.exception(), never thrown into B.
        # Do not log exception text or type. No awaitable callback is part of this API.
        if on_step is not None:

            async def publish() -> None:
                on_step(value)

            progress.start(publish())

    try:
        await store.prepare_schema(
            deadline=deadline, begin_backup=folders.begin_backup, on_step=step
        )
        await store.recover(deadline=deadline, run_id=run_id, on_step=step)
        report = await store.load_recovery_report(run_id)
        if report is None:
            raise StoreError()
        # Reports describe this recovery; quarantine remains inspectable on
        # later runs even when no new item was added by this recovery.
        first_page = await store.list_recovery_items(limit=100)
        guard_settings: GuardSettings | None = None
        profile_document: Mapping[str, object] | None = None
        settings_status: str | None = None
        if settings is not None:
            settings_status, loaded = await load_settings_profile(settings, store)
            if loaded is not None:
                guard_settings = loaded.guard_settings
                profile_document = settings.document
                if profile_saver is None:
                    profile_saver = SettingsFileSaver(settings)
                if product is None and profile_document is not None:
                    registry = ServerRegistry(store)
                    await registry.load()
                    product = ProductInputs(
                        profile_document,
                        registry,
                        store.allocate_call_seq,
                        durable_failure_audit(store),
                        operator_id="local-user",
                        folders=folders,
                    )
        approvals: ApprovalOutcomes | None = None
        if product is not None:
            product = replace(
                product,
                next_call_seq=store.allocate_call_seq,
                failure_audit=durable_failure_audit(store),
            )
            assembled = await assemble_guard(store, product)
            approvals = ApprovalOutcomes()
        # L2 may prepare app-wide resources, but no Task session is created here.
        runtime = SessionRuntime(
            store,
            session_factory=session_factory,
            preparation=preparation,
            owned_store=store,
            guard_settings=guard_settings,
            assembled=assembled,
            approvals=approvals,
            profile_document=profile_document if product is None else product.profile_document,
            profile_saver=profile_saver,
            settings_status=settings_status,
            account=account,
        )
        if preparation is not None:
            await runtime.prepare_startup()
        await store.activate_run()
        runtime.connect_execution_gate(ExecutionGate(store.execution_state))
        if session_factory is None and assembled is not None and approvals is not None:
            runtime.connect_session_factory(
                codex_session_factory(
                    assembled, approvals, execution_allowed=runtime.execution_allowed
                )
            )
        after = first_page.next_after

        async def next_page() -> RecoveryItemPage:
            nonlocal after
            if after is None:
                return RecoveryItemPage(items=(), next_after=None)
            page = await store.list_recovery_items(after=after, limit=100)
            after = page.next_after
            return page

        registration_registry = (
            product.registry
            if product is not None and isinstance(product.registry, ServerRegistry)
            else ServerRegistry(store)
        )
        if product is None or registration_registry is not product.registry:
            await registration_registry.load()
        registration_saver = (
            SettingsFileSaver(settings)
            if settings is not None
            and (settings.document is None or settings_status == SETTINGS_LOADED)
            else None
        )

        def collect(server: ServerSettings) -> Awaitable[ToolListingView]:
            return collect_registration_listing(server, folders)

        scans = _registration_scans(registration_registry, collect)

        async def close() -> None:
            try:
                await scans.aclose()
            finally:
                await runtime.close()

        registrations = runtime.registration_commands(
            registration_registry,
            store,
            collect,
            saver=registration_saver,
            scans=scans,
        )
        bindings = replace(
            runtime.bindings(),
            registration=registrations,
            scan_events=scans.listen,
            scan_for_review=scans.reviewed,
            close=close,
            recovery_report=report,
            recovery_page=first_page,
            recovery_next=next_page,
        )
        runtime.start_execution_watch()
        adopted = True
        return bindings
    finally:
        if not adopted:
            if runtime is not None:
                cleanup = asyncio.create_task(close() if scans is not None else runtime.close())
                cleanup.add_done_callback(
                    lambda task: None if task.cancelled() else task.exception()
                )
                done, _ = await asyncio.wait({cleanup}, timeout=CleanupLimits().store_close_s)
                if not done or cleanup.cancelled() or cleanup.exception() is not None:
                    raise BackendCleanupError
            else:
                try:
                    if assembled is not None:
                        await assembled.aclose()
                finally:
                    await _close_failed_start(store)
