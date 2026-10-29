"""C2.9 Semgrep 예외 위치 검사(`tools/ci/check_semgrep_exceptions.py`). B77-N1 후속."""

import importlib.util
from collections import Counter
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_semgrep_exceptions",
    Path(__file__).resolve().parents[2] / "tools" / "ci" / "check_semgrep_exceptions.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
checker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(checker)

SOURCE = """\
def guarded():
    try:
        work()
    except OSError:
        pass


class Box:
    def inner(self):
        try:
            work()
        except OSError:
            pass
"""
RULE = "tools.ci.no-swallow"


def _report(*hits):
    return {
        "errors": [],
        "results": [
            {"check_id": RULE, "path": path, "start": {"line": line}} for path, line in hits
        ],
    }


def _check(monkeypatch, report):
    monkeypatch.setattr(checker, "EXPECTED", Counter({("no-swallow", "src/a.py", "guarded"): 1}))
    return checker.check(report, {"src/a.py": SOURCE}.__getitem__)


def test_enclosing_names_the_innermost_function_or_module():
    assert checker.enclosing(SOURCE, 4) == "guarded"
    assert checker.enclosing(SOURCE, 12) == "Box.inner"
    assert checker.enclosing(SOURCE, 7) == "<module>"
    assert checker.enclosing(SOURCE, 9000) == "<module>"


def test_exception_in_the_approved_function_passes_with_either_separator(monkeypatch):
    assert _check(monkeypatch, _report(("src/a.py", 4))) == ([], 1)
    assert _check(monkeypatch, _report(("src\\a.py", 4))) == ([], 1)


def test_exception_moved_to_another_function_or_line_fails(monkeypatch):
    """B77-N1: 예전 검사는 규칙·파일별 개수만 봐서 줄이 9000번대로 바뀌어도 통과했다."""
    moved = _check(monkeypatch, _report(("src/a.py", 12)))
    assert moved == (
        ["UNEXPECTED no-swallow src/a.py Box.inner x1", "MISSING no-swallow src/a.py guarded x1"],
        1,
    )
    shifted = _check(monkeypatch, _report(("src/a.py", 9004)))
    assert shifted[0][0] == "UNEXPECTED no-swallow src/a.py <module> x1"


def test_extra_missing_and_errored_scans_fail(monkeypatch):
    assert _check(monkeypatch, _report(("src/a.py", 4), ("src/a.py", 5)))[0] == [
        "UNEXPECTED no-swallow src/a.py guarded x1"
    ]
    assert _check(monkeypatch, _report())[0] == ["MISSING no-swallow src/a.py guarded x1"]
    assert _check(monkeypatch, {"errors": [{}], "results": []}) == (["SEMGREP_ERRORS 1"], 0)


def test_expected_locations_match_the_register_and_design_boundaries():
    assert sum(checker.EXPECTED.values()) == 10
    assert {owner for _, _, owner in checker.EXPECTED} == {
        "SQLiteWriter._execute",  # SG-01
        "SQLiteStore._transaction",  # SG-02
        "defang_scanner_text",  # SG-03
        "BackupTarget.discard",  # SG-04
        "fail_closed",
        "after_dispatch",
        "ServerWorker._execute",
    }
