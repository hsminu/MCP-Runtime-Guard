"""EXE-01 설정의 무고정 호환성·형식·정책 버전 및 순수 저장 검증."""

from copy import deepcopy

import pytest

from guard_app.services.catalog import (
    CatalogConfigError,
    load_profile_settings,
    parse_settings_bytes,
    serialize_settings_bytes,
)
from tests.unit.test_profile_loading import _document, _Registry

BASELINE_POLICY_VERSION = "70571a21f71d7ec0493867c597db21d0f6b2e6d12f022b34092026faa319d062"
PIN_SHA256 = "a" * 64
MAX_SAFE_SIZE = 2**53 - 1


def _load(document):
    return load_profile_settings(document, _Registry())


def _pinned_document():
    document = _document()
    document["servers"][0]["command_sha256"] = PIN_SHA256
    document["servers"][0]["command_size"] = 42
    return document


def test_unpinned_stdio_preserves_existing_policy_version_and_has_no_pin():
    loaded = _load(_document())
    assert loaded.policy_version == BASELINE_POLICY_VERSION
    assert loaded.servers[0].command_sha256 is None
    assert loaded.servers[0].command_size is None


@pytest.mark.parametrize("size", [1, 42, MAX_SAFE_SIZE])
def test_stdio_pin_round_trips_fields_and_changes_policy_version(size):
    document = _pinned_document()
    document["servers"][0]["command_size"] = size
    loaded = _load(document)
    assert loaded.servers[0].command_sha256 == PIN_SHA256
    assert loaded.servers[0].command_size == size
    assert loaded.policy_version != BASELINE_POLICY_VERSION


def test_fingerprint_change_alone_changes_policy_version():
    document = _pinned_document()
    changed = deepcopy(document)
    changed["servers"][0]["command_sha256"] = PIN_SHA256[:-1] + "b"
    assert _load(document).policy_version != _load(changed).policy_version


@pytest.mark.parametrize(
    "pin",
    [
        {"command_sha256": PIN_SHA256},
        {"command_size": 42},
        {"command_sha256": None, "command_size": 42},
        {"command_sha256": PIN_SHA256, "command_size": None},
        {"command_sha256": None, "command_size": None},
        {"command_sha256": PIN_SHA256.upper(), "command_size": 42},
        {"command_sha256": "a" * 63, "command_size": 42},
        {"command_sha256": "a" * 65, "command_size": 42},
        {"command_sha256": "g" * 64, "command_size": 42},
        {"command_sha256": PIN_SHA256 + "\n", "command_size": 42},
        {"command_sha256": PIN_SHA256, "command_size": 0},
        {"command_sha256": PIN_SHA256, "command_size": -1},
        {"command_sha256": PIN_SHA256, "command_size": True},
        {"command_sha256": PIN_SHA256, "command_size": 1.0},
        {"command_sha256": PIN_SHA256, "command_size": 2**53},
    ],
)
def test_invalid_stdio_pin_rejects_entire_configuration(pin):
    document = _document()
    document["servers"][0].update(pin)
    with pytest.raises(CatalogConfigError) as error:
        _load(document)
    assert PIN_SHA256 not in str(error.value)
    assert document["servers"][0]["command"] not in str(error.value)


@pytest.mark.parametrize(
    "pin",
    [
        {"command_sha256": PIN_SHA256},
        {"command_size": 42},
        {"command_sha256": PIN_SHA256, "command_size": 42},
        {"command_sha256": None},
        {"command_size": None},
        {"command_sha256": None, "command_size": None},
    ],
)
def test_http_rejects_any_pin_field_including_explicit_null(pin):
    document = _document()
    document["servers"][1].update(pin)
    with pytest.raises(CatalogConfigError):
        _load(document)


@pytest.mark.parametrize("pinned", [False, True])
def test_serialized_settings_round_trip_preserves_fields_and_policy(pinned):
    document = _pinned_document() if pinned else _document()
    before = _load(document)
    encoded = serialize_settings_bytes(document)
    parsed = parse_settings_bytes(encoded)
    assert parsed == document
    loaded = _load(parsed)
    assert loaded.policy_version == before.policy_version
    assert loaded.servers[0].command_sha256 == before.servers[0].command_sha256
    assert loaded.servers[0].command_size == before.servers[0].command_size
    if not pinned:
        assert "command_sha256" not in parsed["servers"][0]
        assert "command_size" not in parsed["servers"][0]


def test_server_repr_does_not_expose_path_or_pin():
    document = _pinned_document()
    server = _load(document).servers[0]
    assert PIN_SHA256 not in repr(server)
    assert document["servers"][0]["command"] not in repr(server)
    assert PIN_SHA256 not in str(server)
