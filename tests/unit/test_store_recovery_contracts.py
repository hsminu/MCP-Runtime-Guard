"""REC-02·REC-04 복구 결과와 수명 포트 계약."""

import inspect
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from guard_app.domain.enums import RecoveryCategory, RequestStatus, ToolKind
from guard_app.domain.models import RecoveryItem, RecoveryItemPage, RecoveryReport
from guard_app.ports import StoreLifecycle


def _report() -> RecoveryReport:
    return RecoveryReport(
        schema_version=3,
        recovery_version=1,
        run_id="private-run",
        committed_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        changed_task_count=1,
        changed_request_count=2,
        changed_approval_count=0,
        unknown_outcome_count=1,
        quarantined_count=1,
        unresolved_count=0,
    )


def _item() -> RecoveryItem:
    return RecoveryItem(
        run_id="private-run",
        task_id="private-task",
        request_id="private-request",
        original_status=RequestStatus.DISPATCHING,
        category=RecoveryCategory.LEGACY_METADATA_MISSING,
        at_utc=datetime(2026, 1, 1, tzinfo=UTC),
        server_id="private-server",
        tool_name="private-tool",
        destination_id="private-destination",
        kind=None,
        epoch=None,
    )


def test_recovery_report_is_strict_frozen_and_accepts_only_success():
    report = _report()
    assert len(report.model_dump()) == 10
    assert RecoveryReport.model_validate_json(report.model_dump_json()) == report
    assert "private-run" not in repr(report)
    for update in (
        {"schema_version": 2},
        {"recovery_version": 2},
        {"unresolved_count": 1},
        {"changed_request_count": -1},
        {"changed_task_count": True},
        {"committed_at_utc": datetime(2026, 1, 1)},
        {"raw": "private-raw"},
    ):
        with pytest.raises(ValidationError):
            report.model_copy(update=update)
    with pytest.raises(ValidationError):
        report.run_id = "other"


def test_recovery_item_and_page_hide_identifiers_and_preserve_legacy_metadata():
    item = _item()
    page = RecoveryItemPage(items=(item,), next_after="private-cursor")
    assert item.kind is None
    assert item.epoch is None
    assert RecoveryItemPage.model_validate_json(page.model_dump_json()) == page
    assert "private" not in repr(item)
    assert "private" not in repr(page)
    with pytest.raises(ValidationError):
        item.model_copy(update={"kind": "send"})
    with pytest.raises(ValidationError):
        item.model_copy(update={"epoch": -1})
    with pytest.raises(ValidationError):
        item.model_copy(update={"at_utc": datetime(2026, 1, 1)})
    assert item.model_copy(update={"kind": ToolKind.SEND, "epoch": 1}).kind is ToolKind.SEND


def test_lifecycle_recovery_methods_have_expected_signatures():
    expected = {
        "recover": ("self", "deadline", "run_id", "on_step"),
        "activate_run": ("self",),
        "load_recovery_report": ("self", "run_id"),
        "list_recovery_items": ("self", "run_id", "after", "limit"),
    }
    for name, names in expected.items():
        method = getattr(StoreLifecycle, name)
        assert inspect.iscoroutinefunction(method)
        assert tuple(inspect.signature(method).parameters) == names
    for name in ("recover", "list_recovery_items"):
        parameters = inspect.signature(getattr(StoreLifecycle, name)).parameters
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in list(parameters.values())[1:])
    parameters = inspect.signature(StoreLifecycle.list_recovery_items).parameters
    assert parameters["run_id"].default is None
    assert parameters["after"].default is None
    assert parameters["limit"].default == 100
