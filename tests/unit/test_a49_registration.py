"""A49 orchestration: I7/I10, T33/T38 and S01 folder boundaries, EXE-01/CFG-01."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from guard_app.domain.contracts import Denied, Released
from guard_app.domain.enums import Label, RegStatus, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.security.ids import new_id
from guard_app.servers.file_server import FILE_SERVER_TOOL_SETTINGS, TOOLS, file_server_args
from guard_app.services.bridge import BackendBridge, RegistrationRequest, RegistrationView
from guard_app.services.product_assembly import assemble_guard
from guard_app.services.product_settings import SaveOutcomeUnknownError, read_settings
from guard_app.services.registry import ServerRegistry
from guard_app.ui import server_add_folders

from .test_bridge_registration import confirm, harness, registration_form
from .test_product_assembly import Peer, inputs


def request(root, **kwargs):
    return RegistrationRequest(
        "prepare_file",
        "docs",
        tier="internal",
        max_label="CONFIDENTIAL",
        file_root=str(root),
        root_label="PUBLIC",
        list_root_label="INTERNAL",
        **kwargs,
    )


async def file_collect(server):
    return SimpleNamespace(complete=True, tools=tuple(FrozenJsonObject(t) for t in TOOLS))


def test_picker_preserves_case_and_invalidates_confirmation(tmp_path, monkeypatch):
    bridge = BackendBridge(lambda: None)
    sent = []
    monkeypatch.setattr(bridge, "request_registration", lambda req, **kw: sent.append((req, kw)))
    with registration_form() as form:
        form.attach_registration(bridge)
        form.server_id.setText("docs")
        form.transport.setCurrentIndex(form.transport.findData("local_file"))
        form.tier.setCurrentIndex(form.tier.findData("internal"))
        form.max_label.setCurrentIndex(form.max_label.findData("CONFIDENTIAL"))
        folders = form.file_folders
        chosen = str(tmp_path / "MixedRoot")
        monkeypatch.setattr(
            server_add_folders.QFileDialog, "getExistingDirectory", lambda *a: chosen
        )
        folders.root.browse.click()
        folders.root.label.setCurrentIndex(folders.root.label.findData("PUBLIC"))
        folders.list_root.label.setCurrentIndex(folders.list_root.label.findData("INTERNAL"))
        row = folders.add_rule()
        chosen += "/SecretCase"
        row.browse.click()
        row.label.setCurrentIndex(row.label.findData("CONFIDENTIAL"))
        assert folders.root.path.isReadOnly()
        assert row.path.isReadOnly()
        assert not form.command.isVisibleTo(form)
        assert not form.fill_button.isEnabled()
        assert "폴더 이름은 상위 폴더 등급으로 보임" in folders.notice.text()
        form.prepare_button.click()
        req, kw = sent[-1]
        assert BackendBridge.request_registration(bridge, req)
        assert req.action == "prepare_file"
        assert req.command is None
        assert req.folder_labels == ((chosen, "CONFIDENTIAL"),)
        assert req.root_label == "PUBLIC"
        assert req.list_root_label == "INTERNAL"
        assert req.file_root == str(tmp_path / "MixedRoot")
        form._registration_result(
            RegistrationView(
                "REG_CONFIRM_REQUIRED", token=new_id("confirmation"), command_id=kw["command_id"]
            )
        )
        assert form.confirm_button.isEnabled()
        row.label.setCurrentIndex(row.label.findData("INTERNAL"))
        assert not form.confirm_button.isEnabled()
        before = folders.root.path.text()
        monkeypatch.setattr(server_add_folders.QFileDialog, "getExistingDirectory", lambda *a: "")
        folders.root.browse.click()
        assert folders.root.path.text() == before


@pytest.mark.asyncio
async def test_registration_activation_and_guard_use_registered_root(tmp_path):
    root = tmp_path / "MixedRoot"
    sub = root / "SecretCase"
    sub.mkdir(parents=True)
    (sub / "Doc.txt").write_text("synthetic", encoding="utf-8")

    async with harness(tmp_path) as h:
        h.commands.collect = file_collect
        req = request(root, folder_labels=((str(sub), "CONFIDENTIAL"),))
        preview = await h.commands(req)
        assert preview.code == "REG_CONFIRM_REQUIRED"
        assert preview.command == sys.executable
        assert preview.sha256
        assert preview.size > 0
        assert (await confirm(h, preview)).code == "REG_REGISTERED"
        assert h.registry.status("docs") is RegStatus.PENDING
        doc = read_settings(h.folders).document
        server = doc["servers"][0]
        assert server["command"] == sys.executable
        assert server["args"] == list(file_server_args(root))
        assert server["tools"] == list(FILE_SERVER_TOOL_SETTINGS)
        assert doc["profile"]["servers"] == ["docs"]
        assert doc.get("access_rules", {}).get("allow", []) == []
        sources = doc["profile"]["sources"]
        assert {tuple(s["prefix"]) for s in sources} == {(), ("SecretCase",)}
        assert next(s["label"] for s in sources if s["tool"] == "list_root") == "INTERNAL"
        activation = await h.commands(RegistrationRequest("activation_preview", "docs"))
        assert all(
            t.kind == "read" and t.kind_fixed for t in activation.activation.servers[0].tools
        )
        result = await h.commands(
            RegistrationRequest(
                "activate",
                "docs",
                expected_row_revision=activation.row_revision,
                allowed=("list_root", "read_file"),
                denied=("list_folder",),
            )
        )
        assert result.code == "REG_ACTIVATION_RESTART_REQUIRED"
        assert h.registry.status("docs") is RegStatus.ACTIVE
        doc = read_settings(h.folders).document
        # Reload C Registry from actual B SQLite, compose a fresh transport.
        registry = ServerRegistry(h.store)
        await registry.load()
        peer = Peer(list(TOOLS))
        assembled = await assemble_guard(h.store, inputs(peer, registry, doc))
        try:
            settings = assembled.settings
            await h.store.create_task(
                TaskState(
                    task_id="task-a49",
                    epoch=1,
                    profile_id=settings.profile_id,
                    policy_version=settings.policy_version,
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
            # Missing paths must not dispatch even if a fabricated resolver would accept them.
            for call_id, tool, arguments, expected, label, count in (
                ("list", "list_root", {}, Released, Label.INTERNAL, 1),
                (
                    "read",
                    "read_file",
                    {"path": "SecretCase/Doc.txt"},
                    Released,
                    Label.CONFIDENTIAL,
                    2,
                ),
                ("missing", "read_file", {"path": "SecretCase/Missing.txt"}, Denied, None, 2),
                ("denied", "list_folder", {"path": "SecretCase"}, Denied, None, 2),
            ):
                outcome = await assembled.guard.submit(
                    "task-a49",
                    ProposedCall(
                        tool_ref=f"docs__{tool}", arguments=arguments, model_call_id=call_id
                    ),
                    expected_epoch=1,
                )
                assert isinstance(outcome, expected), call_id
                if label is not None:
                    assert (await h.store.load_task("task-a49")).high_watermark is label
                assert len(peer.calls) == count, call_id
            assert assembled.resource_resolver is not None
        finally:
            await assembled.aclose()


@pytest.mark.parametrize("kind", ["overlap", "duplicate", "missing", "outside", "file", "label"])
@pytest.mark.asyncio
async def test_invalid_folders_are_rejected_before_collection(tmp_path, kind):
    root = tmp_path / "Root"
    sub = root / "Child"
    nested = sub / "Nested"
    nested.mkdir(parents=True)
    file = sub / "file.txt"
    file.write_text("synthetic", encoding="utf-8")
    rules = {
        "overlap": ((str(sub), "PUBLIC"), (str(nested), "INTERNAL")),
        "duplicate": ((str(sub), "PUBLIC"), (str(sub).lower(), "INTERNAL")),
        "missing": ((str(root / "Missing"), "PUBLIC"),),
        "outside": ((str(tmp_path), "PUBLIC"),),
        "file": ((str(file), "PUBLIC"),),
        "label": ((str(sub), "untrusted"),),
    }

    async with harness(tmp_path) as h:
        result = await h.commands(request(root, folder_labels=rules[kind]))
        assert result.code == "REG_FOLDER_INVALID"
        assert not h.collected
        assert await h.store.list_registrations() == ()


@pytest.mark.parametrize("failure", ["registry", "settings", "unknown"])
@pytest.mark.asyncio
async def test_file_registration_failure_never_reports_success_or_retries(
    tmp_path, monkeypatch, failure
):
    root = tmp_path / "Root"
    root.mkdir()

    async with harness(tmp_path) as h:
        h.commands.collect = file_collect
        writes = []
        if failure == "registry":

            async def fail(*a, **kw):
                writes.append(1)
                raise StoreError()

            monkeypatch.setattr(h.store, "register_server", fail)
        else:

            async def save(change):
                writes.append(1)
                if failure == "unknown":
                    raise SaveOutcomeUnknownError()
                return False

            h.policy._saver = save
        preview = await h.commands(request(root))
        result = await confirm(h, preview)
        assert (
            result.code
            == {
                "registry": "REG_STORE_UNKNOWN",
                "settings": "REG_SETTINGS_SAVE_FAILED",
                "unknown": "POLICY_SAVE_UNKNOWN",
            }[failure]
        )
        assert (await h.commands(request(root))).code != "REG_CONFIRM_REQUIRED"
        if failure != "registry":
            assert h.admission.restart_code == "POLICY_SAVE_UNKNOWN"
        assert writes == [1]
        assert read_settings(h.folders).document is None


@pytest.mark.asyncio
async def test_folder_removed_after_confirmation_is_rechecked(tmp_path):
    root = tmp_path / "Root"
    sub = root / "Child"
    sub.mkdir(parents=True)

    async with harness(tmp_path) as h:
        preview = await h.commands(request(root, folder_labels=((str(sub), "PUBLIC"),)))
        assert preview.token
        sub.rmdir()
        assert (await confirm(h, preview)).code == "REG_FOLDER_INVALID"
        assert (await confirm(h, preview)).code == "REG_CONFIRM_EXPIRED"
        assert not h.collected
