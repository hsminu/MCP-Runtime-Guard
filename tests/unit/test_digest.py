"""8.1절·I9/T09/T10: 승인 바이트 의미와 부적절한 값 거부의 단위 검증."""

from collections import UserDict
from decimal import Decimal
from enum import IntEnum

import pytest
from hypothesis import given
from hypothesis import strategies as st

from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.security.digest import CanonicalJsonError, canonical_json, digest, json_digest


def test_canonical_utf8_sorted_keys_and_no_whitespace() -> None:
    value = {"z": [True, None, 4, {"나": "가"}], "a": "á"}
    assert canonical_json(value) == '{"a":"á","z":[true,null,4,{"나":"가"}]}'.encode()


def test_hash_has_sha256_known_answers() -> None:
    assert digest(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert digest(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_frozen_objects_and_arguments_match_plain_json() -> None:
    value = {"text": "private", "nested": [{"a": 3}]}
    assert canonical_json(FrozenJsonObject(value)) == canonical_json(value)
    assert json_digest(Arguments(value)) == digest(canonical_json(value))


def test_unicode_nfc_is_not_applied() -> None:
    assert canonical_json("é") == b'"\xc3\xa9"'
    assert canonical_json("e\u0301") == b'"e\xcc\x81"'
    assert json_digest("é") != json_digest("e\u0301")


def test_bool_and_integer_and_null_remain_distinct() -> None:
    assert len({json_digest(True), json_digest(1), json_digest(None), json_digest("1")}) == 4


@pytest.mark.parametrize("value", [0.0, 1.0, -4.5, float("inf"), float("nan")])
def test_any_float_is_rejected_in_nested_values(value: float) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json({"nested": [value]})


def test_frozen_metadata_floats_are_also_rejected() -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(FrozenJsonObject({"limit": 2.0}))


class _Number(IntEnum):
    ONE = 1


@pytest.mark.parametrize(
    "value", [b"secret", bytearray(b"secret"), (1,), {1}, Decimal("1"), _Number.ONE, UserDict()]
)
def test_non_json_types_are_not_coerced(value: object) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(value)


@pytest.mark.parametrize("value", [{1: "secret"}, {True: "secret"}, {None: "secret"}])
def test_non_string_keys_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(value)


@pytest.mark.parametrize("value", ["\ud800", {"\udfff": "secret"}, ["x\ud800y"]])
def test_non_utf8_strings_are_rejected_without_original(value: object) -> None:
    with pytest.raises(CanonicalJsonError) as caught:
        canonical_json(value)
    assert "secret" not in str(caught.value)
    assert "x\ud800y" not in str(caught.value)


@pytest.mark.parametrize("array", [False, True])
def test_cycles_are_rejected(array: bool) -> None:
    value: object
    if array:
        items: list[object] = []
        items.append(items)
        value = items
    else:
        mapping: dict[str, object] = {}
        mapping["secret"] = mapping
        value = mapping
    with pytest.raises(CanonicalJsonError, match="순환"):
        canonical_json(value)


def test_shared_acyclic_child_is_valid() -> None:
    child = {"a": [1]}
    assert canonical_json([child, child]) == b'[{"a":[1]},{"a":[1]}]'


def test_depth_limit_rejects_excess_nesting() -> None:
    value: object = 1
    for _ in range(65):
        value = [value]
    with pytest.raises(CanonicalJsonError, match="중첩"):
        canonical_json(value)


@pytest.mark.parametrize("value", ["abc", bytearray(b"abc"), memoryview(b"abc"), None, 1])
def test_digest_only_accepts_exact_bytes(value: object) -> None:
    with pytest.raises(CanonicalJsonError):
        digest(value)  # type: ignore[arg-type]


@given(st.dictionaries(st.text(alphabet="aé한", max_size=8), st.integers(), max_size=12))
def test_key_insertion_order_does_not_affect_hash(value: dict[str, int]) -> None:
    reversed_value = dict(reversed(list(value.items())))
    assert canonical_json(value) == canonical_json(reversed_value)
    assert json_digest(value) == json_digest(reversed_value)


@given(st.floats(allow_nan=True, allow_infinity=True))
def test_property_float_rejection(value: float) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(value)
