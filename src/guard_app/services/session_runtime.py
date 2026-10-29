"""Task lifecycle: create, run, stop and confirm cleanup of Codex sessions.

Product startup lives in product_startup; bootstrap_product remains re-exported
for existing entry-point and test callers. Runtime collaborators stay private.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from guard_app.adapters.codex_protocol import build_dynamic_tools
from guard_app.domain.contracts import TaskIdentity
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import DispatchError, StoreError
from guard_app.domain.models import TaskState
from guard_app.ports import RegistryStore, SessionAudit, Store, StoreLifecycle
from guard_app.security.ids import new_id
from guard_app.services.account_runtime import AccountAccess, ProductAccount
from guard_app.services.bridge_registration import RegistrationCommands
from guard_app.services.bridge_types import (
    BackendBindings,
    BackendCleanupError,
    ControlAction,
    DestinationEdit,
    SessionOptions,
    TaskProjection,
)
from guard_app.services.catalog import GuardSettings, ServerSettings
from guard_app.services.codex_composition import app_version
from guard_app.services.guard import PolicyTaskSource, SystemControl
from guard_app.services.guard_relay import ApprovalOutcomes
from guard_app.services.policy_view import PolicyView
from guard_app.services.product_assembly import (
    AssembledGuard,
)
from guard_app.services.product_settings import (
    SETTINGS_LOADED,
)
from guard_app.services.product_startup import bootstrap_product as bootstrap_product
from guard_app.services.registry import ServerRegistry, ToolListingView
from guard_app.services.runner import CodexSession, CodexTaskRunner
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_cleanup import BackgroundWork, CleanupLimits, SessionCleanup
from guard_app.services.session_execution import ExecutionGate
from guard_app.services.session_policy import ProfileSaver, TaskPolicy
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    TaskResources,
)
from guard_app.services.session_startup import (
    StartupGate,
    call_preparation,
    require_configuration,
)
from guard_app.services.session_stops import StopChannel

# A-local proposal only: A's factory must use C's executable/config/model hash
# verification and separate app-server, then perform account/read and relay setup.
# Receipts and callbacks are A-local composition seams, not final B/C APIs.
type CodexSessionFactory = Callable[
    [TaskState, SessionOptions, TaskResources], Awaitable[CodexSession]
]


@dataclass(slots=True)
class _RunningTask:
    """An adopted Task: its runner, its options, and the connection code shown for it."""

    runner: CodexTaskRunner
    options: SessionOptions
    code: str = "READY_CODEX"


class _TaskEvents:
    """The bridge's backend-loop hooks: republish a Task, stream answer pieces (A90)."""

    def __init__(self, approvals: ApprovalOutcomes | None, admission: SessionAdmission) -> None:
        self._approvals = approvals
        self._admission = admission
        self._task_listener: Callable[[str], None] | None = None
        self._partial_listener: Callable[[str, int, str], None] | None = None

    def set_task_listener(self, listener: Callable[[str], None] | None) -> None:
        self._task_listener = listener
        if self._approvals is not None:
            self._approvals.set_listener(listener)

    def task_changed(self, task_id: str) -> None:
        if self._task_listener is not None:
            self._task_listener(task_id)

    def set_partial_listener(self, listener: Callable[[str, int, str], None] | None) -> None:
        self._partial_listener = listener

    def answer_partial(self, task_id: str, epoch: int, piece: str) -> None:
        if not self._admission.closed and self._partial_listener is not None:
            self._partial_listener(task_id, epoch, piece)


class _Creations:
    """create_session calls in flight, each with its Task once it is named.

    A creator exclusively owns its Task's cleanup until it returns, even past
    shutdown's bounded wait: a pending factory can still launch a process. The
    Store then stays open until the last such creator returns.
    """

    def __init__(self, store: StoreLifecycle | None) -> None:
        self._creators: dict[asyncio.Task[object], str | None] = {}
        self._store = store
        self._store_close_deferred = False

    def enter(self, creator: asyncio.Task[object]) -> None:
        self._creators[creator] = None

    def name(self, creator: asyncio.Task[object], task_id: str) -> None:
        self._creators[creator] = task_id

    def leave(self, creator: asyncio.Task[object]) -> None:
        self._creators.pop(creator, None)
        if self._store_close_deferred and not self._creators:
            self._close_store_once()

    def owns(self, task_id: str) -> bool:
        return task_id in self._creators.values()

    @property
    def active(self) -> bool:
        return bool(self._creators)

    async def cancel(self, timeout_s: float) -> bool:
        """Cancel every other creator. False when one still runs after the wait."""
        creating = tuple(task for task in self._creators if task is not asyncio.current_task())
        for task in creating:
            task.cancel()
        if not creating:
            return True
        _, pending = await asyncio.wait(creating, timeout=timeout_s)
        self._store_close_deferred = bool(pending)
        return not pending

    def close_store(self) -> StoreLifecycle | None:
        """Close the Store now, unless a creator still running will close it.

        Returns the Store this call asked to close. Its close only starts the
        writer's shutdown (#58); the caller confirms it.
        """
        if not self._store_close_deferred or not self._creators:
            return self._close_store_once()
        return None

    def _close_store_once(self) -> StoreLifecycle | None:
        store, self._store = self._store, None
        if store is not None:
            store.close()
        return store


def _system_control(store: Store, assembled: AssembledGuard | None) -> SystemControl | None:
    # STOP-01: session stops go through Guard's system channel, audited in the
    # Store (SQLiteStore implements SessionAudit), not the human channel.
    if assembled is None or not callable(getattr(store, "record_session_event", None)):
        return None
    return assembled.guard.system_control(cast(SessionAudit, store))


def _task_source(store: Store) -> PolicyTaskSource | None:
    # Every Task this Guard governs, for apply_policy_change (SQLiteStore.list_tasks).
    list_tasks = getattr(store, "list_tasks", None)
    return cast(PolicyTaskSource, list_tasks) if callable(list_tasks) else None


class SessionRuntime:
    """Codex sessions for Guard Tasks: create, run, stop and clean up (10.4).

    The bridge reaches it only through `bindings()`. Each collaborator keeps one
    rule: SessionAdmission whether new sessions start, StartupGate C's
    preparation, TaskPolicy the Profile for new rows, SessionCleanup what a Task
    launched, StopChannel where a stop is recorded. `system_control` and
    `cleanup_limits` default to Guard's system channel and the reviewed bounds.
    `account` gives the login screen its own app-server (A52); logout waits
    until no session may run and blocks new ones meanwhile (#21). `owned_store`
    is the Store shutdown closes, confirmed closed before close returns (#58).
    """

    def __init__(
        self,
        store: Store,
        *,
        session_factory: CodexSessionFactory | None = None,
        preparation: SessionPreparation | None = None,
        owned_store: StoreLifecycle | None = None,
        guard_settings: GuardSettings | None = None,
        assembled: AssembledGuard | None = None,
        approvals: ApprovalOutcomes | None = None,
        profile_document: Mapping[str, object] | None = None,
        profile_saver: ProfileSaver | None = None,
        settings_status: str | None = None,
        system_control: SystemControl | None = None,
        cleanup_limits: CleanupLimits | None = None,
        account: AccountAccess | None = None,
        execution_gate: ExecutionGate | None = None,
    ) -> None:
        self.store = store
        self._execution_gate = execution_gate
        self._execution_timer: asyncio.TimerHandle | None = None
        self._execution_stopped = False
        self._execution_watch_started = False
        self._assembled = assembled
        self._settings_status = settings_status
        self._approvals = approvals
        self._session_factory = session_factory
        self._limits = CleanupLimits() if cleanup_limits is None else cleanup_limits
        self._admission = SessionAdmission()
        self._startup = StartupGate(preparation, self._admission)
        self._policy = TaskPolicy(
            assembled.settings if assembled is not None else guard_settings,
            self._admission,
            assembled=assembled,
            document=profile_document,
            saver=profile_saver,
            task_source=_task_source(store),
            # Without a Profile, a settings-file failure says more than "not connected".
            missing_code=(
                settings_status
                if settings_status is not None and settings_status != SETTINGS_LOADED
                else "GUARD_PROFILE_UNAVAILABLE"
            ),
        )
        self._work = BackgroundWork()
        # Guard's records of session stops and user-input requests, apart from
        # cleanup so shutdown waits for exactly them before the Store closes.
        self._records = BackgroundWork()
        self._stops_unconfirmed = False
        self._store_unconfirmed = False
        self._cleanup = SessionCleanup(
            preparation,
            self._admission,
            self._limits,
            self._work,
            lambda task_id: self._set_code(task_id, "CODEX_CLEANUP_UNCONFIRMED"),
        )
        system = _system_control(store, assembled) if system_control is None else system_control
        self._stops = StopChannel(store, system, self._admission, self._records)
        self._events = _TaskEvents(approvals, self._admission)
        self._creations = _Creations(owned_store)
        self._running: dict[str, _RunningTask] = {}
        # What Codex actually receives: Guard tools minus Codex name-rule exclusions.
        # Servers whose tool list changed this run drop out at snapshot (LIST-01).
        self._tool_refs: frozenset[str] = (
            frozenset()
            if assembled is None
            else build_dynamic_tools(assembled.tool_views).tool_refs
        )
        self._account = (
            None
            if account is None
            else ProductAccount(account, version=app_version(), sessions_alive=self._sessions_alive)
        )
        if execution_gate is not None:
            self.start_execution_watch()

    def connect_execution_gate(self, gate: ExecutionGate) -> None:
        """Connect Store authority after activate_run ACK, before publishing bindings."""
        self._execution_gate = gate
        self.check_execution()

    def connect_session_factory(self, factory: CodexSessionFactory) -> None:
        """Finish Codex composition before any Task is admitted."""
        self._session_factory = factory

    def start_execution_watch(self) -> None:
        """Start the runtime-owned revision watch once; close cancels its timer."""
        if not self._execution_watch_started:
            self._execution_watch_started = True
            self._watch_execution()

    def registration_commands(
        self,
        registry: ServerRegistry,
        store: RegistryStore,
        collect: Callable[[ServerSettings], Awaitable[ToolListingView]],
        *,
        saver: ProfileSaver | None = None,
    ) -> RegistrationCommands:
        """Bind registration to the same policy lock without exposing TaskPolicy."""
        if saver is not None:
            self._policy.connect_registration_saver(saver)
        return RegistrationCommands(
            registry,
            store,
            self._policy,
            collect,
            self.close_policy_stopped,
            self.check_execution,
        )

    def execution_allowed(self) -> bool:
        allowed = self._execution_gate is None or self._execution_gate.allowed()
        if not allowed and not self._execution_stopped and not self._admission.closed:
            self._execution_stopped = True
            if self._admission.restart_code is None:
                self._admission.require_restart("CODEX_STORE_HALTED")
            for task_id, running in tuple(self._running.items()):
                running.runner.close()
                self._stop_in_background(task_id, "CODEX_STORE_HALTED")
        return allowed

    def _watch_execution(self) -> None:
        # SQL-independent revision watch also interrupts idle/approval-waiting sessions.
        if not self._admission.closed and self.execution_allowed():
            self._execution_timer = asyncio.get_running_loop().call_later(
                0.1, self._watch_execution
            )

    def check_execution(self) -> None:
        if not self.execution_allowed():
            raise SessionRuntimeError(self._admission.refusal() or "CODEX_STORE_HALTED")

    @property
    def pending_cleanup(self) -> frozenset[str]:
        """Tasks whose session or C resources are still owned: cleanup not yet proven."""
        return self._cleanup.pending

    def _sessions_alive(self) -> bool:
        """Whether any Task app-server may still run: one is being created or not yet cleaned up."""
        return self._creations.active or bool(self._cleanup.pending)

    def _signing_out(self) -> bool:
        return self._account is not None and self._account.signing_out

    def runner_for_task(self, task_id: str) -> CodexTaskRunner:
        running = self._running.get(task_id)
        if self._admission.closed or running is None:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        return running.runner

    def creation_blocker(self) -> str | None:
        """What is already known to refuse a new session, in creation order.

        None does not promise success: L2 and per-Task checks run on create.
        """
        self.execution_allowed()
        for blocker in (self._admission.refusal(), self._startup.blocker()):
            if blocker is not None:
                return blocker
        if self._signing_out():
            return "CODEX_SIGNING_OUT"
        if self._session_factory is None:
            return "CODEX_SESSION_UNAVAILABLE"
        return self._policy.blocker()

    async def prepare_startup(self) -> None:
        """Run the app-wide preparation once at product start (A101).

        A refusal stays the session blocker with its fixed code rather than
        failing the whole app: 10.4 refuses Task starts, and the approval and
        policy screens remain usable while L2 or the launcher is missing.
        """
        with contextlib.suppress(SessionRuntimeError):
            await self._startup.check()

    async def create_session(self, options: SessionOptions) -> TaskProjection:
        self.check_execution()
        if self._admission.closed or type(options) is not SessionOptions:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        creator = asyncio.current_task()
        if creator is None:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        self._creations.enter(creator)
        try:
            # Checked with nothing awaited since enter: a logout that starts
            # later sees this creator and is refused (#21).
            if self._signing_out():
                raise SessionRuntimeError("CODEX_SIGNING_OUT")
            return await self._create_session(options, creator)
        finally:
            self._creations.leave(creator)

    async def _create_session(
        self, options: SessionOptions, creator: asyncio.Task[object]
    ) -> TaskProjection:
        expected = await self._startup.check()
        await self._startup.verify(expected)
        factory = self._session_factory
        if factory is None:
            raise SessionRuntimeError("CODEX_SESSION_UNAVAILABLE")
        state = self._policy.task_state(new_id("session"))
        self._creations.name(creator, state.task_id)
        created = adopted = False
        try:
            session = await self._launch(state, options, expected, factory)
            # B3.12 (A105): the row takes the Profile in force when it is written.
            # The session only knows task_id and epoch, which stay the same.
            async with self._policy.lock:
                self._admission.check()
                self.check_execution()
                state = self._policy.task_state(state.task_id)
                await self.store.create_task(state, created_at_utc=datetime.now(UTC))
                created = True
                confirmed = await self.store.load_task(state.task_id)
            if confirmed != state:
                raise StoreError()
            self._admission.check()
            self.check_execution()
            self._adopt(state, session, options)
            snapshot = await self.snapshot(state.task_id)
            self._admission.check()
            adopted = True
            return snapshot
        finally:
            if not adopted:
                await self._discard(state.task_id, halt_row=created)

    async def _launch(
        self,
        state: TaskState,
        options: SessionOptions,
        expected: ConfigurationReceipt,
        factory: CodexSessionFactory,
    ) -> CodexSession:
        """C's Task resources, then the Codex session. No Store row exists yet."""
        self.check_execution()
        resources = self._cleanup.claim_resources(
            state.task_id, await self._startup.prepare_task(state.task_id, expected)
        )
        self._admission.check()
        require_configuration(resources.configuration, expected)
        # Recheck after resource setup, immediately before session creation.
        await self._startup.verify(expected)
        self.check_execution()
        # No Store RUNNING row exists while C's secure launch is unconfirmed.
        session = await call_preparation(lambda: factory(state, options, resources))
        self._cleanup.claim_session(state.task_id, session)
        self._admission.check()
        return session

    def _adopt(self, state: TaskState, session: CodexSession, options: SessionOptions) -> None:
        """Give the confirmed Task its runner and route the session's reports to it."""
        task_id, epoch = state.task_id, state.epoch
        runner = CodexTaskRunner(
            task_id,
            epoch,
            session,
            lambda code: self._fail_session(task_id, code),
            execution_allowed=self.execution_allowed,
        )
        self._running[task_id] = _RunningTask(runner, options)
        set_stop = getattr(session, "set_stop_handler", None)
        if callable(set_stop):
            set_stop(lambda code: self._stop_in_background(task_id, code))
        set_partial = getattr(session, "set_partial_handler", None)
        if callable(set_partial):
            set_partial(lambda piece: self._events.answer_partial(task_id, epoch, piece))
        set_user_input = getattr(session, "set_user_input_handler", None)
        if callable(set_user_input):
            identity = TaskIdentity(task_id=task_id, epoch=epoch)
            set_user_input(lambda: self._stops.record_user_input(identity))

    async def _discard(self, task_id: str, *, halt_row: bool) -> None:
        """Undo a creation that was not adopted: runner, session and resources, then the row."""
        discarded = self._running.pop(task_id, None)
        if discarded is not None:
            discarded.runner.close()
        try:
            await self._cleanup.close(task_id)
        finally:
            if halt_row:
                await self._stops.halt_row(task_id)

    async def snapshot(self, task_id: str) -> TaskProjection:
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != runner.epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        running = self._running[task_id]
        assembled = self._assembled
        return TaskProjection(
            task_id,
            state.epoch,
            state.status,
            approval_id=None if self._approvals is None else self._approvals.approval_for(task_id),
            model_mode=running.options.mode,
            model_id=running.options.model,
            model_ready=running.code == "READY_CODEX" and state.status is TaskStatus.RUNNING,
            connection_code=running.code,
            tools_available=assembled is not None
            and any(map(assembled.offers_tool, self._tool_refs)),
            can_advance=runner.can_advance,
            can_accept_message=runner.can_accept_message and state.status is TaskStatus.RUNNING,
            runner_state=runner.runtime_status,
            label=state.high_watermark,
            unclassified=state.unclassified,
        )

    def _set_code(self, task_id: str, code: str) -> None:
        running = self._running.get(task_id)
        if running is not None:
            running.code = code

    async def _fail_session(self, task_id: str, code: str) -> None:
        running = self._running[task_id]
        running.code = code
        running.runner.close()

        def settled(unconfirmed: bool) -> None:
            if unconfirmed:
                self._set_code(task_id, "SESSION_STOP_UNCONFIRMED")
            if not self._admission.closed:
                self._events.task_changed(task_id)

        identity = TaskIdentity(task_id=task_id, epoch=running.runner.epoch)
        try:
            await self._stops.stop(identity, code, settled)
        finally:
            await self._cleanup.close(task_id, interrupt=True)

    def _stop_in_background(self, task_id: str, code: str) -> None:
        """A session stopped outside send(): stop its Task like a failed send."""
        if self._admission.closed or task_id not in self._running:
            return

        async def stop() -> None:
            try:
                await self._fail_session(task_id, code)
            finally:
                self._events.task_changed(task_id)

        self._work.start(stop())

    async def change_server_access(
        self, server_id: str, allowed: tuple[str, ...], denied: tuple[str, ...]
    ) -> PolicyView:
        """B3.10 server access change (TaskPolicy); then the stopped sessions are closed."""
        return await self._policy.change_server_access(
            server_id, allowed, denied, self.close_policy_stopped
        )

    async def destinations(self, edit: DestinationEdit | None) -> PolicyView:
        return await self._policy.destinations(edit, self.close_policy_stopped)

    async def close_policy_stopped(self, task_ids: tuple[str, ...]) -> None:
        """Close the sessions of Tasks Guard stopped for a change, under the policy lock."""
        for task_id in task_ids:
            running = self._running.get(task_id)
            if running is None:
                continue
            running.code = "POLICY_CHANGED"
            running.runner.close()
            try:
                await self._cleanup.close(task_id, interrupt=True)
            finally:
                self._events.task_changed(task_id)

    async def stop_session(self, task_id: str, epoch: int, action: ControlAction) -> None:
        """Close the Codex session of a Task a human stopped.

        With Guard assembled, the bridge has already stopped the Task through
        Guard's human channel (cancel/emergency halt), which records the stop
        under the Task lock with the operator in the audit. A writes nothing to
        the Store then: a second, unlocked HALTED write could collide with an
        in-flight submit's commit and halt every Task (PR #29 B review 5).
        Without Guard nothing else stops the Store Task, so it is written here.
        """
        if action not in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            raise SessionRuntimeError("CONTROL_UNAVAILABLE")
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != epoch or runner.epoch != epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        runner.close()  # Invalidate late responses without waiting for the send lock.
        try:
            if self._assembled is None:
                await self._stops.halt_row(task_id)
        finally:
            await self._cleanup.close(task_id, interrupt=True)

    async def close(self) -> None:
        if self._admission.closed:
            if self._admission.cleanup_unconfirmed:
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
            self._raise_if_closing_unconfirmed()
            return
        self._admission.close()
        if self._execution_timer is not None:
            self._execution_timer.cancel()
        # Bridge normally cancels creation first; direct callers get the same
        # ownership rule so no late factory result can outlive the Store.
        creations_finished = await self._creations.cancel(self._limits.session_s)
        try:
            for running in self._running.values():
                running.runner.close()
            # Start every cleanup independently so a stalled Task cannot stop
            # another session from receiving interrupt/close. A Task whose
            # creator is still running stays that creator's to clean up.
            results = await asyncio.gather(
                *(
                    self._cleanup.close(task_id, interrupt=True)
                    for task_id in self._cleanup.pending
                    if not self._creations.owns(task_id)
                ),
                self._close_account(),
                return_exceptions=True,
            )
            if (
                self._admission.cleanup_unconfirmed
                or not creations_finished
                or any(isinstance(result, BaseException) or result is False for result in results)
            ):
                self._admission.mark_cleanup_unconfirmed()
                raise SessionRuntimeError("CODEX_CLEANUP_UNCONFIRMED")
        finally:
            self._running.clear()
            try:
                self._stops_unconfirmed = not await self._close_guard()
            except DispatchError:
                # Transport owns launcher exit confirmation; retain refusal on repeated close.
                self._admission.mark_cleanup_unconfirmed()
                raise BackendCleanupError from None
            finally:
                self._store_unconfirmed = not await self._close_store()
        self._raise_if_closing_unconfirmed()

    def _raise_if_closing_unconfirmed(self) -> None:
        """Report, on every close, a stop record or Store close that was not confirmed."""
        if self._stops_unconfirmed:
            raise SessionRuntimeError("SESSION_STOP_UNCONFIRMED")
        if self._store_unconfirmed:
            raise SessionRuntimeError("STORE_CLOSE_UNCONFIRMED")

    async def _close_guard(self) -> bool:
        """Close Guard once the session stops it is recording have ended.

        Guard records a stop under the Task lock (STOP-01), in the background.
        Closing Guard halts it globally and a closed Store refuses the write, so
        a stop still waiting then ends as SystemControlError (#58 B review). The
        stops are waited for after the transport has settled reserved calls,
        while Guard still records. One that does not end in time fails against
        the halted Guard, and that too ends before the Store closes. False when
        a stop did not end within its bound.
        """
        recorded = False

        async def settle() -> None:
            nonlocal recorded
            recorded = await self._records.wait(self._limits.stop_record_s)

        if self._assembled is None:
            await settle()
        else:
            await self._assembled.aclose(settle=settle)
        ended = await self._records.wait(self._limits.session_s)
        return recorded and ended

    async def _close_store(self) -> bool:
        """Close the owned Store. False when its connection did not close in time.

        A creator still running closes the Store when it returns; shutdown has
        then already reported cleanup unconfirmed, so that close is not waited for.
        """
        closing = self._creations.close_store()
        return closing is None or await closing.wait_closed(self._limits.store_close_s)

    async def _close_account(self) -> bool:
        """End the login screen's app-server. False when its exit was not confirmed in time."""
        if self._account is None:
            return True
        try:
            return await asyncio.wait_for(self._account.aclose(), self._limits.confirm_exit_s)
        except TimeoutError:
            return False

    def bindings(self) -> BackendBindings:
        assembled = self._assembled
        available = self._policy.available
        return BackendBindings(
            self.runner_for_task,
            None if assembled is None else assembled.guard.human_control(assembled.operator_id),
            close=self.close,
            snapshot=self.snapshot,
            discard_queued=None if assembled is None else assembled.transport.discard_queued,
            create_session=self.create_session,
            stop_session=self.stop_session,
            session_blocker=self.creation_blocker,
            policy=None if assembled is None else self._policy.registration_policy_view(),
            task_events=self._events.set_task_listener,
            answer_partials=self._events.set_partial_listener,
            policy_access=self.change_server_access if available else None,
            policy_destinations=self.destinations if assembled is not None else None,
            settings_status=self._settings_status,
            account=self._account,
        )
