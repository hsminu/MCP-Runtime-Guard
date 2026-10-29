"""Qt-thread command ingress and a separate asyncio backend lifetime.

This is an A-owned bridge kernel. Bootstrap runs in the backend thread and
must supply TaskExecution instances and B's human-only capability. It
does not create Tasks, authorize operators, or infer durable Task state.
Frozen lifecycle/command metadata and ephemeral public conversation events
cross into Qt signals. Raw tool results never cross this boundary.

This module keeps the Qt facade and UI re-exports. The parts it composes:

- ``bridge_host``: backend thread, startup and confirmed shutdown.
- ``bridge_context``: admission, replay and command result stages.

- ``bridge_types``: the public values and fixed codes, without Qt.
- ``bridge_ingress``: command validation, the bounded ingress and the ledger.
- ``bridge_lanes``: backend-loop queues and workers.
- ``bridge_bindings``: ownership of the bootstrap result until it is closed.
- ``bridge_streams``: the streamed answer of a running command (A90).
- ``bridge_commands``: what each command does against the bindings.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from guard_app.domain.enums import StorePrepareStep
from guard_app.domain.models import RecoveryItemPage as RecoveryItemPage
from guard_app.domain.models import RecoveryReport as RecoveryReport
from guard_app.services import catalog_sources
from guard_app.services.bridge_commands import (
    BridgeOutlet,
)
from guard_app.services.bridge_host import BackendHost, LifecycleOutlet
from guard_app.services.bridge_host import ProcessStartGate as _ProcessStartGate
from guard_app.services.bridge_ingress import (
    BridgeCommand,
    account_command,
    bridge_id,
    control_command,
    destination_command,
    message_command,
    policy_command,
    run_command,
    session_command,
)
from guard_app.services.bridge_ingress import new_command_id as new_command_id
from guard_app.services.bridge_ingress import new_draft_key as new_draft_key
from guard_app.services.bridge_registration import endpoint_input as endpoint_input
from guard_app.services.bridge_streams import MAX_PARTIAL_CHARS as MAX_PARTIAL_CHARS
from guard_app.services.bridge_streams import PARTIAL_FLUSH_S as PARTIAL_FLUSH_S
from guard_app.services.bridge_types import CODEX_DEFAULT_EFFORT as CODEX_DEFAULT_EFFORT
from guard_app.services.bridge_types import CODEX_MODEL_ID as CODEX_MODEL_ID
from guard_app.services.bridge_types import CODEX_MODELS as CODEX_MODELS
from guard_app.services.bridge_types import CODEX_REASONING_EFFORTS as CODEX_REASONING_EFFORTS
from guard_app.services.bridge_types import ApprovalReview as ApprovalReview
from guard_app.services.bridge_types import BackendBindings as BackendBindings
from guard_app.services.bridge_types import BackendCleanupError as BackendCleanupError
from guard_app.services.bridge_types import BridgeState as BridgeState
from guard_app.services.bridge_types import CommandEvent as CommandEvent
from guard_app.services.bridge_types import CommandStage as CommandStage
from guard_app.services.bridge_types import ControlAction as ControlAction
from guard_app.services.bridge_types import ConversationEvent as ConversationEvent
from guard_app.services.bridge_types import DestinationEdit as DestinationEdit
from guard_app.services.bridge_types import LifecycleEvent as LifecycleEvent
from guard_app.services.bridge_types import RegistrationRequest as RegistrationRequest
from guard_app.services.bridge_types import RegistrationView as RegistrationView
from guard_app.services.bridge_types import SessionCreated as SessionCreated
from guard_app.services.bridge_types import SessionOptions as SessionOptions
from guard_app.services.bridge_types import SessionStartError as SessionStartError
from guard_app.services.bridge_types import TaskProjection as TaskProjection
from guard_app.services.bridge_types import ToolSettingsEdit as ToolSettingsEdit
from guard_app.services.bridge_types import ToolSettingsRow as ToolSettingsRow
from guard_app.services.codex_account import AccountView as AccountView  # UI re-export
from guard_app.services.policy_view import PolicyDestination as PolicyDestination
from guard_app.services.policy_view import PolicyServer as PolicyServer  # UI re-export
from guard_app.services.policy_view import PolicyTool as PolicyTool  # UI re-export
from guard_app.services.policy_view import PolicyView as PolicyView  # UI re-export
from guard_app.services.session_preparation import L2_FAILURE_CODES as L2_FAILURE_CODES


def valid_server_id(server_id: object) -> bool:
    """Forward B's pure syntax result, including before backend startup/after close.

    This local hint neither normalizes input nor grants Registry acceptance.
    Registry remains responsible for validating every submitted registration.
    """

    return catalog_sources.valid_server_id(server_id)


_PROCESS_START_GATE = _ProcessStartGate()


def _checked_limits(counts: tuple[int, ...], timeouts: tuple[float, ...]) -> tuple[float, ...]:
    if any(type(value) is not int or value < 1 for value in counts) or any(
        type(value) not in (int, float) for value in timeouts
    ):
        raise ValueError("Bridge limits are invalid")
    try:
        seconds = tuple(float(value) for value in timeouts)
    except OverflowError:
        raise ValueError("Bridge limits are invalid") from None
    if any(not math.isfinite(value) or value <= 0 for value in seconds):
        raise ValueError("Bridge limits are invalid")
    return seconds


class BackendBridge(QObject):
    """Call public methods on the Qt thread; never wait there for backend work.

    A command's ``BACKEND_RETURNED`` only records an API return. In
    particular, cancel/emergency_halt require a later authoritative Store
    snapshot before the UI can display an applied Task transition.

    ``control_workers`` serves APPROVE/REJECT only. CANCEL/EMERGENCY_HALT
    have a separate bounded queue and two reserved workers, even when
    ``control_workers=1``. Completed command IDs are cached only for this
    process lifetime; C/B must provide durable replay protection.
    """

    lifecycle_changed = Signal(object)
    command_changed = Signal(object)
    task_changed = Signal(object)
    review_ready = Signal(object)
    conversation_ready = Signal(object)
    # The whole answer so far for one run command; replaces the previous one.
    conversation_partial = Signal(object)
    session_created = Signal(object)
    # str: fixed blocker code, or "" when new sessions may be requested.
    session_capability_changed = Signal(str)
    account_changed = Signal(object)
    policy_changed = Signal(object)
    settings_changed = Signal(object)
    registration_changed = Signal(object)
    recovery_changed = Signal(object)
    recovery_page_changed = Signal(object)

    def __init__(
        self,
        bootstrap: Callable[[], Awaitable[BackendBindings]],
        *,
        run_queue_size: int = 16,
        control_queue_size: int = 16,
        control_workers: int = 4,
        max_command_ids: int = 4096,
        startup_timeout: float = 10.0,
        shutdown_timeout: float = 3.0,
        start_gate: _ProcessStartGate | None = None,
    ) -> None:
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise ValueError("Bridge requires the Qt application thread")
        if not callable(bootstrap):
            raise TypeError("Bridge bootstrap is invalid")
        startup_timeout, shutdown_timeout = _checked_limits(
            (run_queue_size, control_queue_size, control_workers, max_command_ids),
            (startup_timeout, shutdown_timeout),
        )
        super().__init__()
        outlet = BridgeOutlet(
            task_changed=self.task_changed.emit,
            review_ready=self.review_ready.emit,
            conversation_ready=self.conversation_ready.emit,
            session_created=self.session_created.emit,
            session_capability=self.session_capability_changed.emit,
            account_changed=self.account_changed.emit,
            policy_changed=self.policy_changed.emit,
            registration_changed=self.registration_changed.emit,
        )
        lifecycle = LifecycleOutlet(
            self.lifecycle_changed.emit,
            self.command_changed.emit,
            self.conversation_partial.emit,
            self.settings_changed.emit,
            self.recovery_changed.emit,
            self.recovery_page_changed.emit,
            self.policy_changed.emit,
        )
        self._host = BackendHost(
            bootstrap,
            outlet,
            lifecycle,
            run_queue_size=run_queue_size,
            control_queue_size=control_queue_size,
            control_workers=control_workers,
            max_command_ids=max_command_ids,
            startup_timeout=startup_timeout,
            shutdown_timeout=shutdown_timeout,
            start_gate=_PROCESS_START_GATE if start_gate is None else start_gate,
        )

    @property
    def state(self) -> BridgeState:
        return self._host.state

    @property
    def backend_alive(self) -> bool:
        return self._host.backend_alive

    @property
    def thread_done(self) -> bool:
        return self._host.thread_done

    @property
    def cleanup_unconfirmed(self) -> bool:
        return self._host.cleanup_unconfirmed

    @property
    def shutdown_timeout(self) -> float:
        return self._host.shutdown_timeout

    def wait_done(self, timeout_s: float) -> bool:
        """After the Qt loop ends, wait at most timeout_s for the backend."""
        return self._host.wait_done(timeout_s)

    def start(self) -> None:
        self._require_qt_thread()
        self._host.start()

    def close(self) -> None:
        """Request bounded cleanup without joining from the UI thread."""
        self._require_qt_thread()
        self._host.close()

    def request_run(self, task_id: str, *, command_id: str | None = None) -> str:
        self._require_qt_thread()
        return self._host.commands.submit(run_command(task_id, command_id))

    def request_control(
        self,
        action: ControlAction,
        target_id: str,
        *,
        command_id: str | None = None,
        epoch: int | None = None,
    ) -> str:
        self._require_qt_thread()
        return self._host.commands.submit(control_command(action, target_id, command_id, epoch))

    def request_message(
        self,
        task_id: str,
        epoch: int,
        text: str,
        *,
        command_id: str | None = None,
        effort: str | None = None,
    ) -> str:
        """Queue a user turn through the existing Runner, without new sessions.

        `effort` is the reasoning level picked in the composer; None keeps the
        session's current level."""
        self._require_qt_thread()
        return self._host.commands.submit(message_command(task_id, epoch, text, command_id, effort))

    def request_account(
        self, action: str, *, target_id: str = "account", secret: str | None = None
    ) -> str:
        """Codex login commands. `secret` is an API key, never kept or shown."""

        self._require_qt_thread()
        return self._host.commands.submit(account_command(action, target_id, secret))

    def request_policy_destinations(self, edit: DestinationEdit | None = None) -> str:
        self._require_qt_thread()
        return self._host.commands.submit(destination_command(edit))

    def request_policy_access(
        self, server_id: str, allowed: tuple[str, ...], denied: tuple[str, ...]
    ) -> str:
        """B3.10: replace one server's allow/deny rules from the activation dialog."""

        self._require_qt_thread()
        return self._host.commands.submit(policy_command(server_id, allowed, denied))

    def request_registration(
        self, request: RegistrationRequest, *, command_id: str | None = None
    ) -> str:
        self._require_qt_thread()
        if type(request) is not RegistrationRequest or request.action not in (
            "prepare",
            "prepare_file",
            "confirm",
            "recheck",
            "list",
            "activation_preview",
            "tool_settings_preview",
            "tool_settings_save",
            "activate",
            "block",
        ):
            raise ValueError("REG_REQUEST_INVALID")
        return self._host.commands.submit(
            BridgeCommand(
                bridge_id(command_id, generate=True),
                "control",
                "registration",
                "registry",
                registration=request,
            )
        )

    def request_new_session(
        self, draft_key: str, options: SessionOptions, *, command_id: str | None = None
    ) -> str:
        self._require_qt_thread()
        return self._host.commands.submit(session_command(draft_key, options, command_id))

    @staticmethod
    def _require_qt_thread() -> None:
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Bridge method requires the Qt thread")

    def codex_progress(self, code: str) -> None:
        self._host.codex_progress(code)

    def store_progress(self, step: StorePrepareStep) -> None:
        self._host.store_progress(step)

    def load_recovery_page(self) -> None:
        self._host.load_recovery_page()
