"""Commands from the Qt thread: validation, the bounded ingress and the ledger.

Nothing here runs a command. ``CommandIngress`` holds what the Qt thread
handed over until the backend loop admits it. ``CommandLedger`` keeps each
command's latest stage for replay and conflict checks; completed command IDs
are remembered only for this process lifetime.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from guard_app.services import catalog_sources
from guard_app.services.bridge_types import (
    CODEX_REASONING_EFFORTS,
    CommandEvent,
    CommandStage,
    ControlAction,
    SessionOptions,
)

ACCOUNT_ACTIONS = frozenset(
    {
        "account_read",
        "account_login_api_key",
        "account_login_device",
        "account_login_browser",
        "account_cancel_login",
        "account_logout",
    }
)


@dataclass(frozen=True, slots=True)
class BridgeCommand:
    command_id: str
    lane: str
    action: str
    target_id: str
    epoch: int | None = None
    user_text: str | None = field(default=None, repr=False)
    options: SessionOptions | None = None
    access: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    effort: str | None = None

    def event(self, stage: CommandStage, code: str) -> CommandEvent:
        return CommandEvent(self.command_id, self.lane, self.action, self.target_id, stage, code)


def bridge_id(value: str | None, *, generate: bool = False) -> str:
    if value is None and generate:
        return uuid4().hex
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError("Bridge identifier is invalid")
    return value


def run_command(task_id: str, command_id: str | None) -> BridgeCommand:
    return BridgeCommand(bridge_id(command_id, generate=True), "run", "advance", bridge_id(task_id))


def control_command(
    action: ControlAction, target_id: str, command_id: str | None, epoch: int | None
) -> BridgeCommand:
    if type(action) is not ControlAction:
        raise ValueError("Control action is invalid")
    return BridgeCommand(
        bridge_id(command_id, generate=True),
        "control",
        action.value,
        bridge_id(target_id),
        epoch=epoch,
    )


def message_command(
    task_id: str, epoch: int, text: str, command_id: str | None, effort: str | None
) -> BridgeCommand:
    if type(epoch) is not int or epoch < 0:
        raise ValueError("Message epoch is invalid")
    if type(text) is not str or not text.strip() or len(text) > 32768:
        raise ValueError("Message text is invalid")
    if effort is not None and effort not in CODEX_REASONING_EFFORTS:
        raise ValueError("Reasoning effort is invalid")
    return BridgeCommand(
        bridge_id(command_id, generate=True),
        "run",
        "message",
        bridge_id(task_id),
        epoch,
        text,
        effort=effort,
    )


def account_command(action: str, target_id: str, secret: str | None) -> BridgeCommand:
    if action not in ACCOUNT_ACTIONS:
        raise ValueError("Account action is invalid")
    return BridgeCommand(
        bridge_id(None, generate=True),
        "control",
        action,
        bridge_id(target_id),
        user_text=secret if action == "account_login_api_key" else None,
    )


def policy_command(
    server_id: str, allowed: tuple[str, ...], denied: tuple[str, ...]
) -> BridgeCommand:
    if not catalog_sources.valid_server_id(server_id) or any(
        type(names) is not tuple
        or len(names) > 256
        or any(type(name) is not str or not name or len(name) > 128 for name in names)
        for names in (allowed, denied)
    ):
        raise ValueError("Policy access change is invalid")
    return BridgeCommand(
        bridge_id(None, generate=True),
        "control",
        "policy_access",
        bridge_id(server_id),
        access=(allowed, denied),
    )


def session_command(
    draft_key: str, options: SessionOptions, command_id: str | None
) -> BridgeCommand:
    if type(options) is not SessionOptions:
        raise ValueError("Invalid model selection")
    return BridgeCommand(
        bridge_id(command_id, generate=True),
        "run",
        "new_session",
        bridge_id(draft_key),
        options=options,
    )


def ingress_lane(command: BridgeCommand) -> str:
    if command.lane == "run":
        return "run"
    if command.action in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
        return "urgent"
    return "control"


class CommandLedger:
    """Each command's latest stage, shared by the Qt thread and the backend loop.

    Accepted and processing commands stay until they settle. Settled ones are
    kept, oldest dropped first, up to ``max_ids``.
    """

    def __init__(self, max_ids: int) -> None:
        self._lock = threading.Lock()
        self._max_ids = max_ids
        self._records: dict[str, CommandEvent] = {}
        self._recent: OrderedDict[str, CommandEvent] = OrderedDict()

    def previous(self, command_id: str) -> CommandEvent | None:
        with self._lock:
            return self._records.get(command_id) or self._recent.get(command_id)

    def record(self, event: CommandEvent) -> None:
        with self._lock:
            if event.stage in (CommandStage.ACCEPTED, CommandStage.PROCESSING):
                self._records[event.command_id] = event
            elif (
                self._records.pop(event.command_id, None) is not None
                or event.stage is CommandStage.UNCONFIRMED
            ):
                self._recent[event.command_id] = event
                self._recent.move_to_end(event.command_id)
                if len(self._recent) > self._max_ids:
                    self._recent.popitem(last=False)

    def stage(self, command_id: str) -> CommandStage:
        """The unsettled command's stage; KeyError once it has settled."""
        with self._lock:
            return self._records[command_id].stage

    def active_runs(self) -> int:
        with self._lock:
            return sum(event.lane == "run" for event in self._records.values())

    def runs_for(self, target_id: str) -> bool:
        with self._lock:
            return any(
                event.lane == "run" and event.target_id == target_id
                for event in self._records.values()
            )

    def unsettled(self) -> tuple[CommandEvent, ...]:
        with self._lock:
            return tuple(
                event
                for event in self._records.values()
                if event.stage in (CommandStage.PROCESSING, CommandStage.ACCEPTED)
            )

    def settled(self) -> tuple[CommandEvent, ...]:
        with self._lock:
            return tuple(self._recent.values())


@dataclass(frozen=True, slots=True)
class IngressItem:
    command: BridgeCommand
    replay: CommandEvent | None = None


class CommandIngress:
    """Bounded per-lane hand-over from the Qt thread to the backend loop.

    Commands are bounded here before they become asyncio ready callbacks. A
    normal command flood must leave an independent slot for urgent work, and
    a command ID seen before waits in its own replay lane.
    """

    _ORDER = ("urgent", "replay", "control", "run")

    def __init__(self, run_size: int, control_size: int) -> None:
        self._lock = threading.Lock()
        self._lanes: dict[str, deque[IngressItem]] = {
            "run": deque(),
            "control": deque(),
            "urgent": deque(),
            "replay": deque(),
        }
        self._limits = {
            "run": run_size,
            "control": control_size,
            "urgent": control_size,
            "replay": max(run_size, control_size),
        }
        self._pending: dict[str, BridgeCommand] = {}
        self._scheduled = False
        self._closed = False

    @property
    def scheduled(self) -> bool:
        with self._lock:
            return self._scheduled

    def waiting(self, lane: str | None = None) -> int:
        with self._lock:
            if lane is not None:
                return len(self._lanes[lane])
            return sum(len(items) for items in self._lanes.values())

    def offer(
        self, command: BridgeCommand, ledger: CommandLedger, schedule: Callable[[], object]
    ) -> str | None:
        """Qt thread: queue `command`, or return the fixed rejection code.

        An identical command already waiting is dropped without a code.
        `schedule` asks the backend loop for a drain; it raises RuntimeError
        once that loop has closed.
        """
        with self._lock:
            in_flight = self._pending.get(command.command_id)
            if self._closed:
                return "CLOSING"
            if in_flight is not None:
                return None if in_flight == command else "COMMAND_ID_CONFLICT"
            previous = ledger.previous(command.command_id)
            lane = "replay" if previous is not None else ingress_lane(command)
            pending = self._lanes[lane]
            if len(pending) >= self._limits[lane]:
                return "INGRESS_FULL"
            pending.append(IngressItem(command, previous))
            self._pending[command.command_id] = command
            if not self._scheduled:
                self._scheduled = True
                try:
                    schedule()
                except RuntimeError:
                    pending.pop()
                    self._pending.pop(command.command_id)
                    self._scheduled = False
                    return "CLOSING"
            return None

    def take(self) -> IngressItem | None:
        """Backend loop: the next item, urgent commands first."""
        with self._lock:
            for lane in self._ORDER:
                if self._lanes[lane]:
                    return self._lanes[lane].popleft()
            return None

    def release(self, command_id: str) -> None:
        """Backend loop: the taken command was admitted or settled."""
        with self._lock:
            self._pending.pop(command_id, None)

    def more_waiting(self) -> bool:
        """Backend loop, after a drain batch: True keeps the drain scheduled."""
        with self._lock:
            if any(self._lanes.values()):
                return True
            self._scheduled = False
            return False

    def close(self) -> list[BridgeCommand]:
        """Refuse every later offer and return the commands still waiting."""
        with self._lock:
            self._closed = True
            pending = [item.command for lane in self._ORDER for item in self._lanes[lane]]
            for items in self._lanes.values():
                items.clear()
            self._pending.clear()
            self._scheduled = False
        return pending
