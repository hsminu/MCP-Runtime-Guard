"""A-local injectable Codex composition pending B/C agreement.

C supplies startup L2 evidence and secure process/path/cleanup capabilities;
A composes those with its JSON-RPC and Guard relay into the session factory.
Missing capabilities reject before Store Task creation. No launcher, login
persistence, model API, Guard protocol or replacement B port is implemented here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from guard_app.adapters.access_rules_checker import AccessRulesChecker
from guard_app.adapters.codex_protocol import build_dynamic_tools
from guard_app.domain.contracts import TaskIdentity
from guard_app.domain.enums import SystemStopReason, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import SessionAudit, Store
from guard_app.services.bridge import (
    BackendBindings,
    ControlAction,
    SessionOptions,
    SessionStartError,
    TaskProjection,
)
from guard_app.services.catalog import (
    CatalogConfigError,
    GuardSettings,
    PreparedPolicyChange,
    prepare_server_access_change,
)
from guard_app.services.codex_composition import codex_session_factory
from guard_app.services.guard import (
    PolicyChangeError,
    PolicyTaskSource,
    SystemControl,
    SystemControlError,
)
from guard_app.services.guard_relay import ApprovalOutcomes
from guard_app.services.policy_view import PolicyView, policy_view
from guard_app.services.product_assembly import AssembledGuard, ProductInputs, assemble_guard
from guard_app.services.runner import CodexSession, CodexTaskRunner
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
    valid_configuration,
)
from guard_app.storage.sqlite_store import SQLiteStore

# A-local proposal only: A's factory must use C's executable/config/model hash
# verification and separate app-server, then perform account/read and relay setup.
# Receipts and callbacks are A-local composition seams, not final B/C APIs.
type CodexSessionFactory = Callable[
    [TaskState, SessionOptions, TaskResources], Awaitable[CodexSession]
]

# Stores B's prepared policy document (B3.11/B3.12 file format is A·C, open).
# True only after the document is durably saved; apply follows only then.
type ProfileSaver = Callable[[PreparedPolicyChange], Awaitable[bool]]

SESSION_CLEANUP_TIMEOUT_S = 0.5

# A session stop code -> STOP-01 reason (PR #28 review mapping, agreed by B).
# A token refresh conflict arrives as `unauthorized` like any auth failure, so
# AUTH_REFRESH_CONFLICT is not chosen until C's T46 finds a signal.
_STOP_REASONS = {
    "CODEX_L3_VIOLATION": SystemStopReason.L3_VIOLATION,
    "CODEX_WATCHDOG_TIMEOUT": SystemStopReason.WATCHDOG_EXCEEDED,
    "CODEX_AUTH_FAILED": SystemStopReason.AUTH_FAILED,
    "CODEX_USAGE_LIMIT": SystemStopReason.USAGE_LIMIT,
    "CODEX_TOOL_CALL_INVALID": SystemStopReason.PROTOCOL_FAILED,
    "CODEX_SERVER_REQUEST_REFUSED": SystemStopReason.PROTOCOL_FAILED,
    "CODEX_ANSWER_TOO_LARGE": SystemStopReason.PROTOCOL_FAILED,
    "CODEX_INPUT_INVALID": SystemStopReason.PROTOCOL_FAILED,
}


def stop_reason(code: str) -> SystemStopReason:
    """Process exit, turn/start failures and anything unlisted count as PROCESS_FAILED."""
    return _STOP_REASONS.get(code, SystemStopReason.PROCESS_FAILED)


class SessionRuntimeError(SessionStartError):
    pass


class SessionRuntime:
    def __init__(
        self,
        store: Store,
        *,
        session_factory: CodexSessionFactory | None = None,
        preparation: SessionPreparation | None = None,
        close_store: Callable[[], None] | None = None,
        guard_settings: GuardSettings | None = None,
        assembled: AssembledGuard | None = None,
        approvals: ApprovalOutcomes | None = None,
        profile_document: Mapping[str, object] | None = None,
        profile_saver: ProfileSaver | None = None,
    ) -> None:
        if assembled is not None:
            guard_settings = assembled.settings
        # STOP-01: session stops go through Guard's system channel, audited in the
        # Store (SQLiteStore implements SessionAudit), not the human channel.
        self._system: SystemControl | None = (
            assembled.guard.system_control(cast(SessionAudit, store))
            if assembled is not None and callable(getattr(store, "record_session_event", None))
            else None
        )
        # Every Task this Guard governs, for apply_policy_change (SQLiteStore.list_tasks).
        list_tasks = getattr(store, "list_tasks", None)
        self._task_source: PolicyTaskSource | None = (
            cast(PolicyTaskSource, list_tasks) if callable(list_tasks) else None
        )
        self._profile_document = profile_document
        self._profile_saver = profile_saver
        self._policy_lock = asyncio.Lock()
        self.store = store
        # Profile identity and policy_version for new Task rows. Without a loaded
        # Profile no Task is created; there is no placeholder policy (A79).
        self._guard_settings = guard_settings
        self._assembled = assembled
        self._approvals = approvals
        self._task_listener: Callable[[str], None] | None = None
        self._partial_listener: Callable[[str, int, str], None] | None = None
        # What Codex actually receives: Guard tools minus Codex name-rule exclusions.
        self._tools_available = assembled is not None and bool(
            build_dynamic_tools(assembled.tool_views).tool_refs
        )
        self._session_factory = session_factory
        self._preparation = preparation
        self._startup_lock = asyncio.Lock()
        self._configuration: ConfigurationReceipt | None = None
        self._startup_attempted = False
        self._close_store = close_store
        self._runners: dict[str, CodexTaskRunner] = {}
        self._sessions: dict[str, CodexSession] = {}
        self._options: dict[str, SessionOptions] = {}
        self._codes: dict[str, str] = {}
        self._closed = False
        self._creating: set[asyncio.Task[object]] = set()
        self._creating_ids: dict[asyncio.Task[object], str] = {}
        self._defer_store_close = False
        self._cleanup_unconfirmed = False
        self._resources: dict[str, TaskResources] = {}
        self._cleanup_started: set[str] = set()
        self._cleanup_outcomes: dict[str, asyncio.Future[bool]] = {}
        self._cleanup_operations: set[asyncio.Task[None]] = set()

    def runner_for_task(self, task_id: str) -> CodexTaskRunner:
        if self._closed or task_id not in self._runners:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        return self._runners[task_id]

    def _check_creation_allowed(self) -> None:
        current = asyncio.current_task()
        if self._closed or (current is not None and current.cancelling()):
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        if self._cleanup_unconfirmed:
            raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")

    def creation_blocker(self) -> str | None:
        """What is already known to refuse a new session, in creation order.

        None does not promise success: L2 and per-Task checks run on create.
        """
        if self._closed:
            return "SESSION_UNAVAILABLE"
        if self._cleanup_unconfirmed:
            return "CODEX_CLEANUP_UNCONFIRMED"
        preparation = self._preparation
        if type(preparation) is not SessionPreparation:
            return "CODEX_CONFIG_UNAVAILABLE"
        if self._startup_attempted and self._configuration is None:
            return "CODEX_PREPARATION_FAILED"
        if self._session_factory is None:
            return "CODEX_SESSION_UNAVAILABLE"
        if type(self._guard_settings) is not GuardSettings:
            return "GUARD_PROFILE_UNAVAILABLE"
        return None

    @staticmethod
    async def _prepared[Result](action: Callable[[], Awaitable[Result]]) -> Result:
        """Translate callback faults without swallowing cancellation or raw text."""

        async def invoke() -> Result:
            return await action()

        operation = asyncio.create_task(invoke())
        try:
            return await operation
        finally:
            if operation.done() and not operation.cancelled() and operation.exception() is not None:
                raise SessionRuntimeError("CODEX_PREPARATION_FAILED") from None

    async def _check_startup(self) -> ConfigurationReceipt:
        async with self._startup_lock:
            self._check_creation_allowed()
            preparation = self._preparation
            if type(preparation) is not SessionPreparation or not all(
                callable(callback)
                for callback in (
                    preparation.prepare_startup,
                    preparation.verify_configuration,
                    preparation.prepare_task,
                    preparation.confirm_exit,
                    preparation.cleanup_task,
                )
            ):
                raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
            if self._configuration is None:
                if self._startup_attempted:
                    raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
                self._startup_attempted = True
                receipt = await self._prepared(preparation.prepare_startup)
                self._check_creation_allowed()
                if type(receipt) is not StartupReceipt or not valid_configuration(
                    receipt.configuration
                ):
                    raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
                if receipt.l2_configuration is None:
                    raise SessionRuntimeError("CODEX_L2_UNAVAILABLE")
                if receipt.l2_passed is not True:
                    raise SessionRuntimeError("CODEX_L2_FAILED")
                if not valid_configuration(receipt.l2_configuration):
                    raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
                if receipt.configuration != receipt.l2_configuration:
                    raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")
                self._configuration = receipt.configuration
            return self._configuration

    async def _verify_configuration(self, expected: ConfigurationReceipt) -> None:
        preparation = self._preparation
        if preparation is None:
            raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
        actual = await self._prepared(lambda: preparation.verify_configuration(expected))
        self._check_creation_allowed()
        if not valid_configuration(actual):
            raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
        if actual != expected:
            raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")

    async def create_session(self, options: SessionOptions) -> TaskProjection:
        if self._closed or type(options) is not SessionOptions:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        current = asyncio.current_task()
        if current is None:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        self._creating.add(current)
        try:
            return await self._create_session(options)
        finally:
            self._creating.discard(current)
            self._creating_ids.pop(current, None)
            if self._defer_store_close and not self._creating:
                self._close_store_once()

    async def _create_session(self, options: SessionOptions) -> TaskProjection:
        expected = await self._check_startup()
        await self._verify_configuration(expected)
        factory = self._session_factory
        if factory is None:
            raise SessionRuntimeError("CODEX_SESSION_UNAVAILABLE")
        settings = self._guard_settings
        if type(settings) is not GuardSettings:
            raise SessionRuntimeError("GUARD_PROFILE_UNAVAILABLE")
        state = TaskState(
            task_id=f"session-{uuid4().hex}",
            epoch=1,
            profile_id=settings.profile_id,
            policy_version=settings.policy_version,
            status=TaskStatus.RUNNING,
        )
        current = asyncio.current_task()
        if current is not None:
            self._creating_ids[current] = state.task_id
        preparation = self._preparation
        if preparation is None:
            raise SessionRuntimeError("CODEX_CONFIG_UNAVAILABLE")
        created = adopted = False
        try:
            resources = await self._prepared(
                lambda: preparation.prepare_task(state.task_id, expected)
            )
            if (
                type(resources) is not TaskResources
                or resources.task_id != state.task_id
                or type(resources.owner_token) is not str
                or not resources.owner_token
                or len(resources.owner_token) > 256
                or resources.handle is None
                or any(
                    resources.owner_token == owned.owner_token or resources.handle is owned.handle
                    for owned in self._resources.values()
                )
            ):
                # A mismatched/shared receipt never grants ownership to erase it.
                # Its partial resource ownership is nevertheless unconfirmed;
                # reject additional allocations until the app is restarted.
                self._cleanup_unconfirmed = True
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            self._resources[state.task_id] = resources
            self._check_creation_allowed()
            if not valid_configuration(resources.configuration):
                raise SessionRuntimeError("CODEX_PREPARATION_FAILED")
            if resources.configuration != expected:
                raise SessionRuntimeError("CODEX_CONFIG_MISMATCH")
            # Recheck after resource setup, immediately before session creation.
            await self._verify_configuration(expected)
            # No Store RUNNING row exists while C's secure launch is unconfirmed.
            session = await self._prepared(lambda: factory(state, options, resources))
            if any(session is existing for existing in self._sessions.values()):
                raise SessionRuntimeError("CODEX_SESSION_INVALID")
            self._sessions[state.task_id] = session
            if not all(
                callable(getattr(session, name, None)) for name in ("send", "interrupt", "aclose")
            ):
                raise SessionRuntimeError("CODEX_SESSION_INVALID")
            self._check_creation_allowed()
            await self.store.create_task(state, created_at_utc=datetime.now(UTC))
            created = True
            confirmed = await self.store.load_task(state.task_id)
            if confirmed != state:
                raise StoreError()
            self._check_creation_allowed()
            runner = CodexTaskRunner(
                state.task_id,
                state.epoch,
                session,
                lambda code: self._fail_session(state.task_id, code),
            )
            self._runners[state.task_id] = runner
            self._sessions[state.task_id] = session
            self._options[state.task_id] = options
            self._codes[state.task_id] = "READY_CODEX"
            set_stop = getattr(session, "set_stop_handler", None)
            if callable(set_stop):
                task_id = state.task_id
                set_stop(lambda code: self._stop_in_background(task_id, code))
            set_partial = getattr(session, "set_partial_handler", None)
            if callable(set_partial):
                task_id, epoch = state.task_id, state.epoch
                set_partial(lambda piece: self._answer_partial(task_id, epoch, piece))
            set_user_input = getattr(session, "set_user_input_handler", None)
            if callable(set_user_input):
                identity = TaskIdentity(task_id=state.task_id, epoch=state.epoch)
                set_user_input(lambda: self._record_user_input(identity))
            snapshot = await self.snapshot(state.task_id)
            self._check_creation_allowed()
            adopted = True
            return snapshot
        finally:
            if not adopted:
                discarded = self._runners.pop(state.task_id, None)
                if discarded is not None:
                    discarded.close()
                self._options.pop(state.task_id, None)
                self._codes.pop(state.task_id, None)
                try:
                    await self._close_session(state.task_id)
                finally:
                    if created:
                        await self._halt_store_task(state.task_id)

    async def snapshot(self, task_id: str) -> TaskProjection:
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != runner.epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        options = self._options[task_id]
        return TaskProjection(
            task_id,
            state.epoch,
            state.status,
            approval_id=None if self._approvals is None else self._approvals.approval_for(task_id),
            model_mode=options.mode,
            model_id=options.model,
            model_ready=self._codes[task_id] == "READY_CODEX"
            and state.status is TaskStatus.RUNNING,
            connection_code=self._codes[task_id],
            tools_available=self._tools_available,
            can_advance=runner.can_advance,
            can_accept_message=runner.can_accept_message and state.status is TaskStatus.RUNNING,
            runner_state=runner.runtime_status,
            label=state.high_watermark,
            unclassified=state.unclassified,
        )

    async def _halt_store_task(self, task_id: str) -> None:
        state = await self.store.load_task(task_id)
        if state.status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED):
            return
        updated = await self.store.set_task_status(
            task_id,
            TaskStatus.HALTED,
            expected_row_revision=state.row_revision,
            at_utc=datetime.now(UTC),
            reason=Reason.TASK_NOT_RUNNING,
        )
        if (
            updated.task_id != task_id
            or updated.epoch != state.epoch
            or updated.status is not TaskStatus.HALTED
        ):
            raise SessionRuntimeError("SESSION_STOP_UNCONFIRMED")

    async def _fail_session(self, task_id: str, code: str) -> None:
        self._codes[task_id] = code
        runner = self._runners[task_id]
        runner.close()
        try:
            await self._system_stop(task_id, runner.epoch, code)
        finally:
            await self._close_session(task_id, interrupt=True)

    async def _system_stop(self, task_id: str, epoch: int, code: str) -> None:
        """STOP-01: Guard stops the Task; the session is closed after this call.

        `stop_task` blocks dispatch before it waits for the Task lock, so an
        in-flight (shielded) submit is refused at its next check. Its Store write
        and audit happen under that lock, so A must not write HALTED itself: a
        competing write would fail Guard's commit and halt every Task. The wait
        runs in the background and the Task is republished when it settles. If
        it fails, Guard is halted globally and the Task shows that the stop was
        not confirmed rather than applied.
        """
        system = self._system
        if system is None:
            await self._halt_store_task(task_id)
            return
        identity = TaskIdentity(task_id=task_id, epoch=epoch)

        async def stop() -> None:
            try:
                await system.stop_task(identity, stop_reason(code))
            except (SystemControlError, StoreError):
                self._codes[task_id] = "SESSION_STOP_UNCONFIRMED"
                raise
            finally:
                if not self._closed:
                    self._task_changed(task_id)

        stopping = asyncio.ensure_future(stop())
        self._cleanup_operations.add(stopping)
        stopping.add_done_callback(self._consume_cleanup)
        await asyncio.sleep(0)  # let stop_task block dispatch now

    def _record_user_input(self, identity: TaskIdentity) -> None:
        """Audit that Codex asked the user (answered empty); never its text."""
        system = self._system
        if system is None or self._closed:
            return
        recording = asyncio.ensure_future(system.record_request_user_input(identity))
        self._cleanup_operations.add(recording)
        recording.add_done_callback(self._consume_cleanup)

    async def change_server_access(
        self, server_id: str, allowed: tuple[str, ...], denied: tuple[str, ...]
    ) -> PolicyView:
        """B3.10: prepare -> save -> apply; then stop the sessions Guard stopped.

        The saved document is the only new current document. If saving fails
        nothing is applied. If applying fails Guard is halted and this raises.
        """
        assembled = self._assembled
        document = self._profile_document
        saver = self._profile_saver
        task_source = self._task_source
        if (
            assembled is None
            or document is None
            or saver is None
            or task_source is None
            or self._closed
        ):
            raise SessionRuntimeError("POLICY_CHANGE_UNAVAILABLE")
        if set(allowed) & set(denied):
            raise SessionRuntimeError("POLICY_CHANGE_INVALID")
        async with self._policy_lock:
            try:
                change = prepare_server_access_change(
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
            if not change.changed:
                return policy_view(assembled, change_available=True)
            if await saver(change) is not True:
                raise SessionRuntimeError("POLICY_SAVE_FAILED")
            self._profile_document = change.document
            try:
                result = await assembled.guard.apply_policy_change(
                    change,
                    AccessRulesChecker(change.loaded_profile.access_rules),
                    task_source=task_source,
                )
            except PolicyChangeError:
                raise SessionRuntimeError("POLICY_APPLY_FAILED") from None
            # New Task rows and the policy screen follow the applied Profile.
            assembled.loaded = change.loaded_profile
            self._guard_settings = change.loaded_profile.guard_settings
            for task_id in result.stopped_task_ids:
                runner = self._runners.get(task_id)
                if runner is None:
                    continue
                self._codes[task_id] = "POLICY_CHANGED"
                runner.close()
                try:
                    await self._close_session(task_id, interrupt=True)
                finally:
                    self._task_changed(task_id)
            return policy_view(assembled, change_available=True)

    async def _halt_task(self, task_id: str) -> None:
        """A human stop through Guard's human channel when one is assembled.

        Writing HALTED to the Store behind Guard's back makes a still running
        (shielded) submit fail its own Store write, which Guard treats as a
        global halt for every Task. Guard's cancel records the stop intent at
        once, so the in-flight submit is denied as TASK_NOT_RUNNING at its next
        check; the cancel itself waits for the Task lock in the background.
        Session failures use STOP-01 instead (`_system_stop`).
        """
        assembled = self._assembled
        if assembled is not None:
            control = assembled.guard.human_control(assembled.operator_id)

            # Waiting for cancel here would tie this stop to the in-flight
            # submit holding the Task lock.
            async def cancel() -> None:
                await control.cancel(task_id)

            stopping = asyncio.ensure_future(cancel())
            self._cleanup_operations.add(stopping)
            stopping.add_done_callback(self._consume_cleanup)
            await asyncio.sleep(0)  # let cancel set the stop intent now
        await self._halt_store_task(task_id)

    async def stop_session(self, task_id: str, epoch: int, action: ControlAction) -> None:
        if action not in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            raise SessionRuntimeError("CONTROL_UNAVAILABLE")
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != epoch or runner.epoch != epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        runner.close()  # Invalidate late responses without waiting for the send lock.
        try:
            await self._halt_task(task_id)
        finally:
            await self._close_session(task_id, interrupt=True)

    async def _bounded_cleanup(self, action: Callable[[], Awaitable[object]]) -> bool:
        async def invoke() -> None:
            if await action() is not None:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")

        operation = asyncio.create_task(invoke())
        self._cleanup_operations.add(operation)
        operation.add_done_callback(self._consume_cleanup)
        try:
            done, _ = await asyncio.wait({operation}, timeout=SESSION_CLEANUP_TIMEOUT_S)
            return bool(done) and not operation.cancelled() and operation.exception() is None
        finally:
            if not operation.done():
                operation.cancel()

    def _consume_cleanup(self, operation: asyncio.Task[None]) -> None:
        self._cleanup_operations.discard(operation)
        if not operation.cancelled():
            operation.exception()

    async def _close_session(self, task_id: str, *, interrupt: bool = False) -> None:
        session = self._sessions.get(task_id)
        resources = self._resources.get(task_id)
        if session is None and resources is None:
            return
        running = self._cleanup_outcomes.get(task_id)
        if running is not None:
            # A concurrent stop/close adopts the in-flight cleanup's result
            # instead of reporting it unconfirmed or running it a second time.
            if not await asyncio.shield(running):
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            return
        if task_id in self._cleanup_started:
            # An earlier cleanup ended unconfirmed; it is never retried.
            raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        self._cleanup_started.add(task_id)
        outcome = asyncio.get_running_loop().create_future()
        self._cleanup_outcomes[task_id] = outcome
        complete = False
        try:
            interrupted = not interrupt or session is None
            closed = session is None
            if session is not None:
                interrupt_session = getattr(session, "interrupt", None)
                close_session = getattr(session, "aclose", None)
                try:
                    if interrupt and callable(interrupt_session):
                        interrupted = await self._bounded_cleanup(interrupt_session)
                finally:
                    if callable(close_session):
                        closed = await self._bounded_cleanup(close_session)
            confirmed = cleaned = resources is None
            preparation = self._preparation
            if resources is not None and preparation is not None:

                async def confirm() -> None:
                    nonlocal confirmed
                    confirmed = await preparation.confirm_exit(resources) is True

                confirmation_finished = await self._bounded_cleanup(confirm)
                # No removal until termination is positively confirmed. If a
                # timed-out callback later returns True, this path stays closed.
                if closed and confirmation_finished and confirmed:
                    cleaned = await self._bounded_cleanup(
                        lambda: preparation.cleanup_task(resources)
                    )
                    if cleaned:
                        self._resources.pop(task_id, None)
            # interrupt is a protocol courtesy; that the process has exited is
            # what confirm_exit proves. A lost or slow interrupt reply alone
            # must not mark every future session as unsafe (A88 QA).
            del interrupted
            complete = closed and confirmed and cleaned
            if not complete:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            self._sessions.pop(task_id, None)
            self._cleanup_started.discard(task_id)
        finally:
            self._cleanup_outcomes.pop(task_id, None)
            outcome.set_result(complete)
            if not complete:
                self._cleanup_unconfirmed = True
                self._codes[task_id] = "CODEX_CLEANUP_UNCONFIRMED"

    async def close(self) -> None:
        if self._closed:
            if self._cleanup_unconfirmed:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            return
        self._closed = True
        # Bridge normally cancels creation first; direct callers get the same
        # ownership rule so no late factory result can outlive the Store.
        creating = tuple(task for task in self._creating if task is not asyncio.current_task())
        for task in creating:
            task.cancel()
        unfinished_creation = False
        if creating:
            _, pending = await asyncio.wait(creating, timeout=SESSION_CLEANUP_TIMEOUT_S)
            unfinished_creation = bool(pending)
            self._defer_store_close = unfinished_creation
        try:
            for runner in self._runners.values():
                runner.close()
            # Start every cleanup independently so a stalled Task cannot stop
            # another session from receiving interrupt/close.
            results = await asyncio.gather(
                *(
                    self._close_session(task_id, interrupt=True)
                    for task_id in tuple(self._sessions.keys() | self._resources.keys())
                    # A pending factory can still launch a process. Its creator
                    # exclusively owns cleanup until it returns/fails, even if
                    # shutdown's bounded wait has already elapsed.
                    if task_id not in self._creating_ids.values()
                ),
                return_exceptions=True,
            )
            if (
                self._cleanup_unconfirmed
                or unfinished_creation
                or any(isinstance(result, BaseException) for result in results)
            ):
                self._cleanup_unconfirmed = True
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        finally:
            self._runners.clear()
            self._options.clear()
            self._codes.clear()
            try:
                if self._assembled is not None:
                    await self._assembled.aclose()
            finally:
                if not self._defer_store_close or not self._creating:
                    self._close_store_once()

    def _close_store_once(self) -> None:
        close, self._close_store = self._close_store, None
        if close is not None:
            close()

    def _stop_in_background(self, task_id: str, code: str) -> None:
        """A session stopped outside send(): stop its Task like a failed send."""
        if self._closed or task_id not in self._runners:
            return

        async def stop() -> None:
            try:
                await self._fail_session(task_id, code)
            finally:
                self._task_changed(task_id)

        operation = asyncio.ensure_future(stop())
        self._cleanup_operations.add(operation)
        operation.add_done_callback(self._consume_cleanup)

    def set_task_listener(self, listener: Callable[[str], None] | None) -> None:
        """The bridge's backend-loop hook to republish a Task outside a command."""
        self._task_listener = listener
        if self._approvals is not None:
            self._approvals.set_listener(listener)

    def _task_changed(self, task_id: str) -> None:
        if self._task_listener is not None:
            self._task_listener(task_id)

    def set_partial_listener(self, listener: Callable[[str, int, str], None] | None) -> None:
        """The bridge's backend-loop hook for streamed answer pieces (A90)."""
        self._partial_listener = listener

    def _answer_partial(self, task_id: str, epoch: int, piece: str) -> None:
        if not self._closed and self._partial_listener is not None:
            self._partial_listener(task_id, epoch, piece)

    def bindings(self) -> BackendBindings:
        assembled = self._assembled
        return BackendBindings(
            self.runner_for_task,
            None if assembled is None else assembled.guard.human_control(assembled.operator_id),
            close=self.close,
            snapshot=self.snapshot,
            discard_queued=None if assembled is None else assembled.transport.discard_queued,
            create_session=self.create_session,
            stop_session=self.stop_session,
            session_blocker=self.creation_blocker,
            policy=None
            if assembled is None
            else policy_view(assembled, change_available=self._policy_change_available),
            task_events=self.set_task_listener,
            answer_partials=self.set_partial_listener,
            policy_access=self.change_server_access if self._policy_change_available else None,
        )

    @property
    def _policy_change_available(self) -> bool:
        return (
            self._assembled is not None
            and self._profile_document is not None
            and self._profile_saver is not None
            and self._task_source is not None
        )


async def bootstrap_product(
    db_path: str,
    *,
    session_factory: CodexSessionFactory | None = None,
    preparation: SessionPreparation | None = None,
    product: ProductInputs | None = None,
    profile_saver: ProfileSaver | None = None,
) -> BackendBindings:
    """Product start. `product` comes from B3.11 file loading and C's launcher/Registry."""

    store = SQLiteStore(db_path, audit_id_factory=lambda: f"audit-{uuid4().hex}")
    assembled: AssembledGuard | None = None
    approvals: ApprovalOutcomes | None = None
    if product is not None:
        try:
            assembled = await assemble_guard(store, product)
        finally:
            if assembled is None:
                store.close()
        approvals = ApprovalOutcomes()
        if session_factory is None:
            session_factory = codex_session_factory(assembled, approvals)
    runtime = SessionRuntime(
        store,
        session_factory=session_factory,
        preparation=preparation,
        close_store=store.close,
        assembled=assembled,
        approvals=approvals,
        profile_document=None if product is None else product.profile_document,
        profile_saver=profile_saver,
    )
    adopted = False
    try:
        if preparation is not None:
            await runtime._check_startup()
        bindings = runtime.bindings()
        adopted = True
        return bindings
    finally:
        if not adopted:
            await runtime.close()
