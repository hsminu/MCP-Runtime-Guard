"""A208: I2/I6/I8/I9, T10/T18/T29, S03/S04 deletion and failure boundaries.

Real SQLite/Registry/Guard with existing SQL observation fixtures. Transport
dispatch is observed through the A105 peer; no external wire claim is made.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import RegStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.services import session_policy
from guard_app.services.bridge import BackendBridge, RegistrationRequest, RegistrationView
from guard_app.services.catalog import CatalogConfigError, prepare_policy_change
from guard_app.services.product_settings import (
    SaveOutcomeUnknownError,
    SettingsFileChangedError,
    default_settings_document,
    read_settings,
)
from guard_app.services.registration_scans import RegistrationScans
from guard_app.services.registry import ServerRegistry
from guard_app.services.session_admission import SessionRuntimeError
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.servers import ServerListPage
from guard_app.ui.widgets import ConfirmDialog

from .test_a105_activation import assert_guard_closed, harness
from .test_registry_store import NOW, RAW, SELECT_ROWS, activate, add, report, rows, run, tool
from .test_registry_store import store as store
from .test_server_registry import listing, settings
from .test_sqlite_store import (
    test_initial_migration_creates_only_design_tables_and_tags as verify_schema,
)
from .test_store_queries import _seed, call_query


@pytest.mark.parametrize("status", [RegStatus.PENDING, RegStatus.ACTIVE, RegStatus.BLOCKED])
def test_store_requires_blocked_and_revision_cas(store, status):
    entry = add(store)
    if status is RegStatus.ACTIVE:
        entry = activate(store, entry)
    if status is RegStatus.BLOCKED:
        entry = run(store.block_server("server-1", expected_row_revision=1, at_utc=NOW))
    revision = entry.registration.row_revision
    before = rows(store)
    with pytest.raises(StoreError) as error:
        run(store.delete_server("server-1", expected_row_revision=revision + 1, at_utc=NOW))
    assert error.value.reason is Reason.APPROVAL_RACE
    assert rows(store) == before
    if status is not RegStatus.BLOCKED:
        with pytest.raises(StoreError) as error:
            run(store.delete_server("server-1", expected_row_revision=revision, at_utc=NOW))
        assert error.value.reason is Reason.REG_NOT_ACTIVE
        assert rows(store) == before
    else:
        run(store.delete_server("server-1", expected_row_revision=revision, at_utc=NOW))
        assert run(store.load_registration("server-1")) is None


def all_rows(store):
    return store._run_on_writer(
        lambda connection: {
            table: [tuple(row) for row in connection.execute(query)]
            for table, query in SELECT_ROWS.items()
        }
    )


def test_store_deletes_graph_atomically_preserving_calls_approvals_and_audit(store):
    store._run_on_writer(_seed)
    entry = add(store, server_id="docs", tools=(tool(server_id="docs"),))
    entry = run(store.record_scan_report("scan-old", report(server_id="docs"), at_utc=NOW))
    entry = run(store.record_scan_report("scan-new", report(server_id="docs"), at_utc=NOW))
    entry = run(
        store.activate_server(
            "docs",
            expected_row_revision=entry.registration.row_revision,
            tool_fingerprints={"read": tool().fingerprint},
            reviewed_by="operator",
            reviewed_scan_id="scan-new",
            at_utc=NOW,
        )
    )
    entry = run(
        store.block_server(
            "docs", expected_row_revision=entry.registration.row_revision, at_utc=NOW
        )
    )
    before = all_rows(store)
    calls = run(store.list_calls(call_query(server_id="docs")))
    assert calls.total_count == 2
    assert len(before["approvals"]) == 3
    run(
        store.delete_server(
            "docs", expected_row_revision=entry.registration.row_revision, at_utc=NOW
        )
    )
    after = all_rows(store)
    for table in ("registrations", "registration_tools", "scan_reports"):
        assert after[table] == []
    for table in ("tasks", "escalations", "requests", "approvals"):
        assert after[table] == before[table]
    remaining = run(store.list_calls(call_query(server_id="docs")))
    assert remaining.rows == calls.rows
    assert remaining.total_count == calls.total_count
    events = after["audit_events"]
    assert len(events) == len(before["audit_events"]) + 1
    assert all(row in events for row in before["audit_events"])
    deleted = [row for row in events if row[3] == "REGISTRY_DELETED"]
    assert len(deleted) == 1
    assert json.loads(deleted[0][6]) == {
        "server_id": "docs",
        "row_revision": entry.registration.row_revision + 1,
        "scan_id": None,
        "tool_count": 1,
        "changed_count": 0,
    }
    assert RAW not in deleted[0][6]
    assert "inputSchema" not in deleted[0][6]
    verify_schema(store)  # Includes foreign_key_check == [] and unchanged schema version.


def test_store_delete_rolls_back_all_rows_when_audit_fails(store):
    entry = add(store)
    entry = run(store.record_scan_report("scan", report(), at_utc=NOW))
    entry = run(
        store.block_server(
            "server-1", expected_row_revision=entry.registration.row_revision, at_utc=NOW
        )
    )
    before = rows(store)
    store._audit_id_factory = lambda: "audit-0"
    with pytest.raises(StoreError):
        run(
            store.delete_server(
                "server-1", expected_row_revision=entry.registration.row_revision, at_utc=NOW
            )
        )
    assert rows(store) == before
    verify_schema(store)


@pytest.mark.asyncio
async def test_registry_delete_clears_caches_and_allows_same_id_registration(store):
    registry = ServerRegistry(store)
    entry = await registry.register(settings(), listing())
    initial_revision = entry.registration.row_revision
    entry = await registry.block("docs", expected_row_revision=initial_revision)
    registry._unrecorded.add("docs")
    registry._stale.add("docs")
    await registry.delete("docs", expected_row_revision=entry.registration.row_revision)
    assert registry.entry("docs") is None
    assert registry.status("docs") is None
    assert "docs" not in registry._unrecorded
    assert "docs" not in registry._stale
    entry = await registry.register(settings(), listing())
    assert entry.registration.row_revision == initial_revision
    assert entry.registration.status is RegStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_registry_uncertain_delete_marks_stale_without_false_success(
    store, monkeypatch, committed
):
    registry = ServerRegistry(store)
    entry = await registry.register(settings(), listing())
    entry = await registry.block("docs", expected_row_revision=entry.registration.row_revision)
    original = store.delete_server

    async def fail(*args, **kwargs):
        if committed:
            await original(*args, **kwargs)
        raise StoreError()

    monkeypatch.setattr(store, "delete_server", fail)
    with pytest.raises(StoreError):
        await registry.delete("docs", expected_row_revision=entry.registration.row_revision)
    assert "docs" in registry._stale
    assert registry.entry("docs") == entry
    refreshed = await registry.refresh("docs")
    assert refreshed == (None if committed else entry)


async def prepare_baseline(h, document):
    change = prepare_policy_change(h.doc, document, h.registry)
    await h.policy._save_change(h.assembled, h.saver, h.source, change, h.stop)
    await h.store.create_task(h.policy.task_state("delete-task"), created_at_utc=NOW)

    async def source():
        return (await h.store.load_task("delete-task"),)

    h.policy._task_source = source
    h.stopped.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RegStatus.PENDING, RegStatus.ACTIVE, RegStatus.BLOCKED])
async def test_policy_delete_orders_removes_settings_stops_task_and_closes_once(
    tmp_path, monkeypatch, status
):
    async with harness(tmp_path) as h:
        entry = h.registry.entry("docs")
        if status is RegStatus.ACTIVE:
            entry = await h.registry.activate(
                "docs",
                expected_row_revision=entry.registration.row_revision,
                reviewed_by="operator",
            )
        elif status is RegStatus.BLOCKED:
            entry = await h.registry.block(
                "docs", expected_row_revision=entry.registration.row_revision
            )
        # Include both permission lists and a source so all four locations are checked.
        document = deepcopy(h.doc)
        document["access_rules"]["deny"] = [["docs", "second"]]
        document["profile"]["sources"] = [
            {"server": "docs", "tool": "read", "prefix": [], "label": "PUBLIC"}
        ]
        await prepare_baseline(h, document)
        order = []
        block, delete = h.registry.block, h.registry.delete

        async def record_block(*args, **kwargs):
            order.append("block")
            return await block(*args, **kwargs)

        async def save(change):
            order.append("save")
            assert h.registry.status("docs") is RegStatus.BLOCKED
            return await h.saver(change)

        async def record_delete(*args, **kwargs):
            order.append("delete")
            assert (await h.store.load_task("delete-task")).status is TaskStatus.HALTED
            return await delete(*args, **kwargs)

        close = AsyncMock(wraps=h.assembled.guard.aclose)
        monkeypatch.setattr(h.registry, "block", record_block)
        monkeypatch.setattr(h.registry, "delete", record_delete)
        monkeypatch.setattr(h.policy, "_saver", save)
        monkeypatch.setattr(h.assembled.guard, "aclose", close)
        result = await h.commands(
            RegistrationRequest(
                "delete", "docs", expected_row_revision=entry.registration.row_revision
            )
        )
        assert result.code == "REG_DELETED"
        assert result.server_id == "docs"
        assert result.rows == ()
        assert order == (
            ["save", "delete"] if status is RegStatus.BLOCKED else ["block", "save", "delete"]
        )
        assert h.admission.restart_code == "REG_DELETE_RESTART_REQUIRED"
        assert close.await_count == 1
        saved = read_settings(h.folders).document
        assert saved["servers"] == []
        assert saved["profile"]["servers"] == []
        assert saved["profile"]["sources"] == []
        assert saved["access_rules"] == {"allow": [], "deny": []}
        assert h.stopped == ["delete-task"]
        assert (await h.commands(RegistrationRequest("list"))).rows == ()
        await assert_guard_closed(h)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["false", "unknown", "external", "cancel"])
async def test_policy_failed_save_never_deletes_or_retries(tmp_path, monkeypatch, failure):
    async with harness(tmp_path) as h:

        async def save(change):
            if failure == "unknown":
                await h.saver(change)
                raise SaveOutcomeUnknownError()
            if failure == "external":
                raise SettingsFileChangedError()
            if failure == "cancel":
                raise asyncio.CancelledError()
            return False

        saver = AsyncMock(side_effect=save)
        delete = AsyncMock(wraps=h.registry.delete)
        close = AsyncMock(wraps=h.assembled.guard.aclose)
        monkeypatch.setattr(h.policy, "_saver", saver)
        monkeypatch.setattr(h.registry, "delete", delete)
        monkeypatch.setattr(h.assembled.guard, "aclose", close)
        request = replace(h.request, action="delete")
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await h.commands(request)
        else:
            assert (await h.commands(request)).code == "REG_DELETE_UNKNOWN"
        assert h.admission.restart_code == "REG_DELETE_UNKNOWN"
        assert h.registry.status("docs") is RegStatus.BLOCKED
        assert close.await_count == 1
        assert delete.await_count == 0
        assert (await h.commands(request)).code == "REG_DELETE_UNKNOWN"
        assert saver.await_count == 1
        await assert_guard_closed(h)


@pytest.mark.asyncio
@pytest.mark.parametrize("minimal", [False, True])
async def test_blocked_absent_settings_skips_save_and_emergency_halts(
    tmp_path, monkeypatch, minimal
):
    async with harness(tmp_path) as h:
        entry = await h.registry.block(
            "docs", expected_row_revision=h.request.expected_row_revision
        )
        document = default_settings_document()
        if minimal:
            document["access_rules"] = {"allow": []}
            document["profile"].pop("sources", None)
        await prepare_baseline(h, document)
        saver = AsyncMock(return_value=True)
        monkeypatch.setattr(h.policy, "_saver", saver)
        preview = await h.commands(RegistrationRequest("activation_preview", "docs"))
        assert preview.row_revision == entry.registration.row_revision
        assert preview.code == "REG_ACTIVATION_PREVIEW"
        result = await h.commands(
            RegistrationRequest("delete", "docs", expected_row_revision=preview.row_revision)
        )
        assert result.code == "REG_DELETED"
        assert saver.await_count == 0
        assert (await h.store.load_task("delete-task")).status is TaskStatus.HALTED
        assert h.stopped == ["delete-task"]


@pytest.mark.asyncio
async def test_preview_refreshes_revision_and_delete_does_not_touch_other_server(tmp_path):
    async with harness(tmp_path) as h:
        other = h.server.model_copy(update={"id": "other"})
        other_entry = await h.registry.register(other, h.listing)
        document = deepcopy(h.doc)
        other_record = deepcopy(document["servers"][0])
        other_record["id"] = "other"
        document["servers"].append(other_record)
        document["profile"]["servers"].append("other")
        document["profile"]["sources"] = [
            {"server": "other", "tool": "read", "prefix": [], "label": "PUBLIC"}
        ]
        document["access_rules"]["allow"].append(["other", "read"])
        document["access_rules"]["deny"].append(["other", "second"])
        await prepare_baseline(h, document)
        blocked = await h.store.block_server(
            "docs", expected_row_revision=h.request.expected_row_revision, at_utc=NOW
        )
        preview = await h.commands(RegistrationRequest("activation_preview", "docs"))
        assert preview.row_revision == blocked.registration.row_revision
        result = await h.commands(
            RegistrationRequest("delete", "docs", expected_row_revision=preview.row_revision)
        )
        assert result.code == "REG_DELETED"
        assert [row.server_id for row in result.rows] == ["other"]
        assert await h.store.load_registration("other") == other_entry
        saved = read_settings(h.folders).document
        assert saved["servers"] == [other_record]
        assert saved["profile"]["servers"] == ["other"]
        assert saved["profile"]["sources"] == document["profile"]["sources"]
        assert saved["access_rules"] == {
            "allow": [["other", "read"]],
            "deny": [["other", "second"]],
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [0, True, -1])
async def test_policy_stale_revision_does_not_write_or_close(tmp_path, monkeypatch, revision):
    async with harness(tmp_path) as h:
        saver, block, delete, close = (AsyncMock() for _ in range(4))
        monkeypatch.setattr(h.policy, "_saver", saver)
        monkeypatch.setattr(h.registry, "block", block)
        monkeypatch.setattr(h.registry, "delete", delete)
        monkeypatch.setattr(h.assembled.guard, "aclose", close)
        with pytest.raises(SessionRuntimeError, match="REG_ACTIVATION_STALE"):
            await h.policy.delete_server(
                "docs", revision, h.registry, h.stop, operator_id="operator"
            )
        assert [mock.await_count for mock in (saver, block, delete, close)] == [0, 0, 0, 0]
        assert h.admission.restart_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_policy_delete_failure_closes_and_never_retries(tmp_path, monkeypatch, committed):
    async with harness(tmp_path) as h:
        original = h.store.delete_server

        async def fail(*args, **kwargs):
            if committed:
                await original(*args, **kwargs)
            raise StoreError()

        delete = AsyncMock(side_effect=fail)
        monkeypatch.setattr(h.store, "delete_server", delete)
        request = replace(h.request, action="delete")
        assert (await h.commands(request)).code == "REG_DELETE_UNKNOWN"
        assert h.admission.restart_code == "REG_DELETE_UNKNOWN"
        assert (await h.commands(request)).code == "REG_DELETE_UNKNOWN"
        assert delete.await_count == 1
        await assert_guard_closed(h)


@pytest.mark.asyncio
async def test_bridge_cancels_scan_clears_caches_and_pending_confirmation(tmp_path):
    async with harness(tmp_path) as h:
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def scan(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        service = AsyncMock()
        service.scan.side_effect = scan
        scans = RegistrationScans(h.registry, service, h.collect)
        h.commands.scans = scans
        scans.start(h.server, h.listing)
        await asyncio.wait_for(entered.wait(), 3)
        h.commands._pending = ("confirmation", h.server, "register", None)
        result = await h.commands(replace(h.request, action="delete"))
        assert result.code == "REG_DELETED"
        assert cancelled.is_set()
        assert scans.state("docs").run == "none"
        assert scans.server_descriptions("docs") is None
        assert "docs" not in scans._tasks
        assert "docs" not in scans._runs
        assert h.commands._pending is None


def test_bridge_accepts_delete_action(monkeypatch):
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: None)
    commands = []
    monkeypatch.setattr(bridge._host.commands, "submit", lambda command: commands.append(command))
    bridge.request_registration(RegistrationRequest("delete", "docs", expected_row_revision=7))
    assert len(commands) == 1
    assert commands[0].registration.action == "delete"
    app.processEvents()


@pytest.mark.parametrize("status", ["pending", "active", "blocked"])
def test_delete_ui_confirmation_safe_focus_cancel_and_exactly_one_request(monkeypatch, status):
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: None)
    requests = []
    monkeypatch.setattr(
        bridge, "request_registration", lambda request, **kw: requests.append((request, kw))
    )
    page = ServerListPage()
    page.set_servers(
        Surface(
            LoadState.READY,
            (ServerSummary("docs", "docs", registry_status=status, in_settings=False),),
        )
    )
    assert not page.delete_button.isEnabled()
    page.attach_activation(bridge)
    page.select_server("docs")
    assert page.delete_button.isEnabled()
    assert page.delete_button.property("variant") == "danger"
    from guard_app.services.bridge import PolicyServer, PolicyView

    policy = PolicyView("unit", "v", 300, "accumulate", (), (), (PolicyServer("docs", ()),))

    def preview():
        page.delete_button.click()
        assert requests[-1][0].action == "activation_preview"
        assert not page.delete_button.isEnabled()
        page._activation_result(
            RegistrationView(
                "REG_ACTIVATION_PREVIEW",
                requests[-1][1]["command_id"],
                activation=policy,
                row_revision=7,
            )
        )
        app.processEvents()
        return page._activation_dialog

    dialog = preview()
    assert isinstance(dialog, ConfirmDialog)
    assert dialog.windowTitle() == "서버 삭제 확인"
    assert dialog.body.text() == (
        "docs 서버의 등록, 도구 정의, 검사 결과, 설정 파일 기록을 지움. "
        "호출·승인·감사 기록은 남음. 실행 중인 Task는 정지되고, "
        "삭제 뒤 앱을 다시 시작해야 함. 같은 ID로 다시 등록할 수 있음."
    )
    assert dialog.confirm.text() == "삭제 확인"
    assert dialog.confirm.property("variant") == "danger"
    assert dialog.focusWidget() is dialog.cancel
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    QTest.keyClick(dialog, Qt.Key.Key_Enter)
    assert len(requests) == 1
    dialog.cancel.click()
    assert not any(request.action == "delete" for request, _ in requests)
    dialog = preview()
    dialog.confirm.click()
    dialog.confirm.click()
    deletes = [request for request, _ in requests if request.action == "delete"]
    assert len(deletes) == 1
    assert deletes[0].server_id == "docs"
    assert deletes[0].expected_row_revision == 7
    page.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("REG_DELETED", "서버를 삭제함. 앱을 다시 시작하세요."),
        (
            "REG_DELETE_UNKNOWN",
            "삭제가 끝났는지 확인하지 못함. 앱을 다시 시작한 뒤 서버 목록을 확인하세요.",
        ),
        (
            "POLICY_SAVE_UNKNOWN",
            "삭제가 끝났는지 확인하지 못함. 앱을 다시 시작한 뒤 서버 목록을 확인하세요.",
        ),
        ("REG_ACTIVATION_STALE", "서버 상태가 바뀌어 저장하지 않음. 목록을 다시 확인하세요."),
    ],
)
def test_delete_ui_fixed_result_notices(code, message):
    app = QApplication.instance() or QApplication([])
    page = ServerListPage()
    page._activation_action = "delete"
    page._activation_command = "delete-command"
    page._activation_result(RegistrationView(code, "delete-command"))
    assert page.activation_notice.text() == message
    page.close()
    app.processEvents()


@pytest.mark.asyncio
async def test_invalid_removal_refuses_before_block_and_keeps_guard_open(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:

        def invalid(*_args, **_kwargs):
            raise CatalogConfigError("invalid")

        block = AsyncMock(wraps=h.registry.block)
        close = AsyncMock(wraps=h.assembled.guard.aclose)
        monkeypatch.setattr(session_policy, "prepare_policy_change", invalid)
        monkeypatch.setattr(h.registry, "block", block)
        monkeypatch.setattr(h.assembled.guard, "aclose", close)
        result = await h.commands(replace(h.request, action="delete"))
        assert result.code == "POLICY_CHANGE_INVALID"
        assert block.await_count == 0
        assert h.registry.status("docs") is RegStatus.PENDING
        assert h.admission.restart_code is None
        assert close.await_count == 0


@pytest.mark.asyncio
async def test_refusal_before_any_write_keeps_its_own_code(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        h.admission.require_restart("REG_ACTIVATION_RESTART_REQUIRED")
        block = AsyncMock(wraps=h.registry.block)
        monkeypatch.setattr(h.registry, "block", block)
        result = await h.commands(replace(h.request, action="delete"))
        assert result.code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert block.await_count == 0
        assert h.registry.status("docs") is RegStatus.PENDING
        assert h.admission.restart_code == "REG_ACTIVATION_RESTART_REQUIRED"


def test_delete_button_needs_a_registry_row():
    app = QApplication.instance() or QApplication([])
    page = ServerListPage()
    page.set_servers(
        Surface(
            LoadState.READY,
            (ServerSummary("docs", "docs", registry_status=None, in_settings=True),),
        )
    )
    page.attach_activation(BackendBridge(lambda: None))
    page.select_server("docs")
    assert not page.delete_button.isEnabled()
    page.close()
    app.processEvents()
