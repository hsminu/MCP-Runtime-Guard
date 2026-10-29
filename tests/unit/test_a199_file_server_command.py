"""A199/#94: file-server identity before reservation (I2/I6/I7).

A composition unit checks with injected streams, not C's independent acceptance.
"""

import sys
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from guard_app.domain.contracts import Denied, Released
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.digest import tool_fingerprint
from guard_app.security.paths import file_resource_resolver
from guard_app.servers.file_server import file_server_args
from guard_app.services.product_assembly import assemble_guard
from guard_app.storage.sqlite_store import SQLiteStore

from .test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs, spec


async def check_command(tmp_path, monkeypatch, command, *, accepted):
    root = tmp_path / "files"
    (root / "public").mkdir(parents=True)
    (root / "public" / "a.txt").write_text("public text", encoding="utf-8")
    doc = document()
    doc["servers"][0].update(command=command, args=list(file_server_args(root)))
    peer = Peer([READ_DEFINITION])
    registry = Registry(tool_fingerprint(READ_DEFINITION))
    audits = []
    # Remove the helper's permissive resolver: unknown roots must fail closed.
    configured = replace(
        inputs(peer, registry, doc, audits), resource_resolver=file_resource_resolver({})
    )
    with SQLiteStore(
        tmp_path / "assembly.db", audit_id_factory=lambda: f"audit-{uuid4().hex}"
    ) as store:
        reserve = AsyncMock(wraps=store.reserve_dispatch)
        monkeypatch.setattr(store, "reserve_dispatch", reserve)
        assembled = await assemble_guard(store, configured)
        try:
            await store.create_task(
                TaskState(
                    task_id="task-1",
                    epoch=1,
                    profile_id=assembled.settings.profile_id,
                    policy_version=assembled.settings.policy_version,
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
            outcome = await assembled.guard.submit(
                "task-1",
                ProposedCall(
                    tool_ref="docs__read",
                    arguments={"path": "public/a.txt"},
                    model_call_id="c1",
                ),
                expected_epoch=1,
            )
            if accepted:
                assert type(outcome) is Released
                assert outcome.label is Label.PUBLIC
                assert assembled.resource_resolver is not None
                assert (
                    assembled.resource_resolver(spec(READ_DEFINITION), "public/a.txt")
                    == "public/a.txt"
                )
                reserve.assert_awaited_once()
                assert len(peer.calls) == 1
                assert audits == []
            else:
                assert type(outcome) is Denied
                assert outcome.reasons == (Reason.INTERNAL_ERROR,)
                reserve.assert_not_called()
                assert len(peer.calls) == 0
                assert [(event.phase, event.reason) for event in audits] == [
                    ("before_dispatch", Reason.INTERNAL_ERROR)
                ]
            assert (await store.load_task("task-1")).status is TaskStatus.RUNNING
        finally:
            await assembled.aclose()


@pytest.mark.asyncio
async def test_other_executable_with_file_server_args_is_denied_before_reservation(
    tmp_path, monkeypatch
):
    await check_command(tmp_path, monkeypatch, r"C:\Windows\System32\where.exe", accepted=False)


@pytest.mark.asyncio
async def test_app_python_with_file_server_args_resolves_resource(tmp_path, monkeypatch):
    await check_command(tmp_path, monkeypatch, sys.executable, accepted=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path case normalization")
@pytest.mark.asyncio
async def test_app_python_command_is_case_insensitive_on_windows(tmp_path, monkeypatch):
    await check_command(tmp_path, monkeypatch, sys.executable.upper(), accepted=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path separator normalization")
@pytest.mark.asyncio
async def test_app_python_command_accepts_forward_slashes_on_windows(tmp_path, monkeypatch):
    await check_command(tmp_path, monkeypatch, sys.executable.replace("\\", "/"), accepted=True)
