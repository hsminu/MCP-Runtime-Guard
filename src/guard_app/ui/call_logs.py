"""Read-only call log presentation from a bridge-approved public projection.

The pending C audit query contract must supply these values. This module never
reads audit storage, accepts a tool body, or infers a security decision from a
missing row. CSV is deliberately limited to the visible public summary fields.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import LoadState, Surface
from .presentation import (
    DECISIONS,
    MISSING,
    REASONS,
    REQUEST_STATUSES,
    TIERS,
    fixed,
    format_kst,
)
from .widgets import KeyboardActionButton, PlainDetailDialog, StateMessage, plain_label

_KST = timezone(timedelta(hours=9), name="KST")
_KINDS = {"READ": "읽기 · READ", "SEND": "전송 · SEND"}
_APPROVAL = {
    "NOT_REQUIRED": "승인 불필요",
    "PENDING": "승인 대기",
    "APPROVED": "승인됨 · 실행 결과와 별개",
    "REJECTED": "거절됨",
    "EXPIRED": "만료됨",
    "INVALIDATED": "무효화됨",
}
_STAGES = {
    "DECIDED": "최초 판정",
    "WAITING_APPROVAL": "승인 대기",
    "DISPATCHING": "전송 예약",
    "DISPATCHED": "호스트 전송",
    "OBSERVED": "결과 수신",
    "COMMITTED": "상태·감사 확정",
    "RELEASED": "결과 공개",
    "WITHHELD": "결과 미공개",
}


@dataclass(frozen=True, slots=True, repr=False)
class PublicCallSummary:
    """Closed UI allowlist; the bridge must exclude raw data before creating it."""

    call_id: str
    request_id: str | None = None
    task_id: str | None = None
    server_id: str | None = None
    tool_name: str | None = None
    kind: str | None = None
    boundary_tier: str | None = None
    initial_decision: str | None = None
    reason_code: str | None = None
    approval_status: str | None = None
    current_status: str | None = None
    occurred_at: datetime | None = None


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
    """One dialog opening; a later opening of the same call gets a new token."""

    call_id: str
    token: int


def _iso_kst(value: datetime | None) -> str:
    """CSV uses an offset-bearing ISO value; a naive date has no guessed zone."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).isoformat(timespec="seconds")


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
            "신뢰 경계",
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
                    fixed(TIERS, row.boundary_tier),
                    fixed(DECISIONS, row.initial_decision),
                    fixed(REASONS, row.reason_code),
                    fixed(_APPROVAL, row.approval_status),
                    fixed(REQUEST_STATUSES, row.current_status),
                )
            )
        )
    return stream.getvalue()


def _detail_text(detail: PublicCallDetail) -> str:
    row = detail.summary
    lines = [
        f"호출 ID: {row.call_id}",
        f"요청 ID: {row.request_id or MISSING}",
        f"Task ID: {row.task_id or MISSING}",
        f"서버 ID: {row.server_id or MISSING}",
        f"도구 이름: {row.tool_name or MISSING}",
        f"종류: {fixed(_KINDS, row.kind)}",
        f"신뢰 경계: {fixed(TIERS, row.boundary_tier)}",
        f"발생 시각: {format_kst(row.occurred_at)}",
        f"최초 판정: {fixed(DECISIONS, row.initial_decision)}",
        f"판정 사유: {fixed(REASONS, row.reason_code)}",
        f"승인 상태: {fixed(_APPROVAL, row.approval_status)}",
        f"현재 실행 상태: {fixed(REQUEST_STATUSES, row.current_status)}",
        "",
        "처리 순서",
    ]
    if detail.stages is None:
        lines.append("단계별 감사 조회 계약 미제공")
    elif not detail.stages:
        lines.append("확인된 단계가 없어")
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
    lines.extend(
        (
            "",
            "안전한 입출력 요약: 조회 계약 미제공",
            "추가 구조화 메타데이터: 조회 계약 미제공",
            "본문·전체 인자·인증정보·예외 원문은 표시하지 않아.",
        )
    )
    return "\n".join(lines)


class CallLogPage(QWidget):
    """Full-width log list and large plain-text dialog, without backend access."""

    detail_requested = Signal(object)
    _COMPACT_VIEWPORT_WIDTH = 760

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[PublicCallSummary, ...] = ()
        self._visible_rows: list[PublicCallSummary] = []
        self._state = LoadState.UNAVAILABLE
        self._detail_dialog: PlainDetailDialog | None = None
        self._detail_request: DetailRequest | None = None
        self._detail_token = 0
        self._compact_columns: bool | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 28, 26, 42)
        layout.setSpacing(14)
        layout.addWidget(plain_label("CALL LOG", name="eyebrow"))
        layout.addWidget(plain_label("도구 호출 로그", name="pageTitle"))
        layout.addWidget(
            plain_label(
                "감사 조회에서 공개가 허용된 식별자와 처리 상태만 표시해.",
                name="secondary",
            )
        )
        self.state_message = StateMessage()
        layout.addWidget(self.state_message)
        self.search = QLineEdit()
        self.search.setAccessibleName("현재 조회 결과에서 호출 검색")
        self.search.setPlaceholderText("호출·요청·Task·서버 ID 또는 도구 이름 검색")
        self.search.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("callLogTable")
        self.table.setAccessibleName("공개 호출 로그 목록")
        self.table.setHorizontalHeaderLabels(
            (
                "발생 시각",
                "도구 / 서버",
                "호출 / 요청",
                "종류",
                "최초 판정",
                "승인",
                "현재 상태",
            )
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.viewport().installEventFilter(self)
        self.table.itemClicked.connect(self._open_selected)
        self.table.itemActivated.connect(self._open_selected)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.detail_button = KeyboardActionButton("선택한 호출 상세 보기")
        self.detail_button.clicked.connect(self._open_selected)
        actions.addWidget(self.detail_button)
        self.csv_button = KeyboardActionButton("현재 표시 행 CSV 복사")
        self.csv_button.clicked.connect(self._copy_csv)
        actions.addWidget(self.csv_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(
            plain_label(
                "CSV에는 현재 조회·필터 결과의 공개 메타데이터만 들어가. "
                "파일 저장 경로는 감사 조회·파일 계약 확정 뒤 연결해.",
                name="secondary",
            )
        )
        self._set_controls_enabled(False)
        self._update_table_columns(force=True)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_table_columns)
        return super().eventFilter(watched, event)

    def _update_table_columns(self, *, force: bool = False) -> None:
        """Keep timestamp, call ID, and outcome visible in a narrow viewport."""

        compact = self.table.viewport().width() < self._COMPACT_VIEWPORT_WIDTH
        if compact == self._compact_columns and not force:
            return
        self._compact_columns = compact
        header = self.table.horizontalHeader()
        for column in (1, 3, 4, 5):
            self.table.setColumnHidden(column, compact)
        id_header = self.table.horizontalHeaderItem(2)
        if id_header is not None:
            id_header.setText("호출 ID" if compact else "호출 / 요청")

        for column in range(7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        if compact:
            self.table.setColumnWidth(0, 155)
            self.table.setColumnWidth(6, 180)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        else:
            for column, width in ((0, 190), (3, 80), (4, 100), (5, 150), (6, 180)):
                self.table.setColumnWidth(column, width)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        for index, row in enumerate(self._visible_rows):
            time_item = self.table.item(index, 0)
            if time_item is not None:
                timestamp = format_kst(row.occurred_at)
                time_item.setText(
                    timestamp.replace("일 ", "일\n", 1) if compact else timestamp
                )
            id_item = self.table.item(index, 2)
            if id_item is not None:
                id_item.setText(
                    row.call_id
                    if compact
                    else f"{row.call_id}\n{row.request_id or MISSING}"
                )
            status_item = self.table.item(index, 6)
            if status_item is not None:
                status = fixed(REQUEST_STATUSES, row.current_status)
                status_item.setText(
                    "결과 불명\n수신측 확인 필요"
                    if compact and row.current_status == "UNKNOWN_OUTCOME"
                    else status
                )

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
        if (
            self._detail_dialog is not None
            and self._detail_request is not None
            and not any(
                row.call_id == self._detail_request.call_id for row in self._rows
            )
        ):
            self._detail_dialog.close()

    def select_call(self, call_id: str) -> None:
        """Select a backend-supplied target if it exists in this query result."""

        for index, row in enumerate(self._visible_rows):
            if row.call_id == call_id:
                self.table.selectRow(index)
                item = self.table.item(index, 0)
                if item is not None:
                    self.table.scrollToItem(item)
                return

    def set_detail(
        self, request: DetailRequest, surface: Surface[PublicCallDetail]
    ) -> None:
        """Apply only the response for this exact dialog opening and call ID."""

        dialog = self._detail_dialog
        if (
            dialog is None
            or type(request) is not DetailRequest
            or request != self._detail_request
        ):
            return
        if surface.state in (LoadState.READY, LoadState.STALE):
            value = surface.value
            if (
                type(value) is PublicCallDetail
                and value.summary.call_id == request.call_id
            ):
                text = _detail_text(value)
                if surface.state == LoadState.STALE:
                    text = "이전 조회 결과 · 다시 확인 필요\n\n" + text
            else:
                text = "호출 상세를 확인할 수 없어"
        else:
            text = StateMessage._MESSAGES.get(surface.state, "상태 확인 필요")
        dialog.body.setPlainText(text)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.search.setEnabled(enabled)
        self.table.setEnabled(enabled)
        self.csv_button.setEnabled(
            enabled and self._state == LoadState.READY and bool(self._visible_rows)
        )
        self.detail_button.setEnabled(enabled and self._selected_call_id() is not None)

    def _apply_filter(self, *_args) -> None:
        selected = self._selected_call_id()
        term = self.search.text().strip().casefold()
        self._visible_rows = [
            row
            for row in self._rows
            if not term
            or term
            in " ".join(
                value or ""
                for value in (
                    row.call_id,
                    row.request_id,
                    row.task_id,
                    row.server_id,
                    row.tool_name,
                )
            ).casefold()
        ]
        self.table.setRowCount(0)
        for index, row in enumerate(self._visible_rows):
            self.table.insertRow(index)
            columns = (
                format_kst(row.occurred_at),
                f"{row.tool_name or MISSING}\n{row.server_id or MISSING}",
                f"{row.call_id}\n{row.request_id or MISSING}",
                fixed(_KINDS, row.kind),
                fixed(DECISIONS, row.initial_decision),
                fixed(_APPROVAL, row.approval_status),
                fixed(REQUEST_STATUSES, row.current_status),
            )
            for column, value in enumerate(columns):
                self.table.setItem(index, column, QTableWidgetItem(value))
            self.table.setRowHeight(index, 58)
            if row.call_id == selected:
                self.table.selectRow(index)
        if self._state == LoadState.READY:
            if not self._rows:
                self.state_message.set_message("조회된 호출이 없어")
            elif not self._visible_rows:
                self.state_message.set_message("현재 조회 결과와 일치하는 호출이 없어")
        self.state_message.setVisible(
            self._state != LoadState.READY or not self._visible_rows
        )
        self._update_table_columns(force=True)
        self._selection_changed()

    def _selected_call_id(self) -> str | None:
        selected = self.table.selectionModel().selectedRows()
        if len(selected) != 1:
            return None
        index = selected[0].row()
        return (
            self._visible_rows[index].call_id
            if 0 <= index < len(self._visible_rows)
            else None
        )

    def _selection_changed(self) -> None:
        self.detail_button.setEnabled(self._selected_call_id() is not None)
        self.csv_button.setEnabled(
            self._state == LoadState.READY and bool(self._visible_rows)
        )

    def _open_selected(self, *_args) -> None:
        call_id = self._selected_call_id()
        if call_id is None:
            return
        if self._detail_dialog is not None:
            self._detail_dialog.close()
        sender = self.sender()
        return_focus = self.table if sender is self.table else self.detail_button
        dialog = PlainDetailDialog(self)
        dialog.finished.connect(
            lambda result, opened=dialog: self._detail_closed(opened, result)
        )
        self._detail_token += 1
        request = DetailRequest(call_id, self._detail_token)
        self._detail_dialog = dialog
        self._detail_request = request
        dialog.show_detail(
            title=f"호출 상세 · {call_id}",
            text="호출 상세 불러오는 중…",
            return_focus=return_focus,
        )
        self.detail_requested.emit(request)

    def _detail_closed(self, dialog: PlainDetailDialog, _result: int) -> None:
        dialog.body.clear()
        if self._detail_dialog is dialog:
            self._detail_dialog = None
            self._detail_request = None

    def _copy_csv(self) -> None:
        if self._state != LoadState.READY or not self._visible_rows:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(public_calls_csv(self._visible_rows))
