"""A54: bridge forwards B's namespace check unchanged (I7, T32 support).

No backend instance is required: the check is a local syntax hint and cannot
create a Registry record or authorize a tool call.
"""

from __future__ import annotations

import pytest

from guard_app.services import catalog_sources
from guard_app.services.bridge import valid_server_id


@pytest.mark.parametrize(
    "server_id",
    [
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
    ],
)
def test_bridge_preserves_b_server_id_result_without_normalization(server_id: object) -> None:
    assert valid_server_id(server_id) is catalog_sources.valid_server_id(server_id)


@pytest.mark.parametrize("result", [False, True])
def test_bridge_uses_b_checker_instead_of_a_second_policy(
    monkeypatch: pytest.MonkeyPatch, result: bool
) -> None:
    calls: list[object] = []
    value = object()

    def check(server_id: object) -> bool:
        calls.append(server_id)
        return result

    monkeypatch.setattr(catalog_sources, "valid_server_id", check)

    assert valid_server_id(value) is result
    assert calls == [value]
