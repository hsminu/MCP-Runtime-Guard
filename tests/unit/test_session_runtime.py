"""A-only product composition: B SQLite contracts, Task isolation and I7/I8/I9."""

from __future__ import annotations

import asyncio

import pytest

from guard_app.domain.contracts import ModelTurn
from guard_app.domain.enums import TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import Arguments
from guard_app.domain.models import ProposedCall
from guard_app.services.bridge import ControlAction, CredentialState, SessionOptions
from guard_app.services.runner import RunnerStateError
from guard_app.services.session_runtime import (
    CredentialOperations,
    SessionRuntime,
    SessionRuntimeError,
)
from guard_app.storage.sqlite_store import SQLiteStore


class Credentials:
    def __init__(self):
        self.value = "unit-only-credential"
        self.deleted = 0

    async def status(self):
        return CredentialState(
            True, bool(self.value), self.value[-4:] if self.value else "", "CREDENTIAL_READY"
        )

    async def load(self):
        return self.value

    async def save(self, value):
        self.value = value

    async def delete(self):
        self.deleted += 1
        self.value = ""

    def operations(self):
        return CredentialOperations(self.status, self.load, self.save, self.delete)


class Model:
    def __init__(self):
        self.messages = []
        self.closed = 0
        self.tools = []
        self.turn = None

    async def step(self, conversation, tools):
        self.messages.append(conversation)
        self.tools.append(tools)
        return self.turn or ModelTurn(text="unit answer", calls=())

    async def aclose(self):
        self.closed += 1


def make_runtime(tmp_path, *, with_credentials=True):
    store = SQLiteStore(tmp_path / "sessions.db", audit_id_factory=lambda: "unit-audit")
    credentials = Credentials()
    models = []

    def factory(mode, model, key):
        assert mode == "api"
        assert model == "unit-model"
        assert key == credentials.value
        client = Model()
        models.append(client)
        return client

    runtime = SessionRuntime(
        store,
        model_factory=factory,
        credentials=credentials.operations() if with_credentials else None,
        close_store=store.close,
    )
    return runtime, models, credentials


def test_two_real_sqlite_tasks_have_fresh_clients_and_only_their_own_conversation(tmp_path):
    async def run():
        runtime, models, _ = make_runtime(tmp_path)
        try:
            first = await runtime.create_session(SessionOptions("api", "unit-model"))
            second = await runtime.create_session(SessionOptions("api", "unit-model"))
            assert first.task_id != second.task_id
            assert first.model_ready
            assert second.model_ready
            one, two = (
                runtime.runner_for_task(first.task_id),
                runtime.runner_for_task(second.task_id),
            )
            one.add_user_message("first-private-text")
            two.add_user_message("second-private-text")
            assert (await one.advance()).assistant_text == "unit answer"
            assert (await two.advance()).assistant_text == "unit answer"
            first_contents = [message.content for turn in models[0].messages for message in turn]
            second_contents = [message.content for turn in models[1].messages for message in turn]
            assert "first-private-text" in first_contents
            assert "second-private-text" in second_contents
            assert "first-private-text" not in second_contents
            assert "second-private-text" not in first_contents
            assert models[0] is not models[1]
            assert all(model.tools == [()] for model in models)
            assert all(
                message.role != "tool"
                for model in models
                for turn in model.messages
                for message in turn
            )
            await runtime.stop_session(first.task_id, first.epoch, ControlAction.CANCEL)
            assert (await runtime.snapshot(first.task_id)).status is TaskStatus.HALTED
            assert not one._conversation
            assert models[0].closed == 1
            assert models[1].closed == 0
            assert two.can_accept_message
            assert (await runtime.snapshot(second.task_id)).status is TaskStatus.RUNNING
        finally:
            await runtime.close()
        assert all(model.closed == 1 for model in models)
        assert b"first-private-text" not in (tmp_path / "sessions.db").read_bytes()
        assert b"unit-only-credential" not in (tmp_path / "sessions.db").read_bytes()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("api", "WINDOWS_CREDENTIALS_UNAVAILABLE"),
        ("cli", "CLI_DEPENDENCIES_UNAVAILABLE"),
    ],
)
def test_missing_c_capabilities_create_task_but_never_create_a_model_or_call_tools(
    tmp_path, mode, expected
):
    async def run():
        runtime, models, _ = make_runtime(tmp_path, with_credentials=False)
        try:
            state = await runtime.create_session(SessionOptions(mode, "unit-model"))
            assert (await runtime.store.load_task(state.task_id)).status is TaskStatus.RUNNING
            assert not state.model_ready
            assert not state.tools_available
            assert state.connection_code == expected
            assert not models
            assert not (await runtime.save_credential("unit-input-never-saved")).available
        finally:
            await runtime.close()

    asyncio.run(run())


def test_conversation_only_model_cannot_inject_a_tool_or_resume_approval(tmp_path):
    async def run():
        runtime, models, _ = make_runtime(tmp_path)
        try:
            state = await runtime.create_session(SessionOptions("api", "unit-model"))
            models[0].turn = ModelTurn(
                calls=(
                    ProposedCall(
                        tool_ref="unregistered__write",
                        arguments=Arguments({}),
                        model_call_id="unit-call",
                    ),
                )
            )
            runner = runtime.runner_for_task(state.task_id)
            runner.add_user_message("hello")
            result = await runner.advance()
            assert result.status == "model_error"
            assert not runner.can_accept_message
            assert not runner.can_advance
            assert models[0].tools == [()]
            # Runner failure is distinct from a Store transition.
            assert (await runtime.snapshot(state.task_id)).status is TaskStatus.RUNNING
            assert (await runtime.snapshot(state.task_id)).runner_state == "model_error"
            with pytest.raises(RunnerStateError):
                runner.add_user_message("do not retry")
            with pytest.raises(RunnerStateError):
                await runner.resume_approval(state.task_id, 1, "invented", None)
        finally:
            await runtime.close()

    asyncio.run(run())


def test_delete_is_refused_until_each_active_api_client_has_stopped(tmp_path):
    async def run():
        runtime, models, credentials = make_runtime(tmp_path)
        try:
            state = await runtime.create_session(SessionOptions("api", "unit-model"))
            assert (await runtime.delete_credential()).code == "CREDENTIAL_IN_USE"
            assert credentials.deleted == 0
            await runtime.stop_session(state.task_id, state.epoch, ControlAction.EMERGENCY_HALT)
            assert models[0].closed == 1
            assert not (await runtime.delete_credential()).stored
            assert credentials.deleted == 1
        finally:
            await runtime.close()

    asyncio.run(run())


def test_old_epoch_stop_cannot_close_a_fresh_session(tmp_path):
    async def run():
        runtime, models, _ = make_runtime(tmp_path)
        try:
            state = await runtime.create_session(SessionOptions("api", "unit-model"))
            with pytest.raises(SessionRuntimeError, match="SESSION_STATE_MISMATCH"):
                await runtime.stop_session(state.task_id, state.epoch + 1, ControlAction.CANCEL)
            assert (await runtime.snapshot(state.task_id)).status is TaskStatus.RUNNING
            assert models[0].closed == 0
        finally:
            await runtime.close()

    asyncio.run(run())


def test_create_failure_closes_unadopted_client_without_returning_a_session(tmp_path, monkeypatch):
    async def run():
        runtime, models, _ = make_runtime(tmp_path)

        async def fail(*args, **kwargs):
            raise StoreError()

        monkeypatch.setattr(runtime.store, "create_task", fail)
        try:
            with pytest.raises(StoreError):
                await runtime.create_session(SessionOptions("api", "unit-model"))
            assert len(models) == 1
            assert models[0].closed == 1
            assert not runtime._runners
            assert not runtime._models
        finally:
            await runtime.close()

    asyncio.run(run())
