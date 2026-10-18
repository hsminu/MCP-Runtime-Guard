"""One Task's model loop. GuardService is the only managed tool-call path.

The caller creates a fresh TaskRunner and ModelClient for each Store-created
Task. Approval decisions arrive from the backend's human-only control channel;
the Runner never exposes that channel as a model tool.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from guard_app.adapters.model_client import (
    ModelFormatError,
    validate_model_conversation_budget,
)
from guard_app.domain.contracts import (
    ApprovalRecord,
    ConversationMessage,
    Denied,
    ModelTurn,
    Outcome,
    OutcomeUnknown,
    Pending,
    Released,
    Withheld,
)
from guard_app.domain.enums import ApprovalStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import ModelClient, Store
from guard_app.services.catalog import Catalog, CatalogConfigError
from guard_app.services.guard import GuardService

_PENDING_TEXT = "승인을 기다리는 중입니다."
_DENIED_TEXT = "이 요청은 실행되지 않았습니다."
_SKIPPED_TEXT = "이 호출은 실행되지 않았습니다."
# Fixed notice for a READ the gate did not disclose. No result data, reason,
# label or request status enters the model context.
_WITHHELD_TEXT = "이 호출 결과는 공개되지 않았습니다."

# Guard containment without a policy decision. Guard may hold only an
# in-memory global halt while Store still says RUNNING, so these stop the
# Runner regardless of the Store status.
_GUARD_FAILURE_REASONS = frozenset(
    (Reason.INTERNAL_ERROR, Reason.STATE_MISSING, Reason.STATE_COMMIT_FAILED)
)
# A pre-decision denial caused by the Task itself (stop latch, epoch, policy
# or classification). The Runner stops even if Store has not caught up yet.
_TASK_STOP_REASONS = frozenset(
    (Reason.TASK_NOT_RUNNING, Reason.POLICY_CHANGED, Reason.TASK_UNCLASSIFIED)
)
# Pre-decision resolution of the model's proposed call. Only these, without
# a request ID, are an ordinary deny that may accept another user turn.
_RESOLUTION_DENY_REASONS = frozenset(
    (
        Reason.INVALID_ARGUMENTS,
        Reason.TOOL_UNSUPPORTED,
        Reason.UNKNOWN_DESTINATION,
        Reason.UNKNOWN_SERVER,
        Reason.REG_NOT_ACTIVE,
        Reason.TOOL_FINGERPRINT_CHANGED,
    )
)
# Approval states after which no actual human-control result can follow.
_SETTLED_APPROVALS = frozenset(
    (
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
        ApprovalStatus.INVALIDATED,
    )
)

type RunStatus = Literal[
    "complete",
    "continue",
    "pending",
    "denied",
    "withheld",
    "halted",
    "outcome_unknown",
    "model_error",
    "guard_error",
    "closed",
    "stopped",
]


class RunnerStateError(ValueError):
    """Fixed message for stale or invalid Task continuation."""

    def __init__(self) -> None:
        super().__init__("작업 실행 상태가 일치하지 않는다")


@dataclass(frozen=True, slots=True)
class RunUpdate:
    """Local loop state for the UI, not an authoritative Store TaskStatus.

    In particular, ``closed`` only invalidates this Runner's conversation.
    The backend must use B/Store's confirmed state for Task status and display.
    ``withheld`` means Store kept the Task RUNNING after an undisclosed READ
    result; the turn ended and a new user turn may follow. ``guard_error``
    means Guard contained an internal failure (for example an in-memory
    global halt); this Runner stops even when Store still says RUNNING.
    """

    task_id: str
    epoch: int
    status: RunStatus
    approval_id: str | None = None
    assistant_text: str | None = field(default=None, repr=False)


def _denied_kind(outcome: Denied) -> Literal["deny", "guard_error", "task_stop"]:
    """Separate Guard containment and Task stops from an ordinary deny."""
    reasons = frozenset(outcome.reasons)
    if reasons & _GUARD_FAILURE_REASONS:
        return "guard_error"
    if outcome.request_id is None:
        if reasons & _TASK_STOP_REASONS:
            return "task_stop"
        if not reasons or not reasons <= _RESOLUTION_DENY_REASONS:
            # Unknown pre-decision reasons (for example a StoreError reason
            # that also set the global halt) fail closed.
            return "guard_error"
    return "deny"


class TaskRunner:
    """Task-owned conversation and explicit one-turn progression.

    Each ``advance`` asks the model once, then serially submits all proposed
    calls to Guard. A pending approval pauses the remaining calls. There is no
    automatic model retry after DENY or an unknown SEND outcome.
    """

    def __init__(
        self,
        state: TaskState,
        *,
        model_client_factory: Callable[[], ModelClient],
        guard: GuardService,
        catalog: Catalog,
        store: Store,
        server_ids: tuple[str, ...],
        system_message: str,
    ) -> None:
        state = TaskState.model_validate(state)
        if state.status is not TaskStatus.RUNNING:
            raise RunnerStateError()
        if (
            type(server_ids) is not tuple
            or any(type(server_id) is not str for server_id in server_ids)
            or type(system_message) is not str
            or not callable(model_client_factory)
        ):
            raise RunnerStateError()
        self.task_id = state.task_id
        self.epoch = state.epoch
        self._profile_id = state.profile_id
        self._policy_version = state.policy_version
        # The client, including any provider handle/cache, is created for this
        # Task only. A20 must provide a factory with a genuinely fresh backend.
        self._model = model_client_factory()
        if not callable(getattr(self._model, "step", None)):
            raise RunnerStateError()
        self._guard = guard
        self._catalog = catalog
        self._store = store
        self._server_ids = server_ids
        self._conversation = [ConversationMessage(role="system", content=system_message)]
        self._seen_call_ids: set[str] = set()
        self._pending: Pending | None = None
        self._pending_call_id: str | None = None
        self._pending_message_index: int | None = None
        self._remaining: tuple[ProposedCall, ...] = ()
        self._awaiting_user = True
        self._stopped = False
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def pending_approval_id(self) -> str | None:
        """이번 Runner가 Pending으로 받은 ID만 제공한다. 실행 권한은 아니다."""
        return self._pending.approval_id if self._pending is not None else None

    @property
    def can_accept_message(self) -> bool:
        """A local input capability; Task state is still checked by the bridge."""
        return bool(
            self._awaiting_user
            and not (self._closed or self._stopped or self._pending or self._lock.locked())
        )

    @property
    def can_advance(self) -> bool:
        return not (
            self._closed
            or self._stopped
            or self._pending
            or self._awaiting_user
            or self._lock.locked()
        )

    def add_user_message(self, content: str) -> None:
        if (
            self._closed
            or self._stopped
            or self._pending is not None
            or self._lock.locked()
            or not self._awaiting_user
            or type(content) is not str
        ):
            raise RunnerStateError()
        self._conversation.append(ConversationMessage(role="user", content=content))
        self._awaiting_user = False

    async def advance(self) -> RunUpdate:
        """Run one model turn; no model call occurs while approval is pending."""
        async with self._lock:
            if self._closed or self._stopped or self._pending or self._awaiting_user:
                raise RunnerStateError()
            return await self._stop_on_error(self._advance_locked())

    async def _advance_locked(self) -> RunUpdate:
        task_status = await self._task_run_state()
        if self._closed:
            return self._update("closed")
        if task_status != "running":
            return self._update(task_status)
        try:
            views = self._catalog.tool_views(self._server_ids)
        except CatalogConfigError:
            self._stopped = True
            return self._update("stopped")
        try:
            conversation = tuple(self._conversation)
            validate_model_conversation_budget(conversation)
            turn = await self._model.step(conversation, views)
        except ModelFormatError:
            if self._closed:
                return self._update("closed")
            self._stopped = True
            return self._update("model_error")
        if self._closed:
            return self._update("closed")
        task_status = await self._task_run_state()
        if self._closed:
            return self._update("closed")
        if task_status != "running":
            return self._update(task_status)
        try:
            turn = ModelTurn.model_validate(turn)
        except ValidationError:
            self._stopped = True
            return self._update("model_error")
        if not self._valid_calls(turn, tuple(view.tool_ref for view in views)):
            self._stopped = True
            return self._update("model_error")
        self._conversation.append(
            ConversationMessage(role="assistant", content=turn.text or "", calls=turn.calls)
        )
        self._seen_call_ids.update(call.model_call_id for call in turn.calls)
        if not turn.calls:
            self._awaiting_user = True
            return self._update("complete", assistant_text=turn.text)
        return await self._dispatch(turn.calls)

    async def resume_approval(
        self, task_id: str, epoch: int, approval_id: str, outcome: Outcome
    ) -> RunUpdate:
        """Consume the exact backend-confirmed human-control result once.

        The backend routes the result to the same Task and epoch; this method
        additionally checks approval and request IDs before changing history.
        """
        async with self._lock:
            pending = self._pending
            if (
                self._closed
                or self._stopped
                or pending is None
                or task_id != self.task_id
                or epoch != self.epoch
                or approval_id != pending.approval_id
                or not isinstance(outcome, (Released, Denied, Withheld, OutcomeUnknown))
            ):
                raise RunnerStateError()
            # Approval-path containment answers this exact approval without a
            # request ID. It is a terminal approval failure, not a stale result.
            contained = isinstance(outcome, Denied) and outcome.request_id is None
            if not contained and outcome.request_id != pending.request_id:
                raise RunnerStateError()
            if self._pending_message_index is None or self._pending_call_id is None:
                raise RunnerStateError()
            return await self._stop_on_error(self._resume_locked(pending, outcome))

    async def _resume_locked(self, pending: Pending, outcome: Outcome) -> RunUpdate:
        if isinstance(outcome, Denied):
            kind = _denied_kind(outcome)
            if outcome.request_id is None and kind == "deny":
                kind = "guard_error"
            if kind != "deny":
                return await self._stop_pending(kind)
            approval_race = outcome.reasons == (Reason.APPROVAL_RACE,)
            # A premature expiry can leave the Task waiting, but a
            # concurrent cancel or policy change can stop it first.
            task_status = await self._task_run_state(allow_waiting_approval=approval_race)
            if self._closed:
                return self._update("closed")
            if task_status in ("halted", "outcome_unknown", "stopped"):
                return self._update(task_status)
            if approval_race and task_status == "pending":
                # This is not a confirmed decision. Keep the placeholder
                # until the actual human-control result arrives.
                return self._update("pending", approval_id=pending.approval_id)
            if approval_race:
                # B resumed the Task to RUNNING. Only a settled approval record
                # proves that no actual human-control result can follow.
                settled = await self._approval_settled(pending)
                if self._closed:
                    return self._update("closed")
                if settled is None:
                    self._stopped = True
                    return self._update("stopped")
                if not settled:
                    return self._update("pending", approval_id=pending.approval_id)
        message_index, call_id, remaining = self._take_pending()
        if isinstance(outcome, Released):
            task_status = await self._task_run_state()
            if self._closed:
                return self._update("closed")
            if task_status != "running":
                return self._update(task_status)
            self._conversation[message_index] = self._released_message(call_id, outcome)
            return await self._dispatch(remaining)
        if isinstance(outcome, Denied):
            self._conversation[message_index] = ConversationMessage(
                role="tool", content=_DENIED_TEXT, model_call_id=call_id
            )
            self._append_skipped(remaining)
            self._awaiting_user = True
            return self._update("denied")
        if isinstance(outcome, Withheld):
            task_status = await self._task_run_state()
            if self._closed:
                return self._update("closed")
            if task_status == "running":
                # Store kept the Task RUNNING (a READ without data). Only the
                # fixed notice replaces the placeholder; the turn ends here.
                self._conversation[message_index] = ConversationMessage(
                    role="tool", content=_WITHHELD_TEXT, model_call_id=call_id
                )
                self._append_skipped(remaining)
                self._awaiting_user = True
                return self._update("withheld")
        # A post-dispatch failure cannot be called a blocked request. The
        # placeholder is discarded because the model must never continue.
        del self._conversation[message_index]
        self._stopped = True
        if isinstance(outcome, OutcomeUnknown):
            return self._update("outcome_unknown")
        return self._update("halted")

    def _take_pending(self) -> tuple[int, str, tuple[ProposedCall, ...]]:
        message_index = self._pending_message_index
        call_id = self._pending_call_id
        if message_index is None or call_id is None:
            raise RunnerStateError()
        remaining = self._remaining
        self._pending = None
        self._pending_call_id = None
        self._pending_message_index = None
        self._remaining = ()
        return message_index, call_id, remaining

    async def _stop_pending(self, kind: Literal["guard_error", "task_stop"]) -> RunUpdate:
        """Drop the approval placeholder and stop without another model turn."""
        message_index, _, _ = self._take_pending()
        del self._conversation[message_index]
        return await self._contained_stop(kind)

    async def _contained_stop(self, kind: Literal["guard_error", "task_stop"]) -> RunUpdate:
        self._stopped = True
        task_status = await self._task_run_state()
        if self._closed:
            return self._update("closed")
        if kind == "guard_error":
            return self._update("guard_error")
        return self._update("stopped" if task_status == "running" else task_status)

    async def _approval_settled(self, pending: Pending) -> bool | None:
        """True only for a terminal record of this approval; None if unconfirmed."""
        try:
            record = ApprovalRecord.model_validate(
                await self._store.get_approval(pending.approval_id)
            )
        except (StoreError, ValidationError):
            return None
        if record.approval_id != pending.approval_id or record.request_id != pending.request_id:
            return None
        return record.status in _SETTLED_APPROVALS

    async def _stop_on_error(self, work: Awaitable[RunUpdate]) -> RunUpdate:
        """Leave no runnable loop after an unclassified failure.

        The error itself propagates unchanged to the bridge, which reports a
        fixed code; this only keeps the Runner from accepting another turn.
        """
        completed = False
        try:
            update = await work
            completed = True
            return update
        finally:
            if not completed:
                self._stopped = True

    def close(self) -> None:
        """Invalidate late model/approval responses and drop local history."""
        self._closed = True
        self._stopped = True
        self._pending = None
        self._remaining = ()
        self._conversation.clear()

    async def _task_run_state(
        self, *, allow_waiting_approval: bool = False
    ) -> Literal["running", "pending", "halted", "outcome_unknown", "stopped"]:
        try:
            state = TaskState.model_validate(await self._store.load_task(self.task_id))
        except (StoreError, ValidationError):
            self._stopped = True
            return "stopped"
        if (
            state.task_id != self.task_id
            or state.epoch != self.epoch
            or state.profile_id != self._profile_id
            or state.policy_version != self._policy_version
            or state.unclassified
        ):
            self._stopped = True
            return "stopped"
        if state.status is TaskStatus.WAITING_APPROVAL and allow_waiting_approval:
            return "pending"
        if state.status is not TaskStatus.RUNNING:
            self._stopped = True
            if state.status is TaskStatus.HALTED:
                return "halted"
            if state.status is TaskStatus.UNKNOWN_OUTCOME:
                return "outcome_unknown"
            return "stopped"
        return "running"

    def _valid_calls(self, turn: ModelTurn, advertised: tuple[str, ...]) -> bool:
        call_ids = tuple(call.model_call_id for call in turn.calls)
        return (
            len(call_ids) == len(set(call_ids))
            and not self._seen_call_ids.intersection(call_ids)
            and all(call.tool_ref in advertised for call in turn.calls)
        )

    async def _dispatch(self, calls: tuple[ProposedCall, ...]) -> RunUpdate:
        for index, call in enumerate(calls):
            if self._closed:
                return self._update("closed")
            # A previous call can change or replace the Task before the next
            # one is submitted. This local check narrows that window; Guard
            # must still bind its own submit to the expected epoch atomically.
            task_status = await self._task_run_state()
            if self._closed:
                return self._update("closed")
            if task_status != "running":
                return self._update(task_status)
            outcome = await self._guard.submit(self.task_id, call, expected_epoch=self.epoch)
            if self._closed:
                # Closing the local conversation cannot reclassify a call that
                # Guard already reserved. In particular, a SEND uncertainty
                # remains unknown even when its Task view has closed.
                if isinstance(outcome, OutcomeUnknown):
                    return self._update("outcome_unknown")
                if isinstance(outcome, Withheld):
                    return self._update("halted")
                return self._update("closed")
            if isinstance(outcome, Released):
                self._conversation.append(self._released_message(call.model_call_id, outcome))
                continue
            if isinstance(outcome, Pending):
                self._pending = outcome
                self._pending_call_id = call.model_call_id
                self._remaining = calls[index + 1 :]
                self._pending_message_index = len(self._conversation)
                self._conversation.append(
                    ConversationMessage(
                        role="tool",
                        content=_PENDING_TEXT,
                        model_call_id=call.model_call_id,
                    )
                )
                return self._update("pending", approval_id=outcome.approval_id)
            if isinstance(outcome, Denied):
                kind = _denied_kind(outcome)
                if kind != "deny":
                    return await self._contained_stop(kind)
                # Guard can halt the Task while returning Denied (for example
                # when its current policy version differs). Only a Task that
                # is still RUNNING may accept another user turn.
                task_status = await self._task_run_state()
                if self._closed:
                    return self._update("closed")
                if task_status != "running":
                    return self._update(task_status)
                self._conversation.append(
                    ConversationMessage(
                        role="tool",
                        content=_DENIED_TEXT,
                        model_call_id=call.model_call_id,
                    )
                )
                self._append_skipped(calls[index + 1 :])
                self._awaiting_user = True
                return self._update("denied")
            if isinstance(outcome, Withheld):
                # B can commit a failed READ without data and keep the Task
                # RUNNING. Store decides; withheld data never reaches the model.
                task_status = await self._task_run_state()
                if self._closed:
                    return self._update("halted")
                if task_status != "running":
                    self._stopped = True
                    return self._update(
                        "outcome_unknown" if task_status == "outcome_unknown" else "halted"
                    )
                self._conversation.append(
                    ConversationMessage(
                        role="tool",
                        content=_WITHHELD_TEXT,
                        model_call_id=call.model_call_id,
                    )
                )
                self._append_skipped(calls[index + 1 :])
                self._awaiting_user = True
                return self._update("withheld")
            self._stopped = True
            if isinstance(outcome, OutcomeUnknown):
                return self._update("outcome_unknown")
            return self._update("halted")
        return self._update("continue")

    def _append_skipped(self, calls: tuple[ProposedCall, ...]) -> None:
        for call in calls:
            self._conversation.append(
                ConversationMessage(
                    role="tool", content=_SKIPPED_TEXT, model_call_id=call.model_call_id
                )
            )

    @staticmethod
    def _released_message(call_id: str, outcome: Released) -> ConversationMessage:
        # The gate has already committed and returned this ReleasedResult. No
        # RawResult, label, boundary, reason or policy version enters content.
        result = outcome.result
        content = json.dumps(
            {
                "text": result.text,
                "structured_content": (
                    None
                    if result.structured_content is None
                    else result.structured_content.to_dict()
                ),
                "is_error": result.is_error,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ConversationMessage(role="tool", content=content, model_call_id=call_id)

    def _update(
        self,
        status: RunStatus,
        *,
        approval_id: str | None = None,
        assistant_text: str | None = None,
    ) -> RunUpdate:
        return RunUpdate(
            task_id=self.task_id,
            epoch=self.epoch,
            status=status,
            approval_id=approval_id,
            assistant_text=assistant_text,
        )
