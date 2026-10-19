"""SCAN-01·T17/T19의 B DTO 단위 부분. SDK 번역·표시·C 인수는 별도다."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from guard_app.domain.contracts import AnalyzerDiagnostic, Finding, ScanReport
from guard_app.domain.enums import Label, ScanErrorCode
from tests.unit.test_domain_contracts import make_scan
from tests.unit.test_guard import _call, _Harness


def _finding(**updates):
    return Finding.model_validate({"analyzer": "LLM", "code": "F1", "severity": "HIGH", **updates})


def test_legacy_report_and_finding_inputs_keep_all_existing_fields():
    finding = _finding(description="PRIVATE_DESCRIPTION")
    report = make_scan(findings=(finding,), max_severity="HIGH")
    assert finding.tool_name is None
    assert finding.source is None
    assert finding.overall_risk is None
    assert finding.threat_summary == ""
    assert report.overall_risk is None
    assert report.threat_summary == ""
    assert report.analyzer_diagnostics == ()
    assert report.findings == (finding,)
    assert report.max_severity == "HIGH"
    assert report.analyzers_completed == ("offline-rules",)
    assert ScanReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.parametrize("source", ["LLM", "YARA"])
@pytest.mark.parametrize(
    "severity", ["SAFE", "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]
)
def test_per_tool_source_fingerprint_and_sdk_severity_are_preserved(source, severity):
    finding = _finding(
        analyzer=source,
        severity=severity,
        tool_name="read_document",
        tool_fingerprint="a" * 64,
        source=source,
        overall_risk="low",
        threat_summary="검증되지 않은 도구 설명입니다.",
    )
    report = make_scan(findings=(finding,), max_severity=severity, overall_risk="SAFE")
    assert finding.severity == severity
    assert report.max_severity == severity
    assert finding.overall_risk == "low"
    assert finding.source == source
    assert finding.tool_name == "read_document"
    assert finding.tool_fingerprint == "a" * 64
    assert Finding.model_validate_json(finding.model_dump_json()) == finding


@pytest.mark.parametrize(
    "text", ["", "설명" * 150, "가" * 300, "🙂" * 300, "e\u0301", "줄1\n줄2\t설명", "ＡＢＣ"]
)
def test_summary_accepts_bounded_plain_text_without_rewriting(text):
    finding = _finding(threat_summary=text)
    report = make_scan(threat_summary=text, findings=(finding,))
    assert report.threat_summary == text
    assert finding.threat_summary == text
    assert ScanReport.model_validate_json(report.model_dump_json()) == report


@pytest.mark.parametrize(
    "text",
    [
        "가" * 301,
        "a" * 301,
        "PRIVATE_MARKER <b>본문</b>",
        "<https://example.com>",
        "https://example.com",
        "HTTP://EXAMPLE.COM",
        "custom://endpoint",
        "www.example.com",
        "example.com",
        "사용자@example.com",
        "mailto:user@example.com",
        "javascript:alert(1)",
        "data:text/html,content",
        "file:C:/private",
        "[문구](target)",
        "[문구][id]",
        "[id]: target",
        "&lt;script&gt;",
        "&#60;script&#62;",
        "＜script＞",
        "ｈｔｔｐｓ：／／ｅｘａｍｐｌｅ．ｃｏｍ",
        "example。com",
        "PRIVATE_MARKER\x00",
        "PRIVATE_MARKER\x1b[31m",
        "PRIVATE_MARKER\x7f",
        "PRIVATE_MARKER\x85",
        "PRIVATE_MARKER\u202e",
        "PRIVATE_MARKER\u2066",
        "PRIVATE_MARKER\u200b",
        "PRIVATE_MARKER\u2028",
        "PRIVATE_MARKER\ud800",
    ],
)
@pytest.mark.parametrize("target", ["report", "finding"])
def test_invalid_summary_is_rejected_without_echoing_or_sanitizing(text, target):
    with pytest.raises(ValidationError) as error:
        make_scan(threat_summary=text) if target == "report" else _finding(threat_summary=text)
    assert "PRIVATE_MARKER" not in str(error.value)


@pytest.mark.parametrize("value", [None, b"text", 1, True, [], {}])
def test_summary_does_not_coerce_missing_or_non_text_values(value):
    with pytest.raises(ValidationError):
        make_scan(threat_summary=value)


@pytest.mark.parametrize("value", ["", "x" * 65, "<b>high</b>", "https://example.com", 1, True])
def test_overall_risk_is_a_bounded_untrusted_display_value(value):
    with pytest.raises(ValidationError):
        make_scan(overall_risk=value)


def test_report_and_nested_finding_hide_all_new_untrusted_text_from_repr():
    marker = "PRIVATE_MARKER"
    finding = _finding(
        description=marker, tool_name=marker, overall_risk=marker, threat_summary=marker
    )
    report = make_scan(findings=(finding,), overall_risk=marker, threat_summary=marker)
    assert marker not in repr(finding)
    assert marker not in repr(report)
    assert marker not in str(report)
    assert report.model_dump()["threat_summary"] == marker  # 명시적 직렬화는 로그 권한이 아니다.
    with pytest.raises(ValidationError):
        report.threat_summary = "changed"
    with pytest.raises(ValidationError):
        report.model_copy(update={"threat_summary": "<b>changed</b>"})


@pytest.mark.parametrize("code", list(ScanErrorCode))
def test_fixed_diagnostics_preserve_failure_with_zero_findings(code):
    diagnostic = AnalyzerDiagnostic(
        analyzer="LLM", code=code, tool_name="read", tool_fingerprint="a" * 64
    )
    report = make_scan(
        analyzers_required=("LLM", "YARA"),
        analyzers_completed=("YARA",),
        analyzers_failed=("LLM",),
        analyzer_diagnostics=(diagnostic,),
        overall_risk="SAFE",
    )
    assert report.findings == ()
    assert report.analyzers_failed == ("LLM",)
    assert report.analyzers_completed == ("YARA",)
    assert report.analyzer_diagnostics == (diagnostic,)
    assert report.analyzer_diagnostics[0].code is code
    assert ScanReport.model_validate_json(report.model_dump_json()) == report


def test_skipped_analyzer_has_fixed_reason_and_legacy_missing_codes_remain_incomplete():
    report = make_scan(
        analyzers_completed=(),
        analyzers_skipped=("offline-rules",),
        analyzer_diagnostics=(
            AnalyzerDiagnostic(analyzer="offline-rules", code=ScanErrorCode.SCAN_INCOMPLETE),
        ),
    )
    assert report.analyzers_skipped == ("offline-rules",)
    assert report.findings == ()
    legacy = make_scan(analyzers_completed=(), analyzers_failed=("offline-rules",))
    assert legacy.analyzers_failed == ("offline-rules",)
    assert legacy.analyzer_diagnostics == ()
    assert not legacy.analyzers_completed


@pytest.mark.parametrize("status", ["completed", "absent"])
def test_diagnostic_cannot_claim_a_completed_or_unknown_analyzer_failed(status):
    diagnostic = AnalyzerDiagnostic(
        analyzer="offline-rules" if status == "completed" else "missing",
        code=ScanErrorCode.SCAN_MALFORMED,
    )
    with pytest.raises(ValidationError):
        make_scan(analyzer_diagnostics=(diagnostic,))


def test_diagnostic_rejects_duplicate_and_unbound_fingerprint():
    diagnostic = AnalyzerDiagnostic(analyzer="LLM", code=ScanErrorCode.SCAN_LLM_TIMEOUT)
    kwargs = {"analyzers_completed": (), "analyzers_failed": ("LLM",)}
    with pytest.raises(ValidationError):
        make_scan(**kwargs, analyzer_diagnostics=(diagnostic, diagnostic))
    with pytest.raises(ValidationError):
        make_scan(
            **kwargs,
            analyzer_diagnostics=(diagnostic.model_copy(update={"tool_fingerprint": "b" * 64}),),
        )
    with pytest.raises(ValidationError) as error:
        AnalyzerDiagnostic(analyzer="LLM", code="PRIVATE_EXCEPTION_MESSAGE")
    assert "PRIVATE_EXCEPTION_MESSAGE" not in str(error.value)


def test_diagnostics_are_strict_in_python_and_accept_fixed_codes_in_json():
    encoded = json.dumps({"analyzer": "LLM", "code": "SCAN_LLM_TIMEOUT"})
    item = AnalyzerDiagnostic.model_validate_json(encoded)
    assert item.code is ScanErrorCode.SCAN_LLM_TIMEOUT
    with pytest.raises(ValidationError):
        AnalyzerDiagnostic(analyzer="LLM", code="SCAN_LLM_TIMEOUT")
    with pytest.raises(ValidationError):
        _finding(source="custom")


@pytest.mark.parametrize(
    ("label", "expected", "dispatches"),
    [
        (Label.PUBLIC, "released", 1),
        (Label.INTERNAL, "pending", 0),
        (Label.CONFIDENTIAL, "denied", 0),
    ],
)
def test_overall_risk_and_scanner_warnings_do_not_change_guard_decision(
    label, expected, dispatches
):
    async def scenario():
        for risk in ("SAFE", "CRITICAL"):
            h = _Harness(label=label)
            h.registry.scan_report = make_scan(
                max_severity="CRITICAL",
                overall_risk=risk,
                findings=(_finding(severity="CRITICAL", overall_risk="SAFE"),),
            )
            result = await h.guard.submit("task", _call("send"))
            assert result.kind == expected
            assert len(h.transport.calls) == dispatches
            await h.guard.aclose()

    asyncio.run(scenario())
