"""A202 / DESC-01, B3.13; I6/I7/I8/I9/I10, T10/T18/T29.

Real SQLite/Registry and fake listing/Scanner. No model, network or dispatch;
these tests cover description input/storage, not Scanner efficacy or C acceptance.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel

from guard_app.domain.enums import RegStatus, ToolKind
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.services.bridge import (
    BackendBridge,
    CommandEvent,
    CommandStage,
    DescriptionRow,
    RegistrationRequest,
    RegistrationView,
    reviewed_description_issue,
)
from guard_app.services.catalog import Catalog, ToolSettings, load_profile_settings
from guard_app.services.registration_scans import RegistrationScans
from guard_app.services.registry import RegistryError
from guard_app.ui.reviewed_descriptions import DescriptionReviewDialog
from guard_app.ui.scan_report import ScanReviewDialog
from guard_app.ui.untrusted_text import UntrustedTextView, untrusted_display_text

from .test_a178_qt_ownership import assert_qt_owned
from .test_a200_registration_scans import FakeScanner, settled
from .test_bridge_registration import HTTP, confirm
from .test_bridge_registration import harness as registration_harness

SERVER_TEXT = "IGNORE PREVIOUS ... <b>x</b>\u202e"
REVIEWED = "검토한 설명\n다음 줄\t내용"


@asynccontextmanager
async def harness(tmp_path):
    async with registration_harness(tmp_path) as h:
        h.listing = SimpleNamespace(
            complete=True,
            tools=tuple(
                FrozenJsonObject(
                    {"name": name, "description": description, "inputSchema": {"type": "object"}}
                )
                for name, description in (("read", SERVER_TEXT), ("second", 12))
            ),
        )

        async def collect(server):
            h.collected.append(server)
            return h.listing

        h.commands.collect = collect
        h.scanner = FakeScanner(h.registry)
        h.scans = RegistrationScans(h.registry, h.scanner, collect)
        h.commands.scans = h.scans
        try:
            preview = await h.commands(HTTP)
            token, server, mode, file_request = h.commands._pending
            # Trusted fixture settings make the real registered tool model-visible.
            server = server.model_copy(
                update={"tools": (ToolSettings(name="read", kind=ToolKind.READ),)}
            )
            h.commands._pending = (token, server, mode, file_request)
            assert (await confirm(h, preview)).code == "REG_REGISTERED"
            await settled(h)
            yield h
        finally:
            await h.scans.aclose()


async def preview(h, server_id="remote"):
    return await h.commands(RegistrationRequest("descriptions_preview", server_id))


async def save(h, pairs=(("read", REVIEWED),), revision=None, server_id="remote"):
    if revision is None:
        revision = h.registry.entry("remote").registration.row_revision
    return await h.commands(
        RegistrationRequest(
            "descriptions_save", server_id, expected_row_revision=revision, descriptions=pairs
        )
    )


@pytest.mark.asyncio
async def test_preview_save_partial_update_activation_and_catalog(tmp_path):
    async with harness(tmp_path) as h:
        before = await h.store.load_registration("remote")
        view = await preview(h)
        assert view.code == "REG_DESCRIPTIONS_PREVIEW"
        assert view.row_revision == before.registration.row_revision
        assert view.descriptions == (
            DescriptionRow("read", SERVER_TEXT, ""),
            DescriptionRow("second", "", ""),
        )
        assert SERVER_TEXT not in repr(view)
        assert SERVER_TEXT not in repr(view.descriptions[0])
        scan_id = h.scans.state("remote").scan_id
        assert (await save(h, (("second", "기존 값"),))).code == "REG_DESCRIPTIONS_SAVED"
        middle = await h.store.load_registration("remote")
        result = await save(h)
        assert result.code == "REG_DESCRIPTIONS_SAVED"
        assert result.server_id == "remote"
        assert result.rows[0].status == "pending"
        saved = await h.store.load_registration("remote")
        assert saved.registration.status is RegStatus.PENDING
        assert saved.registration.row_revision > middle.registration.row_revision
        assert middle.registration.row_revision > before.registration.row_revision
        assert tuple(t.reviewed_description for t in saved.registration.tools) == (
            REVIEWED,
            "기존 값",
        )
        assert tuple(t.fingerprint for t in saved.registration.tools) == tuple(
            t.fingerprint for t in before.registration.tools
        )
        assert h.scans.state("remote").scan_id == scan_id
        activation = await h.commands(RegistrationRequest("activation_preview", "remote"))
        assert activation.row_revision == saved.registration.row_revision
        assert activation.scan.scan_id == scan_id
        activated = await h.commands(
            RegistrationRequest(
                "activate",
                "remote",
                expected_row_revision=activation.row_revision,
                reviewed_scan_id=scan_id,
                allowed=("read",),
            )
        )
        assert activated.code == "REG_ACTIVATION_RESTART_REQUIRED"
        active = await h.store.load_registration("remote")
        assert active.registration.status is RegStatus.ACTIVE
        assert active.reviewed_scan_id == scan_id
        tools = Catalog(h.registry, profile_id="default").tool_views(("remote",))
        assert len(tools) == 1
        assert tools[0].description == REVIEWED
        assert SERVER_TEXT not in tools[0].description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pairs", "code"),
    [
        ((("read", "x" * 2001),), "REG_DESCRIPTION_INVALID"),
        ((("read", "a\rb"),), "REG_DESCRIPTION_INVALID"),
        ((("read", "\u2028"),), "REG_DESCRIPTION_INVALID"),
        ((("missing", "value"),), "REG_DESCRIPTIONS_INVALID"),
        ((("read", "one"), ("read", "two")), "REG_DESCRIPTIONS_INVALID"),
        ([("read", "value")], "REG_DESCRIPTIONS_INVALID"),
        ((["read", "value"],), "REG_DESCRIPTIONS_INVALID"),
        ((("read",),), "REG_DESCRIPTIONS_INVALID"),
        ((("read", 1),), "REG_DESCRIPTIONS_INVALID"),
    ],
)
async def test_invalid_inputs_never_write(tmp_path, monkeypatch, pairs, code):
    async with harness(tmp_path) as h:
        before = await h.store.load_registration("remote")
        writes = []

        async def unexpected(*args, **kwargs):
            writes.append(1)
            raise AssertionError("unexpected description write")

        monkeypatch.setattr(h.store, "save_reviewed_descriptions", unexpected)
        assert (await save(h, pairs)).code == code
        assert writes == []
        assert await h.store.load_registration("remote") == before


@pytest.mark.asyncio
async def test_stale_active_and_missing_never_write(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        before = await h.store.load_registration("remote")
        writes = []

        async def unexpected(*args, **kwargs):
            writes.append(1)
            raise AssertionError("unexpected description write")

        monkeypatch.setattr(h.store, "save_reviewed_descriptions", unexpected)
        assert (await save(h, revision=before.registration.row_revision - 1)).code == (
            "REG_DESCRIPTIONS_STALE"
        )
        assert (await preview(h, "missing")).code == "REG_DESCRIPTIONS_UNAVAILABLE"
        assert (await save(h, server_id="missing")).code == "REG_DESCRIPTIONS_STALE"
        assert await h.store.load_registration("remote") == before
        await h.registry.activate(
            "remote",
            expected_row_revision=before.registration.row_revision,
            reviewed_scan_id=None,
            reviewed_by="local-user",
        )
        active = await h.store.load_registration("remote")
        assert (await preview(h)).code == "REG_DESCRIPTIONS_UNAVAILABLE"
        assert (await save(h)).code == "REG_DESCRIPTIONS_STALE"
        assert await h.store.load_registration("remote") == active
        assert writes == []


@pytest.mark.asyncio
async def test_fresh_run_has_no_server_text_but_retains_reviewed(tmp_path):
    async with harness(tmp_path) as h:
        await save(h)
        await h.scans.aclose()
        assert h.scans.server_descriptions("remote") is None
        assert h.scans._descriptions == {}
        h.scans = RegistrationScans(h.registry, FakeScanner(h.registry), h.commands.collect)
        h.commands.scans = h.scans
        view = await preview(h)
        assert view.descriptions[0] == DescriptionRow("read", None, REVIEWED)
        h.commands.scans = None
        assert (await preview(h)).descriptions == view.descriptions


@pytest.mark.asyncio
async def test_cache_copies_matches_fingerprints_and_precedes_scanner(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        cached = h.scans.server_descriptions("remote")
        cached["read"] = "mutated"
        assert h.scans.server_descriptions("remote")["read"] == SERVER_TEXT
        assert h.scans.server_descriptions("missing") is None
        changed = SimpleNamespace(
            complete=True,
            tools=(
                FrozenJsonObject(
                    {"name": "read", "description": "new", "inputSchema": {"type": "object"}}
                ),
            ),
        )
        server = load_profile_settings(h.policy.registration_document(), h.registry).servers[0]
        await h.registry.observe(server, changed)
        assert h.scans.server_descriptions("remote") is None
        h.listing = changed
        seen = []

        async def scan(_id, _listing, **kwargs):
            seen.append(h.scans.server_descriptions("remote"))
            raise RuntimeError("private scanner error")

        monkeypatch.setattr(h.scanner, "scan", scan)
        assert (await h.commands(RegistrationRequest("scan", "remote"))).code == "SCAN_STARTED"
        await settled(h)
        assert seen == [{"read": "new"}]
        assert h.scans.server_descriptions("remote") == {"read": "new"}
        h.scans._descriptions.clear()
        h.listing = SimpleNamespace(complete=False, tools=())
        await h.commands(RegistrationRequest("scan", "remote"))
        await settled(h)
        assert seen[-1] is None
        assert h.scans._descriptions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        StoreError(Reason.APPROVAL_RACE),
        StoreError(Reason.REG_NOT_ACTIVE),
        RegistryError("REG_SERVER_UNKNOWN"),
    ],
)
async def test_known_write_refusals_are_stale(tmp_path, monkeypatch, error):
    async with harness(tmp_path) as h:
        before = await h.store.load_registration("remote")

        async def refuse(*args, **kwargs):
            raise error

        monkeypatch.setattr(h.registry, "save_reviewed_descriptions", refuse)
        assert (await save(h)).code == "REG_DESCRIPTIONS_STALE"
        assert not h.commands._unknown
        assert await h.store.load_registration("remote") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["store", "cancel", "unexpected"])
async def test_uncertain_writes_block_retry_and_propagate_cancellation(
    tmp_path, monkeypatch, failure
):
    async with harness(tmp_path) as h:
        writes = []
        original = h.store.save_reviewed_descriptions

        async def uncertain(*args, **kwargs):
            writes.append(1)
            await original(*args, **kwargs)
            if failure == "store":
                raise StoreError()
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("private exception text")

        monkeypatch.setattr(h.store, "save_reviewed_descriptions", uncertain)
        if failure == "store":
            assert (await save(h)).code == "REG_STORE_UNKNOWN"
        elif failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await save(h)
        else:
            with pytest.raises(RuntimeError, match="private exception text"):
                await save(h)
        assert h.commands._unknown
        assert "remote" in h.registry._stale
        assert (await save(h)).code == "REG_STORE_UNKNOWN"
        assert writes == [1]
        assert (await h.store.load_registration("remote")).registration.tools[
            0
        ].reviewed_description == REVIEWED


@pytest.mark.parametrize(
    ("value", "code"),
    [(value, None) for value in ("", "\n", "\t", "한글", "가" * 2000, "😀" * 2000)]
    + [("가" * 2001, "REG_DESCRIPTION_TOO_LONG")]
    + [
        (value, "REG_DESCRIPTION_CHARACTERS")
        for value in ("a\rb", "\x00", "\u2028", "\u202e", "\ud800", None, 1)
    ],
)
def test_description_validator(value, code):
    assert reviewed_description_issue(value) == code


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(app, monkeypatch):
    bridge = BackendBridge(lambda: None)
    requests, roots = [], []
    monkeypatch.setattr(
        bridge, "request_registration", lambda request, **kw: requests.append((request, kw))
    )
    yield bridge, requests, roots
    for root in roots:
        root.close()
        root.deleteLater()
    bridge.close()
    bridge.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def dialog_preview(ui, server_text=SERVER_TEXT):
    bridge, requests, roots = ui
    dialog = DescriptionReviewDialog(bridge, "remote")
    roots.append(dialog)
    dialog.open()
    assert len(requests) == 1
    assert requests[0][0] == RegistrationRequest("descriptions_preview", "remote")
    assert not dialog.save_button.isEnabled()
    view = RegistrationView(
        "REG_DESCRIPTIONS_PREVIEW",
        requests[-1][1]["command_id"],
        row_revision=17,
        descriptions=(DescriptionRow("read", server_text, REVIEWED),),
    )
    bridge.registration_changed.emit(replace(view, command_id="other"))
    assert dialog.edits == {}
    bridge.registration_changed.emit(view)
    assert_qt_owned(roots)
    return dialog


def test_dialog_plain_text_validation_save_lf_and_cleanup(ui):
    bridge, requests, _ = ui
    dialog = dialog_preview(ui)
    view = dialog.findChildren(UntrustedTextView)[0]
    assert view.body.toPlainText() == untrusted_display_text(SERVER_TEXT, max_chars=4000)[0]
    assert view.body.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
    assert view.body.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
    assert all(SERVER_TEXT not in label.text() for label in dialog.findChildren(QLabel))
    assert all(
        label.textFormat() == Qt.TextFormat.PlainText for label in dialog.findChildren(QLabel)
    )
    edit = dialog.edits["read"]
    assert edit.toPlainText() == REVIEWED
    assert edit.accessibleName() == "모델에 보낼 설명 · read"
    edit.setPlainText("가" * 2001)
    assert not dialog.save_button.isEnabled()
    assert "2,000자를 넘음" in dialog._issues["read"].text()
    edit.setPlainText("bad\u202e")
    assert not dialog.save_button.isEnabled()
    assert "쓸 수 없는 문자가 있음" in dialog._issues["read"].text()
    edit.setPlainText("첫 줄\r\n둘째 줄\n셋째 줄")
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    request, options = requests[-1]
    assert request.action == "descriptions_save"
    assert request.expected_row_revision == 17
    assert request.descriptions == (("read", "첫 줄\n둘째 줄\n셋째 줄"),)
    assert "\r" not in request.descriptions[0][1]
    assert not dialog.save_button.isEnabled()
    dialog.save_button.click()
    assert len(requests) == 2
    saved = RegistrationView("REG_DESCRIPTIONS_SAVED", options["command_id"])
    bridge.registration_changed.emit(replace(saved, command_id="other"))
    assert dialog.result_label.text() == ""
    bridge.registration_changed.emit(saved)
    assert dialog.result_label.text() == (
        "검토한 설명을 저장함. 이 서버를 활성화하고 앱을 다시 시작하면 새 세션부터 쓰임."
    )
    edit.setPlainText("수정")
    assert not dialog.save_button.isEnabled()
    dialog.close()
    assert view.body.toPlainText() == edit.toPlainText() == ""
    assert not edit.document().isUndoAvailable()
    bridge.registration_changed.emit(saved)
    assert not dialog.save_button.isEnabled()


@pytest.mark.parametrize("server_text", [None, ""])
def test_dialog_missing_or_empty_server_description(ui, server_text):
    dialog = dialog_preview(ui, server_text)
    if server_text is None:
        assert any(
            "이번 실행에서 받은 서버 설명 없음" in label.text()
            for label in dialog.findChildren(QLabel)
        )
        assert not dialog.findChildren(UntrustedTextView)
    else:
        assert not dialog.findChildren(UntrustedTextView)
        assert any(
            label.text() == "서버가 보낸 설명 없음" and label.objectName() == "secondary"
            for label in dialog.findChildren(QLabel)
        )
    assert dialog.edits["read"].toPlainText() == REVIEWED


@pytest.mark.parametrize("failure", [CommandStage.REJECTED, CommandStage.UNCONFIRMED, "OTHER"])
def test_dialog_preview_failure_blocks_save(ui, failure):
    # I6/I8: failed previews stay disabled without implying a write occurred.
    bridge, requests, roots = ui
    dialog = DescriptionReviewDialog(bridge, "remote")
    roots.append(dialog)
    dialog.open()
    request, options = requests[-1]
    assert request.action == "descriptions_preview"
    if failure == "OTHER":
        event = RegistrationView(failure, options["command_id"])
        signal = bridge.registration_changed
    else:
        event = CommandEvent(
            options["command_id"], "control", "registration", "registry", failure, "fixed"
        )
        signal = bridge.command_changed
    signal.emit(replace(event, command_id="other"))
    assert dialog.result_label.text() == ""
    signal.emit(event)
    assert dialog.result_label.text() == "설명을 불러오지 못함. 창을 닫고 다시 여세요."
    assert "저장 결과" not in dialog.result_label.text()
    assert not dialog.save_button.isEnabled()
    dialog.save_button.click()
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("REG_DESCRIPTIONS_UNAVAILABLE", "PENDING 서버만 설명을 검토할 수 있음."),
        ("REG_STORE_UNKNOWN", "저장 결과 불명. 다시 저장하지 말고 앱을 다시 시작하세요."),
    ],
)
def test_dialog_preview_known_failure_messages(ui, code, text):
    bridge, requests, roots = ui
    dialog = DescriptionReviewDialog(bridge, "remote")
    roots.append(dialog)
    dialog.open()
    assert requests[-1][0].action == "descriptions_preview"
    bridge.registration_changed.emit(RegistrationView(code, requests[-1][1]["command_id"]))
    assert dialog.result_label.text() == text
    assert not dialog.save_button.isEnabled()


@pytest.mark.parametrize(
    ("code", "text", "enabled"),
    [
        ("REG_DESCRIPTIONS_STALE", "서버 상태가 바뀌어 저장하지 않음", False),
        ("REG_DESCRIPTION_INVALID", "저장할 수 없는 설명이 있음", True),
        ("REG_DESCRIPTIONS_INVALID", "저장할 수 없는 설명이 있음", True),
        ("REG_DESCRIPTIONS_UNAVAILABLE", "PENDING 서버만 설명을 검토할 수 있음", False),
        ("REG_STORE_UNKNOWN", "저장 결과 불명", False),
        ("OTHER", "저장 결과를 확인하지 못함", False),
    ],
)
def test_dialog_result_messages_and_retry_rules(ui, code, text, enabled):
    bridge, requests, _ = ui
    dialog = dialog_preview(ui)
    dialog.save_button.click()
    bridge.registration_changed.emit(RegistrationView(code, requests[-1][1]["command_id"]))
    assert dialog.result_label.text().startswith(text)
    assert dialog.save_button.isEnabled() == enabled


@pytest.mark.parametrize("stage", [CommandStage.REJECTED, CommandStage.UNCONFIRMED])
def test_dialog_command_failure_is_correlated_and_blocks_save(ui, stage):
    bridge, requests, _ = ui
    dialog = dialog_preview(ui)
    dialog.save_button.click()
    event = CommandEvent(
        requests[-1][1]["command_id"], "control", "registration", "registry", stage, "fixed"
    )
    bridge.command_changed.emit(replace(event, command_id="other"))
    assert dialog.result_label.text() == ""
    bridge.command_changed.emit(event)
    assert dialog.result_label.text() == (
        "저장 결과를 확인하지 못함. 다시 저장하지 말고 앱을 다시 시작하세요."
    )
    assert not dialog.save_button.isEnabled()
    dialog.close()
    bridge.command_changed.emit(event)


def test_scan_review_opens_description_dialog(ui):
    bridge, requests, roots = ui
    scan = ScanReviewDialog(bridge, "remote")
    roots.append(scan)
    scan.open()
    assert scan.describe_button.text() == "설명 검토"
    scan.describe_button.click()
    dialog = scan._description_dialog
    assert isinstance(dialog, DescriptionReviewDialog)
    assert dialog.isVisible()
    assert dialog.parent() is scan
    assert requests[-1][0] == RegistrationRequest("descriptions_preview", "remote")
    assert_qt_owned(roots)
    dialog.close()


def test_bridge_facade_accepts_description_actions(app, monkeypatch):
    bridge = BackendBridge(lambda: None)
    submitted = []
    monkeypatch.setattr(bridge._host.commands, "submit", lambda command: submitted.append(command))
    try:
        for action in ("descriptions_preview", "descriptions_save"):
            bridge.request_registration(RegistrationRequest(action, "remote"))
        assert [command.registration.action for command in submitted] == [
            "descriptions_preview",
            "descriptions_save",
        ]
    finally:
        bridge.close()
        bridge.deleteLater()
