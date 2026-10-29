"""A201: advisory scan rendering and review binding; I7/I9/I10, T18/T29.

Only fixed domain reports and Qt signals; no Scanner, credentials or dispatch.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea

from guard_app.domain.contracts import AnalyzerDiagnostic, Finding, ScanReport
from guard_app.domain.enums import ScanErrorCode, TaskStatus
from guard_app.domain.text import defang_scanner_text
from guard_app.services.bridge import (
    ApprovalReview,
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
    LifecycleEvent,
    PolicyServer,
    PolicyTool,
    PolicyView,
    RegistrationView,
    ScanState,
    TaskProjection,
)
from guard_app.ui.models import LoadState, ServerSummary, Surface
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.scan_report import (
    KEY_MISSING_NOTICE,
    RUN_TEXT,
    SERVICE_ERRORS,
    ScanReportPanel,
    ScanReviewDialog,
    ToolScanWarningPanel,
    activation_scan_text,
)
from guard_app.ui.servers import ServerListPage
from guard_app.ui.untrusted_text import UntrustedTextView, untrusted_display_text

from .test_a178_qt_ownership import assert_qt_owned

SUMMARY = defang_scanner_text("<b>x</b> https://evil.example \u202e" + "설명" * 180)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def roots(app):
    widgets = []
    yield widgets
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def bridge(app, monkeypatch):
    bridge = BackendBridge(lambda: None)
    requests = []
    monkeypatch.setattr(
        bridge, "request_registration", lambda request, **kw: requests.append((request, kw))
    )
    yield bridge, requests
    bridge.close()
    bridge.deleteLater()


def report(*, yara_timeout=False):
    diagnostics = [
        AnalyzerDiagnostic(analyzer="LLM", code=ScanErrorCode.SCAN_LLM_KEY_MISSING_OR_INVALID)
    ]
    if yara_timeout:
        diagnostics.append(
            AnalyzerDiagnostic(
                analyzer="YARA", code=ScanErrorCode.SCAN_YARA_TIMEOUT, tool_name="second"
            )
        )
    # B requires diagnostics to belong to failed/skipped analyzers. Test YARA
    # completion and a tool timeout with separate valid reports, never bypass validation.
    return ScanReport(
        server_id="docs",
        fingerprint_set=("a" * 64,),
        analyzers_required=("LLM", "YARA"),
        analyzers_completed=() if yara_timeout else ("YARA",),
        analyzers_failed=("LLM", "YARA") if yara_timeout else ("LLM",),
        analyzers_skipped=(),
        analyzer_diagnostics=tuple(diagnostics),
        max_severity="HIGH",
        findings=(
            Finding(
                analyzer="YARA", source="YARA", severity="HIGH", code="READ_YARA", tool_name="read"
            ),
            Finding(
                analyzer="LLM",
                source="LLM",
                severity="LOW",
                code="READ_LLM",
                tool_name="read",
                overall_risk="INFO",
                threat_summary=SUMMARY,
            ),
            Finding(analyzer="YARA", severity="INFO", code="SECOND_ONLY", tool_name="second"),
            Finding(analyzer="YARA", severity="INFO", code="SERVER_ONLY"),
            Finding(analyzer="YARA", severity="INFO", code="UNKNOWN_TOOL", tool_name="unknown"),
        ),
        scanned_at_utc=datetime(2026, 10, 30, tzinfo=UTC),
        scanner_version="unit-1",
        overall_risk="LOW",
        threat_summary=SUMMARY,
    )


def state(**kwargs):
    return replace(
        ScanState(
            "docs", "done", scan_id="scan-1", report=report(), tools=("read", "second", "empty")
        ),
        **kwargs,
    )


def text(widget):
    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def test_report_plain_text_analyzers_risks_and_summary_cleanup(roots):
    panel = ScanReportPanel()
    roots.append(panel)
    panel.set_state(state())
    assert_qt_owned(roots)
    shown = text(panel)
    for expected in (
        "LLM: 안 됨",
        KEY_MISSING_NOTICE,
        "YARA: 완료",
        "높음 · HIGH",
        "보고된 위험 없음",
        "LLM 자체 평가(참고): INFO",
        "서버 전체 LLM 자체 평가(참고): LOW",
        "서버 전체",
        "SERVER_ONLY",
        "UNKNOWN_TOOL",
        "2026년 10월 30일 09:00:00 KST",
    ):
        assert expected in shown
    assert "안전" not in shown
    assert SUMMARY not in shown
    assert all(
        label.textFormat() == Qt.TextFormat.PlainText for label in panel.findChildren(QLabel)
    )
    summaries = panel.findChildren(UntrustedTextView)
    assert len(summaries) == 2
    for view in summaries:
        assert view.body.toPlainText() == untrusted_display_text(SUMMARY, max_chars=300)[0]
        assert view.notice.isVisibleTo(panel)
    scroll = panel.findChild(QScrollArea)
    assert scroll.minimumHeight() >= 160
    assert scroll.focusPolicy() != Qt.FocusPolicy.NoFocus
    panel.set_state(state(report=report(yara_timeout=True)))
    assert "미완료: YARA · YARA 시간 초과(5초) · SCAN_YARA_TIMEOUT" in text(panel)
    assert all(view.body.toPlainText() == "" for view in summaries)
    summaries = panel.findChildren(UntrustedTextView)
    panel.clear()
    assert all(view.body.toPlainText() == "" for view in summaries)
    assert not panel.findChildren(QLabel)


@pytest.mark.parametrize("code", [*SERVICE_ERRORS, "REG_LISTING_INVALID", "SCAN_OTHER"])
def test_service_errors_are_separate_from_diagnostics(roots, code):
    panel = ScanReportPanel()
    roots.append(panel)
    panel.set_state(state(run="failed", code=code))
    errors = next(
        f for f in panel.findChildren(QFrame) if f.accessibleName() == "Scanner 서비스 오류"
    )
    analyzers = next(f for f in panel.findChildren(QFrame) if f.accessibleName() == "분석기 결과")
    expected = SERVICE_ERRORS.get(
        code,
        "도구 목록을 검사할 수 있는 형식으로 받지 못함. 서버를 확인하세요."
        if code.startswith("REG_LISTING_")
        else "검사를 끝내지 못함. 다시 검사하세요.",
    )
    assert expected in text(errors)
    assert f"코드: {code}" in text(errors)
    assert expected not in text(analyzers)
    assert not errors.isHidden()
    panel.set_state(state(run="done", code=code))
    assert panel.service_errors.isHidden()


@pytest.mark.parametrize("run", list(RUN_TEXT))
def test_run_states_and_missing_report(roots, run):
    panel = ScanReportPanel()
    roots.append(panel)
    panel.set_state(ScanState("docs", run, outdated=True))
    assert RUN_TEXT[run] in text(panel)
    assert "저장된 검사 결과가 지금 도구와 다름. 다시 검사하세요." in text(panel)
    panel.set_state(None)
    assert "검사 기능이 연결되지 않음" in text(panel)


@pytest.mark.parametrize(
    ("scan", "expected"),
    [
        (None, "검사 기능이 연결되지 않음"),
        (ScanState("docs", "queued"), "검사가 아직 끝나지 않음"),
        (ScanState("docs", "running"), "검사가 아직 끝나지 않음"),
        (state(run="running"), "아래 검사 결과를 확인하세요"),
        (ScanState("docs", outdated=True), "저장된 검사 결과가 지금 도구와 다름"),
        (ScanState("docs", "failed"), "검사 결과 없음"),
        (ScanState("docs"), "검사 결과 없음"),
    ],
)
def test_activation_copy(scan, expected):
    assert activation_scan_text(scan).startswith(expected)


@pytest.mark.parametrize("action", ["activate", "block"])
@pytest.mark.parametrize("has_report", [True, False])
def test_activation_attaches_only_for_activation_and_records_shown_scan(
    roots, bridge, action, has_report
):
    backend, requests = bridge
    page = ServerListPage()
    roots.append(page)
    page.attach_activation(backend)
    page.set_servers(
        Surface(LoadState.READY, (ServerSummary("docs", "docs", registry_status="pending"),))
    )
    page.select_server("docs")
    (page.activate_button if action == "activate" else page.block_button).click()
    policy = PolicyView(
        "unit",
        "v",
        300,
        "accumulate",
        (),
        (),
        (PolicyServer("docs", (PolicyTool("read", "read", True),)),),
        change_available=True,
    )
    backend.registration_changed.emit(
        RegistrationView(
            "REG_ACTIVATION_PREVIEW",
            requests[-1][1]["command_id"],
            activation=policy,
            row_revision=7,
            scan=state() if has_report else None,
        )
    )
    dialog = page._activation_dialog
    assert dialog is not None
    assert bool(dialog.findChildren(ScanReportPanel)) == (action == "activate")
    if action == "activate":
        assert dialog.warning.text().splitlines()[0] == activation_scan_text(
            state() if has_report else None
        )
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    request = requests[-1][0]
    assert request.action == action
    assert request.reviewed_scan_id == ("scan-1" if action == "activate" and has_report else None)
    assert request.expected_row_revision == 7


def test_scan_dialog_correlates_signals_and_disconnects(roots, bridge):
    backend, requests = bridge
    dialog = ScanReviewDialog(backend, "docs")
    roots.append(dialog)
    dialog.open()
    assert_qt_owned(roots)
    assert len(requests) == 1
    assert requests[0][0].action == "scan_status"
    assert not dialog.rescan_button.isEnabled()
    command_id = requests[0][1]["command_id"]
    backend.registration_changed.emit(RegistrationView("SCAN_STATUS", "other", scan=state()))
    assert "READ_YARA" not in text(dialog)
    backend.registration_changed.emit(RegistrationView("SCAN_STATUS", command_id, scan=state()))
    assert dialog.result_label.text() == ""
    backend.scan_changed.emit(state(server_id="other", run="running"))
    assert dialog.rescan_button.isEnabled()
    backend.scan_changed.emit(state(run="running"))
    assert not dialog.rescan_button.isEnabled()
    backend.scan_changed.emit(state(run="done"))
    assert dialog.rescan_button.isEnabled()
    dialog.rescan_button.click()
    assert requests[-1][0].action == "scan"
    assert not dialog.rescan_button.isEnabled()
    backend.scan_changed.emit(state())
    assert not dialog.rescan_button.isEnabled()  # Still awaiting our command reply.
    backend.registration_changed.emit(
        RegistrationView("SCAN_STARTED", requests[-1][1]["command_id"], scan=state(run="queued"))
    )
    assert dialog.result_label.text() == "검사를 시작함."
    assert not dialog.rescan_button.isEnabled()
    dialog.close()
    backend.scan_changed.emit(state())
    backend.registration_changed.emit(RegistrationView("SCAN_STATUS", command_id, scan=state()))
    backend.command_changed.emit(
        CommandEvent(
            command_id, "control", "registration", "registry", CommandStage.REJECTED, "fixed"
        )
    )
    assert not dialog.scan_report.findChildren(QLabel)


@pytest.mark.parametrize("stage", [CommandStage.REJECTED, CommandStage.UNCONFIRMED])
def test_scan_dialog_command_failure(roots, bridge, stage):
    backend, requests = bridge
    dialog = ScanReviewDialog(backend, "docs")
    roots.append(dialog)
    command_id = requests[-1][1]["command_id"]
    event = CommandEvent(command_id, "control", "registration", "registry", stage, "fixed")
    backend.command_changed.emit(replace(event, command_id="other"))
    assert dialog.result_label.text() == ""
    backend.command_changed.emit(event)
    assert dialog.result_label.text() == "검사 요청을 처리하지 못함."
    assert dialog.rescan_button.isEnabled()


@pytest.mark.parametrize(
    ("status", "configured", "enabled"),
    [
        ("pending", True, True),
        ("active", True, False),
        ("blocked", True, False),
        ("pending", False, False),
    ],
)
def test_server_scan_button_uses_activation_conditions(roots, bridge, status, configured, enabled):
    backend, requests = bridge
    page = ServerListPage()
    roots.append(page)
    page.attach_activation(backend)
    page.set_servers(
        Surface(
            LoadState.READY,
            (ServerSummary("docs", "docs", registry_status=status, in_settings=configured),),
        )
    )
    page.select_server("docs")
    assert page.scan_button.isEnabled() == enabled
    page.scan_button.click()
    assert len(requests) == int(enabled)
    if enabled:
        previous = page._scan_dialog
        page.scan_button.click()
        assert previous._closed
        assert page._scan_dialog is not previous


def test_runtime_review_filters_to_called_tool_and_preserves_approval(roots, bridge):
    backend, _ = bridge
    panel = RuntimePanel(backend)
    roots.append(panel)
    backend.lifecycle_changed.emit(LifecycleEvent(BridgeState.READY, "READY"))
    backend.task_changed.emit(
        TaskProjection("task-one", 1, TaskStatus.WAITING_APPROVAL, approval_id="approval-one")
    )
    panel._review_command = "review-command"
    review = ApprovalReview(
        "review-command",
        "approval-one",
        "task-one",
        1,
        "fixed",
        b"{}",
        tool_name="read",
        scan=state(reviewed=True),
    )
    backend.review_ready.emit(review)
    warning = panel.scan_warning
    assert warning.isVisibleTo(panel)
    assert "READ_YARA" in text(warning)
    assert "READ_LLM" in text(warning)
    assert "SECOND_ONLY" not in text(warning)
    assert "SERVER_ONLY" not in text(warning)
    assert "UNKNOWN_TOOL" not in text(warning)
    assert "Scanner API 키가 없거나 형식이 맞지 않음" in text(warning)
    assert panel.approve_button.isEnabled()
    backend.review_ready.emit(replace(review, scan=ScanState("docs", code="SCAN_NOT_REVIEWED")))
    assert "검사 결과 없이 활성화됨" in text(warning)
    assert panel.approve_button.isEnabled()
    panel._clear_review()
    assert warning.isHidden()
    assert not warning.findChildren(UntrustedTextView)


def test_tool_warning_missing_reports_and_diagnostics(roots):
    panel = ToolScanWarningPanel()
    roots.append(panel)
    panel.set_review(None, "read")
    assert panel.isHidden()
    panel.set_review(ScanState("docs", code="SCAN_REVIEWED_UNAVAILABLE"), "read")
    assert "활성화 때 확인한 검사 결과를 불러오지 못함." in text(panel)
    panel.set_review(state(report=report(yara_timeout=True)), "second")
    assert "미완료: YARA" in text(panel)
    assert "READ_YARA" not in text(panel)
    clean = ScanReport(**{**report().model_dump(), "analyzer_diagnostics": ()})
    panel.set_review(state(report=clean), "empty")
    assert "활성화 때 확인한 검사에서 이 도구의 경고 없음." in text(panel)
    assert "LLM 검사는 안 됨." in text(panel)
