"""8장·I3/I7/I9/T09–T12/T37: 승인 맥락·시간·불변성의 순수 단위 검증.

저장소 원자 소비와 wire/수신 계수는 이 메모리 전용 테스트의 대상이 아니다.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from guard_app.core.snapshot import ApprovalSnapshot, SnapshotError, make_snapshot
from guard_app.domain.enums import Decision, Label, TaskStatus, ToolKind, TrustTier
from guard_app.domain.models import PolicyDecision, ResolvedRequest, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.digest import canonical_json, json_digest

_EXPIRY = datetime(2026, 10, 11, 2, 3, 4, 567890, tzinfo=UTC)


def _request() -> ResolvedRequest:
    return ResolvedRequest.model_validate(
        {
            "request_id": "request-1",
            "task_id": "task-1",
            "epoch": 3,
            "call_seq": 4,
            "tool": {
                "server_id": "server-1",
                "tool_name": "send",
                "kind": ToolKind.SEND,
                "input_schema": {"type": "object"},
                "destination_arg": "destination_id",
                "resource_arg": None,
                "fingerprint": "a" * 64,
            },
            "arguments": {"destination_id": "external", "body": "private-marker-é"},
            "resource_id": None,
            "server_boundary": {
                "boundary_id": "server",
                "tier": TrustTier.APPROVED_EXTERNAL,
                "max_label": Label.INTERNAL,
            },
            "destination": {
                "boundary_id": "external",
                "tier": TrustTier.GENERAL_EXTERNAL,
                "max_label": Label.INTERNAL,
            },
        }
    )


def _state() -> TaskState:
    return TaskState(
        task_id="task-1",
        epoch=3,
        profile_id="profile-1",
        policy_version="policy-1",
        high_watermark=Label.INTERNAL,
        status=TaskStatus.RUNNING,
        security_version=7,
    )


def _decision(state: TaskState) -> PolicyDecision:
    return PolicyDecision(
        decision=Decision.ASK,
        reasons=(Reason.INTERNAL_TO_EXTERNAL,),
        verdicts=(),
        policy_version=state.policy_version,
        security_version=state.security_version,
    )


def _snapshot(
    req: ResolvedRequest | None = None, state: TaskState | None = None, **kwargs: Any
) -> ApprovalSnapshot:
    task = state or _state()
    options: dict[str, Any] = {
        "effective_label": Label.INTERNAL,
        "approver_id": "registered-operator",
        "expires_at_utc": _EXPIRY,
        "monotonic_deadline": 100.0,
        "hash_json": json_digest,
        "canonicalize": canonical_json,
    }
    options.update(kwargs)
    decision = options.pop("decision", _decision(task))
    return make_snapshot(req or _request(), task, decision, **options)


def test_all_design_snapshot_fields_and_argument_digest_are_bound() -> None:
    snap = _snapshot()
    assert set(snap.payload) >= {
        "task_id",
        "epoch",
        "call_seq",
        "request_id",
        "server_id",
        "tool_name",
        "tool_fingerprint",
        "server_boundary_id",
        "destination_id",
        "arguments_digest",
        "effective_label",
        "security_version",
        "policy_version",
        "approver_id",
        "expires_at_utc",
    }
    assert snap.payload["arguments_digest"] == json_digest(snap.request.arguments)
    assert snap.hash == json_digest(snap.payload)
    assert snap.arguments_bytes == canonical_json(snap.request.arguments)
    assert snap.task_id == "task-1"
    assert snap.payload["expires_at_utc"] == "2026-10-11T02:03:04.567890+00:00"


@pytest.mark.parametrize(
    ("section", "field", "changed"),
    [
        (None, "request_id", "request-2"),
        (None, "call_seq", 5),
        (None, "arguments", {"body": "modified"}),
        (None, "resource_id", "registered/resource"),
        ("tool", "server_id", "server-2"),
        ("tool", "tool_name", "send-2"),
        ("tool", "fingerprint", "b" * 64),
        ("tool", "kind", ToolKind.READ),
        ("tool", "destination_arg", "recipient"),
        ("tool", "resource_arg", "path"),
        ("server_boundary", "boundary_id", "server-2"),
        ("server_boundary", "tier", TrustTier.GENERAL_EXTERNAL),
        ("server_boundary", "max_label", Label.PUBLIC),
        ("destination", "boundary_id", "external-2"),
        ("destination", "tier", TrustTier.APPROVED_EXTERNAL),
        ("destination", "max_label", Label.PUBLIC),
    ],
)
def test_request_security_changes_invalidate_hash(
    section: str | None, field: str, changed: object
) -> None:
    values = _request().model_dump()
    if section is None:
        values[field] = changed
    else:
        values[section][field] = changed
    updated = ResolvedRequest.model_validate(values)
    assert _snapshot(updated).hash != _snapshot().hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_id", "profile-2"),
        ("policy_version", "policy-2"),
        ("security_version", 8),
        ("high_watermark", Label.CONFIDENTIAL),
    ],
)
def test_task_security_changes_invalidate_hash(field: str, value: object) -> None:
    state = _state().model_copy(update={field: value})
    assert _snapshot(state=state).hash != _snapshot().hash


@pytest.mark.parametrize(("field", "value"), [("task_id", "task-2"), ("epoch", 4)])
def test_task_identity_changes_are_bound(field: str, value: object) -> None:
    state = _state().model_copy(update={field: value})
    req = _request().model_copy(update={field: value})
    assert _snapshot(req, state).hash != _snapshot().hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effective_label", Label.PUBLIC),
        ("approver_id", "operator-2"),
        ("expires_at_utc", _EXPIRY + timedelta(microseconds=1)),
    ],
)
def test_approval_specific_changes_invalidate_hash(field: str, value: object) -> None:
    assert _snapshot(**{field: value}).hash != _snapshot().hash


def test_status_row_revision_and_comparison_label_do_not_change_hash() -> None:
    waiting = _state().model_copy(
        update={
            "status": TaskStatus.WAITING_APPROVAL,
            "row_revision": 100,
            "last_read_label": Label.CONFIDENTIAL,
        }
    )
    assert _snapshot(state=waiting).hash == _snapshot().hash


def test_monotonic_deadline_is_memory_only_and_not_hashed() -> None:
    snap = _snapshot(monotonic_deadline=200.0)
    assert snap.hash == _snapshot().hash
    assert "monotonic_deadline" not in snap.payload
    assert snap.monotonic_deadline == 200.0


def test_read_without_destination_is_valid_snapshot() -> None:
    req = _request()
    req = req.model_copy(
        update={"tool": req.tool.model_copy(update={"kind": ToolKind.READ}), "destination": None}
    )
    snap = _snapshot(req)
    assert snap.payload["destination_id"] is None
    assert snap.payload["destination"] is None


def test_argument_bytes_and_full_dtos_are_memory_only_and_redacted() -> None:
    snap = _snapshot()
    assert b"private-marker" in snap.arguments_bytes
    assert "private-marker" not in canonical_json(snap.payload).decode()
    assert "input_schema" not in snap.payload
    assert "private-marker" not in repr(snap)
    assert "request-1" not in repr(snap)
    assert snap.request is not _request()
    assert snap.state.high_watermark is Label.INTERNAL


def test_snapshot_is_deeply_immutable() -> None:
    snap = _snapshot()
    with pytest.raises(FrozenInstanceError):
        snap.approver_id = "different"
    with pytest.raises(TypeError):
        snap.payload["approver_id"] = "different"  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.request.arguments["body"] = "different"  # type: ignore[index]
    detached = snap.request.arguments.to_dict()
    detached["body"] = "modified"
    assert "private-marker" in str(snap.request.arguments["body"])


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
def test_non_ask_decision_cannot_create_approval(decision: Decision) -> None:
    with pytest.raises(SnapshotError, match="ASK"):
        _snapshot(decision=_decision(_state()).model_copy(update={"decision": decision}))


@pytest.mark.parametrize(
    "status", list(set(TaskStatus) - {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL})
)
def test_non_approvable_state_is_rejected(status: TaskStatus) -> None:
    with pytest.raises(SnapshotError, match="상태"):
        _snapshot(state=_state().model_copy(update={"status": status}))


def test_unclassified_state_is_rejected() -> None:
    with pytest.raises(SnapshotError, match="상태"):
        _snapshot(state=_state().model_copy(update={"unclassified": True}))


@pytest.mark.parametrize(
    ("field", "value"), [("policy_version", "different"), ("security_version", 8)]
)
def test_mismatched_decision_version_is_rejected(field: str, value: object) -> None:
    with pytest.raises(SnapshotError, match="버전"):
        _snapshot(decision=_decision(_state()).model_copy(update={field: value}))


@pytest.mark.parametrize(("field", "value"), [("task_id", "different"), ("epoch", 4)])
def test_mismatched_task_identity_is_rejected(field: str, value: object) -> None:
    with pytest.raises(SnapshotError, match="맥락"):
        _snapshot(state=_state().model_copy(update={field: value}))


@pytest.mark.parametrize("value", [0, True, "internal", None])
def test_effective_label_is_exact_enum(value: object) -> None:
    with pytest.raises(SnapshotError, match="등급"):
        _snapshot(effective_label=value)


@pytest.mark.parametrize("value", ["", 4, None, "secret\ud800"])
def test_invalid_approver_is_rejected_without_raw(value: object) -> None:
    with pytest.raises(SnapshotError) as caught:
        _snapshot(approver_id=value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "expiry", [datetime(2026, 10, 11), datetime(2026, 10, 11, tzinfo=timezone(timedelta(hours=9)))]
)
def test_expiry_requires_utc_aware(expiry: datetime) -> None:
    with pytest.raises(SnapshotError, match="UTC"):
        _snapshot(expires_at_utc=expiry)


@pytest.mark.parametrize(
    "deadline", [-1.0, float("nan"), float("inf"), True, "100", None, 10**1000]
)
def test_monotonic_deadline_requires_finite_nonnegative_number(deadline: object) -> None:
    with pytest.raises(SnapshotError, match="monotonic"):
        _snapshot(monotonic_deadline=deadline)


@pytest.mark.parametrize(
    ("utc", "monotonic", "expired"),
    [
        (_EXPIRY - timedelta(seconds=1), 99.0, False),
        (_EXPIRY, 99.0, True),
        (_EXPIRY + timedelta(seconds=1), 99.0, True),
        (_EXPIRY - timedelta(seconds=1), 100.0, True),
        (_EXPIRY - timedelta(days=365), 101.0, True),
    ],
)
def test_both_clocks_and_exact_expiry_boundary(
    utc: datetime, monotonic: float, expired: bool
) -> None:
    assert _snapshot().expired(now_utc=utc, now_monotonic=monotonic) is expired


def test_bad_hash_dependency_is_rejected() -> None:
    with pytest.raises(SnapshotError, match="SHA-256"):
        _snapshot(hash_json=lambda _: "not-a-hash")


def test_bad_serializer_dependency_is_rejected() -> None:
    with pytest.raises(SnapshotError, match="bytes"):
        _snapshot(canonicalize=lambda _: "raw-secret")
