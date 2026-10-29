"""P5 보완·SCAN-01·T17/T19: 오탐·참 양성·명시적 무해화의 B 단위 부분."""

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from guard_app.domain.contracts import AnalyzerDiagnostic, Finding, ScanReport
from guard_app.domain.enums import ScanErrorCode
from guard_app.domain.text import ScanRisk, ThreatSummary, defang_scanner_text
from tests.unit.test_domain_contracts import make_scan

_SUMMARY = TypeAdapter(ThreatSummary)
_RISK = TypeAdapter(ScanRisk | None)
_STARTUP_CODES = (
    ScanErrorCode.SCAN_METADATA_FORMAT_INVALID,
    ScanErrorCode.SCAN_SERVER_ID_INVALID,
    ScanErrorCode.SCAN_METADATA_BATCH_INVALID,
    ScanErrorCode.SCAN_SDK_TOKENIZER_INVALID,
)


def test_ordinary_prose_file_names_and_korean_punctuation_are_not_links(subtests):
    cases = [
        "Profile:",
        "Hotel:",
        "Data: the tool changes instructions.",
        "Profile: default",
        "Hotel: none",
        "Metadata: data",
        "file: local path",
        "config.json 파일을 읽습니다.",
        "config.yaml 및 policy.toml",
        "report.csv, notes.txt, app.ini",
        "os.path 함수를 사용합니다.",
        "지시합니다.그리고 자료를 전송합니다.",
        "읽습니다.다음 동작을 지시합니다.",
        "Ｐｒｏｆｉｌｅ： default",
    ]
    for case, text in enumerate(cases):
        with subtests.test(case=case):
            assert _SUMMARY.validate_python(text) == text
            assert defang_scanner_text(text) == text
            assert make_scan(threat_summary=text).threat_summary == text


def test_link_html_and_fullwidth_true_positives_still_require_explicit_defanging(subtests):
    cases = [
        "https://example.com",
        "HTTPS://127.0.0.1",
        "custom://endpoint",
        "www.example.com",
        "a@b.com",
        "사용자@유해.com",
        "[link](target)",
        "[link][id]",
        "[id]: target",
        "<IMPORTANT>",
        "&lt;IMPORTANT&gt;",
        "&#60;tag&#62;",
        "javascript:alert(1)",
        "Data:text/html,evil",
        "file:C:/private",
        "tel:123",
        "ftp://example.com",
        "evil.com",
        "sub.evil.com",
        "evil.co.uk",
        "evil.zip",
        "evil.app",
        "evil.dev",
        "evil.py",
        "evil.xn--p1ai",
        "유해.com",
        "evil.한국",
        "evil.рф",
        "evil.json.com",
        "http://config.json",
        "a@config.json",
        "＜IMPORTANT＞",
        "ｈｔｔｐｓ：／／ｅｘａｍｐｌｅ．ｃｏｍ",
        "evil。com",
    ]
    for case, text in enumerate(cases):
        with subtests.test(case=case):
            with pytest.raises(ValidationError):
                _SUMMARY.validate_python(text)
            result = defang_scanner_text(text)
            assert result != text
            assert _SUMMARY.validate_python(result) == result


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<IMPORTANT> evil.com", "‹IMPORTANT› evil[.]com"),
        ("https://evil.com", "hxxps[:]//evil[.]com"),
        ("http://evil.com", "hxxp[:]//evil[.]com"),
        ("a@b.com", "a[@]b[.]com"),
        ("javascript:alert(1)", "javascript[:]alert(1)"),
        ("[text](https://evil.com)", "⟦text⟧(hxxps[:]//evil[.]com)"),
        ("&lt;IMPORTANT&gt;", "[&]lt;IMPORTANT[&]gt;"),
        ("＜IMPORTANT＞", "‹IMPORTANT›"),
        ("evil.com\x00\u202e\ud800", "evil[.]com   "),
    ],
)
def test_defanging_preserves_threat_markers_without_active_link_syntax(text, expected):
    assert defang_scanner_text(text) == expected


@pytest.mark.parametrize(
    "text", ["..", ".(host)", "a@b..com", "&lt;[evil](host)&gt;", "http://a..com:(b)"]
)
def test_defanged_punctuation_cannot_form_a_new_markdown_link(text):
    result = defang_scanner_text(text)
    assert _SUMMARY.validate_python(result) == result


@pytest.mark.parametrize("limit", [1, 2, 10, 64, 300])
def test_truncation_counts_ellipsis_and_cannot_reintroduce_a_link(limit):
    value = "config.json " * 30
    result = defang_scanner_text(value, max_length=limit)
    assert len(result) <= limit
    assert result.endswith("…")
    assert _SUMMARY.validate_python(result) == result
    assert defang_scanner_text("가" * 301, max_length=limit) == "가" * (limit - 1) + "…"


@pytest.mark.parametrize(
    "value", [None, b"PRIVATE_INPUT", 1, True, {}, [], ValueError("PRIVATE_INPUT")]
)
def test_non_string_input_is_a_fixed_value_error_without_input_repr(value, caplog):
    with pytest.raises(ValueError, match="^검사 표시 입력은 문자열이어야 한다$") as error:
        defang_scanner_text(value)
    assert "PRIVATE_INPUT" not in str(error.value)
    assert "PRIVATE_INPUT" not in repr(error.value)
    assert error.value.__context__ is None
    assert not caplog.records


@pytest.mark.parametrize("limit", [0, -1, 301, True, 1.0, "PRIVATE_LIMIT", None])
def test_invalid_length_is_rejected_without_echoing_input_or_limit(limit):
    with pytest.raises(
        ValueError, match="^검사 표시 상한은 1부터 300까지의 정수여야 한다$"
    ) as error:
        defang_scanner_text("PRIVATE_INPUT", max_length=limit)
    assert "PRIVATE_" not in str(error.value)
    assert "PRIVATE_" not in repr(error.value)


@settings(max_examples=200, deadline=None)
@given(
    text=st.text(alphabet=st.characters(blacklist_categories=()), max_size=1000),
    limit=st.integers(min_value=1, max_value=300),
)
@example(text="\ud800<IMPORTANT> evil.com\u202e", limit=300)
@example(text="config.json", limit=9)
def test_arbitrary_unicode_defanging_always_validates_within_limit(text, limit):
    result = defang_scanner_text(text, max_length=limit)
    assert len(result) <= limit
    assert _SUMMARY.validate_python(result) == result
    assert defang_scanner_text(result, max_length=limit) == result


_THREAT_TOKENS = st.sampled_from(
    [
        "evil.com",
        "https://",
        "<IMPORTANT>",
        "&lt;",
        "a@b.com",
        "[x](y)",
        "..",
        "\x00",
        "\u202e",
        "\ud800",
        "ｈｔｔｐ：／／",
        "\u3002",
        "[:][.]",
        "Data: ",
        "config.json",
    ]
)


@settings(max_examples=200, deadline=None)
@given(tokens=st.lists(_THREAT_TOKENS, max_size=40), limit=st.integers(1, 300))
def test_composed_threat_syntax_and_truncation_always_validate(tokens, limit):
    result = defang_scanner_text("".join(tokens), max_length=limit)
    assert len(result) <= limit
    assert _SUMMARY.validate_python(result) == result


@settings(max_examples=200, deadline=None)
@given(text=st.text(alphabet=st.characters(blacklist_categories=()), max_size=200))
def test_same_helper_supports_optional_scan_risk_without_inventing_a_risk_level(text):
    result = defang_scanner_text(text, max_length=64) or None
    assert _RISK.validate_python(result) == result


@pytest.mark.parametrize("code", _STARTUP_CODES)
def test_whole_scan_startup_failure_marks_all_required_analyzers_failed(code):
    report = make_scan(
        fingerprint_set=(),
        analyzers_required=("LLM", "YARA"),
        analyzers_completed=(),
        analyzers_failed=("LLM", "YARA"),
        analyzer_diagnostics=tuple(
            AnalyzerDiagnostic(analyzer=name, code=code) for name in ("LLM", "YARA")
        ),
    )
    assert report.analyzers_required == report.analyzers_failed
    assert report.analyzers_completed == ()
    assert report.findings == ()
    assert all(
        item.code is code and item.tool_fingerprint is None for item in report.analyzer_diagnostics
    )
    assert ScanReport.model_validate_json(report.model_dump_json()) == report


def test_explicit_defanging_keeps_finding_severity_and_evidence_summary():
    summary = defang_scanner_text("<IMPORTANT> evil.com 에 보내라고 지시합니다.")
    finding = Finding(analyzer="LLM", code="INJECTION", severity="HIGH", threat_summary=summary)
    report = make_scan(findings=(finding,), max_severity="HIGH", threat_summary=summary)
    assert report.findings == (finding,)
    assert report.findings[0].severity == "HIGH"
    assert "IMPORTANT" in report.threat_summary
    assert "evil[.]com" in report.threat_summary
    assert "IMPORTANT" not in repr(report)
