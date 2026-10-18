"""Read-only Scanner and Registry status from a trusted public projection.

This is an A-owned display shape, not the C Scanner/Registry API. The bridge
must supply only approved analyzer identifiers and a safe finding summary.
No scan result here authorizes activation or changes Registry state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from .models import LoadState, Surface
from .presentation import REGISTRY_STATUSES, SESSION_STATUSES, fixed, format_kst
from .widgets import StateMessage, plain_label

_SCAN_STATES = {
    "not_started": "검사 미실행",
    "requested": "검사 요청 중 · backend 결과 대기",
    "running": "검사 진행 중",
    "completed": "검사 실행 완료 · 관리자 활성화 별도",
    "failed": "검사 실패",
    "malformed": "검사 결과 형식 불명",
}

_SEVERITIES = {
    "INFO": "정보 · INFO",
    "LOW": "낮음 · LOW",
    "MEDIUM": "중간 · MEDIUM",
    "HIGH": "높음 · HIGH",
    "CRITICAL": "매우 높음 · CRITICAL",
}


@dataclass(frozen=True, slots=True)
class ScanStatusProjection:
    """Public values confirmed by the backend, never derived from UI rows.

    ``None`` means not supplied, while an empty analyzer tuple means the
    backend explicitly supplied an empty set. A false fingerprint-stale flag
    is meaningful only for the supplied query snapshot, not future tool sets.
    """

    registry_status: str | None = None
    session_status: str | None = None
    scan_state: str | None = None
    analyzers_required: tuple[str, ...] | None = None
    analyzers_completed: tuple[str, ...] | None = None
    analyzers_skipped: tuple[str, ...] | None = None
    analyzers_failed: tuple[str, ...] | None = None
    fingerprint_present: bool | None = None
    fingerprint_stale: bool | None = None
    scanner_version: str | None = None
    scanned_at: datetime | None = None
    max_severity: str | None = None
    safe_finding_summary: str | None = None


def _analyzer_text(values: tuple[str, ...] | None) -> str:
    if values is None:
        return "미제공"
    if not values:
        return "0개 · backend 제공"
    return f"{len(values)}개 · {', '.join(values)}"


def _fingerprint_text(present: bool | None, stale: bool | None, *, query_stale: bool) -> str:
    if stale is True:
        return "현재 도구 지문과 불일치 · 재검사 필요"
    if present is False:
        return "검사 대상 지문 미수집"
    if present is None:
        return "검사 대상 지문 미확인"
    if query_stale:
        return "검사 대상 지문 수집됨 · 오래된 조회, 현재 상태 재확인 필요"
    if stale is False:
        return "검사 대상 지문 수집됨 · 조회 시점 변경 감지 없음"
    return "검사 대상 지문 수집됨 · 현재 지문 대조 미확인"


class ScanStatusCard(QFrame):
    """Show scan evidence separately from registration and live connection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scanStatusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(plain_label("Scanner · Registry 상태", name="sectionTitle"))
        layout.addWidget(
            plain_label(
                "검사 실행, 등록 활성화, 현재 세션 연결은 각각 다른 상태야.",
                name="secondary",
            )
        )
        self.state_message = StateMessage()
        layout.addWidget(self.state_message)

        self.scan_state = self._field(layout, "검사 진행 상태")
        self.required = self._field(layout, "필수 분석기 · required")
        self.completed = self._field(layout, "완료 분석기 · completed")
        self.skipped = self._field(layout, "건너뛴 분석기 · skipped")
        self.failed = self._field(layout, "실패 분석기 · failed")
        self.fingerprint = self._field(layout, "검사 대상 지문")
        self.scanner_version = self._field(layout, "Scanner 버전")
        self.scanned_at = self._field(layout, "검사 시각")
        self.max_severity = self._field(layout, "보고된 최대 위험도")
        self.findings = self._field(layout, "안전한 finding 요약")
        self.registry_status = self._field(layout, "Registry 등록 상태")
        self.session_status = self._field(layout, "현재 세션 연결 상태")
        self.set_snapshot(Surface())

    @staticmethod
    def _field(layout: QVBoxLayout, title: str) -> QLabel:
        field = QWidget()
        field_layout = QVBoxLayout(field)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(2)
        field_layout.addWidget(plain_label(title, name="secondary"))
        value = plain_label("미제공")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        field_layout.addWidget(value)
        layout.addWidget(field)
        return value

    def set_snapshot(self, surface: Surface[ScanStatusProjection]) -> None:
        """Display a confirmed public query without inferring a scan verdict."""

        self.state_message.set_state(surface.state, as_of=surface.as_of)
        projection = surface.value
        valid = surface.state in (LoadState.READY, LoadState.STALE) and projection is not None
        self.state_message.setVisible(not valid or surface.state == LoadState.STALE)
        if not valid or projection is None:
            if surface.state == LoadState.READY:
                self.state_message.set_message("검사·등록 조회 결과 미제공")
            self._clear()
            return

        self.scan_state.setText(fixed(_SCAN_STATES, projection.scan_state))
        self.required.setText(_analyzer_text(projection.analyzers_required))
        self.completed.setText(_analyzer_text(projection.analyzers_completed))
        self.skipped.setText(_analyzer_text(projection.analyzers_skipped))
        self.failed.setText(_analyzer_text(projection.analyzers_failed))
        self.fingerprint.setText(
            _fingerprint_text(
                projection.fingerprint_present,
                projection.fingerprint_stale,
                query_stale=surface.state == LoadState.STALE,
            )
        )
        self.scanner_version.setText(projection.scanner_version or "미제공")
        self.scanned_at.setText(format_kst(projection.scanned_at))
        self.max_severity.setText(fixed(_SEVERITIES, projection.max_severity))
        self.findings.setText(projection.safe_finding_summary or "미제공")
        self.registry_status.setText(fixed(REGISTRY_STATUSES, projection.registry_status))
        self.session_status.setText(fixed(SESSION_STATUSES, projection.session_status))

    def _clear(self) -> None:
        for value in (
            self.scan_state,
            self.required,
            self.completed,
            self.skipped,
            self.failed,
            self.fingerprint,
            self.scanner_version,
            self.scanned_at,
            self.max_severity,
            self.findings,
            self.registry_status,
            self.session_status,
        ):
            value.setText("미제공")
