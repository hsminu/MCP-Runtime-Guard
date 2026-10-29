"""Local A31 form checks; no Registry, process, network, or C validation."""

from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDir, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from guard_app.services import catalog_sources
from guard_app.ui import server_add
from guard_app.ui.models import LoadState, Surface
from guard_app.ui.scan_status import ScanStatusCard, ScanStatusProjection
from guard_app.ui.server_add import (
    FolderLabelRule,
    LocalDraftPreview,
    LocalFolderDraft,
    LocalFolderRulesForm,
    PastedServer,
    PasteError,
    ServerAddForm,
    read_pasted_server,
    suggested_server_id,
)
from guard_app.ui.window import GuardWindow


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
    command = str(Path.cwd() / "private-executable.exe")
    form.server_id.setText("local_docs")
    form.display_name.setText("Local Docs")
    form.command.setText(command)
    form.args_json.setPlainText('["--secret=unshown-value", "serve"]')
    form.env_allow.setPlainText("LANG\nPATH")
    form.tier.setCurrentIndex(form.tier.findData("internal"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    return command


def _valid_http(form: ServerAddForm, tier: str = "general_external") -> None:
    form.server_id.setText("remote")
    form.display_name.setText("Remote")
    form.tier.setCurrentIndex(form.tier.findData(tier))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    form.transport.setCurrentIndex(form.transport.findData("streamable_http"))


def test_trust_boundary_and_max_label_require_explicit_choices(
    form: ServerAddForm,
) -> None:
    command = str(Path.cwd() / "private-executable.exe")
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


def test_invalid_fields_clear_draft_and_reject_preparation(form: ServerAddForm, subtests) -> None:
    # A31 / A54 / I7 / T32; QA R12 and EXE-01.
    for field, widget_name, setter, value, message in (
        ("args", "args_json", "setPlainText", '["ok",', "JSON 문자열 배열"),
        ("args", "args_json", "setPlainText", '["ok", 17]', "문자열만 담은 JSON 배열"),
        ("args", "args_json", "setPlainText", '{"arg": "ok"}', "문자열만 담은 JSON 배열"),
        ("args", "args_json", "setPlainText", "[" * 10000 + "]" * 10000, "JSON 문자열 배열"),
        ("args", "args_json", "setPlainText", " " * 65537, "65536자"),
        ("args", "args_json", "setPlainText", json.dumps(["ok"] * 257), "256개"),
        ("command", "command", "setText", "\\\\attacker.example\\share\\payload.exe", "UNC"),
        ("command", "command", "setText", "\\\\?\\UNC\\attacker.example\\share\\x.exe", "UNC"),
        ("command", "command", "setText", "//attacker.example/share/x.exe", "UNC"),
        ("command", "command", "setText", "C:/mcp/py\u202eexe.thon", "보이지 않는 문자"),
        ("command", "command", "setText", "C:/mcp/py\x00thon.exe", "보이지 않는 문자"),
        ("command", "command", "setText", "C:/mcp/py\u200bthon.exe", "보이지 않는 문자"),
        ("server_id", "server_id", "setText", "docs\u202e", "보이지 않는 문자"),
        ("server_id", "server_id", "setText", "do\u200bcs", "보이지 않는 문자"),
        ("server_id", "server_id", "setText", "do\x00cs", "보이지 않는 문자"),
        ("server_id", "server_id", "setText", "docsㅤ", "보이지 않는 문자"),
        ("server_id", "server_id", "setText", "DOCS", "대소문자"),
        ("server_id", "server_id", "setText", "ｄｏｃｓ", "대소문자"),
        ("server_id", "server_id", "setText", "a" * 129, "128자 이하"),
        ("command", "command", "setText", str(Path.cwd() / "npx.cmd"), "배치 파일"),
        ("command", "command", "setText", str(Path.cwd() / "run.bat"), "배치 파일"),
        ("command", "command", "setText", str(Path.cwd() / "server.py"), ".exe"),
        ("command", "command", "setText", "python", "절대 경로"),
        ("server_id", "server_id", "setText", "a" * 40_000, "128자 이하"),
        ("display_name", "display_name", "setText", "Local\u202eDocs", None),
    ):
        for prepared in (False, True):
            with subtests.test(field=field, value=value, prepared=prepared):
                _valid_stdio(form)
                form.set_known_ids({"docs"})
                form.server_id.setText("docs-2")
                assert form.prepare_button.isEnabled()
                if prepared:
                    form.prepare_button.click()
                    assert form.validated_draft is not None
                    assert "2개" in form.preview.toPlainText()
                widget = getattr(form, widget_name)
                getattr(widget, setter)(value)
                error = form._errors[field].text()
                assert error if message is None else message in error
                if field == "server_id" and len(value) == 40_000:
                    assert len(form.server_id.text()) == form.server_id.maxLength()
                assert not form.prepare_button.isEnabled()
                assert form.validated_draft is None
                assert "unshown-value" not in form.preview.toPlainText()
                assert "2개" not in form.preview.toPlainText()
                assert "입력 오류" in form.preview.toPlainText()
                form.prepare_button.click()
                assert form.validated_draft is None


def test_environment_names_reject_values_and_duplicates(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.env_allow.setPlainText("LANG=ko_KR")
    assert not form.prepare_button.isEnabled()
    assert "이름만" in form._errors["env_allow"].text()
    form.env_allow.setPlainText("LANG\nLANG")
    assert not form.prepare_button.isEnabled()
    assert "두 번" in form._errors["env_allow"].text()


def test_malformed_or_credential_bearing_url_is_rejected_locally(
    form: ServerAddForm, subtests
) -> None:
    for url in [
        "http://",
        "https://user:password@example.test/mcp",
        "https://@example.test/mcp",
        "https://example.test:invalid/mcp",
        "https://example.test/mcp?token=hidden",
        "https://example.test/mcp#fragment",
    ]:
        with subtests.test(url=url):
            _valid_http(form)
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
    form.local_draft_prepared.connect(previews.append)
    form.prepare_button.click()
    first_draft = form.validated_draft
    form.prepare_button.click()
    assert len(previews) == 1
    assert isinstance(previews[0], LocalDraftPreview)
    assert "unshown-value" not in previews[0].text
    assert form.validated_draft is first_draft
    assert not form.prepare_button.isEnabled()
    assert "Registry에 등록되지 않음" in form.status.text()

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


# QA 2026-10-13 R12: each of these prepared a draft with no visible error.


def test_ambiguous_or_metadata_url_is_rejected_locally(form: ServerAddForm, subtests) -> None:
    for url, message in [
        ("http://good.example\\.evil.test/mcp", "역슬래시"),
        ("http://127.0.0.1:0/mcp", "1–65535"),
        ("http://169.254.169.254/latest", "메타데이터"),
        ("http://[fe80::1]/mcp", "링크 로컬"),
        ("http://[::ffff:169.254.169.254]/mcp", "링크 로컬"),
        ("http://0.0.0.0:8080/mcp", "미지정"),
        ("http://2852039166/mcp", "네 개의 십진수"),
        ("http://0xa9.254.169.254/mcp", "네 개의 십진수"),
        ("https://x.test/\u202emcp", "경로"),
        ("https://x.test/m\u200bcp", "경로"),
    ]:
        with subtests.test(url=url, message=message):
            _valid_http(form)
            form.url.setText("https://mcp.example.test/mcp")
            assert form.prepare_button.isEnabled()
            form.url.setText(url)
            assert message in form._errors["url"].text()
            assert not form.prepare_button.isEnabled()


def test_ordinary_local_and_punycode_urls_remain_accepted(form: ServerAddForm, subtests) -> None:
    for url in [
        "http://127.0.0.1:8080/mcp",
        "http://[::1]:8080/mcp",
        "https://xn--h32b13v.example/mcp",
        "https://mcp.example.test:443/mcp",
        "https://faß.de/mcp",
    ]:
        with subtests.test(url=url):
            _valid_http(form, "internal")
            form.url.setText(url)
            assert not form._errors["url"].text()
            assert form.prepare_button.isEnabled()


# A54 / I7 / T32 support: UI is a syntax hint; Registry enforces registration.
def test_namespace_server_id_error_is_immediate_and_never_prepares_a_draft(
    form: ServerAddForm, subtests
) -> None:
    for transport in ["stdio", "streamable_http"]:
        for server_id in ["__", "docs__read", "docs_", "_", " docs_ "]:
            with subtests.test(transport=transport, server_id=server_id):
                _valid_stdio(form)
                form.transport.setCurrentIndex(form.transport.findData(transport))
                form.url.setText("https://mcp.example.test/mcp")
                previews: list[LocalDraftPreview] = []
                form.local_draft_prepared.connect(previews.append)
                assert form.prepare_button.isEnabled()

                form.server_id.setText(server_id)

                assert not catalog_sources.valid_server_id(server_id.strip())
                assert "연속 밑줄(__)과 끝의 밑줄(_)" in form._errors["server_id"].text()
                assert not form._errors["server_id"].isHidden()
                assert form._errors["server_id"].property("validationError") is True
                assert not form.prepare_button.isEnabled()
                form.prepare_button.click()
                form._prepare_local_draft()  # The submit handler rechecks even if invoked directly.
                assert form.validated_draft is None
                assert previews == []


def test_b_valid_server_ids_remain_accepted_after_existing_ui_checks(
    form: ServerAddForm, subtests
) -> None:
    for server_id in ["docs", "local_docs", "_docs", "서버 이름", "a" * 128]:
        with subtests.test(server_id=server_id):
            _valid_stdio(form)
            assert catalog_sources.valid_server_id(server_id)
            form.server_id.setText(f"  {server_id}  ")
            assert form._errors["server_id"].text() == ""
            assert form.prepare_button.isEnabled()
            form.prepare_button.click()
            assert form.validated_draft is not None
            assert form.validated_draft.server_id == server_id
            assert "Registry에 등록되지 않음" in form.status.text()


def test_editing_server_id_clears_prepared_draft_and_updates_errors_immediately(
    form: ServerAddForm,
) -> None:
    _valid_stdio(form)
    form.prepare_button.click()
    assert form.validated_draft is not None

    form.server_id.setText("docs__read")
    assert form.validated_draft is None
    assert "연속 밑줄" in form._errors["server_id"].text()
    assert "입력 오류" in form.preview.toPlainText()

    form.server_id.setText("")
    assert form._errors["server_id"].text() == "서버 ID를 입력하세요."
    form.server_id.setText("docs_read")
    assert form._errors["server_id"].text() == ""
    assert form._errors["server_id"].isHidden()
    assert form._errors["server_id"].property("validationError") is False
    assert form.prepare_button.isEnabled()
    assert form.validated_draft is None


def test_form_uses_same_b_result_through_bridge_with_trimmed_input(
    form: ServerAddForm, monkeypatch: pytest.MonkeyPatch
) -> None:
    _valid_stdio(form)
    check = catalog_sources.valid_server_id
    calls: list[object] = []

    def recording_check(server_id: object) -> bool:
        calls.append(server_id)
        return check(server_id)

    monkeypatch.setattr(catalog_sources, "valid_server_id", recording_check)
    form.server_id.setText("  docs_  ")
    assert calls == ["docs_"]
    assert not form.prepare_button.isEnabled()

    form.server_id.setText("  docs_read  ")
    assert calls[-1] == "docs_read"
    assert form.prepare_button.isEnabled()


def test_product_window_uses_bridge_validation_without_backend_startup(
    app: QApplication,
) -> None:
    window = GuardWindow()
    try:
        window.navigate("server_add")
        form = window.server_add_form
        _valid_stdio(form)
        form.server_id.setText("docs__read")
        assert "연속 밑줄" in form._errors["server_id"].text()
        assert not form.prepare_button.isEnabled()
        form.server_id.setText("docs_read")
        assert form._errors["server_id"].text() == ""
        assert form.prepare_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


# 2026-10-28: paste a config entry instead of typing every field.


def _choose_boundary(form: ServerAddForm) -> None:
    form.tier.setCurrentIndex(form.tier.findData("internal"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))


def test_pasted_config_fills_the_fields_and_keeps_no_values(form: ServerAddForm) -> None:
    command = str(Path.cwd() / "docs-server.exe")
    form.quick_input.setPlainText(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "command": command,
                        "args": ["serve", "--root", "C:\\My Docs"],
                        "env": {"DOCS_TOKEN": "pasted-secret-value", "LANG": "ko_KR"},
                    },
                    "mail": {"url": "https://mail.example.test/mcp"},
                }
            }
        )
    )
    form.fill_button.click()

    assert form.quick_input.toPlainText() == ""
    assert form.server_id.text() == "docs"
    assert form.transport.currentData() == "stdio"
    assert form.command.text() == command
    assert form.args_json.toPlainText() == 'serve --root "C:\\My Docs"'
    assert form.env_allow.toPlainText() == "DOCS_TOKEN\nLANG"
    assert "2개는 가져오지 않음" in form.paste_status.text()
    assert "나머지 1개" in form.paste_status.text()
    texts = [form.preview.toPlainText(), form.paste_status.text(), form.env_allow.toPlainText()]
    assert not any("pasted-secret-value" in text or "ko_KR" in text for text in texts)
    _choose_boundary(form)
    form.prepare_button.click()
    draft = form.validated_draft
    assert draft is not None
    assert draft.args == ("serve", "--root", "C:\\My Docs")
    assert draft.env_allow == ("DOCS_TOKEN", "LANG")
    assert draft.display_name == "docs"


def test_paste_reader_understands_common_forms(subtests) -> None:
    for text, expected in [
        (
            '[mcp_servers.docs]\ncommand = "C:\\\\tools\\\\docs.exe"\nargs = ["serve"]\n'
            'env = { DOCS_TOKEN = "secret" }\n',
            PastedServer(
                "docs",
                "stdio",
                command="C:\\tools\\docs.exe",
                args=("serve",),
                env_names=("DOCS_TOKEN",),
                dropped_values=1,
            ),
        ),
        (
            '{"url": "https://mail.example.test/mcp", "headers": {"Authorization": "Bearer x"}}',
            PastedServer(
                "", "streamable_http", url="https://mail.example.test/mcp", dropped_values=1
            ),
        ),
        (
            "https://mail.example.test/mcp",
            PastedServer("", "streamable_http", url="https://mail.example.test/mcp"),
        ),
        (
            'C:\\tools\\node.exe "C:\\My Server\\index.js" --port 3',
            PastedServer(
                "",
                "stdio",
                command="C:\\tools\\node.exe",
                args=("C:\\My Server\\index.js", "--port", "3"),
            ),
        ),
    ]:
        with subtests.test(text=text, expected=expected):
            assert read_pasted_server(text) == expected


def test_unreadable_paste_stays_in_the_box_with_a_fixed_reason(
    form: ServerAddForm, subtests
) -> None:
    for text, message in [
        ("", "없음"),
        ('{"mcpServers": {"docs": {', "JSON 형식"),
        ('{"name": "docs"}', "찾지 못함"),
        ('{"command": "C:\\\\x.exe", "args": "serve"}', "문자열 배열"),
        ('C:\\tools\\node.exe "unclosed', "따옴표"),
    ]:
        with subtests.test(text=text, message=message):
            form.quick_input.setPlainText(text)
            form.fill_button.click()
            assert message in form.paste_status.text()
            assert form.quick_input.toPlainText() == text
            assert form.server_id.text() == ""
            with pytest.raises(PasteError):
                read_pasted_server(text)


def test_arguments_read_like_a_command_line(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.args_json.setPlainText('serve --root "C:\\My Docs" ""')
    form.prepare_button.click()
    draft = form.validated_draft
    assert draft is not None
    assert draft.args == ("serve", "--root", "C:\\My Docs", "")
    form.args_json.setPlainText('serve "C:\\My Docs')
    assert '따옴표(")' in form._errors["args"].text()
    assert not form.prepare_button.isEnabled()
    form.env_allow.setPlainText("LANG, PATH")
    form.args_json.setPlainText("")
    form.prepare_button.click()
    draft = form.validated_draft
    assert draft is not None
    assert draft.args == ()
    assert draft.env_allow == ("LANG", "PATH")


def test_display_name_is_optional_and_suggests_an_ascii_id(form: ServerAddForm) -> None:
    QTest.keyClicks(form.display_name, "MCP Docs Server")
    assert form.server_id.text() == "docs-server"
    form.display_name.clear()
    # keyClicks types ASCII only; a Korean name arrives as one edit, like an IME commit.
    form.display_name.setText("사내 문서")
    form.display_name.textEdited.emit("사내 문서")
    assert form.server_id.text() == ""
    QTest.keyClicks(form.server_id, "intranet")
    QTest.keyClicks(form.display_name, " v2")
    assert form.server_id.text() == "intranet"
    assert suggested_server_id("mcp") == ""
    assert suggested_server_id("Mail__Box_") == "mail-box"

    form.display_name.clear()
    form.command.setText(str(Path.cwd() / "intranet.exe"))
    _choose_boundary(form)
    form.prepare_button.click()
    draft = form.validated_draft
    assert draft is not None
    assert draft.display_name == "intranet"


def test_browse_puts_the_chosen_executable_in_the_field(
    form: ServerAddForm, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = str(Path.cwd() / "picked.exe")
    monkeypatch.setattr(server_add.QFileDialog, "getOpenFileName", lambda *_args: (chosen, ""))
    form.browse_button.click()
    assert form.command.text() == QDir.toNativeSeparators(chosen)
    monkeypatch.setattr(server_add.QFileDialog, "getOpenFileName", lambda *_args: ("", ""))
    form.browse_button.click()
    assert form.command.text() == QDir.toNativeSeparators(chosen)


def test_folder_rules_stay_folded_until_opened(app: QApplication) -> None:
    window = GuardWindow()
    try:
        window.navigate("server_add")
        window.show()
        app.processEvents()
        assert not window.folder_rules_form.isVisibleTo(window)
        toggle = next(
            button
            for button in window.server_add_form.findChildren(QPushButton)
            if button.text().startswith("로컬 파일 서버 폴더 등급")
        )
        toggle.click()
        assert window.folder_rules_form.isVisibleTo(window)
        assert toggle.accessibleName().endswith("접기")
        toggle.click()
        assert not window.folder_rules_form.isVisibleTo(window)
    finally:
        window.close()
        app.processEvents()


# Local folder draft: format checks only; no Registry or filesystem access.
@pytest.fixture
def folder_form(app: QApplication) -> LocalFolderRulesForm:
    widget = LocalFolderRulesForm()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def _set_rule(folder_form: LocalFolderRulesForm, index: int, prefix: str, label: str) -> None:
    while len(folder_form.rows) <= index:
        folder_form.add_rule()
    row = folder_form.rows[index]
    row.prefix.setText(prefix)
    row.label.setCurrentIndex(row.label.findData(label))


def test_root_and_labelled_rules_are_required(folder_form: LocalFolderRulesForm) -> None:
    assert not folder_form.prepare_button.isEnabled()
    assert "절대 경로" in folder_form.root_error.text()
    folder_form.root.setText(str(Path.cwd()))
    assert not folder_form.prepare_button.isEnabled()
    assert "등급을 선택" in folder_form.rows[0].error.text()
    _set_rule(folder_form, 0, "", "PUBLIC")
    assert folder_form.prepare_button.isEnabled()


def test_prepared_draft_keeps_segments_and_preview_hides_the_root(
    folder_form: LocalFolderRulesForm,
) -> None:
    root = str(Path.cwd() / "private-root-marker")
    folder_form.root.setText(root)
    _set_rule(folder_form, 0, "", "PUBLIC")
    _set_rule(folder_form, 1, "personal/notes", "INTERNAL")
    _set_rule(folder_form, 2, "private", "CONFIDENTIAL")
    _set_rule(folder_form, 3, "private/public", "PUBLIC")  # 겹침 허용: 판정은 B가 최댓값으로 한다.
    previews: list[object] = []
    folder_form.folder_draft_prepared.connect(previews.append)
    folder_form.prepare_button.click()

    assert folder_form.validated_draft == LocalFolderDraft(
        root,
        (
            FolderLabelRule((), "PUBLIC"),
            FolderLabelRule(("personal", "notes"), "INTERNAL"),
            FolderLabelRule(("private",), "CONFIDENTIAL"),
            FolderLabelRule(("private", "public"), "PUBLIC"),
        ),
    )
    assert not folder_form.prepare_button.isEnabled()
    assert "아직 등록되지 않음" in folder_form.status.text()
    assert len(previews) == 1
    preview = previews[0]
    assert isinstance(preview, LocalDraftPreview)
    assert "private-root-marker" not in preview.text
    shown = json.loads(preview.text)
    assert shown["rules"][0] == {"folder": "(루트 전체)", "label": "PUBLIC"}
    assert shown["rules"][1] == {"folder": "personal/notes", "label": "INTERNAL"}


def test_editing_after_prepare_drops_the_draft(folder_form: LocalFolderRulesForm) -> None:
    folder_form.root.setText(str(Path.cwd()))
    _set_rule(folder_form, 0, "docs", "INTERNAL")
    folder_form.prepare_button.click()
    assert folder_form.validated_draft is not None
    folder_form.rows[0].prefix.setText("docs2")
    assert folder_form.validated_draft is None
    assert folder_form.prepare_button.isEnabled()


def test_unusable_roots_are_rejected(folder_form: LocalFolderRulesForm, subtests) -> None:
    for root, message in [
        ("relative\\folder", "절대 경로"),
        ("\\\\server\\share", "UNC"),
        ("//server/share", "UNC"),
        (str(Path.cwd()) + "‮", "보이지 않는"),
    ]:
        with subtests.test(root=root, message=message):
            _set_rule(folder_form, 0, "", "PUBLIC")
            folder_form.root.setText(root)
            assert message in folder_form.root_error.text()
            assert not folder_form.prepare_button.isEnabled()


def test_unusable_folder_prefixes_are_rejected(folder_form: LocalFolderRulesForm, subtests) -> None:
    for prefix, message in [
        ("personal\\notes", "역슬래시"),
        ("/personal", "상대 경로"),
        ("personal/", "상대 경로"),
        ("a//b", "상대 경로"),
        ("..", "상대 경로"),
        ("a/./b", "상대 경로"),
        ("per​sonal", "보이지 않는"),
    ]:
        with subtests.test(prefix=prefix, message=message):
            folder_form.root.setText(str(Path.cwd()))
            _set_rule(folder_form, 0, prefix, "INTERNAL")
            assert message in folder_form.rows[0].error.text()
            assert not folder_form.prepare_button.isEnabled()


def test_duplicate_folder_rules_are_rejected(folder_form: LocalFolderRulesForm) -> None:
    folder_form.root.setText(str(Path.cwd()))
    _set_rule(folder_form, 0, "personal", "INTERNAL")
    _set_rule(folder_form, 1, " personal ", "PUBLIC")
    assert "두 번" in folder_form.rows[1].error.text()
    assert not folder_form.prepare_button.isEnabled()


def test_folder_rules_differing_only_in_case_are_duplicates(
    folder_form: LocalFolderRulesForm,
) -> None:
    folder_form.root.setText(str(Path.cwd()))
    _set_rule(folder_form, 0, "Docs/Private", "CONFIDENTIAL")
    _set_rule(folder_form, 1, "docs/private", "PUBLIC")
    assert "대소문자" in folder_form.rows[1].error.text()
    assert not folder_form.prepare_button.isEnabled()


def test_removing_every_rule_blocks_the_draft(folder_form: LocalFolderRulesForm) -> None:
    folder_form.root.setText(str(Path.cwd()))
    _set_rule(folder_form, 0, "", "PUBLIC")
    folder_form.rows[0].remove_button.click()
    assert folder_form.rows == []
    assert "하나 이상" in folder_form.rules_error.text()
    assert not folder_form.prepare_button.isEnabled()


# A32: display only; no C Scanner verdict or Registry transition.
def test_scan_card_state_transitions_and_safe_projection(app) -> None:
    card = ScanStatusCard()
    assert "조회 기능 미연결" in card.state_message.message.text()
    assert card.scan_state.text() == "미제공"

    card.set_snapshot(
        Surface(
            LoadState.READY,
            ScanStatusProjection(
                registry_status="active",
                session_status="connected",
                scan_state="completed",
                analyzers_required=("offline",),
            ),
        )
    )
    assert "검사 실행 완료" in card.scan_state.text()
    assert "ACTIVE" in card.registry_status.text()

    card.set_snapshot(Surface(LoadState.ERROR))
    assert "불러오지 못함" in card.state_message.message.text()
    assert card.scan_state.text() == "미제공"
    assert card.registry_status.text() == "미제공"
    assert card.session_status.text() == "미제공"
    assert card.required.text() == "미제공"

    card.set_snapshot(Surface(LoadState.READY))
    assert "조회 결과 미제공" in card.state_message.message.text()
    assert card.scan_state.text() == "미제공"

    # scan_completion_does_not_imply_registration_or_connection
    projection = ScanStatusProjection(
        registry_status="pending",
        session_status="disconnected",
        scan_state="completed",
        analyzers_required=("offline-rules", "schema"),
        analyzers_completed=("offline-rules",),
        analyzers_skipped=("schema",),
        analyzers_failed=("network-check",),
        fingerprint_present=True,
        fingerprint_stale=True,
        scanner_version="fixture-1",
        scanned_at=datetime(2026, 10, 12, 5, 35, tzinfo=UTC),
        max_severity="HIGH",
        safe_finding_summary="높은 위험 1건 · 사용자 검토 필요",
    )
    card.set_snapshot(Surface(LoadState.READY, projection))

    assert card.required.text() == "2개 · offline-rules, schema"
    assert card.completed.text() == "1개 · offline-rules"
    assert card.skipped.text() == "1개 · schema"
    assert card.failed.text() == "1개 · network-check"
    assert card.scan_state.text() == "검사 실행 완료 · 사용자 활성화 별도"
    assert "지문과 불일치" in card.fingerprint.text()
    assert "PENDING" in card.registry_status.text()
    assert "연결 안 됨" in card.session_status.text()
    assert card.scanned_at.text() == "2026년 10월 12일 14:35:00 KST"
    assert "HIGH" in card.max_severity.text()
    assert card.findings.text() == "높은 위험 1건 · 사용자 검토 필요"
    assert "통과" not in card.scan_state.text()
    assert card.findChildren(QPushButton) == []

    # stale_query_does_not_present_fingerprint_as_current
    card.set_snapshot(
        Surface(
            LoadState.STALE,
            ScanStatusProjection(
                registry_status="active",
                session_status="connected",
                scan_state="completed",
                fingerprint_present=True,
                fingerprint_stale=False,
            ),
            as_of=datetime(2026, 10, 12, 5, 35, tzinfo=UTC),
        )
    )
    assert "이전 조회 결과" in card.state_message.message.text()
    assert "재확인 필요" in card.fingerprint.text()
    assert "변경 감지 없음" not in card.fingerprint.text()

    # unknown_states_and_empty_analyzer_sets_remain_explicit
    card.set_snapshot(
        Surface(
            LoadState.READY,
            ScanStatusProjection(
                registry_status="unexpected-secret-value",
                session_status="unknown-session",
                scan_state="unknown-scan-state",
                analyzers_required=(),
                analyzers_completed=None,
                analyzers_skipped=(),
                analyzers_failed=(),
                fingerprint_present=None,
                max_severity="unexpected-severity",
            ),
        )
    )
    assert card.scan_state.text() == "미확인"
    assert card.registry_status.text() == "미확인"
    assert card.session_status.text() == "미확인"
    assert card.max_severity.text() == "미확인"
    assert card.required.text() == "0개 · backend 제공"
    assert card.completed.text() == "미제공"
    assert card.skipped.text() == "0개 · backend 제공"
    assert "미확인" in card.fingerprint.text()

    # finding_summary_is_plain_text_and_projection_is_immutable
    summary = '<img src="https://example.invalid/pixel"> <b>finding</b>'
    projection = ScanStatusProjection(safe_finding_summary=summary)
    card.set_snapshot(Surface(LoadState.READY, projection))
    assert card.findings.text() == summary
    assert card.findings.textFormat() == Qt.TextFormat.PlainText
    with pytest.raises(FrozenInstanceError):
        projection.scan_state = "completed"  # type: ignore[misc]
