"""C4.9·C4.15·C4.17: C 내부 집계 → B ScanReport 번역과 Registry 저장. SDK 대신 대역을 쓴다.

실제 SDK(LLM 응답의 자체 평가 위치, native YARA)는
tests/contract/test_cisco_aggregation_sdk.py가 본다.
"""

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import pytest

from guard_app.adapters.cisco_aggregation import (
    SCANNER_VERSION,
    InternalMetadataScanner,
    ScanReportError,
    scan_report,
)
from guard_app.adapters.cisco_llm import ScannerLLMError
from guard_app.adapters.mcp_transport import ToolListing
from guard_app.domain.enums import RegStatus, ScanErrorCode
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.security.digest import tool_fingerprint
from guard_app.services.catalog import ServerSettings
from guard_app.services.registry import RegistryError, ServerRegistry, listing_fingerprints
from guard_app.storage import SQLiteStore

NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)
PLANTED_EVIDENCE = "SDK-EVIDENCE-PLANTED"


def definition(name, **extra):
    return {"name": name, "inputSchema": {"type": "object"}, **extra}


DEFINITIONS = [definition("read_doc"), definition("send_mail", description="Send a message")]


def pairs(definitions=DEFINITIONS):
    return [(item["name"], tool_fingerprint(item)) for item in definitions]


@dataclass
class SdkFinding:
    analyzer: str
    severity: str = "HIGH"
    threat_category: str = "PROMPT INJECTION"
    details: dict | None = None


def llm_finding(risk="MEDIUM", summary="Tool text asks the model to ignore rules.", **extra):
    analysis = {"overall_risk": risk, "threat_summary": summary, **extra}
    return SdkFinding(
        "LLM",
        details={
            "threat_type": "PROMPT INJECTION",
            "evidence": PLANTED_EVIDENCE,
            "raw_response": {"threat_analysis": analysis},
        },
    )


class Analyzer:
    """도구 이름별 결과·오류를 돌려주는 대역."""

    def __init__(self, results=None, errors=None):
        self.results = results or {}
        self.errors = errors or {}

    async def analyze_prepared(self, prepared):
        name = json.loads(prepared.definition_json)["name"]
        if name in self.errors:
            raise self.errors[name]
        return list(self.results.get(name, ()))


def scanned(llm, yara, definitions=DEFINITIONS, **kwargs):
    scanner = InternalMetadataScanner(llm, yara, **kwargs)
    return asyncio.run(scanner.scan("docs", definitions))


def report_of(result, tools=None):
    return scan_report(result, pairs() if tools is None else tools, scanned_at_utc=NOW)


def test_completed_scan_becomes_a_report_with_sdk_severity_and_both_sources():
    yara_finding = SdkFinding("YARA", "MEDIUM", "TOOL POISONING", {"threat_type": "TOOL POISONING"})
    result = scanned(
        Analyzer({"read_doc": [llm_finding()]}),
        Analyzer({"send_mail": [yara_finding]}),
    )
    report = report_of(result)
    read_fp, send_fp = (fingerprint for _, fingerprint in pairs())
    assert report.server_id == "docs"
    assert report.fingerprint_set == (read_fp, send_fp)
    assert report.analyzers_required == ("LLM", "YARA")
    assert report.analyzers_completed == ("LLM", "YARA")
    assert report.analyzers_failed == report.analyzers_skipped == ()
    assert report.analyzer_diagnostics == ()
    assert report.max_severity == "HIGH"
    assert report.scanner_version == SCANNER_VERSION
    assert report.scanned_at_utc == NOW
    llm, yara = report.findings
    assert (llm.source, llm.tool_name, llm.tool_fingerprint) == ("LLM", "read_doc", read_fp)
    assert (llm.code, llm.severity) == ("PROMPT INJECTION", "HIGH")
    assert llm.overall_risk == "MEDIUM"
    assert llm.threat_summary == "Tool text asks the model to ignore rules."
    assert llm.description == ""
    assert (yara.source, yara.tool_name, yara.severity) == ("YARA", "send_mail", "MEDIUM")
    assert (yara.overall_risk, yara.threat_summary) == (None, "")
    # 보고서 어디에도 SDK evidence가 없다. 남는 모델 문장은 threat_summary 하나다.
    assert PLANTED_EVIDENCE not in report.model_dump_json()


def test_self_assessment_is_defanged_and_never_lowers_the_sdk_severity():
    summary = "See https://evil.example/x <b>now</b> " + "x" * 400
    result = scanned(Analyzer({"read_doc": [llm_finding("SAFE", summary)]}), Analyzer())
    finding = report_of(result).findings[0]
    assert finding.severity == "HIGH"  # 모델이 SAFE라 해도 SDK 값 그대로
    assert finding.overall_risk == "SAFE"
    assert len(finding.threat_summary) <= 300
    assert "https://" not in finding.threat_summary
    assert "<b>" not in finding.threat_summary


@pytest.mark.parametrize(
    "details",
    [
        None,
        {"threat_type": "PROMPT INJECTION"},
        {"raw_response": "PLAIN-TEXT-RESPONSE"},
        {"raw_response": {"threat_analysis": "not-an-object"}},
        {"raw_response": {"threat_analysis": {"overall_risk": 3, "threat_summary": ["x"]}}},
        {"raw_response": {"threat_analysis": {"overall_risk": "", "threat_summary": ""}}},
    ],
    ids=["none", "no-raw", "raw-text", "analysis-text", "wrong-types", "empty"],
)
def test_missing_or_malformed_self_assessment_is_left_empty(details):
    finding = SdkFinding("LLM", details=details)
    report = report_of(scanned(Analyzer({"read_doc": [finding]}), Analyzer()))
    assert report.findings[0].overall_risk is None
    assert report.findings[0].threat_summary == ""
    assert "PLAIN-TEXT-RESPONSE" not in report.model_dump_json()


def test_long_overall_risk_is_cut_to_the_b_limit():
    result = scanned(Analyzer({"read_doc": [llm_finding("R" * 200)]}), Analyzer())
    assert len(report_of(result).findings[0].overall_risk) <= 64


def test_setup_failure_fails_the_analyzer_once_without_tool_details():
    result = scanned(None, Analyzer(), setup_errors={"LLM": "SCAN_LLM_KEY_MISSING_OR_INVALID"})
    report = report_of(result)
    assert report.analyzers_completed == ("YARA",)
    assert report.analyzers_failed == ("LLM",)
    (diagnostic,) = report.analyzer_diagnostics
    assert diagnostic.analyzer == "LLM"
    assert diagnostic.code is ScanErrorCode.SCAN_LLM_KEY_MISSING_OR_INVALID
    assert (diagnostic.tool_name, diagnostic.tool_fingerprint) == (None, None)


def test_tool_failure_names_the_tool_and_keeps_other_findings():
    result = scanned(
        Analyzer(
            {"read_doc": [llm_finding()]},
            errors={"send_mail": ScannerLLMError("SCAN_LLM_TIMEOUT")},
        ),
        Analyzer(),
    )
    report = report_of(result)
    assert report.analyzers_failed == ("LLM",)
    assert report.analyzers_completed == ("YARA",)
    (diagnostic,) = report.analyzer_diagnostics
    assert diagnostic.code is ScanErrorCode.SCAN_LLM_TIMEOUT
    assert (diagnostic.tool_name, diagnostic.tool_fingerprint) == pairs()[1]
    assert [finding.tool_name for finding in report.findings] == ["read_doc"]


def test_unscanned_definition_fails_both_analyzers_for_that_tool():
    large = [definition("read_doc"), definition("send_mail", description="x" * 200)]
    result = scanned(Analyzer(), Analyzer(), definitions=large, max_definition_bytes=120)
    report = report_of(result, pairs(large))
    assert report.analyzers_failed == ("LLM", "YARA")
    assert report.fingerprint_set == tuple(fp for _, fp in pairs(large))
    assert {(d.analyzer, d.tool_name, d.code) for d in report.analyzer_diagnostics} == {
        ("LLM", "send_mail", ScanErrorCode.SCAN_METADATA_TOO_LARGE),
        ("YARA", "send_mail", ScanErrorCode.SCAN_METADATA_TOO_LARGE),
    }


def test_unknown_severity_is_kept_and_marks_the_result_invalid():
    finding = SdkFinding("YARA", "UNKNOWN", "NEW CATEGORY", {"threat_type": "new_rule"})
    report = report_of(scanned(Analyzer(), Analyzer({"read_doc": [finding]})))
    assert report.findings[0].severity == "UNKNOWN"
    assert report.findings[0].code == "new_rule"
    assert report.max_severity == "UNKNOWN"
    assert report.analyzers_failed == ("YARA",)
    assert report.analyzer_diagnostics[0].code is ScanErrorCode.SCAN_ANALYZER_RESULT_INVALID


def test_codes_outside_the_b_enum_become_the_analyzers_general_failure():
    result = scanned(Analyzer(errors={"read_doc": ScannerLLMError("SCAN_LLM_TIMEOUT")}), Analyzer())
    tool = result.tools[0]
    odd = dataclasses.replace(tool.analyzers[0], error_code="SCAN_SOMETHING_NEW")
    patched = dataclasses.replace(
        result,
        tools=(dataclasses.replace(tool, analyzers=(odd, tool.analyzers[1])), result.tools[1]),
    )
    diagnostic = report_of(patched).analyzer_diagnostics[0]
    assert diagnostic.code is ScanErrorCode.SCAN_LLM_FAILED


def test_server_without_tools_is_a_complete_empty_scan():
    report = report_of(scanned(Analyzer(), Analyzer(), definitions=[]), [])
    assert report.fingerprint_set == ()
    assert report.analyzers_completed == ("LLM", "YARA")
    assert report.findings == ()


@pytest.mark.parametrize(
    "tools",
    [
        pairs()[:1],
        [("other", pairs()[0][1]), pairs()[1]],
        [list(pairs()[0]), pairs()[1]],
        "not-a-list",
    ],
    ids=["count", "name", "item-type", "type"],
)
def test_tools_must_match_the_scanned_listing(tools):
    result = scanned(Analyzer(), Analyzer())
    with pytest.raises(ScanReportError) as raised:
        scan_report(result, tools, scanned_at_utc=NOW)
    assert str(raised.value) == "SCAN_REPORT_INPUT_MISMATCH"
    with pytest.raises(ScanReportError) as raised:
        scan_report(object(), pairs(), scanned_at_utc=NOW)
    assert str(raised.value) == "SCAN_REPORT_INPUT_MISMATCH"


def test_values_b_rejects_produce_no_partial_report():
    result = scanned(Analyzer(), Analyzer())
    bad_fingerprint = [(pairs()[0][0], "NOT-A-SHA"), pairs()[1]]
    for tools, when in ((bad_fingerprint, NOW), (pairs(), datetime(2026, 10, 28, 9))):
        with pytest.raises(ScanReportError) as raised:
            scan_report(result, tools, scanned_at_utc=when)
        assert str(raised.value) == "SCAN_REPORT_INVALID"
        assert raised.value.__cause__ is None


def test_scanner_version_matches_the_installed_sdk():
    assert f"cisco-ai-mcp-scanner-{version('cisco-ai-mcp-scanner')}" == SCANNER_VERSION


# Registry 저장과 활성화 대조

SERVER = {
    "id": "docs",
    "transport": "stdio",
    "command": "C:/PRIVATE_COMMAND/python.exe",
    "tier": "internal",
    "max_label": "CONFIDENTIAL",
    "tools": [{"name": "read_doc", "kind": "read", "resource_arg": "path"}],
}


def run(operation):
    return asyncio.run(operation)


def listing(definitions):
    return ToolListing(
        complete=True, tools=tuple(FrozenJsonObject(item) for item in definitions), reason=None
    )


@pytest.fixture
def registry(tmp_path: Path):
    sequence = iter(range(10_000))
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(sequence)}"
    ) as store:
        created = ServerRegistry(store, clock=lambda: NOW)
        run(created.load())
        yield created, store


def _registered_report(registry, definitions=DEFINITIONS):
    server = ServerSettings.model_validate_json(json.dumps(SERVER), strict=True)
    observed = listing(definitions)
    entry = run(registry.register(server, observed))
    scanner = InternalMetadataScanner(Analyzer({"read_doc": [llm_finding()]}), Analyzer())
    result = run(scanner.scan("docs", observed.tools))
    tools = list(listing_fingerprints(observed).items())
    return entry, scan_report(result, tools, scanned_at_utc=NOW)


def test_recorded_scan_is_the_one_the_user_activates_with(registry):
    registry, store = registry
    entry, report = _registered_report(registry)
    recorded = run(registry.record_scan("scan-1", report))
    assert recorded.last_scan_id == "scan-1"
    assert registry.entry("docs") == recorded == run(store.load_registration("docs"))
    assert registry.status("docs") is RegStatus.PENDING  # 검사는 상태를 바꾸지 않는다
    active = run(
        registry.activate(
            "docs",
            expected_row_revision=recorded.registration.row_revision,
            reviewed_by="operator",
            reviewed_scan_id="scan-1",
        )
    )
    assert active.reviewed_scan_id == "scan-1"
    assert registry.status("docs") is RegStatus.ACTIVE


def test_scan_of_a_different_listing_cannot_be_used_to_activate(registry):
    registry, _ = registry
    _registered_report(registry)
    changed = [definition("read_doc", description="changed"), DEFINITIONS[1]]
    scanner = InternalMetadataScanner(Analyzer(), Analyzer())
    result = run(scanner.scan("docs", changed))
    report = scan_report(result, pairs(changed), scanned_at_utc=NOW)
    recorded = run(registry.record_scan("scan-other", report))
    with pytest.raises(StoreError):
        run(
            registry.activate(
                "docs",
                expected_row_revision=recorded.registration.row_revision,
                reviewed_by="operator",
                reviewed_scan_id="scan-other",
            )
        )
    assert registry.status("docs") is RegStatus.PENDING


def test_record_scan_refuses_non_reports_and_unknown_servers(registry):
    registry, _ = registry
    with pytest.raises(RegistryError) as raised:
        run(registry.record_scan("scan-1", {"server_id": "docs"}))
    assert str(raised.value) == "REG_SCAN_INVALID"
    result = scanned(Analyzer(), Analyzer())
    with pytest.raises(RegistryError) as raised:
        run(registry.record_scan("scan-1", report_of(result)))
    assert str(raised.value) == "REG_SERVER_UNKNOWN"
