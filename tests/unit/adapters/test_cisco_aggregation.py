"""I6·I9 / T17·T19: C 내부 집계의 누락·실패·사본·취소 검증."""

import asyncio
import json
from dataclasses import FrozenInstanceError, dataclass

import pytest

from guard_app.adapters.cisco import (
    CiscoMetadataAdapter,
    DefinitionInputError,
    PreparedToolDefinition,
)
from guard_app.adapters.cisco_aggregation import InternalMetadataScanner, ScannerAggregationError
from guard_app.adapters.cisco_llm import ScannerLLMError


def definition(name="sensitive_tool_name", **extra):
    return {"name": name, "inputSchema": {"type": "object"}, **extra}


@dataclass
class Finding:
    analyzer: str
    severity: str = "HIGH"
    threat_category: str = "PROMPT INJECTION"
    details: dict | None = None


class Analyzer:
    def __init__(self, result=(), error=None, action=None):
        self.result = list(result)
        self.error = error
        self.action = action
        self.inputs = []

    async def analyze_prepared(self, prepared):
        self.inputs.append(prepared)
        if self.action:
            await self.action()
        if self.error:
            raise self.error
        return self.result


def scan(llm, yara, definitions=None, **kwargs):
    scanner = InternalMetadataScanner(llm, yara, **kwargs)
    return asyncio.run(scanner.scan("trusted_server", definitions or [definition()]))


def test_completed_findings_preserve_both_sources_without_deduplication():
    details = {
        "threat_type": "prompt_injection",
        "evidence": "SENSITIVE_EVIDENCE",
        "raw_response": "SENSITIVE_RAW",
    }
    llm = Analyzer([Finding("LLM", details=details)])
    yara = Analyzer([Finding("YARA", details=details)])
    result = scan(llm, yara)
    assert result.summary.completion == "completed"
    assert result.summary.known_findings_count == 2
    assert result.summary.tools_with_warnings == 1
    assert result.summary.maximum_observed_severity == "HIGH"
    assert [a.findings[0].analyzer for a in result.tools[0].analyzers] == ["LLM", "YARA"]
    assert llm.inputs[0] is yara.inputs[0]
    assert result.tools[0].analyzers[0].findings[0].code == "prompt_injection"
    payload = result.to_json()
    assert "SENSITIVE_EVIDENCE" not in payload
    assert "SENSITIVE_RAW" not in payload
    assert "sensitive_tool_name" not in repr(result)
    assert "PROMPT INJECTION" not in repr(result)
    assert "ALLOW" not in payload
    assert "ACTIVE" not in payload
    assert result.scope == "provided_definitions"
    llm.result[0].severity = "LOW"
    details["threat_type"] = "changed"
    assert result.tools[0].analyzers[0].findings[0].severity == "HIGH"
    assert result.tools[0].analyzers[0].findings[0].code == "prompt_injection"
    with pytest.raises(FrozenInstanceError):
        result.summary.completed = 999
    copy = result.to_dict()
    copy["tools"][0]["analyzers"][0]["findings"].clear()
    assert result.summary.known_findings_count == 2
    assert len(result.tools[0].analyzers[0].findings) == 1


@pytest.mark.parametrize("failed_name", ["LLM", "YARA"])
def test_expected_failure_does_not_drop_other_analyzers_high_finding(failed_name):
    llm = Analyzer([Finding("LLM")])
    yara = Analyzer([Finding("YARA")])
    failed = llm if failed_name == "LLM" else yara
    failed.error = ValueError("SENSITIVE_ERROR")
    result = scan(llm, yara)
    assert result.summary.completion == "partial"
    assert result.summary.known_findings_count == 1
    assert result.summary.maximum_observed_severity == "HIGH"
    failed_result = next(a for a in result.tools[0].analyzers if a.analyzer == failed_name)
    assert failed_result.status == "failed"
    assert failed_result.findings is None
    assert failed_result.error_code == f"SCAN_{failed_name}_FAILED"
    assert "SENSITIVE_ERROR" not in result.to_json()


def test_both_failed_and_completed_zero_findings_are_distinct():
    failed = scan(
        Analyzer(error=ScannerLLMError("SCAN_LLM_RATE_LIMITED")),
        Analyzer(error=TimeoutError("secret")),
    )
    assert failed.summary.completion == "failed"
    assert all(a.findings is None for a in failed.tools[0].analyzers)
    assert failed.tools[0].analyzers[0].error_code == "SCAN_LLM_RATE_LIMITED"
    assert failed.tools[0].analyzers[1].error_code == "SCAN_YARA_TIMEOUT"
    completed = scan(Analyzer(), Analyzer())
    assert completed.summary.completion == "completed"
    assert all(a.findings == () for a in completed.tools[0].analyzers)
    assert completed.summary.maximum_observed_severity is None


def test_exact_byte_limit_and_rejected_inputs_do_not_invoke_analyzers():
    base = definition("a", description="")
    base_bytes = len(json.dumps(base, sort_keys=True, separators=(",", ":")).encode())
    exact = definition("a", description="x" * (65_536 - base_bytes))
    oversized = {**exact, "description": exact["description"] + "x"}
    llm, yara = Analyzer(), Analyzer()
    result = scan(llm, yara, [exact, oversized, {"name": "missing_schema"}, definition("last")])
    assert result.tools[0].definition_bytes == 65_536
    assert result.tools[1].input_error_code == "SCAN_METADATA_TOO_LARGE"
    assert result.tools[2].input_error_code == "SCAN_METADATA_INVALID"
    assert result.tools[3].completion == "completed"
    assert result.summary.completion == "partial"
    assert result.summary.completed == 2
    assert result.summary.not_scanned == 2
    assert len(llm.inputs) == len(yara.inputs) == 2
    assert all(a.status == "not_run" and a.findings is None for a in result.tools[1].analyzers)


def test_all_definitions_are_snapshotted_once_before_first_await():
    class FrozenLike:
        calls = 0

        def to_dict(self):
            self.calls += 1
            return definition("frozen", outputSchema={"description": "original"})

    frozen = FrozenLike()
    later = definition("later", annotations={"title": "original"})

    async def mutate():
        later["annotations"]["title"] = "mutated"

    llm, yara = Analyzer(action=mutate), Analyzer()
    scan(llm, yara, [frozen, later])
    assert frozen.calls == 1
    assert json.loads(llm.inputs[1].definition_json)["annotations"]["title"] == "original"
    assert llm.inputs == yara.inputs


def test_analyzers_run_concurrently_for_each_tool():
    async def run():
        llm_started, yara_started = asyncio.Event(), asyncio.Event()

        async def llm_action():
            llm_started.set()
            await yara_started.wait()

        async def yara_action():
            yara_started.set()
            await llm_started.wait()

        async with asyncio.timeout(1):
            result = await InternalMetadataScanner(
                Analyzer(action=llm_action), Analyzer(action=yara_action)
            ).scan("trusted", [definition()])
        assert result.summary.completed == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad",
    [
        Finding("YARA"),
        Finding("LLM", severity="invalid"),
        Finding("LLM", threat_category="ANALYZER INFRASTRUCTURE"),
        object(),
    ],
)
def test_invalid_results_preserve_known_findings_and_mark_failure(bad):
    result = scan(Analyzer([Finding("LLM"), bad]), Analyzer())
    llm = result.tools[0].analyzers[0]
    assert llm.status == "failed"
    assert llm.error_code == "SCAN_ANALYZER_RESULT_INVALID"
    assert len(llm.findings) == 1
    assert llm.findings[0].analyzer == "LLM"
    assert result.summary.maximum_observed_severity == "HIGH"
    assert result.summary.completion == "partial"


def test_unknown_severity_is_preserved_as_incomplete_observation():
    result = scan(Analyzer(), Analyzer([Finding("YARA", severity="UNKNOWN")]))
    assert result.tools[0].analyzers[1].status == "failed"
    assert result.tools[0].analyzers[1].findings[0].severity == "UNKNOWN"
    assert result.summary.maximum_observed_severity == "UNKNOWN"


def test_unavailable_analyzers_and_empty_input_keep_setup_errors():
    scanner = InternalMetadataScanner(
        None, Analyzer(), setup_errors={"LLM": "SCAN_LLM_KEY_MISSING_OR_INVALID"}
    )
    result = asyncio.run(scanner.scan("trusted", [definition()]))
    assert result.summary.completion == "partial"
    assert result.analyzer_summary[0].setup_error_code == "SCAN_LLM_KEY_MISSING_OR_INVALID"
    empty = asyncio.run(scanner.scan("trusted", []))
    assert empty.summary.completion == "not_scanned"
    assert empty.summary.definitions_total == 0
    assert empty.analyzer_summary[0].setup_error_code == "SCAN_LLM_KEY_MISSING_OR_INVALID"
    healthy_empty = asyncio.run(InternalMetadataScanner(Analyzer(), Analyzer()).scan("trusted", []))
    assert healthy_empty.summary.completion == "completed"
    assert healthy_empty.scope == "provided_definitions"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(DefinitionInputError, match="^SCAN_METADATA_LIMIT_INVALID$"):
        InternalMetadataScanner(Analyzer(), Analyzer(), max_definition_bytes=limit)


def test_configurable_limit_and_invalid_batch_identifiers():
    result = scan(Analyzer(), Analyzer(), max_definition_bytes=1)
    assert result.tools[0].input_error_code == "SCAN_METADATA_TOO_LARGE"
    scanner = InternalMetadataScanner(Analyzer(), Analyzer())
    with pytest.raises(ScannerAggregationError, match="^SCAN_SERVER_ID_INVALID$"):
        asyncio.run(scanner.scan(" ", []))
    with pytest.raises(ScannerAggregationError, match="^SCAN_METADATA_BATCH_INVALID$"):
        asyncio.run(scanner.scan("trusted", "bad"))


def test_unexpected_error_waits_for_other_analyzer_then_propagates():
    other_completed = []

    async def other():
        await asyncio.sleep(0)
        other_completed.append(True)

    with pytest.raises(RuntimeError, match="^programming_error$"):
        scan(Analyzer(error=RuntimeError("programming_error")), Analyzer(action=other))
    assert other_completed == [True]


def test_caller_cancellation_cleans_up_both_async_tasks():
    async def run():
        entered = 0
        both_entered = asyncio.Event()
        finished = []

        async def block():
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.append(True)

        job = asyncio.create_task(
            InternalMetadataScanner(Analyzer(action=block), Analyzer(action=block)).scan(
                "trusted", [definition()]
            )
        )
        await asyncio.wait_for(both_entered.wait(), 1)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert len(finished) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        '{"name":"a","inputSchema":{}} ',
        '{"name":"a","name":"b","inputSchema":{}}',
        '"not an object"',
        '{"name":"a","inputSchema":{},"extra":NaN}',
    ],
)
def test_direct_prepared_input_cannot_bypass_canonical_validation(text):
    class SDK:
        async def analyze(self, content, context=None):
            pytest.fail("invalid input must not reach SDK")

    adapter = CiscoMetadataAdapter(SDK(), input_format="yara", max_definition_bytes=65_536)
    with pytest.raises(DefinitionInputError, match="^SCAN_METADATA_INVALID$"):
        asyncio.run(adapter.analyze_prepared(PreparedToolDefinition(text)))
