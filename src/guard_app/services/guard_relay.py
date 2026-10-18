"""Codex `item/tool/call` -> `Guard.submit` -> fixed model answer (10.4, I1/I7/I8).

The Task id and epoch come only from the session manager's trusted binding.
Codex supplies `namespace` and `tool`; they are joined as `server__tool` into a
`ProposedCall` and nothing else from the model is kept. Model-facing text comes
only from B's `core.model_messages` table or from Guard's Released result.

Pending: Guard has no final-result notification yet (NOTIFY-01, B5.9). Until it
lands, `ApprovalOutcomes` is an A-local hub that the bridge feeds with the
outcome HumanControl returns for approve/reject. Expiry stays Guard's timer
decision, so this module never calls `expire`; a missed answer ends in the
session watchdog and a Task stop instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

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
from guard_app.domain.contracts import Denied, OutcomeUnknown, Pending, Released, Withheld
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


@dataclass(frozen=True, slots=True, repr=False)
class FinalApproval:
    kind: ModelMessageKind
    outcome: object | None = None


# HumanControl.reject on an approval that was already resolved elsewhere.
_STALE_REASONS = frozenset(
    {Reason.APPROVAL_STALE, Reason.APPROVAL_RACE, Reason.APPROVAL_CONTEXT_LOST}
)


class ApprovalOutcomes:
    """One loop's waiting tool calls, keyed by Guard approval_id.

    It also records which trusted Task waits on which approval so the Task's
    projection can offer review/approve/reject while the model turn waits, and
    tells a listener (the bridge) when that changes.
    """

    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future[FinalApproval]] = {}
        self._tasks: dict[str, str] = {}
        self._listener: Callable[[str], None] | None = None

    def set_listener(self, listener: Callable[[str], None] | None) -> None:
        self._listener = listener

    def waiting(self, approval_id: str) -> bool:
        return approval_id in self._waiting

    def approval_for(self, task_id: str) -> str | None:
        return next((approval for approval, owner in self._tasks.items() if owner == task_id), None)

    async def wait(self, pending: Pending, task_id: str) -> FinalApproval:
        future: asyncio.Future[FinalApproval] = asyncio.get_running_loop().create_future()
        if pending.approval_id in self._waiting:
            raise RuntimeError("approval already awaited")
        self._waiting[pending.approval_id] = future
        self._tasks[pending.approval_id] = task_id
        self._notify(task_id)
        try:
            return await future
        finally:
            self._waiting.pop(pending.approval_id, None)
            self._tasks.pop(pending.approval_id, None)
            self._notify(task_id)

    def resolve(self, approval_id: str, action: str, outcome: object) -> None:
        future = self._waiting.get(approval_id)
        if future is None or future.done():
            return
        if action == "approve":
            future.set_result(FinalApproval(ModelMessageKind.RELEASED, outcome))
        elif action == "reject":
            stale = type(outcome) is Denied and bool(_STALE_REASONS & set(outcome.reasons))
            future.set_result(
                FinalApproval(
                    ModelMessageKind.APPROVAL_INVALIDATED
                    if stale
                    else ModelMessageKind.APPROVAL_REJECTED
                )
            )
        else:
            future.set_result(FinalApproval(ModelMessageKind.APPROVAL_CANCELLED))

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
        if not callable(submit):
            raise ValueError("Guard submit entry required")
        self._task = task
        self._submit = submit
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
        while type(outcome) is Pending:
            final = await self._approvals.wait(outcome, self._task.task_id)
            if final.kind is not ModelMessageKind.RELEASED:
                return self._fixed(fixed_model_message(final.kind))
            outcome = final.outcome
        return self._answer(outcome)

    def _answer(self, outcome: object) -> RelayResult:
        if type(outcome) is Released:
            return RelayResult(tool_call_response(True, released_texts(outcome.result)))
        if type(outcome) in (Withheld, OutcomeUnknown):
            success, text = fixed_model_message(ModelMessageKind.STOPPED)
            return RelayResult(tool_call_response(success, (text,)), TOOL_STOP_CODE)
        if type(outcome) is Denied:
            return self._fixed(outcome_model_message(outcome))
        # Anything else is not a known final outcome: stop rather than guess.
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
