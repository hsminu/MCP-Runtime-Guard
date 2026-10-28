"""REC-05의 준비 진행·실패 코드와 스키마 ACK 계약."""

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from guard_app.domain.enums import StorePrepareStep, StoreStartFailure
from guard_app.domain.errors import StoreError, StorePrepareError
from guard_app.domain.models import MigrationReport
from guard_app.ports import BackupDestination, StoreLifecycle


def test_prepare_steps_and_start_failures_are_fixed_strings():
    assert {item.value for item in StorePrepareStep} == {
        "STORE_CHECK",
        "STORE_BACKUP",
        "STORE_MIGRATE",
        "STORE_VERIFY",
        "STORE_RECOVER",
    }
    assert {item.value for item in StoreStartFailure} == {
        "STORE_SCHEMA_UNSUPPORTED",
        "STORE_INTEGRITY_FAILED",
        "STORE_RECOVERY_UNRESOLVED",
        "STORE_BACKUP_FAILED",
        "STORE_MIGRATION_FAILED",
        "STORE_COMMIT_UNKNOWN",
        "STORE_DEADLINE_EXCEEDED",
        "STORE_IO_FAILED",
    }


@pytest.mark.parametrize("code", list(StoreStartFailure))
def test_prepare_error_is_separate_from_store_error_and_contains_only_code(code):
    error = StorePrepareError(code)
    assert not isinstance(error, StoreError)
    assert error.code is code
    assert str(error) == code.value
    assert repr(error) == code.value


def test_prepare_error_rejects_raw_message_and_path():
    with pytest.raises(TypeError) as error:
        StorePrepareError("private path or SQL")
    assert "private path or SQL" not in str(error.value)


def test_migration_report_is_frozen_and_contains_only_preparation_facts():
    report = MigrationReport(from_version=2, to_version=3, backup_published=True, migrated=True)
    assert report.model_dump() == {
        "from_version": 2,
        "to_version": 3,
        "backup_published": True,
        "migrated": True,
    }
    assert "path" not in repr(report)
    with pytest.raises(ValidationError):
        report.migrated = False
    with pytest.raises(ValidationError):
        report.model_copy(update={"to_version": 2})
    with pytest.raises(ValidationError):
        report.model_copy(update={"from_version": -1})
    with pytest.raises(ValidationError):
        report.model_copy(update={"backup_published": 1})
    with pytest.raises(ValidationError):
        MigrationReport(
            from_version=0,
            to_version=3,
            backup_published=False,
            migrated=True,
            path="private-path",
        )


def test_prepare_protocol_has_async_entry_and_keyword_only_parameters():
    assert inspect.iscoroutinefunction(StoreLifecycle.prepare_schema)
    parameters = inspect.signature(StoreLifecycle.prepare_schema).parameters
    assert list(parameters) == ["self", "deadline", "begin_backup", "on_step"]
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("deadline", "begin_backup", "on_step")
    )
    assert parameters["on_step"].default is None
    assert isinstance(BackupDestination.path, property)
    assert not inspect.iscoroutinefunction(BackupDestination.publish)
    assert not inspect.iscoroutinefunction(BackupDestination.discard)
    assert BackupDestination.publish.__annotations__["return"] is Path
