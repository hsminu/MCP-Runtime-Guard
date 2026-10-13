"""One Task's model loop. GuardService is the only managed tool-call path.

The caller creates a fresh TaskRunner and ModelClient for each Store-created
Task. Approval decisions arrive from the backend's human-only control channel;
the Runner never exposes that channel as a model tool.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from guard_app.adapters.model_client import (
    ModelFormatError,
    validate_model_conversation_budget,
)
from guard_app.domain.contracts import (
    ConversationMessage,
    Denied,
    ModelTurn,
    Outcome,
    OutcomeUnknown,
    Pending,
    Released,
    Withheld,
)
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import ModelClient, Store
from guard_app.services.catalog import Catalog, CatalogConfigError
from guard_app.services.guard import GuardService

_PENDING_TEXT = "승인을 기다리는 중입니다."
_DENIED_TEXT = "이 요청은 실행되지 않았습니다."
_SKIPPED_TEXT = "이 호출은 실행되지 않았습니다."


class RunnerStateError(ValueError):
    """Fixed message for stale or invalid Task continuation."""

    def __init__(self) -> None:
        super().__init__("작업 실행 상태가 일치하지 않는다")


@dataclass(frozen=True, slots=True)
class RunUpdate:
    """Local loop state for the UI, not an authoritative Store TaskStatus.

    In particular, ``closed`` only invalidates this Runner's conversation.
    The backend must use B/Store's confirmed state for Task status and display.
    """

    task_id: str
    epoch: int
    status: Literal[
        "complete",
        "continue",
        "pending",
        "denied",
        "halted",
        "outcome_unknown",
        "model_error",
        "closed",
        "stopped",
    ]
    approval_id: str | None = None
    assistant_text: str | None = field(default=None, repr=False)


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
                or outcome.request_id != pending.request_id
            ):
                raise RunnerStateError()
            if self._pending_message_index is None or self._pending_call_id is None:
                raise RunnerStateError()
            if isinstance(outcome, Denied) and outcome.reasons == (Reason.APPROVAL_RACE,):
                # B also returns this result for a premature expire or a
                # reject while approval is still live. It is not a confirmed
                # decision on the pending request; retain the placeholder and
                # wait for the actual control result.
                return self._update("pending", approval_id=pending.approval_id)
            if isinstance(outcome, Denied):
                # A policy/context failure can halt the Task instead of
                # resuming it. Do not advertise a new user turn in that case.
                task_status = await self._task_run_state()
                if self._closed:
                    return self._update("closed")
                if task_status != "running":
                    return self._update(task_status)
            message_index = self._pending_message_index
            call_id = self._pending_call_id
            remaining = self._remaining
            self._pending = None
            self._pending_call_id = None
            self._pending_message_index = None
            self._remaining = ()
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
            # A post-dispatch failure cannot be called a blocked request. The
            # placeholder is discarded because the model must never continue.
            del self._conversation[message_index]
            self._stopped = True
            if isinstance(outcome, OutcomeUnknown):
                return self._update("outcome_unknown")
            return self._update("halted")

    def close(self) -> None:
        """Invalidate late model/approval responses and drop local history."""
        self._closed = True
        self._stopped = True
        self._pending = None
        self._remaining = ()
        self._conversation.clear()

    async def _task_run_state(
        self,
    ) -> Literal["running", "halted", "outcome_unknown", "stopped"]:
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
            if isinstance(outcome, (Withheld, OutcomeUnknown)):
                self._stopped = True
                return self._update(
                    "outcome_unknown" if isinstance(outcome, OutcomeUnknown) else "halted"
                )
            self._stopped = True
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
        status: Literal[
            "complete",
            "continue",
            "pending",
            "denied",
            "halted",
            "outcome_unknown",
            "model_error",
            "closed",
            "stopped",
        ],
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
