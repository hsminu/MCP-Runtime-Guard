"""I6·I9 / T17·T19: 도구 정의 누락·절단·원문 노출 회귀."""

import asyncio
import copy
import json
import unittest

import pytest

from guard_app.adapters.cisco import (
    CiscoMetadataAdapter,
    DefinitionInputError,
    prepare_tool_definition,
)


def tool_definition() -> dict[str, object]:
    return {
        "name": "tool_001",
        "description": '공개 문서 조회. 따옴표: "example" / 역슬래시: \\',
        "inputSchema": {
            "type": "object",
            "description": "ROOT_SCHEMA_MARKER",
            "$defs": {"query": {"type": "string", "description": "DEFS_MARKER"}},
            "properties": {
                "request": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "description": "NESTED_MARKER\u200b",
                            "examples": ["EXAMPLE_MARKER"],
                            "default": "DEFAULT_MARKER",
                        }
                    },
                },
                "items": {"type": "array", "items": {"description": "ARRAY_MARKER"}},
            },
            "required": ["request"],
        },
        "annotations": {"title": "ANNOTATIONS_MARKER", "readOnlyHint": True},
        "title": "TOP_LEVEL_TITLE_MARKER",
        "outputSchema": {"description": "OUTPUT_MARKER", "minimum": 0.5},
        "_meta": {"vendor_extension": {"description": "EXTENSION_MARKER"}},
    }


class FrozenDefinition:
    """B의 to_dict 구조와의 호환성을 단위 검증하는 입력."""

    def __init__(self, value: dict[str, object]) -> None:
        self._value = copy.deepcopy(value)

    def to_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._value)


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def analyze(self, content: str, context: dict[str, object] | None = None) -> list[str]:
        self.calls.append((content, context))
        return ["internal_sdk_result"]


class MetadataInputTests(unittest.TestCase):
    def test_full_definition_roundtrip_without_normalizing_values(self) -> None:
        definition = tool_definition()
        prepared = prepare_tool_definition(definition, max_definition_bytes=10_000)
        assert json.loads(prepared.definition_json) == definition
        assert "\u200b" in prepared.definition_json

    def test_llm_envelope_contains_entire_definition(self) -> None:
        definition = tool_definition()
        prepared = prepare_tool_definition(definition, max_definition_bytes=10_000)
        envelope = json.loads(prepared.llm_content())
        assert set(envelope) == {"description"}
        assert json.loads(envelope["description"]) == definition

    def test_snapshot_does_not_change_with_mutable_source(self) -> None:
        definition = tool_definition()
        before = copy.deepcopy(definition)
        prepared = prepare_tool_definition(definition, max_definition_bytes=10_000)
        definition["description"] = "changed"
        definition["inputSchema"]["description"] = "changed"
        assert json.loads(prepared.definition_json) == before

    def test_b_frozen_object_interface_preserves_fields(self) -> None:
        definition = tool_definition()
        prepared = prepare_tool_definition(
            FrozenDefinition(definition), max_definition_bytes=10_000
        )
        assert json.loads(prepared.definition_json) == definition

    def test_optional_fields_need_not_be_synthesized(self) -> None:
        definition = {"name": "tool_001", "inputSchema": {"type": "object"}}
        prepared = prepare_tool_definition(definition, max_definition_bytes=10_000)
        assert json.loads(prepared.definition_json) == definition

    def test_unicode_limit_counts_utf8_bytes_and_never_truncates(self) -> None:
        definition = tool_definition()
        prepared = prepare_tool_definition(definition, max_definition_bytes=10_000)
        byte_count = len(prepared.definition_json.encode("utf-8"))
        exact = prepare_tool_definition(definition, max_definition_bytes=byte_count)
        assert exact == prepared
        with pytest.raises(DefinitionInputError, match="^SCAN_METADATA_TOO_LARGE$"):
            prepare_tool_definition(definition, max_definition_bytes=byte_count - 1)

    def test_invalid_json_is_rejected_without_exposing_marker(self) -> None:
        marker = "SENSITIVE_TEST_MARKER"
        invalid_values = [float("nan"), float("inf"), {1: marker}, {marker}, (marker,), object()]
        cyclic: list[object] = [marker]
        cyclic.append(cyclic)
        invalid_values.extend([cyclic, "\ud800" + marker])
        for value in invalid_values:
            with self.subTest(value_type=type(value).__name__):
                definition = {"name": "tool_001", "inputSchema": {}, "extra": value}
                with pytest.raises(DefinitionInputError) as captured:
                    prepare_tool_definition(definition, max_definition_bytes=10_000)
                assert str(captured.value) == "SCAN_METADATA_INVALID"
                assert marker not in repr(captured.value)

    def test_malformed_required_fields_are_rejected(self) -> None:
        definitions = [
            {},
            {"name": "", "inputSchema": {}},
            {"name": "tool_001"},
            {"name": "tool_001", "inputSchema": []},
            {"name": "tool_001", "inputSchema": {}, "description": []},
            {"name": "tool_001", "inputSchema": {}, "annotations": []},
        ]
        for definition in definitions:
            with (
                self.subTest(definition_fields=tuple(definition)),
                pytest.raises(DefinitionInputError, match="^SCAN_METADATA_INVALID$"),
            ):
                prepare_tool_definition(definition, max_definition_bytes=10_000)

    def test_invalid_limit_is_rejected(self) -> None:
        for limit in (0, -1, True, 1.5):
            with (
                self.subTest(limit=limit),
                pytest.raises(DefinitionInputError, match="^SCAN_METADATA_LIMIT_INVALID$"),
            ):
                prepare_tool_definition(tool_definition(), max_definition_bytes=limit)

    def test_default_repr_hides_tool_definition(self) -> None:
        prepared = prepare_tool_definition(tool_definition(), max_definition_bytes=10_000)
        assert "MARKER" not in repr(prepared)
        assert "tool_001" not in repr(prepared)


class MetadataAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_formats_preserve_all_fields_at_public_analyzer_entry(self) -> None:
        definition = tool_definition()
        for input_format in ("yara", "llm"):
            with self.subTest(input_format=input_format):
                analyzer = RecordingAnalyzer()
                adapter = CiscoMetadataAdapter(
                    analyzer, input_format=input_format, max_definition_bytes=10_000
                )
                result = await adapter.analyze_tool_definition(definition)
                assert result == ["internal_sdk_result"]
                assert len(analyzer.calls) == 1
                content, context = analyzer.calls[0]
                reconstructed = json.loads(content)
                if input_format == "llm":
                    reconstructed = json.loads(reconstructed["description"])
                assert reconstructed == definition
                assert context["tool_name"] == "tool_definition"

    async def test_oversized_input_never_reaches_analyzer(self) -> None:
        analyzer = RecordingAnalyzer()
        adapter = CiscoMetadataAdapter(analyzer, input_format="llm", max_definition_bytes=10)
        with pytest.raises(DefinitionInputError, match="^SCAN_METADATA_TOO_LARGE$"):
            await adapter.analyze_tool_definition(tool_definition())
        assert analyzer.calls == []

    async def test_unknown_extension_cannot_bypass_input_validation(self) -> None:
        analyzer = RecordingAnalyzer()
        adapter = CiscoMetadataAdapter(analyzer, input_format="yara", max_definition_bytes=10_000)
        definition = tool_definition()
        definition["_meta"] = {"invalid": object()}
        with pytest.raises(DefinitionInputError):
            await adapter.analyze_tool_definition(definition)
        assert analyzer.calls == []

    async def test_analyzer_failure_is_not_replaced_with_empty_findings(self) -> None:
        class BrokenAnalyzer:
            async def analyze(self, content: str, context=None) -> list[str]:
                raise RuntimeError("synthetic analyzer failure")

        adapter = CiscoMetadataAdapter(
            BrokenAnalyzer(), input_format="yara", max_definition_bytes=10_000
        )
        with pytest.raises(RuntimeError):
            await adapter.analyze_tool_definition(tool_definition())

    async def test_cancellation_propagates(self) -> None:
        class CancelledAnalyzer:
            async def analyze(self, content: str, context=None) -> list[str]:
                raise asyncio.CancelledError

        adapter = CiscoMetadataAdapter(
            CancelledAnalyzer(), input_format="llm", max_definition_bytes=10_000
        )
        with pytest.raises(asyncio.CancelledError):
            await adapter.analyze_tool_definition(tool_definition())
