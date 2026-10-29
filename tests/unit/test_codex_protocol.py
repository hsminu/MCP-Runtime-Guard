"""Pure Codex 0.162.1 builders/parsers (A87): names, limits and strict calls."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from guard_app.adapters.codex_protocol import (
    MAX_TURN_TEXT_CHARS,
    REASONING_EFFORTS,
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


def test_dynamic_tool_names_follow_codex_rules() -> None:
    failures = []
    for ref, kept in [
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
    ]:
        with check_case(failures, ((ref, kept))):
            plan = build_dynamic_tools((view(ref),))
            assert (ref in plan.tool_refs) is kept
            assert (ref in plan.excluded) is (not kept)

    for ref, reason in [
        ("docs__read.v2", "TOOL_NAME"),
        ("docs__" + "t" * 129, "TOOL_NAME"),
        ("docs__mcp", "TOOL_NAME"),
        ("s" * 65 + "__read", "NAMESPACE_NAME"),
        ("mcp__read", "NAMESPACE_NAME"),
        ("docsread", "NAMESPACE_NAME"),
        ("web__search", "NAMESPACE_RESERVED"),
    ]:
        with check_case(failures, ((ref, reason))):
            plan = build_dynamic_tools((view(ref),))
            assert [(item.tool_ref, item.reason) for item in plan.exclusions] == [(ref, reason)]
            assert plan.excluded == (ref,)
    assert not failures, failures


def test_tools_are_grouped_by_server_namespace_once() -> None:
    plan = build_dynamic_tools(
        (view("docs__read"), view("docs__list"), view("mail__send"), view("docs__read"))
    )
    assert [(ns["name"], [t["name"] for t in ns["tools"]]) for ns in plan.tools] == [
        ("docs", ["read", "list"]),
        ("mail", ["send"]),
    ]
    assert plan.excluded == ("docs__read",)  # duplicate ref is not registered twice

    plan = build_dynamic_tools((view("docs__read"), view("docs__read")))
    assert [(item.tool_ref, item.reason) for item in plan.exclusions] == [
        ("docs__read", "DUPLICATE")
    ]


def test_tool_call_parse_keeps_only_codex_fields() -> None:
    failures = []
    call = parse_dynamic_tool_call(
        {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x", "arguments": {"a": 1}}
    )
    assert (call.namespace, call.tool, call.arguments) == (None, "x", {"a": 1})

    for arguments in [[], [1], "x", 1, None, True]:
        # A88 QA: Codex may send any JSON; the relay answers INVALID_ARGUMENTS (I7).
        with check_case(failures, (arguments)):
            call = parse_dynamic_tool_call(
                {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x", "arguments": arguments}
            )
            assert call.arguments is None

    for params in [
        None,
        [],
        {"threadId": "t", "turnId": "u", "callId": "c", "tool": "x"},
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
    ]:
        with check_case(failures, (params)), pytest.raises(CodexProtocolError):
            parse_dynamic_tool_call(params)
    assert not failures, failures


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


def test_turn_effort_is_sent_only_when_picked_and_only_from_the_offered_levels() -> None:
    # turn/start can also override the model, sandbox and approval policy for
    # the turn; any such key would get around L1, so the whole key set is pinned.
    failures = []
    assert turn_start_params("thread", "hi") == {
        "threadId": "thread",
        "input": [{"type": "text", "text": "hi"}],
    }
    picked = turn_start_params("thread", "hi", "high")
    assert set(picked) == {"threadId", "input", "effort"}
    assert picked["effort"] == "high"
    for effort in ("ultra", "minimal", "", "HIGH"):
        with check_case(failures, (effort)), pytest.raises(CodexProtocolError):
            turn_start_params("thread", "hi", effort)

    assert turn_start_params("thread", "x" * MAX_TURN_TEXT_CHARS)["threadId"] == "thread"
    for text in ("", "x" * (MAX_TURN_TEXT_CHARS + 1)):
        with check_case(failures, (text)), pytest.raises(CodexProtocolError):
            turn_start_params("thread", text)
    assert not failures, failures


def test_offered_levels_come_from_the_pinned_model_catalog_without_delegation() -> None:
    catalog = Path(__file__).resolve().parents[2] / "config" / "codex" / "model_catalog.json"
    (model,) = json.loads(catalog.read_text(encoding="utf-8"))["models"]
    advertised = [level["effort"] for level in model["supported_reasoning_levels"]]
    assert set(REASONING_EFFORTS) <= set(advertised)
    assert "ultra" not in REASONING_EFFORTS  # delegates to sub-agents, off in L1
    assert model["default_reasoning_level"] in REASONING_EFFORTS


@contextmanager
def check_case(failures, case):
    try:
        yield
    except (AssertionError, pytest.fail.Exception) as error:
        failures.append((case, str(error)))
