"""SCHEMA-01 제안 포트 형태와 시험 전용 동기 작업 대역."""

import inspect
from typing import get_type_hints

from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.schema_jobs import SchemaJobRequest, SchemaJobResponse
from guard_app.ports import SchemaJobRunner
from guard_app.services.schema_jobs import (
    check_job_response,
    decode_response,
    encode_request,
    run_schema_job,
)


def _synchronous_job_double(request: SchemaJobRequest) -> SchemaJobResponse:
    """직접 호출은 시험 전용이다. deadline·프로세스 종료를 구현하지 않는다."""
    return check_job_response(request, decode_response(run_schema_job(encode_request(request))))


def test_schema_job_runner_is_async_with_keyword_only_deadline():
    assert inspect.iscoroutinefunction(SchemaJobRunner.run)
    signature = inspect.signature(SchemaJobRunner.run)
    assert tuple(signature.parameters) == ("self", "request", "deadline")
    assert signature.parameters["deadline"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["deadline"].default is inspect.Parameter.empty
    annotations = get_type_hints(SchemaJobRunner.run)
    assert annotations == {
        "request": SchemaJobRequest,
        "deadline": float,
        "return": SchemaJobResponse,
    }


def test_synchronous_test_double_uses_request_response_contract_without_payload_leak():
    schema = FrozenJsonObject({"type": "object", "properties": {"count": {"type": "integer"}}})
    prepared = _synchronous_job_double(
        SchemaJobRequest(kind="PREPARE", job_id="prepare-1", schema=schema)
    )
    assert prepared.code == "OK"
    assert prepared.prepared is not None
    arguments = Arguments({"count": "private-marker"})
    result = _synchronous_job_double(
        SchemaJobRequest(
            kind="VALIDATE", job_id="validate-1", schema=prepared.prepared, arguments=arguments
        )
    )
    assert result.job_id == "validate-1"
    assert result.code == "INVALID_ARGUMENTS"
    assert result.prepared is None
    assert "private-marker" not in repr(result)
