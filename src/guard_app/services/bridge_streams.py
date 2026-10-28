"""The answer of a running command, republished while it streams (A90)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from guard_app.services.bridge_types import ConversationEvent

# Streamed answer text is republished at most this often (A90).
PARTIAL_FLUSH_S = 0.1
# Above Codex's answer limit plus item breaks; a longer stream stops updating.
MAX_PARTIAL_CHARS = 70_000


@dataclass(slots=True, repr=False)
class AnswerStream:
    """Backend loop only: the in-progress answer of one run command (A90)."""

    epoch: int
    command_id: str
    pieces: list[str] = field(default_factory=list)
    chars: int = 0
    dirty: bool = False
    flush: asyncio.TimerHandle | None = None


class AnswerStreams:
    """Backend loop only. A run command opens its Task's stream and closes it.

    `publish` receives the whole answer so far, at most once per
    ``PARTIAL_FLUSH_S``. Pieces for another epoch or outside a run are dropped.
    """

    def __init__(
        self, publish: Callable[[ConversationEvent], None], stopped: Callable[[], bool]
    ) -> None:
        self._publish = publish
        self._stopped = stopped
        self._streams: dict[str, AnswerStream] = {}

    def active(self) -> int:
        return len(self._streams)

    def open(self, task_id: str, epoch: int, command_id: str) -> AnswerStream:
        stream = AnswerStream(epoch, command_id)
        self._streams[task_id] = stream
        return stream

    def add(self, task_id: str, epoch: int, piece: str) -> None:
        """Collect a streamed piece of the running answer."""
        stream = self._streams.get(task_id)
        if (
            stream is None
            or stream.epoch != epoch
            or type(piece) is not str
            or not piece
            or self._stopped()
        ):
            return
        if stream.chars + len(piece) > MAX_PARTIAL_CHARS:
            return
        stream.pieces.append(piece)
        stream.chars += len(piece)
        stream.dirty = True
        if stream.flush is None:
            stream.flush = asyncio.get_running_loop().call_later(
                PARTIAL_FLUSH_S, self._flush, task_id, stream
            )

    def close(self, task_id: str, stream: AnswerStream) -> None:
        """Publish what is left; the final answer, if any, is emitted after this."""
        if stream.flush is not None:
            stream.flush.cancel()
        self._flush(task_id, stream)
        if self._streams.get(task_id) is stream:
            del self._streams[task_id]

    def _flush(self, task_id: str, stream: AnswerStream) -> None:
        stream.flush = None
        if not stream.dirty or self._streams.get(task_id) is not stream:
            return
        stream.dirty = False
        if self._stopped():
            return
        self._publish(
            ConversationEvent(
                task_id, stream.epoch, "assistant", "".join(stream.pieces), stream.command_id
            )
        )
