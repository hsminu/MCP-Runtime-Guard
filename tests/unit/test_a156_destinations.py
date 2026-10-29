"""A156 orchestration: I1/I2/I6/I7, T01–T06/T18/T29; synthetic MCP receipts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from guard_app.domain.contracts import Denied, Pending, Released
from guard_app.domain.enums import Label, TaskStatus, ToolKind
from guard_app.domain.models import ProposedCall
from guard_app.domain.reasons import Reason
from guard_app.security.digest import tool_fingerprint
from guard_app.security.ids import new_id
from guard_app.security.paths import prepare_app_folders
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    DestinationEdit,
    SessionOptions,
)
from guard_app.services.catalog import prepare_policy_change
from guard_app.services.guard import PolicyChangeError
from guard_app.services.product_assembly import assemble_guard
from guard_app.services.product_settings import (
    SaveOutcomeUnknownError,
    SettingsFileSaver,
    read_settings,
)
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_destinations import destination_document
from guard_app.services.session_policy import TaskPolicy
from guard_app.storage.sqlite_store import SQLiteStore
from guard_app.ui.policy import CHANGE_RESULTS, STOP_WARNING, PolicyPage

from .test_b28_integration import policy_product
from .test_bridge import FakeControl, attach, close_bridge, pump_until
from .test_bridge_policy_access import result
from .test_product_assembly import (
    READ_DEFINITION,
    READ_SETTINGS,
    SEND_DEFINITION,
    Peer,
    Registry,
    document,
    inputs,
    spec,
)
from .test_ui_policy import view

ADD = DestinationEdit("add", "vault", "internal", "CONFIDENTIAL", True)


@pytest.mark.parametrize("tier", ["approved_external", "general_external"])
def test_document_builder_rejects_external_confidential(tier):
    with pytest.raises(SessionRuntimeError, match="POLICY_DESTINATION_INVALID"):
        destination_document({"profile": {}}, replace(ADD, tier=tier))


@asynccontextmanager
async def harness(path):
    folders = prepare_app_folders(path / "app")
    store = SQLiteStore(folders.db_path(), audit_id_factory=lambda: new_id("audit"))
    registry = Registry(tool_fingerprint(READ_DEFINITION), spec(SEND_DEFINITION, ToolKind.SEND))
    doc = document(tools=(READ_SETTINGS, {"name": "send", "kind": "send", "destination_arg": "to"}))
    peer = Peer([READ_DEFINITION, SEND_DEFINITION])
    saver = SettingsFileSaver(read_settings(folders))
    assert await saver(prepare_policy_change(doc, doc, registry))
    assembled = await assemble_guard(store, inputs(peer, registry, doc))
    admission = SessionAdmission()
    policy = TaskPolicy(
        assembled.settings,
        admission,
        assembled=assembled,
        document=doc,
        saver=saver,
        task_source=store.list_tasks,
    )
    stopped, saves, dispatch = [], [], []
    original_call = assembled.transport.call

    async def call(req):
        dispatch.append(req.request_id)
        return await original_call(req)

    assembled.transport.call = call

    async def save(change):
        saves.append(change)
        return await saver(change)

    policy._saver = save

    async def stop(ids):
        stopped.extend(ids)

    async def change(edit):
        return await policy.destinations(edit, stop)

    async def send(label=Label.PUBLIC, destination="vault"):
        task_id = new_id("task")
        state = policy.task_state(task_id).model_copy(update={"high_watermark": label})
        await store.create_task(state, created_at_utc=datetime.now(UTC))
        return await assembled.guard.submit(
            task_id,
            ProposedCall(
                tool_ref="docs__send",
                arguments={"to": destination, "body": "synthetic"},
                model_call_id="call",
            ),
            expected_epoch=1,
        )

    await store.create_task(policy.task_state("running"), created_at_utc=datetime.now(UTC))
    try:
        async with asyncio.timeout(15):
            yield SimpleNamespace(**locals())
    finally:
        await assembled.aclose()
        store.close()
        assert await store.wait_closed(timeout=5)
        folders.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edit",
    [
        replace(ADD, destination_id=value)
        for value in (
            "",
            " ",
            " vault",
            "vault ",
            "a\nb",
            "a\x00b",
            "a\x7fb",
            "a\u202eb",
            "a\u2028b",
            "a" * 257,
        )
    ]
    + [
        replace(ADD, tier="bogus"),
        replace(ADD, max_label="bogus"),
        replace(ADD, max_label=2),
        replace(ADD, selected=1),
        replace(ADD, tier="approved_external"),
        replace(ADD, tier="general_external"),
        replace(ADD, action="update"),
        replace(ADD, action="delete"),
        replace(ADD, action="bogus"),
    ],
)
async def test_invalid_edit_never_writes_or_applies(tmp_path, edit):
    async with harness(tmp_path) as h:
        before = read_settings(h.folders).document
        version = h.assembled.settings.policy_version
        with pytest.raises(SessionRuntimeError, match="POLICY_DESTINATION_INVALID"):
            await h.change(edit)
        assert read_settings(h.folders).document == before
        assert h.assembled.settings.policy_version == version
        assert (await h.store.load_task("running")).status is TaskStatus.RUNNING
        assert h.saves == h.stopped == h.dispatch == h.peer.calls == []


@pytest.mark.asyncio
async def test_add_update_selection_delete_and_noop(tmp_path):
    async with harness(tmp_path) as h:
        assert Reason.UNKNOWN_DESTINATION in (await h.send()).reasons
        changed = await h.change(ADD)
        assert changed.destinations[0].selected
        assert changed.destinations[0].destination_id == "vault"
        assert (await h.store.load_task("running")).status is TaskStatus.HALTED
        assert "running" in h.stopped
        assert h.admission.restart_code is None
        assert isinstance(await h.send(Label.CONFIDENTIAL), Released)
        assert len(h.dispatch) == len(h.peer.calls) == 1
        with pytest.raises(SessionRuntimeError, match="POLICY_DESTINATION_INVALID"):
            await h.change(ADD)
        version, stopped = changed.policy_version, list(h.stopped)
        same = await h.change(replace(ADD, action="update"))
        assert same.policy_version == version
        assert len(h.saves) == 1
        assert h.stopped == stopped
        assert Reason.UNKNOWN_DESTINATION in (await h.send(destination="Vault")).reasons
        unused = await h.change(replace(ADD, action="update", selected=False))
        assert not unused.destinations[0].selected
        saved = read_settings(h.folders).document
        assert (saved["profile"]["destinations"], saved["destinations"][0]["id"]) == ([], "vault")
        assert Reason.UNKNOWN_DESTINATION in (await h.send()).reasons
        await h.change(replace(ADD, action="update"))
        assert isinstance(await h.send(), Released)
        removed = await h.change(replace(ADD, action="delete"))
        saved = read_settings(h.folders).document
        assert saved["profile"]["destinations"] == saved["destinations"] == []
        assert removed.destinations == ()
        assert Reason.UNKNOWN_DESTINATION in (await h.send()).reasons
        assert len(h.dispatch) == len(h.peer.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "label", "expected"),
    [
        ("internal", Label.CONFIDENTIAL, Released),
        ("approved_external", Label.PUBLIC, Released),
        ("approved_external", Label.INTERNAL, Pending),
        ("general_external", Label.INTERNAL, Pending),
        ("general_external", Label.CONFIDENTIAL, Denied),
    ],
)
async def test_live_catalog_matrix_and_dispatch_counts(tmp_path, tier, label, expected):
    async with harness(tmp_path) as h:
        await h.change(
            replace(ADD, tier=tier, max_label="CONFIDENTIAL" if tier == "internal" else "INTERNAL")
        )
        outcome = await h.send(label)
        assert isinstance(outcome, expected)
        assert len(h.dispatch) == len(h.peer.calls) == (1 if expected is Released else 0)
        if isinstance(outcome, Pending):
            approved = await h.assembled.guard.human_control(h.assembled.operator_id).approve(
                outcome.approval_id
            )
            assert isinstance(approved, Released)
            assert len(h.dispatch) == len(h.peer.calls) == 1
        await h.change(replace(ADD, action="update", tier="general_external", max_label="PUBLIC"))
        denied = await h.send(Label.INTERNAL)
        assert isinstance(denied, Denied)
        assert Reason.LABEL_EXCEEDS_BOUNDARY in denied.reasons


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "code", "restart"),
    [
        ("false", "POLICY_SAVE_FAILED", False),
        ("unknown", "POLICY_SAVE_UNKNOWN", True),
        ("external", "POLICY_SAVE_FILE_CHANGED", False),
        ("apply", "POLICY_APPLY_FAILED", True),
        ("cancel", "POLICY_SAVE_UNKNOWN", True),
    ],
)
async def test_failure_semantics_and_applied_view(tmp_path, monkeypatch, failure, code, restart):
    async with harness(tmp_path) as h:
        before = read_settings(h.folders).document
        version = h.assembled.settings.policy_version
        closes = []
        original_close = h.assembled.guard.aclose

        async def close():
            closes.append(1)
            await original_close()

        monkeypatch.setattr(h.assembled.guard, "aclose", close)
        if failure == "external":
            external = {**h.doc, "policy": {"approval_ttl_seconds": 120}}
            assert await SettingsFileSaver(read_settings(h.folders))(
                prepare_policy_change(h.doc, external, h.registry)
            )
        if failure == "apply":

            async def refuse(*args, **kwargs):
                raise PolicyChangeError("private")

            monkeypatch.setattr(h.assembled.guard, "apply_policy_change", refuse)

        async def save(change):
            h.saves.append(change)
            if failure == "false":
                return False
            if failure == "cancel":
                raise asyncio.CancelledError()
            saved = await h.saver(change)
            if failure == "unknown":
                raise SaveOutcomeUnknownError()
            return saved

        h.policy._saver = save
        with pytest.raises(
            asyncio.CancelledError if failure == "cancel" else SessionRuntimeError
        ) as error:
            await h.change(ADD)
        if failure != "cancel":
            assert error.value.code == code
        assert h.admission.restart_code == (code if restart else None)
        assert bool(closes) == restart
        assert h.assembled.settings.policy_version == version
        assert (await h.change(None)).destinations == ()
        if failure in ("false", "cancel"):
            assert read_settings(h.folders).document == before
        if failure == "external":
            assert read_settings(h.folders).document == external
        if failure in ("unknown", "apply"):
            assert read_settings(h.folders).document["destinations"][0]["id"] == "vault"
        if restart:
            with pytest.raises(SessionRuntimeError, match=code):
                await h.change(ADD)
            assert len(h.saves) == 1
        assert h.stopped == h.dispatch == h.peer.calls == []


@pytest.mark.asyncio
async def test_lock_reads_latest_document_and_restart_refusal(tmp_path):
    async with harness(tmp_path) as h:
        entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def save(change):
            h.saves.append(change)
            if len(h.saves) == 1:
                entered.set()
                await asyncio.wait_for(release.wait(), 2)
            return await h.saver(change)

        h.policy._saver = save
        first = asyncio.create_task(h.change(ADD))
        await asyncio.wait_for(entered.wait(), 2)

        async def second():
            second_started.set()
            return await h.change(replace(ADD, destination_id="second"))

        other = asyncio.create_task(second())
        try:
            await asyncio.wait_for(second_started.wait(), 2)
            assert len(h.saves) == 1
            assert not other.done()
        finally:
            release.set()
            completed = await asyncio.wait_for(
                asyncio.gather(first, other, return_exceptions=True), 3
            )
        assert all(not isinstance(item, BaseException) for item in completed)
        assert [d.destination_id for d in (await h.change(None)).destinations] == [
            "vault",
            "second",
        ]
        h.admission.require_restart("REG_TOOLS_RESTART_REQUIRED")
        with pytest.raises(SessionRuntimeError, match="REG_TOOLS_RESTART_REQUIRED"):
            await h.change(replace(ADD, destination_id="third"))
        assert len(h.saves) == 2


@pytest.mark.asyncio
async def test_exact_id_limit_unselected_add_and_unavailable(tmp_path):
    async with harness(tmp_path) as h:
        identifier = "가" * 256
        edit = replace(ADD, destination_id=identifier, selected=False)
        added = await h.change(edit)
        assert added.destinations[0].destination_id == identifier
        assert not added.destinations[0].selected
        assert Reason.UNKNOWN_DESTINATION in (await h.send(destination=identifier)).reasons
        await h.change(replace(edit, action="update", selected=True))
        assert isinstance(await h.send(destination=identifier), Released)
        assert len(h.dispatch) == len(h.peer.calls) == 1
        h.policy._saver = None
        assert not (await h.change(None)).change_available
        with pytest.raises(SessionRuntimeError, match="POLICY_CHANGE_UNAVAILABLE"):
            await h.change(ADD)
        assert len(h.saves) == 2


@pytest.mark.asyncio
async def test_b_preparation_revalidates_document(tmp_path, monkeypatch):
    from guard_app.services import session_policy

    async with harness(tmp_path) as h:
        bad = destination_document(h.doc, ADD)
        bad["destinations"][0]["tier"] = "general_external"
        monkeypatch.setattr(session_policy, "destination_document", lambda *args: bad)
        with pytest.raises(SessionRuntimeError, match="POLICY_DESTINATION_INVALID"):
            await h.change(ADD)
        assert read_settings(h.folders).document == h.doc
        assert h.saves == h.dispatch == h.peer.calls == []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.asyncio
async def test_runtime_binding_stops_session_and_allows_new_session(tmp_path):
    servers, saved = [], []
    bindings, peer = await policy_product(tmp_path, servers, saved)
    try:
        old = await bindings.create_session(SessionOptions())
        previous = bindings.policy.policy_version
        changed = await bindings.policy_destinations(ADD)
        assert changed.policy_version != previous
        after = await bindings.snapshot(old.task_id)
        assert after.status is TaskStatus.HALTED
        assert after.connection_code == "POLICY_CHANGED"
        assert not after.model_ready
        assert (await bindings.policy_destinations(None)).destinations == changed.destinations
        # Changing access later keeps the same registered destination rows.
        access = await bindings.policy_access("docs", (), ("read",))
        assert access.destinations == changed.destinations
        new = await bindings.create_session(SessionOptions())
        assert new.status is TaskStatus.RUNNING
        assert new.task_id != old.task_id
        assert peer.calls == []
    finally:
        await bindings.close()


def test_ui_plain_id_choices_commands_and_fixed_results(app):
    from guard_app.services.bridge import PolicyDestination

    row = PolicyDestination("<b>vault</b>", "internal", "CONFIDENTIAL", True)
    page = PolicyPage()
    page.set_view(replace(view(change=True), destinations=(row,)))
    label = page.destinations.cellWidget(0, 0)
    assert (label.textFormat(), label.text()) == (Qt.TextFormat.PlainText, row.destination_id)
    assert page.destinations.item(0, 1).text() == "내부"
    assert page.destinations.item(0, 3).text() == "사용"
    edits = []
    page.destination_change_requested.connect(edits.append)
    dialog = page.open_destination_dialog(row)
    assert dialog.label.currentData() == "CONFIDENTIAL"
    dialog.tier.setCurrentIndex(dialog.tier.findData("approved_external"))
    assert dialog.label.findData("CONFIDENTIAL") == -1
    assert not dialog.save_button.isEnabled()
    dialog.label.setCurrentIndex(dialog.label.findData("INTERNAL"))
    dialog.selected.setChecked(False)
    dialog.save_button.click()
    assert edits == [
        DestinationEdit("update", row.destination_id, "approved_external", "INTERNAL", False)
    ]
    deleting = page.open_destination_dialog(row, deleting=True)
    assert any(w.text() == STOP_WARNING for w in deleting.findChildren(type(label)))
    deleting.save_button.click()
    assert edits[-1].action == "delete"
    adding = page.open_destination_dialog()
    adding.identifier.setText("new")
    adding.tier.setCurrentIndex(adding.tier.findData("internal"))
    adding.label.setCurrentIndex(adding.label.findData("PUBLIC"))
    adding.save_button.click()
    assert edits[-1] == DestinationEdit("add", "new", "internal", "PUBLIC", False)
    page.set_change_pending("mine")
    page.settle_change("mine", "POLICY_DESTINATION_INVALID")
    assert page.notice.text() == CHANGE_RESULTS["POLICY_DESTINATION_INVALID"]
    page.close()


def test_bridge_destination_read_change_and_refusal(app):
    calls = []

    async def change(edit):
        calls.append(edit)
        if edit is not None and edit.action == "delete":
            raise SessionRuntimeError("POLICY_DESTINATION_INVALID")
        return view(change=True)

    async def bootstrap():
        return BackendBindings(lambda task_id: None, FakeControl(), policy_destinations=change)

    bridge = BackendBridge(bootstrap)
    receiver = attach(bridge)
    views = []
    bridge.policy_changed.connect(views.append)
    bridge.start()
    pump_until(app, lambda: bridge.state is BridgeState.READY)
    try:
        for edit, code in (
            (None, "POLICY_READ"),
            (ADD, "POLICY_CHANGED"),
            (replace(ADD, action="delete"), "POLICY_DESTINATION_INVALID"),
        ):
            command = bridge.request_policy_destinations(edit)
            pump_until(app, lambda command=command: result(receiver, command) is not None)
            assert result(receiver, command) == code
        assert calls == [None, ADD, replace(ADD, action="delete")]
        assert len([v for v in views if v is not None]) == 2
    finally:
        close_bridge(app, bridge)
