"""What Codex answered in each turn of one app-server session (A90, I9).

The agent's finished messages, the deltas streamed while it writes, and how
each turn ended. All of it is model output: bounded here, shown only as plain
text, and never Guard input.
"""

from __future__ import annotations

from collections.abc import Callable

from guard_app.services.runner import CodexAuthenticationError, CodexSessionError

MAX_ANSWER_CHARS = 65_536
# A221 (D-02): text kept for turns not answered yet, whatever turn IDs the
# app-server reports. An honest turn keeps its deltas and its finished messages,
# each within MAX_ANSWER_CHARS; past these bounds the session stops.
MAX_RETAINED_CHARS = 4 * MAX_ANSWER_CHARS
MAX_TRACKED_TURNS = 8
_USAGE_LIMITS = frozenset({"usageLimitExceeded", "rateLimitExceeded"})


class TurnAnswers:
    """Per-turn answer text, streamed pieces and completion, keyed by turn ID."""

    def __init__(self, on_partial: Callable[[str], None] | None = None) -> None:
        self._on_partial = on_partial
        self._turns: dict[str, dict[str, object]] = {}
        self._texts: dict[str, list[str]] = {}
        self._finals: dict[str, list[str]] = {}
        self._deltas: dict[str, list[str]] = {}
        # Shown pieces: deltas, with a paragraph break where a new agentMessage begins.
        self._pieces: dict[str, list[str]] = {}
        self._piece_items: dict[str, str] = {}
        self._streamed: dict[str, int] = {}
        self._answer_chars: dict[str, int] = {}
        self._tracked: set[str] = set()
        self._retained = 0
        # Set once a bound above is passed; the session then stops (A221).
        self.overflowed = False

    def set_partial_handler(self, handler: Callable[[str], None] | None) -> None:
        self._on_partial = handler

    def _admit(self, turn_id: str, chars: int) -> bool:
        """Count what a turn keeps; False, and nothing more kept, past the bounds."""
        if turn_id not in self._tracked:
            if len(self._tracked) >= MAX_TRACKED_TURNS:
                self.overflowed = True
            self._tracked.add(turn_id)
        self._retained += chars
        if self._retained > MAX_RETAINED_CHARS:
            self.overflowed = True
        return not self.overflowed

    def message_completed(self, turn_id: object, item: dict[str, object]) -> None:
        """A finished agentMessage; `final_answer` messages make up the answer when present."""
        answer = item.get("text")
        if type(turn_id) is str and type(answer) is str:
            if not self._admit(turn_id, len(answer)):
                return
            self._texts.setdefault(turn_id, []).append(answer)
            if item.get("phase") == "final_answer":
                self._finals.setdefault(turn_id, []).append(answer)

    def add_delta(
        self, turn_id: object, delta: object, item_id: object, *, current_turn: str | None
    ) -> bool:
        """Record a streamed piece and show it for the current turn.

        False when the turn's answer has grown past MAX_ANSWER_CHARS; the piece
        is then neither kept nor shown.
        """
        if type(turn_id) is not str or type(delta) is not str:
            return True
        if not self._admit(turn_id, len(delta)):
            return False
        total = self._answer_chars.get(turn_id, 0) + len(delta)
        self._answer_chars[turn_id] = total
        if total > MAX_ANSWER_CHARS:
            return False
        self._deltas.setdefault(turn_id, []).append(delta)
        piece = delta
        if type(item_id) is str:
            previous = self._piece_items.get(turn_id)
            if previous is not None and previous != item_id:
                piece = "\n\n" + delta
            self._piece_items[turn_id] = item_id
        self._pieces.setdefault(turn_id, []).append(piece)
        if turn_id == current_turn:
            self.stream(turn_id)
        return True

    def complete(self, turn: object) -> bool:
        """Record how a turn ended; False for a report without a turn ID."""
        turn_id = turn.get("id") if type(turn) is dict else None
        if type(turn) is not dict or type(turn_id) is not str:
            return False
        if not self._admit(turn_id, 0):
            return False
        # Only what answer() reads; the report's items and messages are not kept.
        error = turn.get("error")
        info = error.get("codexErrorInfo") if type(error) is dict else None
        self._turns[turn_id] = {"status": turn.get("status"), "error": {"codexErrorInfo": info}}
        return True

    def completed(self, turn_id: str) -> bool:
        return turn_id in self._turns

    def stream(self, turn_id: str) -> None:
        """Hand the pieces not shown yet to the partial handler."""
        if self._on_partial is None:
            return
        pieces = self._pieces.get(turn_id, [])
        for piece in pieces[self._streamed.get(turn_id, 0) :]:
            self._on_partial(piece)
        self._streamed[turn_id] = len(pieces)

    def answer(self, turn_id: str) -> str:
        """A completed turn's answer, or the fixed failure of a turn that did not complete.

        Afterwards only this turn's completion is kept (A221): its text and every
        other turn's state are dropped, so a long session does not accumulate.
        """
        try:
            return self._answer(turn_id)
        finally:
            turn = self._turns[turn_id]
            for kept in (
                self._turns,
                self._texts,
                self._finals,
                self._deltas,
                self._pieces,
                self._piece_items,
                self._streamed,
                self._answer_chars,
            ):
                kept.clear()
            self._turns[turn_id] = turn
            self._tracked = {turn_id}
            self._retained = 0

    def _answer(self, turn_id: str) -> str:
        turn = self._turns[turn_id]
        status = turn.get("status")
        if status == "completed":
            finals = self._finals.get(turn_id)
            texts = self._texts.get(turn_id)
            if finals:
                answer = "\n\n".join(finals)
            elif texts:
                answer = "\n\n".join(texts)
            else:
                answer = "".join(self._deltas.get(turn_id, []))
            if len(answer) > MAX_ANSWER_CHARS:
                raise CodexSessionError("CODEX_ANSWER_TOO_LARGE")
            return answer
        error = turn.get("error")
        info = error.get("codexErrorInfo") if type(error) is dict else None
        if info == "unauthorized" or _http_status(info) == 401:
            raise CodexAuthenticationError()
        if type(info) is str and info in _USAGE_LIMITS:
            raise CodexSessionError("CODEX_USAGE_LIMIT")
        if status == "interrupted":
            raise CodexSessionError("CODEX_TURN_INTERRUPTED")
        raise CodexSessionError("CODEX_TURN_FAILED")


def _http_status(info: object) -> int | None:
    """Pinned Codex also reports API auth failure as a tagged HTTP error.

    Read only its structured status; the message can contain the rejected key.
    Other tags and malformed values retain the generic fixed failure.
    """
    if type(info) is not dict or set(info) != {"httpConnectionFailed"}:
        return None
    detail = info["httpConnectionFailed"]
    status = detail.get("httpStatusCode") if type(detail) is dict else None
    return status if type(status) is int else None
