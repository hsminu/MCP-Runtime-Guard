"""A19: model output remains an untrusted proposal (I1/I7/I10)."""

from __future__ import annotations

import asyncio
import json

import pytest
from examples import legacy_model_client as model_client_module
from examples.legacy_model_client import (
    MAX_MODEL_ARGUMENT_CHARS,
    MAX_MODEL_ARGUMENT_INTEGER_BITS,
    MAX_MODEL_ARGUMENT_NODES,
    MAX_MODEL_CALLS,
    MAX_MODEL_COMPLETION_CHARS,
    MAX_MODEL_CONVERSATION_MESSAGES,
    MAX_MODEL_CONVERSATION_UTF8_BYTES,
    MAX_MODEL_TEXT_CHARS,
    ModelCompletionError,
    ModelDeadlineError,
    ModelFormatError,
    ProviderNeutralModelClient,
    parse_model_turn,
)

from guard_app.domain.contracts import ConversationMessage, RegisteredServer, ToolView
from guard_app.domain.enums import Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.json_types import Arguments, FrozenJsonObject
from guard_app.domain.models import Boundary, ProposedCall, ToolSpec
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


@pytest.mark.parametrize(
    "timeout_s", [0, -1, 301, 10**1000, float("nan"), float("inf"), True, "1"]
)
def test_completion_deadline_must_be_finite_and_positive(timeout_s: object) -> None:
    async def complete(conversation, tools):
        return {"text": "ok", "calls": []}

    with pytest.raises(ValueError, match="유효한 모델 응답 제한 시간"):
        ProviderNeutralModelClient(complete, timeout_s=timeout_s)


@pytest.mark.parametrize(
    "failure",
    [
        OSError("synthetic-private-marker"),
        asyncio.CancelledError(),
        # R07: provider SDKs also raise plain ValueError/TypeError/etc.
        ValueError("synthetic-private-marker"),
        TypeError("synthetic-private-marker"),
        KeyError("synthetic-private-marker"),
        RuntimeError("synthetic-private-marker"),
        OverflowError("synthetic-private-marker"),
    ],
)
def test_provider_failure_has_fixed_content_free_error(failure: BaseException) -> None:
    async def complete(conversation, tools):
        raise failure

    client = ProviderNeutralModelClient(complete)
    with pytest.raises(ModelCompletionError) as captured:
        asyncio.run(
            client.step((ConversationMessage(role="user", content="read"),), ())
        )
    assert str(captured.value) == "모델 요청을 완료할 수 없다"
    assert "synthetic-private-marker" not in str(captured.value)


@pytest.mark.parametrize(
    "pattern",
    ["a{4294967296}", "(" * 600 + ")" * 600, "[z-a]"],
    ids=["overflow", "recursion", "re-error"],
)
def test_regex_format_failure_is_classified_without_content(pattern: str) -> None:
    """R07/I7: a model-chosen regex cannot escape as an unclassified error."""
    view = ToolView(
        tool_ref="alpha__search",
        input_schema=FrozenJsonObject(
            {
                "type": "object",
                "properties": {"pattern": {"type": "string", "format": "regex"}},
                "required": ["pattern"],
                "additionalProperties": False,
            }
        ),
        description="",
    )
    response = {
        "text": None,
        "calls": [_call("alpha__search", json.dumps({"pattern": pattern}))],
    }
    with pytest.raises(ModelFormatError) as captured:
        parse_model_turn(response, (view,))
    assert str(captured.value) == "모델 응답 형식이 유효하지 않다"


def test_provider_timeout_has_fixed_content_free_error() -> None:
    async def complete(conversation, tools):
        raise TimeoutError("synthetic-private-marker")

    client = ProviderNeutralModelClient(complete)
    with pytest.raises(ModelDeadlineError) as captured:
        asyncio.run(
            client.step((ConversationMessage(role="user", content="read"),), ())
        )
    assert str(captured.value) == "모델 응답 대기 시간이 초과됐다"
    assert "synthetic-private-marker" not in str(captured.value)


def test_completion_deadline_discards_late_result_after_ignored_cancellation() -> None:
    release = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()

    async def complete(conversation, tools):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            try:
                await release.wait()
                return {"text": "late", "calls": [_call()]}
            finally:
                finished.set()

    client = ProviderNeutralModelClient(complete, timeout_s=0.01)

    async def run():
        attempt = asyncio.create_task(
            client.step(
                (ConversationMessage(role="user", content="read"),), (_view("alpha"),)
            )
        )
        done, _ = await asyncio.wait({attempt}, timeout=1)
        try:
            assert done == {attempt}
            with pytest.raises(
                ModelDeadlineError, match="모델 응답 대기 시간이 초과됐다"
            ):
                await attempt
            await asyncio.wait_for(cancelled.wait(), timeout=1)
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)

    asyncio.run(run())


def test_caller_cancellation_propagates_and_discards_late_completion() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def complete(conversation, tools):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await release.wait()
                return {"text": "late", "calls": [_call()]}
            finally:
                finished.set()

    client = ProviderNeutralModelClient(complete, timeout_s=1)

    async def run():
        attempt = asyncio.create_task(
            client.step(
                (ConversationMessage(role="user", content="read"),), (_view("alpha"),)
            )
        )
        await entered.wait()
        attempt.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await attempt
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)

    asyncio.run(run())


def test_conversation_utf8_boundary_is_checked_before_provider_egress() -> None:
    import asyncio

    observed = []

    async def complete(conversation, tools):
        observed.append(conversation)
        return {"text": "ok", "calls": []}

    client = ProviderNeutralModelClient(complete)
    empty_message = ConversationMessage(role="user", content="")
    fixed_size = len(
        json.dumps(
            [empty_message.model_dump(mode="json")],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    padding = MAX_MODEL_CONVERSATION_UTF8_BYTES - fixed_size
    content = "한" * (padding // 3) + "x" * (padding % 3)
    accepted = ConversationMessage(role="user", content=content)

    async def run():
        assert (await client.step((accepted,), ())).text == "ok"
        with pytest.raises(ModelFormatError, match="모델 응답 형식"):
            await client.step(
                (ConversationMessage(role="user", content=content + "x"),), ()
            )

    asyncio.run(run())
    assert len(observed) == 1
    assert observed[0][0].content == content


def test_conversation_message_count_is_checked_before_provider_egress() -> None:
    import asyncio

    observed = []

    async def complete(conversation, tools):
        observed.append(len(conversation))
        return {"text": "ok", "calls": []}

    client = ProviderNeutralModelClient(complete)
    message = ConversationMessage(role="user", content="x")

    async def run():
        assert (
            await client.step((message,) * MAX_MODEL_CONVERSATION_MESSAGES, ())
        ).text == "ok"
        with pytest.raises(ModelFormatError):
            await client.step((message,) * (MAX_MODEL_CONVERSATION_MESSAGES + 1), ())

    asyncio.run(run())
    assert observed == [MAX_MODEL_CONVERSATION_MESSAGES]


def test_conversation_budget_includes_prior_call_arguments() -> None:
    import asyncio

    observed = []

    async def complete(conversation, tools):
        observed.append(conversation)
        return {"text": "ok", "calls": []}

    client = ProviderNeutralModelClient(complete)
    call = ProposedCall(
        tool_ref="alpha__read",
        arguments=Arguments({"path": "한" * (MAX_MODEL_CONVERSATION_UTF8_BYTES // 3)}),
        model_call_id="call-1",
    )
    message = ConversationMessage(role="assistant", calls=(call,))
    with pytest.raises(ModelFormatError):
        asyncio.run(client.step((message,), (_view("alpha"),)))
    assert observed == []


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


def test_model_text_limit_preserves_boundary_and_rejects_oversize() -> None:
    assert len(
        parse_model_turn({"text": "x" * MAX_MODEL_TEXT_CHARS, "calls": []}, ()).text
    ) == (MAX_MODEL_TEXT_CHARS)
    with pytest.raises(ModelFormatError, match="모델 응답 형식"):
        parse_model_turn({"text": "x" * (MAX_MODEL_TEXT_CHARS + 1), "calls": []}, ())


def test_oversized_call_list_fails_before_schema_validation(monkeypatch) -> None:
    def unexpected_validation(*args, **kwargs):
        raise AssertionError("schema validation ran before the call count limit")

    monkeypatch.setattr(
        "examples.legacy_model_client.Draft202012Validator.check_schema",
        unexpected_validation,
    )
    calls = [
        {**_call(), "model_call_id": f"call-{index}"}
        for index in range(MAX_MODEL_CALLS + 1)
    ]
    with pytest.raises(ModelFormatError):
        parse_model_turn({"text": None, "calls": calls}, (_view("alpha"),))


def test_maximum_number_of_valid_calls_remains_accepted() -> None:
    calls = [
        {**_call(), "model_call_id": f"call-{index}"}
        for index in range(MAX_MODEL_CALLS)
    ]
    turn = parse_model_turn({"text": None, "calls": calls}, (_view("alpha"),))
    assert len(turn.calls) == MAX_MODEL_CALLS


def test_encoded_argument_length_is_checked_before_json_parse(monkeypatch) -> None:
    def unexpected_parse(*args, **kwargs):
        raise AssertionError("JSON parsing ran before the argument length limit")

    monkeypatch.setattr("examples.legacy_model_client.json.loads", unexpected_parse)
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {
                "text": None,
                "calls": [_call(arguments=" " * (MAX_MODEL_ARGUMENT_CHARS + 1))],
            },
            (_view("alpha"),),
        )


def test_encoded_argument_at_length_boundary_still_works() -> None:
    prefix = '{"path":"'
    suffix = '"}'
    encoded = (
        prefix + "x" * (MAX_MODEL_ARGUMENT_CHARS - len(prefix) - len(suffix)) + suffix
    )
    assert len(encoded) == MAX_MODEL_ARGUMENT_CHARS
    turn = parse_model_turn(
        {"text": None, "calls": [_call(arguments=encoded)]}, (_view("alpha"),)
    )
    assert len(turn.calls[0].arguments["path"]) == MAX_MODEL_ARGUMENT_CHARS - 11


def test_raw_argument_size_is_checked_before_freezing(monkeypatch) -> None:
    def unexpected_freeze(*args, **kwargs):
        raise AssertionError("argument freezing ran before the raw object size limit")

    monkeypatch.setattr("examples.legacy_model_client.Arguments", unexpected_freeze)
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {
                "text": None,
                "calls": [_call(arguments={"path": "x" * MAX_MODEL_ARGUMENT_CHARS})],
            },
            (_view("alpha"),),
        )


def test_encoded_argument_structure_is_bounded_before_json_parse(monkeypatch) -> None:
    def unexpected_parse(*args, **kwargs):
        raise AssertionError("JSON parsing ran before the structure limit")

    monkeypatch.setattr("examples.legacy_model_client.json.loads", unexpected_parse)
    encoded = (
        '{"path":['
        + ",".join("0" for _ in range(2 * MAX_MODEL_ARGUMENT_NODES + 1))
        + "]}"
    )
    assert len(encoded) < MAX_MODEL_ARGUMENT_CHARS
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {"text": None, "calls": [_call(arguments=encoded)]}, (_view("alpha"),)
        )


def test_decoded_argument_nodes_are_bounded_before_freezing(monkeypatch) -> None:
    def unexpected_freeze(*args, **kwargs):
        raise AssertionError("argument freezing ran before the node limit")

    monkeypatch.setattr("examples.legacy_model_client.Arguments", unexpected_freeze)
    encoded = json.dumps(
        {"path": [0] * (MAX_MODEL_ARGUMENT_NODES - 1)}, separators=(",", ":")
    )
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {"text": None, "calls": [_call(arguments=encoded)]}, (_view("alpha"),)
        )


def test_raw_argument_node_boundary_remains_accepted() -> None:
    view = ToolView(
        tool_ref="alpha__read",
        input_schema=FrozenJsonObject(
            {
                "type": "object",
                "properties": {"path": {"type": "array", "items": {"type": "integer"}}},
                "required": ["path"],
                "additionalProperties": False,
            }
        ),
        description="",
    )
    values = [0] * (MAX_MODEL_ARGUMENT_NODES - 2)
    turn = parse_model_turn(
        {"text": None, "calls": [_call(arguments={"path": values})]}, (view,)
    )
    assert len(turn.calls[0].arguments["path"]) == len(values)


def test_shared_raw_container_is_rejected_before_freezing(monkeypatch) -> None:
    def unexpected_freeze(*args, **kwargs):
        raise AssertionError("shared argument graph reached freezing")

    monkeypatch.setattr("examples.legacy_model_client.Arguments", unexpected_freeze)
    shared: list[object] = []
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {"text": None, "calls": [_call(arguments={"path": [shared, shared]})]},
            (_view("alpha"),),
        )


def test_pending_node_budget_rejects_alias_amplification_before_freezing(
    monkeypatch,
) -> None:
    def unexpected_freeze(*args, **kwargs):
        raise AssertionError("amplified argument graph reached freezing")

    monkeypatch.setattr("examples.legacy_model_client.Arguments", unexpected_freeze)
    shared = [None] * (MAX_MODEL_ARGUMENT_NODES - 1)
    amplified = {"path": [shared] * (MAX_MODEL_ARGUMENT_NODES - 2)}
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {"text": None, "calls": [_call(arguments=amplified)]},
            (_view("alpha"),),
        )


def test_mutating_provider_response_after_budget_check_cannot_change_validated_turn(
    monkeypatch,
) -> None:
    view = ToolView(
        tool_ref="alpha__read",
        input_schema=FrozenJsonObject(
            {
                "type": "object",
                "properties": {"path": {"type": "array", "items": {"type": "integer"}}},
                "required": ["path"],
                "additionalProperties": False,
            }
        ),
        description="",
    )
    values = [1]
    calls = [_call(arguments={"path": values})]
    original_check = model_client_module._check_completion_budget

    def mutate_after_check(text, provider_calls):
        prepared = original_check(text, provider_calls)
        values.extend([0] * (MAX_MODEL_ARGUMENT_NODES + 1))
        calls.extend(
            {**_call(), "model_call_id": f"late-{index}"}
            for index in range(MAX_MODEL_CALLS + 1)
        )
        return prepared

    monkeypatch.setattr(
        model_client_module, "_check_completion_budget", mutate_after_check
    )
    turn = parse_model_turn({"text": None, "calls": calls}, (view,))
    assert len(turn.calls) == 1
    assert turn.calls[0].arguments["path"] == (1,)


def test_deep_encoded_arguments_are_rejected_before_json_parse(monkeypatch) -> None:
    def unexpected_parse(*args, **kwargs):
        raise AssertionError("JSON parsing ran before the depth limit")

    monkeypatch.setattr("examples.legacy_model_client.json.loads", unexpected_parse)
    encoded = '{"path":' + "[" * 65 + "0" + "]" * 65 + "}"
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {"text": None, "calls": [_call(arguments=encoded)]}, (_view("alpha"),)
        )


def test_large_raw_integer_is_rejected_before_freezing(monkeypatch) -> None:
    def unexpected_freeze(*args, **kwargs):
        raise AssertionError("argument freezing ran before the integer size limit")

    monkeypatch.setattr("examples.legacy_model_client.Arguments", unexpected_freeze)
    with pytest.raises(ModelFormatError):
        parse_model_turn(
            {
                "text": None,
                "calls": [
                    _call(arguments={"path": 1 << MAX_MODEL_ARGUMENT_INTEGER_BITS})
                ],
            },
            (_view("alpha"),),
        )


def test_total_completion_budget_rejects_multiple_individually_bounded_calls(
    monkeypatch,
) -> None:
    def unexpected_validation(*args, **kwargs):
        raise AssertionError("schema validation ran before the aggregate limit")

    monkeypatch.setattr(
        "examples.legacy_model_client.Draft202012Validator.check_schema",
        unexpected_validation,
    )
    argument = '{"path":"' + "x" * 60_000 + '"}'
    assert len(argument) < MAX_MODEL_ARGUMENT_CHARS
    calls = [
        {**_call(arguments=argument), "model_call_id": f"call-{index}"}
        for index in range(5)
    ]
    assert sum(len(call["arguments"]) for call in calls) > MAX_MODEL_COMPLETION_CHARS
    with pytest.raises(ModelFormatError):
        parse_model_turn({"text": None, "calls": calls}, (_view("alpha"),))


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
