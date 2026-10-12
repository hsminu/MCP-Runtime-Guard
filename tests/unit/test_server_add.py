"""Local A31 form checks; no Registry, process, network, or C validation."""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from guard_app.ui.server_add import LocalDraftPreview, ServerAddForm


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


@pytest.fixture
def form(app: QApplication) -> ServerAddForm:
    widget = ServerAddForm()
    widget.resize(800, 800)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def _valid_stdio(form: ServerAddForm) -> str:
    command = str(Path.cwd() / "private-executable")
    form.server_id.setText("local_docs")
    form.display_name.setText("Local Docs")
    form.command.setText(command)
    form.args_json.setPlainText('["--secret=unshown-value", "serve"]')
    form.env_allow.setPlainText("LANG\nPATH")
    form.tier.setCurrentIndex(form.tier.findData("internal"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    return command


def test_trust_boundary_and_max_label_require_explicit_choices(
    form: ServerAddForm,
) -> None:
    command = str(Path.cwd() / "private-executable")
    form.server_id.setText("local_docs")
    form.display_name.setText("Local Docs")
    form.command.setText(command)
    assert not form.prepare_button.isEnabled()
    assert "신뢰 경계를 선택" in form._errors["tier"].text()
    assert "최대 수신 등급을 선택" in form._errors["max_label"].text()

    form.tier.setCurrentIndex(form.tier.findData("internal"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    assert form.prepare_button.isEnabled()


def test_transport_switch_preserves_inputs_but_preview_has_only_active_fields(
    form: ServerAddForm,
) -> None:
    command = _valid_stdio(form)
    stdio_preview = form.preview.toPlainText()
    assert '"command"' in stdio_preview
    assert '"args"' in stdio_preview
    assert '"env_allow"' in stdio_preview
    assert '"url"' not in stdio_preview
    assert command not in stdio_preview
    assert "unshown-value" not in stdio_preview
    assert form.prepare_button.isEnabled()

    form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
    form.url.setText("https://example.test/private-route/mcp")
    http_preview = form.preview.toPlainText()
    assert '"url"' in http_preview
    assert '"command"' not in http_preview
    assert '"args"' not in http_preview
    assert '"env_allow"' not in http_preview
    assert "private-route" not in http_preview
    form.prepare_button.click()
    draft = form.validated_draft
    assert draft is not None
    assert draft.transport == "streamable_http"
    assert draft.url == "https://example.test/private-route/mcp"
    assert draft.command is None and draft.args is None and draft.env_allow is None

    form.transport.setCurrentIndex(form.transport.findData("stdio"))
    assert form.validated_draft is None
    assert form.command.text() == command
    assert form.args_json.toPlainText() == '["--secret=unshown-value", "serve"]'
    assert form.env_allow.toPlainText() == "LANG\nPATH"
    assert '"url"' not in form.preview.toPlainText()
    form.prepare_button.click()
    restored = form.validated_draft
    assert restored is not None
    assert restored.command == command
    assert restored.args == ("--secret=unshown-value", "serve")
    assert restored.env_allow == ("LANG", "PATH")
    assert restored.url is None


@pytest.mark.parametrize(
    "args_text, message",
    [
        ('["ok",', "JSON 문자열 배열"),
        ('["ok", 17]', "문자열만 담은 JSON 배열"),
        ('{"arg": "ok"}', "문자열만 담은 JSON 배열"),
    ],
)
def test_invalid_json_and_non_string_args_block_local_preparation(
    form: ServerAddForm, args_text: str, message: str
) -> None:
    _valid_stdio(form)
    form.args_json.setPlainText(args_text)
    assert not form.prepare_button.isEnabled()
    assert form.validated_draft is None
    assert message in form._errors["args"].text()
    assert "unshown-value" not in form.preview.toPlainText()


def test_relative_executable_path_is_a_visible_local_error(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.command.setText("python")
    assert not form.prepare_button.isEnabled()
    assert "절대 경로" in form._errors["command"].text()
    assert form.validated_draft is None


def test_environment_names_reject_values_and_duplicates(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.env_allow.setPlainText("LANG=ko_KR")
    assert not form.prepare_button.isEnabled()
    assert "이름만" in form._errors["env_allow"].text()
    form.env_allow.setPlainText("LANG\nLANG")
    assert not form.prepare_button.isEnabled()
    assert "두 번" in form._errors["env_allow"].text()


@pytest.mark.parametrize(
    "url",
    [
        "http://",
        "https://user:password@example.test/mcp",
        "https://@example.test/mcp",
        "https://example.test:invalid/mcp",
        "https://example.test/mcp?token=hidden",
        "https://example.test/mcp#fragment",
    ],
)
def test_malformed_or_credential_bearing_url_is_rejected_locally(
    form: ServerAddForm, url: str
) -> None:
    form.server_id.setText("remote")
    form.display_name.setText("Remote")
    form.tier.setCurrentIndex(form.tier.findData("general_external"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
    form.url.setText(url)
    assert not form.prepare_button.isEnabled()
    assert form._errors["url"].text()
    assert form.validated_draft is None


def test_external_tier_clamps_confidential_and_transport_does_not_set_tier(
    form: ServerAddForm,
) -> None:
    _valid_stdio(form)
    form.max_label.setCurrentIndex(form.max_label.findData("CONFIDENTIAL"))
    assert form.max_label.currentData() == "CONFIDENTIAL"
    form.tier.setCurrentIndex(form.tier.findData("general_external"))
    assert form.max_label.currentData() == "INTERNAL"
    assert "CONFIDENTIAL 선택을 INTERNAL로 조정" in form.boundary_notice.text()
    form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
    assert form.tier.currentData() == "general_external"
    assert form.max_label.currentData() == "INTERNAL"


def test_duplicate_local_click_emits_once_without_claiming_registration(
    form: ServerAddForm,
) -> None:
    _valid_stdio(form)
    previews: list[LocalDraftPreview] = []
    form.localDraftPrepared.connect(previews.append)
    form.prepare_button.click()
    first_draft = form.validated_draft
    form.prepare_button.click()
    assert len(previews) == 1
    assert isinstance(previews[0], LocalDraftPreview)
    assert "unshown-value" not in previews[0].text
    assert form.validated_draft is first_draft
    assert not form.prepare_button.isEnabled()
    assert "Registry에 등록되지 않았어" in form.status.text()

    with pytest.raises(FrozenInstanceError):
        previews[0].text = "changed"  # type: ignore[misc]
    form.display_name.setText("Renamed")
    assert form.validated_draft is None
    assert form.prepare_button.isEnabled()
    form.prepare_button.click()
    assert len(previews) == 2


def test_known_id_is_only_a_local_duplicate_hint(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.set_known_ids({"local_docs"})
    assert "같은 ID" in form._errors["server_id"].text()
    assert not form.prepare_button.isEnabled()
    form.set_known_ids(set())
    assert form.prepare_button.isEnabled()


def test_narrow_form_keeps_all_inputs_reachable_by_scrolling(
    form: ServerAddForm, app: QApplication
) -> None:
    _valid_stdio(form)
    form.resize(780, 580)
    app.processEvents()
    scroll = form.findChild(QScrollArea, "serverAddScroll")
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0
    form.prepare_button.setFocus()
    app.processEvents()
    assert form.prepare_button.hasFocus()
