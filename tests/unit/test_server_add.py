"""Local A31 form checks; no Registry, process, network, or C validation."""

from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDir
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from guard_app.services import catalog_sources
from guard_app.ui import server_add
from guard_app.ui.server_add import (
    LocalDraftPreview,
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


@pytest.mark.parametrize(
    "args_text, message",
    [
        ("[" * 10_000 + "]" * 10_000, "JSON 문자열 배열"),
        (" " * 65_537, "65536자"),
        (json.dumps(["ok"] * 257), "256개"),
    ],
    ids=("deep-json", "too-large", "too-many"),
)
def test_bounded_args_failure_clears_prepared_draft_and_preview(
    form: ServerAddForm, args_text: str, message: str
) -> None:
    _valid_stdio(form)
    form.prepare_button.click()
    assert form.validated_draft is not None
    assert "2개" in form.preview.toPlainText()

    form.args_json.setPlainText(args_text)

    assert form.validated_draft is None
    assert not form.prepare_button.isEnabled()
    assert message in form._errors["args"].text()
    assert "2개" not in form.preview.toPlainText()
    assert "입력 오류" in form.preview.toPlainText()


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
@pytest.mark.parametrize(
    ("command", "message"),
    [
        (r"\\attacker.example\share\payload.exe", "UNC"),
        (r"\\?\UNC\attacker.example\share\x.exe", "UNC"),
        ("//attacker.example/share/x.exe", "UNC"),
        ("C:/mcp/py\u202eexe.thon", "보이지 않는 문자"),
        ("C:/mcp/py\x00thon.exe", "보이지 않는 문자"),
        ("C:/mcp/py\u200bthon.exe", "보이지 않는 문자"),
    ],
)
def test_remote_or_hidden_character_executable_is_rejected(
    form: ServerAddForm, command: str, message: str
) -> None:
    _valid_stdio(form)
    assert form.prepare_button.isEnabled()
    form.command.setText(command)
    assert message in form._errors["command"].text()
    assert not form.prepare_button.isEnabled()
    form.prepare_button.click()
    assert form.validated_draft is None


@pytest.mark.parametrize(
    ("server_id", "message"),
    [
        ("docs\u202e", "보이지 않는 문자"),
        ("do\u200bcs", "보이지 않는 문자"),
        ("do\x00cs", "보이지 않는 문자"),
        ("docs\u3164", "보이지 않는 문자"),
        ("DOCS", "대소문자"),
        ("\uff44\uff4f\uff43\uff53", "대소문자"),
        ("a" * 129, "128자 이하"),
    ],
)
def test_hidden_long_or_case_folded_duplicate_server_id_is_rejected(
    form: ServerAddForm, server_id: str, message: str
) -> None:
    _valid_stdio(form)
    form.set_known_ids({"docs"})
    form.server_id.setText("docs-2")
    assert form.prepare_button.isEnabled()
    form.server_id.setText(server_id)
    assert message in form._errors["server_id"].text()
    assert not form.prepare_button.isEnabled()


def test_truncated_paste_never_prepares_a_shorter_id(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.server_id.setText("a" * 40_000)
    assert len(form.server_id.text()) == form.server_id.maxLength()
    assert "128자 이하" in form._errors["server_id"].text()
    assert not form.prepare_button.isEnabled()


def test_hidden_characters_in_display_name_are_rejected(form: ServerAddForm) -> None:
    _valid_stdio(form)
    form.display_name.setText("Local\u202eDocs")
    assert form._errors["display_name"].text()
    assert not form.prepare_button.isEnabled()


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://good.example\\.evil.test/mcp", "역슬래시"),
        ("http://127.0.0.1:0/mcp", "1–65535"),
        ("https://\u0435\u0445.example/mcp", "ASCII"),
        ("http://169.254.169.254/latest", "메타데이터"),
        ("http://[fe80::1]/mcp", "링크 로컬"),
        ("http://[::ffff:169.254.169.254]/mcp", "링크 로컬"),
        ("http://0.0.0.0:8080/mcp", "미지정"),
        ("http://2852039166/mcp", "네 개의 십진수"),
        ("http://0xa9.254.169.254/mcp", "네 개의 십진수"),
        ("https://x.test/\u202emcp", "보이지 않는 문자"),
        ("https://x.test/m\u200bcp", "보이지 않는 문자"),
    ],
)
def test_ambiguous_or_metadata_url_is_rejected_locally(
    form: ServerAddForm, url: str, message: str
) -> None:
    form.server_id.setText("remote")
    form.display_name.setText("Remote")
    form.tier.setCurrentIndex(form.tier.findData("general_external"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
    form.url.setText("https://mcp.example.test/mcp")
    assert form.prepare_button.isEnabled()
    form.url.setText(url)
    assert message in form._errors["url"].text()
    assert not form.prepare_button.isEnabled()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/mcp",
        "http://[::1]:8080/mcp",
        "https://xn--h32b13v.example/mcp",
        "https://mcp.example.test:443/분기",
    ],
)
def test_ordinary_local_and_punycode_urls_remain_accepted(form: ServerAddForm, url: str) -> None:
    form.server_id.setText("remote")
    form.display_name.setText("Remote")
    form.tier.setCurrentIndex(form.tier.findData("internal"))
    form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
    form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
    form.url.setText(url)
    assert not form._errors["url"].text()
    assert form.prepare_button.isEnabled()


# A54 / I7 / T32 support: UI is a syntax hint; Registry enforces registration.
@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
@pytest.mark.parametrize("server_id", ["__", "docs__read", "docs_", "_", " docs_ "])
def test_namespace_server_id_error_is_immediate_and_never_prepares_a_draft(
    form: ServerAddForm, transport: str, server_id: str
) -> None:
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


@pytest.mark.parametrize("server_id", ["docs", "local_docs", "_docs", "서버 이름", "a" * 128])
def test_b_valid_server_ids_remain_accepted_after_existing_ui_checks(
    form: ServerAddForm, server_id: str
) -> None:
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
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
    ],
    ids=("codex-toml", "json-url-with-headers", "bare-url", "command-line"),
)
def test_paste_reader_understands_common_forms(text: str, expected: PastedServer) -> None:
    assert read_pasted_server(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "없음"),
        ('{"mcpServers": {"docs": {', "JSON 형식"),
        ('{"name": "docs"}', "찾지 못함"),
        ('{"command": "C:\\\\x.exe", "args": "serve"}', "문자열 배열"),
        ('C:\\tools\\node.exe "unclosed', "따옴표"),
    ],
    ids=("empty", "broken-json", "no-entry", "args-not-array", "open-quote"),
)
def test_unreadable_paste_stays_in_the_box_with_a_fixed_reason(
    form: ServerAddForm, text: str, message: str
) -> None:
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


@pytest.mark.parametrize(
    ("name", "message"),
    [("npx.cmd", "배치 파일"), ("run.bat", "배치 파일"), ("server.py", ".exe")],
)
def test_only_exe_files_pass_like_the_launcher(
    form: ServerAddForm, name: str, message: str
) -> None:
    _valid_stdio(form)
    form.command.setText(str(Path.cwd() / name))
    assert message in form._errors["command"].text()
    assert not form.prepare_button.isEnabled()


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
