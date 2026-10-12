"""A19: model output remains an untrusted proposal (I1/I7/I10)."""

from __future__ import annotations

import pytest

from guard_app.adapters.model_client import (
    ModelFormatError,
    ProviderNeutralModelClient,
    parse_model_turn,
)
from guard_app.domain.contracts import ConversationMessage, RegisteredServer, ToolView
from guard_app.domain.enums import Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import Boundary, ToolSpec
from guard_app.services.catalog import Catalog


def _view(server: str) -> ToolView:
    return ToolView(
        tool_ref=f"{server}__read",
        input_schema=FrozenJsonObject(
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            }
        ),
        description="",
    )


def _call(name: str = "alpha__read", arguments: object = '{"path":"item"}') -> dict:
    return {"tool_ref": name, "arguments": arguments, "model_call_id": "call-1"}


def test_namespaced_calls_and_distinct_servers() -> None:
    response = {
        "text": None,
        "calls": [
            _call("alpha__read"),
            {**_call("beta__read"), "model_call_id": "call-2"},
        ],
    }
    turn = parse_model_turn(response, (_view("alpha"), _view("beta")))
    assert tuple(call.tool_ref for call in turn.calls) == ("alpha__read", "beta__read")
    assert turn.calls[0].arguments.to_dict() == {"path": "item"}


@pytest.mark.parametrize(
    "response",
    [
        {"text": None, "calls": [_call("read")]},
        {"text": None, "calls": [_call("approval__approve")]},
        {"text": None, "calls": [_call(arguments='{"path":')]},
        {"text": None, "calls": [_call(arguments='{"path":"a","path":"b"}')]},
        {"text": None, "calls": [_call(arguments={"path": "a", "label": 0})]},
        {"text": None, "calls": [{**_call(), "task_id": "forged"}]},
        {"text": None, "calls": [{**_call(), "tier": "internal"}]},
        {"text": None, "calls": [{**_call(), "approver_id": "operator"}]},
        {"text": None, "calls": [_call(), _call()]},
        {"text": None, "calls": "not-a-list"},
        {"text": None, "calls": [], "security_version": 0},
    ],
)
def test_invalid_or_unadvertised_output_is_rejected(response: object) -> None:
    with pytest.raises(ModelFormatError, match="모델 응답 형식"):
        parse_model_turn(response, (_view("alpha"),))


def test_provider_boundary_does_not_execute_tools() -> None:
    observed = []

    async def complete(conversation, tools):
        observed.append((conversation, tools))
        return {"text": "ok", "calls": [_call(arguments={"path": "item"})]}

    client = ProviderNeutralModelClient(complete)

    async def run():
        return await client.step(
            (ConversationMessage(role="user", content="read item"),), (_view("alpha"),)
        )

    import asyncio

    turn = asyncio.run(run())
    assert turn.text == "ok"
    assert len(observed) == 1
    assert observed[0][1][0].tool_ref == "alpha__read"


def test_raw_content_is_not_in_format_error() -> None:
    secret = "synthetic-private-marker"
    with pytest.raises(ModelFormatError) as captured:
        parse_model_turn(
            {
                "text": None,
                "calls": [_call(arguments=f'{{"path":"{secret}","path":"x"}}')],
            },
            (_view("alpha"),),
        )
    assert secret not in str(captured.value)


@pytest.mark.parametrize("status", list(RegStatus))
def test_catalog_status_controls_model_tool_surface(status: RegStatus) -> None:
    tool = ToolSpec(
        server_id="alpha",
        tool_name="read",
        kind=ToolKind.READ,
        input_schema=_view("alpha").input_schema,
        destination_arg=None,
        resource_arg=None,
        fingerprint="a" * 64,
    )
    record = RegisteredServer(
        server_id="alpha",
        status=status,
        boundary=Boundary(
            boundary_id="alpha", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
        ),
        tools=(tool,),
    )

    class Registry:
        def registration(self, server_id):
            return record

        def status(self, server_id):
            return status

        def fingerprint(self, tool_spec):
            return tool_spec.fingerprint

    views = Catalog(Registry(), profile_id="profile-1").tool_views(("alpha",))
    if status is RegStatus.ACTIVE:
        assert tuple(view.tool_ref for view in views) == ("alpha__read",)
        assert (
            parse_model_turn({"text": None, "calls": [_call()]}, views)
            .calls[0]
            .tool_ref
            == "alpha__read"
        )
        return
    assert views == ()
    with pytest.raises(ModelFormatError):
        parse_model_turn({"text": None, "calls": [_call()]}, views)
