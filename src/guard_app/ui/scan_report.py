"""Advisory Scanner UI; never gates activation, approval or Guard decisions.

Keep service errors separate from report diagnostics. Model summaries are
unverified plain text capped at 300 characters, without links or logging.
A report reads top-down: one headline, then each tool's findings as C's plain
categories (C4.22) with a meaning and an action. Analyzer names, raw codes,
the model's self-assessment and summaries stay behind a folded details toggle.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.contracts import AnalyzerDiagnostic, Finding, ScanReport
from guard_app.services.bridge import (
    BackendBridge,
    CommandEvent,
    CommandStage,
    RegistrationRequest,
    RegistrationView,
    ScanCategory,
    ScanState,
    new_command_id,
    scan_category,
)

from .presentation import format_kst, public_display_text
from .reviewed_descriptions import DescriptionReviewDialog
from .untrusted_text import UntrustedTextView
from .widgets import KeyboardActionButton, plain_label, size_detail_for_viewport

RUN_TEXT = {
    "none": "이번 실행에서 검사하지 않음",
    "queued": "검사 대기 중",
    "running": "검사 중 · 최대 10분",
    "done": "검사 끝남",
    "failed": "검사하지 못함",
}
SERVICE_ERRORS = {
    "SCAN_QUEUE_FULL": "검사 대기열이 가득 차 접수하지 않음. 잠시 뒤 다시 검사하세요.",
    "SCAN_SERVICE_CLOSED": "앱이 종료 중이라 검사하지 않음.",
    "SCAN_SERVER_UNKNOWN": "등록되지 않은 서버임. 서버 목록을 다시 확인하세요.",
    "SCAN_LISTING_NOT_REGISTERED": (
        "도구 목록이 등록 내용과 달라 검사하지 않음. 서버 상태를 다시 확인하세요."
    ),
    "SCAN_JOB_TIMEOUT": "검사기가 응답하지 않아 중단함. 다시 검사하세요.",
    "SCAN_RECORD_UNKNOWN": "검사 결과 저장을 확인하지 못함. 다시 검사하세요.",
    "SCAN_SERVER_UNREACHABLE": (
        "서버에 연결하지 못해 검사하지 않음. 서버를 확인한 뒤 다시 검사하세요."
    ),
    "SCAN_COLLECT_TIMEOUT": "도구 목록을 제때 받지 못해 검사하지 않음. 다시 검사하세요.",
    "SCAN_CANCELLED": "검사가 취소됨. 다시 검사하세요.",
    "SCAN_UNCONFIRMED": "검사 결과를 확인하지 못함. 다시 검사하세요.",
}
DIAGNOSTICS = {
    "SCAN_LLM_KEY_MISSING_OR_INVALID": "Scanner API 키가 없거나 형식이 맞지 않음",
    "SCAN_LLM_AUTH_FAILED": "Scanner API 키 인증 실패",
    "SCAN_LLM_RATE_LIMITED": "LLM 요청 한도 초과",
    "SCAN_LLM_TIMEOUT": "LLM 응답 시간 초과",
    "SCAN_YARA_TIMEOUT": "YARA 시간 초과(5초)",
    "SCAN_INCOMPLETE": "검사 시간(10분) 안에 끝나지 않음",
    "SCAN_METADATA_TOO_LARGE": "도구 정의가 너무 커서 검사하지 않음(64KiB)",
}
RISKS = {"CRITICAL": "매우 높음", "HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음", "INFO": "정보"}
RISK_ORDER = {risk: rank for rank, risk in enumerate(reversed(RISKS), start=1)}
KEY_MISSING_NOTICE = (
    "LLM 검사 안 됨 · YARA 결과만 있음. 설정에서 Scanner API 키를 저장하면 "
    "다음 검사부터 LLM도 검사함."
)
ADVISORY_NOTICE = "Scanner 경고는 활성화·승인을 막지 않음. 내용을 보고 직접 판단하세요."
NO_FINDINGS = "보고된 위험 없음 · 위험이 없다는 보장은 아님"
INCOMPLETE_NOTICE = "끝내지 못한 검사가 있어 결과가 일부만 있음"
SERVER_WIDE = "서버 전체"


def _plain_findings(findings: Sequence[Finding]) -> list[tuple[ScanCategory, str]]:
    """One line per category with its highest risk, highest first (LLM·YARA together)."""
    highest: dict[str, tuple[ScanCategory, str]] = {}
    for finding in findings:
        category = scan_category(finding.code)
        kept = highest.get(category.name)
        if kept is None or RISK_ORDER.get(finding.severity, 0) > RISK_ORDER.get(kept[1], 0):
            highest[category.name] = (category, finding.severity)
    return sorted(highest.values(), key=lambda item: -RISK_ORDER.get(item[1], 0))


def scan_headline(groups: Sequence[tuple[str, Sequence[Finding]]]) -> str:
    """Count of plain lines and the tools they belong to; never says the server is safe."""
    flagged = [(tool, len(_plain_findings(findings))) for tool, findings in groups if findings]
    count = sum(lines for _, lines in flagged)
    if not count:
        return NO_FINDINGS
    names = [public_display_text(tool) for tool, _ in flagged]
    more = f" 외 {len(names) - 3}개" if len(names) > 3 else ""
    return f"주의할 점 {count}개 · {', '.join(names[:3])}{more}"


def _add_plain_finding(
    layout: QVBoxLayout, category: ScanCategory, severity: str, *, action: bool = True
) -> None:
    layout.addWidget(plain_label(f"{category.name} · 위험도 {RISKS.get(severity, '알 수 없음')}"))
    layout.addWidget(plain_label(category.meaning, name="secondary"))
    if action:
        layout.addWidget(plain_label(f"권장: {category.action}", name="secondary"))


def _add_details(layout: QVBoxLayout) -> tuple[KeyboardActionButton, QVBoxLayout]:
    """Raw codes, analyzers and model text, folded until the user opens them."""
    toggle = KeyboardActionButton("자세히 ▸")
    toggle.setObjectName("linkButton")
    toggle.setCheckable(True)
    toggle.setAutoDefault(False)
    toggle.setAccessibleName("검사 자세히 펼치기")
    frame = QFrame()
    frame.setAccessibleName("검사 자세히")
    layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(frame)
    details = QVBoxLayout(frame)
    frame.setVisible(False)

    def flip(opened: bool) -> None:
        frame.setVisible(opened)
        toggle.setText(f"자세히 {'▾' if opened else '▸'}")
        toggle.setAccessibleName(f"검사 자세히 {'접기' if opened else '펼치기'}")

    toggle.toggled.connect(flip)
    return toggle, details


def _diagnostic_text(diagnostic: AnalyzerDiagnostic) -> str:
    code = diagnostic.code.value
    fallback = (
        "LLM 검사 실패"
        if code.startswith("SCAN_LLM_")
        else "YARA 검사 실패"
        if code.startswith("SCAN_YARA_")
        else "검사 미완료"
    )
    return f"{DIAGNOSTICS.get(code, fallback)} · {code}"


def _risk_text(severity: str) -> str:
    return f"{RISKS.get(severity, '알 수 없음')} · {public_display_text(severity)}"


def _clear_content(layout: QVBoxLayout) -> None:
    while (item := layout.takeAt(0)) is not None:
        widget = item.widget()
        if widget is not None:
            for view in widget.findChildren(UntrustedTextView):
                view.clear()
            if isinstance(widget, UntrustedTextView):
                widget.clear()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def _add_summary(layout: QVBoxLayout, summary: str, name: str) -> None:
    if summary:
        view = UntrustedTextView(name, max_chars=300, show_unverified_notice=True)
        layout.addWidget(view)
        view.set_text(summary)


def _add_finding(layout: QVBoxLayout, finding: Finding, tool: str) -> None:
    layout.addWidget(
        plain_label(
            f"{public_display_text(finding.source or finding.analyzer)} · "
            f"위험도 {_risk_text(finding.severity)} · 코드 {public_display_text(finding.code)}"
        )
    )
    if finding.overall_risk is not None:
        layout.addWidget(
            plain_label(f"LLM 자체 평가(참고): {public_display_text(finding.overall_risk)}")
        )
    _add_summary(layout, finding.threat_summary, f"모델 설명 · {public_display_text(tool)}")


def _add_diagnostic(layout: QVBoxLayout, diagnostic: AnalyzerDiagnostic) -> None:
    layout.addWidget(
        plain_label(
            f"미완료: {public_display_text(diagnostic.analyzer)} · {_diagnostic_text(diagnostic)}"
        )
    )


class ScanReportPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self.set_state(None)

    def clear(self) -> None:
        _clear_content(self._layout)

    def set_loading(self) -> None:
        self.clear()
        self._layout.addWidget(plain_label("검사 결과 확인 중", name="runtimeMessage"))

    def set_failed(self, text: str) -> None:
        self.clear()
        self._layout.addWidget(plain_label(text, name="runtimeMessage"))

    def set_state(self, state: ScanState | None) -> None:
        self.clear()
        layout = self._layout
        layout.addWidget(
            plain_label(
                RUN_TEXT.get(state.run, "검사 상태 미확인")
                if state is not None
                else "검사 기능이 연결되지 않음",
                name="runtimeMessage",
            )
        )
        self.service_errors = QFrame()
        self.service_errors.setAccessibleName("Scanner 서비스 오류")
        errors = QVBoxLayout(self.service_errors)
        layout.addWidget(self.service_errors)
        failed = state is not None and state.run == "failed"
        self.service_errors.setVisible(failed)
        if failed and state is not None:
            fallback = (
                "도구 목록을 검사할 수 있는 형식으로 받지 못함. 서버를 확인하세요."
                if state.code.startswith("REG_LISTING_")
                else "검사를 끝내지 못함. 다시 검사하세요."
            )
            errors.addWidget(plain_label(SERVICE_ERRORS.get(state.code, fallback)))
            errors.addWidget(plain_label(f"코드: {state.code}", name="secondary"))
        if state is None or state.report is None:
            layout.addWidget(
                plain_label(
                    "저장된 검사 결과가 지금 도구와 다름. 다시 검사하세요."
                    if state is not None and state.outdated
                    else "표시할 검사 결과 없음"
                )
            )
        else:
            self._add_report(state, state.report)
        layout.addWidget(plain_label(ADVISORY_NOTICE, name="secondary"))

    def _add_report(self, state: ScanState, report: ScanReport) -> None:
        layout = self._layout
        groups = [
            (tool, tuple(f for f in report.findings if f.tool_name == tool)) for tool in state.tools
        ]
        unmatched = tuple(f for f in report.findings if f.tool_name not in state.tools)
        if unmatched:
            groups.append((SERVER_WIDE, unmatched))
        layout.addWidget(plain_label(scan_headline(groups), name="sectionTitle"))
        diagnostics = tuple(d for d in report.analyzer_diagnostics if d.tool_name is None)
        if set(report.analyzers_required) - set(report.analyzers_completed):
            layout.addWidget(plain_label(INCOMPLETE_NOTICE, name="runtimeMessage"))
        if any(
            d.analyzer == "LLM" and d.code == "SCAN_LLM_KEY_MISSING_OR_INVALID" for d in diagnostics
        ):
            layout.addWidget(plain_label(KEY_MISSING_NOTICE, name="runtimeMessage"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(160)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        body = QWidget()
        cards = QVBoxLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        for tool, findings in groups:
            self._add_tool_card(cards, tool, findings, report, server_wide=tool is SERVER_WIDE)
        cards.addStretch(1)
        self.details_toggle, folded = _add_details(layout)
        folded.addWidget(
            plain_label(
                f"검사 시각 {format_kst(report.scanned_at_utc)} · "
                f"Scanner {public_display_text(report.scanner_version)}",
                name="secondary",
            )
        )
        analyzers = QFrame()
        analyzers.setAccessibleName("분석기 결과")
        analyzer_layout = QVBoxLayout(analyzers)
        folded.addWidget(analyzers)
        for analyzer in report.analyzers_required:
            details = " · ".join(_diagnostic_text(d) for d in diagnostics if d.analyzer == analyzer)
            status = (
                "완료"
                if analyzer in report.analyzers_completed
                else "건너뜀"
                if analyzer in report.analyzers_skipped
                else "안 됨"
            )
            analyzer_layout.addWidget(
                plain_label(
                    f"{public_display_text(analyzer)}: {status}"
                    + (f" · {details}" if details else "")
                )
            )
        if report.overall_risk is not None:
            folded.addWidget(
                plain_label(
                    f"서버 전체 LLM 자체 평가(참고): {public_display_text(report.overall_risk)}"
                )
            )
        _add_summary(folded, report.threat_summary, "모델 설명 · 서버 전체")
        for tool, findings in groups:
            unfinished = (
                ()
                if tool is SERVER_WIDE
                else tuple(d for d in report.analyzer_diagnostics if d.tool_name == tool)
            )
            if findings or unfinished:
                folded.addWidget(plain_label(public_display_text(tool), name="sectionTitle"))
            for diagnostic in unfinished:
                _add_diagnostic(folded, diagnostic)
            for finding in findings:
                _add_finding(folded, finding, tool)

    @staticmethod
    def _add_tool_card(
        cards: QVBoxLayout,
        tool: str,
        findings: tuple[Finding, ...],
        report: ScanReport,
        *,
        server_wide: bool = False,
    ) -> None:
        card = QFrame()
        layout = QVBoxLayout(card)
        cards.addWidget(card)
        layout.addWidget(plain_label(public_display_text(tool), name="sectionTitle"))
        plain = _plain_findings(findings)
        for category, severity in plain:
            _add_plain_finding(layout, category, severity)
        if not plain:
            unfinished = not server_wide and any(
                d.tool_name == tool for d in report.analyzer_diagnostics
            )
            layout.addWidget(
                plain_label("이 도구는 검사를 끝내지 못함" if unfinished else "보고된 위험 없음")
            )


def activation_scan_text(scan: ScanState | None) -> str:
    if scan is None:
        return "검사 기능이 연결되지 않음. 검사 결과 없이 확인하는 단계임."
    if scan.run in ("queued", "running") and scan.report is None:
        return (
            "검사가 아직 끝나지 않음. 결과 없이 활성화할 수 있음. "
            "결과를 보려면 검사가 끝난 뒤 다시 여세요."
        )
    if scan.report is not None:
        return "아래 검사 결과를 확인하세요. 확인한 검사 기록이 활성화와 함께 저장됨."
    if scan.outdated:
        return (
            "저장된 검사 결과가 지금 도구와 다름. 결과 없이 활성화할 수 있음. 먼저 다시 검사하세요."
        )
    return (
        "검사 결과 없음. 결과 없이 활성화할 수 있음. "
        "먼저 검사하려면 '검사 결과·설명 검토'에서 검사하세요."
    )


def attach_activation_scan(dialog: QDialog, warning: QLabel, scan: ScanState | None) -> str | None:
    panel = ScanReportPanel(dialog)
    panel.set_state(scan)
    layout = dialog.layout()
    if not isinstance(layout, QBoxLayout):
        raise TypeError("Activation dialog requires a box layout")
    layout.insertWidget(layout.indexOf(warning), panel)
    dialog.scan_report = panel  # type: ignore[attr-defined]
    dialog.finished.connect(panel.clear)
    return scan.scan_id if scan is not None and scan.report is not None else None


class ToolScanWarningPanel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("호출 도구의 Scanner 경고")
        self._layout = QVBoxLayout(self)
        self.hide()

    def clear(self) -> None:
        _clear_content(self._layout)
        self.hide()

    def set_review(self, scan: ScanState | None, tool_name: str) -> None:
        self.clear()
        if scan is None:
            return
        layout = self._layout
        if scan.code == "SCAN_NOT_REVIEWED":
            layout.addWidget(plain_label("이 서버는 검사 결과 없이 활성화됨."))
        elif scan.code == "SCAN_REVIEWED_UNAVAILABLE":
            layout.addWidget(plain_label("활성화 때 확인한 검사 결과를 불러오지 못함."))
        elif scan.report is not None:
            report = scan.report
            findings = tuple(f for f in report.findings if f.tool_name == tool_name)
            diagnostics = tuple(
                d for d in report.analyzer_diagnostics if d.tool_name in (None, tool_name)
            )
            plain = _plain_findings(findings)
            if plain:
                layout.addWidget(
                    plain_label(
                        f"이 도구의 Scanner 경고 {len(plain)}개 · 승인을 막지 않음",
                        name="sectionTitle",
                    )
                )
                for category, severity in plain:
                    _add_plain_finding(layout, category, severity, action=False)
            elif not diagnostics:
                layout.addWidget(plain_label("활성화 때 확인한 검사에서 이 도구의 경고 없음."))
            if "LLM" not in report.analyzers_completed:
                layout.addWidget(plain_label("LLM 검사는 안 됨."))
            if diagnostics:
                layout.addWidget(plain_label("끝내지 못한 검사가 있음."))
            if diagnostics or findings:
                self.details_toggle, folded = _add_details(layout)
                for diagnostic in diagnostics:
                    _add_diagnostic(folded, diagnostic)
                for finding in findings:
                    _add_finding(folded, finding, tool_name)
        else:
            layout.addWidget(plain_label("표시할 검사 결과 없음"))
        self.show()


class ScanReviewDialog(QDialog):
    def __init__(
        self, bridge: BackendBridge, server_id: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._server_id = server_id
        self._command_id = ""
        self._running = False
        self._closed = False
        self.setWindowTitle(f"검사 결과 · {public_display_text(server_id)}")
        size_detail_for_viewport(self, 760, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(
            plain_label(
                "PENDING 서버의 도구 정의를 검사함. 검사는 서버를 실행해 도구 목록을 받음.",
                name="secondary",
            )
        )
        self.scan_report = ScanReportPanel()
        layout.addWidget(self.scan_report)
        self.result_label = plain_label("", name="runtimeMessage")
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.rescan_button = KeyboardActionButton("다시 검사")
        self.rescan_button.clicked.connect(lambda: self._send("scan"))
        buttons.addWidget(self.rescan_button)
        self._description_dialog: DescriptionReviewDialog | None = None
        self.describe_button = KeyboardActionButton("설명 검토")
        self.describe_button.clicked.connect(self._describe)
        buttons.addWidget(self.describe_button)
        close = KeyboardActionButton("닫기")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        bridge.registration_changed.connect(self._registration)
        bridge.command_changed.connect(self._command)
        bridge.scan_changed.connect(self._scan)
        self.finished.connect(self._finish)
        self._send("scan_status")

    def _describe(self) -> None:
        self._description_dialog = DescriptionReviewDialog(self._bridge, self._server_id, self)
        self._description_dialog.open()

    def _send(self, action: str) -> None:
        if self._closed or self._command_id:
            return
        self._command_id = new_command_id()
        self.rescan_button.setEnabled(False)
        self._bridge.request_registration(
            RegistrationRequest(action, self._server_id), command_id=self._command_id
        )

    def _registration(self, view: RegistrationView) -> None:
        if self._closed or not self._command_id or view.command_id != self._command_id:
            return
        self._command_id = ""
        if view.scan is not None:
            self._scan(view.scan)
        messages = {
            "SCAN_STATUS": "",
            "SCAN_STARTED": "검사를 시작함.",
            "SCAN_ALREADY_RUNNING": "이미 검사 중임.",
            "SCAN_TARGET_INVALID": "검사할 수 없는 서버임. 설정 파일에 있는 PENDING 서버만 검사함.",
            "SCAN_UNAVAILABLE": "검사 기능이 연결되지 않음.",
        }
        self.result_label.setText(messages.get(view.code, "검사 요청 결과를 확인하지 못함."))
        self.rescan_button.setEnabled(not self._running)

    def _command(self, event: CommandEvent) -> None:
        if (
            not self._closed
            and self._command_id
            and event.command_id == self._command_id
            and event.stage in (CommandStage.REJECTED, CommandStage.UNCONFIRMED)
        ):
            self._command_id = ""
            self.result_label.setText("검사 요청을 처리하지 못함.")
            self.rescan_button.setEnabled(not self._running)

    def _scan(self, state: ScanState) -> None:
        if self._closed or state.server_id != self._server_id:
            return
        self.scan_report.set_state(state)
        self._running = state.run in ("queued", "running")
        self.rescan_button.setEnabled(not self._running and not self._command_id)

    def _finish(self, _result: int) -> None:
        if self._closed:
            return
        self._closed = True
        self._bridge.registration_changed.disconnect(self._registration)
        self._bridge.command_changed.disconnect(self._command)
        self._bridge.scan_changed.disconnect(self._scan)
        self.scan_report.clear()
