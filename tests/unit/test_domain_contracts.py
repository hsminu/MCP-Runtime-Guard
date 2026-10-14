"""B1 DTO의 I3/I6/I7/I9 입력 경계를 검증한다.

DB 원자성, 응답 커밋 뒤 공개, 실제 전송 횟수는 검증하지 않는다.
"""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from guard_app.domain.contracts import (
    ApprovalRecord,
    ConversationMessage,
    Denied,
    Finding,
    ModelTurn,
    Outcome,
    OutcomeUnknown,
    Pending,
    RawResult,
    RegisteredServer,
    Released,
    ReleasedResult,
    ScanReport,
    ToolView,
    Withheld,
)
from guard_app.domain.enums import ApprovalStatus, Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.models import Boundary, ProposedCall, ToolSpec
from guard_app.domain.reasons import Reason

CREATED = datetime(2026, 10, 11, 12, tzinfo=UTC)
EXPIRES = CREATED + timedelta(minutes=5)


def make_approval(**updates: object) -> ApprovalRecord:
    values: dict[str, object] = {
        "approval_id": "approval-1",
        "request_id": "request-1",
        "snapshot_hash": "a" * 64,
        "approver_id": "operator-1",
        "status": ApprovalStatus.PENDING,
        "created_at_utc": CREATED,
        "expires_at_utc": EXPIRES,
    }
    values.update(updates)
    return ApprovalRecord.model_validate(values)


def make_tool(**updates: object) -> ToolSpec:
    values: dict[str, object] = {
        "server_id": "docs",
        "tool_name": "read_document",
        "kind": ToolKind.READ,
        "input_schema": {"type": "object"},
        "destination_arg": None,
        "resource_arg": "path",
        "fingerprint": "a" * 64,
    }
    values.update(updates)
    return ToolSpec.model_validate(values)


def make_registration(**updates: object) -> RegisteredServer:
    values: dict[str, object] = {
        "server_id": "docs",
        "status": RegStatus.ACTIVE,
        "boundary": Boundary(
            boundary_id="docs", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
        ),
        "tools": (make_tool(),),
    }
    values.update(updates)
    return RegisteredServer.model_validate(values)


def make_scan(**updates: object) -> ScanReport:
    values: dict[str, object] = {
        "server_id": "docs",
        "fingerprint_set": ("a" * 64,),
        "analyzers_required": ("offline-rules",),
        "analyzers_completed": ("offline-rules",),
        "analyzers_skipped": (),
        "analyzers_failed": (),
        "max_severity": None,
        "findings": (),
        "scanned_at_utc": CREATED,
        "scanner_version": "test-version",
    }
    values.update(updates)
    return ScanReport.model_validate(values)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"failed": True},
        {"failed": True, "payload": {}, "failure_reason": Reason.INTERNAL_ERROR},
        {"payload": {}, "failure_reason": Reason.INTERNAL_ERROR},
        {"payload": {}, "sent": False},
        {"payload": {}, "failed": "false"},
    ],
)
def test_raw_result_rejects_ambiguous_or_coerced_failure_envelopes(
    values: dict[str, object],
) -> None:
    """I6: 내용이 있는 결과를 무자료 전송 실패로 바꿔 분류를 우회할 수 없다."""
    with pytest.raises(ValidationError):
        RawResult.model_validate(values)


@pytest.mark.parametrize("sent", [True, False])
def test_known_transport_failure_carries_only_safe_metadata(sent: bool) -> None:
    failure = RawResult(failed=True, sent=sent, failure_reason=Reason.INTERNAL_ERROR)
    assert failure.payload is None
    assert failure.failed is True
    assert failure.sent is sent


def test_error_result_with_content_remains_a_received_result() -> None:
    """6.2: 도구의 isError 응답에 섞인 본문도 gate가 분류할 자료로 남는다."""
    result = RawResult(payload={"isError": True, "content": [{"text": "원문 일부"}]})
    assert result.failed is False
    assert result.payload["isError"] is True
    assert result.payload["content"][0]["text"] == "원문 일부"


def test_raw_and_released_payloads_detach_from_callers() -> None:
    """I3/I9: 공유 mutable JSON을 변경해 격리·공개 DTO의 내용을 바꿀 수 없다."""
    original = {"content": [{"text": "원본"}]}
    raw = RawResult(payload=original)
    released = ReleasedResult(structured_content=original)
    original["content"][0]["text"] = "변조"
    assert raw.payload["content"][0]["text"] == "원본"
    assert released.structured_content["content"][0]["text"] == "원본"
    with pytest.raises(TypeError):
        raw.payload["content"][0]["text"] = "변조"


def test_sensitive_contract_fields_are_hidden_in_default_repr() -> None:
    """I9: 기본 표현의 누출만 검사하며 전체 로그 경로의 안전을 주장하지 않는다."""
    marker = "PRIVATE_CONTRACT_MARKER_123"
    call = ProposedCall(
        tool_ref="docs__read_document", arguments={"body": marker}, model_call_id="model-1"
    )
    released = ReleasedResult(text=marker, structured_content={"body": marker})
    models = (
        RawResult(payload={"content": marker}),
        released,
        Released(request_id="request-1", result=released, label=Label.INTERNAL),
        Finding(analyzer="offline-rules", code="F1", severity="LOW", description=marker),
        ToolView(
            tool_ref="docs__read_document", input_schema={"title": marker}, description=marker
        ),
        ConversationMessage(role="assistant", content=marker, calls=(call,)),
        ModelTurn(text=marker, calls=(call,)),
    )
    for model in models:
        assert marker not in repr(model)
        assert marker not in str(model)


@pytest.mark.parametrize(
    "field", ["created_at_utc", "expires_at_utc", "decided_at_utc", "consumed_at_utc"]
)
@pytest.mark.parametrize(
    "invalid_time",
    [CREATED.replace(tzinfo=None), CREATED.astimezone(timezone(timedelta(hours=9)))],
)
def test_approval_requires_explicit_utc_for_every_timestamp(
    field: str, invalid_time: datetime
) -> None:
    """8.4: 날짜의 로컬 시각·naive 시각을 UTC로 추정하지 않는다."""
    with pytest.raises(ValidationError):
        make_approval(**{field: invalid_time})


@pytest.mark.parametrize("expiry", [CREATED, CREATED - timedelta(seconds=1)])
def test_approval_expiry_must_follow_creation(expiry: datetime) -> None:
    with pytest.raises(ValidationError):
        make_approval(expires_at_utc=expiry)


@pytest.mark.parametrize(
    "updates",
    [
        {"decided_at_utc": CREATED},
        {"status": ApprovalStatus.APPROVED},
        {"status": ApprovalStatus.APPROVED, "decided_at_utc": EXPIRES},
        {"status": ApprovalStatus.APPROVED, "decided_at_utc": CREATED - timedelta(seconds=1)},
        {"status": ApprovalStatus.CONSUMED, "decided_at_utc": CREATED},
        {"status": ApprovalStatus.CONSUMED, "consumed_at_utc": CREATED},
        {
            "status": ApprovalStatus.CONSUMED,
            "decided_at_utc": CREATED + timedelta(seconds=2),
            "consumed_at_utc": CREATED + timedelta(seconds=1),
        },
        {
            "status": ApprovalStatus.CONSUMED,
            "decided_at_utc": CREATED,
            "consumed_at_utc": EXPIRES,
        },
        {"status": ApprovalStatus.REJECTED, "consumed_at_utc": CREATED},
    ],
)
def test_approval_rejects_inconsistent_state_and_time_windows(updates: dict[str, object]) -> None:
    """I3/8.2: 이 검사는 레코드 무결성이며 실제 승인 소비 원자성을 증명하지 않는다."""
    with pytest.raises(ValidationError):
        make_approval(**updates)


def test_approval_accepts_valid_consumed_window_and_json_roundtrip() -> None:
    approval = make_approval(
        status=ApprovalStatus.CONSUMED,
        decided_at_utc=CREATED + timedelta(seconds=1),
        consumed_at_utc=CREATED + timedelta(seconds=2),
    )
    assert ApprovalRecord.model_validate_json(approval.model_dump_json()) == approval
    copied = approval.model_copy(update={"row_revision": 1})
    assert copied.created_at_utc == approval.created_at_utc
    assert copied.consumed_at_utc == approval.consumed_at_utc
    assert ApprovalRecord.model_validate_json(copied.model_dump_json()) == copied
    with pytest.raises(ValidationError):
        approval.model_copy(update={"consumed_at_utc": EXPIRES})


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"kind": "denied", "request_id": None, "reasons": ["INTERNAL_ERROR"]}, Denied),
        (
            {
                "kind": "pending",
                "request_id": "r-1",
                "approval_id": "a-1",
                "reasons": ["INTERNAL_TO_EXTERNAL"],
            },
            Pending,
        ),
        (
            {"kind": "released", "request_id": "r-1", "result": {"text": "ok"}, "label": None},
            Released,
        ),
        (
            {"kind": "withheld", "request_id": "r-1", "reasons": ["RESULT_UNCLASSIFIED"]},
            Withheld,
        ),
        (
            {"kind": "outcome_unknown", "request_id": "r-1", "reasons": ["POST_DISPATCH_ERROR"]},
            OutcomeUnknown,
        ),
    ],
)
def test_guard_outcome_discriminator_parses_each_safe_result(
    payload: dict[str, object], expected: type
) -> None:
    outcome = TypeAdapter(Outcome).validate_json(json.dumps(payload))
    assert isinstance(outcome, expected)
    assert outcome.model_dump(mode="json")["kind"] == payload["kind"]
    if isinstance(outcome, Released):
        assert outcome.label is None  # SEND에는 등급이 없을 수 있다(6.4절).


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "allowed", "request_id": "r-1"},
        {"request_id": "r-1", "reasons": ["INTERNAL_ERROR"]},
        {"kind": "denied", "request_id": None, "reasons": ["private exception text"]},
        {"kind": "denied", "request_id": None, "reasons": []},
        {"kind": "denied", "request_id": None, "reasons": ["INTERNAL_ERROR"], "raw": "body"},
        {
            "kind": "released",
            "request_id": "r-1",
            "label": None,
            "result": {"text": "ok", "raw_payload": "body"},
        },
    ],
)
def test_guard_outcome_json_rejects_unknown_kind_reason_or_extra_fields(
    payload: dict[str, object],
) -> None:
    """I6/I7/I9: 미정 결과·원문 reason·미지 필드를 경계에서 거부한다."""
    with pytest.raises(ValidationError):
        TypeAdapter(Outcome).validate_json(json.dumps(payload))


@pytest.mark.parametrize("invalid_label", [True, 1.0, "1"])
def test_released_json_does_not_coerce_noninteger_security_labels(invalid_label: object) -> None:
    """I6/I7: JSON에서도 bool·float·문자열을 신뢰 등급 정수로 변환하지 않는다."""
    with pytest.raises(ValidationError):
        TypeAdapter(Outcome).validate_json(
            json.dumps(
                {
                    "kind": "released",
                    "request_id": "r-1",
                    "result": {"text": "ok"},
                    "label": invalid_label,
                }
            )
        )


def test_registration_rejects_tools_owned_by_another_server() -> None:
    """I7: 조회 레코드가 다른 서버 도구의 소유권을 주장할 수 없다."""
    with pytest.raises(ValidationError):
        make_registration(tools=(make_tool(server_id="remote"),))
    assert make_registration().tools[0].server_id == "docs"


def test_registration_rejects_duplicate_names_but_allows_distinct_tools() -> None:
    first = make_tool()
    with pytest.raises(ValidationError):
        make_registration(tools=(first, first))
    registration = make_registration(tools=(first, make_tool(tool_name="read_summary")))
    assert len(registration.tools) == 2


@pytest.mark.parametrize(
    "field",
    [
        "fingerprint_set",
        "analyzers_required",
        "analyzers_completed",
        "analyzers_skipped",
        "analyzers_failed",
    ],
)
def test_scan_rejects_duplicate_fingerprint_or_analyzer_inventory(field: str) -> None:
    duplicated = ("a" * 64,) * 2 if field == "fingerprint_set" else ("offline-rules",) * 2
    with pytest.raises(ValidationError):
        make_scan(**{field: duplicated})


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("analyzers_completed", "analyzers_skipped"),
        ("analyzers_completed", "analyzers_failed"),
        ("analyzers_skipped", "analyzers_failed"),
    ],
)
def test_scan_rejects_conflicting_outcomes_for_one_analyzer(first: str, second: str) -> None:
    with pytest.raises(ValidationError):
        make_scan(**{first: ("offline-rules",), second: ("offline-rules",)})


def test_empty_findings_do_not_claim_required_analyzer_completion() -> None:
    """10.2: 검사 없음과 검사 완료를 구별한 자료를 Registry에 전달한다."""
    report = make_scan(analyzers_completed=(), analyzers_skipped=("offline-rules",))
    assert report.findings == ()
    assert report.analyzers_required != report.analyzers_completed
    assert report.analyzers_skipped == ("offline-rules",)
    assert ScanReport.model_validate_json(report.model_dump_json()) == report


def test_scan_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValidationError):
        make_scan(scanned_at_utc=CREATED.replace(tzinfo=None))
