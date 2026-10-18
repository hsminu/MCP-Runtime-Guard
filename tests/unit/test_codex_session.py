"""A88: Codex app-server session manager against a scripted fake (10.4).

I1 (Guard-only execution), I7 (trusted Task binding), I8 (no resend), I9 (fixed
texts), I11 (Guard tools only), T21 (per-Task session), T22 (L3 part).
Counts come from the fake's recorded messages and a counting fake Guard; the
real codex.exe, C launcher and L2 are not exercised (C2.12, C6.14).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from guard_app.adapters.codex_protocol import build_dynamic_tools
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    Released,
    ReleasedResult,
    ToolView,
    Withheld,
)
from guard_app.domain.enums import Label
from guard_app.domain.reasons import Reason
from guard_app.services.codex_prompt import BASE_INSTRUCTIONS
from guard_app.services.codex_session import CodexAppServerSession, SessionLimits
from guard_app.services.guard_relay import ApprovalOutcomes, GuardToolRelay, TrustedTask
from guard_app.services.runner import CodexAuthenticationError, CodexSessionError

from .fake_codex_app_server import THREAD_ID, FakeAppServer

SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}}}
TASK = TrustedTask("task-trusted", 4)


class FakeGuard:
    def __init__(self, *outcomes: object, block: asyncio.Event | None = None) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, object, int | None]] = []
        self.block = block
        self.active = 0
        self.max_active = 0

    async def submit(self, task_id: str, call: object, *, expected_epoch: int | None = None):
        self.calls.append((task_id, call, expected_epoch))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.block is not None:
                await self.block.wait()
            await asyncio.sleep(0)
            return self.outcomes.pop(0)
        finally:
            self.active -= 1


def released(text: str = "doc text", structured: dict[str, object] | None = None) -> Released:
    return Released(
        request_id="request-1",
        result=ReleasedResult(text=text, structured_content=structured),
        label=Label.PUBLIC,
    )


def views(*refs: str) -> tuple[ToolView, ...]:
    return tuple(ToolView(tool_ref=ref, input_schema=SCHEMA, description="") for ref in refs)


async def started(
    fake: FakeAppServer,
    guard: FakeGuard,
    *,
    tools: tuple[ToolView, ...] = views("docs__read"),
    approvals: ApprovalOutcomes | None = None,
    limits: SessionLimits | None = None,
    partial: list[str] | None = None,
) -> CodexAppServerSession:
    relay = GuardToolRelay(TASK, guard, approvals or ApprovalOutcomes())
    session = CodexAppServerSession(
        fake,
        task=TASK,
        plan=build_dynamic_tools(tools),
        relay=relay.relay,
        model="gpt-6.1-sol",
        base_instructions=BASE_INSTRUCTIONS,
        version="0.1.0",
        limits=limits,
        on_partial=None if partial is None else partial.append,
    )
    await session.start()
    return session


def test_start_registers_only_guard_tools_in_an_isolated_ephemeral_thread() -> None:
    async def run() -> None:
        fake = FakeAppServer()
        session = await started(
            fake,
            FakeGuard(),
            tools=views("docs__read", "docs__bad.name", "functions__x", "mcp__x", "docs__mcp"),
        )
        try:
            assert fake.initialize_params is not None
            assert fake.initialize_params["capabilities"] == {"experimentalApi": True}
            assert fake.methods()[:3] == ["initialize", "initialized", "thread/start"]
            params = fake.thread_params
            assert params is not None
            assert params["environments"] == []
            assert params["ephemeral"] is True
            assert params["approvalsReviewer"] == "user"
            assert params["model"] == "gpt-6.1-sol"
            assert params["baseInstructions"] == BASE_INSTRUCTIONS
            [namespace] = params["dynamicTools"]
            assert namespace["type"] == "namespace"
            assert namespace["name"] == "docs"
            assert [tool["name"] for tool in namespace["tools"]] == ["read"]
            assert set(session.observations.excluded_tools) == {
                "docs__bad.name",
                "functions__x",
                "mcp__x",
                "docs__mcp",
            }
            assert "jsonrpc" not in json.dumps(fake.received)
        finally:
            await session.aclose()

    asyncio.run(run())


def test_turn_answer_and_streamed_deltas() -> None:
    async def run() -> None:
        fake, partial = FakeAppServer(), []
        session = await started(fake, FakeGuard(), partial=partial)
        try:
            assert await session.send("hello") == "echo:hello"
            assert partial == ["echo:hello"]
            assert fake.methods().count("turn/start") == 1
        finally:
            await session.aclose()

    asyncio.run(run())


def _tool_script(*calls: dict[str, object], final: str = "done"):
    async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
        for call in calls:
            server.last_reply = await server.tool_call(turn_id, **call)  # type: ignore[attr-defined]
        await server.agent(turn_id, final)
        server.complete(turn_id)

    return script


def reply_result(fake: FakeAppServer) -> dict[str, object]:
    reply = fake.last_reply  # type: ignore[attr-defined]
    assert "result" in reply
    return reply["result"]


def test_released_call_uses_trusted_task_and_returns_text_and_structured_json() -> None:
    async def run() -> None:
        guard = FakeGuard(released("doc text", {"resource_id": "public/a"}))
        fake = FakeAppServer(
            _tool_script({"call_id": "c1", "tool": "read", "arguments": {"path": "a"}})
        )
        session = await started(fake, guard)
        try:
            assert await session.send("read it") == "done"
            [(task_id, call, epoch)] = guard.calls
            assert (task_id, epoch) == ("task-trusted", 4)
            assert call.tool_ref == "docs__read"
            assert call.model_call_id == "c1"
            result = reply_result(fake)
            assert result["success"] is True
            assert [item["text"] for item in result["contentItems"]] == [
                "doc text",
                '{"resource_id": "public/a"}',
            ]
            assert session.observations.responses_by_call == {"c1": 1}
        finally:
            await session.aclose()

    asyncio.run(run())


def test_missing_namespace_is_denied_without_guard() -> None:
    async def run() -> None:
        guard = FakeGuard()
        fake = FakeAppServer(
            _tool_script({"call_id": "c1", "tool": "read", "arguments": {}, "namespace": None})
        )
        session = await started(fake, guard)
        try:
            await session.send("go")
            assert guard.calls == []
            result = reply_result(fake)
            assert result["success"] is False
            assert result["contentItems"][0]["text"] == "이 요청은 정책에 의해 차단되었습니다"
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("reasons", "text"),
    [
        (
            (Reason.CONFIDENTIAL_TO_EXTERNAL,),
            "이 요청은 정책에 의해 차단되었습니다: CONFIDENTIAL_TO_EXTERNAL",
        ),
        ((Reason.UNKNOWN_SERVER,), "이 요청은 정책에 의해 차단되었습니다"),
        (
            (Reason.CONFIDENTIAL_TO_EXTERNAL, Reason.UNKNOWN_SERVER),
            "이 요청은 정책에 의해 차단되었습니다",
        ),
    ],
)
def test_denied_answer_shows_only_public_reason_codes(reasons, text) -> None:
    async def run() -> None:
        guard = FakeGuard(Denied(request_id=None, reasons=reasons))
        fake = FakeAppServer(_tool_script({"call_id": "c1", "tool": "read", "arguments": {}}))
        session = await started(fake, guard)
        try:
            await session.send("go")
            result = reply_result(fake)
            assert result["success"] is False
            assert [item["text"] for item in result["contentItems"]] == [text]
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("kind", [Withheld, OutcomeUnknown])
def test_withheld_or_unknown_answers_stopped_then_stops_the_task(kind) -> None:
    async def run() -> None:
        outcome = kind(request_id="request-1", reasons=(Reason.RESULT_UNCLASSIFIED,))
        guard = FakeGuard(outcome)

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.tool_call(turn_id, "c1", "read", {})  # type: ignore[attr-defined]
            await asyncio.Event().wait()  # the model would continue; we must stop it

        fake = FakeAppServer(script)
        session = await started(fake, guard)
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_TOOL_STOPPED"
            result = reply_result(fake)
            assert result["success"] is False
            assert result["contentItems"][0]["text"] == "작업이 정지되었습니다"
            await session.interrupt()
            assert fake.interrupts == 1
            assert len(guard.calls) == 1
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("action", "final", "success", "text"),
    [
        ("approve", released("approved text"), True, "approved text"),
        ("reject", None, False, "승인 요청이 거절되었습니다"),
    ],
)
def test_pending_waits_for_the_human_result_without_guessing(action, final, success, text) -> None:
    async def run() -> None:
        pending = Pending(
            request_id="r1", approval_id="ap-1", reasons=(Reason.INTERNAL_TO_EXTERNAL,)
        )
        guard, approvals = FakeGuard(pending), ApprovalOutcomes()
        fake = FakeAppServer(_tool_script({"call_id": "c1", "tool": "read", "arguments": {}}))
        session = await started(fake, guard, approvals=approvals)
        try:
            sending = asyncio.ensure_future(session.send("go"))
            for _ in range(200):
                if approvals.waiting("ap-1"):
                    break
                await asyncio.sleep(0.005)
            assert approvals.waiting("ap-1")
            assert not sending.done()
            approvals.resolve("ap-1", action, final)
            assert await asyncio.wait_for(sending, 2) == "done"
            result = reply_result(fake)
            assert result["success"] is success
            assert result["contentItems"][0]["text"] == text
            assert len(guard.calls) == 1
        finally:
            await session.aclose()

    asyncio.run(run())


def test_two_calls_in_one_turn_reach_guard_one_at_a_time_in_order() -> None:
    async def run() -> None:
        guard = FakeGuard(released("one"), released("two"))
        replies: list[dict[str, object]] = []

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            first = asyncio.ensure_future(server.tool_call(turn_id, "c1", "read", {"path": "1"}))
            second = asyncio.ensure_future(server.tool_call(turn_id, "c2", "read", {"path": "2"}))
            replies.extend(await asyncio.gather(first, second))
            await server.agent(turn_id, "both")
            server.complete(turn_id)

        fake = FakeAppServer(script)
        session = await started(fake, guard)
        try:
            assert await session.send("go") == "both"
            assert [call.model_call_id for _t, call, _e in guard.calls] == ["c1", "c2"]
            assert guard.max_active == 1
            assert [r["result"]["contentItems"][0]["text"] for r in replies] == ["one", "two"]
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["commandExecution", "fileChange", "webSearch", "mcpToolCall"])
def test_l3_item_outside_the_allow_list_stops_the_task(kind) -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.item(turn_id, kind)
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        session = await started(fake, FakeGuard())
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_L3_VIOLATION"
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "method", ["item/commandExecution/requestApproval", "item/fileChange/requestApproval"]
)
def test_codex_approval_request_is_declined_then_the_task_stops(method) -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.request(  # type: ignore[attr-defined]
                method, {"threadId": THREAD_ID, "turnId": turn_id, "itemId": "i1"}
            )
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        session = await started(fake, FakeGuard())
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_L3_VIOLATION"
            assert reply_result(fake) == {"decision": "decline"}
        finally:
            await session.aclose()

    asyncio.run(run())


def test_unknown_server_request_stops_the_task() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            await server.request("item/permissions/requestApproval", {"turnId": turn_id})

        fake = FakeAppServer(script)
        session = await started(fake, FakeGuard())
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_L3_VIOLATION"
        finally:
            await session.aclose()

    asyncio.run(run())


def test_request_user_input_gets_an_empty_answer_and_is_only_counted() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.request(  # type: ignore[attr-defined]
                "item/tool/requestUserInput",
                {"threadId": THREAD_ID, "turnId": turn_id, "itemId": "i1", "questions": []},
            )
            await server.agent(turn_id, "continued")
            server.complete(turn_id)

        fake = FakeAppServer(script)
        session = await started(fake, FakeGuard())
        try:
            assert await session.send("go") == "continued"
            assert reply_result(fake) == {"answers": {}}
            assert session.observations.user_input_requests == 1
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("info", "error"),
    [
        ("unauthorized", CodexAuthenticationError),
        ("usageLimitExceeded", CodexSessionError),
        ("rateLimitExceeded", CodexSessionError),
        ("serverOverloaded", CodexSessionError),
    ],
)
def test_failed_turn_maps_auth_and_usage_limits_to_fixed_codes(info, error) -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.complete(turn_id, "failed", {"message": "PRIVATE", "codexErrorInfo": info})

        session = await started(FakeAppServer(script), FakeGuard())
        try:
            with pytest.raises(error) as failed:
                await asyncio.wait_for(session.send("go"), 2)
            expected = {
                "unauthorized": "CODEX_AUTH_FAILED",
                "usageLimitExceeded": "CODEX_USAGE_LIMIT",
                "rateLimitExceeded": "CODEX_USAGE_LIMIT",
            }.get(info, "CODEX_TURN_FAILED")
            assert failed.value.code == expected
            assert "PRIVATE" not in str(failed.value)
        finally:
            await session.aclose()

    asyncio.run(run())


def test_reached_rate_limit_notification_stops_the_task() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.notify(
                "account/rateLimits/updated",
                {"rateLimits": {"rateLimitReachedType": "rate_limit_reached"}},
            )
            await asyncio.Event().wait()

        session = await started(FakeAppServer(script), FakeGuard())
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_USAGE_LIMIT"
        finally:
            await session.aclose()

    asyncio.run(run())


def test_watchdog_answers_stopped_and_stops_when_guard_never_returns() -> None:
    async def run() -> None:
        guard = FakeGuard(released(), block=asyncio.Event())

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.tool_call(turn_id, "c1", "read", {})  # type: ignore[attr-defined]
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        session = await started(fake, guard, limits=SessionLimits(call_watchdog_s=0.05))
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_WATCHDOG_TIMEOUT"
            assert reply_result(fake)["contentItems"][0]["text"] == "작업이 정지되었습니다"
            assert len(guard.calls) == 1  # no resend after the timeout (I8)
        finally:
            await session.aclose()

    asyncio.run(run())


def test_tool_call_with_model_supplied_extra_fields_never_reaches_guard() -> None:
    async def run() -> None:
        guard = FakeGuard()

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.tool_call(  # type: ignore[attr-defined]
                turn_id, "c1", "read", {}, extra={"task_id": "other-task", "label": "PUBLIC"}
            )
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        session = await started(fake, guard)
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_TOOL_CALL_INVALID"
            assert guard.calls == []
        finally:
            await session.aclose()

    asyncio.run(run())


def test_closed_stdout_while_waiting_fails_instead_of_hanging() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, _turn_id: str, _text: str) -> None:
            server.close_stdout()

        session = await started(FakeAppServer(script), FakeGuard())
        try:
            with pytest.raises(CodexSessionError):
                await asyncio.wait_for(session.send("go"), 2)
        finally:
            await session.aclose()

    asyncio.run(run())
