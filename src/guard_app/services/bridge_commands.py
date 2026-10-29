"""What each admitted command does against the trusted BackendBindings.

Backend loop only. Results leave as fixed codes and frozen public values.
A Task is shown only from a fresh Store snapshot; nothing here infers a
durable Task transition, and raw tool results never cross.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC
from typing import Any

from guard_app.core.snapshot import ApprovalSnapshot
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    Released,
    Withheld,
)
from guard_app.domain.enums import TaskStatus
from guard_app.services.approvals import HumanControl
from guard_app.services.bridge_bindings import BindingsOwner
from guard_app.services.bridge_ingress import ACCOUNT_ACTIONS, BridgeCommand
from guard_app.services.bridge_streams import AnswerStreams
from guard_app.services.bridge_types import (
    ApprovalReview,
    BackendBindings,
    ControlAction,
    ConversationEvent,
    RegistrationView,
    SessionCreated,
    SessionStartError,
    TaskProjection,
)
from guard_app.services.codex_account import AccountError, AccountView, CodexAccountPort
from guard_app.services.policy_view import PolicyView
from guard_app.services.runner import RunUpdate, TaskExecution

# A browser/device login left unfinished is cancelled after this long.
DEVICE_LOGIN_TIMEOUT_S = 15 * 60

_STOPS = (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT)
_KNOWN_RESULTS = (Denied, Pending, Released, Withheld, OutcomeUnknown)


@dataclass(frozen=True, slots=True)
class BridgeOutlet:
    """The bridge's Qt signal emitters; each takes one frozen public value."""

    task_changed: Callable[[TaskProjection], None]
    review_ready: Callable[[ApprovalReview], None]
    conversation_ready: Callable[[ConversationEvent], None]
    session_created: Callable[[SessionCreated], None]
    # A fixed blocker code, or "" when new sessions may be requested.
    session_capability: Callable[[str], None]
    account_changed: Callable[[AccountView], None]
    policy_changed: Callable[[PolicyView], None]
    registration_changed: Callable[[RegistrationView], None] = lambda view: None


class TaskViews:
    """Publishes Store projections of Tasks and whether new sessions may start."""

    def __init__(
        self,
        bindings: BindingsOwner,
        outlet: BridgeOutlet,
        stopped: Callable[[], bool],
        consume: Callable[[asyncio.Future[Any]], None],
    ) -> None:
        self._bindings = bindings
        self._out = outlet
        self._stopped = stopped
        self._consume = consume

    def publish_capability(self) -> None:
        bindings = self._bindings.current
        if bindings is None or bindings.create_session is None:
            code: str | None = "SESSION_CREATION_UNAVAILABLE"
        elif bindings.session_blocker is None:
            code = None
        else:
            code = bindings.session_blocker()
        self._out.session_capability(
            code if type(code) is str and code else "" if code is None else "SESSION_UNAVAILABLE"
        )

    def task_event(self, task_id: str) -> None:
        """Backend loop only: republish one Task without waiting for a command."""
        if self._stopped() or type(task_id) is not str or not task_id:
            return
        publish = asyncio.ensure_future(self.publish_after(task_id))
        publish.add_done_callback(self._consume)
        # A failed session can change what new sessions may do.
        self.publish_capability()

    async def publish_after(self, task_id: str) -> None:
        if not self._stopped():
            await self.publish(task_id)

    async def publish(self, task_id: str) -> None:
        bindings = self._bindings.current
        if bindings is None or bindings.snapshot is None:
            return
        snapshot = await bindings.snapshot(task_id)
        if self._stopped():
            return
        if type(snapshot) is not TaskProjection or snapshot.task_id != task_id:
            raise ValueError("Invalid public Task projection")
        runner = bindings.runner_for_task(task_id)
        if isinstance(runner, TaskExecution):
            if snapshot.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
                # Invalidate late model responses and drop terminal Task history.
                runner.close()
            snapshot = replace(
                snapshot,
                can_accept_message=(
                    snapshot.status is TaskStatus.RUNNING
                    and snapshot.model_ready
                    and runner.task_id == snapshot.task_id
                    and getattr(runner, "epoch", None) == snapshot.epoch
                    and bool(getattr(runner, "can_accept_message", False))
                ),
                runner_state=getattr(runner, "runtime_status", ""),
            )
        self._out.task_changed(snapshot)


class AccountCommands:
    """Codex login on the C-launched app-server (A52) and server access (B3.10)."""

    def __init__(
        self,
        views: TaskViews,
        outlet: BridgeOutlet,
        stopped: Callable[[], bool],
        consume: Callable[[asyncio.Future[Any]], None],
    ) -> None:
        self._views = views
        self._out = outlet
        self._stopped = stopped
        self._consume = consume
        self._waits: set[asyncio.Future[None]] = set()
        self._account_lock = asyncio.Lock()
        self._pending_view: AccountView | None = None
        self._login_generation = 0
        self._browser_available = False

    def cancel_waits(self) -> None:
        self._login_generation += 1
        self._pending_view = None
        for waiter in tuple(self._waits):
            waiter.cancel()

    async def account(self, bindings: BackendBindings, command: BridgeCommand) -> str:
        # Only the RPC is serialized; the human wait never holds this lock or
        # a control worker. A second click cannot start a second callback.
        async with self._account_lock:
            return await self._account_request(bindings, command)

    async def _account_request(self, bindings: BackendBindings, command: BridgeCommand) -> str:
        account = bindings.account
        if account is None:
            self._out.account_changed(AccountView("UNAVAILABLE"))
            return "ACCOUNT_UNAVAILABLE"
        try:
            if self._pending_view is not None and command.action not in (
                "account_cancel_login",
                "account_logout",
            ):
                view = self._pending_view
            elif command.action == "account_read":
                view = await account.read()
            elif command.action == "account_login_api_key":
                view = await account.login_api_key(command.user_text or "")
            elif command.action in ("account_login_device", "account_login_browser"):
                view = await self._start_login(account, browser=command.action.endswith("browser"))
            elif command.action == "account_cancel_login":
                view = await self._cancel_login(account, command.target_id)
            else:
                if self._pending_view is not None:
                    await self._cancel_login(account, self._pending_view.login_id or "")
                try:
                    view = await account.logout()
                finally:
                    # New sessions were refused while the logout ran (#21).
                    self._views.publish_capability()
        except AccountError:
            view = AccountView("FAILED", browser_available=self._browser_available)
        self._browser_available = view.browser_available
        self._out.account_changed(view)
        return f"ACCOUNT_{view.state}"

    async def _start_login(self, account: CodexAccountPort, *, browser: bool) -> AccountView:
        view = await (account.start_browser_login() if browser else account.start_device_login())
        self._login_generation += 1
        if view.state in ("PENDING_DEVICE", "PENDING_BROWSER") and view.login_id is not None:
            self._pending_view = view
            waiter = asyncio.ensure_future(
                self._await_login(account, view.login_id, self._login_generation)
            )
            self._waits.add(waiter)
            waiter.add_done_callback(self._waits.discard)
            waiter.add_done_callback(self._consume)
        return view

    async def _cancel_login(self, account: CodexAccountPort, login_id: str) -> AccountView:
        pending = self._pending_view
        if pending is None or pending.login_id != login_id:
            return pending if pending is not None else await account.read()
        view = await account.cancel_login(login_id)
        self.cancel_waits()
        return replace(view, notice="LOGIN_CANCELLED")

    async def policy_access(self, bindings: BackendBindings, command: BridgeCommand) -> str:
        change = bindings.policy_access
        if change is None or command.access is None:
            return "POLICY_CHANGE_UNAVAILABLE"
        try:
            view = await change(command.target_id, *command.access)
        except SessionStartError as error:
            return error.code
        finally:
            # Stopped sessions change what a new session may do.
            self._views.publish_capability()
        if type(view) is not PolicyView:
            return "POLICY_CHANGE_UNCONFIRMED"
        self._out.policy_changed(view)
        return "POLICY_CHANGED"

    async def _await_login(self, account: CodexAccountPort, login_id: str, generation: int) -> None:
        expired = False
        try:
            async with asyncio.timeout(DEVICE_LOGIN_TIMEOUT_S):
                view = await account.wait_login(login_id)
        except TimeoutError:
            expired = True
            view = AccountView("LOGIN_NOT_COMPLETED", notice="LOGIN_TIMEOUT")
        except AccountError:
            view = AccountView("FAILED")
        async with self._account_lock:
            # Cancelling the waiter under this lock is the first guard. The
            # generation also drops a wait that returns after its cancel, for a
            # port whose wait absorbs cancellation (a shielded inner wait).
            if self._stopped() or generation != self._login_generation:
                return
            pending = self._pending_view
            if expired:
                try:
                    await account.cancel_login(login_id)
                except AccountError:
                    view = AccountView("FAILED")
            self._pending_view = None
            if pending is not None:
                view = replace(view, browser_available=pending.browser_available)
            self._out.account_changed(view)


@dataclass(slots=True)
class _ExecutionView:
    """Per-command refresh target, armed only after the handler's prerequisites.

    Approval routing may become known during the domain call, even on failure.
    Never infer the Task from the command target when that target is an approval.
    """

    task_id: str | None = None
    approval_id: str | None = None

    async def publish(self, views: TaskViews, pending: dict[str, tuple[str, int]]) -> None:
        task_id = self.task_id
        if task_id is None and self.approval_id is not None:
            routed = pending.get(self.approval_id)
            if routed is not None:
                task_id = routed[0]
        if task_id is not None:
            await views.publish_after(task_id)


class CommandExecutor:
    """Runs one admitted command and returns its fixed result code."""

    def __init__(
        self,
        bindings: BindingsOwner,
        views: TaskViews,
        accounts: AccountCommands,
        streams: AnswerStreams,
        outlet: BridgeOutlet,
    ) -> None:
        self._bindings = bindings
        self._views = views
        self._accounts = accounts
        self._streams = streams
        self._out = outlet
        # approval_id -> (task_id, epoch) of the run that waits on it.
        self._pending_runs: dict[str, tuple[str, int]] = {}

    async def execute(self, command: BridgeCommand) -> str:
        view = _ExecutionView()
        try:
            return await self._execute(command, view)
        finally:
            # Failed/unclassified runs and failed approvals must refresh from
            # Store too; the handler arms this only after its old prerequisites.
            await view.publish(self._views, self._pending_runs)

    async def _execute(self, command: BridgeCommand, execution: _ExecutionView) -> str:
        bindings = self._bindings.current
        if bindings is None:
            raise RuntimeError("BRIDGE_BINDINGS_UNAVAILABLE")
        if command.action == "registration":
            if bindings.registration is None or command.registration is None:
                view = RegistrationView("REG_UNAVAILABLE")
            else:
                view = await bindings.registration(command.registration)
            self._out.registration_changed(replace(view, command_id=command.command_id))
            if view.current_policy is not None:
                self._out.policy_changed(view.current_policy)
            self._views.publish_capability()
            return view.code
        if command.action == "new_session":
            return await self._new_session(bindings, command)
        if command.lane == "run":
            return await self._run(bindings, command, execution)
        if command.action in ACCOUNT_ACTIONS:
            return await self._accounts.account(bindings, command)
        if command.action == "policy_access":
            return await self._accounts.policy_access(bindings, command)
        control = bindings.human_control
        if control is None:
            return await self._stop_without_guard(bindings, command, execution)
        if command.action == ControlAction.REVIEW:
            return await self._review(control, command)
        if command.action in _STOPS:
            return await self._stop(bindings, control, command, execution)
        return await self._decide(bindings, control, command, execution)

    async def _new_session(self, bindings: BackendBindings, command: BridgeCommand) -> str:
        if bindings.create_session is None or command.options is None:
            return "SESSION_CREATION_UNAVAILABLE"
        try:
            created_snapshot = await bindings.create_session(command.options)
        except SessionStartError as error:
            return error.code
        finally:
            self._views.publish_capability()
        if type(created_snapshot) is not TaskProjection:
            return "SESSION_CREATION_UNCONFIRMED"
        self._out.session_created(
            SessionCreated(command.command_id, command.target_id, created_snapshot)
        )
        await self._views.publish_after(created_snapshot.task_id)
        return "SESSION_CREATED"

    async def _run(
        self, bindings: BackendBindings, command: BridgeCommand, view: _ExecutionView
    ) -> str:
        runner = bindings.runner_for_task(command.target_id)
        if not isinstance(runner, TaskExecution) or runner.task_id != command.target_id:
            return "RUNNER_UNAVAILABLE"
        view.task_id = command.target_id
        if command.action == "message":
            refused = await self._add_message(bindings, runner, command)
            if refused is not None:
                return refused
        return await self._advance(runner, command)

    async def _add_message(
        self, bindings: BackendBindings, runner: TaskExecution, command: BridgeCommand
    ) -> str | None:
        if bindings.snapshot is None:
            return "MESSAGE_UNAVAILABLE"
        state = await bindings.snapshot(command.target_id)
        if (
            type(state) is not TaskProjection
            or state.task_id != command.target_id
            or state.epoch != command.epoch
            or runner.epoch != command.epoch
            or not state.model_ready
            or state.status is not TaskStatus.RUNNING
            or not runner.can_accept_message
        ):
            return "MESSAGE_UNAVAILABLE"
        if command.user_text is None:
            return "MESSAGE_UNAVAILABLE"
        runner.add_user_message(command.user_text, command.effort)
        self._out.conversation_ready(
            ConversationEvent(
                command.target_id,
                state.epoch,
                "user",
                command.user_text,
                command.command_id,
            )
        )
        return None

    async def _advance(self, runner: TaskExecution, command: BridgeCommand) -> str:
        stream = self._streams.open(command.target_id, runner.epoch, command.command_id)
        try:
            update = await runner.advance()
        finally:
            self._streams.close(command.target_id, stream)
        if type(update) is not RunUpdate or update.task_id != command.target_id:
            return "RUNNER_UNCONFIRMED"
        if update.approval_id is not None:
            self._pending_runs[update.approval_id] = (update.task_id, update.epoch)
        if update.assistant_text is not None:
            self._out.conversation_ready(
                ConversationEvent(
                    update.task_id,
                    update.epoch,
                    "assistant",
                    update.assistant_text,
                    command.command_id,
                )
            )
        return f"RUN_{update.status.upper()}_QUERY_REQUIRED"

    async def _stop_without_guard(
        self, bindings: BackendBindings, command: BridgeCommand, view: _ExecutionView
    ) -> str:
        if command.action not in _STOPS:
            return "CONTROL_UNAVAILABLE"
        if bindings.stop_session is None or command.epoch is None:
            return "CONTROL_UNAVAILABLE"
        view.task_id = command.target_id
        await bindings.stop_session(command.target_id, command.epoch, ControlAction(command.action))
        return "SESSION_STOP_RETURNED"

    async def _review(self, control: HumanControl, command: BridgeCommand) -> str:
        snapshot = await control.review(command.target_id)
        if snapshot is None:
            return "REVIEW_UNAVAILABLE"
        self._out.review_ready(
            ApprovalReview(
                command.command_id,
                command.target_id,
                snapshot.task_id,
                snapshot.request.epoch,
                review_header(snapshot),
                snapshot.arguments_bytes,
                review_fields(snapshot),
            )
        )
        return "REVIEW_READY"

    async def _stop(
        self,
        bindings: BackendBindings,
        control: HumanControl,
        command: BridgeCommand,
        view: _ExecutionView,
    ) -> str:
        runner = bindings.runner_for_task(command.target_id)
        if not isinstance(runner, TaskExecution) or runner.task_id != command.target_id:
            return "RUNNER_UNAVAILABLE"
        view.task_id = command.target_id
        return await self._stop_task(bindings, control, command)

    async def _stop_task(
        self, bindings: BackendBindings, control: HumanControl, command: BridgeCommand
    ) -> str:
        discard = bindings.discard_queued
        try:
            if discard is not None:
                # Synchronous: queued commands are settled before wire, and
                # the stop latch below is set before its first await.
                discard(command.target_id)
        finally:
            # A failed discard still issues the stop (fail closed); its error
            # then leaves this command unconfirmed.
            if command.action == ControlAction.CANCEL:
                result = await control.cancel(command.target_id)
            else:
                result = await control.emergency_halt(command.target_id)
        # A model session (Codex) outlives Guard's Task stop unless it is
        # interrupted and closed too; its cleanup result decides confirmation.
        if bindings.stop_session is not None and command.epoch is not None:
            await bindings.stop_session(
                command.target_id, command.epoch, ControlAction(command.action)
            )
        if isinstance(result, _KNOWN_RESULTS):
            return f"CONTROL_{result.kind.upper()}_QUERY_REQUIRED"
        return "CONTROL_UNCONFIRMED"

    async def _decide(
        self,
        bindings: BackendBindings,
        control: HumanControl,
        command: BridgeCommand,
        view: _ExecutionView,
    ) -> str:
        known = self._pending_runs.get(command.target_id)
        view.task_id = known[0] if known is not None else None
        view.approval_id = command.target_id
        return await self._decide_approval(bindings, control, command)

    async def _decide_approval(
        self, bindings: BackendBindings, control: HumanControl, command: BridgeCommand
    ) -> str:
        if command.action == ControlAction.APPROVE:
            result = await control.approve(command.target_id)
        else:
            result = await control.reject(command.target_id)
        pending = self._pending_runs.get(command.target_id)
        if pending is not None:
            task_id, epoch = pending
            runner = bindings.runner_for_task(task_id)
            update = await runner.resume_approval(task_id, epoch, command.target_id, result)
            if update.status != "pending":
                self._pending_runs.pop(command.target_id, None)
            if update.approval_id is not None:
                self._pending_runs[update.approval_id] = (task_id, epoch)
            if update.status == "guard_error":
                return "CONTROL_GUARD_ERROR_QUERY_REQUIRED"
        if isinstance(result, _KNOWN_RESULTS):
            return f"CONTROL_{result.kind.upper()}_QUERY_REQUIRED"
        return "CONTROL_UNCONFIRMED"


def review_fields(snapshot: ApprovalSnapshot) -> tuple[tuple[str, str], ...]:
    """Trusted request metadata as (fixed label, value) pairs.

    Tool, kind and expiry tell apart two pending approvals with the same
    server and arguments. Values come from the snapshot and are escaped by
    the UI one line at a time.
    """
    req = snapshot.request
    destination = req.destination.boundary_id if req.destination else "없음"
    expires = snapshot.expires_at_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        ("도구", f"{req.tool.tool_name} ({req.tool.kind.name})"),
        ("MCP 서버", f"{req.tool.server_id} (경계 {req.server_boundary.boundary_id})"),
        ("목적지 경계", destination),
        ("Task 등급", snapshot.effective_label.name),
        ("만료", expires),
    )


def review_header(snapshot: ApprovalSnapshot) -> str:
    """The same metadata on one line, with a separator instead of newlines."""
    return " · ".join(f"{label}: {value}" for label, value in review_fields(snapshot))
