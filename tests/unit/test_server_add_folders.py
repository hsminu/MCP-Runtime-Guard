"""로컬 파일 서버 폴더 등급 초안: 형식 확인만, Registry·파일 접근 없음."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.ui.server_add import (
    FolderLabelRule,
    LocalDraftPreview,
    LocalFolderDraft,
    LocalFolderRulesForm,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


@pytest.fixture
def form(app: QApplication) -> LocalFolderRulesForm:
    widget = LocalFolderRulesForm()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def _set_rule(form: LocalFolderRulesForm, index: int, prefix: str, label: str) -> None:
    while len(form.rows) <= index:
        form.add_rule()
    row = form.rows[index]
    row.prefix.setText(prefix)
    row.label.setCurrentIndex(row.label.findData(label))


def test_root_and_labelled_rules_are_required(form: LocalFolderRulesForm) -> None:
    assert not form.prepare_button.isEnabled()
    assert "절대 경로" in form.root_error.text()
    form.root.setText(str(Path.cwd()))
    assert not form.prepare_button.isEnabled()
    assert "등급을 선택" in form.rows[0].error.text()
    _set_rule(form, 0, "", "PUBLIC")
    assert form.prepare_button.isEnabled()


def test_prepared_draft_keeps_segments_and_preview_hides_the_root(
    form: LocalFolderRulesForm,
) -> None:
    root = str(Path.cwd() / "private-root-marker")
    form.root.setText(root)
    _set_rule(form, 0, "", "PUBLIC")
    _set_rule(form, 1, "personal/notes", "INTERNAL")
    _set_rule(form, 2, "private", "CONFIDENTIAL")
    _set_rule(form, 3, "private/public", "PUBLIC")  # 겹침 허용: 판정은 B가 최댓값으로 한다.
    previews: list[object] = []
    form.folder_draft_prepared.connect(previews.append)
    form.prepare_button.click()

    assert form.validated_draft == LocalFolderDraft(
        root,
        (
            FolderLabelRule((), "PUBLIC"),
            FolderLabelRule(("personal", "notes"), "INTERNAL"),
            FolderLabelRule(("private",), "CONFIDENTIAL"),
            FolderLabelRule(("private", "public"), "PUBLIC"),
        ),
    )
    assert not form.prepare_button.isEnabled()
    assert "아직 등록되지 않음" in form.status.text()
    assert len(previews) == 1
    preview = previews[0]
    assert isinstance(preview, LocalDraftPreview)
    assert "private-root-marker" not in preview.text
    shown = json.loads(preview.text)
    assert shown["rules"][0] == {"folder": "(루트 전체)", "label": "PUBLIC"}
    assert shown["rules"][1] == {"folder": "personal/notes", "label": "INTERNAL"}


def test_editing_after_prepare_drops_the_draft(form: LocalFolderRulesForm) -> None:
    form.root.setText(str(Path.cwd()))
    _set_rule(form, 0, "docs", "INTERNAL")
    form.prepare_button.click()
    assert form.validated_draft is not None
    form.rows[0].prefix.setText("docs2")
    assert form.validated_draft is None
    assert form.prepare_button.isEnabled()


@pytest.mark.parametrize(
    ("root", "message"),
    [
        ("relative\\folder", "절대 경로"),
        ("\\\\server\\share", "UNC"),
        ("//server/share", "UNC"),
        (str(Path.cwd()) + "‮", "보이지 않는"),
    ],
)
def test_unusable_roots_are_rejected(form: LocalFolderRulesForm, root: str, message: str) -> None:
    _set_rule(form, 0, "", "PUBLIC")
    form.root.setText(root)
    assert message in form.root_error.text()
    assert not form.prepare_button.isEnabled()


@pytest.mark.parametrize(
    ("prefix", "message"),
    [
        ("personal\\notes", "역슬래시"),
        ("/personal", "상대 경로"),
        ("personal/", "상대 경로"),
        ("a//b", "상대 경로"),
        ("..", "상대 경로"),
        ("a/./b", "상대 경로"),
        ("per​sonal", "보이지 않는"),
    ],
)
def test_unusable_folder_prefixes_are_rejected(
    form: LocalFolderRulesForm, prefix: str, message: str
) -> None:
    form.root.setText(str(Path.cwd()))
    _set_rule(form, 0, prefix, "INTERNAL")
    assert message in form.rows[0].error.text()
    assert not form.prepare_button.isEnabled()


def test_duplicate_folder_rules_are_rejected(form: LocalFolderRulesForm) -> None:
    form.root.setText(str(Path.cwd()))
    _set_rule(form, 0, "personal", "INTERNAL")
    _set_rule(form, 1, " personal ", "PUBLIC")
    assert "두 번" in form.rows[1].error.text()
    assert not form.prepare_button.isEnabled()


def test_folder_rules_differing_only_in_case_are_duplicates(form: LocalFolderRulesForm) -> None:
    form.root.setText(str(Path.cwd()))
    _set_rule(form, 0, "Docs/Private", "CONFIDENTIAL")
    _set_rule(form, 1, "docs/private", "PUBLIC")
    assert "대소문자" in form.rows[1].error.text()
    assert not form.prepare_button.isEnabled()


def test_removing_every_rule_blocks_the_draft(form: LocalFolderRulesForm) -> None:
    form.root.setText(str(Path.cwd()))
    _set_rule(form, 0, "", "PUBLIC")
    form.rows[0].remove_button.click()
    assert form.rows == []
    assert "하나 이상" in form.rules_error.text()
    assert not form.prepare_button.isEnabled()
