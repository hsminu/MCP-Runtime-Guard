"""B3.11·CFG-01·I6/I9: 순수 바이트 변환. C 파일 연산/A 시작 연결은 제외한다."""

import json
from collections import UserDict
from copy import deepcopy
from inspect import signature
from itertools import product
from types import MappingProxyType

import pytest

from guard_app.services import catalog as catalog_module
from guard_app.services.catalog import (
    SETTINGS_MAX_BYTES,
    SETTINGS_MAX_DEPTH,
    CatalogConfigError,
    SettingsFileError,
    load_profile_settings,
    parse_settings_bytes,
    prepare_policy_change,
    serialize_settings_bytes,
)
from tests.unit.test_profile_loading import _document, _Registry

LIMIT = SETTINGS_MAX_BYTES
BOM = b"\xef\xbb\xbf"
PRIVATE = "PRIVATE_SETTINGS_CONTENT"


def _envelope(settings):
    return {"schema_version": 1, "settings": settings}


def _bytes(settings):
    return json.dumps(_envelope(settings), ensure_ascii=False).encode("utf-8")


def _at_depth(depth):
    # 봉투=1, settings=2, 이후 배열마다 1. leaf 자체는 깊이를 늘리지 않는다.
    return (
        b'{"schema_version":1,"settings":{"value":'
        + b"[" * (depth - 2)
        + b"null"
        + b"]" * (depth - 2)
        + b"}}"
    )


def test_public_limits_are_the_function_defaults_and_exports(subtests):
    for case_index, (operation,) in enumerate(
        product([parse_settings_bytes, serialize_settings_bytes])
    ):
        with subtests.test(case=case_index):
            assert SETTINGS_MAX_BYTES == 1_048_576
            assert SETTINGS_MAX_DEPTH == 64
            parameters = signature(operation).parameters
            assert parameters["max_bytes"].default == SETTINGS_MAX_BYTES
            assert parameters["max_depth"].default == SETTINGS_MAX_DEPTH
            assert {"SETTINGS_MAX_BYTES", "SETTINGS_MAX_DEPTH"} <= set(catalog_module.__all__)


def test_c_limited_read_sentinel_byte_is_rejected():
    payload = b" " * (SETTINGS_MAX_BYTES + 1)
    with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_LARGE$"):
        parse_settings_bytes(payload)


def test_prepared_document_round_trip_preserves_policy_version_and_input():
    registry = _Registry()
    before = _document()
    new = deepcopy(before)
    new["policy"]["approval_ttl_seconds"] = 301
    change = prepare_policy_change(before, new, registry)
    document = change.document.to_dict()
    snapshot = deepcopy(document)
    registry.queries.clear()
    encoded = serialize_settings_bytes(document)
    parsed = parse_settings_bytes(encoded)
    assert registry.queries == []  # 변환은 Registry에도 접근하지 않는다.
    assert parsed == document == snapshot
    assert parsed is not document
    assert parsed["servers"] is not document["servers"]
    loaded = load_profile_settings(parsed, registry)
    assert loaded.policy_version == change.policy_version
    assert loaded.guard_settings.approval_ttl_seconds == 301
    assert parsed["servers"][0]["max_label"] == "CONFIDENTIAL"
    parsed["servers"][0]["args"].append("local mutation")
    assert document == snapshot == change.document.to_dict()


def test_file_format_does_not_change_policy_version(subtests):
    for case_index, (reverse, indent, bom) in enumerate(
        product([False, True], [None, 2, 4], [b"", BOM])
    ):
        with subtests.test(case=case_index):
            document = _document()
            if reverse:
                document = dict(reversed(document.items()))
                document["profile"] = dict(reversed(document["profile"].items()))
            payload = (
                bom + b" \r\n" + json.dumps(_envelope(document), indent=indent).encode() + b"\t"
            )
            parsed = parse_settings_bytes(payload)
            assert (
                load_profile_settings(parsed, _Registry()).policy_version
                == load_profile_settings(_document(), _Registry()).policy_version
            )


def test_serializer_is_deterministic_utf8_sorted_and_preserves_values(subtests):
    for case_index, (wrapper,) in enumerate(product([dict, UserDict, MappingProxyType])):
        with subtests.test(case=case_index):
            settings = {"z": ["한글", "\r\n", 1.5, True, None], "a": {"z": 2, "a": 1}}
            before = deepcopy(settings)
            data = serialize_settings_bytes(wrapper(settings))
            assert data == serialize_settings_bytes(dict(reversed(settings.items())))
            assert not data.startswith(BOM)
            assert b"\r" not in data
            assert data.endswith(b"\n")
            assert not data.endswith(b"\n\n")
            assert "한글".encode() in data
            assert data.startswith(b'{\n  "schema_version": 1,\n  "settings": {\n    "a": {')
            assert settings == before
            assert parse_settings_bytes(data) == settings


def test_parser_leaves_profile_validation_to_existing_loader():
    document = parse_settings_bytes(_bytes({"not_a_profile": PRIVATE}))
    assert document == {"not_a_profile": PRIVATE}
    with pytest.raises(CatalogConfigError):
        load_profile_settings(document, _Registry())


def test_parse_byte_limit_includes_bom_and_whitespace(subtests):
    for case_index, (bom,) in enumerate(product([b"", BOM])):
        with subtests.test(case=case_index):
            data = bom + _bytes({})
            exact = data + b" " * (LIMIT - len(data))
            assert parse_settings_bytes(exact) == {}
            with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_LARGE$"):
                parse_settings_bytes(exact + b" ")


def test_serialize_byte_limit_includes_pretty_format_unicode_and_final_newline():
    settings = {"value": "한글"}
    data = serialize_settings_bytes(settings)
    assert serialize_settings_bytes(settings, max_bytes=len(data)) == data
    with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_LARGE$"):
        serialize_settings_bytes(settings, max_bytes=len(data) - 1)
    with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_LARGE$"):
        serialize_settings_bytes({"value": "x" * LIMIT})


def test_depth_64_is_allowed_and_65_is_rejected_for_both_directions():
    at_limit = parse_settings_bytes(_at_depth(64))
    assert parse_settings_bytes(serialize_settings_bytes(at_limit)) == at_limit
    for operation in (
        lambda: parse_settings_bytes(_at_depth(65)),
        lambda: serialize_settings_bytes({"nested": at_limit}),
    ):
        with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_DEEP$"):
            operation()


def test_depth_scanner_ignores_brackets_quotes_and_escaped_backslashes_in_strings():
    settings = {"[[[[": '[{]}"\\' * 100}
    assert parse_settings_bytes(_bytes(settings), max_depth=2) == settings
    assert parse_settings_bytes(serialize_settings_bytes(settings, max_depth=2)) == settings


def test_trusted_limits_apply_before_decode_or_json_parse(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("상한 위반에서 JSON 파서에 도달했다")

    monkeypatch.setattr(catalog_module.json, "loads", forbidden)
    for payload, options, code in (
        (b"\xff\xff", {"max_bytes": 1}, "SETTINGS_TOO_LARGE"),
        (_at_depth(65), {}, "SETTINGS_TOO_DEEP"),
    ):
        with pytest.raises(SettingsFileError, match=f"^{code}$"):
            parse_settings_bytes(payload, **options)


def test_invalid_trusted_limits_are_fixed_errors(subtests):
    for case_index, (value, option, operation) in enumerate(
        product(
            [0, -1, True, False, 1.0, "64", None],
            ["max_bytes", "max_depth"],
            [parse_settings_bytes, serialize_settings_bytes],
        )
    ):
        with subtests.test(case=case_index):
            argument = _bytes({}) if operation is parse_settings_bytes else {}
            with pytest.raises(SettingsFileError, match="^SETTINGS_LIMIT_INVALID$"):
                operation(argument, **{option: value})


def test_small_valid_depth_limit_rejects_envelope_settings_object():
    for operation, argument in ((parse_settings_bytes, _bytes({})), (serialize_settings_bytes, {})):
        with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_DEEP$"):
            operation(argument, max_depth=1)
    assert parse_settings_bytes(_bytes({}), max_depth=2) == {}


def test_parse_rejects_invalid_input_with_only_fixed_code(subtests):
    for case_index, ((payload, code),) in enumerate(
        product(
            [
                (
                    b'{"schema_version":1,"schema_version":1,"settings":{}}',
                    "SETTINGS_DUPLICATE_KEY",
                ),
                (b'{"schema_version":1,"settings":{"x":{"a":1,"a":2}}}', "SETTINGS_DUPLICATE_KEY"),
                (
                    b'{"schema_version":1,"settings":{"x":{"a":1,"\\u0061":2}}}',
                    "SETTINGS_DUPLICATE_KEY",
                ),
                (
                    b'{"schema_version":1,"settings":{"x":[{"a":1,"a":2}]}}',
                    "SETTINGS_DUPLICATE_KEY",
                ),
                (BOM * 2 + b'{"schema_version":1,"settings":{}}', "SETTINGS_JSON_INVALID"),
                (b"\xffPRIVATE", "SETTINGS_ENCODING_INVALID"),
                (
                    '{"schema_version":1,"settings":{}}'.encode("utf-16"),
                    "SETTINGS_ENCODING_INVALID",
                ),
                (b'{"schema_version":1,"settings":{"x":"\\ud800"}}', "SETTINGS_ENCODING_INVALID"),
                (b'{"schema_version":1,"settings":{"\\udfff":1}}', "SETTINGS_ENCODING_INVALID"),
                (b'{"schema_version":1,"settings":{"x":1e999}}', "SETTINGS_NONFINITE_NUMBER"),
                (b'{"schema_version":1,"settings":{"x":-1e999}}', "SETTINGS_NONFINITE_NUMBER"),
                (
                    b'{"schema_version":1,"settings":{"x":9007199254740992}}',
                    "SETTINGS_NUMBER_RANGE",
                ),
                (
                    b'{"schema_version":1,"settings":{"x":-9007199254740992}}',
                    "SETTINGS_NUMBER_RANGE",
                ),
                (b"", "SETTINGS_JSON_INVALID"),
                (b'{"schema_version":1,"settings":{}}{}', "SETTINGS_JSON_INVALID"),
                (b'{"schema_version":1,"settings":{"x":"\x00"}}', "SETTINGS_JSON_INVALID"),
                (b'{"schema_version":1,"settings":{"x":1,}}', "SETTINGS_JSON_INVALID"),
            ]
        )
    ):
        with subtests.test(case=case_index):
            with pytest.raises(SettingsFileError, match=f"^{code}$") as caught:
                parse_settings_bytes(payload)
            assert caught.value.code == code
            assert caught.value.args == (code,)
            assert caught.value.__cause__ is None
            assert "PRIVATE" not in str(caught.value) + repr(caught.value)


def test_nonstandard_numbers_are_rejected_even_in_nested_objects(subtests):
    for case_index, (token,) in enumerate(product(["NaN", "Infinity", "-Infinity"])):
        with subtests.test(case=case_index):
            payload = '{"schema_version":1,"settings":{"nested":[{"value":' + token + "}]}}"
            with pytest.raises(SettingsFileError, match="^SETTINGS_NONFINITE_NUMBER$"):
                parse_settings_bytes(payload.encode())


def test_invalid_envelopes_are_not_coerced_or_defaulted(subtests):
    for case_index, (envelope,) in enumerate(
        product(
            [
                {},
                {"settings": {}},
                {"schema_version": 1},
                {"schema_version": 1, "settings": {}, "extra": PRIVATE},
                {"schema_version": True, "settings": {}},
                {"schema_version": "1", "settings": {}},
                {"schema_version": 1.0, "settings": {}},
                *({"schema_version": 1, "settings": value} for value in [None, [], "x", 1, True]),
                [],
                None,
            ]
        )
    ):
        with (
            subtests.test(case=case_index),
            pytest.raises(SettingsFileError, match="^SETTINGS_ENVELOPE_INVALID$"),
        ):
            parse_settings_bytes(json.dumps(envelope).encode())


def test_unsupported_integer_versions_do_not_fall_back(subtests):
    for case_index, (version,) in enumerate(product([0, 2, -1])):
        with (
            subtests.test(case=case_index),
            pytest.raises(SettingsFileError, match="^SETTINGS_VERSION_UNSUPPORTED$"),
        ):
            parse_settings_bytes(json.dumps({"schema_version": version, "settings": {}}).encode())


def test_serializer_rejects_non_json_and_out_of_range_values(subtests):
    for case_index, ((value, code),) in enumerate(
        product(
            [
                (float("nan"), "SETTINGS_NONFINITE_NUMBER"),
                (float("inf"), "SETTINGS_NONFINITE_NUMBER"),
                (float("-inf"), "SETTINGS_NONFINITE_NUMBER"),
                (2**53, "SETTINGS_NUMBER_RANGE"),
                (-(2**53), "SETTINGS_NUMBER_RANGE"),
                ("\ud800", "SETTINGS_ENCODING_INVALID"),
                (b"PRIVATE", "SETTINGS_JSON_INVALID"),
                ((1, 2), "SETTINGS_JSON_INVALID"),
                ({1, 2}, "SETTINGS_JSON_INVALID"),
                (object(), "SETTINGS_JSON_INVALID"),
                ({1: PRIVATE}, "SETTINGS_JSON_INVALID"),
            ]
        )
    ):
        with subtests.test(case=case_index), pytest.raises(SettingsFileError, match=f"^{code}$"):
            serialize_settings_bytes({"value": value})


def test_serializer_rejects_cycles_but_copies_shared_values_without_mutation():
    shared = {"value": [1]}
    assert parse_settings_bytes(serialize_settings_bytes({"a": shared, "b": shared})) == {
        "a": shared,
        "b": shared,
    }
    cycle = []
    cycle.append(cycle)
    with pytest.raises(SettingsFileError, match="^SETTINGS_JSON_INVALID$"):
        serialize_settings_bytes({"cycle": cycle})


def test_json_numbers_remain_typed_and_jcs_boundary_values_round_trip():
    settings = {"values": [-(2**53 - 1), 2**53 - 1, 1e100, 300.0, -0.0]}
    result = parse_settings_bytes(serialize_settings_bytes(settings))
    assert result == settings
    assert type(result["values"][3]) is float
    invalid_ttl = _document()
    invalid_ttl["policy"]["approval_ttl_seconds"] = 300.0
    with pytest.raises(CatalogConfigError):
        load_profile_settings(parse_settings_bytes(_bytes(invalid_ttl)), _Registry())


def test_error_does_not_log_or_chain_raw_input(caplog):
    for payload in (
        b'{"schema_version":1,"settings":{"' + PRIVATE.encode() + b'":}}',
        b"\xff" + PRIVATE.encode(),
        _bytes({PRIVATE: "x"})[:-1],
    ):
        with pytest.raises(SettingsFileError) as caught:
            parse_settings_bytes(payload)
        assert PRIVATE not in str(caught.value) + repr(caught.value) + repr(vars(caught.value))
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__
    assert PRIVATE not in caplog.text
    assert PRIVATE not in repr(SettingsFileError(PRIVATE))


def test_untrusted_file_cannot_override_limits():
    settings = {"max_bytes": LIMIT * 2, "max_depth": 1000}
    settings["value"] = json.loads(b"[" * 63 + b"0" + b"]" * 63)
    with pytest.raises(SettingsFileError, match="^SETTINGS_TOO_DEEP$"):
        parse_settings_bytes(_bytes(settings))


def test_parser_rejects_very_large_integer_without_using_unbounded_int_conversion():
    payload = b'{"schema_version":1,"settings":{"n":' + b"9" * 5000 + b"}}"
    with pytest.raises(SettingsFileError, match="^SETTINGS_NUMBER_RANGE$"):
        parse_settings_bytes(payload)
