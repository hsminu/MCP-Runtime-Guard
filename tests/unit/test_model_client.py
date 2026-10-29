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


def test_invalid_or_unadvertised_output_is_rejected(subtests):

    for response in [
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
    ]:
        with (
            subtests.test(response=response),
            pytest.raises(ModelFormatError, match="모델 응답 형식"),
        ):
            parse_model_turn(response, (_view("alpha"),))


@pytest.mark.asyncio
async def test_provider_boundary_does_not_execute_tools() -> None:
    async with asyncio.timeout(30):
        observed = []

        async def complete(conversation, tools):
            observed.append((conversation, tools))
            return {"text": "ok", "calls": [_call(arguments={"path": "item"})]}

        client = ProviderNeutralModelClient(complete)

        turn = await client.step(
            (ConversationMessage(role="user", content="read item"),), (_view("alpha"),)
        )
        assert turn.text == "ok"
        assert len(observed) == 1
        assert observed[0][1][0].tool_ref == "alpha__read"


def test_completion_deadline_must_be_finite_and_positive(subtests):
    def check(timeout_s):
        async def complete(conversation, tools):
            return {"text": "ok", "calls": []}

        with pytest.raises(ValueError, match="유효한 모델 응답 제한 시간"):
            ProviderNeutralModelClient(complete, timeout_s=timeout_s)

    for timeout_s in [0, -1, 301, 10**1000, float("nan"), float("inf"), True, "1"]:
        with subtests.test(timeout_s=timeout_s):
            check(timeout_s)


@pytest.mark.asyncio
async def test_provider_failures_and_timeout_have_fixed_content_free_errors(subtests):
    async with asyncio.timeout(30):

        async def check(failure, expected, message):
            async def complete(conversation, tools):
                raise failure

            client = ProviderNeutralModelClient(complete)
            with pytest.raises(expected) as captured:
                await client.step((ConversationMessage(role="user", content="read"),), ())
            assert str(captured.value) == message
            assert "synthetic-private-marker" not in str(captured.value)

        # R07: plain provider errors and provider cancellation retain fixed text.
        for failure in (
            OSError,
            asyncio.CancelledError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            OverflowError,
            TimeoutError,
        ):
            with subtests.test(failure=failure.__name__):
                timeout = failure is TimeoutError
                await check(
                    failure("synthetic-private-marker"),
                    ModelDeadlineError if timeout else ModelCompletionError,
                    "모델 응답 대기 시간이 초과됐다" if timeout else "모델 요청을 완료할 수 없다",
                )


def test_regex_format_failure_is_classified_without_content(subtests):

    for pattern in ["a{4294967296}", "(" * 600 + ")" * 600, "[z-a]"]:
        with subtests.test(pattern=pattern):
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


@pytest.mark.asyncio
async def test_completion_deadline_discards_late_result_after_ignored_cancellation() -> None:
    async with asyncio.timeout(30):
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

        attempt = asyncio.create_task(
            client.step((ConversationMessage(role="user", content="read"),), (_view("alpha"),))
        )
        done, _ = await asyncio.wait({attempt}, timeout=1)
        try:
            assert done == {attempt}
            with pytest.raises(ModelDeadlineError, match="모델 응답 대기 시간이 초과됐다"):
                await attempt
            await asyncio.wait_for(cancelled.wait(), timeout=1)
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_discards_late_completion() -> None:
    async with asyncio.timeout(30):
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

        attempt = asyncio.create_task(
            client.step((ConversationMessage(role="user", content="read"),), (_view("alpha"),))
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


@pytest.mark.asyncio
async def test_conversation_utf8_boundary_is_checked_before_provider_egress() -> None:

    async with asyncio.timeout(30):
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
        assert (await client.step((accepted,), ())).text == "ok"
        with pytest.raises(ModelFormatError, match="모델 응답 형식"):
            await client.step((ConversationMessage(role="user", content=content + "x"),), ())
        assert len(observed) == 1
        assert observed[0][0].content == content


@pytest.mark.asyncio
async def test_conversation_message_count_is_checked_before_provider_egress() -> None:

    async with asyncio.timeout(30):
        observed = []

        async def complete(conversation, tools):
            observed.append(len(conversation))
            return {"text": "ok", "calls": []}

        client = ProviderNeutralModelClient(complete)
        message = ConversationMessage(role="user", content="x")
        assert (await client.step((message,) * MAX_MODEL_CONVERSATION_MESSAGES, ())).text == "ok"
        with pytest.raises(ModelFormatError):
            await client.step((message,) * (MAX_MODEL_CONVERSATION_MESSAGES + 1), ())
        assert observed == [MAX_MODEL_CONVERSATION_MESSAGES]


@pytest.mark.asyncio
async def test_conversation_budget_includes_prior_call_arguments() -> None:

    async with asyncio.timeout(30):
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
            await client.step((message,), (_view("alpha"),))
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


def test_text_call_argument_and_node_boundaries(subtests):
    with subtests.test(boundary="test_model_text_limit_preserves_boundary_and_rejects_oversize"):
        assert len(
            parse_model_turn({"text": "x" * MAX_MODEL_TEXT_CHARS, "calls": []}, ()).text
        ) == (MAX_MODEL_TEXT_CHARS)
        with pytest.raises(ModelFormatError, match="모델 응답 형식"):
            parse_model_turn({"text": "x" * (MAX_MODEL_TEXT_CHARS + 1), "calls": []}, ())
    with subtests.test(boundary="test_maximum_number_of_valid_calls_remains_accepted"):
        calls = [{**_call(), "model_call_id": f"call-{index}"} for index in range(MAX_MODEL_CALLS)]
        turn = parse_model_turn({"text": None, "calls": calls}, (_view("alpha"),))
        assert len(turn.calls) == MAX_MODEL_CALLS
    with subtests.test(boundary="test_encoded_argument_at_length_boundary_still_works"):
        prefix = '{"path":"'
        suffix = '"}'
        encoded = prefix + "x" * (MAX_MODEL_ARGUMENT_CHARS - len(prefix) - len(suffix)) + suffix
        assert len(encoded) == MAX_MODEL_ARGUMENT_CHARS
        turn = parse_model_turn(
            {"text": None, "calls": [_call(arguments=encoded)]}, (_view("alpha"),)
        )
        assert len(turn.calls[0].arguments["path"]) == MAX_MODEL_ARGUMENT_CHARS - 11
    with subtests.test(boundary="test_raw_argument_node_boundary_remains_accepted"):
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


def test_argument_and_completion_limits_precede_expensive_processing(monkeypatch, subtests):
    def forbidden(*args, **kwargs):
        raise AssertionError("budget rejection must precede parsing/freezing/schema validation")

    structure = '{"path":[' + ",".join("0" for _ in range(2 * MAX_MODEL_ARGUMENT_NODES + 1)) + "]}"
    assert len(structure) < MAX_MODEL_ARGUMENT_CHARS
    shared = []
    amplified = [None] * (MAX_MODEL_ARGUMENT_NODES - 1)
    argument = '{"path":"' + "x" * 60_000 + '"}'
    assert len(argument) < MAX_MODEL_ARGUMENT_CHARS
    aggregate = [{**_call(arguments=argument), "model_call_id": f"call-{i}"} for i in range(5)]
    assert sum(len(call["arguments"]) for call in aggregate) > MAX_MODEL_COMPLETION_CHARS
    cases = [
        (
            "call count",
            "Draft202012Validator.check_schema",
            [{**_call(), "model_call_id": f"call-{i}"} for i in range(MAX_MODEL_CALLS + 1)],
        ),
        ("encoded length", "json.loads", [_call(arguments=" " * (MAX_MODEL_ARGUMENT_CHARS + 1))]),
        ("raw size", "Arguments", [_call(arguments={"path": "x" * MAX_MODEL_ARGUMENT_CHARS})]),
        ("encoded nodes", "json.loads", [_call(arguments=structure)]),
        (
            "decoded nodes",
            "Arguments",
            [
                _call(
                    arguments=json.dumps(
                        {"path": [0] * (MAX_MODEL_ARGUMENT_NODES - 1)}, separators=(",", ":")
                    )
                )
            ],
        ),
        ("shared container", "Arguments", [_call(arguments={"path": [shared, shared]})]),
        (
            "alias amplification",
            "Arguments",
            [_call(arguments={"path": [amplified] * (MAX_MODEL_ARGUMENT_NODES - 2)})],
        ),
        ("depth", "json.loads", [_call(arguments='{"path":' + "[" * 65 + "0" + "]" * 65 + "}")]),
        ("integer", "Arguments", [_call(arguments={"path": 1 << MAX_MODEL_ARGUMENT_INTEGER_BITS})]),
        ("aggregate", "Draft202012Validator.check_schema", aggregate),
    ]
    for name, target, calls in cases:
        with subtests.test(case=name), monkeypatch.context() as patch:
            patch.setattr(f"examples.legacy_model_client.{target}", forbidden)
            with pytest.raises(ModelFormatError):
                parse_model_turn({"text": None, "calls": calls}, (_view("alpha"),))


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
            {**_call(), "model_call_id": f"late-{index}"} for index in range(MAX_MODEL_CALLS + 1)
        )
        return prepared

    monkeypatch.setattr(model_client_module, "_check_completion_budget", mutate_after_check)
    turn = parse_model_turn({"text": None, "calls": calls}, (view,))
    assert len(turn.calls) == 1
    assert turn.calls[0].arguments["path"] == (1,)


def test_catalog_status_controls_model_tool_surface(subtests):
    def check(status):
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
                parse_model_turn({"text": None, "calls": [_call()]}, views).calls[0].tool_ref
                == "alpha__read"
            )
            return
        assert views == ()
        with pytest.raises(ModelFormatError):
            parse_model_turn({"text": None, "calls": [_call()]}, views)

    for status in list(RegStatus):
        with subtests.test(status=status):
            check(status)
