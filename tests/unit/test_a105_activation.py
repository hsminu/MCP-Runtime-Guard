"""A105 A orchestration units: real B SQLite/C Registry/settings; I2/I6/I7/I8/I10, T10/T18/T29."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import Denied
from guard_app.domain.enums import RegStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ProposedCall
from guard_app.security.ids import new_id
from guard_app.security.paths import prepare_app_folders
from guard_app.services.bridge import BackendBridge, RegistrationRequest, RegistrationView
from guard_app.services.bridge_registration import RegistrationCommands
from guard_app.services.catalog import load_profile_settings, prepare_policy_change
from guard_app.services.product_assembly import assemble_guard
from guard_app.services.product_settings import (
    SaveOutcomeUnknownError,
    SettingsFileSaver,
    read_settings,
)
from guard_app.services.registry import ServerRegistry
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_policy import TaskPolicy
from guard_app.storage.sqlite_store import SQLiteStore
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.servers import ServerListPage

from .test_product_assembly import Peer, inputs


async def loop_barrier():
    barrier = asyncio.Event()
    asyncio.get_running_loop().call_soon(barrier.set)
    await asyncio.wait_for(barrier.wait(), 3)


async def assert_guard_closed(h):
    result = await h.assembled.guard.submit(
        "task",
        ProposedCall(tool_ref="docs__read", arguments={}, model_call_id="c"),
        expected_epoch=1,
    )
    assert isinstance(result, Denied)
    assert h.peer.calls == []


@asynccontextmanager
async def harness(path, *, selected=True):
    folders = prepare_app_folders(path / "app")
    store = SQLiteStore(folders.db_path(), audit_id_factory=lambda: new_id("audit"))
    registry = ServerRegistry(store)
    await registry.load()
    definitions = [
        {"name": name, "inputSchema": {"type": "object"}} for name in ("read", "second", "unknown")
    ]
    listing = SimpleNamespace(complete=True, tools=tuple(FrozenJsonObject(d) for d in definitions))
    doc = {
        "profile": {"id": "unit", "servers": ["docs"] if selected else []},
        "servers": [
            {
                "id": "docs",
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
                "tier": "internal",
                "max_label": "CONFIDENTIAL",
                "tools": [{"name": n, "kind": "read"} for n in ("read", "second")],
            }
        ],
        "access_rules": {"allow": [["docs", "read"], ["docs", "second"]], "deny": []},
    }
    server = load_profile_settings(doc, registry).servers[0]
    await registry.register(server, listing)
    saver = SettingsFileSaver(read_settings(folders))
    assert await saver(prepare_policy_change(doc, doc, registry))
    peer = Peer(definitions)
    assembled = await assemble_guard(store, inputs(peer, registry, doc))
    admission = SessionAdmission()
    stopped = []

    async def source():
        return (await store.load_task("task"),)

    async def stop(ids):
        stopped.extend(ids)

    async def collect(_server):
        return listing

    policy = TaskPolicy(
        assembled.settings,
        admission,
        assembled=assembled,
        document=doc,
        saver=saver,
        task_source=source,
    )
    await store.create_task(policy.task_state("task"), created_at_utc=datetime.now(UTC))
    commands = RegistrationCommands(
        registry, store, policy, collect, stop, operator_id="backend-user"
    )
    preview = await commands(RegistrationRequest("activation_preview", "docs"))
    request = RegistrationRequest(
        "activate",
        "docs",
        expected_row_revision=preview.row_revision,
        allowed=("read",),
        denied=("second",),
        token=new_id("screen"),
    )
    try:
        yield SimpleNamespace(**locals())
    finally:
        await assembled.aclose()
        store.close()
        assert await store.wait_closed(timeout=5)
        folders.close()


@pytest.mark.asyncio
async def test_activation_saves_applies_then_activates_and_restart_denies_unchecked(
    tmp_path, monkeypatch
):
    async with harness(tmp_path) as h:
        original = h.registry.activate
        observed = []

        async def activate(*args, **kwargs):
            saved = read_settings(h.folders).document
            assert saved["access_rules"]["deny"] == [["docs", "second"]]
            assert (await h.store.load_task("task")).status is TaskStatus.HALTED
            assert h.registry.status("docs") is RegStatus.PENDING
            observed.append(kwargs)
            return await original(*args, **kwargs)

        monkeypatch.setattr(h.registry, "activate", activate)
        assert (await h.commands(h.request)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert h.stopped == ["task"]
        assert observed[0]["reviewed_by"] == "backend-user"
        assert observed[0]["reviewed_scan_id"] is None
        entry = await h.store.load_registration("docs")
        assert entry.reviewed_by == "backend-user"
        assert entry.registration.status is RegStatus.ACTIVE
        assert h.assembled.settings.policy_version != h.preview.activation.policy_version
        with pytest.raises(SessionRuntimeError, match="REG_ACTIVATION_RESTART_REQUIRED"):
            h.admission.check()
        saved = read_settings(h.folders).document
        await h.assembled.aclose()
        h.store.close()
        assert await h.store.wait_closed(timeout=5)
        restarted_store = SQLiteStore(h.folders.db_path(), audit_id_factory=lambda: new_id("audit"))
        restarted_registry = ServerRegistry(restarted_store)
        await restarted_registry.load()
        restarted_peer = Peer(h.definitions)
        fresh = await assemble_guard(
            restarted_store, inputs(restarted_peer, restarted_registry, saved)
        )
        try:
            state = h.policy.task_state("new")
            await restarted_store.create_task(state, created_at_utc=datetime.now(UTC))
            result = await fresh.guard.submit(
                "new",
                ProposedCall(tool_ref="docs__second", arguments={}, model_call_id="c"),
                expected_epoch=1,
            )
            assert isinstance(result, Denied)
            assert len(result.reasons) == 1
            assert h.peer.calls == []
            assert restarted_peer.calls == []
        finally:
            await fresh.aclose()
            restarted_store.close()
            assert await restarted_store.wait_closed(timeout=5)


@pytest.mark.asyncio
async def test_activation_selects_profile_and_other_cannot_be_granted(tmp_path):
    async with harness(tmp_path, selected=False) as h:
        assert [t.kind_fixed for t in h.preview.activation.servers[0].tools] == [True, True, False]
        bad = replace(h.request, allowed=("read", "unknown"))
        assert (await h.commands(bad)).code == "POLICY_CHANGE_INVALID"
        assert h.registry.status("docs") is RegStatus.PENDING
        assert (await h.commands(h.request)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert read_settings(h.folders).document["profile"]["servers"] == ["docs"]


@pytest.mark.asyncio
async def test_stale_revision_and_unrecorded_never_activate(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        assert (
            await h.commands(replace(h.request, expected_row_revision=0))
        ).code == "REG_ACTIVATION_STALE"
        assert h.admission.restart_code is None

        async def refuse(*args, **kwargs):
            raise StoreError()

        monkeypatch.setattr(h.store, "record_fingerprint_change", refuse)
        with pytest.raises(StoreError):
            await h.registry.observe(h.server, h.listing)
        assert (await h.commands(h.request)).code == "REG_SERVER_UNRECORDED"
        assert h.registry.status("docs") is RegStatus.PENDING
        assert h.admission.restart_code == "REG_ACTIVATION_UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocking", [False, True])
@pytest.mark.parametrize("failure", ["false", "unknown", "cancel", "external", "move"])
async def test_settings_failure_orders_and_no_retry(tmp_path, monkeypatch, blocking, failure):
    async with harness(tmp_path) as h:
        calls = []
        if failure == "move":
            from guard_app.security import settings_io

            def refuse_move(*args):
                return 0

            monkeypatch.setattr(settings_io, "_MoveFileExW", refuse_move)
        if failure == "external":
            external = {**h.doc, "policy": {"approval_ttl_seconds": 120}}
            outside_saver = SettingsFileSaver(read_settings(h.folders))
            assert await outside_saver(prepare_policy_change(h.doc, external, h.registry))

        async def save(change):
            calls.append(1)
            assert h.registry.status("docs") is (
                RegStatus.BLOCKED if blocking else RegStatus.PENDING
            )
            if failure == "unknown":
                await h.saver(change)  # committed but ACK lost
                raise SaveOutcomeUnknownError()
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure in ("external", "move"):
                return await h.saver(change)
            return False

        h.policy._saver = save
        request = replace(h.request, action="block") if blocking else h.request
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await h.commands(request)
        else:
            assert (await h.commands(request)).code == (
                "POLICY_SAVE_UNKNOWN"
                if failure == "unknown"
                else "POLICY_SAVE_FILE_CHANGED"
                if failure == "external"
                else "POLICY_SAVE_FAILED"
            )
        if failure == "external":
            assert read_settings(h.folders).document == external
        assert (await h.commands(request)).code == "REG_ACTIVATION_UNKNOWN"
        assert calls == [1]
        await assert_guard_closed(h)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocking", [False, True])
@pytest.mark.parametrize("committed", [False, True])
async def test_registry_failure_orders_close_guard_and_never_retry(
    tmp_path, monkeypatch, blocking, committed
):
    async with harness(tmp_path) as h:
        name = "block_server" if blocking else "activate_server"
        original = getattr(h.store, name)
        calls = []

        async def fail(*args, **kwargs):
            calls.append(1)
            saved = read_settings(h.folders).document
            assert saved["access_rules"]["deny"] == ([] if blocking else [["docs", "second"]])
            if committed:
                await original(*args, **kwargs)
            raise StoreError()

        monkeypatch.setattr(h.store, name, fail)
        request = replace(h.request, action="block") if blocking else h.request
        assert (await h.commands(request)).code == "REG_STORE_UNKNOWN"
        assert h.admission.restart_code == "REG_ACTIVATION_UNKNOWN"
        assert (await h.commands(request)).code == "REG_ACTIVATION_UNKNOWN"
        assert calls == [1]
        assert h.registry.status("docs") is not RegStatus.ACTIVE
        assert h.peer.calls == []


@pytest.mark.asyncio
async def test_policy_edit_precedes_activation_and_overlapping_activation_is_refused(tmp_path):
    async with harness(tmp_path) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def save(change):
            writes.append(change.document.to_dict())
            if len(writes) == 1:
                entered.set()
                await asyncio.wait_for(release.wait(), 3)
            return await h.saver(change)

        h.policy._saver = save
        edit = asyncio.create_task(
            h.policy.change_server_access("docs", (), ("read", "second"), h.stop)
        )
        await asyncio.wait_for(entered.wait(), 3)
        activation = asyncio.create_task(h.commands(h.request))
        # A loop barrier lets activation reach the held policy lock; no timing sleep.
        await loop_barrier()
        assert len(writes) == 1
        release.set()
        await asyncio.wait_for(edit, 3)
        assert (await asyncio.wait_for(activation, 3)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert (await h.commands(h.request)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert len(writes) == 2
        assert read_settings(h.folders).document["access_rules"] == {
            "allow": [["docs", "read"]],
            "deny": [["docs", "second"]],
        }


def test_activation_ui_uses_bridge_revision_checkboxes_and_fixed_notices(monkeypatch):
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: None)
    requests = []
    monkeypatch.setattr(
        bridge, "request_registration", lambda request, **kw: requests.append((request, kw))
    )
    page = ServerListPage()
    page.attach_activation(bridge)
    page.set_servers(
        Surface(LoadState.READY, (ServerSummary("docs", "docs", registry_status="pending"),))
    )
    page.select_server("docs")
    page.activate_button.click()
    assert requests[-1][0].action == "activation_preview"
    from guard_app.services.bridge import PolicyServer, PolicyTool, PolicyView

    view = PolicyView(
        "unit",
        "v",
        300,
        "accumulate",
        (),
        (),
        (
            PolicyServer(
                "docs", (PolicyTool("read", "read", True), PolicyTool("unknown", "other", False))
            ),
        ),
        change_available=True,
    )
    response = RegistrationView(
        "REG_ACTIVATION_PREVIEW", requests[-1][1]["command_id"], activation=view, row_revision=7
    )
    page._activation_result(replace(response, command_id="stale"))
    assert page._activation_dialog is None
    page._activation_result(response)
    dialog = page._activation_dialog
    assert "검사 기능이 연결되지 않음" in dialog.warning.text()
    assert "Task가 모두 정지됨" in dialog.warning.text()
    # Re-activating identical settings can keep the content-hash version.
    assert "정책 버전" not in dialog.warning.text()
    assert dialog.boxes["read"].isChecked()
    assert not dialog.boxes["unknown"].isChecked()
    assert not dialog.boxes["unknown"].isEnabled()
    dialog.boxes["read"].setChecked(False)
    dialog.save_button.click()
    request = requests[-1][0]
    assert (request.action, request.expected_row_revision) == ("activate", 7)
    assert (request.allowed, request.denied) == ((), ("read",))
    page._activation_result(
        RegistrationView("REG_ACTIVATION_RESTART_REQUIRED", requests[-1][1]["command_id"])
    )
    assert "다시 시작하면 연결됨" in page.activation_notice.text()
    # A failed activation save latches restart and closes Guard; say so.
    page._activation_command = "failed-save"
    page._activation_result(RegistrationView("POLICY_SAVE_FAILED", "failed-save"))
    assert page.activation_notice.text() == (
        "설정을 저장하지 못해 Guard를 정지함. 앱을 다시 시작하세요."
    )
    page.close()
    app.processEvents()


@pytest.mark.asyncio
async def test_block_revokes_registry_before_settings_and_stops_task(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        await h.registry.activate(
            "docs",
            expected_row_revision=h.request.expected_row_revision,
            reviewed_by="backend-user",
        )
        entry = h.registry.entry("docs")
        seen = []

        async def save(change):
            seen.append(h.registry.status("docs"))
            return await h.saver(change)

        monkeypatch.setattr(h.policy, "_saver", save)
        request = replace(
            h.request, action="block", expected_row_revision=entry.registration.row_revision
        )
        assert (await h.commands(request)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert seen == [RegStatus.BLOCKED]
        assert (await h.store.load_task("task")).status is TaskStatus.HALTED
        saved = read_settings(h.folders).document
        assert saved["profile"]["servers"] == []
        assert saved["access_rules"] == {"allow": [], "deny": []}
        assert h.peer.calls == []


@pytest.mark.asyncio
async def test_first_registration_file_can_activate_without_assembled_guard(tmp_path):
    from .test_bridge_registration import HTTP, confirm
    from .test_bridge_registration import harness as first_run

    async with first_run(tmp_path) as h:
        assert read_settings(h.folders).document is None
        await confirm(h, await h.commands(HTTP))
        preview = await h.commands(RegistrationRequest("activation_preview", "remote"))
        assert not preview.activation.servers[0].tools[0].kind_fixed
        request = RegistrationRequest(
            "activate", "remote", expected_row_revision=preview.row_revision
        )
        assert (await h.commands(request)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        saved = read_settings(h.folders).document
        assert saved["profile"]["servers"] == ["remote"]
        assert saved["access_rules"] == {"allow": [], "deny": []}
        assert h.registry.status("remote") is RegStatus.ACTIVE
        assert h.admission.restart_code == "REG_ACTIVATION_RESTART_REQUIRED"


@pytest.mark.asyncio
async def test_identical_activation_stops_tasks_without_inventing_policy_version(tmp_path):
    async with harness(tmp_path) as h:
        before = h.assembled.settings.policy_version
        request = replace(h.request, allowed=("read", "second"), denied=())
        assert (await h.commands(request)).code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert h.assembled.settings.policy_version == before
        assert (await h.store.load_task("task")).status is TaskStatus.HALTED
        assert h.stopped == ["task"]


@pytest.mark.asyncio
async def test_overlapping_activations_share_policy_lock_and_write_only_once(tmp_path):
    async with harness(tmp_path) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def save(change):
            writes.append(1)
            entered.set()
            await asyncio.wait_for(release.wait(), 3)
            return await h.saver(change)

        h.policy._saver = save

        async def activate():
            await h.policy.set_server_active(
                "docs",
                h.request.expected_row_revision,
                ("read",),
                ("second",),
                h.registry,
                h.stop,
                active=True,
                operator_id="backend-user",
            )

        first = asyncio.create_task(activate())
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(activate())
        await loop_barrier()
        assert writes == [1]
        release.set()
        await asyncio.wait_for(first, 3)
        with pytest.raises(SessionRuntimeError, match="REG_ACTIVATION_RESTART_REQUIRED"):
            await asyncio.wait_for(second, 3)
        assert writes == [1]
