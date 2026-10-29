"""Admission, replay and result stages for one backend's command stream.

The host lends its state lock; readiness and queue admission remain atomic with
lifecycle transitions. Domain handlers never write the command ledger.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable

from guard_app.services.bridge_commands import CommandExecutor
from guard_app.services.bridge_ingress import (
    BridgeCommand,
    CommandIngress,
    CommandLedger,
    IngressItem,
    ingress_lane,
)
from guard_app.services.bridge_lanes import CommandLanes
from guard_app.services.bridge_types import BridgeState, CommandEvent, CommandStage


class CommandContext:
    def __init__(
        self,
        *,
        state_lock: threading.Lock,
        state: Callable[[], BridgeState],
        loop: Callable[[], asyncio.AbstractEventLoop | None],
        failed: Callable[[], None],
        emit: Callable[[CommandEvent], None],
        commands: CommandExecutor,
        run_size: int,
        control_size: int,
        control_workers: int,
        max_ids: int,
        consume: Callable[[asyncio.Future[object]], None],
    ) -> None:
        self._state_lock = state_lock
        self._state = state
        self._loop = loop
        self._failed = failed
        self._emit = emit
        self._commands = commands
        self._run_queue_size = run_size
        self.ledger = CommandLedger(max_ids)
        self.ingress = CommandIngress(run_size, control_size)
        self.lanes = CommandLanes(
            run_size=run_size,
            control_size=control_size,
            control_workers=control_workers,
            perform=self._perform,
            runs_for=self.ledger.runs_for,
            consume=consume,
        )

    def submit(self, command: BridgeCommand) -> str:
        with self._state_lock:
            state, loop = self._state(), self._loop()
        if state is not BridgeState.READY or loop is None:
            code = "CLOSING" if state in (BridgeState.CLOSING, BridgeState.CLOSED) else "NOT_READY"
            rejection: str | None = code
        else:
            schedule = functools.partial(loop.call_soon_threadsafe, self._drain_ingress)
            rejection = self.ingress.offer(command, self.ledger, schedule)
        if rejection is not None:
            self._emit_untracked_rejection(command, rejection)
        return command.command_id

    def _drain_ingress(self) -> None:
        # Re-schedule after a small batch so stop/worker callbacks can run.
        # Every batch selects urgent commands first, including ones received
        # after a normal-command drain began.
        try:
            for _ in range(8):
                item = self.ingress.take()
                if item is None:
                    return
                self._admit_item(item)
        finally:
            if self.ingress.more_waiting():
                asyncio.get_running_loop().call_soon(self._drain_ingress)

    def _admit_item(self, item: IngressItem) -> None:
        command = item.command
        completed = False
        try:
            if item.replay is not None:
                current = self.ledger.previous(command.command_id)
                self._replay_or_conflict(command, current if current is not None else item.replay)
            else:
                self._admit(command)
            completed = True
        finally:
            try:
                if not completed:
                    self._failed()
                    if item.replay is not None:
                        self._emit(command.event(CommandStage.UNCONFIRMED, "REPLAY_UNCONFIRMED"))
                    else:
                        self.emit_command(
                            command, CommandStage.UNCONFIRMED, "ADMISSION_UNCONFIRMED"
                        )
            finally:
                self.ingress.release(command.command_id)

    def reject_ingress(self, code: str) -> None:
        for command in self.ingress.close():
            self._emit_untracked_rejection(command, code)

    def _emit_untracked_rejection(self, command: BridgeCommand, code: str) -> None:
        self._emit(command.event(CommandStage.REJECTED, code))

    def _admit(self, command: BridgeCommand) -> None:
        previous = self.ledger.previous(command.command_id)
        if previous is not None:
            self._replay_or_conflict(command, previous)
            return
        lane = ingress_lane(command)
        queue = self.lanes.queue(lane)
        if queue is None:
            raise RuntimeError("BRIDGE_QUEUE_UNAVAILABLE")
        accepted: CommandEvent | None = None
        rejection = "CLOSING"
        with self._state_lock:
            if self._state() is BridgeState.READY:
                try:
                    if lane == "run" and self.ledger.active_runs() >= self._run_queue_size + 1:
                        raise asyncio.QueueFull
                    queue.put_nowait(command)
                except asyncio.QueueFull:
                    rejection = "QUEUE_FULL"
                else:
                    accepted = command.event(CommandStage.ACCEPTED, "QUEUED")
                    self.ledger.record(accepted)
        if accepted is None:
            self.emit_command(command, CommandStage.REJECTED, rejection)
        else:
            self._emit(accepted)

    def _replay_or_conflict(self, command: BridgeCommand, previous: CommandEvent) -> None:
        if (
            previous.lane,
            previous.action,
            previous.target_id,
        ) == (command.lane, command.action, command.target_id):
            self._emit(previous)
        else:
            self._emit_untracked_rejection(command, "COMMAND_ID_CONFLICT")

    def emit_command(self, command: BridgeCommand, stage: CommandStage, code: str) -> None:
        event = command.event(stage, code)
        self.ledger.record(event)
        self._emit(event)

    async def _perform(self, command: BridgeCommand) -> None:
        self.emit_command(command, CommandStage.PROCESSING, "BACKEND_STARTED")
        completed = False
        try:
            code = await self._commands.execute(command)
            completed = True
            if self.ledger.stage(command.command_id) is CommandStage.PROCESSING:
                self.emit_command(command, CommandStage.BACKEND_RETURNED, code)
        finally:
            if not completed:
                self.emit_command(command, CommandStage.UNCONFIRMED, "BACKEND_UNCONFIRMED")
