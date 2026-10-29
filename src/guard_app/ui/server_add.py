"""MCP server add form: paste a config entry or fill a few fields, checked locally.

This widget performs convenience syntax checks. It neither registers a server
nor checks whether an executable or endpoint is safe. C's Registry and
security modules must validate every field again before creating PENDING.
A pasted entry fills the fields and is cleared at once; environment values
and headers in it are never kept, only environment variable names.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QObject, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from guard_app.services.bridge import (
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
    LifecycleEvent,
    RegistrationRequest,
    RegistrationView,
    endpoint_input,
    new_command_id,
    valid_server_id,
)

from .presentation import LABELS, TIER_NAMES, is_hidden_char
from .server_add_folders import FileServerFolders
from .untrusted_text import untrusted_display_text
from .widgets import FitStack, KeyboardActionButton, ResponsiveRow, SelectionBox, plain_label

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ENV_SEPARATORS = re.compile(r"[\s,]+")
_MAX_ARGS_JSON_CHARS = 65_536
_MAX_ARGS_COUNT = 256
_MAX_SERVER_ID_CHARS = 128
_MAX_DISPLAY_NAME_CHARS = 256
_DUPLICATE_ID = "현재 목록에 같은 ID가 있음. Registry에서도 다시 확인해야 함."
_MAX_PASTE_CHARS = 65_536
# Codex takes server IDs of ASCII letters, digits, _ and -, at most 64 long.
_MAX_SUGGESTED_ID_CHARS = 64
_SLUG_GAP = re.compile(r"[^a-z0-9]+")
_CODEX_TOML_SERVER = re.compile(r"^\s*\[mcp_servers\.", re.MULTILINE)


def _has_hidden_char(value: str) -> bool:
    return any(is_hidden_char(char) for char in value)


def _id_key(value: str) -> str:
    """Compare IDs as an operator would read them: case and width folded."""

    return unicodedata.normalize("NFKC", value).casefold()


def suggested_server_id(name: str) -> str:
    """An ASCII ID from a display name, or "" when the name gives none.

    A leading "mcp" is dropped because Codex refuses server IDs that start
    with it; "MCP Docs" suggests "docs"."""

    slug = _SLUG_GAP.sub("-", unicodedata.normalize("NFKC", name).casefold()).strip("-")
    if slug == "mcp" or slug.startswith("mcp-"):
        slug = slug[3:].lstrip("-")
    slug = slug[:_MAX_SUGGESTED_ID_CHARS].rstrip("-")
    return slug if slug and valid_server_id(slug) else ""


def split_command_line(text: str) -> list[str] | None:
    """Split on blanks; double quotes keep blanks. Backslashes stay literal.

    Windows paths keep their backslashes. None when a quote is left open."""

    tokens: list[str] = []
    current: list[str] = []
    quoted = in_token = False
    for char in text:
        if char == '"':
            quoted = not quoted
            in_token = True
        elif char.isspace() and not quoted:
            if in_token:
                tokens.append("".join(current))
                current = []
                in_token = False
        else:
            current.append(char)
            in_token = True
    if quoted:
        return None
    if in_token:
        tokens.append("".join(current))
    return tokens


def join_command_line(args: tuple[str, ...]) -> str:
    """The inverse of split_command_line; JSON when an argument holds a quote."""

    if any('"' in arg for arg in args):
        return json.dumps(list(args), ensure_ascii=False)
    return " ".join(
        f'"{arg}"' if not arg or any(char.isspace() for char in arg) else arg for arg in args
    )


def _command_error(command: str) -> str | None:
    if _has_hidden_char(command):
        return "실행 파일 경로에 보이지 않는 문자나 제어 문자(NUL·방향 전환 등)를 넣지 마세요."
    if command[:2] in ("\\\\", "//", "\\/", "/\\"):
        return (
            "네트워크 공유(UNC)·장치 경로의 실행 파일은 쓸 수 없음. "
            "이 PC의 로컬 드라이브 경로를 입력하세요."
        )
    if not Path(command).is_absolute():
        return "실행 파일은 절대 경로여야 함. 찾아보기로 고를 수 있음."
    # C's launcher starts only .exe files; a batch file would be re-read by cmd.exe.
    suffix = Path(command).suffix.casefold()
    if suffix in (".cmd", ".bat"):
        return (
            "배치 파일(.cmd·.bat)은 실행할 수 없음. node.exe 같은 실제 실행 파일을 고르고 "
            "스크립트 경로는 인자에 넣으세요."
        )
    if suffix != ".exe":
        return "실행 파일은 .exe여야 함."
    return None


URL_MESSAGES = {
    "URL_INVALID": "URL에는 공백·제어 문자·역슬래시를 넣지 말고 전체 주소를 입력하세요.",
    "URL_SCHEME": "https 주소를 입력하세요. http는 루프백 IP에만 허용됨.",
    "URL_USERINFO": "URL에서 계정·암호를 제거하세요.",
    "URL_QUERY": "URL에서 쿼리(?)를 제거하세요.",
    "URL_FRAGMENT": "URL에서 fragment(#)를 제거하세요.",
    "URL_HOST": "호스트를 확인하세요. IPv4는 네 개의 십진수로 입력하고 끝 점·인코딩을 제거하세요.",
    "URL_IDNA": "국제화 도메인 이름 형식이 맞지 않음. 호스트 이름을 확인하세요.",
    "URL_PORT": "포트는 1–65535 사이로 입력하세요.",
    "URL_PATH": "경로의 인코딩·점(.)·상위 경로(..)·빈 구간을 제거하세요.",
    "URL_ADDRESS_FORBIDDEN": (
        "링크 로컬·메타데이터·미지정·멀티캐스트·예약 주소 및 IPv4 포함 IPv6는 사용할 수 없음."
    ),
    "URL_HTTP_NOT_LOOPBACK": (
        "http는 루프백 IP(127.0.0.1 또는 [::1])만 허용됨. https를 사용하세요."
    ),
    "URL_NOT_CANONICAL": "정규화된 URL을 확인하세요.",
}


def _url_error(url: str) -> str | None:
    _, code = endpoint_input(url)
    return None if code is None else URL_MESSAGES[code]


def _parse_stdio_args(raw: str) -> tuple[tuple[str, ...] | None, str | None]:
    """A command line (serve --root "C:\\My Docs") or a JSON string array, bounded."""

    if len(raw) > _MAX_ARGS_JSON_CHARS:
        return None, "인자는 65536자 이하로 입력하세요."
    text = raw.strip()
    if text[:1] in ("[", "{"):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, RecursionError):
            return None, "JSON 문자열 배열 형식으로 입력하세요."
        if not isinstance(parsed, list) or not all(isinstance(value, str) for value in parsed):
            return None, "인자는 문자열만 담은 JSON 배열이어야 함."
        args = tuple(parsed)
    else:
        split = split_command_line(text)
        if split is None:
            return None, '따옴표(")가 닫히지 않았음.'
        args = tuple(split)
    if len(args) > _MAX_ARGS_COUNT:
        return None, "인자는 256개 이하로 입력하세요."
    return args, None


class PasteError(ValueError):
    """A fixed message for a paste the form could not read; never its content."""


@dataclass(frozen=True, slots=True)
class PastedServer:
    """What one pasted entry fills in. Environment values and headers are dropped."""

    server_id: str
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    url: str = ""
    dropped_values: int = 0
    skipped_servers: int = 0


def read_pasted_server(text: str) -> PastedServer:
    """Read a config entry (Claude/VS Code JSON, Codex TOML), a URL or a command.

    Only the first server of a config is read; the count of the rest is kept
    so the form can say so."""

    text = text.strip()
    if not text:
        raise PasteError("붙여 넣은 내용이 없음.")
    if len(text) > _MAX_PASTE_CHARS:
        raise PasteError("붙여 넣은 내용이 너무 김. 서버 하나의 항목만 넣으세요.")
    if _CODEX_TOML_SERVER.search(text):
        try:
            data: object = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, RecursionError):
            raise PasteError("TOML 형식을 읽지 못함. 대괄호와 따옴표를 확인하세요.") from None
        return _server_from_config(data)
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            raise PasteError("JSON 형식을 읽지 못함. 괄호와 따옴표를 확인하세요.") from None
        return _server_from_config(data)
    if re.match(r"https?://", text, re.IGNORECASE):
        return PastedServer(server_id="", transport="streamable_http", url=text)
    tokens = split_command_line(text)
    if tokens is None:
        raise PasteError('따옴표(")가 닫히지 않았음.')
    return PastedServer(server_id="", transport="stdio", command=tokens[0], args=tuple(tokens[1:]))


def _is_entry(value: object) -> bool:
    return isinstance(value, dict) and ("command" in value or "url" in value)


def _server_from_config(data: object) -> PastedServer:
    entries: dict[object, object] | None = None
    if isinstance(data, dict):
        for key in ("mcpServers", "mcp_servers", "servers"):
            if isinstance(data.get(key), dict):
                entries = data[key]
                break
        else:
            if _is_entry(data):
                return _server_from_entry("", data, 0)
            if data and all(_is_entry(value) for value in data.values()):
                entries = data
    if not entries:
        raise PasteError("서버 항목(command 또는 url)을 찾지 못함.")
    name, entry = next(iter(entries.items()))
    if not isinstance(entry, dict) or not _is_entry(entry):
        raise PasteError("첫 서버 항목에 command나 url이 없음.")
    return _server_from_entry(str(name), entry, len(entries) - 1)


def _server_from_entry(name: str, entry: dict[object, object], skipped: int) -> PastedServer:
    env = entry.get("env")
    headers = entry.get("headers")
    env_names = tuple(key for key in env if isinstance(key, str)) if isinstance(env, dict) else ()
    dropped = (len(env) if isinstance(env, dict) else 0) + (
        len(headers) if isinstance(headers, dict) else 0
    )
    url = entry.get("url")
    if url is not None:
        if not isinstance(url, str):
            raise PasteError("url은 문자열이어야 함.")
        return PastedServer(
            server_id=name,
            transport="streamable_http",
            url=url,
            dropped_values=dropped,
            skipped_servers=skipped,
        )
    command = entry.get("command")
    args = entry.get("args", [])
    if not isinstance(command, str):
        raise PasteError("command는 문자열이어야 함.")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise PasteError("args는 문자열 배열이어야 함.")
    return PastedServer(
        server_id=name,
        transport="stdio",
        command=command,
        args=tuple(args),
        env_names=env_names,
        dropped_values=dropped,
        skipped_servers=skipped,
    )


@dataclass(frozen=True, slots=True)
class LocalServerDraft:
    """Locally syntax-checked input; never an accepted Registry record."""

    server_id: str
    display_name: str
    transport: str
    tier: str
    max_label: str
    command: str | None = None
    args: tuple[str, ...] | None = None
    env_allow: tuple[str, ...] | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class LocalDraftPreview:
    """Display-only summary, with executable, arguments, and URL hidden."""

    transport: str
    text: str


# One label column for every form on the page, so fields line up across the
# common, transport and boundary groups; inputs stop at a readable width.
_LABEL_WIDTH = 128
_CONTENT_MAX_WIDTH = 808
PASTE_HINT = (
    "Claude·VS Code 설정의 서버 항목(JSON), Codex config.toml의 [mcp_servers.이름] 항목, "
    "MCP URL, 또는 실행 명령 한 줄을 붙여 넣으세요."
)
STATUS_IDLE = "Registry 등록은 아직 연결되지 않아 여기서는 입력 형식만 확인함."
STATUS_CHECKED = "입력 확인 완료 · Registry에 등록되지 않음."


def _form_label(title: str) -> QLabel:
    label = plain_label(title)
    label.setMinimumWidth(_LABEL_WIDTH)
    return label


class _TouchedFields(QObject):
    """Validation stays unconditional; only its presentation waits for interaction."""

    def __init__(self, parent: QWidget, refresh: Callable[[], None]) -> None:
        super().__init__(parent)
        self.keys: set[str] = set()
        self.submitted = False
        self._fields: dict[QObject, str] = {}
        self._refresh = refresh

    def watch(self, field: QWidget, key: str) -> None:
        self._fields[field] = key
        field.installEventFilter(self)
        if isinstance(field, (QLineEdit, QPlainTextEdit)):
            field.textChanged.connect(lambda *_args: self.keys.add(key))
        elif isinstance(field, SelectionBox):
            field.currentIndexChanged.connect(lambda *_args: self.keys.add(key))

    def touch(self, key: str) -> None:
        self.keys.add(key)
        self._refresh()

    def shows(self, key: str) -> bool:
        return self.submitted or key in self.keys

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FocusOut and watched in self._fields:
            self.touch(self._fields[watched])
        return super().eventFilter(watched, event)


class ServerAddForm(QWidget):
    """Prepare human input and send registration commands through the bridge."""

    local_draft_prepared = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._registration_bridge: BackendBridge | None = None
        self._registration_id = ""
        self._registration_token = ""
        # The server ID whose executable pin is being rechecked, or "".
        self._recheck_id = ""
        # The registered server ID whose missing settings record is being
        # written back (A128·A105), or "".
        self._restore_id = ""
        self._known_ids: frozenset[str] = frozenset()
        self._validated_draft: LocalServerDraft | None = None
        self._prepared = False
        self._boundary_adjusted = False
        self._id_from_name = False
        self._errors: dict[str, QLabel] = {}
        self._forms: list[QFormLayout] = []
        self._compact_form: bool | None = None

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("serverAddScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setMaximumWidth(_CONTENT_MAX_WIDTH)
        self._outer = QVBoxLayout(content)
        outer = self._outer
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)
        outer.addWidget(plain_label("MCP 서버 추가", name="pageTitle"))

        paste = QVBoxLayout()
        outer.addLayout(paste)
        paste.setSpacing(8)
        paste.addWidget(plain_label("설정 붙여넣기", name="sectionTitle"))
        paste.addWidget(plain_label(PASTE_HINT, name="secondary"))
        self.quick_input = QPlainTextEdit()
        self.quick_input.setObjectName("quickInput")
        self.quick_input.setAccessibleName("붙여 넣을 서버 설정 항목, URL 또는 실행 명령")
        self.quick_input.setPlaceholderText(
            '{"mcpServers": {"docs": {"command": "C:\\\\tools\\\\docs.exe", "args": ["serve"]}}}'
        )
        self.quick_input.setMaximumHeight(84)
        self.quick_input.setTabChangesFocus(True)
        paste.addWidget(self.quick_input)
        paste_actions = QHBoxLayout()
        paste.addLayout(paste_actions)
        paste_actions.setSpacing(12)
        self.fill_button = KeyboardActionButton("칸 채우기")
        self.fill_button.set_visual(icon="compose", variant="soft")
        self.fill_button.setObjectName("fillFromPasteButton")
        self.fill_button.setAccessibleName("붙여 넣은 설정으로 아래 칸 채우기")
        paste_actions.addWidget(self.fill_button)
        self.paste_status = plain_label(name="secondary")
        paste_actions.addWidget(self.paste_status, 1)

        common = self._new_form()
        outer.addLayout(common)
        self.display_name = QLineEdit()
        self.display_name.setObjectName("displayNameInput")
        self.display_name.setAccessibleName("표시 이름, 비우면 서버 ID")
        self.display_name.setPlaceholderText("비우면 서버 ID를 씀")
        self._add_field(common, "표시 이름", self.display_name, "display_name")
        self.server_id = QLineEdit()
        self.server_id.setObjectName("serverIdInput")
        self.server_id.setAccessibleName("서버 ID")
        self.server_id.setPlaceholderText("영문 표시 이름을 쓰면 자동으로 채움")
        self._add_field(common, "서버 ID", self.server_id, "server_id")

        self.transport = SelectionBox()
        self.transport.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.transport.setObjectName("transportSelect")
        self.transport.setAccessibleName("연결 방식")
        self.transport.addItem("stdio", "stdio")
        self.transport.addItem("Streamable HTTP", "streamable_http")
        self.transport.addItem("로컬 파일 서버", "local_file")
        common.addRow(_form_label("연결 방식"), self.transport)

        self.transport_fields = FitStack()
        self.transport_fields.setObjectName("transportFields")
        self.transport_fields.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        self.transport_fields.addWidget(self._stdio_fields())
        self.transport_fields.addWidget(self._http_fields())
        self.file_folders = FileServerFolders()
        self.transport_fields.addWidget(self.file_folders)
        outer.addWidget(self.transport_fields)

        boundary = self._new_form()
        outer.addLayout(boundary)
        self.tier = SelectionBox()
        self.tier.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.tier.setObjectName("tierSelect")
        self.tier.setAccessibleName("신뢰 경계")
        self.tier.addItem("신뢰 경계 선택", None)
        for value, title in TIER_NAMES.items():
            _add_coded_item(self.tier, title, value, value)
        self._add_field(boundary, "신뢰 경계", self.tier, "tier")
        self.max_label = SelectionBox()
        self.max_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.max_label.setObjectName("maxLabelSelect")
        self.max_label.setAccessibleName("최대 수신 등급")
        self.max_label.addItem("최대 수신 등급 선택", None)
        for value, (title, code, _tone) in LABELS.items():
            _add_coded_item(self.max_label, title, value, code)
        self._add_field(boundary, "최대 수신 등급", self.max_label, "max_label")
        self.boundary_notice = plain_label(name="boundaryNotice")
        outer.addWidget(self.boundary_notice)

        outer.addWidget(plain_label("등록할 내용", name="sectionTitle"))
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("draftPreview")
        self.preview.setAccessibleName("비밀값을 숨긴 현재 방식의 등록 내용")
        self.preview.setReadOnly(True)
        self.preview.setUndoRedoEnabled(False)
        self.preview.setMinimumHeight(132)
        outer.addWidget(self.preview)
        outer.addWidget(
            plain_label(
                "환경변수는 이름만 적으세요. "
                "인자와 URL에 인증값을 넣지 말고 별도 비밀 주입 계약을 사용해야 함.",
                name="secondary",
            )
        )
        self.status = plain_label(name="draftStatus")
        outer.addWidget(self.status)
        actions = QHBoxLayout()
        outer.addLayout(actions)
        self._registration_actions = actions
        self.prepare_button = KeyboardActionButton("입력 확인")
        self.prepare_button.set_visual(icon="check", variant="primary")
        self.prepare_button.setObjectName("prepareDraftButton")
        self.prepare_button.setAccessibleName("입력 형식 확인")
        actions.addWidget(self.prepare_button)
        self.confirm_button = KeyboardActionButton("확인한 내용으로 등록")
        self.confirm_button.setEnabled(False)
        self.confirm_button.hide()
        self.confirm_button.clicked.connect(self._confirm_registration)
        actions.addWidget(self.confirm_button)
        self.recheck_button = KeyboardActionButton("기존 실행 파일 다시 확인")
        self.recheck_button.hide()
        self.recheck_button.clicked.connect(self._recheck_registration)
        actions.addWidget(self.recheck_button)
        self.required_hint = plain_label(name="secondary")
        self.required_hint.setAccessibleName("남은 필수 입력 안내")
        actions.addWidget(self.required_hint)
        actions.addStretch(1)
        scroll.setWidget(content)
        shell.addWidget(scroll)

        self._touch = _TouchedFields(self, self._refresh)
        for field, key in (
            (self.server_id, "server_id"),
            (self.display_name, "display_name"),
            (self.command, "command"),
            (self.args_json, "args"),
            (self.env_allow, "env_allow"),
            (self.url, "url"),
            (self.tier, "tier"),
            (self.max_label, "max_label"),
        ):
            self._touch.watch(field, key)
        self.server_id.textChanged.connect(self._input_changed)
        self.server_id.textEdited.connect(self._id_edited)
        self.display_name.textChanged.connect(self._input_changed)
        self.display_name.textEdited.connect(self._name_edited)
        self.command.textChanged.connect(self._input_changed)
        self.args_json.textChanged.connect(self._input_changed)
        self.env_allow.textChanged.connect(self._input_changed)
        self.url.textChanged.connect(self._input_changed)
        self.transport.currentIndexChanged.connect(self._transport_changed)
        self.tier.currentIndexChanged.connect(self._tier_changed)
        self.max_label.currentIndexChanged.connect(self._label_changed)
        self.prepare_button.clicked.connect(self._prepare_local_draft)
        self.fill_button.clicked.connect(self.fill_from_paste)
        self.browse_button.clicked.connect(self._browse_executable)
        self.file_folders.changed.connect(self._input_changed)
        self._refresh()

    @property
    def validated_draft(self) -> LocalServerDraft | None:
        """Return the locally checked draft after a click; C must revalidate."""

        return self._validated_draft

    def set_known_ids(self, ids: set[str] | frozenset[str]) -> None:
        """Optional local duplicate hint; backend uniqueness remains authoritative."""

        self._known_ids = frozenset(ids)
        if self._registration_bridge is None:
            self._input_changed()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = self.width() < 620
        if compact == self._compact_form:
            return
        self._compact_form = compact
        self._registration_actions.setDirection(
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        )
        policy = (
            QFormLayout.RowWrapPolicy.WrapAllRows
            if compact
            else QFormLayout.RowWrapPolicy.DontWrapRows
        )
        for form in self._forms:
            form.setRowWrapPolicy(policy)
        margin = 12 if compact else 24
        self._outer.setContentsMargins(margin, 16 if compact else 24, margin, 20 if compact else 24)

    def add_section(self, widget: QWidget, title: str) -> None:
        """Append a related form below, folded behind a toggle until opened."""
        toggle = KeyboardActionButton(f"{title} ▸")
        toggle.setObjectName("linkButton")
        toggle.setCheckable(True)
        toggle.setAccessibleName(f"{title} 펼치기")
        widget.setVisible(False)

        def flip(opened: bool) -> None:
            widget.setVisible(opened)
            toggle.setText(f"{title} {'▾' if opened else '▸'}")
            toggle.setAccessibleName(f"{title} {'접기' if opened else '펼치기'}")

        toggle.toggled.connect(flip)
        self._outer.addSpacing(8)
        self._outer.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._outer.addWidget(widget)

    def fill_from_paste(self) -> None:
        """Fill the fields from the paste box, then empty it.

        The paste may hold secrets (environment values, headers, tokens in
        arguments); none of it stays in the box once read."""
        try:
            pasted = read_pasted_server(self.quick_input.toPlainText())
        except PasteError as error:
            self.paste_status.setText(str(error))
            return
        self.quick_input.clear()
        self._touch.keys.update(
            ("command", "args", "env_allow") if pasted.transport == "stdio" else ("url",)
        )
        if pasted.server_id:
            self._touch.keys.add("server_id")
            self.server_id.setText(pasted.server_id)
            self._id_from_name = False
        self.transport.setCurrentIndex(self.transport.findData(pasted.transport))
        if pasted.transport == "stdio":
            self.command.setText(pasted.command)
            self.args_json.setPlainText(join_command_line(pasted.args))
            self.env_allow.setPlainText("\n".join(pasted.env_names))
        else:
            self.url.setText(pasted.url)
        notes = ["칸을 채움. 신뢰 경계와 최대 수신 등급은 직접 고르세요."]
        if pasted.dropped_values:
            notes.append(f"환경변수 값·헤더 {pasted.dropped_values}개는 가져오지 않음.")
        if pasted.skipped_servers:
            notes.append(f"첫 서버만 채움. 나머지 {pasted.skipped_servers}개는 따로 추가하세요.")
        self.paste_status.setText(" ".join(notes))
        self._refresh()

    def _browse_executable(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "실행 파일 선택", "", "실행 파일 (*.exe)")
        if path:
            self.command.setText(QDir.toNativeSeparators(path))

    def _name_edited(self, name: str) -> None:
        """Follow the display name with an ID until the ID is typed by hand."""
        if self.server_id.text() and not self._id_from_name:
            return
        suggestion = suggested_server_id(name)
        self._id_from_name = bool(suggestion)
        self.server_id.setText(suggestion)

    def _id_edited(self, _text: str) -> None:
        self._id_from_name = False

    def _new_form(self, parent: QWidget | None = None) -> QFormLayout:
        form = QFormLayout(parent)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._forms.append(form)
        return form

    def _stdio_fields(self) -> QWidget:
        page = QWidget()
        form = self._new_form(page)
        command_row = ResponsiveRow(400)
        command_layout = command_row.row
        self.command = QLineEdit()
        self.command.setObjectName("commandInput")
        self.command.setAccessibleName("stdio 실행 파일 절대 경로")
        self.command.setPlaceholderText("C:\\tools\\server.exe")
        command_layout.addWidget(self.command, 1)
        self.browse_button = KeyboardActionButton("찾아보기")
        self.browse_button.setAccessibleName("실행 파일 찾아보기")
        command_layout.addWidget(self.browse_button)
        self._add_field(form, "실행 파일", command_row, "command")
        self.args_json = QPlainTextEdit()
        self.args_json.setObjectName("argsInput")
        self.args_json.setAccessibleName("인자, 명령줄처럼 공백으로 구분")
        self.args_json.setPlaceholderText('serve --root "C:\\My Docs"')
        self.args_json.setMaximumHeight(60)
        self.args_json.setTabChangesFocus(True)
        self._add_field(form, "인자", self.args_json, "args")
        self.env_allow = QPlainTextEdit()
        self.env_allow.setObjectName("envAllowInput")
        self.env_allow.setAccessibleName("넘겨줄 환경변수 이름, 쉼표나 줄바꿈으로 구분")
        self.env_allow.setPlaceholderText("LANG, PATH")
        self.env_allow.setMaximumHeight(60)
        self.env_allow.setTabChangesFocus(True)
        self.env_allow.setToolTip("env_allow · 이름만, 값은 넣지 마세요")
        self._add_field(form, "환경변수 이름", self.env_allow, "env_allow")
        return page

    def _http_fields(self) -> QWidget:
        page = QWidget()
        form = self._new_form(page)
        self.url = QLineEdit()
        self.url.setObjectName("urlInput")
        self.url.setAccessibleName("전체 MCP endpoint URL")
        self.url.setPlaceholderText("https://example.invalid/mcp")
        self._add_field(form, "MCP endpoint URL", self.url, "url")
        return page

    def _add_field(self, form: QFormLayout, title: str, control: QWidget, key: str) -> None:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(control)
        error = plain_label(name=f"{key}Error")
        error.setAccessibleName(f"{title} 입력 오류")
        layout.addWidget(error)
        self._errors[key] = error
        form.addRow(_form_label(title), wrapper)

    def _input_changed(self, *_args: object) -> None:
        self._registration_id = ""
        self._registration_token = ""
        self.confirm_button.setEnabled(False)
        self._prepared = False
        self._validated_draft = None
        self._refresh()

    def _transport_changed(self, *_args: object) -> None:
        self.transport_fields.setCurrentIndex(self.transport.currentIndex())
        local = self.transport.currentData() == "local_file"
        self.quick_input.setEnabled(not local)
        self.fill_button.setEnabled(not local)
        self._input_changed()

    def _tier_changed(self, *_args: object) -> None:
        if self.tier.currentData() not in (
            "approved_external",
            "general_external",
        ):
            self._boundary_adjusted = False
        self._limit_external_label()
        self._input_changed()

    def _label_changed(self, *_args: object) -> None:
        self._limit_external_label()
        self._input_changed()

    def _limit_external_label(self) -> None:
        if (
            self.tier.currentData() in ("approved_external", "general_external")
            and self.max_label.currentData() == "CONFIDENTIAL"
        ):
            self._boundary_adjusted = True
            self.max_label.setCurrentIndex(self.max_label.findData("INTERNAL"))

    def _build_draft(self) -> tuple[LocalServerDraft | None, dict[str, str]]:
        errors: dict[str, str] = {}
        server_id = self.server_id.text().strip()
        display_name = self.display_name.text().strip()
        # The listed ID of the registration being restored is not a new one.
        known = self._known_ids - {self._restore_id}
        if not server_id:
            errors["server_id"] = "서버 ID를 입력하세요."
        elif len(server_id) > _MAX_SERVER_ID_CHARS:
            errors["server_id"] = f"서버 ID는 {_MAX_SERVER_ID_CHARS}자 이하로 입력하세요."
        elif _has_hidden_char(server_id):
            errors["server_id"] = (
                "서버 ID에 보이지 않는 문자나 제어 문자(NUL·방향 전환·zero-width 등)를 넣지 마세요."
            )
        elif server_id in known:
            errors["server_id"] = _DUPLICATE_ID
        elif any(_id_key(server_id) == _id_key(other) for other in known):
            errors["server_id"] = (
                "현재 목록에 대소문자·전각 여부만 다른 같은 ID가 있음. 다른 ID를 입력하세요."
            )
        elif not valid_server_id(server_id):
            errors["server_id"] = (
                "서버 ID 형식이 맞지 않음. 연속 밑줄(__)과 끝의 밑줄(_)은 쓸 수 없음."
            )
        # An empty display name falls back to the ID; a typed one is checked.
        if len(display_name) > _MAX_DISPLAY_NAME_CHARS:
            errors["display_name"] = f"표시 이름은 {_MAX_DISPLAY_NAME_CHARS}자 이하로 입력하세요."
        elif _has_hidden_char(display_name):
            errors["display_name"] = "표시 이름에 보이지 않는 문자나 제어 문자를 넣지 마세요."
        if self.tier.currentData() is None:
            errors["tier"] = "신뢰 경계를 선택하세요."
        if self.max_label.currentData() is None:
            errors["max_label"] = "최대 수신 등급을 선택하세요."

        transport = str(self.transport.currentData())
        command: str | None = None
        args: tuple[str, ...] | None = None
        env_allow: tuple[str, ...] | None = None
        url: str | None = None
        if transport == "local_file":
            if not self.file_folders.ready():
                errors["command"] = "루트 폴더와 각 등급을 선택하세요."
        elif transport == "stdio":
            command = self.command.text().strip()
            if not command:
                errors["command"] = "실행 파일 절대 경로를 입력하거나 찾아보기로 고르세요."
            elif len(command) >= self.command.maxLength():
                errors["command"] = (
                    "실행 파일 경로가 입력 칸 한도를 넘어 잘렸을 수 있음. 경로를 확인하세요."
                )
            elif (command_error := _command_error(command)) is not None:
                errors["command"] = command_error
            args, args_error = _parse_stdio_args(self.args_json.toPlainText())
            if args_error is not None:
                errors["args"] = args_error
            names = tuple(
                name for name in _ENV_SEPARATORS.split(self.env_allow.toPlainText()) if name
            )
            if any(_ENV_NAME.fullmatch(name) is None for name in names):
                errors["env_allow"] = (
                    "이름만 쉼표나 줄바꿈으로 나눠 적으세요. 값이나 '='은 넣지 마세요."
                )
            elif len(names) != len(set(names)):
                errors["env_allow"] = "같은 환경변수 이름을 두 번 적지 마세요."
            else:
                env_allow = names
        else:
            url = self.url.text()
            if not url:
                errors["url"] = "전체 MCP endpoint URL을 입력하세요."
            elif len(url) >= self.url.maxLength():
                errors["url"] = "URL이 입력 칸 한도를 넘어 잘렸을 수 있음. 주소를 확인하세요."
            elif (url_error := _url_error(url)) is not None:
                errors["url"] = url_error
            else:
                url, _ = endpoint_input(url)

        if errors:
            return None, errors
        return (
            LocalServerDraft(
                server_id=server_id,
                display_name=display_name or server_id,
                transport=transport,
                tier=str(self.tier.currentData()),
                max_label=str(self.max_label.currentData()),
                command=command,
                args=args,
                env_allow=env_allow,
                url=url,
            ),
            errors,
        )

    def _preview_for(self, draft: LocalServerDraft) -> LocalDraftPreview:
        shown: dict[str, object] = {
            "server_id": draft.server_id,
            "display_name": draft.display_name,
            "transport": draft.transport,
            "tier": draft.tier,
            "max_label": draft.max_label,
        }
        if draft.transport == "local_file":
            shown["root"] = "선택됨 · 값 미표시"
            shown["root_label"] = self.file_folders.root.label.currentData()
            shown["list_root_label"] = self.file_folders.list_root.label.currentData()
            shown["folder_rules"] = len(self.file_folders.rows)
        elif draft.transport == "stdio":
            shown["command"] = "입력됨 · 값 미표시"
            shown["args"] = f"{len(draft.args or ())}개 · 값 미표시"
            shown["env_allow"] = list(draft.env_allow or ())
        else:
            shown["url"] = "입력됨 · 값 미표시"
        return LocalDraftPreview(
            transport=draft.transport,
            text=json.dumps(shown, ensure_ascii=False, indent=2),
        )

    def start_restore(self, server_id: str, tier: str | None, max_label: str | None) -> None:
        """Write back the settings record of a registered server; the human re-enters it.

        Only the ID and the boundary the Registry row shows are filled in. The
        connection details come from the human, and the backend saves them
        only if the server lists the registered tools (A128·A105).
        """
        self._recheck_id = ""
        self._restore_id = server_id
        self._touch.keys.update(("server_id", "tier", "max_label"))
        self.server_id.setText(server_id)
        self._id_from_name = False
        self.tier.setCurrentIndex(max(0, self.tier.findData(tier)))
        self.max_label.setCurrentIndex(max(0, self.max_label.findData(max_label)))
        self._input_changed()

    def _rechecking(self) -> bool:
        return bool(self._recheck_id) and self.server_id.text().strip() == self._recheck_id

    def _restoring(self) -> bool:
        return bool(self._restore_id) and self.server_id.text().strip() == self._restore_id

    def _refresh(self) -> None:
        draft, errors = self._build_draft()
        rechecking = self._rechecking()
        restoring = self._restoring()
        if self._recheck_id and not rechecking:
            self._recheck_id = ""  # editing the ID ends the recheck
        if self._restore_id and not restoring:
            self._restore_id = ""  # and the restore
            draft, errors = self._build_draft()
        if rechecking and errors.get("server_id") == _DUPLICATE_ID:
            # The listed ID is the server being rechecked, not a new registration.
            del errors["server_id"]
        for key, widget in self._errors.items():
            shown = key in errors and self._touch.shows(key)
            widget.setProperty("validationError", shown)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.setVisible(shown)
            if key in errors:
                widget.setText(errors[key])
            else:
                widget.setText("")
        external = self.tier.currentData() in (
            "approved_external",
            "general_external",
        )
        self.boundary_notice.setVisible(external)
        if external:
            notice = "외부 경계의 최대 수신 등급은 INTERNAL임."
            if self._boundary_adjusted:
                notice += " CONFIDENTIAL 선택을 INTERNAL로 조정함."
            self.boundary_notice.setText(notice)
        self.prepare_button.setEnabled(draft is not None and not self._prepared)
        self.required_hint.setText(f"필수 입력·선택 {len(errors)}개를 확인하세요" if errors else "")
        self.required_hint.setVisible(bool(errors))
        self.confirm_button.setText(
            "확인한 지문 저장"
            if rechecking
            else "확인한 설정 저장"
            if restoring
            else "확인한 내용으로 등록"
        )
        self.preview.setPlainText(
            self._preview_for(draft).text
            if draft is not None
            else "기존 서버의 실행 파일 지문만 다시 확인함. 다른 등록 내용은 바뀌지 않음."
            if rechecking
            else "등록된 서버의 연결 정보를 다시 입력하세요. 설정 파일에만 저장함."
            if restoring
            else "입력 오류를 고치면 등록할 내용이 표시됨."
        )
        self.status.setText(
            "설정 다시 저장 · 도구 목록이 등록과 같을 때만 설정 파일에 저장함. "
            "등록 상태는 PENDING으로 남음."
            if self._registration_bridge is not None and restoring
            else "등록 전 내용을 확인하세요. 등록 후에도 검사·활성화 전에는 실행되지 않음."
            if self._registration_bridge is not None
            else STATUS_CHECKED
            if self._prepared
            else STATUS_IDLE
        )

    def _prepare_local_draft(self) -> None:
        self._touch.submitted = True
        if self._prepared:
            return
        draft, errors = self._build_draft()
        if draft is None or errors:
            self._refresh()
            return
        self._validated_draft = draft
        self._prepared = True
        self._recheck_id = ""
        self._refresh()
        if self._registration_bridge is not None:
            self._send_registration(
                RegistrationRequest(
                    "prepare_file" if draft.transport == "local_file" else "prepare",
                    draft.server_id,
                    draft.transport,
                    draft.tier,
                    draft.max_label,
                    draft.command,
                    draft.args or (),
                    draft.env_allow or (),
                    draft.url,
                    file_root=self.file_folders.root.path.text(),
                    root_label=str(self.file_folders.root.label.currentData()),
                    list_root_label=str(self.file_folders.list_root.label.currentData()),
                    folder_labels=self.file_folders.folder_labels(),
                    restore=self._restoring(),
                )
            )
        else:
            self.local_draft_prepared.emit(self._preview_for(draft))

    def attach_registration(self, bridge: BackendBridge) -> None:
        self._registration_bridge = bridge
        self.prepare_button.setText("등록 내용 확인")
        self.confirm_button.show()
        self.recheck_button.show()
        bridge.registration_changed.connect(self._registration_result)
        bridge.command_changed.connect(self._registration_command)
        bridge.lifecycle_changed.connect(self._registration_lifecycle)
        self._refresh()

    def _registration_lifecycle(self, event: LifecycleEvent) -> None:
        if event.state is BridgeState.READY and self._registration_bridge is not None:
            self._registration_bridge.request_registration(RegistrationRequest("list"))
        elif event.state in (BridgeState.CLOSING, BridgeState.CLOSED, BridgeState.FAILED):
            self._registration_id = ""
            self._registration_token = ""
            self._recheck_id = ""
            self._restore_id = ""
            self.confirm_button.setEnabled(False)
            self.prepare_button.setEnabled(False)
            self.recheck_button.setEnabled(False)

    def _send_registration(self, request: RegistrationRequest) -> None:
        bridge = self._registration_bridge
        if bridge is None:
            return
        self._registration_id = new_command_id()
        self.confirm_button.setEnabled(False)
        self.prepare_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        self.status.setText(
            "확인 중 · 아직 지문 저장 결과가 아님."
            if self._rechecking()
            else "확인 중 · 아직 설정 저장 결과가 아님."
            if self._restoring()
            else "확인 중 · 아직 등록 결과가 아님."
        )
        bridge.request_registration(request, command_id=self._registration_id)

    def _confirm_registration(self) -> None:
        self._touch.submitted = True
        self._refresh()
        token = self._registration_token
        self._registration_token = ""
        if token:
            self._send_registration(RegistrationRequest("confirm", token=token))

    def _recheck_registration(self) -> None:
        self._registration_token = ""
        self._restore_id = ""
        self._recheck_id = self.server_id.text().strip()
        self._refresh()
        self._send_registration(RegistrationRequest("recheck", server_id=self._recheck_id))

    def _registration_command(self, event: CommandEvent) -> None:
        if event.command_id == self._registration_id and event.stage in (
            CommandStage.REJECTED,
            CommandStage.UNCONFIRMED,
        ):
            self._registration_result(
                RegistrationView("REG_UNCONFIRMED", command_id=event.command_id)
            )

    def _registration_result(self, view: RegistrationView) -> None:
        if view.command_id != self._registration_id:
            return
        self._registration_id = ""
        self._registration_token = view.token
        self.recheck_button.setEnabled(True)
        self._prepared = False
        self._refresh()
        if view.token:
            lines = []
            if view.command is not None:
                lines = [
                    "실행 파일: " + untrusted_display_text(view.command)[0],
                    f"크기: {view.size} bytes",
                    f"SHA-256: {view.sha256}",
                    "지문은 실행 파일만 포함함. 스크립트·패키지는 포함하지 않음.",
                ]
            elif view.url is not None:
                self.url.blockSignals(True)
                self.url.setText(view.url)
                self.url.blockSignals(False)
                lines = ["정규화 URL: " + view.url]
            self.preview.setPlainText(chr(10).join(lines))
            self.confirm_button.setEnabled(True)
            self.status.setText(
                "실행 파일 지문이 바뀜. 다시 확인하세요."
                if view.code == "REG_PIN_CHANGED"
                else "표시된 지문을 확인한 뒤 지문 저장 버튼을 누르세요."
                if self._rechecking()
                else "표시된 내용을 확인한 뒤 설정 저장 버튼을 누르세요."
                if self._restoring()
                else "표시된 내용을 확인한 뒤 등록 버튼을 누르세요."
            )
            return
        self.confirm_button.setEnabled(False)
        if view.code == "REG_RECHECK_UNAVAILABLE":
            # Not a recheck target, so the duplicate hint applies again.
            self._recheck_id = ""
            self._refresh()
        elif view.code in ("REG_SETTINGS_RESTORED", "REG_RESTORE_UNAVAILABLE"):
            # Restored, or nothing left to restore: the ID is a duplicate again.
            self._restore_id = ""
            self._refresh()
        messages = {
            "REG_FOLDER_INVALID": (
                "폴더 검증 거부. 존재하는 루트와 하위 폴더를 고르고 겹친 규칙·등급을 확인하세요."
            ),
            "REG_REGISTERED": (
                "Registry 등록·설정 저장 완료 · PENDING. 검사·활성화 전에는 실행되지 않음."
            ),
            "REG_PIN_SAVED": "실행 파일 지문 저장 완료 · 다시 연결하려면 앱을 재시작하세요.",
            "REG_SERVER_EXISTS": (
                "이미 등록된 ID임. 새 ID를 입력하거나 기존 실행 파일을 다시 확인하세요."
            ),
            "REG_STORE_UNKNOWN": "등록 저장 결과 불명. 재등록하지 말고 앱을 재시작해 확인하세요.",
            "REG_SETTINGS_SAVE_FAILED": (
                "설정 저장 실패 · Registry 행은 남아 있을 수 있음. 앱을 재시작해 확인하세요."
            ),
            "REG_EXECUTABLE_INVALID": (
                "실행 파일·인자·환경변수 검증 거부. 파일과 입력을 확인하세요."
            ),
            "REG_SERVER_UNREACHABLE": (
                "서버에 연결하지 못해 등록하지 않음. 서버를 확인한 뒤 다시 등록하세요."
            ),
            "REG_LISTING_INVALID": (
                "도구 목록을 끝까지 받지 못했거나 등록 조건에 맞지 않아 등록하지 않음. "
                "서버를 확인한 뒤 다시 등록하세요."
            ),
            "REG_UNAVAILABLE": "등록 서비스가 연결되지 않음.",
            "REG_RECHECK_UNAVAILABLE": ("설정과 Registry에 있는 stdio 서버 ID를 입력하세요."),
            "REG_CONFIRM_EXPIRED": "확인 내용이 만료됨. 등록 내용을 다시 확인하세요.",
            "REG_SETTINGS_RESTORED": (
                "설정 다시 저장 완료 · PENDING. 검사·활성화 전에는 실행되지 않음."
            ),
            "REG_RESTORE_UNAVAILABLE": (
                "설정을 다시 저장할 등록이 아님. 설정 파일에 없는 PENDING 등록만 복구함."
            ),
            "REG_RESTORE_BOUNDARY_DIFFERS": (
                "등록된 신뢰 경계·최대 수신 등급과 다름. 서버 목록에 보이는 값으로 고르세요."
            ),
            "REG_RESTORE_TOOLS_DIFFER": (
                "도구 목록이 등록 내용과 달라 저장하지 않음. 같은 서버를 입력했는지 확인하세요."
            ),
        }
        self.status.setText(
            messages.get(
                view.code,
                URL_MESSAGES.get(
                    view.code, "등록 완료를 확인하지 못함. 입력을 확인하거나 앱을 재시작하세요."
                ),
            )
        )


# ---- 로컬 파일 MCP 서버의 폴더 등급 (contracts 2026-10-15 로컬 파일 접근 결정) ----


def _add_coded_item(box: SelectionBox, title: str, value: str, code: str) -> None:
    """A Korean choice whose config value is shown as its tooltip."""
    box.addItem(title, value)
    box.setItemData(box.count() - 1, code, Qt.ItemDataRole.ToolTipRole)


def _folder_error(root: str) -> str | None:
    if _has_hidden_char(root):
        return "폴더 경로에 보이지 않는 문자나 제어 문자를 넣지 마세요."
    if root[:2] in ("\\\\", "//", "\\/", "/\\"):
        return "네트워크 공유(UNC)·장치 경로는 쓸 수 없음. 이 PC의 로컬 폴더 경로를 입력하세요."
    if not Path(root).is_absolute():
        return "루트 폴더는 절대 경로여야 함."
    return None


def _prefix_parts(raw: str) -> tuple[tuple[str, ...] | None, str | None]:
    """Lexical segment-prefix check only; C's safe_path resolves real paths."""

    if raw == "":
        return (), None
    if _has_hidden_char(raw):
        return None, "폴더 이름에 보이지 않는 문자나 제어 문자를 넣지 마세요."
    if "\\" in raw:
        return None, "하위 폴더는 /로 구분하세요. 역슬래시는 쓰지 마세요."
    parts = tuple(raw.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        return None, "앞뒤의 /, 빈 이름, 점(.)·점 두 개(..) 없이 루트 기준 상대 경로로 입력하세요."
    return parts, None


@dataclass(frozen=True, slots=True)
class FolderLabelRule:
    """One segment-prefix rule relative to the root; () is the whole root."""

    prefix: tuple[str, ...]
    label: str


@dataclass(frozen=True, slots=True)
class LocalFolderDraft:
    """Locally syntax-checked folder labels; never an accepted Registry record."""

    root: str
    rules: tuple[FolderLabelRule, ...]


class _FolderRuleRow(QWidget):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        layout.addLayout(row)
        row.setSpacing(4)
        row.setDirection(QBoxLayout.Direction.TopToBottom)
        self._row_actions = row
        self.prefix = QLineEdit()
        self.prefix.setAccessibleName("루트 기준 하위 폴더, 비우면 루트 전체")
        self.prefix.setPlaceholderText("예: personal/notes · 비우면 루트 전체")
        row.addWidget(self.prefix, 1)
        self.label = SelectionBox()
        self.label.setAccessibleName("이 폴더의 등급")
        self.label.addItem("등급 선택", None)
        for value, (title, code, _tone) in LABELS.items():
            _add_coded_item(self.label, title, value, code)
        row.addWidget(self.label)
        self.remove_button = KeyboardActionButton("삭제")
        self.remove_button.setAccessibleName("이 폴더 규칙 삭제")
        row.addWidget(self.remove_button)
        self.error = plain_label(name="folderRuleError")
        self.error.setAccessibleName("폴더 규칙 입력 오류")
        self.error.setVisible(False)
        layout.addWidget(self.error)
        self.touch = _TouchedFields(self, self.changed.emit)
        self.touch.watch(self.prefix, "prefix")
        self.touch.watch(self.label, "label")
        self.prefix.textChanged.connect(lambda _text: self.changed.emit())
        self.label.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_row_actions"):
            self._row_actions.setDirection(
                QBoxLayout.Direction.TopToBottom
                if self.width() < 450
                else QBoxLayout.Direction.LeftToRight
            )


class LocalFolderRulesForm(QWidget):
    """Root folder and per-subfolder labels for the app's read-only file server.

    Convenience checks only. C's Registry and ``safe_path`` must validate the
    root and every rule again; overlapping rules take the highest label and a
    file matching no rule is Unclassified (B Catalog, design 4.2).
    """

    folder_draft_prepared = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._validated_draft: LocalFolderDraft | None = None
        self._prepared = False
        self.rows: list[_FolderRuleRow] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        outer.addWidget(
            plain_label(
                "루트 폴더와 하위 폴더별 등급을 정하세요. 규칙이 겹치면 더 높은 등급이 적용되고, "
                "어느 규칙에도 맞지 않는 파일을 읽으면 미분류로 작업이 멈춤. "
                "여기서는 입력 형식만 확인하고, 실제 경로 검증은 등록 단계에서 다시 함.",
                name="secondary",
            )
        )
        self.root = QLineEdit()
        self.root.setObjectName("folderRootInput")
        self.root.setAccessibleName("로컬 파일 서버 루트 폴더 절대 경로")
        self.root.setPlaceholderText("루트 폴더의 절대 경로")
        outer.addWidget(self.root)
        self.root_error = plain_label(name="folderRootError")
        self.root_error.setAccessibleName("루트 폴더 입력 오류")
        outer.addWidget(self.root_error)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(8)
        outer.addLayout(self._rows_layout)
        self.rules_error = plain_label(name="folderRulesError")
        outer.addWidget(self.rules_error)
        actions = QHBoxLayout()
        outer.addLayout(actions)
        self.add_button = KeyboardActionButton("폴더 규칙 추가")
        self.add_button.setAccessibleName("하위 폴더 등급 규칙 추가")
        actions.addWidget(self.add_button)
        self.prepare_button = KeyboardActionButton("폴더 등급 확인")
        self.prepare_button.setAccessibleName("입력 형식 확인 후 폴더 등급 준비")
        actions.addWidget(self.prepare_button)
        actions.addStretch(1)
        self.status = plain_label(name="folderDraftStatus")
        outer.addWidget(self.status)

        self.required_hint = plain_label(name="secondary")
        actions.addWidget(self.required_hint)
        self._touch = _TouchedFields(self, self._refresh)
        self._touch.watch(self.root, "root")
        self.root.textChanged.connect(self._input_changed)
        self.add_button.clicked.connect(self.add_rule)
        self.prepare_button.clicked.connect(self._prepare)
        self.add_rule()

    @property
    def validated_draft(self) -> LocalFolderDraft | None:
        """Return the locally checked draft after a click; C must revalidate."""

        return self._validated_draft

    def add_rule(self) -> _FolderRuleRow:
        row = _FolderRuleRow()
        row.changed.connect(self._input_changed)
        row.remove_requested.connect(self._remove_rule)
        self.rows.append(row)
        self._rows_layout.addWidget(row)
        self._input_changed()
        return row

    def _remove_rule(self, row: _FolderRuleRow) -> None:
        if row not in self.rows:
            return
        self._touch.keys.add("rules")
        self.rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()
        self._input_changed()

    def _input_changed(self, *_args: object) -> None:
        self._prepared = False
        self._validated_draft = None
        self._refresh()

    def _build_draft(self) -> LocalFolderDraft | None:
        root = self.root.text().strip()
        if not root:
            root_error: str | None = "루트 폴더 절대 경로를 입력하세요."
        elif len(root) >= self.root.maxLength():
            root_error = "경로가 입력 칸 한도를 넘어 잘렸을 수 있음. 경로를 확인하세요."
        else:
            root_error = _folder_error(root)
        self.root_error.setText(root_error or "")
        self.root_error.setVisible(root_error is not None and self._touch.shows("root"))
        failed = root_error is not None

        rules: list[FolderLabelRule] = []
        # Windows folder names ignore case, so Docs and docs are the same folder.
        seen: set[tuple[str, ...]] = set()
        for row in self.rows:
            parts, error = _prefix_parts(row.prefix.text().strip())
            error_key = "prefix"
            key = None if parts is None else tuple(part.casefold() for part in parts)
            if key is not None and key in seen:
                error = "같은 폴더 규칙을 두 번 적지 마세요. 대소문자만 다른 이름도 같은 폴더임."
            elif error is None and row.label.currentData() is None:
                error_key = "label"
                error = "이 폴더의 등급을 선택하세요."
            row.error.setText(error or "")
            row.error.setVisible(
                error is not None and (self._touch.submitted or row.touch.shows(error_key))
            )
            if key is not None:
                seen.add(key)
            if error is not None or parts is None:
                failed = True
                continue
            rules.append(FolderLabelRule(parts, str(row.label.currentData())))
        rules_error = None if self.rows else "폴더 규칙을 하나 이상 추가하세요."
        self.rules_error.setText(rules_error or "")
        self.rules_error.setVisible(rules_error is not None and self._touch.shows("rules"))
        if failed or rules_error is not None:
            return None
        return LocalFolderDraft(root, tuple(rules))

    def _refresh(self) -> None:
        draft = self._build_draft()
        self.prepare_button.setEnabled(draft is not None and not self._prepared)
        remaining = sum(
            bool(label.text())
            for label in (self.root_error, self.rules_error, *(row.error for row in self.rows))
        )
        self.required_hint.setText(
            f"필수 입력·선택 {remaining}개를 확인하세요" if remaining else ""
        )
        self.required_hint.setVisible(bool(remaining))
        self.status.setText(
            "폴더 등급 확인 완료 · 아직 등록되지 않음."
            if self._prepared
            else "입력 형식만 확인하세요. 등록은 아직 연결되지 않음."
        )

    def _prepare(self) -> None:
        self._touch.submitted = True
        if self._prepared:
            return
        draft = self._build_draft()
        if draft is None:
            self._refresh()
            return
        self._validated_draft = draft
        self._prepared = True
        self._refresh()
        shown = [
            {"folder": "/".join(rule.prefix) or "(루트 전체)", "label": rule.label}
            for rule in draft.rules
        ]
        text = json.dumps(
            {"root": "입력됨 · 값 미표시", "rules": shown}, ensure_ascii=False, indent=2
        )
        self.folder_draft_prepared.emit(LocalDraftPreview(transport="stdio", text=text))
