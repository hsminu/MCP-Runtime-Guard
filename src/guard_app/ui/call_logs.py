"""Read-only call log presentation from a bridge-approved public projection.

B's Store queries (B6-05) supply these values through the bridge; without a
query source the page shows whatever ``set_calls`` supplied. This module never
reads audit storage, accepts a tool body, or infers a security decision from a
missing row. CSV is deliberately limited to the visible public summary fields.
The boundary tier at decision time is not stored, so no tier is shown (#86).
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import ToolKind

from .approval_presentation import (
    QUERY_TEXT_INVALID,
    QUERY_UNKNOWN,
    SERVER_FILTER_INVALID,
    valid_query_text,
    valid_server_filter,
)
from .models import LoadState, Surface
from .presentation import (
    DECISIONS,
    MISSING,
    REASONS,
    REQUEST_STATUSES,
    fixed,
    format_kst,
)
from .presentation import public_display_text as _public_display_text
from .style import TOKENS, ui_font
from .widgets import (
    KeyboardActionButton,
    ResponsiveRow,
    SelectionBox,
    StateMessage,
    StatusPill,
    plain_label,
    plain_tooltip,
)

_KST = timezone(timedelta(hours=9), name="KST")
NO_SELECTION = "호출을 선택하세요."
_KINDS = {ToolKind.READ.value: "읽기 · READ", ToolKind.SEND.value: "전송 · SEND"}
_APPROVAL = {
    "NOT_REQUIRED": "승인 불필요",
    "PENDING": "승인 대기",
    "APPROVED": "승인됨 · 실행 결과와 별개",
    "CONSUMED": "승인 소비됨 · 실행 결과와 별개",
    "REJECTED": "거절됨",
    "EXPIRED": "만료됨",
    "CANCELLED": "취소됨",
    "INVALIDATED": "무효화됨",
}
# B6-05 stages from the audit events. The Store cannot tell whether a reserved
# call reached the server, and success or release shows in the current status.
_STAGES = {
    "DECIDED": "최초 판정",
    "WAITING_APPROVAL": "승인 대기",
    "DISPATCHING": "전송 예약",
    "OBSERVED": "결과 수신",
    "COMMITTED": "결과 기록 · 성공 여부는 현재 실행 상태로 확인",
    "WITHHELD": "결과 미공개",
}
CALL_PAGE_SIZE = 50
SOURCE_NOT_CONNECTED = "호출 로그 조회가 아직 연결되지 않음."

# Compact table words only; CSV and the detail keep the full public wording.
DECISION_PILLS = {
    "ALLOW": ("허용", "ok"),
    "ASK": ("승인 필요", "attn"),
    "DENY": ("차단", "deny"),
}
APPROVAL_PILLS = {
    "NOT_REQUIRED": ("승인 불필요", "neutral"),
    "PENDING": ("승인 대기", "attn"),
    "APPROVED": ("승인됨", "ok"),
    "CONSUMED": ("승인 사용됨", "ok"),
    "REJECTED": ("거절됨", "deny"),
    "EXPIRED": ("만료됨", "neutral"),
    "CANCELLED": ("취소됨", "neutral"),
    "INVALIDATED": ("무효화됨", "neutral"),
}
STATUS_PILLS = {
    "DECIDED": ("판정 완료", "neutral"),
    "WAITING_APPROVAL": ("승인 대기", "attn"),
    "DISPATCHING": ("실행 중", "live"),
    "SUCCEEDED": ("완료", "ok"),
    "WITHHELD": ("결과 미공개", "attn"),
    "FAILED": ("실패", "deny"),
    "UNKNOWN_OUTCOME": ("결과 불명", "attn"),
    "DENIED": ("차단", "deny"),
}
_UNKNOWN_PILL = ("미확인", "neutral")
_TONE_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallSummary:
    """Closed UI allowlist; the bridge must exclude raw data before creating it."""

    call_id: str
    request_id: str | None = None
    task_id: str | None = None
    server_id: str | None = None
    tool_name: str | None = None
    kind: str | None = None
    initial_decision: str | None = None
    reason_code: str | None = None
    approval_status: str | None = None
    current_status: str | None = None
    occurred_at: datetime | None = None
    # Every recorded reason; reason_code is the first for the table and CSV.
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallStage:
    """A backend-supplied stage; NOT_RUN is distinct from an unknown timestamp."""

    stage_code: str
    state: str
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallDetail:
    """Public summary plus confirmed stages, with no payload or exception text."""

    summary: PublicCallSummary
    stages: tuple[PublicCallStage, ...] | None = None


@dataclass(frozen=True, slots=True)
class DetailRequest:
    """One detail intent; a later selection of the same call gets a new token."""

    call_id: str
    token: int


@dataclass(frozen=True, slots=True)
class CallLogQuery:
    """One list query intent; the token drops replies to older search/page intents."""

    token: int
    search: str
    page: int
    page_size: int = CALL_PAGE_SIZE
    server_id: str = ""


@dataclass(frozen=True, slots=True, repr=False)
class CallLogResult:
    """One backend page and its Store-counted total, not a local row cache."""

    query: CallLogQuery
    rows: tuple[PublicCallSummary, ...]
    total_count: int
    as_of: datetime | None = None


def _iso_kst(value: datetime | None) -> str:
    """CSV uses an offset-bearing ISO value; a naive date has no guessed zone."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).isoformat(timespec="seconds")


def _short_time(value: datetime | None) -> str:
    """Keep date, seconds, and timezone readable in a narrow column."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).strftime("%m-%d\n%H:%M:%S")


def _safe_csv_cell(value: str) -> str:
    """Keep spreadsheet importers from treating external text as a formula."""

    index = 0
    while index < len(value):
        char = value[index]
        if char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}:
            index += 1
        else:
            break
    if (index < len(value) and value[index] in "=+-@") or (
        value and (value[0].isspace() or unicodedata.category(value[0]) in {"Cc", "Cf"})
    ):
        return "'" + value
    return value


def _cell_texts(row: PublicCallSummary) -> tuple[str, ...]:
    """Every table write uses these escaped texts; raw IDs never reach a cell."""

    tool = _public_display_text(row.tool_name)
    server = _public_display_text(row.server_id)
    kind = {ToolKind.READ.value: "읽기", ToolKind.SEND.value: "전송"}.get(row.kind or "", "미확인")
    return (
        _short_time(row.occurred_at),
        f"{tool}\n{server} · {kind}",
        DECISION_PILLS.get(row.initial_decision or "", _UNKNOWN_PILL)[0],
        APPROVAL_PILLS.get(row.approval_status or "", _UNKNOWN_PILL)[0],
        STATUS_PILLS.get(row.current_status or "", _UNKNOWN_PILL)[0],
        _public_display_text(row.call_id),
    )


def public_calls_csv(rows: Sequence[PublicCallSummary]) -> str:
    """Export only the closed public fields of the supplied, already queried rows."""

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(
        (
            "발생 시각 (ISO 8601)",
            "호출 ID",
            "요청 ID",
            "Task ID",
            "서버 ID",
            "도구 이름",
            "종류",
            "최초 판정",
            "판정 사유",
            "승인 상태",
            "현재 실행 상태",
        )
    )
    for row in rows:
        if type(row) is not PublicCallSummary:
            raise TypeError("call log row must be a public summary")
        writer.writerow(
            tuple(
                _safe_csv_cell(value)
                for value in (
                    _iso_kst(row.occurred_at),
                    row.call_id,
                    row.request_id or MISSING,
                    row.task_id or MISSING,
                    row.server_id or MISSING,
                    row.tool_name or MISSING,
                    fixed(_KINDS, row.kind),
                    fixed(DECISIONS, row.initial_decision),
                    fixed(REASONS, row.reason_code),
                    fixed(_APPROVAL, row.approval_status),
                    fixed(REQUEST_STATUSES, row.current_status),
                )
            )
        )
    return stream.getvalue()


def _stage_text(detail: PublicCallDetail) -> str:
    lines: list[str] = []
    if detail.stages is None:
        lines.append("단계별 감사 조회 계약 미제공")
    elif not detail.stages:
        lines.append("확인된 단계가 없음")
    else:
        for stage in detail.stages:
            name = _STAGES.get(stage.stage_code, "미확인 단계")
            if stage.state == "NOT_RUN":
                when = "미실행"
            elif stage.state == "OCCURRED":
                when = format_kst(stage.occurred_at)
            else:
                when = "상태 미확인"
            lines.append(f"{name}: {when}")
    return "\n".join(lines)


class CallCellDelegate(QStyledItemDelegate):
    """Paint only; DisplayRole retains accessible, complete public words."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(option.rect, QColor(TOKENS["selected" if selected else "surface"]))
        if selected and index.column() == 0:
            painter.fillRect(
                option.rect.adjusted(0, 0, 3 - option.rect.width(), 0), QColor(TOKENS["primary"])
            )
        rect = QRectF(option.rect).adjusted(12, 0, -12, 0)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        tone = index.data(_TONE_ROLE)
        if tone:
            self._pill(painter, rect, text, str(tone))
        else:
            lines = text.split("\n")
            for n, line in enumerate(lines):
                tool = index.column() == 1 and n == 0
                font = ui_font(
                    14 if tool else 12,
                    QFont.Weight.DemiBold if tool else QFont.Weight.Normal,
                    mono=index.column() in (0, 5),
                )
                painter.setFont(font)
                painter.setPen(QColor(TOKENS["ink" if tool else "mute"]))
                area = QRectF(rect)
                if len(lines) > 1:
                    area.setTop(rect.top() + 6 + 21 * n)
                    area.setHeight(21)
                shown = QFontMetrics(font).elidedText(
                    line, Qt.TextElideMode.ElideRight, int(rect.width())
                )
                painter.drawText(area, Qt.AlignmentFlag.AlignVCenter, shown)
        painter.restore()

    @staticmethod
    def _pill(painter: QPainter, rect: QRectF, text: str, tone: str) -> None:
        fg = {"ok": "ok", "attn": "warn", "deny": "danger", "live": "primary_text"}.get(
            tone, "mute"
        )
        bg = {
            "ok": "ok_soft",
            "attn": "warn_soft",
            "deny": "danger_soft",
            "live": "primary_soft",
        }.get(tone, "bubble")
        font = ui_font(12, QFont.Weight.DemiBold)
        painter.setFont(font)
        pill = QRectF(
            rect.left(), rect.center().y() - 14, QFontMetrics(font).horizontalAdvance(text) + 32, 28
        )
        color = QColor(TOKENS[fg])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TOKENS[bg]))
        painter.drawRoundedRect(pill, 14, 14)
        painter.setPen(QPen(color, 1.5))
        x, y = pill.left() + 11, pill.center().y()
        if tone == "ok":
            painter.drawLine(QPointF(x - 3, y), QPointF(x - 1, y + 3))
            painter.drawLine(QPointF(x - 1, y + 3), QPointF(x + 4, y - 3))
        elif tone == "attn":
            painter.setBrush(color)
            painter.drawPolygon(
                [QPointF(x, y - 4), QPointF(x + 4, y), QPointF(x, y + 4), QPointF(x - 4, y)]
            )
        elif tone == "deny":
            painter.drawLine(QPointF(x - 3, y - 3), QPointF(x + 3, y + 3))
            painter.drawLine(QPointF(x - 3, y + 3), QPointF(x + 3, y - 3))
        elif tone == "live":
            painter.drawArc(QRectF(x - 4, y - 4, 8, 8), 90 * 16, -270 * 16)
        else:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, y), 2, 2)
        painter.drawText(pill.adjusted(23, 0, -9, 0), Qt.AlignmentFlag.AlignVCenter, text)


class CallDetailPanel(QScrollArea):
    """Only public, selectable plain text; each copy action copies one identifier."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setMinimumSize(0, 100)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 12)
        self.message = plain_label(NO_SELECTION, name="secondary")
        layout.addWidget(self.message)
        self.fields = QWidget()
        layout.addWidget(self.fields)
        fields = QVBoxLayout(self.fields)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        self.title = plain_label("")
        self.title.setStyleSheet("font-size: 17px; font-weight: 600;")
        fields.addWidget(self.title)
        self.subtitle = plain_label("", name="secondary")
        fields.addWidget(self.subtitle)
        fields.addWidget(plain_label("단계", name="sectionTitle"))
        self.pills: list[StatusPill] = []
        self.descriptions: list[QLabel] = []
        for name in ("최초 판정", "승인 상태", "현재 실행 상태"):
            fields.addWidget(plain_label(name, name="secondary"))
            pill = StatusPill()
            fields.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
            self.pills.append(pill)
            description = plain_label("")
            fields.addWidget(description)
            self.descriptions.append(description)
        fields.addWidget(plain_label("처리 순서", name="sectionTitle"))
        self.stages = plain_label("")
        fields.addWidget(self.stages)
        fields.addWidget(plain_label("식별자", name="sectionTitle"))
        self.identifiers: list[QLabel] = []
        self.copy_buttons: list[KeyboardActionButton] = []
        self._ids: tuple[str | None, ...] = ()
        for index, name in enumerate(("호출 ID", "요청 ID", "Task ID")):
            row = QWidget()
            line = QBoxLayout(QBoxLayout.Direction.LeftToRight, row)
            line.setContentsMargins(0, 0, 0, 0)
            value = plain_label("")
            value.setStyleSheet(f"font-family: {TOKENS['mono']}; font-size: 12px;")
            line.addWidget(value, 1)
            button = KeyboardActionButton("복사")
            button.setAccessibleName(f"{name} 복사")
            button.set_visual(icon="copy")
            button.clicked.connect(lambda checked=False, i=index: self._copy_id(i))
            line.addWidget(button)
            fields.addWidget(row)
            self.identifiers.append(value)
            self.copy_buttons.append(button)
        fields.addWidget(plain_label("시각", name="sectionTitle"))
        self.time = plain_label("")
        fields.addWidget(self.time)
        fields.addWidget(
            plain_label("본문·전체 인자·인증정보·예외 원문은 표시하지 않음.", name="secondary")
        )
        for label in self.findChildren(QLabel):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            label.setMinimumWidth(0)
            if not isinstance(label, StatusPill):
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addStretch(1)
        self.show_message(NO_SELECTION)

    def show_message(self, message: str) -> None:
        self._ids = ()
        for label in (
            self.title,
            self.subtitle,
            self.stages,
            self.time,
            *self.descriptions,
            *self.identifiers,
            *self.pills,
        ):
            label.clear()
        for button in self.copy_buttons:
            button.setEnabled(False)
        self.fields.hide()
        self.message.setText(message)
        self.message.show()

    def show_detail(self, detail: PublicCallDetail, *, stale: bool) -> None:
        row = detail.summary
        self.title.setText(_public_display_text(row.tool_name))
        self.subtitle.setText(f"{_public_display_text(row.server_id)} · {fixed(_KINDS, row.kind)}")
        for pill, mapping, code in zip(
            self.pills,
            (DECISION_PILLS, APPROVAL_PILLS, STATUS_PILLS),
            (row.initial_decision, row.approval_status, row.current_status),
            strict=True,
        ):
            pill.set_status(*mapping.get(code or "", _UNKNOWN_PILL))
        self.pills[1].setToolTip("승인은 실행 결과와 별개임")
        self.descriptions[0].setText(
            "판정 사유: "
            + ", ".join(fixed(REASONS, code) for code in (row.reason_codes or (row.reason_code,)))
        )
        self.descriptions[1].setText(f"승인 상태: {fixed(_APPROVAL, row.approval_status)}")
        self.descriptions[2].setText(
            f"현재 실행 상태: {fixed(REQUEST_STATUSES, row.current_status)}"
        )
        self.stages.setText(_stage_text(detail))
        self._ids = (row.call_id, row.request_id, row.task_id)
        for label, button, name, value in zip(
            self.identifiers,
            self.copy_buttons,
            ("호출 ID", "요청 ID", "Task ID"),
            self._ids,
            strict=True,
        ):
            label.setText(f"{name}: {_public_display_text(value)}")
            button.setEnabled(bool(value))
        self.time.setText(f"{format_kst(row.occurred_at)}\n{_iso_kst(row.occurred_at)}")
        self.message.setText("이전 조회 결과 · 다시 확인 필요" if stale else "")
        self.message.setVisible(stale)
        self.fields.show()
        self.verticalScrollBar().setValue(0)

    def _copy_id(self, index: int) -> None:
        if index < len(self._ids) and self._ids[index]:
            QApplication.clipboard().setText(self._ids[index] or "")


class CallLogPage(QWidget):
    """Public list and independently tokened detail panel, without backend access.

    With a query source (``set_source_connected``) the search and pages are
    Store queries sent as ``query_requested``; replies are accepted only
    through ``set_page`` with the exact query. Without one, ``set_calls``
    supplies the rows and the search filters them locally.
    """

    detail_requested = Signal(object)
    query_requested = Signal(object)
    _COMPACT_VIEWPORT_WIDTH = 600
    _NARROW_VIEWPORT_WIDTH = 460

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[PublicCallSummary, ...] = ()
        self._visible_rows: list[PublicCallSummary] = []
        self._state = LoadState.UNAVAILABLE
        self._detail_request: DetailRequest | None = None
        self._detail_token = 0
        self._selected_id: str | None = None
        self._column_mode: str | None = None
        self._source_connected = False
        self._sequence = 0
        self._query: CallLogQuery | None = None
        self._total_count = 0
        self._known_servers: set[str] = set()
        self._manual_widths: dict[int, int] = {}
        self._sizing_columns = False
        self._resizing_header = False
        self._missing_time = 0
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._detail_timer.setInterval(150)
        self._detail_timer.timeout.connect(self._send_detail)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(2000)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)
        layout.addWidget(plain_label("도구 호출 로그", name="pageTitle"))
        self.state_message = StateMessage()
        layout.addWidget(self.state_message)
        filters = ResponsiveRow(breakpoint=600)
        layout.addWidget(filters)
        self.search = QLineEdit()
        self.search.setMinimumWidth(0)
        self.search.setAccessibleName("현재 조회 결과에서 호출 검색")
        self.search.setPlaceholderText("호출·요청·Task·서버 ID 또는 도구 이름 검색")
        self.search.textChanged.connect(self._search_edited)
        self.search.editingFinished.connect(self._search_finished)
        filters.add_widget(self.search, 1)
        choices = ResponsiveRow(breakpoint=420)
        filters.add_widget(choices, 1)
        first_choices, last_choices = QWidget(), QWidget()
        first_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, first_choices)
        last_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, last_choices)
        for group, row in ((first_choices, first_row), (last_choices, last_row)):
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            choices.add_widget(group, 1)
        self.server_filter = SelectionBox()
        self.server_filter.setAccessibleName("서버 선택")
        self.server_filter.addItem("모든 서버", "")
        self.server_filter.currentIndexChanged.connect(self._server_changed)
        self.status_filter = SelectionBox()
        self.status_filter.setAccessibleName("상태 선택")
        for text, value in (
            ("모든 상태", ""),
            ("허용", "ALLOW"),
            ("승인 필요", "ASK"),
            ("차단", "DENY"),
            ("실행 중", "DISPATCHING"),
            ("완료", "SUCCEEDED"),
            ("실패", "FAILED"),
            ("결과 불명", "UNKNOWN_OUTCOME"),
        ):
            self.status_filter.addItem(text, value)
        self.status_filter.currentIndexChanged.connect(self._apply_filter)
        self.period_filter = SelectionBox()
        self.period_filter.setAccessibleName("기간 선택")
        for text, value in (
            ("전체 기간", ""),
            ("최근 1시간", "hour"),
            ("오늘", "today"),
            ("최근 7일", "week"),
        ):
            self.period_filter.addItem(text, value)
        self.period_filter.currentIndexChanged.connect(self._apply_filter)
        choice_metrics = QFontMetrics(ui_font(14))
        for combo in (self.server_filter, self.status_filter, self.period_filter):
            # The longest fixed item stays readable; long server IDs elide.
            combo.setMinimumWidth(
                max(
                    choice_metrics.horizontalAdvance(combo.itemText(i))
                    for i in range(combo.count())
                )
                + 80
            )
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(3)
            combo.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            (last_row if combo is self.period_filter else first_row).addWidget(combo, 1)
        choices.setMinimumWidth(240)
        self.columns_button = KeyboardActionButton("열 선택")
        self.columns_button.set_visual(icon="filter")
        self.columns_menu = QMenu(self.columns_button)
        self.call_id_action = self.columns_menu.addAction("호출 ID")
        self.call_id_action.setCheckable(True)
        self.call_id_action.toggled.connect(self._update_table_columns)
        self.columns_button.setMenu(self.columns_menu)
        last_row.addWidget(self.columns_button)
        self.scope_label = plain_label("모든 필터는 현재 표시된 행에만 적용됨", name="secondary")
        layout.addWidget(self.scope_label)
        self.count_label = plain_label("조회 기능 미연결", name="secondary")
        layout.addWidget(self.count_label)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self.table = QTableWidget(0, 6)
        self.table.setMinimumSize(0, 100)
        self.table.setObjectName("callLogTable")
        self.table.setAccessibleName("공개 호출 로그 목록")
        self.table.setHorizontalHeaderLabels(
            ("발생 시각", "도구 / 서버", "최초 판정", "승인 상태", "현재 상태", "호출 ID")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.setItemDelegate(CallCellDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(80)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.sectionResized.connect(self._section_resized)
        header.viewport().installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table.itemActivated.connect(self._open_selected)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.splitter.addWidget(self.table)
        self.detail_panel = CallDetailPanel()
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([800, 340])
        self.detail_panel.hide()
        self.pager = QWidget()
        pager = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.pager)
        pager.setContentsMargins(0, 0, 0, 0)
        self.previous_button = KeyboardActionButton("이전")
        self.previous_button.clicked.connect(self.previous_page)
        pager.addWidget(self.previous_button)
        self.page_label = plain_label("— / —", name="secondary", wrap=False)
        pager.addWidget(self.page_label)
        self.next_button = KeyboardActionButton("다음")
        self.next_button.clicked.connect(self.next_page)
        pager.addWidget(self.next_button)
        pager.addStretch(1)
        self.refresh_button = KeyboardActionButton("새로고침")
        self.refresh_button.set_visual(icon="refresh")
        self.refresh_button.clicked.connect(self.refresh)
        pager.addWidget(self.refresh_button)
        self.pager.hide()
        layout.addWidget(self.pager)
        actions = QWidget()
        self._actions_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, actions)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(actions)
        self.csv_button = KeyboardActionButton("표시된 0행 CSV 복사")
        self.csv_button.set_visual(icon="copy")
        self.csv_button.clicked.connect(self._copy_csv)
        self._actions_layout.addWidget(self.csv_button)
        self.copy_message = plain_label("", name="secondary")
        self._actions_layout.addWidget(self.copy_message, 1)
        self._copy_timer.timeout.connect(self.copy_message.clear)
        self._set_controls_enabled(False)
        self._update_table_columns()
        self._update_count()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        orientation = Qt.Orientation.Vertical if self.width() < 900 else Qt.Orientation.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes(
                [max(200, self.height() // 2), 240]
                if self.width() < 900
                else [max(400, self.width() - 392), 340]
            )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.horizontalHeader().viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                self._resizing_header = True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._resizing_header = False
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_table_columns)
        return super().eventFilter(watched, event)

    def _section_resized(self, column: int, _old: int, width: int) -> None:
        if not self._sizing_columns and self._resizing_header:
            self._manual_widths[column] = max(120 if column == 1 else 80, width)
            self._update_table_columns()

    def _update_table_columns(self, *_args: object, force: bool = False) -> None:
        if not shiboken6.isValid(self.table):
            return
        width = self.table.viewport().width()
        if width < 100:
            return
        self._column_mode = (
            "narrow"
            if width < self._NARROW_VIEWPORT_WIDTH
            else "medium"
            if width < self._COMPACT_VIEWPORT_WIDTH
            else "full"
        )
        self._sizing_columns = True
        visible = {0, 1, 4}
        if width >= self._NARROW_VIEWPORT_WIDTH:
            visible.add(2)
        if width >= self._COMPACT_VIEWPORT_WIDTH:
            visible.add(3)
        if self.call_id_action.isChecked():
            visible.add(5)
        for column in range(6):
            self.table.setColumnHidden(column, column not in visible)
        metrics = QFontMetrics(ui_font(12, QFont.Weight.DemiBold))
        minimum = {0: 92, 1: 120, 5: 120}
        for column, mapping in ((2, DECISION_PILLS), (3, APPROVAL_PILLS), (4, STATUS_PILLS)):
            minimum[column] = (
                max(metrics.horizontalAdvance(word) for word, _ in mapping.values()) + 56
            )
        spare = max(0, width - sum(minimum[c] for c in visible))
        for column in sorted(visible):
            proposed = minimum[column] + int(
                spare * (0.65 if column == 1 else 0.35 / (len(visible) - 1))
            )
            self.table.setColumnWidth(
                column, max(minimum[column], self._manual_widths.get(column, proposed))
            )
        self._sizing_columns = False
        for index, row in enumerate(self._visible_rows):
            item = self.table.item(index, 4)
            if item is not None:
                tip = fixed(REQUEST_STATUSES, row.current_status)
                if 3 not in visible:
                    tip += (
                        "\n승인 상태: "
                        + APPROVAL_PILLS.get(row.approval_status or "", _UNKNOWN_PILL)[0]
                    )
                    tip += " · 승인은 실행 결과와 별개임"
                item.setToolTip(tip)

    def set_calls(self, surface: Surface[tuple[PublicCallSummary, ...]]) -> None:
        """Replace only the supplied audit page; never invent totals or results."""

        selected = self._selected_call_id()
        scroll = self.table.verticalScrollBar().value()
        self._state = surface.state
        valid = (
            surface.state in (LoadState.READY, LoadState.STALE)
            and type(surface.value) is tuple
            and all(type(row) is PublicCallSummary for row in surface.value)
        )
        if surface.state in (LoadState.READY, LoadState.STALE) and not valid:
            self._state = LoadState.ERROR
        self._rows = surface.value if valid and surface.value is not None else ()
        self.state_message.set_state(self._state, as_of=surface.as_of)
        self._set_controls_enabled(valid)
        self._apply_filter()
        if selected:
            self.select_call(selected)
        self.table.verticalScrollBar().setValue(scroll)
        self._update_servers()
        if selected and selected == self._selected_id:
            self._schedule_detail(selected)

    @property
    def current_query(self) -> CallLogQuery | None:
        return self._query

    @property
    def source_connected(self) -> bool:
        return self._source_connected

    def set_source_connected(self, connected: bool) -> None:
        """Switch between Store pages and supplied rows; never wait without a source."""

        self._source_connected = connected is True
        self._sequence += 1
        self._query = None
        self._total_count = 0
        self._clear_detail(NO_SELECTION)
        self.scope_label.setText(
            "상태·기간은 현재 페이지에만 적용됨"
            if self._source_connected
            else "모든 필터는 현재 표시된 행에만 적용됨"
        )
        self.pager.setVisible(self._source_connected)
        if self._source_connected:
            self.search.setAccessibleName("호출 로그 검색")
            self.search.setPlaceholderText(
                "요청·Task·서버 ID 또는 도구 이름의 앞부분 검색 · 대소문자 구분"
            )
        else:
            self.search.setAccessibleName("현재 조회 결과에서 호출 검색")
            self.search.setPlaceholderText("호출·요청·Task·서버 ID 또는 도구 이름 검색")
        self.set_calls(Surface(LoadState.UNAVAILABLE))
        if not self._source_connected:
            self.state_message.set_message(SOURCE_NOT_CONNECTED)
        self._set_controls_enabled(False)
        self.search.setEnabled(self._source_connected)
        self._update_pager()

    def refresh(self) -> None:
        """Read again from the Store."""

        self._request_page(self._query.page if self._query is not None else 1)

    def next_page(self) -> None:
        if (
            self._state is LoadState.READY
            and self._query is not None
            and self._query.page < self._page_count()
        ):
            self._request_page(self._query.page + 1)

    def previous_page(self) -> None:
        if self._state is LoadState.READY and self._query is not None and self._query.page > 1:
            self._request_page(self._query.page - 1)

    def _search_edited(self, *_args: object) -> None:
        if not self._source_connected:
            self._apply_filter()

    def _search_finished(self) -> None:
        # editingFinished also fires on focus loss; an unchanged search is no new query.
        if self._source_connected and (
            self._query is None or self._query.search != self.search.text().strip()
        ):
            self._request_page(1)

    def _request_page(self, page: int) -> None:
        if not self._source_connected:
            return
        self._sequence += 1
        search = self.search.text().strip()
        self._rows = ()
        self._total_count = 0
        if not valid_query_text(search):
            # Not sent: B accepts only this pattern, and the text stays as typed.
            self._query = None
            self._show_query_state(LoadState.ERROR, QUERY_TEXT_INVALID)
            return
        server = self.server_filter.currentData() or ""
        self._query = CallLogQuery(self._sequence, search, page, server_id=server)
        if not valid_server_filter(server):
            self.set_error(self._query)
            self.state_message.set_message(SERVER_FILTER_INVALID)
            return
        self._show_query_state(LoadState.LOADING, None)
        self.query_requested.emit(self._query)

    def set_page(self, result: CallLogResult) -> bool:
        """Apply only a current, structurally valid backend page."""

        if type(result) is not CallLogResult or result.query != self._query:
            return False
        query = result.query
        if (
            type(result.total_count) is not int
            or result.total_count < 0
            or type(result.rows) is not tuple
            or len(result.rows) > query.page_size
            or not all(type(row) is PublicCallSummary for row in result.rows)
        ):
            self.set_error(query)
            return False
        last_page = max(1, (result.total_count + query.page_size - 1) // query.page_size)
        if query.page > last_page:
            self._request_page(last_page)
            return False
        self._total_count = result.total_count
        self.set_calls(Surface(LoadState.READY, result.rows, as_of=result.as_of))
        self._update_pager()
        return True

    def set_error(self, query: CallLogQuery) -> bool:
        """A failed current query cannot reuse old rows or imply zero results."""

        if query != self._query:
            return False
        self._rows = ()
        self._total_count = 0
        self._show_query_state(LoadState.ERROR, QUERY_UNKNOWN)
        return True

    def _show_query_state(self, state: LoadState, message: str | None) -> None:
        self._state = state
        self.state_message.set_state(state)
        if message is not None:
            self.state_message.set_message(message)
        self._set_controls_enabled(False)
        self._apply_filter()
        self._update_pager()

    def _page_count(self) -> int:
        size = self._query.page_size if self._query is not None else CALL_PAGE_SIZE
        return max(1, (self._total_count + size - 1) // size)

    def _update_pager(self) -> None:
        ready = self._state is LoadState.READY and self._query is not None
        if ready and self._query is not None:
            self.page_label.setText(f"{self._query.page} / {self._page_count()}")
        else:
            self.count_label.setText("총건수 미제공 · 현재 페이지 0건")
            self.page_label.setText("— / —")
        page = self._query.page if self._query is not None else 1
        self.previous_button.setEnabled(ready and page > 1)
        self.next_button.setEnabled(ready and page < self._page_count())
        self.refresh_button.setEnabled(self._source_connected)
        self._update_count()

    def select_call(self, call_id: str) -> None:
        """Select a backend-supplied target if it exists in this query result."""

        for index, row in enumerate(self._visible_rows):
            if row.call_id == call_id:
                self.table.selectRow(index)
                item = self.table.item(index, 0)
                if item is not None:
                    self.table.scrollToItem(item)
                return

    def set_known_servers(self, ids: Sequence[str]) -> None:
        self._known_servers = {value for value in ids if type(value) is str and value}
        self._update_servers()

    def _update_servers(self) -> None:
        current = self.server_filter.currentData() or ""
        values = self._known_servers | {row.server_id for row in self._rows if row.server_id}
        if current:
            values.add(current)
        self.server_filter.blockSignals(True)
        self.server_filter.clear()
        self.server_filter.addItem("모든 서버", "")
        for value in sorted(values):
            self.server_filter.addItem(_public_display_text(value), value)
        self.server_filter.setCurrentIndex(self.server_filter.findData(current))
        self.server_filter.blockSignals(False)

    def _server_changed(self, *_args: object) -> None:
        if self._source_connected:
            self._request_page(1)
        else:
            self._apply_filter()

    def _update_count(self) -> None:
        count = len(self._visible_rows)
        if self._state not in (LoadState.READY, LoadState.STALE):
            text = StateMessage._MESSAGES.get(self._state, "상태 확인 필요")
        elif self._source_connected:
            text = (
                f"전체 {self._total_count:,}건 중 이 페이지 {len(self._rows)}건"
                f" · 필터 후 {count}건 표시"
            )
        else:
            text = f"{count}건 표시 · 받은 행 {len(self._rows)}건"
        if self._state is LoadState.STALE:
            text = "이전 조회 결과 · " + text
        if self._missing_time:
            text += f" · 시각 없음 {self._missing_time}건 제외"
        self.count_label.setText(text)
        self.csv_button.setText(f"표시된 {count}행 CSV 복사")
        self.csv_button.setToolTip(
            (
                "현재 페이지에서 필터를 거친 행만 복사함. 전체 결과가 아님."
                if self._source_connected
                else "현재 표시된 행만 복사함."
            )
            + "\n파일로 저장하는 기능은 아직 없음"
        )
        self.csv_button.setEnabled(self._state is LoadState.READY and bool(count))

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.search.setEnabled(enabled or self._source_connected)
        self.server_filter.setEnabled(enabled or self._source_connected)
        self.status_filter.setEnabled(enabled)
        self.period_filter.setEnabled(enabled)
        self.table.setEnabled(enabled)
        self.csv_button.setEnabled(
            enabled and self._state is LoadState.READY and bool(self._visible_rows)
        )

    def _apply_filter(self, *_args: object) -> None:
        selected = self._selected_id
        term = "" if self._source_connected else self.search.text().strip().casefold()
        server = "" if self._source_connected else self.server_filter.currentData()
        status = self.status_filter.currentData()
        period = self.period_filter.currentData()
        now = datetime.now(_KST)
        cutoff = (
            now - timedelta(hours=1)
            if period == "hour"
            else now.replace(hour=0, minute=0, second=0, microsecond=0)
            if period == "today"
            else now - timedelta(days=7)
        )
        self._missing_time = 0
        rows = []
        for row in self._rows:
            if (
                term
                and term
                not in " ".join(
                    value or ""
                    for value in (
                        row.call_id,
                        row.request_id,
                        row.task_id,
                        row.server_id,
                        row.tool_name,
                    )
                ).casefold()
            ):
                continue
            if server and row.server_id != server:
                continue
            if (
                status
                and (row.initial_decision if status in DECISION_PILLS else row.current_status)
                != status
            ):
                continue
            if period:
                when = row.occurred_at
                if when is None or when.tzinfo is None or when.utcoffset() is None:
                    self._missing_time += 1
                    continue
                if not cutoff <= when <= now:
                    continue
            rows.append(row)
        self.table.blockSignals(True)
        self._visible_rows = rows
        self.table.setRowCount(0)
        for index, row in enumerate(rows):
            self.table.insertRow(index)
            for column, value in enumerate(_cell_texts(row)):
                item = QTableWidgetItem(value)
                # 도구 이름은 MCP 서버가 정한다. 태그로 보이는 이름도 서식 없이 보인다.
                item.setToolTip(plain_tooltip(value))
                self.table.setItem(index, column, item)
            for column, mapping, code in (
                (2, DECISION_PILLS, row.initial_decision),
                (3, APPROVAL_PILLS, row.approval_status),
                (4, STATUS_PILLS, row.current_status),
            ):
                status_item = self.table.item(index, column)
                if status_item is not None:
                    status_item.setData(_TONE_ROLE, mapping.get(code or "", _UNKNOWN_PILL)[1])
            time_item = self.table.item(index, 0)
            if time_item is not None:
                time_item.setToolTip(f"{format_kst(row.occurred_at)}\n{_iso_kst(row.occurred_at)}")
            approval_item = self.table.item(index, 3)
            if approval_item is not None:
                approval_item.setToolTip(
                    f"{fixed(_APPROVAL, row.approval_status)}\n승인은 실행 결과와 별개임"
                )
            self.table.setRowHeight(index, 52)
            if row.call_id == selected:
                self.table.selectRow(index)
        self.table.blockSignals(False)
        if selected and not any(row.call_id == selected for row in rows):
            self._clear_detail("선택한 호출이 현재 결과에 없음")
        if self._state is LoadState.READY:
            if not self._rows and self._query is not None and self._query.search:
                self.state_message.set_message("검색 결과가 없음")
            elif not self._rows:
                self.state_message.set_message("조회된 호출이 없음")
            elif not rows:
                self.state_message.set_message("현재 조회 결과와 일치하는 호출이 없음")
        self.state_message.setVisible(self._state is not LoadState.READY or not rows)
        self._update_table_columns()
        self._update_count()

    def _selected_call_id(self) -> str | None:
        selected = self.table.selectionModel().selectedRows()
        if len(selected) != 1:
            return None
        index = selected[0].row()
        return self._visible_rows[index].call_id if 0 <= index < len(self._visible_rows) else None

    def _selection_changed(self) -> None:
        call_id = self._selected_call_id()
        if call_id != self._selected_id:
            if call_id is None:
                self._clear_detail(NO_SELECTION)
            else:
                self._schedule_detail(call_id)

    def _schedule_detail(self, call_id: str) -> None:
        self._detail_request = None
        self._selected_id = call_id
        self.detail_panel.show()
        self.detail_panel.show_message("호출 상세 불러오는 중…")
        self._detail_timer.start()

    def _clear_detail(self, message: str) -> None:
        self._detail_timer.stop()
        self._detail_request = None
        self._selected_id = None
        self.detail_panel.show_message(message)
        # The panel opens with a selection; empty, it would only take table width.
        self.detail_panel.setVisible(message != NO_SELECTION)

    def _open_selected(self, *_args: object) -> None:
        call_id = self._selected_call_id()
        if call_id is not None:
            self._schedule_detail(call_id)

    def _send_detail(self) -> None:
        call_id = self._selected_call_id()
        if call_id is None or call_id != self._selected_id:
            return
        self._detail_token += 1
        self._detail_request = DetailRequest(call_id, self._detail_token)
        self.detail_requested.emit(self._detail_request)

    def set_detail(self, request: DetailRequest, surface: Surface[PublicCallDetail]) -> None:
        if type(request) is not DetailRequest or request != self._detail_request:
            return
        if surface.state in (LoadState.READY, LoadState.STALE):
            value = surface.value
            if type(value) is PublicCallDetail:
                if value.summary.call_id != request.call_id:
                    return
                self.detail_panel.show_detail(value, stale=surface.state is LoadState.STALE)
            elif value is None and surface.state is LoadState.READY:
                self.detail_panel.show_message("해당 호출 없음")
            else:
                self.detail_panel.show_message("호출 상세를 확인할 수 없음")
        else:
            self.detail_panel.show_message(
                StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
            )

    def _copy_csv(self) -> None:
        if self._state is not LoadState.READY or not self._visible_rows:
            return
        QApplication.clipboard().setText(public_calls_csv(self._visible_rows))
        self.copy_message.setText(f"{len(self._visible_rows)}행 복사함")
        self._copy_timer.start()
