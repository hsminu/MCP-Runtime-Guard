"""Pure Codex 0.162.1 builders/parsers (A87): names, limits and strict calls."""

from __future__ import annotations

import pytest

from guard_app.adapters.codex_protocol import (
    MAX_TURN_TEXT_CHARS,
    CodexProtocolError,
    build_dynamic_tools,
    parse_dynamic_tool_call,
    released_texts,
    tool_call_response,
    turn_start_params,
)
from guard_app.domain.contracts import ReleasedResult, ToolView

SCHEMA = {"type": "object"}


def view(ref: str) -> ToolView:
    return ToolView(tool_ref=ref, input_schema=SCHEMA, description="d")


@pytest.mark.parametrize(
    ("ref", "kept"),
    [
        ("docs__read", True),
        ("docs__read_v2-final", True),
        ("docs__" + "t" * 128, True),
        ("docs__" + "t" * 129, False),
        ("s" * 64 + "__read", True),
        ("s" * 65 + "__read", False),
        ("docs__read.v2", False),
        ("docs__", False),
        ("docsread", False),
        ("mcp__read", False),
        ("docs__mcp", False),
        ("docs__mcp__inner", False),
        ("web__search", False),
        ("terminal__run", False),
        ("docs__한글", False),
    ],
    ids=lambda value: str(value)[:24],
)
def test_dynamic_tool_names_follow_codex_rules(ref: str, kept: bool) -> None:
    plan = build_dynamic_tools((view(ref),))
    assert (ref in plan.tool_refs) is kept
    assert (ref in plan.excluded) is (not kept)


def test_tools_are_grouped_by_server_namespace_once() -> None:
    plan = build_dynamic_tools(
        (view("docs__read"), view("docs__list"), view("mail__send"), view("docs__read"))
    )
    assert [(ns["name"], [t["name"] for t in ns["tools"]]) for ns in plan.tools] == [
        ("docs", ["read", "list"]),
        ("mail", ["send"]),
    ]
    assert plan.excluded == ("docs__read",)  # duplicate ref is not registered twice


@pytest.mark.parametrize(
    "params",
    [
        None,
        [],
        {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x"},
        {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x", "arguments": []},
        {"threadId": "t", "turnId": "u", "callId": "", "tool": "x", "arguments": {}},
        {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x", "arguments": {}, "label": 0},
        {
            "threadId": "t",
            "turnId": "u",
            "callId": "c",
            "tool": "x",
            "arguments": {},
            "namespace": 1,
        },
    ],
)
def test_tool_call_parse_is_strict(params: object) -> None:
    with pytest.raises(CodexProtocolError):
        parse_dynamic_tool_call(params)


def test_tool_call_parse_keeps_only_codex_fields() -> None:
    call = parse_dynamic_tool_call(
        {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x", "arguments": {"a": 1}}
    )
    assert (call.namespace, call.tool, call.arguments) == (None, "x", {"a": 1})


def test_released_texts_add_structured_json_after_text() -> None:
    assert released_texts(ReleasedResult(text="t")) == ("t",)
    assert released_texts(ReleasedResult(text="", structured_content={"b": 1, "a": "한"})) == (
        '{"a": "한", "b": 1}',
    )
    assert released_texts(ReleasedResult(text="t", structured_content={})) == ("t", "{}")
    assert tool_call_response(True, ()) == {
        "contentItems": [{"type": "inputText", "text": ""}],
        "success": True,
    }


def test_turn_text_limit_matches_codex() -> None:
    assert turn_start_params("thread", "x" * MAX_TURN_TEXT_CHARS)["threadId"] == "thread"
    for text in ("", "x" * (MAX_TURN_TEXT_CHARS + 1)):
        with pytest.raises(CodexProtocolError):
            turn_start_params("thread", text)
