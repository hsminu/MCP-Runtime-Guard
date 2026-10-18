"""Local-only MCP registration draft form.

This widget performs convenience syntax checks. It neither registers a server
nor checks whether an executable or endpoint is safe. C's Registry and
security modules must validate every field again before creating PENDING.
"""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .presentation import is_hidden_char
from .widgets import KeyboardActionButton, SelectionBox, plain_label

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_ARGS_JSON_CHARS = 65_536
_MAX_ARGS_COUNT = 256
_MAX_SERVER_ID_CHARS = 128
_MAX_DISPLAY_NAME_CHARS = 256
# Numeric host spellings (decimal, octal, hex, dotted or not) that resolvers
# may accept as an IPv4 address even though they are not dotted-quad text.
_NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+))*\.?\Z")


def _has_hidden_char(value: str) -> bool:
    return any(is_hidden_char(char) for char in value)


def _id_key(value: str) -> str:
    """Compare IDs as an operator would read them: case and width folded."""

    return unicodedata.normalize("NFKC", value).casefold()


def _command_error(command: str) -> str | None:
    if _has_hidden_char(command):
        return "실행 파일 경로에 보이지 않는 문자나 제어 문자(NUL·방향 전환 등)를 넣지 마."
    if command[:2] in ("\\\\", "//", "\\/", "/\\"):
        return (
            "네트워크 공유(UNC)·장치 경로의 실행 파일은 쓸 수 없어. "
            "이 PC의 로컬 드라이브 경로를 입력해."
        )
    if not Path(command).is_absolute():
        return "실행 파일은 절대 경로여야 해."
    return None


def _url_error(url: str) -> str | None:
    if any(char.isspace() for char in url) or _has_hidden_char(url):
        return "URL에는 공백, 제어 문자, 보이지 않는 문자를 넣지 마."
    if "\\" in url:
        return "URL에 역슬래시(\\)를 넣지 마. 주소창에서는 다른 호스트로 해석될 수 있어."
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname or ""
        valid = (
            parts.scheme in ("http", "https")
            and bool(host)
            and "@" not in parts.netloc
            and not parts.query
            and not parts.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        return "http(s) 전체 URL만 입력해. 계정·암호·쿼리·fragment는 제외해."
    if port == 0:
        return "포트는 1–65535 사이로 입력해."
    if not host.isascii():
        return "호스트 이름은 ASCII로 입력해. 국제화 도메인은 xn-- 형식으로 바꿔 입력해."
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if _NUMERIC_HOST.fullmatch(host):
            return "IP 주소는 점으로 구분한 네 개의 십진수(예: 10.0.0.1)로 입력해."
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_link_local or address.is_unspecified or address.is_multicast:
        return (
            "링크 로컬·클라우드 메타데이터(169.254.x.x 등)·미지정·멀티캐스트 주소는 "
            "MCP endpoint로 쓸 수 없어."
        )
    return None


def _parse_stdio_args(raw: str) -> tuple[tuple[str, ...] | None, str | None]:
    """Bound the local preview parser and require a flat string array."""

    if len(raw) > _MAX_ARGS_JSON_CHARS:
        return None, "인자 JSON은 65536자 이하로 입력해."
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None, "JSON 문자열 배열 형식으로 입력해."
    if not isinstance(parsed, list) or not all(isinstance(value, str) for value in parsed):
        return None, "인자는 문자열만 담은 JSON 배열이어야 해."
    if len(parsed) > _MAX_ARGS_COUNT:
        return None, "인자는 256개 이하로 입력해."
    return tuple(parsed), None


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


class ServerAddForm(QWidget):
    """Prepare one local draft without calling Registry or changing status."""

    localDraftPrepared = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._known_ids: frozenset[str] = frozenset()
        self._validated_draft: LocalServerDraft | None = None
        self._prepared = False
        self._boundary_adjusted = False
        self._errors: dict[str, QLabel] = {}
        self._forms: list[QFormLayout] = []
        self._compact_form: bool | None = None

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("serverAddScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._outer = QVBoxLayout(content)
        outer = self._outer
        outer.setContentsMargins(26, 28, 26, 28)
        outer.setSpacing(14)
        outer.addWidget(plain_label("MCP 서버 추가 초안", name="pageTitle"))
        outer.addWidget(
            plain_label(
                "여기서는 입력 형식만 확인해. 등록과 PENDING 상태는 Registry 확인 뒤에만 확정돼.",
                name="secondary",
            )
        )

        common = self._new_form()
        self.server_id = QLineEdit()
        self.server_id.setObjectName("serverIdInput")
        self.server_id.setAccessibleName("서버 ID")
        self._add_field(common, "서버 ID", self.server_id, "server_id")
        self.display_name = QLineEdit()
        self.display_name.setObjectName("displayNameInput")
        self.display_name.setAccessibleName("표시 이름")
        self._add_field(common, "표시 이름", self.display_name, "display_name")

        self.transport = SelectionBox()
        self.transport.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.transport.setObjectName("transportSelect")
        self.transport.setAccessibleName("연결 방식")
        self.transport.addItem("stdio", "stdio")
        self.transport.addItem("Streamable HTTP", "streamable_http")
        common.addRow("연결 방식", self.transport)
        outer.addLayout(common)

        self.transport_fields = QStackedWidget()
        self.transport_fields.setObjectName("transportFields")
        self.transport_fields.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.transport_fields.addWidget(self._stdio_fields())
        self.transport_fields.addWidget(self._http_fields())
        outer.addWidget(self.transport_fields)

        boundary = self._new_form()
        self.tier = SelectionBox()
        self.tier.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.tier.setObjectName("tierSelect")
        self.tier.setAccessibleName("신뢰 경계")
        self.tier.addItem("신뢰 경계 선택", None)
        self.tier.addItem("내부 · internal", "internal")
        self.tier.addItem("승인된 외부 · approved_external", "approved_external")
        self.tier.addItem("일반 외부 · general_external", "general_external")
        self._add_field(boundary, "신뢰 경계", self.tier, "tier")
        self.max_label = SelectionBox()
        self.max_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.max_label.setObjectName("maxLabelSelect")
        self.max_label.setAccessibleName("최대 수신 등급")
        self.max_label.addItem("최대 수신 등급 선택", None)
        self.max_label.addItem("공개 · PUBLIC", "PUBLIC")
        self.max_label.addItem("내부 · INTERNAL", "INTERNAL")
        self.max_label.addItem("기밀 · CONFIDENTIAL", "CONFIDENTIAL")
        self._add_field(boundary, "최대 수신 등급", self.max_label, "max_label")
        outer.addLayout(boundary)
        self.boundary_notice = plain_label(name="boundaryNotice")
        outer.addWidget(self.boundary_notice)

        outer.addWidget(plain_label("등록 초안 미리보기", name="sectionTitle"))
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("draftPreview")
        self.preview.setAccessibleName("비밀값을 숨긴 현재 방식의 등록 초안")
        self.preview.setReadOnly(True)
        self.preview.setUndoRedoEnabled(False)
        self.preview.setMinimumHeight(150)
        outer.addWidget(self.preview)
        outer.addWidget(
            plain_label(
                "env_allow에는 환경변수 이름만 적어. "
                "인자와 URL에 인증값을 넣지 말고 별도 비밀 주입 계약을 사용해야 해.",
                name="secondary",
            )
        )
        self.status = plain_label(name="draftStatus")
        outer.addWidget(self.status)
        actions = QHBoxLayout()
        self.prepare_button = KeyboardActionButton("로컬 초안 확인")
        self.prepare_button.set_visual(icon="check", variant="primary")
        self.prepare_button.setObjectName("prepareDraftButton")
        self.prepare_button.setAccessibleName("입력 형식 확인 후 로컬 초안 준비")
        actions.addWidget(self.prepare_button)
        actions.addStretch(1)
        outer.addLayout(actions)
        scroll.setWidget(content)
        shell.addWidget(scroll)

        self.server_id.textChanged.connect(self._input_changed)
        self.display_name.textChanged.connect(self._input_changed)
        self.command.textChanged.connect(self._input_changed)
        self.args_json.textChanged.connect(self._input_changed)
        self.env_allow.textChanged.connect(self._input_changed)
        self.url.textChanged.connect(self._input_changed)
        self.transport.currentIndexChanged.connect(self._transport_changed)
        self.tier.currentIndexChanged.connect(self._tier_changed)
        self.max_label.currentIndexChanged.connect(self._label_changed)
        self.prepare_button.clicked.connect(self._prepare_local_draft)
        self._refresh()

    @property
    def validated_draft(self) -> LocalServerDraft | None:
        """Return the locally checked draft after a click; C must revalidate."""

        return self._validated_draft

    def set_known_ids(self, ids: set[str] | frozenset[str]) -> None:
        """Optional local duplicate hint; backend uniqueness remains authoritative."""

        self._known_ids = frozenset(ids)
        self._input_changed()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = self.width() < 620
        if compact == self._compact_form:
            return
        self._compact_form = compact
        policy = (
            QFormLayout.RowWrapPolicy.WrapAllRows
            if compact
            else QFormLayout.RowWrapPolicy.DontWrapRows
        )
        for form in self._forms:
            form.setRowWrapPolicy(policy)
        margin = 12 if compact else 26
        self._outer.setContentsMargins(margin, 16 if compact else 28, margin, 20 if compact else 28)

    def _new_form(self, parent: QWidget | None = None) -> QFormLayout:
        form = QFormLayout(parent)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._forms.append(form)
        return form

    def _stdio_fields(self) -> QWidget:
        page = QWidget()
        form = self._new_form(page)
        self.command = QLineEdit()
        self.command.setObjectName("commandInput")
        self.command.setAccessibleName("stdio 실행 파일 절대 경로")
        self.command.setPlaceholderText("실행 파일의 절대 경로")
        self._add_field(form, "실행 파일", self.command, "command")
        self.args_json = QPlainTextEdit()
        self.args_json.setObjectName("argsInput")
        self.args_json.setAccessibleName("JSON 문자열 인자 배열")
        self.args_json.setPlaceholderText('["-m", "server.module"]')
        self.args_json.setMaximumHeight(76)
        self.args_json.setTabChangesFocus(True)
        self.args_json.setPlainText("[]")
        self._add_field(form, "인자 · JSON 문자열 배열", self.args_json, "args")
        self.env_allow = QPlainTextEdit()
        self.env_allow.setObjectName("envAllowInput")
        self.env_allow.setAccessibleName("전달할 환경변수 이름 목록, 한 줄에 하나")
        self.env_allow.setPlaceholderText("LANG\nPATH")
        self.env_allow.setMaximumHeight(76)
        self.env_allow.setTabChangesFocus(True)
        self._add_field(form, "env_allow · 한 줄에 이름 하나", self.env_allow, "env_allow")
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
        layout.setSpacing(3)
        layout.addWidget(control)
        error = plain_label(name=f"{key}Error")
        error.setAccessibleName(f"{title} 입력 오류")
        layout.addWidget(error)
        self._errors[key] = error
        form.addRow(title, wrapper)

    def _input_changed(self, *_args: object) -> None:
        self._prepared = False
        self._validated_draft = None
        self._refresh()

    def _transport_changed(self, *_args: object) -> None:
        self.transport_fields.setCurrentIndex(self.transport.currentIndex())
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
        if not server_id:
            errors["server_id"] = "서버 ID를 입력해."
        elif len(server_id) > _MAX_SERVER_ID_CHARS:
            errors["server_id"] = f"서버 ID는 {_MAX_SERVER_ID_CHARS}자 이하로 입력해."
        elif _has_hidden_char(server_id):
            errors["server_id"] = (
                "서버 ID에 보이지 않는 문자나 제어 문자(NUL·방향 전환·zero-width 등)를 넣지 마."
            )
        elif server_id in self._known_ids:
            errors["server_id"] = "현재 목록에 같은 ID가 있어. Registry에서도 다시 확인해야 해."
        elif any(_id_key(server_id) == _id_key(known) for known in self._known_ids):
            errors["server_id"] = (
                "현재 목록에 대소문자·전각 여부만 다른 같은 ID가 있어. 다른 ID를 입력해."
            )
        if not display_name:
            errors["display_name"] = "표시 이름을 입력해."
        elif len(display_name) > _MAX_DISPLAY_NAME_CHARS:
            errors["display_name"] = f"표시 이름은 {_MAX_DISPLAY_NAME_CHARS}자 이하로 입력해."
        elif _has_hidden_char(display_name):
            errors["display_name"] = "표시 이름에 보이지 않는 문자나 제어 문자를 넣지 마."
        if self.tier.currentData() is None:
            errors["tier"] = "신뢰 경계를 선택해."
        if self.max_label.currentData() is None:
            errors["max_label"] = "최대 수신 등급을 선택해."

        transport = str(self.transport.currentData())
        command: str | None = None
        args: tuple[str, ...] | None = None
        env_allow: tuple[str, ...] | None = None
        url: str | None = None
        if transport == "stdio":
            command = self.command.text().strip()
            if not command:
                errors["command"] = "실행 파일 절대 경로를 입력해."
            elif len(command) >= self.command.maxLength():
                errors["command"] = (
                    "실행 파일 경로가 입력 칸 한도를 넘어 잘렸을 수 있어. 경로를 확인해."
                )
            elif (command_error := _command_error(command)) is not None:
                errors["command"] = command_error
            args, args_error = _parse_stdio_args(self.args_json.toPlainText())
            if args_error is not None:
                errors["args"] = args_error
            names = tuple(
                line.strip() for line in self.env_allow.toPlainText().splitlines() if line.strip()
            )
            if any(_ENV_NAME.fullmatch(name) is None for name in names):
                errors["env_allow"] = "이름만 한 줄에 하나씩 적어. 값이나 '='은 넣지 마."
            elif len(names) != len(set(names)):
                errors["env_allow"] = "같은 환경변수 이름을 두 번 적지 마."
            else:
                env_allow = names
        else:
            url = self.url.text().strip()
            if not url:
                errors["url"] = "전체 MCP endpoint URL을 입력해."
            elif len(url) >= self.url.maxLength():
                errors["url"] = "URL이 입력 칸 한도를 넘어 잘렸을 수 있어. 주소를 확인해."
            elif (url_error := _url_error(url)) is not None:
                errors["url"] = url_error

        if errors:
            return None, errors
        return (
            LocalServerDraft(
                server_id=server_id,
                display_name=display_name,
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
        if draft.transport == "stdio":
            shown["command"] = "입력됨 · 값 미표시"
            shown["args"] = f"{len(draft.args or ())}개 · 값 미표시"
            shown["env_allow"] = list(draft.env_allow or ())
        else:
            shown["url"] = "입력됨 · 값 미표시"
        return LocalDraftPreview(
            transport=draft.transport,
            text=json.dumps(shown, ensure_ascii=False, indent=2),
        )

    def _refresh(self) -> None:
        draft, errors = self._build_draft()
        for key, widget in self._errors.items():
            widget.setProperty("validationError", key in errors)
            widget.setVisible(key in errors)
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
            notice = "외부 경계의 최대 수신 등급은 INTERNAL이야."
            if self._boundary_adjusted:
                notice += " CONFIDENTIAL 선택을 INTERNAL로 조정했어."
            self.boundary_notice.setText(notice)
        self.prepare_button.setEnabled(draft is not None and not self._prepared)
        self.preview.setPlainText(
            self._preview_for(draft).text
            if draft is not None
            else "입력 오류를 고치면 미리보기가 표시돼."
        )
        self.status.setText(
            "로컬 초안 확인 완료 · Registry에 등록되지 않았어."
            if self._prepared
            else "입력 형식만 확인해. Registry 등록은 아직 연결되지 않았어."
        )

    def _prepare_local_draft(self) -> None:
        if self._prepared:
            return
        draft, errors = self._build_draft()
        if draft is None or errors:
            self._refresh()
            return
        self._validated_draft = draft
        self._prepared = True
        self._refresh()
        self.localDraftPrepared.emit(self._preview_for(draft))


# ---- 로컬 파일 MCP 서버의 폴더 등급 초안 (contracts 2026-10-15 로컬 파일 접근 결정) ----

_LABELS = (
    ("공개 · PUBLIC", "PUBLIC"),
    ("내부 · INTERNAL", "INTERNAL"),
    ("기밀 · CONFIDENTIAL", "CONFIDENTIAL"),
)


def _folder_error(root: str) -> str | None:
    if _has_hidden_char(root):
        return "폴더 경로에 보이지 않는 문자나 제어 문자를 넣지 마."
    if root[:2] in ("\\\\", "//", "\\/", "/\\"):
        return "네트워크 공유(UNC)·장치 경로는 쓸 수 없어. 이 PC의 로컬 폴더 경로를 입력해."
    if not Path(root).is_absolute():
        return "루트 폴더는 절대 경로여야 해."
    return None


def _prefix_parts(raw: str) -> tuple[tuple[str, ...] | None, str | None]:
    """Lexical segment-prefix check only; C's safe_path resolves real paths."""

    if raw == "":
        return (), None
    if _has_hidden_char(raw):
        return None, "폴더 이름에 보이지 않는 문자나 제어 문자를 넣지 마."
    if "\\" in raw:
        return None, "하위 폴더는 /로 구분해. 역슬래시는 쓰지 마."
    parts = tuple(raw.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        return None, "앞뒤의 /, 빈 이름, 점(.)·점 두 개(..) 없이 루트 기준 상대 경로로 입력해."
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
    removeRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row = QHBoxLayout()
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
        for title, value in _LABELS:
            self.label.addItem(title, value)
        row.addWidget(self.label)
        self.remove_button = KeyboardActionButton("삭제")
        self.remove_button.setAccessibleName("이 폴더 규칙 삭제")
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        self.error = plain_label(name="folderRuleError")
        self.error.setAccessibleName("폴더 규칙 입력 오류")
        self.error.setVisible(False)
        layout.addWidget(self.error)
        self.prefix.textChanged.connect(lambda _text: self.changed.emit())
        self.label.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self))

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

    folderDraftPrepared = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._validated_draft: LocalFolderDraft | None = None
        self._prepared = False
        self.rows: list[_FolderRuleRow] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        outer.addWidget(plain_label("로컬 파일 서버 · 폴더 등급", name="sectionTitle"))
        outer.addWidget(
            plain_label(
                "루트 폴더와 하위 폴더별 등급을 정해. 규칙이 겹치면 더 높은 등급이 적용되고, "
                "어느 규칙에도 맞지 않는 파일을 읽으면 미분류로 작업이 멈춰. "
                "여기서는 입력 형식만 확인하고, 실제 경로 검증은 등록 단계에서 다시 해.",
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
        self._rows_layout.setSpacing(6)
        outer.addLayout(self._rows_layout)
        self.rules_error = plain_label(name="folderRulesError")
        outer.addWidget(self.rules_error)
        actions = QHBoxLayout()
        self.add_button = KeyboardActionButton("폴더 규칙 추가")
        self.add_button.setAccessibleName("하위 폴더 등급 규칙 추가")
        actions.addWidget(self.add_button)
        self.prepare_button = KeyboardActionButton("폴더 등급 초안 확인")
        self.prepare_button.setAccessibleName("입력 형식 확인 후 폴더 등급 초안 준비")
        actions.addWidget(self.prepare_button)
        actions.addStretch(1)
        outer.addLayout(actions)
        self.status = plain_label(name="folderDraftStatus")
        outer.addWidget(self.status)

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
        row.removeRequested.connect(self._remove_rule)
        self.rows.append(row)
        self._rows_layout.addWidget(row)
        self._input_changed()
        return row

    def _remove_rule(self, row: _FolderRuleRow) -> None:
        if row not in self.rows:
            return
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
            root_error: str | None = "루트 폴더 절대 경로를 입력해."
        elif len(root) >= self.root.maxLength():
            root_error = "경로가 입력 칸 한도를 넘어 잘렸을 수 있어. 경로를 확인해."
        else:
            root_error = _folder_error(root)
        self.root_error.setText(root_error or "")
        self.root_error.setVisible(root_error is not None)
        failed = root_error is not None

        rules: list[FolderLabelRule] = []
        # Windows folder names ignore case, so Docs and docs are the same folder.
        seen: set[tuple[str, ...]] = set()
        for row in self.rows:
            parts, error = _prefix_parts(row.prefix.text().strip())
            key = None if parts is None else tuple(part.casefold() for part in parts)
            if key is not None and key in seen:
                error = "같은 폴더 규칙을 두 번 적지 마. 대소문자만 다른 이름도 같은 폴더야."
            elif error is None and row.label.currentData() is None:
                error = "이 폴더의 등급을 선택해."
            row.error.setText(error or "")
            row.error.setVisible(error is not None)
            if key is not None:
                seen.add(key)
            if error is not None or parts is None:
                failed = True
                continue
            rules.append(FolderLabelRule(parts, str(row.label.currentData())))
        rules_error = None if self.rows else "폴더 규칙을 하나 이상 추가해."
        self.rules_error.setText(rules_error or "")
        self.rules_error.setVisible(rules_error is not None)
        if failed or rules_error is not None:
            return None
        return LocalFolderDraft(root, tuple(rules))

    def _refresh(self) -> None:
        draft = self._build_draft()
        self.prepare_button.setEnabled(draft is not None and not self._prepared)
        self.status.setText(
            "폴더 등급 초안 확인 완료 · 아직 등록되지 않았어."
            if self._prepared
            else "입력 형식만 확인해. 등록은 아직 연결되지 않았어."
        )

    def _prepare(self) -> None:
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
        self.folderDraftPrepared.emit(LocalDraftPreview(transport="stdio", text=text))
