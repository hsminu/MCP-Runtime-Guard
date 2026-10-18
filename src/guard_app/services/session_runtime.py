"""A-owned conversation-only composition over the existing B Store contract.

No Registry, transport, process launcher or credential implementation lives here.
Those capabilities must be explicitly injected; there are no fallback secrets.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from guard_app.adapters.model_client import ModelCompletionError
from guard_app.domain.contracts import Conversation, ModelTurn, ToolView
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.models import TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import ModelClient, Store
from guard_app.services.bridge import (
    BackendBindings,
    ControlAction,
    CredentialState,
    SessionOptions,
    TaskProjection,
)
from guard_app.services.runner import TaskRunner
from guard_app.storage.sqlite_store import SQLiteStore


class ClosableModel(ModelClient, Protocol):
    async def aclose(self) -> None: ...


type ModelFactory = Callable[[str, str, str], ClosableModel]


@dataclass(frozen=True, slots=True, repr=False)
class CredentialOperations:
    """A-local injected capability; C's Windows credential adapter is pending."""

    status: Callable[[], Awaitable[CredentialState]]
    load: Callable[[], Awaitable[str]]
    save: Callable[[str], Awaitable[None]]
    delete: Callable[[], Awaitable[None]]


class SessionRuntimeError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _UnavailableModel:
    async def step(self, conversation: Conversation, tools: tuple[ToolView, ...]) -> ModelTurn:
        raise ModelCompletionError()

    async def aclose(self) -> None:
        pass


class SessionRuntime:
    """One backend thread, one Store writer, and a fresh client per Task."""

    def __init__(
        self,
        store: Store,
        *,
        model_factory: ModelFactory | None = None,
        credentials: CredentialOperations | None = None,
        close_store: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self._model_factory = model_factory
        self._credentials = credentials
        self._close_store = close_store
        self._runners: dict[str, TaskRunner] = {}
        self._models: dict[str, ClosableModel] = {}
        self._options: dict[str, SessionOptions] = {}
        self._codes: dict[str, str] = {}
        self._closed = False

    def runner_for_task(self, task_id: str) -> TaskRunner:
        if self._closed or task_id not in self._runners:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        return self._runners[task_id]

    async def credential_status(self) -> CredentialState:
        if self._credentials is None:
            return CredentialState(False)
        state = await self._credentials.status()
        if type(state) is not CredentialState:
            raise SessionRuntimeError("CREDENTIAL_UNCONFIRMED")
        if len(state.suffix) > 4 or (state.suffix and not state.stored):
            raise SessionRuntimeError("CREDENTIAL_UNCONFIRMED")
        return state

    async def save_credential(self, secret: str) -> CredentialState:
        if self._credentials is None:
            return CredentialState(False)
        await self._credentials.save(secret)
        return await self.credential_status()

    async def delete_credential(self) -> CredentialState:
        if self._credentials is None:
            return CredentialState(False)
        if any(self._codes.get(task_id) == "READY_CHAT_ONLY" for task_id in self._models):
            current = await self.credential_status()
            return CredentialState(
                current.available, current.stored, current.suffix, "CREDENTIAL_IN_USE"
            )
        await self._credentials.delete()
        return await self.credential_status()

    async def _new_model(self, options: SessionOptions) -> tuple[ClosableModel, str]:
        if options.mode == "cli":
            return _UnavailableModel(), "CLI_DEPENDENCIES_UNAVAILABLE"
        if not options.model:
            return _UnavailableModel(), "MODEL_ID_REQUIRED"
        state = await self.credential_status()
        if not state.available:
            return _UnavailableModel(), "WINDOWS_CREDENTIALS_UNAVAILABLE"
        if not state.stored:
            return _UnavailableModel(), "MODEL_KEY_MISSING"
        if self._model_factory is None or self._credentials is None:
            return _UnavailableModel(), "API_ADAPTER_UNAVAILABLE"
        secret = await self._credentials.load()
        if type(secret) is not str or not secret.strip():
            raise SessionRuntimeError("MODEL_KEY_MISSING")
        try:
            client = self._model_factory(options.mode, options.model, secret)
        finally:
            del secret
        if not callable(getattr(client, "step", None)) or not callable(
            getattr(client, "aclose", None)
        ):
            raise SessionRuntimeError("MODEL_CLIENT_INVALID")
        return client, "READY_CHAT_ONLY"

    async def create_session(self, options: SessionOptions) -> TaskProjection:
        if self._closed or type(options) is not SessionOptions:
            raise SessionRuntimeError("SESSION_UNAVAILABLE")
        client, code = await self._new_model(options)
        state = TaskState(
            task_id=f"session-{uuid4().hex}",
            epoch=1,
            profile_id="conversation-only",
            policy_version="conversation-only-v1",
            status=TaskStatus.RUNNING,
        )
        created = False
        adopted = False
        try:
            await self.store.create_task(state, created_at_utc=datetime.now(UTC))
            created = True
            confirmed = await self.store.load_task(state.task_id)
            if confirmed != state:
                raise StoreError()
            runner = TaskRunner(
                state,
                model_client_factory=lambda: client,
                guard=None,
                catalog=None,
                store=self.store,
                server_ids=(),
                system_message=(
                    "개인 사용자와 대화하는 모델이다. 현재 앱에는 사용할 수 있는 도구가 없다. "
                    "파일 읽기, 네트워크 작업, 명령 실행을 했다고 주장하지 않는다."
                ),
            )
            self._runners[state.task_id] = runner
            self._models[state.task_id] = client
            self._options[state.task_id] = options
            self._codes[state.task_id] = code
            snapshot = await self.snapshot(state.task_id)
            adopted = True
            return snapshot
        finally:
            if not adopted:
                try:
                    discarded_runner = self._runners.pop(state.task_id, None)
                    if discarded_runner is not None:
                        discarded_runner.close()
                    self._models.pop(state.task_id, None)
                    self._options.pop(state.task_id, None)
                    self._codes.pop(state.task_id, None)
                    await client.aclose()
                finally:
                    if created:
                        current = await self.store.load_task(state.task_id)
                        await self.store.set_task_status(
                            state.task_id,
                            TaskStatus.HALTED,
                            expected_row_revision=current.row_revision,
                            at_utc=datetime.now(UTC),
                            reason=Reason.INTERNAL_ERROR,
                        )

    async def snapshot(self, task_id: str) -> TaskProjection:
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != runner.epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        options = self._options[task_id]
        return TaskProjection(
            task_id,
            state.epoch,
            state.status,
            model_mode=options.mode,
            model_id=options.model,
            model_ready=self._codes[task_id] == "READY_CHAT_ONLY",
            connection_code=self._codes[task_id],
            tools_available=False,
            can_advance=runner.can_advance,
            runner_state=runner.runtime_status,
        )

    async def stop_session(self, task_id: str, epoch: int, action: ControlAction) -> None:
        if action not in (ControlAction.CANCEL, ControlAction.EMERGENCY_HALT):
            raise SessionRuntimeError("CONTROL_UNAVAILABLE")
        runner = self.runner_for_task(task_id)
        state = await self.store.load_task(task_id)
        if state.task_id != task_id or state.epoch != epoch or runner.epoch != epoch:
            raise SessionRuntimeError("SESSION_STATE_MISMATCH")
        if state.status in (TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED):
            return
        updated = await self.store.set_task_status(
            task_id,
            TaskStatus.HALTED,
            expected_row_revision=state.row_revision,
            at_utc=datetime.now(UTC),
            reason=Reason.TASK_NOT_RUNNING,
        )
        if (
            updated.task_id != task_id
            or updated.epoch != epoch
            or updated.status is not TaskStatus.HALTED
        ):
            raise SessionRuntimeError("SESSION_STOP_UNCONFIRMED")
        runner.close()
        await self._close_model(task_id)

    async def _close_model(self, task_id: str) -> None:
        model = self._models.pop(task_id, None)
        if model is not None:
            await model.aclose()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for runner in self._runners.values():
                runner.close()
            # Try every client even if another close fails; propagate a fixed
            # cleanup failure only after all clients have had their close call.
            failed = False
            for task_id in tuple(self._models):
                completed = False
                try:
                    await self._close_model(task_id)
                    completed = True
                except (OSError, ValueError, RuntimeError):
                    failed = True
                finally:
                    if not completed:
                        failed = True
            if failed:
                raise SessionRuntimeError("MODEL_CLEANUP_UNCONFIRMED")
        finally:
            self._runners.clear()
            self._options.clear()
            self._codes.clear()
            if self._close_store is not None:
                self._close_store()

    def bindings(self) -> BackendBindings:
        return BackendBindings(
            self.runner_for_task,
            None,
            close=self.close,
            snapshot=self.snapshot,
            create_session=self.create_session,
            stop_session=self.stop_session,
            credential_status=self.credential_status,
            save_credential=self.save_credential,
            delete_credential=self.delete_credential,
        )


async def bootstrap_product(
    db_path: str,
    *,
    model_factory: ModelFactory | None = None,
    credentials: CredentialOperations | None = None,
) -> BackendBindings:
    store = SQLiteStore(db_path, audit_id_factory=lambda: f"audit-{uuid4().hex}")
    runtime = SessionRuntime(
        store,
        model_factory=model_factory,
        credentials=credentials,
        close_store=store.close,
    )
    return runtime.bindings()
