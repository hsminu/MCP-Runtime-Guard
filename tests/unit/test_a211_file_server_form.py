"""A211 / I7, T32, S01: UI choices only; backend validation stays authoritative."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from guard_app.services.bridge import BackendBridge, RegistrationView
from guard_app.ui import server_add, server_add_folders
from guard_app.ui.presentation import LABELS, TIER_NAMES
from guard_app.ui.server_add import LocalFolderRulesForm

from .test_server_add import app as app
from .test_server_add import form as form


@pytest.fixture
def picker(app, monkeypatch):
    selected = [""]
    starts = []

    def choose(_parent, _title, start):
        starts.append(start)
        return selected[0]

    monkeypatch.setattr(server_add_folders.QFileDialog, "getExistingDirectory", choose)

    def pick(choice, path):
        selected[0] = str(path)
        choice.browse.click()
        app.processEvents()

    pick.starts = starts
    return pick


@pytest.fixture
def registration(form, monkeypatch):
    def unused():
        raise AssertionError("No backend startup")

    bridge = BackendBridge(unused)
    sent = []
    monkeypatch.setattr(bridge, "request_registration", lambda req, **kw: sent.append((req, kw)))
    form.attach_registration(bridge)
    yield sent
    bridge.close()


def local(form):
    form.transport.setCurrentIndex(form.transport.findData("local_file"))
    return form.file_folders


def grade(choice, value):
    choice.label.setCurrentIndex(choice.label.findData(value))


def values(choice):
    return [choice.label.itemData(i) for i in range(1, choice.label.count())]


def test_local_hides_fields_titles_and_errors_and_keeps_display_name(form):
    local(form)
    assert form.server_id.text() == "local-files"
    assert form.tier.currentData() == "internal"
    assert form.max_label.currentData() == "CONFIDENTIAL"
    for key in ("server_id", "tier", "max_label"):
        assert all(not widget.isVisibleTo(form) for widget in form._field_rows[key])
        assert not form._errors[key].isVisibleTo(form)
    assert form.display_name.isVisibleTo(form)
    assert form.transport.isVisibleTo(form)
    form.display_name.setText("Different Name")
    form.display_name.textEdited.emit("Different Name")
    assert form.server_id.text() == "local-files"


def test_local_id_skips_case_and_width_folded_duplicates_on_each_update(form):
    form.set_known_ids({"local-files", "Local-Files-2"})
    local(form)
    assert form.server_id.text() == "local-files-3"
    form.set_known_ids({"ｌｏｃａｌ-files", "LOCAL-FILES-2", "local-files-3"})
    assert form.server_id.text() == "local-files-4"
    form.set_known_ids({"local-files"})
    assert form.server_id.text() == "local-files-2"


@pytest.mark.parametrize("manual", [False, True])
def test_leaving_local_restores_only_manual_id_and_resets_boundary(form, manual):
    form.server_id.setText("hand-written" if manual else "name-suggestion")
    form._id_from_name = not manual
    local(form)
    form.transport.setCurrentIndex(form.transport.findData("stdio"))
    assert form.server_id.text() == ("hand-written" if manual else "")
    assert form.tier.currentData() is None
    assert form.max_label.currentData() is None
    for key in ("server_id", "tier", "max_label"):
        assert all(widget.isVisibleTo(form) for widget in form._field_rows[key])
    assert form._errors["tier"].isHidden()
    assert form._errors["max_label"].isHidden()


def test_request_and_preview_use_fixed_defaults_and_shared_folder_label(form, picker, registration):
    folders = local(form)
    assert not hasattr(folders, "list_root")
    picker(folders.root, Path.cwd() / "Shared")
    grade(folders.root, "INTERNAL")
    preview = json.loads(form.preview.toPlainText())
    assert (preview["server_id"], preview["tier"], preview["max_label"]) == (
        "local-files",
        "internal",
        "CONFIDENTIAL",
    )
    form.prepare_button.click()
    req, _ = registration[-1]
    assert req.action == "prepare_file"
    assert req.list_root_label == req.root_label == "INTERNAL"
    assert (req.server_id, req.tier, req.max_label) == ("local-files", "internal", "CONFIDENTIAL")


def test_child_grades_follow_shared_grade_and_reset_invalid_selection(form, picker):
    folders = local(form)
    picker(folders.root, Path.cwd() / "Shared")
    row = folders.add_rule()
    assert not row.label.isEnabled()
    grade(folders.root, "PUBLIC")
    assert values(row) == ["PUBLIC", "INTERNAL", "CONFIDENTIAL"]
    grade(folders.root, "INTERNAL")
    assert values(row) == ["INTERNAL", "CONFIDENTIAL"]
    grade(row, "INTERNAL")
    grade(folders.root, "CONFIDENTIAL")
    assert row.label.currentData() is None
    assert row.label.currentText() == "등급 선택"
    assert values(row) == ["CONFIDENTIAL"]
    grade(row, "CONFIDENTIAL")
    grade(folders.root, "INTERNAL")
    assert row.label.currentData() == "CONFIDENTIAL"
    grade(folders.root, None)
    assert not row.label.isEnabled()
    assert row.label.currentData() is None


@pytest.mark.parametrize("suffix", ["outside", "self", "parent_escape"])
def test_child_picker_rejects_outside_or_shared_folder(form, picker, suffix):
    folders = local(form)
    root = Path.cwd() / "Shared"
    picker(folders.root, root)
    row = folders.add_rule()
    picker(row, root / "previous")
    candidate = {
        "outside": root.parent / "outside",
        "self": root,
        "parent_escape": root / ".." / "outside",
    }[suffix]
    picker(row, candidate)
    assert picker.starts[-1] == str(root)
    assert row.path.text() == row.selected_path == row.path.toolTip() == ""
    assert row.error.text() == "공유 폴더 안의 폴더를 고르세요."
    assert row.error.isVisibleTo(form)


@pytest.mark.parametrize(("first", "second"), [("a", "a/b"), ("a/b", "a"), ("a", "a")])
def test_child_picker_rejects_overlap_in_both_directions(form, picker, first, second):
    folders = local(form)
    root = Path.cwd() / "Shared"
    picker(folders.root, root)
    picker(folders.add_rule(), root / first)
    row = folders.add_rule()
    picker(row, root / second)
    assert not row.path.text()
    assert row.error.text() == "이미 고른 하위 폴더와 겹침. 다른 폴더를 고르세요."
    assert row.error.isVisibleTo(form)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path case folding")
def test_windows_overlap_is_case_insensitive(form, picker):
    folders = local(form)
    root = Path.cwd() / "Shared"
    picker(folders.root, root)
    picker(folders.add_rule(), root / "a")
    row = folders.add_rule()
    picker(row, root / "A" / "b")
    assert not row.selected_path
    assert "겹침" in row.error.text()


def test_relative_display_full_request_and_tooltip_without_filesystem_reads(
    form, picker, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No filesystem checks in the picker")

    root = Path.cwd() / "Shared"
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    folders = local(form)
    picker(folders.root, root)
    grade(folders.root, "INTERNAL")
    row = folders.add_rule()
    picker(row, root.parent / "outside")
    picker(row, root / "a" / "b")
    grade(row, "CONFIDENTIAL")
    assert row.path.text() == "└ a/b"
    assert row.path.toolTip() == str(root / "a" / "b")
    assert row.error.isHidden()
    assert folders.folder_labels() == ((str(root / "a" / "b"), "CONFIDENTIAL"),)
    assert folders.ready()
    picker(row, "")  # Cancel preserves the valid selection.
    assert folders.ready()


def test_root_change_clears_children_and_controls_add_button(form, picker):
    folders = local(form)
    assert not folders.add_button.isEnabled()
    root = Path.cwd() / "Shared"
    picker(folders.root, root)
    assert folders.add_button.isEnabled()
    folders.add_button.click()
    picker(folders.rows[0], root / "a")
    assert folders.rows[0].path.text() == "└ a"
    picker(folders.root, root)
    assert len(folders.rows) == 1
    picker(folders.root, root.parent / "Other")
    assert not folders.rows
    assert folders._rows.count() == 0
    assert folders.folder_labels() == ()


def test_visible_vocabulary_and_app_labels(form):
    folders = local(form)
    folders.add_rule()
    assert folders.notice.text() == (
        "공유 폴더를 고르고 등급을 정하세요. 더 높은 등급이 필요한 하위 폴더만 따로 추가하세요."
    )
    for word in ("낮아지지", "겹친", "list_root", "루트"):
        assert word not in folders.notice.text()
    legacy = LocalFolderRulesForm()
    try:
        for widget in (folders, legacy):
            for child in widget.findChildren(QWidget):
                assert "루트" not in child.accessibleName()
                if isinstance(child, (QLabel, QPushButton)):
                    assert "루트" not in child.text()
    finally:
        legacy.close()
    assert [LABELS[code][0] for code in ("PUBLIC", "INTERNAL", "CONFIDENTIAL")] == [
        "공개",
        "개인",
        "민감",
    ]
    assert TIER_NAMES["internal"] == "내부"


@pytest.mark.parametrize("already_local", [False, True])
def test_restore_keeps_registered_id_and_boundary(form, picker, registration, already_local):
    # The hidden fields cannot be changed, so a restore sends the Registry row's
    # boundary instead of the fixed defaults (A211 review).
    form.set_known_ids({"registered", "local-files"})
    if already_local:
        local(form)
    form.start_restore("registered", "general_external", "PUBLIC")
    folders = local(form)
    form.set_known_ids({"registered", "local-files", "local-files-2"})
    assert form.server_id.text() == "registered"
    assert form.tier.currentData() == "general_external"
    assert form.max_label.currentData() == "PUBLIC"
    picker(folders.root, Path.cwd() / "Shared")
    grade(folders.root, "PUBLIC")
    preview = json.loads(form.preview.toPlainText())
    assert (preview["tier"], preview["max_label"]) == ("general_external", "PUBLIC")
    assert "list_root_label" not in preview
    form.prepare_button.click()
    req, _kw = registration[-1]
    assert req.restore
    assert req.server_id == "registered"
    assert (req.tier, req.max_label) == ("general_external", "PUBLIC")
    form.transport.setCurrentIndex(form.transport.findData("stdio"))
    assert form.server_id.text() == "registered"
    assert form.tier.currentData() == "general_external"
    assert form.max_label.currentData() == "PUBLIC"


def test_restore_without_listed_boundary_sends_fixed_defaults(form, picker, registration):
    form.start_restore("registered", None, None)
    folders = local(form)
    picker(folders.root, Path.cwd() / "Shared")
    grade(folders.root, "PUBLIC")
    form.prepare_button.click()
    req, kw = registration[-1]
    assert (req.server_id, req.tier, req.max_label) == ("registered", "internal", "CONFIDENTIAL")
    form._registration_result(
        RegistrationView("REG_RESTORE_BOUNDARY_DIFFERS", command_id=kw["command_id"])
    )
    assert (
        form.status.text()
        == "등록된 신뢰 경계·최대 수신 등급과 다름. 서버 목록에 보이는 값으로 고르세요."
    )


def test_recheck_keeps_registered_id_when_switching_and_updating_known_ids(form, registration):
    form.server_id.setText("registered")
    form._recheck_registration()
    local(form)
    form.set_known_ids({"registered", "local-files"})
    assert form.server_id.text() == "registered"
    assert form._recheck_id == "registered"
    form._recheck_registration()
    assert registration[-1][0].server_id == "registered"


def test_hidden_id_error_appears_in_folder_area(form, monkeypatch):
    local(form)
    monkeypatch.setattr(server_add, "valid_server_id", lambda _value: False)
    form._prepare_local_draft()
    assert "서버 ID 형식" in form.file_folders.error.text()
    assert form.file_folders.error.isVisibleTo(form)
    assert not form._errors["server_id"].isVisibleTo(form)
    assert form.validated_draft is None


def test_recheck_name_suggested_id_stays_fixed_in_local_mode(form, registration):
    form.display_name.setText("Registered")
    form.display_name.textEdited.emit("Registered")
    assert form._id_from_name
    rechecked = form.server_id.text()
    form._recheck_registration()
    local(form)
    form.display_name.setText("Other Name")
    form.display_name.textEdited.emit("Other Name")
    assert form.server_id.text() == form._recheck_id == rechecked
