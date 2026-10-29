"""A139: A callers of C2.7; I3/I7/I9, T08/T21/T46 (local wiring only)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard_app.services import (
    bridge,
    bridge_ingress,
    codex_preparation,
    session_cleanup,
    session_runtime,
)
from guard_app.services.bridge import ControlAction, SessionOptions
from guard_app.services.session_preparation import TaskResources

from .product_folders import product_folders
from .test_product_assembly import (
    READ_DEFINITION,
    Peer,
    Registry,
    document,
    running,
    tool_fingerprint,
)
from .test_session_preparation import CONFIGURATION, Harness
from .test_session_runtime import make_runtime
from .test_ui_sessions import app, snapshot, window  # noqa: F401 - Qt fixtures


def assert_ids(values, prefix):
    assert len(values) >= 2
    assert len(set(values)) == len(values)
    assert all(re.fullmatch(re.escape(prefix) + r"[0-9a-f]{32}", value) for value in values)


def test_bridge_generated_ids_and_explicit_id_preservation():
    assert_ids([bridge_ingress.bridge_id(None, generate=True) for _ in range(32)], "command-")
    assert_ids([bridge.new_command_id() for _ in range(32)], "command-")
    assert_ids([bridge.new_draft_key() for _ in range(32)], "draft:")
    assert bridge_ingress.bridge_id("existing-command") == "existing-command"


@pytest.mark.asyncio
async def test_guard_id_factories_keep_storage_format(tmp_path):
    async with asyncio.timeout(10):
        peer = Peer([READ_DEFINITION])
        async with running(
            tmp_path, peer, Registry(tool_fingerprint(READ_DEFINITION)), document()
        ) as (assembled, _store):
            guard = assembled.guard
            assert_ids([guard._request_id_factory() for _ in range(32)], "request-")
            assert_ids([guard._approvals._approval_id_factory() for _ in range(32)], "approval-")


@pytest.mark.asyncio
async def test_session_ids_are_distinct_persisted_task_keys(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    async with asyncio.timeout(10):
        try:
            states = [await runtime.create_session(SessionOptions()) for _ in range(4)]
            assert_ids([state.task_id for state in states], "session-")
            for state in states:
                assert (await runtime.store.load_task(state.task_id)).task_id == state.task_id
        finally:
            await runtime.close()


@pytest.mark.asyncio
async def test_product_store_receives_distinct_audit_factory(tmp_path, monkeypatch):
    factories = []
    original = session_runtime.SQLiteStore

    def record(*args, **kwargs):
        factories.append(kwargs["audit_id_factory"])
        return original(*args, **kwargs)

    monkeypatch.setattr(session_runtime, "SQLiteStore", record)
    async with asyncio.timeout(10):
        bindings = await session_runtime.bootstrap_product(
            product_folders(str(tmp_path / "product.db"))
        )
        try:
            assert len(factories) == 1
            assert_ids([factories[0]() for _ in range(32)], "audit-")
        finally:
            await bindings.close()


@pytest.mark.asyncio
async def test_preparation_uses_distinct_tokens_and_account_folder_ids(monkeypatch):
    folders_created = []

    def create_folder(name):
        folders_created.append(name)
        return Path(name)

    async def launch(folder):
        return object()

    folders = SimpleNamespace(create_task_folder=create_folder)
    launcher = SimpleNamespace(launch=launch)
    owner = codex_preparation.CodexProcessPreparation()
    owner._configuration = CONFIGURATION
    monkeypatch.setattr(owner, "_ready", lambda: (folders, launcher))
    async with asyncio.timeout(10):
        resources = [await owner._prepare_task(f"task-{i}", CONFIGURATION) for i in range(32)]
        tokens = [item.owner_token for item in resources]
        assert len(set(tokens)) == 32
        assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", token) for token in tokens)
        assert len(owner._tasks) == 32
        for item in resources:
            assert owner._task(item).process is item.handle
        folders_created.clear()
        for _ in range(32):
            await owner._launch_account()
        assert_ids(folders_created, "account-")


def test_resource_claim_uses_token_comparator(monkeypatch):
    harness = Harness()
    cleanup = harness.runtime._cleanup
    first = TaskResources("first", CONFIGURATION, "first-token", object())
    second = TaskResources("second", CONFIGURATION, "second-token", object())
    cleanup.claim_resources("first", first)
    comparisons = []

    def matches(expected, received):
        comparisons.append((expected, received))
        return True

    monkeypatch.setattr(session_cleanup, "token_matches", matches)
    with pytest.raises(session_runtime.SessionRuntimeError, match="CODEX_CLEANUP_UNCONFIRMED"):
        cleanup.claim_resources("second", second)
    assert comparisons == [(first.owner_token, second.owner_token)]


@pytest.mark.parametrize("action", ["run", "control", "send", "start", "draft"])
def test_ui_allocates_distinct_bridge_identifiers(window, monkeypatch, action):  # noqa: F811
    shell, panel, backend = window
    captured = []
    monkeypatch.setattr(backend, "request_run", lambda *a, **kw: captured.append(kw["command_id"]))
    monkeypatch.setattr(
        backend, "request_control", lambda *a, **kw: captured.append(kw["command_id"])
    )
    monkeypatch.setattr(backend, "request_message", lambda *a, **kw: None)
    monkeypatch.setattr(backend, "request_new_session", lambda *a, **kw: None)
    shell.sessions.message_requested.connect(lambda *args: captured.append(args[3]))
    shell.sessions.new_session_requested.connect(lambda *args: captured.append(args[2]))
    if action not in ("start", "draft"):
        backend.task_changed.emit(snapshot(can_advance=True, can_accept_message=True))
    for _ in range(4):
        view = shell.sessions.current
        if action == "run":
            panel.run_button.setEnabled(True)
            panel._run()
        elif action == "control":
            panel._control(ControlAction.CANCEL)
        elif action == "send":
            view.send.setEnabled(True)
            shell.sessions._send(view)
        elif action == "start":
            view.start.setEnabled(True)
            shell.sessions._start(view)
        else:
            shell.sessions.new_session()
            captured.append(shell.sessions.current.key)
    assert len(captured) == 4
    assert_ids(captured, "draft:" if action == "draft" else "command-")
