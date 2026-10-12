"""Local-only MCP registration draft form.

This widget performs convenience syntax checks. It neither registers a server
nor checks whether an executable or endpoint is safe. C's Registry and
security modules must validate every field again before creating PENDING.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .widgets import KeyboardActionButton, plain_label

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


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

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("serverAddScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(26, 28, 26, 28)
        outer.setSpacing(14)
        outer.addWidget(plain_label("MCP 서버 추가 초안", name="pageTitle"))
        outer.addWidget(
            plain_label(
                "여기서는 입력 형식만 확인해. 등록과 PENDING 상태는 Registry 확인 뒤에만 확정돼.",
                name="secondary",
            )
        )

        common = QFormLayout()
        self.server_id = QLineEdit()
        self.server_id.setObjectName("serverIdInput")
        self.server_id.setAccessibleName("서버 ID")
        self._add_field(common, "서버 ID", self.server_id, "server_id")
        self.display_name = QLineEdit()
        self.display_name.setObjectName("displayNameInput")
        self.display_name.setAccessibleName("표시 이름")
        self._add_field(common, "표시 이름", self.display_name, "display_name")

        self.transport = QComboBox()
        self.transport.setObjectName("transportSelect")
        self.transport.setAccessibleName("연결 방식")
        self.transport.addItem("stdio", "stdio")
        self.transport.addItem("Streamable HTTP", "streamable_http")
        common.addRow("연결 방식", self.transport)
        outer.addLayout(common)

        self.transport_fields = QStackedWidget()
        self.transport_fields.setObjectName("transportFields")
        self.transport_fields.addWidget(self._stdio_fields())
        self.transport_fields.addWidget(self._http_fields())
        outer.addWidget(self.transport_fields)

        boundary = QFormLayout()
        self.tier = QComboBox()
        self.tier.setObjectName("tierSelect")
        self.tier.setAccessibleName("신뢰 경계")
        self.tier.addItem("신뢰 경계 선택", None)
        self.tier.addItem("내부 · internal", "internal")
        self.tier.addItem("승인된 외부 · approved_external", "approved_external")
        self.tier.addItem("일반 외부 · general_external", "general_external")
        self._add_field(boundary, "신뢰 경계", self.tier, "tier")
        self.max_label = QComboBox()
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
                "env_allow에는 환경변수 이름만 적어. 인자와 URL에 인증값을 넣지 말고 별도 비밀 주입 계약을 사용해야 해.",
                name="secondary",
            )
        )
        self.status = plain_label(name="draftStatus")
        outer.addWidget(self.status)
        actions = QHBoxLayout()
        self.prepare_button = KeyboardActionButton("로컬 초안 확인")
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

    def _stdio_fields(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
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
        self.args_json.setPlainText("[]")
        self._add_field(form, "인자 · JSON 문자열 배열", self.args_json, "args")
        self.env_allow = QPlainTextEdit()
        self.env_allow.setObjectName("envAllowInput")
        self.env_allow.setAccessibleName("전달할 환경변수 이름 목록, 한 줄에 하나")
        self.env_allow.setPlaceholderText("LANG\nPATH")
        self.env_allow.setMaximumHeight(76)
        self._add_field(
            form, "env_allow · 한 줄에 이름 하나", self.env_allow, "env_allow"
        )
        return page

    def _http_fields(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.url = QLineEdit()
        self.url.setObjectName("urlInput")
        self.url.setAccessibleName("전체 MCP endpoint URL")
        self.url.setPlaceholderText("https://example.invalid/mcp")
        self._add_field(form, "MCP endpoint URL", self.url, "url")
        return page

    def _add_field(
        self, form: QFormLayout, title: str, control: QWidget, key: str
    ) -> None:
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
        elif server_id in self._known_ids:
            errors["server_id"] = (
                "현재 목록에 같은 ID가 있어. Registry에서도 다시 확인해야 해."
            )
        if not display_name:
            errors["display_name"] = "표시 이름을 입력해."
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
            elif not Path(command).is_absolute():
                errors["command"] = "실행 파일은 절대 경로여야 해."
            try:
                parsed_args = json.loads(self.args_json.toPlainText())
            except (TypeError, ValueError):
                errors["args"] = "JSON 문자열 배열 형식으로 입력해."
            else:
                if not isinstance(parsed_args, list) or not all(
                    isinstance(value, str) for value in parsed_args
                ):
                    errors["args"] = "인자는 문자열만 담은 JSON 배열이어야 해."
                else:
                    args = tuple(parsed_args)
            names = tuple(
                line.strip()
                for line in self.env_allow.toPlainText().splitlines()
                if line.strip()
            )
            if any(_ENV_NAME.fullmatch(name) is None for name in names):
                errors["env_allow"] = (
                    "이름만 한 줄에 하나씩 적어. 값이나 '='은 넣지 마."
                )
            elif len(names) != len(set(names)):
                errors["env_allow"] = "같은 환경변수 이름을 두 번 적지 마."
            else:
                env_allow = names
        else:
            url = self.url.text().strip()
            if not url:
                errors["url"] = "전체 MCP endpoint URL을 입력해."
            elif any(char.isspace() or ord(char) < 32 for char in url):
                errors["url"] = "URL에는 공백이나 제어 문자를 넣지 마."
            else:
                try:
                    parts = urlsplit(url)
                    _port = parts.port
                    valid = (
                        parts.scheme in ("http", "https")
                        and bool(parts.hostname)
                        and "@" not in parts.netloc
                        and not parts.query
                        and not parts.fragment
                    )
                except ValueError:
                    valid = False
                if not valid:
                    errors["url"] = (
                        "http(s) 전체 URL만 입력해. 계정·암호·쿼리·fragment는 제외해."
                    )

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
