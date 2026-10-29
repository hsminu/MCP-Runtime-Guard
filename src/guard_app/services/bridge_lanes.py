"""Backend-loop queues and workers for admitted commands.

Run commands have one worker and run one at a time per Task. APPROVE/REJECT
and the other control commands share ``control_workers`` workers. CANCEL and
EMERGENCY_HALT have a separate bounded queue and two reserved workers, even
when ``control_workers=1``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from guard_app.services.bridge_ingress import BridgeCommand


class CommandLanes:
    def __init__(
        self,
        *,
        run_size: int,
        control_size: int,
        control_workers: int,
        perform: Callable[[BridgeCommand], Awaitable[None]],
        runs_for: Callable[[str], bool],
        consume: Callable[[asyncio.Future[Any]], None],
    ) -> None:
        self._sizes = {"run": run_size, "control": control_size, "urgent": control_size}
        self._control_workers = control_workers
        self._perform = perform
        # True while the ledger still holds an unsettled run for the Task.
        self._runs_for = runs_for
        self._consume = consume
        self._queues: dict[str, asyncio.Queue[BridgeCommand]] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._operations: dict[str, asyncio.Task[None]] = {}
        self._run_locks: dict[str, asyncio.Lock] = {}

    def open(self) -> None:
        """Backend loop, once the bindings are usable."""
        self._queues = {lane: asyncio.Queue(maxsize=size) for lane, size in self._sizes.items()}
        self._workers = [asyncio.create_task(self._worker("run"))]
        self._workers.extend(
            asyncio.create_task(self._worker("control")) for _ in range(self._control_workers)
        )
        self._workers.extend(asyncio.create_task(self._worker("urgent")) for _ in range(2))

    def queue(self, lane: str) -> asyncio.Queue[BridgeCommand] | None:
        """None until `open`."""
        return self._queues.get(lane)

    def running(self) -> int:
        return len(self._operations)

    def reject_waiting(self, reject: Callable[[BridgeCommand], None]) -> None:
        """Hand every queued command that no worker has taken to `reject`."""
        for lane in ("run", "control", "urgent"):
            queue = self._queues.get(lane)
            while queue is not None and not queue.empty():
                reject(queue.get_nowait())
                queue.task_done()

    def cancel_operations(self) -> None:
        for task in self._operations.values():
            task.cancel()

    def cancel_workers(self) -> None:
        for worker in self._workers:
            worker.cancel()

    def active(self) -> tuple[asyncio.Task[None], ...]:
        """Operations and workers to wait for, cancelled or not."""
        return tuple(self._operations.values()) + tuple(self._workers)

    async def _worker(self, lane: str) -> None:
        queue = self._queues.get(lane)
        if queue is None:
            raise RuntimeError("BRIDGE_QUEUE_UNAVAILABLE")
        while True:
            command = await queue.get()
            operation = asyncio.create_task(self._operate(command, queue))
            operation.add_done_callback(self._consume)
            self._operations[command.command_id] = operation
            if lane != "run":
                await asyncio.wait({operation})
            del command, operation

    async def _operate(self, command: BridgeCommand, queue: asyncio.Queue[BridgeCommand]) -> None:
        try:
            if command.lane == "run":
                lock = self._run_locks.setdefault(command.target_id, asyncio.Lock())
                async with lock:
                    await self._perform(command)
            else:
                await self._perform(command)
        finally:
            self._operations.pop(command.command_id, None)
            queue.task_done()
            if command.lane == "run" and not self._runs_for(command.target_id):
                self._run_locks.pop(command.target_id, None)
