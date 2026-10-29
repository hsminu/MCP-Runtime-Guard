"""A155 / I1,I6,I7,I8,I10 / T10,T18,T29,T38: A orchestration with B/C APIs."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from guard_app.domain.enums import RegStatus, TaskStatus, ToolKind
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.services.bridge import (
    BackendBridge,
    RegistrationRequest,
    RegistrationView,
    ToolSettingsEdit,
    ToolSettingsRow,
)
from guard_app.services.bridge_registration import RegistrationCommands
from guard_app.services.catalog import load_profile_settings, prepare_policy_change
from guard_app.services.guard import PolicyChangeError
from guard_app.services.product_settings import (
    SaveOutcomeUnknownError,
    SettingsFileChangedError,
    SettingsFileSaver,
    read_settings,
)
from guard_app.services.registry import RegistryError, ServerRegistry
from guard_app.services.session_admission import SessionAdmission
from guard_app.services.session_policy import TaskPolicy
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.presentation import session_blocker_notice
from guard_app.ui.servers import ServerListPage

from .test_a105_activation import assert_guard_closed, harness, loop_barrier


@asynccontextmanager
async def tools_harness(path, *, active=False, selected=True):
    async with harness(path, selected=selected) as h:
        definitions = [
            {
                "name": n,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "destination": {"type": "string"},
                        "number": {"type": "integer"},
                        "union": {"type": ["string", "null"]},
                        "nested": {"type": "object", "properties": {"hidden": {"type": "string"}}},
                    },
                },
            }
            for n in ("read", "second", "unknown")
        ]
        h.listing = SimpleNamespace(
            complete=True, tools=tuple(FrozenJsonObject(d) for d in definitions)
        )
        await h.registry.observe(h.server, h.listing)
        if active:
            await h.registry.activate(
                "docs",
                expected_row_revision=h.registry.entry("docs").registration.row_revision,
                reviewed_by="unit",
            )
        h.preview = await h.commands(RegistrationRequest("tool_settings_preview", "docs"))
        h.request = RegistrationRequest(
            "tool_settings_save",
            "docs",
            expected_row_revision=h.preview.row_revision,
            tool_settings=(ToolSettingsEdit("unknown", "read", "INTERNAL"),),
        )
        yield h


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "name",
        "kind",
        "label",
        "missing_label",
        "destination",
        "number",
        "union",
        "nested",
        "resource",
        "blocked",
        "revision",
        "bool_revision",
        "duplicate",
        "shape",
        "extraneous",
        "server",
    ],
)
async def test_rejects_without_any_write(tmp_path, case):
    async with tools_harness(tmp_path) as h:
        request = h.request
        if case == "server":
            request = replace(request, server_id="missing")
        edits = {
            "name": ToolSettingsEdit("missing", "other"),
            "kind": ToolSettingsEdit("unknown", "execute"),
            "label": ToolSettingsEdit("unknown", "read", "SECRET"),
            "missing_label": ToolSettingsEdit("unknown", "read"),
            "extraneous": ToolSettingsEdit("unknown", "other", "PUBLIC"),
        }
        if case in edits:
            request = replace(request, tool_settings=(edits[case],))
        if case in ("destination", "number", "union", "nested"):
            arg = {"destination": "missing", "nested": "hidden"}.get(case, case)
            request = replace(
                request, tool_settings=(ToolSettingsEdit("unknown", "send", destination_arg=arg),)
            )
        if case == "resource":
            h.doc["servers"][0]["tools"][0]["resource_arg"] = "path"
            request = replace(request, tool_settings=(ToolSettingsEdit("read", "other"),))
            preview = await h.commands(RegistrationRequest("tool_settings_preview", "docs"))
            assert preview.tool_settings[0].readonly
        if case == "blocked":
            await h.registry.block("docs", expected_row_revision=request.expected_row_revision)
        if case in ("revision", "bool_revision"):
            request = replace(
                request, expected_row_revision=False if case == "bool_revision" else -1
            )
        if case == "duplicate":
            request = replace(request, tool_settings=request.tool_settings * 2)
        if case == "shape":
            request = replace(request, tool_settings=({"name": "unknown", "kind": "read"},))
        before_file = read_settings(h.folders).document
        before_registry = await h.store.load_registration("docs")
        assert (await h.commands(request)).code in ("REG_TOOLS_INVALID", "REG_TOOLS_STALE")
        assert read_settings(h.folders).document == before_file
        assert await h.store.load_registration("docs") == before_registry
        assert h.admission.restart_code is None
        assert h.peer.calls == []


@pytest.mark.asyncio
async def test_document_and_restart_without_observe_enables_activation(tmp_path):
    async with tools_harness(tmp_path, selected=False) as h:
        assert h.preview.tool_settings[2].kind == "other"
        assert h.preview.tool_settings[2].registry_kind == "other"
        assert h.preview.tool_settings[2].string_arguments == ("destination",)
        activation = await h.commands(RegistrationRequest("activation_preview", "docs"))
        assert not activation.activation.servers[0].tools[2].kind_fixed
        assert (await h.commands(h.request)).code == "REG_TOOLS_RESTART_REQUIRED"
        saved = read_settings(h.folders).document
        assert saved["profile"]["servers"] == ["docs"]
        assert saved["profile"]["sources"] == [
            {"server": "docs", "tool": "unknown", "prefix": [], "label": "INTERNAL"}
        ]
        assert saved["servers"][0]["tools"][-1] == {"name": "unknown", "kind": "read"}
        assert (await h.store.load_task("task")).status is TaskStatus.HALTED
        assert (h.stopped, h.admission.restart_code) == (["task"], "REG_TOOLS_RESTART_REQUIRED")
        registry = ServerRegistry(h.store)
        await registry.load()
        policy = TaskPolicy(None, SessionAdmission(), document=saved, saver=h.saver)
        commands = RegistrationCommands(registry, h.store, policy, h.collect, h.stop)
        # A173: the saved metadata already matches, without starting the server.
        assert load_profile_settings(saved, registry).server_issue("docs") is None
        assert registry.status("docs") is RegStatus.PENDING
        assert registry.entry("docs").registration.tools[2].kind is ToolKind.READ
        after = await commands(RegistrationRequest("activation_preview", "docs"))
        assert after.activation.servers[0].tools[2].kind_fixed
        assert h.peer.calls == []


@pytest.mark.asyncio
async def test_preserves_unedited_sources_denies_and_removes_changed_allow(tmp_path):
    async with tools_harness(tmp_path) as h:
        before = FrozenJsonObject(h.doc).to_dict()
        h.doc["profile"]["sources"] = [
            {"server": "docs", "tool": "read", "prefix": [], "label": "PUBLIC"},
            {"server": "docs", "tool": "read", "prefix": ["keep"], "label": "CONFIDENTIAL"},
            {"server": "other", "tool": "second", "prefix": [], "label": "INTERNAL"},
            {"server": "docs", "tool": "unknown", "prefix": [], "label": "PUBLIC"},
        ]
        h.doc["servers"][0]["tools"].append({"name": "unknown", "kind": "read"})
        h.doc["servers"].append({**h.doc["servers"][0], "id": "other"})
        h.doc["profile"]["servers"].append("other")
        h.doc["access_rules"]["deny"] = [["docs", "second"]]
        seed = prepare_policy_change(before, h.doc, h.assembled.registry)
        assert await h.saver(seed)
        await h.policy._apply(h.assembled, seed, h.source)
        request = replace(
            h.request,
            tool_settings=(
                ToolSettingsEdit("read", "read", "CONFIDENTIAL"),
                ToolSettingsEdit("second", "send", destination_arg="destination"),
            ),
        )
        assert (await h.commands(request)).code == "REG_TOOLS_RESTART_REQUIRED"
        saved = read_settings(h.folders).document
        assert saved["profile"]["sources"] == [
            *h.doc["profile"]["sources"][1:],
            {"server": "docs", "tool": "read", "prefix": [], "label": "CONFIDENTIAL"},
        ]
        assert saved["access_rules"] == {"allow": [["docs", "read"]], "deny": [["docs", "second"]]}
        assert saved["servers"][0]["tools"][-1] == {
            "name": "second",
            "kind": "send",
            "destination_arg": "destination",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["other", "read"])
async def test_other_and_identical_read_save_stop_tasks(tmp_path, kind):
    async with tools_harness(tmp_path, active=True) as h:
        # No-op READ still stops Task rows even when B's content hash is unchanged.
        document = FrozenJsonObject(h.doc).to_dict()
        document["servers"][0]["tools"].reverse()
        document["profile"]["sources"] = [
            {"server": "docs", "tool": "read", "prefix": [], "label": "PUBLIC"}
        ]
        seed = prepare_policy_change(h.doc, document, h.assembled.registry)
        assert await h.saver(seed)
        await h.policy._apply(h.assembled, seed, h.source)
        h.policy._document = seed.document
        from datetime import UTC, datetime

        await h.store.create_task(h.policy.task_state("new"), created_at_utc=datetime.now(UTC))

        async def source():
            return (await h.store.load_task("new"),)

        h.policy._task_source = source
        previous_version = h.assembled.settings.policy_version
        request = replace(
            h.request,
            tool_settings=(ToolSettingsEdit("read", kind, "PUBLIC" if kind == "read" else None),),
        )
        assert (await h.commands(request)).code == "REG_TOOLS_RESTART_REQUIRED"
        saved = read_settings(h.folders).document
        assert saved["servers"][0]["tools"][-1] == {"name": "read", "kind": kind}
        assert (await h.store.load_task("new")).status is TaskStatus.HALTED
        assert "new" in h.stopped
        assert (h.assembled.settings.policy_version == previous_version) == (kind == "read")
        assert (["docs", "read"] in saved["access_rules"]["allow"]) == (kind == "read")
        assert bool(saved["profile"]["sources"]) == (kind == "read")


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("assembled", [False, True])
async def test_apply_settings_precedes_file_policy_and_close(
    tmp_path, monkeypatch, active, assembled
):
    """A173 / I6,I10 / T10: persisted metadata precedes the file, even for PENDING."""
    async with tools_harness(tmp_path, active=active) as h:
        calls = []
        apply_settings = h.registry.apply_settings
        apply_policy = h.policy._apply
        close = h.assembled.guard.aclose
        before_file = read_settings(h.folders).document
        if not assembled:
            h.policy._assembled = None

        async def apply(server, **kwargs):
            calls.append("settings")
            assert read_settings(h.folders).document == before_file
            assert server.tools[-1].name == "unknown"
            assert server.tools[-1].kind is ToolKind.READ
            assert kwargs["expected_row_revision"] == h.request.expected_row_revision
            assert kwargs["invalidate_tool"] == (
                h.assembled.guard.invalidate_tool if assembled else None
            )
            return await apply_settings(server, **kwargs)

        async def save(change):
            calls.append("file")
            assert h.registry.entry("docs").registration.tools[2].kind is ToolKind.READ
            assert h.registry.status("docs") is RegStatus.PENDING
            return await h.saver(change)

        async def policy(*args):
            calls.append("policy")
            assert read_settings(h.folders).document["servers"][0]["tools"][-1]["kind"] == "read"
            return await apply_policy(*args)

        async def closed():
            calls.append("close")
            assert h.admission.restart_code == "REG_TOOLS_RESTART_REQUIRED"
            await close()

        async def forbidden(*args, **kwargs):
            pytest.fail("request_review/activate must not be used by tool settings")

        monkeypatch.setattr(h.registry, "apply_settings", apply)
        monkeypatch.setattr(h.registry, "request_review", forbidden)
        monkeypatch.setattr(h.registry, "activate", forbidden)
        monkeypatch.setattr(h.policy, "_apply", policy)
        h.policy._saver = save
        with monkeypatch.context() as scoped:
            scoped.setattr(h.assembled.guard, "aclose", closed)
            assert (await h.commands(h.request)).code == "REG_TOOLS_RESTART_REQUIRED"
        assert calls == (
            ["settings", "file", "policy", "close"] if assembled else ["settings", "file"]
        )
        assert not h.commands._unknown
        assert h.peer.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "REG_SETTINGS_INVALID",
        "REG_SERVER_ID_INVALID",
        "REG_SERVER_UNKNOWN",
        "REG_SETTINGS_MISMATCH",
    ],
)
async def test_registry_refusal_does_not_save_or_require_restart(tmp_path, monkeypatch, code):
    async with tools_harness(tmp_path, active=True) as h:
        calls = []
        before = await h.store.load_registration("docs")
        before_file = read_settings(h.folders).document

        async def refuse(*args, **kwargs):
            calls.append("settings")
            raise RegistryError(code)

        async def forbidden(*args, **kwargs):
            pytest.fail("pre-write refusal must neither save nor close")

        monkeypatch.setattr(h.registry, "apply_settings", refuse)
        h.policy._saver = forbidden
        with monkeypatch.context() as scoped:
            scoped.setattr(h.assembled.guard, "aclose", forbidden)
            assert (await h.commands(h.request)).code == "REG_TOOLS_INVALID"
        assert calls == ["settings"]
        assert h.admission.restart_code is None
        assert not h.commands._unknown
        assert read_settings(h.folders).document == before_file
        assert await h.store.load_registration("docs") == before
        assert h.peer.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_missing_saver_refuses_before_apply_settings(tmp_path, monkeypatch, active):
    async with tools_harness(tmp_path, active=active) as h:
        before = await h.store.load_registration("docs")
        before_file = read_settings(h.folders).document

        async def forbidden(*args, **kwargs):
            pytest.fail("a missing saver must refuse before apply_settings")

        monkeypatch.setattr(h.registry, "apply_settings", forbidden)
        h.policy._saver = None
        assert (await h.commands(h.request)).code == "REG_SETTINGS_UNAVAILABLE"
        assert h.admission.restart_code is None
        assert not h.commands._unknown
        assert read_settings(h.folders).document == before_file
        assert await h.store.load_registration("docs") == before
        assert h.peer.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "false",
        "unknown",
        "cancel",
        "external",
        "store",
        "revision",
        "committed",
        "settings_cancel",
        "committed_cancel",
        "invalidate",
        "apply",
    ],
)
async def test_unknown_settings_outcomes_block_registration_and_close(
    tmp_path, monkeypatch, failure
):
    async with tools_harness(tmp_path, active=True) as h:
        calls = []
        apply_settings = h.registry.apply_settings

        async def apply(server, **kwargs):
            calls.append("settings")
            if failure == "store":
                raise StoreError()
            if failure == "settings_cancel":
                raise asyncio.CancelledError()
            if failure == "revision":
                kwargs["expected_row_revision"] -= 1
            await apply_settings(server, **kwargs)
            if failure == "committed":
                raise StoreError()
            if failure == "committed_cancel":
                raise asyncio.CancelledError()

        async def invalidate(*args):
            assert h.registry.entry("docs").registration.tools[2].kind is ToolKind.READ
            raise RuntimeError()

        monkeypatch.setattr(h.registry, "apply_settings", apply)
        if failure == "invalidate":
            monkeypatch.setattr(h.assembled.guard, "invalidate_tool", invalidate)

        async def save(change):
            calls.append("save")
            if failure == "false":
                return False
            if failure == "external":
                raise SettingsFileChangedError()
            if failure == "cancel":
                raise asyncio.CancelledError()
            result = await h.saver(change)
            if failure == "unknown":
                raise SaveOutcomeUnknownError()
            return result

        h.policy._saver = save
        if failure == "apply":

            async def refuse_apply(*args, **kwargs):
                raise PolicyChangeError("POLICY_CHANGE_INVALID")

            monkeypatch.setattr(h.assembled.guard, "apply_policy_change", refuse_apply)
        if "cancel" in failure or failure == "invalidate":
            with pytest.raises(RuntimeError if failure == "invalidate" else asyncio.CancelledError):
                await h.commands(h.request)
        else:
            assert (await h.commands(h.request)).code == {
                "false": "POLICY_SAVE_FAILED",
                "unknown": "POLICY_SAVE_UNKNOWN",
                "external": "POLICY_SAVE_FILE_CHANGED",
                "store": "REG_STORE_UNKNOWN",
                "revision": "REG_STORE_UNKNOWN",
                "committed": "REG_STORE_UNKNOWN",
                "apply": "POLICY_APPLY_FAILED",
            }[failure]
        before_file = failure in {
            "store",
            "revision",
            "committed",
            "settings_cancel",
            "committed_cancel",
            "invalidate",
        }
        assert calls == (["settings"] if before_file else ["settings", "save"])
        assert h.admission.restart_code == "REG_TOOLS_UNKNOWN"
        assert h.commands._unknown
        assert (await h.commands(h.request)).code == "REG_TOOLS_UNKNOWN"
        assert len(calls) == (1 if before_file else 2)
        await assert_guard_closed(h)


@pytest.mark.asyncio
async def test_unrecorded_active_server_settings_are_applied_before_save(tmp_path):
    async with tools_harness(tmp_path, active=True) as h:
        h.registry._unrecorded.add("docs")
        assert h.registry.status("docs") is RegStatus.PENDING
        assert (await h.commands(h.request)).code == "REG_TOOLS_RESTART_REQUIRED"
        stored = (await h.store.load_registration("docs")).registration
        assert stored.status is RegStatus.PENDING
        assert stored.tools[2].kind is ToolKind.READ


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["false", "unknown"])
async def test_resave_clears_persisted_settings_mismatch_without_connecting(
    tmp_path, monkeypatch, failure
):
    """#91 A promise 5 / I6,I10: real SQLite/Registry, no observe or startable_servers."""
    async with tools_harness(tmp_path, active=True) as h:

        async def failed_file(change):
            if failure == "unknown":
                raise SaveOutcomeUnknownError()
            return False

        h.request = replace(
            h.request,
            tool_settings=(ToolSettingsEdit("second", "send", destination_arg="destination"),),
        )
        h.policy._saver = failed_file
        assert (await h.commands(h.request)).code == (
            "POLICY_SAVE_UNKNOWN" if failure == "unknown" else "POLICY_SAVE_FAILED"
        )
        saved = read_settings(h.folders).document
        assert (await h.store.load_registration("docs")).registration.tools[1].kind is ToolKind.SEND
        assert (
            load_profile_settings(saved, h.registry).server_issue("docs") == "REG_SETTINGS_MISMATCH"
        )
        registry = ServerRegistry(h.store)
        await registry.load()
        admission = SessionAdmission()
        policy = TaskPolicy(
            None, admission, document=saved, saver=SettingsFileSaver(read_settings(h.folders))
        )

        async def forbidden(*args, **kwargs):
            pytest.fail("repair must not connect, observe, or activate")

        monkeypatch.setattr(registry, "observe", forbidden)
        monkeypatch.setattr(registry, "activate", forbidden)
        commands = RegistrationCommands(registry, h.store, policy, forbidden, h.stop)
        preview = await commands(RegistrationRequest("tool_settings_preview", "docs"))
        assert preview.code == "REG_TOOLS_PREVIEW"
        assert preview.tool_settings[1].registry_kind == "send"
        assert preview.tool_settings[1].kind == "read"
        result = await commands(replace(h.request, expected_row_revision=preview.row_revision))
        assert result.code == "REG_TOOLS_RESTART_REQUIRED"
        repaired = read_settings(h.folders).document
        assert load_profile_settings(repaired, registry).server_issue("docs") is None
        assert registry.status("docs") is RegStatus.PENDING
        assert h.peer.calls == []


@pytest.mark.asyncio
async def test_waits_for_policy_lock_and_uses_latest_document(tmp_path):
    async with tools_harness(tmp_path) as h:
        writes = []

        async def save(change):
            writes.append(1)
            return await h.saver(change)

        h.policy._saver = save
        async with h.policy.lock:
            operation = asyncio.create_task(h.commands(h.request))
            await loop_barrier()
            assert not operation.done()
            assert writes == []
            h.policy._document = {**h.doc, "policy": {"approval_ttl_seconds": 170}}
            seed = prepare_policy_change(h.doc, h.policy._document, h.assembled.registry)
            assert await h.saver(seed)
            await h.policy._apply(h.assembled, seed, h.source)
        assert (await asyncio.wait_for(operation, 3)).code == "REG_TOOLS_RESTART_REQUIRED"
        assert read_settings(h.folders).document["policy"]["approval_ttl_seconds"] == 170


def test_ui_plain_names_controls_bridge_request_and_result(monkeypatch):
    app = QApplication.instance() or QApplication([])
    bridge = BackendBridge(lambda: None)
    submitted = []
    monkeypatch.setattr(
        bridge._host.commands,
        "submit",
        lambda command: submitted.append(command) or command.command_id,
    )
    for action in ("tool_settings_preview", "tool_settings_save"):
        bridge.request_registration(RegistrationRequest(action, "docs"))
        assert submitted[-1].registration.action == action
    assert "다시 시작한 뒤 서버 목록에서 활성화하세요" in session_blocker_notice(
        "REG_TOOLS_RESTART_REQUIRED"
    )
    assert "다시 저장하지 말고" in session_blocker_notice("REG_TOOLS_UNKNOWN")
    requests = []
    monkeypatch.setattr(
        bridge, "request_registration", lambda request, **kw: requests.append((request, kw))
    )
    page = ServerListPage()
    page.attach_activation(bridge)
    page.set_servers(
        Surface(LoadState.READY, (ServerSummary("docs", "docs", registry_status="active"),))
    )
    page.select_server("docs")
    page.tools_button.click()
    assert requests[-1][0].action == "tool_settings_preview"
    name, arg = "<b>tool</b>\u202e", "<i>target</i>\u202e"
    rows = (
        ToolSettingsRow(name, "other", "other", None, None, (arg,), False),
        ToolSettingsRow("none", "other", "other", None, None, (), False),
        ToolSettingsRow("resource", "read", "read", None, "PUBLIC", (), True),
    )
    view = RegistrationView(
        "REG_TOOLS_PREVIEW",
        requests[-1][1]["command_id"],
        row_revision=17,
        tool_settings=rows,
        server_id="docs",
    )
    page._activation_result(replace(view, command_id="stale"))
    assert page._tools_dialog is None
    page._activation_result(view)
    dialog = page._tools_dialog
    labels = dialog.findChildren(QLabel)
    assert all(w.textFormat() is Qt.TextFormat.PlainText for w in labels if "<b>" in w.text())
    assert any("<b>tool</b>" in w.text() and "\\u202E" in w.text() for w in labels)
    kind, label, dest = dialog.controls[name]
    assert not label.isEnabled()
    assert not dest.isEnabled()
    kind.setCurrentIndex(kind.findData("read"))
    assert label.isEnabled()
    assert not dest.isEnabled()
    assert not dialog.save_button.isEnabled()
    label.setCurrentIndex(label.findData("INTERNAL"))
    assert dialog.selection() == (ToolSettingsEdit(name, "read", "INTERNAL"),)
    kind.setCurrentIndex(kind.findData("send"))
    assert not label.isEnabled()
    assert dest.isEnabled()
    assert "\\u202E" in dest.itemText(1)
    assert "<i>target</i>" in dest.itemText(1)
    dest.setCurrentIndex(1)
    assert not dialog.controls["resource"][0].isEnabled()
    assert not dialog.controls["none"][0].model().item(2).isEnabled()
    assert "다시 활성화" in dialog.warning.text()
    assert dialog.warning.text().splitlines()[2] == (
        "읽기 결과 등급은 그 도구가 돌려주는 모든 결과에 같이 붙음. "
        "여러 출처를 섞어 돌려주는 도구는 그중 가장 높은 등급을 고르세요."
    )
    assert "목적지는 정책 화면에서 등록하세요" in dialog.warning.text()
    dialog.save_button.click()
    request = requests[-1][0]
    assert request == RegistrationRequest(
        "tool_settings_save",
        "docs",
        expected_row_revision=17,
        tool_settings=(ToolSettingsEdit(name, "send", destination_arg=arg),),
    )
    page._activation_result(
        RegistrationView("REG_TOOLS_RESTART_REQUIRED", requests[-1][1]["command_id"])
    )
    assert "다시 시작한 뒤 서버 목록에서 활성화하세요" in page.activation_notice.text()
    page.close()
    app.processEvents()
