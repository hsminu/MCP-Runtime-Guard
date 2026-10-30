"""C4.22: Scanner 발견 코드의 사용자용 분류(`services.scan_categories`).

설치된 Cisco Scanner의 LLM·YARA 위협 이름과 대체 분류가 모두 "기타"가 아닌 분류로 가는지 본다.
Scanner를 올려 새 이름이 생기면 이 시험이 실패해 분류표를 함께 고치게 한다.
"""

from __future__ import annotations

import pytest
from mcpscanner.threats.threats import ThreatMapping

from guard_app.adapters.cisco_aggregation import UNRECOGNIZED_FINDING_CODE
from guard_app.services.scan_categories import (
    CREDENTIALS,
    DATA_LEAK,
    EXECUTION,
    MODEL_STEERING,
    OTHER,
    SCAN_CATEGORIES,
    TOOL_DISGUISE,
    scan_category,
)

SDK_TABLES = (ThreatMapping.LLM_THREATS, ThreatMapping.YARA_THREATS)


def _sdk_codes() -> set[str]:
    names = {name for table in SDK_TABLES for name in table}
    categories = {info["scanner_category"] for table in SDK_TABLES for info in table.values()}
    return names | categories


@pytest.mark.parametrize("code", sorted(_sdk_codes() - {"SECURITY VIOLATION"}))
def test_every_llm_and_yara_name_has_a_category(code):
    assert scan_category(code) is not OTHER


def test_the_installed_names_are_the_ones_the_table_was_written_for():
    # 표를 만들 때 본 설치본 4.8.6의 이름. 늘거나 바뀌면 분류표를 다시 본다.
    assert _sdk_codes() == {
        "PROMPT INJECTION",
        "TOOL POISONING",
        "TOOL SHADOWING",
        "DATA EXFILTRATION",
        "CREDENTIAL HARVESTING",
        "CODE EXECUTION",
        "SUSPICIOUS CODE EXECUTION",
        "INJECTION ATTACK",
        "SYSTEM MANIPULATION",
        "SECURITY VIOLATION",
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("PROMPT INJECTION", MODEL_STEERING),
        ("PROMPT_INJECTION", MODEL_STEERING),
        ("prompt injection", MODEL_STEERING),
        ("  Prompt   Injection ", MODEL_STEERING),
        ("TOOL POISONING", TOOL_DISGUISE),
        ("TOOL_SHADOWING", TOOL_DISGUISE),
        ("DATA EXFILTRATION", DATA_LEAK),
        ("CREDENTIAL HARVESTING", CREDENTIALS),
        ("SYSTEM_MANIPULATION", EXECUTION),
        ("INJECTION ATTACK", EXECUTION),
    ],
)
def test_case_spaces_and_underscores_do_not_matter(code, expected):
    assert scan_category(code) is expected


@pytest.mark.parametrize(
    "code",
    [
        "SECURITY VIOLATION",
        "unknown",
        UNRECOGNIZED_FINDING_CODE,
        "READ_YARA",
        "",
        "PROMPT",
        "PROMPT INJECTIONS",
        None,
        3,
    ],
)
def test_unknown_codes_are_other_and_never_lower(code):
    assert scan_category(code) is OTHER  # type: ignore[arg-type]


def test_texts_are_fixed_korean_and_never_claim_safety():
    assert len({category.name for category in SCAN_CATEGORIES}) == len(SCAN_CATEGORIES)
    for category in SCAN_CATEGORIES:
        for text in (category.name, category.meaning, category.action):
            assert text
            assert "안전" not in text
            assert text.isprintable()
            assert len(text) <= 40
