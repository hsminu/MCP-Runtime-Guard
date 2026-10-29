"""A54: local syntax hint delegates to B; no Registry authorization (I7, T32 support)."""

from __future__ import annotations

from guard_app.services import catalog_sources
from guard_app.services.bridge import valid_server_id


def test_bridge_preserves_b_server_id_result_without_normalization():
    values = (
        "docs",
        "local_docs",
        "_docs",
        "서버 이름",
        " docs_ ",
        "docs__read",
        "docs_",
        "_",
        "__",
        "",
        "do\x00cs",
        "docs\u202e",
        None,
        1,
        ["docs"],
    )
    assert [
        value
        for value in values
        if valid_server_id(value) is not catalog_sources.valid_server_id(value)
    ] == []


def test_bridge_uses_b_checker_instead_of_a_second_policy(monkeypatch):
    calls, results = [], iter((False, True))
    value = object()

    def check(server_id):
        calls.append(server_id)
        return next(results)

    monkeypatch.setattr(catalog_sources, "valid_server_id", check)
    observed = [(valid_server_id(value), calls[:]) for _ in range(2)]
    assert observed == [(False, [value]), (True, [value, value])]
    assert observed[0][0] is False
    assert observed[1][0] is True
