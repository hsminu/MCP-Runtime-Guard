"""FP-01·I9/I10·T10/T17: RFC 8785 지문 바이트와 입력 거부의 단위 검증.

RFC 8785 3.2절·부록 B: https://www.rfc-editor.org/rfc/rfc8785.html
Registry의 변경 감지·승인 무효화와 실제 전송 계수는 C의 독립 검증 대상이다.
"""

import hashlib
import json
import struct
import sys
from collections import UserDict
from decimal import Decimal
from enum import IntEnum

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from guard_app.domain.json_types import FrozenJsonObject
from guard_app.security.digest import (
    JcsCanonicalizationError,
    jcs_canonicalize,
    tool_fingerprint,
)


# IEEE 754 bits, not parsed decimal literals, preserve the RFC's exact inputs.
def test_rfc_appendix_b_number_samples(subtests) -> None:
    cases = [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("8000000000000001", "-5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("ffefffffffffffff", "-1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("c340000000000000", "-9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("44b52d02c7e14af5", "9.999999999999997e+22"),
        ("44b52d02c7e14af6", "1e+23"),
        ("44b52d02c7e14af7", "1.0000000000000001e+23"),
        ("444b1ae4d6e2ef4e", "999999999999999700000"),
        ("444b1ae4d6e2ef4f", "999999999999999900000"),
        ("444b1ae4d6e2ef50", "1e+21"),
        ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("41b3de4355555553", "333333333.3333332"),
        ("41b3de4355555554", "333333333.33333325"),
        ("41b3de4355555555", "333333333.3333333"),
        ("41b3de4355555556", "333333333.3333334"),
        ("41b3de4355555557", "333333333.33333343"),
        ("becbf647612f3696", "-0.0000033333333333333333"),
        ("43143ff3c1cb0959", "1424953923781206.2"),
    ]
    for case, (bits, expected) in enumerate(cases):
        with subtests.test(case=case):
            value = struct.unpack(">d", bytes.fromhex(bits))[0]
            assert jcs_canonicalize(value) == expected.encode("ascii")


@pytest.mark.parametrize("bits", ["7fffffffffffffff", "7ff0000000000000", "fff0000000000000"])
def test_rfc_appendix_b_nonfinite_numbers_are_rejected(bits: str) -> None:
    with pytest.raises(JcsCanonicalizationError):
        jcs_canonicalize(struct.unpack(">d", bytes.fromhex(bits))[0])


def test_rfc_example_object_matches_published_utf8_bytes() -> None:
    value = json.loads(
        r"""{
            "numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 1e-27],
            "string": "\u20ac$\u000F\u000aA'\u0042\u0022\u005c\\\"\/",
            "literals": [null, true, false]
        }"""
    )
    expected = bytes.fromhex(
        """
        7b 22 6c 69 74 65 72 61 6c 73 22 3a 5b 6e 75 6c 6c 2c 74 72
        75 65 2c 66 61 6c 73 65 5d 2c 22 6e 75 6d 62 65 72 73 22 3a
        5b 33 33 33 33 33 33 33 33 33 2e 33 33 33 33 33 33 33 2c 31
        65 2b 33 30 2c 34 2e 35 2c 30 2e 30 30 32 2c 31 65 2d 32 37
        5d 2c 22 73 74 72 69 6e 67 22 3a 22 e2 82 ac 24 5c 75 30 30
        30 66 5c 6e 41 27 42 5c 22 5c 5c 5c 5c 5c 22 2f 22 7d
        """
    )
    assert jcs_canonicalize(value) == expected


def test_rfc_utf16_property_sorting_example() -> None:
    value = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
    }
    encoded = jcs_canonicalize(value)
    assert list(json.loads(encoded).values()) == [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Hebrew Letter Dalet With Dagesh",
    ]


def test_supplementary_keys_sort_before_bmp_private_use_keys_recursively() -> None:
    value = {"\uffff": 3, "\ue000": 2, "\U00010000": 1}
    assert jcs_canonicalize([value]) == '[{"𐀀":1,"\ue000":2,"\uffff":3}]'.encode()


def test_strings_use_minimal_lowercase_control_escapes_and_raw_utf8() -> None:
    controls = "".join(chr(number) for number in range(32))
    expected = (
        r'"\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\b\t\n'
        r"\u000b\f\r\u000e\u000f\u0010\u0011\u0012\u0013\u0014\u0015"
        r'\u0016\u0017\u0018\u0019\u001a\u001b\u001c\u001d\u001e\u001f"'
    )
    assert jcs_canonicalize(controls) == expected.encode()
    assert jcs_canonicalize('"\\/é한😀\u2028\u2029') == '"\\"\\\\/é한😀\u2028\u2029"'.encode()


def test_unicode_normalization_is_not_applied() -> None:
    assert jcs_canonicalize("é") != jcs_canonicalize("e\u0301")
    assert tool_fingerprint({"é": 1}) != tool_fingerprint({"e\u0301": 1})


def test_ecmascript_decimal_placement_and_zero(subtests) -> None:
    cases = [
        (0.0, "0"),
        (-0.0, "0"),
        (4.0, "4"),
        (-4.5, "-4.5"),
        (1e20, "100000000000000000000"),
        (-1e21, "-1e+21"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (1e-8, "1e-8"),
        (0.00123, "0.00123"),
        (100.0, "100"),
    ]
    for case, (value, expected) in enumerate(cases):
        with subtests.test(case=case):
            assert jcs_canonicalize(value) == expected.encode()


def test_safe_python_integers_are_exact(subtests) -> None:
    for case, value in enumerate([-(2**53 - 1), -1, 0, 1, 2**53 - 1]):
        with subtests.test(case=case):
            assert jcs_canonicalize(value) == str(value).encode()


def test_unsafe_python_integers_are_rejected_without_rounding(subtests) -> None:
    for case, value in enumerate([-(2**53), 2**53, -(2**53 + 1), 2**53 + 1, 10**100]):
        with subtests.test(case=case), pytest.raises(JcsCanonicalizationError):
            jcs_canonicalize({"nested": [value]})


def test_literals_and_numeric_equivalents_have_ecmascript_meaning() -> None:
    assert jcs_canonicalize([True, False, None, 1, 1.0, -0.0]) == b"[true,false,null,1,1,0]"


class _Number(IntEnum):
    ONE = 1


@pytest.mark.parametrize(
    "value",
    [
        b"private-input",
        bytearray(b"private-input"),
        (1,),
        {1},
        Decimal("1"),
        _Number.ONE,
        UserDict(),
        FrozenJsonObject({"private-input": 1}),
    ],
)
def test_non_json_types_are_rejected_without_input_in_errors(value: object) -> None:
    with pytest.raises(JcsCanonicalizationError) as caught:
        jcs_canonicalize({"private-input": [value]})
    assert "private-input" not in str(caught.value)
    assert "private-input" not in repr(caught.value)


def test_non_string_keys_are_rejected_before_sorting(subtests) -> None:
    for case, key in enumerate([1, True, None, ("private-input",), _Number.ONE]):
        with subtests.test(case=case), pytest.raises(JcsCanonicalizationError):
            jcs_canonicalize({"ok": 1, key: "private-input"})


@pytest.mark.parametrize("value", ["\ud800", ["private-input\udfff"], {"\ud800": 1}])
def test_surrogates_are_rejected_without_original_exception(value: object) -> None:
    with pytest.raises(JcsCanonicalizationError) as caught:
        jcs_canonicalize(value)
    assert "private-input" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_parsed_utf16_surrogate_pair_is_valid_unicode() -> None:
    assert jcs_canonicalize(json.loads(r'"\ud83d\ude00"')) == '"😀"'.encode()


@pytest.mark.parametrize("array", [False, True])
def test_cycles_are_rejected_but_shared_children_are_allowed(array: bool) -> None:
    value: object
    if array:
        items: list[object] = []
        items.append(items)
        value = items
    else:
        mapping: dict[str, object] = {}
        mapping["private-input"] = mapping
        value = mapping
    with pytest.raises(JcsCanonicalizationError, match="순환"):
        jcs_canonicalize(value)
    child = {"nested": [0.5]}
    assert jcs_canonicalize([child, child]) == b'[{"nested":[0.5]},{"nested":[0.5]}]'


def test_excessive_depth_has_a_fixed_error_without_original_exception() -> None:
    value: object = "private-input"
    for _ in range(sys.getrecursionlimit() + 1):
        value = [value]
    with pytest.raises(JcsCanonicalizationError, match="중첩") as caught:
        jcs_canonicalize(value)
    assert "private-input" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_tool_fingerprint_hashes_the_whole_definition_without_mutation() -> None:
    tool = {"name": "read", "description": "a", "inputSchema": {"minimum": 0.5}}
    expected = hashlib.sha256(
        b'{"description":"a","inputSchema":{"minimum":0.5},"name":"read"}'
    ).hexdigest()
    assert tool_fingerprint(tool) == expected
    assert tool_fingerprint(tool) == tool_fingerprint(dict(reversed(list(tool.items()))))
    assert tool == {"name": "read", "description": "a", "inputSchema": {"minimum": 0.5}}
    assert len(expected) == 64
    assert set(expected) <= set("0123456789abcdef")


def test_each_tool_field_including_extensions_affects_fingerprint(subtests) -> None:
    for case, field in enumerate(
        ["name", "description", "inputSchema", "outputSchema", "annotations", "_meta", "extra"]
    ):
        with subtests.test(case=case):
            tool = {"name": "read", "description": "a", "inputSchema": {"minimum": 0.5}}
            changed = {**tool, field: "b"}
            assert tool_fingerprint(tool) != tool_fingerprint(changed)


def test_array_order_is_part_of_the_fingerprint() -> None:
    assert tool_fingerprint({"enum": ["a", "b"]}) != tool_fingerprint({"enum": ["b", "a"]})


def test_fingerprint_requires_a_plain_tool_object(subtests) -> None:
    for case, value in enumerate([None, "tool", [], 1, 1.0, True]):
        with subtests.test(case=case), pytest.raises(JcsCanonicalizationError):
            tool_fingerprint(value)


def test_nonfinite_values_accepted_by_default_json_parser_are_rejected(subtests) -> None:
    for case, literal in enumerate(["NaN", "Infinity", "-Infinity", "1e9999"]):
        with subtests.test(case=case), pytest.raises(JcsCanonicalizationError):
            tool_fingerprint(json.loads('{"minimum":' + literal + "}"))


_json_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=12)
_json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53 - 1), max_value=2**53 - 1)
    | st.floats(allow_nan=False, allow_infinity=False)
    | _json_text,
    lambda child: st.lists(child, max_size=5) | st.dictionaries(_json_text, child, max_size=5),
    max_leaves=25,
)


@settings(max_examples=200)
@given(_json_values)
def test_parsing_and_recanonicalizing_is_idempotent(value: object) -> None:
    encoded = jcs_canonicalize(value)
    # Binary64 integer-valued floats can serialize beyond Python's safe-int range.
    # Parse JSON numbers as ECMAScript Number, rather than as arbitrary-size ints.
    parsed = json.loads(encoded, parse_int=float)
    assert jcs_canonicalize(parsed) == encoded
    assert jcs_canonicalize(value) == encoded


@given(st.dictionaries(_json_text, _json_values, max_size=8))
def test_object_insertion_order_does_not_affect_fingerprint(value: dict[str, object]) -> None:
    assert tool_fingerprint(value) == tool_fingerprint(dict(reversed(list(value.items()))))
