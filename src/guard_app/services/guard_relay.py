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


class ApprovalOutcomes:
    """One loop's waiting tool calls, keyed by Guard approval_id."""

    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future[FinalApproval]] = {}

    def waiting(self, approval_id: str) -> bool:
        return approval_id in self._waiting

    async def wait(self, pending: Pending) -> FinalApproval:
        future: asyncio.Future[FinalApproval] = asyncio.get_running_loop().create_future()
        if pending.approval_id in self._waiting:
            raise RuntimeError("approval already awaited")
        self._waiting[pending.approval_id] = future
        try:
            return await future
        finally:
            self._waiting.pop(pending.approval_id, None)

    def resolve(self, approval_id: str, action: str, outcome: object) -> None:
        future = self._waiting.get(approval_id)
        if future is None or future.done():
            return
        if action == "approve":
            future.set_result(FinalApproval(ModelMessageKind.RELEASED, outcome))
        elif action == "reject":
            future.set_result(FinalApproval(ModelMessageKind.APPROVAL_REJECTED))
        else:
            future.set_result(FinalApproval(ModelMessageKind.APPROVAL_CANCELLED))


class GuardToolRelay:
    """Run one Codex tool call through Guard; the caller serializes per Task."""

    def __init__(
        self,
        task: TrustedTask,
        guard: object,
        approvals: ApprovalOutcomes,
    ) -> None:
        submit = getattr(guard, "submit", None)
        if not callable(submit):
            raise ValueError("Guard submit entry required")
        self._task = task
        self._submit = submit
        self._approvals = approvals
        self.submitted = 0

    async def relay(self, call: DynamicToolCall) -> RelayResult:
        if call.namespace is None:
            return self._fixed(fixed_model_message(ModelMessageKind.NAMESPACE_MISSING))
        try:
            # Validate untrusted model input through the domain model, never by hand.
            proposed = ProposedCall.model_validate(
                {
                    "tool_ref": f"{call.namespace}__{call.tool}",
                    "arguments": call.arguments,
                    "model_call_id": call.call_id,
                }
            )
        except ValidationError:
            return self._fixed(
                fixed_model_message(ModelMessageKind.DENIED, Reason.INVALID_ARGUMENTS)
            )
        self.submitted += 1
        outcome = await self._submit(self._task.task_id, proposed, expected_epoch=self._task.epoch)
        while type(outcome) is Pending:
            final = await self._approvals.wait(outcome)
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

    @staticmethod
    def _fixed(message: tuple[bool, str]) -> RelayResult:
        success, text = message
        return RelayResult(tool_call_response(success, (text,)))
