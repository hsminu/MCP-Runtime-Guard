"""SCHEMA-01 제안: 요청·응답의 제한된 JCS 경계와 순수 진입점."""

import json
import math
import random
import struct

import pytest
from pydantic import ValidationError

from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.schema_jobs import (
    INVALID_JOB_ID,
    MAX_JOB_BYTES,
    MAX_JOB_DEPTH,
    MAX_JOB_ELEMENTS,
    MAX_REGISTERED_SCHEMA_BYTES,
    SchemaJobRequest,
    SchemaJobResponse,
    _jcs_size,
)
from guard_app.security.digest import jcs_canonicalize
from guard_app.services.catalog_schema import ArgumentSchema
from guard_app.services.schema_jobs import (
    SchemaJobCodecError,
    SchemaJobResult,
    check_job_response,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
    run_schema_job,
    validate,
)


def _request(*, schema=None, arguments=None, kind="VALIDATE"):
    return SchemaJobRequest(
        kind=kind,
        job_id="job-1",
        schema=FrozenJsonObject({"type": "object"} if schema is None else schema),
        arguments=Arguments({} if arguments is None else arguments) if kind == "VALIDATE" else None,
    )


def _response(schema):
    return SchemaJobResponse(job_id="job-1", code="OK", prepared=FrozenJsonObject(schema))


def _nodes(value):
    if type(value) is dict:
        return 1 + sum(_nodes(child) for child in value.values())
    if type(value) is list:
        return 1 + sum(_nodes(child) for child in value)
    return 1


def _message_with_padding(message_type, count):
    if message_type == "request":
        return _request(arguments={"padding": "x" * count})
    return _response({"type": "object", "padding": "x" * count})


def _message_with_items(message_type, count):
    if message_type == "request":
        return _request(arguments={"items": [None] * count})
    return _response({"type": "object", "items": [None] * count})


@pytest.mark.parametrize("size_delta", [-1, 0, 1])
def test_prepare_schema_uses_exact_registry_jcs_size(size_delta):
    from guard_app.storage.sqlite_store import _MAX_SCHEMA_BYTES

    assert MAX_REGISTERED_SCHEMA_BYTES == _MAX_SCHEMA_BYTES
    base = {"type": "object", "description": ""}
    count = MAX_REGISTERED_SCHEMA_BYTES + size_delta - len(jcs_canonicalize(base))
    schema = {"type": "object", "description": "x" * count}
    assert len(jcs_canonicalize(schema)) == MAX_REGISTERED_SCHEMA_BYTES + size_delta
    if size_delta <= 0:
        assert _request(schema=schema, kind="PREPARE").schema_data.to_dict() == schema
    else:
        with pytest.raises(ValidationError):
            _request(schema=schema, kind="PREPARE")


@pytest.mark.parametrize(
    "value",
    [
        {"type": "object", "title": '한글😀\\"\n'},
        {"type": "object", "default": [1e-7, 1e-6, 1e20, 1e21, -0.0, 0.000123]},
        {"type": "object", "properties": {"😀": {"default": -9007199254740991}}},
    ],
)
def test_registered_schema_size_matches_jcs_for_unicode_and_numbers(value):
    frozen = FrozenJsonObject(value)
    assert _jcs_size(frozen) == len(jcs_canonicalize(value))


def test_registered_schema_size_matches_jcs_for_varied_binary64():
    random_source = random.Random(20261029)  # noqa: S311 -- 합성 시험 데이터다.
    for _ in range(4096):
        number = struct.unpack(">d", random_source.getrandbits(64).to_bytes(8, "big"))[0]
        if not math.isfinite(number):
            continue
        schema = {"type": "object", "default": number, "title": '한글😀\n\\"'}
        assert _jcs_size(FrozenJsonObject(schema)) == len(jcs_canonicalize(schema))


@pytest.mark.parametrize("message_type", ["request", "response"])
@pytest.mark.parametrize("size_delta", [-1, 0, 1])
def test_message_byte_limit_on_encoding_and_decoding(message_type, size_delta):
    if message_type == "request":
        encode, decode = encode_request, decode_request
        base = _request(arguments={"padding": ""})
    else:
        encode, decode = encode_response, decode_response
        base = _response({"type": "object", "padding": ""})
    count = MAX_JOB_BYTES + size_delta - len(encode(base))
    message = _message_with_padding(message_type, count)
    if size_delta <= 0:
        encoded = encode(message)
        assert len(encoded) == MAX_JOB_BYTES + size_delta
        assert decode(encoded) == message
    else:
        with pytest.raises(SchemaJobCodecError):
            encode(message)
        # Exactly one excess byte must be refused before parsing as well.
        max_encoded = encode(_message_with_padding(message_type, count - 1))
        with pytest.raises(SchemaJobCodecError):
            decode(max_encoded + b" ")


@pytest.mark.parametrize("message_type", ["request", "response"])
@pytest.mark.parametrize("count_delta", [-1, 0, 1])
def test_message_element_limit_on_encoding_and_decoding(message_type, count_delta):
    if message_type == "request":
        encode, decode = encode_request, decode_request
        base = _request(arguments={"items": []})
    else:
        encode, decode = encode_response, decode_response
        base = _response({"type": "object", "items": []})
    baseline = _nodes(json.loads(encode(base)))
    count = MAX_JOB_ELEMENTS + count_delta - baseline
    message = _message_with_items(message_type, count)
    if count_delta <= 0:
        encoded = encode(message)
        assert _nodes(json.loads(encoded)) == MAX_JOB_ELEMENTS + count_delta
        assert decode(encoded) == message
    else:
        with pytest.raises(SchemaJobCodecError):
            encode(message)
        raw = json.loads(encode(_message_with_items(message_type, count - 1)))
        if message_type == "request":
            raw["arguments"]["items"].append(None)
        else:
            raw["prepared"]["items"].append(None)
        with pytest.raises(SchemaJobCodecError):
            decode(jcs_canonicalize(raw))


def _schema_at_wire_depth(depth):
    value = "leaf"
    # envelope(0) -> schema object(1) -> annotation values -> leaf(depth)
    for _ in range(depth - 2):
        value = {"child": value}
    return {"type": "object", "default": value}


@pytest.mark.parametrize("message_type", ["request", "response"])
@pytest.mark.parametrize("depth_delta", [0, 1])
def test_message_depth_64_or_65_on_encoding_and_decoding(message_type, depth_delta):
    schema = _schema_at_wire_depth(MAX_JOB_DEPTH + depth_delta)
    if message_type == "request":
        encode, decode = encode_request, decode_request
        message = _request(schema=schema)
    else:
        encode, decode = encode_response, decode_response
        message = _response(schema)
    if depth_delta == 0:
        encoded = encode(message)
        assert decode(encoded) == message
    else:
        with pytest.raises(SchemaJobCodecError):
            encode(message)
        payload = message.model_dump(mode="json", by_alias=True)
        with pytest.raises(SchemaJobCodecError):
            decode(jcs_canonicalize(payload))


def test_unsafe_integer_keeps_legacy_catalog_behavior_but_wire_encode_fails():
    schema = {"type": "object", "properties": {"n": {"const": 2**53}}}
    assert ArgumentSchema(FrozenJsonObject(schema)).validate(Arguments({"n": 2**53})) is None
    request = _request(schema=schema, kind="PREPARE")
    with pytest.raises(SchemaJobCodecError) as error:
        encode_request(request)
    assert str(2**53) not in str(error.value)


def test_request_schema_alias_round_trips_as_schema_key():
    request = _request(schema={"type": "object", "properties": {"v": {"type": "integer"}}})
    encoded = encode_request(request)
    payload = json.loads(encoded)
    assert "schema" in payload
    assert "schema_data" not in payload
    assert decode_request(encoded) == request


def test_request_rejects_alias_duplicate_or_internal_field_on_wire():
    request = _request()
    payload = json.loads(encode_request(request))
    payload["schema_data"] = payload["schema"]
    with pytest.raises(SchemaJobCodecError):
        decode_request(jcs_canonicalize(payload))
    del payload["schema"]
    with pytest.raises(SchemaJobCodecError):
        decode_request(jcs_canonicalize(payload))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":"OK","job_id":"x","extra":"secret"}',
        b'{"code":"UNKNOWN","job_id":"x"}',
        b'{"code":"OK","code":"SCHEMA_INVALID","job_id":"x"}',
        b'{"code":"SCHEMA_INVALID","job_id":"x","prepared":{"token":"secret"}}',
        b"\xff",
    ],
)
def test_parent_rejects_malformed_responses_without_original(raw):
    with pytest.raises(SchemaJobCodecError) as error:
        decode_response(raw)
    assert "secret" not in str(error.value)


def test_parent_rejects_response_from_other_job_and_wrong_result_shape():
    request = _request(kind="PREPARE")
    with pytest.raises(SchemaJobCodecError):
        check_job_response(request, SchemaJobResponse(job_id="other", code="OK"))
    with pytest.raises(SchemaJobCodecError):
        check_job_response(request, SchemaJobResponse(job_id=request.job_id, code="OK"))
    with pytest.raises(SchemaJobCodecError):
        check_job_response(
            request, SchemaJobResponse(job_id=request.job_id, code="INVALID_ARGUMENTS")
        )


@pytest.mark.parametrize(
    ("schema", "arguments"),
    [
        ({"type": "bogus"}, {"value": "private-marker"}),
        ({"type": "object", "properties": {"x": {"multipleOf": 0}}}, {"x": 1}),
    ],
)
def test_validate_malformed_prepared_schema_returns_fixed_failure_without_exception_text(
    capsys, schema, arguments
):
    request = _request(schema=schema, arguments=arguments)
    output = run_schema_job(encode_request(request))
    response = decode_response(output)
    assert response.job_id == request.job_id
    assert response.code == "SCHEMA_INVALID"
    assert response.prepared is None
    assert b"private-marker" not in output
    assert b"bogus" not in output
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"x": 4}},
        {"type": "object", "properties": {"x": "invalid"}},
        {"type": "object", "properties": {"x": None}},
        {"type": "object", "properties": None},
        {"type": "object", "propertyNames": 4},
        {"type": "object", "allOf": [4]},
        {"type": "object", "additionalProperties": 4},
        {"type": "object", "required": [None]},
        {"type": "object", "properties": {"x": {"enum": None}}},
        {"type": "object", "properties": {"x": {"$ref": "https://secret.invalid"}}},
    ],
)
def test_invalid_prepared_schema_never_looks_like_success(schema, capsys):
    request = _request(schema=schema, arguments={"x": "private-argument-marker"})
    output = run_schema_job(encode_request(request))
    response = decode_response(output)
    assert response.job_id == request.job_id
    assert response.code == "SCHEMA_INVALID"
    assert response.prepared is None
    assert b"private-argument-marker" not in output
    assert b"https://secret.invalid" not in output
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "bogus"},
        {"type": "object", "properties": {"x": 4}},
        {"type": "object", "properties": {"x": None}},
        {"type": "object", "properties": None},
        {"type": "object", "propertyNames": 4},
        {"type": "object", "allOf": [4]},
        {"type": "object", "additionalProperties": 4},
        {"type": "object", "required": [None]},
        {"type": "object", "properties": {"x": {"enum": None}}},
        {"type": "object", "properties": {"x": {"multipleOf": 0}}},
        {"type": "object", "properties": {"x": {"$ref": "https://secret.invalid"}}},
    ],
)
def test_direct_validate_rejects_malformed_prepared_schema_without_original(schema, capsys):
    result = validate(FrozenJsonObject(schema), Arguments({"x": "private-argument-marker"}))
    assert result is SchemaJobResult.SCHEMA_INVALID
    assert "secret.invalid" not in repr(result)
    assert "private-argument-marker" not in repr(result)
    assert capsys.readouterr() == ("", "")


def test_prepared_schema_keeps_ref_as_const_data():
    schema = {
        "type": "object",
        "properties": {"x": {"const": {"$ref": "https://data.invalid"}}},
    }
    prepared_response = decode_response(
        run_schema_job(encode_request(_request(kind="PREPARE", schema=schema)))
    )
    assert prepared_response.code == "OK"
    assert prepared_response.prepared is not None
    request = SchemaJobRequest(
        kind="VALIDATE",
        job_id="job-1",
        schema=prepared_response.prepared,
        arguments=Arguments({"x": {"$ref": "https://data.invalid"}}),
    )
    response = decode_response(run_schema_job(encode_request(request)))
    assert response.code == "OK"


def test_prepared_response_that_exceeds_element_limit_is_fixed_failure(capsys):
    # 원본 AND가 사본을 한 번 더 싣기 때문에 정상 요청이라도 응답 상한을 넘을 수 있다.
    request = _request(kind="PREPARE", schema={"type": "object", "default": [None] * 50_000})
    encoded = encode_request(request)
    assert _nodes(json.loads(encoded)) < MAX_JOB_ELEMENTS
    output = run_schema_job(encoded)
    response = decode_response(output)
    assert response.job_id == request.job_id
    assert response.code == "SCHEMA_INVALID"
    assert response.prepared is None
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b'{"kind":"PREPARE","kind":"VALIDATE"}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"\xff",
        b'{"kind":"OTHER","job_id":"x","schema":{}}',
        b'{"kind":"PREPARE","job_id":"x","schema":{},"extra":"secret"}',
    ],
)
def test_invalid_requests_get_fixed_failure_and_no_original(raw, capsys):
    response_bytes = run_schema_job(raw)
    assert response_bytes == run_schema_job(raw)
    response = decode_response(response_bytes)
    assert response.job_id == INVALID_JOB_ID
    assert response.code == "SCHEMA_INVALID"
    assert response.prepared is None
    assert b"secret" not in response_bytes
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("kind", ["PREPARE", "VALIDATE"])
def test_entry_is_deterministic_and_does_not_echo_arguments_or_write_output(kind, capsys):
    marker = "private-argument-marker"
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    if kind == "PREPARE":
        request = _request(schema=schema, kind=kind)
    else:
        prepared = decode_response(
            run_schema_job(encode_request(_request(schema=schema, kind="PREPARE")))
        )
        assert prepared.prepared is not None
        request = SchemaJobRequest(
            kind="VALIDATE",
            job_id="job-2",
            schema=prepared.prepared,
            arguments=Arguments({"x": marker}),
        )
    encoded = encode_request(request)
    response = run_schema_job(encoded)
    assert response == run_schema_job(encoded)
    decoded = decode_response(response)
    assert decoded.job_id == request.job_id
    assert marker.encode() not in response
    assert marker not in repr(request)
    assert marker not in repr(decoded)
    assert capsys.readouterr() == ("", "")
