"""Frozen values and fixed codes that cross the bridge, without Qt.

Services that only build or read these values import this module; the UI
imports them through ``services.bridge``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from guard_app.adapters.codex_protocol import REASONING_EFFORTS
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import RecoveryItemPage, RecoveryReport
from guard_app.services.approvals import HumanControl
from guard_app.services.codex_account import CodexAccountPort
from guard_app.services.policy_view import PolicyView
from guard_app.services.runner import TaskExecution


class BackendCleanupError(StoreError):
    """A 자원 정리에서 발생한 StoreError. 원문 없이 정리 미확인을 전달한다."""


class BridgeState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSING = "closing"
    CLOSED = "closed"


class ControlAction(StrEnum):
    REVIEW = "review"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    EMERGENCY_HALT = "emergency_halt"


class CommandStage(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    BACKEND_RETURNED = "backend_returned"
    REJECTED = "rejected"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    state: BridgeState
    code: str


@dataclass(frozen=True, slots=True)
class CommandEvent:
    command_id: str
    lane: str
    action: str
    target_id: str
    stage: CommandStage
    code: str


@dataclass(frozen=True, slots=True)
class BackendBindings:
    """Construct only in the backend thread from trusted app composition.

    ``runner_for_task`` must resolve a Store-created Task's execution handle. The
    bridge invokes only ``TaskExecution.advance``; it accepts no user-supplied
    coroutine or direct transport operation. ``human_control`` is the B
    capability obtained from ``GuardService.human_control``.
    ``discard_queued`` settles a Task's queued, not-yet-active transport
    commands before wire; it is called synchronously right before a human
    cancel or emergency halt starts.
    """

    runner_for_task: Callable[[str], TaskExecution]
    human_control: HumanControl | None
    close: Callable[[], Awaitable[None]] | None = None
    snapshot: Callable[[str], Awaitable[TaskProjection]] | None = None
    initial_task_id: str | None = None
    discard_queued: Callable[[str], int] | None = None
    create_session: Callable[[SessionOptions], Awaitable[TaskProjection]] | None = None
    stop_session: Callable[[str, int, ControlAction], Awaitable[None]] | None = None
    # Fixed code of what is known to prevent new sessions now, or None. Probed
    # on the backend thread at READY and after each creation attempt.
    session_blocker: Callable[[], str | None] | None = None
    # Codex account methods on a C-launched app-server (A52); None = unavailable.
    account: CodexAccountPort | None = None
    # Read-only Profile/rules for the policy screen (A91); None = no Profile.
    policy: PolicyView | None = None
    # Registers a backend-loop callback for Task changes outside a command
    # (approval waits, asynchronous session stops).
    task_events: Callable[[Callable[[str], None]], None] | None = None
    # Registers a backend-loop callback (task_id, epoch, piece) for the model
    # answer while a run is in progress (A90). Pieces outside a run are dropped.
    answer_partials: Callable[[Callable[[str, int, str], None]], None] | None = None
    # B3.10 server access change: (server_id, allowed, denied) -> new policy view.
    # None when no saver for the Profile document is connected.
    policy_access: (
        Callable[[str, tuple[str, ...], tuple[str, ...]], Awaitable[PolicyView]] | None
    ) = None
    # None argument reads the current destination list; edits return the applied view.
    policy_destinations: Callable[[DestinationEdit | None], Awaitable[PolicyView]] | None = None
    # Fixed code of what the start made of the CFG-01 settings file
    # (product_settings); None when this run reads no file (demo, tests).
    settings_status: str | None = None
    registration: Callable[[RegistrationRequest], Awaitable[RegistrationView]] | None = None
    recovery_report: RecoveryReport | None = None
    recovery_page: RecoveryItemPage | None = None
    recovery_next: Callable[[], Awaitable[RecoveryItemPage]] | None = None


# The only model pinned by C's config/codex/codex.lock.json (allowed_models).
CODEX_MODEL_ID = "gpt-6.1-sol"
# The model picker lists these; adding one needs C's lock and L2 first.
CODEX_MODELS = (CODEX_MODEL_ID,)
# Offered in the composer; the wire builder checks the same tuple again.
CODEX_REASONING_EFFORTS = REASONING_EFFORTS
# The model's own default in config/codex/model_catalog.json.
CODEX_DEFAULT_EFFORT = "low"


@dataclass(frozen=True, slots=True)
class SessionOptions:
    mode: str = "codex"
    model: str = CODEX_MODEL_ID

    def __post_init__(self) -> None:
        if self.mode != "codex" or self.model not in CODEX_MODELS:
            raise ValueError("Invalid Codex selection")


class SessionStartError(ValueError):
    """Fixed public rejection codes from trusted A session composition."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SessionCreated:
    command_id: str
    draft_key: str
    snapshot: TaskProjection


@dataclass(frozen=True, slots=True)
class TaskProjection:
    task_id: str
    epoch: int
    status: TaskStatus
    approval_id: str | None = None
    host_dispatch: int | None = None
    receiver_effect: int | None = None
    model_mode: str = "unconfigured"
    can_advance: bool = False
    can_accept_message: bool = False
    model_ready: bool = True
    model_id: str = ""
    connection_code: str = ""
    runner_state: str = ""
    tools_available: bool = True
    # Store TaskState.high_watermark / unclassified; None when not supplied.
    label: Label | None = None
    unclassified: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class ConversationEvent:
    """Ephemeral plain text for one Task/epoch, never command/audit metadata."""

    task_id: str
    epoch: int
    role: str
    text: str
    command_id: str


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalReview:
    command_id: str
    approval_id: str
    task_id: str
    epoch: int
    receiving_boundaries: str
    arguments_bytes: bytes
    # The same metadata as (label, value) pairs, shown one field per line.
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class DestinationEdit:
    action: str
    destination_id: str
    tier: str = ""
    max_label: str = ""
    selected: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class ToolSettingsEdit:
    name: str
    kind: str
    label: str | None = None
    destination_arg: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ToolSettingsRow:
    name: str
    registry_kind: str
    kind: str
    destination_arg: str | None
    label: str | None
    string_arguments: tuple[str, ...]
    readonly: bool


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationRequest:
    action: str
    server_id: str = ""
    transport: str = "stdio"
    tier: str = ""
    max_label: str = ""
    command: str | None = None
    args: tuple[str, ...] = ()
    env_allow: tuple[str, ...] = ()
    url: str | None = None
    token: str = ""
    expected_row_revision: int = 0
    allowed: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    file_root: str | None = None
    # Absolute folders selected by the human; backend derives source prefixes.
    folder_labels: tuple[tuple[str, str], ...] = ()
    root_label: str = ""
    list_root_label: str = ""
    tool_settings: tuple[ToolSettingsEdit, ...] = ()


@dataclass(frozen=True, slots=True)
class RegistrationRow:
    server_id: str
    status: str
    tier: str
    max_label: str
    transport: str | None
    pinned: bool


@dataclass(frozen=True, slots=True, repr=False)
class RegistrationView:
    code: str
    command_id: str = ""
    token: str = ""
    command: str | None = None
    size: int | None = None
    sha256: str | None = None
    url: str | None = None
    rows: tuple[RegistrationRow, ...] | None = None
    activation: PolicyView | None = None
    row_revision: int = 0
    current_policy: PolicyView | None = None
    tool_settings: tuple[ToolSettingsRow, ...] = ()
    server_id: str = ""
