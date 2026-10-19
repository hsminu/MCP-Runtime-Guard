"""Codex `item/tool/call` -> `Guard.submit` -> fixed model answer (10.4, I1/I7/I8).

The Task id and epoch come only from the session manager's trusted binding.
Codex supplies `namespace` and `tool`; they are joined as `server__tool` into a
`ProposedCall` and nothing else from the model is kept. Model-facing text comes
only from B's `core.model_messages` table or from Guard's Released result.

Pending: the model answer comes only from Guard's final-result notification
(NOTIFY-01, `guard.pending_result(request_id).wait()`), never from what a human
approve/reject returned. One request has exactly one final result; a fresh
approval after re-evaluation arrives as a new submit. Expiry stays Guard's
timer decision. If no result arrives, the session watchdog cancels only this
wait and the Task is stopped (STOP-01).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from guard_app.adapters.codex_protocol import (
    DynamicToolCall,
    released_texts,
    tool_call_response,
)
from guard_app.core.model_messages import (
    ModelMessageKind,
    fixed_model_message,
    outcome_model_message,
)
from guard_app.domain.contracts import (
    Denied,
    Pending,
    PendingFinal,
    Released,
)
from guard_app.domain.models import ProposedCall
from guard_app.domain.reasons import Reason

TOOL_STOP_CODE = "CODEX_TOOL_STOPPED"


@dataclass(frozen=True, slots=True)
class TrustedTask:
    task_id: str
    epoch: int


@dataclass(frozen=True, slots=True, repr=False)
class RelayResult:
    response: dict[str, object]
    stop_code: str | None = None


class PendingWait(Protocol):
    async def wait(self) -> PendingFinal: ...


# Non-execution causes of a Pending's final result (NOTIFY-01). B is to add
# `final_model_message(PendingFinal)` to its fixed table (PR #28); until then
# this mirrors the mapping B accepted there.
_FINAL_MESSAGES = {
    "rejected": ModelMessageKind.APPROVAL_REJECTED,
    "expired": ModelMessageKind.APPROVAL_EXPIRED,
    "cancelled": ModelMessageKind.APPROVAL_CANCELLED,
    "invalidated": ModelMessageKind.APPROVAL_INVALIDATED,
}


class ApprovalOutcomes:
    """One loop's waiting tool calls, keyed by Guard approval_id.

    It records which trusted Task waits on which approval so the Task's
    projection can offer review/approve/reject while the model turn waits, and
    tells a listener (the bridge) when that changes. The result itself comes
    from Guard's `PendingResult`; nothing here decides or forwards it.
    """

    def __init__(self) -> None:
        self._waiting: set[str] = set()
        self._tasks: dict[str, str] = {}
        self._listener: Callable[[str], None] | None = None

    def set_listener(self, listener: Callable[[str], None] | None) -> None:
        self._listener = listener

    def waiting(self, approval_id: str) -> bool:
        return approval_id in self._waiting

    def approval_for(self, task_id: str) -> str | None:
        return next((approval for approval, owner in self._tasks.items() if owner == task_id), None)

    async def wait(self, pending: Pending, task_id: str, result: PendingWait) -> PendingFinal:
        if pending.approval_id in self._waiting:
            raise RuntimeError("approval already awaited")
        self._waiting.add(pending.approval_id)
        self._tasks[pending.approval_id] = task_id
        self._notify(task_id)
        try:
            return await result.wait()
        finally:
            self._waiting.discard(pending.approval_id)
            self._tasks.pop(pending.approval_id, None)
            self._notify(task_id)

    def _notify(self, task_id: str) -> None:
        if self._listener is not None:
            self._listener(task_id)


class GuardToolRelay:
    """Run one Codex tool call through Guard; the caller serializes per Task."""

    def __init__(
        self,
        task: TrustedTask,
        guard: object,
        approvals: ApprovalOutcomes,
        *,
        allowed_tool_refs: frozenset[str] | None = None,
    ) -> None:
        submit = getattr(guard, "submit", None)
        pending_result = getattr(guard, "pending_result", None)
        if not callable(submit) or not callable(pending_result):
            raise ValueError("Guard submit and pending result entries required")
        self._task = task
        self._submit = submit
        self._pending_result: Callable[[str], PendingWait] = pending_result
        self._approvals = approvals
        self._allowed = allowed_tool_refs
        self._inflight: set[asyncio.Future[object]] = set()
        self.submitted = 0

    async def relay(self, call: DynamicToolCall) -> RelayResult:
        if call.namespace is None:
            return self._fixed(fixed_model_message(ModelMessageKind.NAMESPACE_MISSING))
        tool_ref = f"{call.namespace}__{call.tool}"
        if self._allowed is not None and tool_ref not in self._allowed:
            # Only tools actually registered with Codex are ever relayed (I11).
            return self._fixed(
                fixed_model_message(ModelMessageKind.DENIED, Reason.TOOL_UNSUPPORTED)
            )
        if call.arguments is None:
            return self._fixed(
                fixed_model_message(ModelMessageKind.DENIED, Reason.INVALID_ARGUMENTS)
            )
        try:
            # Validate untrusted model input through the domain model, never by hand.
            proposed = ProposedCall.model_validate(
                {
                    "tool_ref": tool_ref,
                    "arguments": call.arguments,
                    "model_call_id": call.call_id,
                }
            )
        except ValidationError:
            return self._fixed(
                fixed_model_message(ModelMessageKind.DENIED, Reason.INVALID_ARGUMENTS)
            )
        self.submitted += 1
        # Shielded: stopping this session must not cancel Guard mid-submit;
        # a pre-reservation cancel would halt Guard for every Task (B7.2).
        # Guard finishes the call itself and refuses it if the Task stopped.
        submission = asyncio.ensure_future(
            self._submit(self._task.task_id, proposed, expected_epoch=self._task.epoch)
        )
        self._inflight.add(submission)
        submission.add_done_callback(self._settled)
        outcome = await asyncio.shield(submission)
        if type(outcome) is Pending:
            try:
                final = await self._approvals.wait(
                    outcome, self._task.task_id, self._pending_result(outcome.request_id)
                )
            except ValueError:
                # Unknown or already consumed request: nothing trustworthy to say.
                return self._stopped()
            return self._final(outcome, final)
        return self._answer(outcome)

    def _final(self, pending: Pending, final: object) -> RelayResult:
        if (
            type(final) is not PendingFinal
            or final.request_id != pending.request_id
            or final.approval_id != pending.approval_id
            or final.task_id != self._task.task_id
            or final.epoch != self._task.epoch
        ):
            return self._stopped()
        if final.cause == "executed":
            return self._answer(final.outcome)
        kind = _FINAL_MESSAGES.get(final.cause)
        if kind is None:
            # failed/shutdown: Guard could not settle the request; stop the Task.
            return self._stopped()
        return self._fixed(fixed_model_message(kind))

    def _answer(self, outcome: object) -> RelayResult:
        if type(outcome) is Released:
            return RelayResult(tool_call_response(True, released_texts(outcome.result)))
        if type(outcome) is Denied:
            return self._fixed(outcome_model_message(outcome))
        # Withheld/OutcomeUnknown, or anything that is not a known final
        # outcome: stop rather than guess.
        return self._stopped()

    @staticmethod
    def _stopped() -> RelayResult:
        success, text = fixed_model_message(ModelMessageKind.STOPPED)
        return RelayResult(tool_call_response(success, (text,)), TOOL_STOP_CODE)

    def _settled(self, submission: asyncio.Future[object]) -> None:
        self._inflight.discard(submission)
        if not submission.cancelled():
            submission.exception()  # fixed codes only; never log raw text

    @staticmethod
    def _fixed(message: tuple[bool, str]) -> RelayResult:
        success, text = message
        return RelayResult(tool_call_response(success, (text,)))
