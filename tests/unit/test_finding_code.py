"""SCAN-01: finding codes have the bounded, non-executable display shape."""

from itertools import count

import pytest
from pydantic import ValidationError

from guard_app.domain.contracts import Finding
from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason
from guard_app.storage import SQLiteStore
from tests.unit.test_registry_store import NOW, add, report, run


@pytest.mark.parametrize("code", ["A", "A" * 64, "lower UPPER_0123", " ", "_"])
def test_finding_code_accepts_bounded_ascii_code(code):
    assert Finding(analyzer="LLM", code=code, severity="HIGH").code == code


@pytest.mark.parametrize(
    "code",
    ["", "A" * 65, "bad-code", "bad.code", "한글", "bad\ncode", "bad\x00code", "bad\u200bcode"],
)
def test_finding_code_rejects_invalid_length_and_characters(code):
    with pytest.raises(ValidationError):
        Finding(analyzer="LLM", code=code, severity="HIGH")


def test_invalid_finding_code_is_not_echoed_in_error_text():
    marker = "PRIVATE_FINDING_CODE-not-allowed"
    with pytest.raises(ValidationError) as error:
        Finding(analyzer="LLM", code=marker, severity="HIGH")
    assert marker not in str(error.value)
    assert marker not in repr(error.value)


def test_preexisting_scan_row_with_old_code_fails_closed_on_read(tmp_path):
    """Old persisted codes allowed by Identifier are no longer deserializable."""
    marker = "PRIVATE-CODE"
    audit_ids = count()
    with SQLiteStore(
        tmp_path / "legacy-scan.db", audit_id_factory=lambda: f"audit-{next(audit_ids)}"
    ) as store:
        add(store)
        run(store.record_scan_report("scan-1", report(), at_utc=NOW))

        def inject_old_code(connection):
            connection.execute(
                "UPDATE scan_reports SET report_json=replace(report_json, ?, ?) WHERE scan_id=?",
                ('"code":"warning"', f'"code":"{marker}"', "scan-1"),
            )
            return connection.execute(
                "SELECT report_json FROM scan_reports WHERE scan_id=?", ("scan-1",)
            ).fetchone()[0]

        original_row = store._run_on_writer(inject_old_code)
        assert marker in original_row
        with pytest.raises(StoreError) as error:
            run(store.load_registration("server-1"))
        assert error.value.reason is Reason.INTERNAL_ERROR
        assert marker not in str(error.value)
        assert marker not in repr(error.value)
        assert (
            store._run_on_writer(
                lambda connection: connection.execute(
                    "SELECT report_json FROM scan_reports WHERE scan_id=?", ("scan-1",)
                ).fetchone()[0]
            )
            == original_row
        )
