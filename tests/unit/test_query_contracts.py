"""B6-05 query contracts, privacy and input boundaries."""

import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guard_app.domain.enums import ApprovalStatus, Decision, Label, RequestStatus, ToolKind
from guard_app.domain.queries import (
    ApprovalListLane,
    ApprovalListPage,
    ApprovalListQuery,
    ApprovalRow,
    CallDetail,
    CallListPage,
    CallListQuery,
    CallRow,
    CallStage,
    ListSort,
)
from guard_app.domain.reasons import Reason
from guard_app.ports import StoreQueries

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def _approval_query(**overrides):
    values = {"lane": ApprovalListLane.PENDING, "sort": ListSort.NEWEST, "offset": 0, "limit": 6}
    return ApprovalListQuery(**(values | overrides))


def _call_query(**overrides):
    values = {"sort": ListSort.OLDEST, "offset": 0, "limit": 100}
    return CallListQuery(**(values | overrides))


def _approval_row():
    return ApprovalRow(
        approval_id="SECRET-approval",
        task_id="SECRET-task",
        request_id="SECRET-request",
        server_id="SECRET-server",
        tool_name="SECRET-tool",
        destination_id="SECRET-destination",
        status=ApprovalStatus.PENDING,
        created_at_utc=NOW,
        expires_at_utc=NOW,
        decided_at_utc=None,
        reason_code=None,
        row_revision=0,
    )


def _call_row():
    return CallRow(
        request_id="SECRET-request",
        task_id="SECRET-task",
        epoch=1,
        call_seq=1,
        server_id="SECRET-server",
        tool_name="SECRET-tool",
        kind=ToolKind.READ,
        destination_id=None,
        decision=Decision.ALLOW,
        reason_codes=(Reason.OK,),
        approval_id=None,
        approval_status=None,
        status=RequestStatus.SUCCEEDED,
        decided_at_utc=NOW,
        row_revision=0,
    )


@pytest.mark.parametrize("query", [_approval_query, _call_query])
@pytest.mark.parametrize("search", ["", "a_", "A.0:-z", "x" * 64])
def test_search_accepts_literal_restricted_prefix(query, search):
    assert query(search=search).search == search
    if search:
        assert search not in repr(query(search=search))


@pytest.mark.parametrize("query", [_approval_query, _call_query])
@pytest.mark.parametrize("search", ["x" * 65, "%", "a b", "a/b", "x\n", "한글"])
def test_search_rejects_non_contract_values(query, search):
    with pytest.raises(ValidationError):
        query(search=search)


@pytest.mark.parametrize("query", [_approval_query, _call_query])
@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 10_001},
        {"limit": True},
        {"offset": 1.0},
    ],
)
def test_pagination_bounds_are_strict(query, changes):
    with pytest.raises(ValidationError):
        query(**changes)


def test_query_models_are_strict_frozen_and_hide_identifiers():
    approval = _approval_row()
    call = _call_row()
    row_fields = {
        "approval_id",
        "task_id",
        "request_id",
        "server_id",
        "tool_name",
        "destination_id",
        "status",
        "created_at_utc",
        "expires_at_utc",
        "decided_at_utc",
        "reason_code",
        "row_revision",
    }
    assert set(ApprovalRow.model_fields) == row_fields
    assert set(CallRow.model_fields) == {
        "request_id",
        "task_id",
        "epoch",
        "call_seq",
        "server_id",
        "tool_name",
        "kind",
        "destination_id",
        "decision",
        "reason_codes",
        "approval_id",
        "approval_status",
        "status",
        "decided_at_utc",
        "row_revision",
    }
    for model in (
        approval,
        call,
        ApprovalListPage(rows=(approval,), total_count=1, as_of_utc=NOW),
        CallListPage(rows=(call,), total_count=1, as_of_utc=NOW),
        CallDetail(
            row=call,
            observed_label=Label.PUBLIC,
            stages=(CallStage(stage_code="OBSERVED", at_utc=NOW),),
        ),
    ):
        assert "SECRET" not in repr(model)
        with pytest.raises(ValidationError):
            model.model_copy(update={"unexpected": "SECRET"})
    with pytest.raises(ValidationError):
        approval.row_revision = 9
    with pytest.raises(ValidationError):
        call.model_copy(update={"status": "SUCCEEDED"})


@pytest.mark.parametrize(
    "value", [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9)))]
)
def test_projection_utc_is_required(value):
    with pytest.raises(ValidationError):
        _approval_row().model_copy(update={"created_at_utc": value})
    with pytest.raises(ValidationError):
        CallStage(stage_code="DECIDED", at_utc=value)
    with pytest.raises(ValidationError):
        CallListPage(rows=(), total_count=0, as_of_utc=value)


def test_only_agreed_stages_and_port_methods_are_exposed():
    with pytest.raises(ValidationError):
        CallStage(stage_code="RELEASED", at_utc=NOW)
    assert set(CallStage.model_fields) == {"stage_code", "at_utc"}
    for name in ("list_approvals", "list_calls", "get_call"):
        assert inspect.iscoroutinefunction(getattr(StoreQueries, name))
