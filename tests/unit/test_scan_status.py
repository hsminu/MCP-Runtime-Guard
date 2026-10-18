"""A32 display checks; no C Scanner verdict or Registry transition is mocked."""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from guard_app.ui.models import LoadState, Surface
from guard_app.ui.scan_status import ScanStatusCard, ScanStatusProjection


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def test_missing_and_error_surfaces_clear_previous_scan_evidence(app) -> None:
    card = ScanStatusCard()
    assert "조회 연결 대기" in card.state_message.message.text()
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


def test_scan_completion_does_not_imply_registration_or_connection(app) -> None:
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
    card = ScanStatusCard()
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


def test_stale_query_does_not_present_fingerprint_as_current(app) -> None:
    card = ScanStatusCard()
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


def test_unknown_states_and_empty_analyzer_sets_remain_explicit(app) -> None:
    card = ScanStatusCard()
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


def test_finding_summary_is_plain_text_and_projection_is_immutable(app) -> None:
    summary = '<img src="https://example.invalid/pixel"> <b>finding</b>'
    projection = ScanStatusProjection(safe_finding_summary=summary)
    card = ScanStatusCard()
    card.set_snapshot(Surface(LoadState.READY, projection))
    assert card.findings.text() == summary
    assert card.findings.textFormat() == Qt.TextFormat.PlainText
    with pytest.raises(FrozenInstanceError):
        projection.scan_state = "completed"  # type: ignore[misc]
