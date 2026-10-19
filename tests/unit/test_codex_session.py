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
from guard_app.core.model_messages import ModelMessageKind, fixed_model_message
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    PendingFinal,
    Released,
    ReleasedResult,
    ToolView,
    Withheld,
)
from guard_app.domain.enums import Label
from guard_app.domain.reasons import Reason
from guard_app.services.codex_prompt import BASE_INSTRUCTIONS
from guard_app.services.codex_session import (
    MAX_ANSWER_CHARS,
    CodexAppServerSession,
    SessionLimits,
)
from guard_app.services.guard_relay import ApprovalOutcomes, GuardToolRelay, TrustedTask
from guard_app.services.runner import CodexAuthenticationError, CodexSessionError

from .fake_codex_app_server import THREAD_ID, FakeAppServer

SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}}}
TASK = TrustedTask("task-trusted", 4)


class FakePendingResult:
    """Like B's handle: a cancelled wait leaves the result in place."""

    def __init__(self) -> None:
        self.result: asyncio.Future[PendingFinal] = asyncio.get_running_loop().create_future()

    async def wait(self) -> PendingFinal:
        return await asyncio.shield(self.result)


class FakeGuard:
    def __init__(
        self,
        *outcomes: object,
        block: asyncio.Event | None = None,
        delays: tuple[float, ...] = (),
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, object, int | None]] = []
        self.block = block
        self.delays = list(delays)
        self.active = 0
        self.max_active = 0
        self.results: dict[str, FakePendingResult] = {}
        self.unknown_requests: set[str] = set()

    def pending_result(self, request_id: str) -> FakePendingResult:
        if request_id in self.unknown_requests:
            raise ValueError("unknown request")
        return self.results.setdefault(request_id, FakePendingResult())

    def finish(self, final: PendingFinal) -> None:
        self.pending_result(final.request_id).result.set_result(final)

    async def submit(self, task_id: str, call: object, *, expected_epoch: int | None = None):
        self.calls.append((task_id, call, expected_epoch))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.block is not None:
                await self.block.wait()
            await asyncio.sleep(self.delays.pop(0) if self.delays else 0)
            return self.outcomes.pop(0)
        finally:
            self.active -= 1


def released(
    text: str = "doc text",
    structured: dict[str, object] | None = None,
    *,
    is_error: bool = False,
) -> Released:
    return Released(
        request_id="request-1",
        result=ReleasedResult(text=text, structured_content=structured, is_error=is_error),
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
    allowed: frozenset[str] | None = None,
) -> CodexAppServerSession:
    relay = GuardToolRelay(TASK, guard, approvals or ApprovalOutcomes(), allowed_tool_refs=allowed)
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


@pytest.mark.parametrize("approved", [False, True], ids=["direct", "after-approval"])
def test_released_tool_error_reaches_codex_as_a_failed_call(approved) -> None:
    """MCP `isError`: the released text still reaches the model, marked as failed."""

    async def run() -> None:
        outcome = released("tool said no", is_error=True)
        approvals = ApprovalOutcomes()
        guard = FakeGuard(PENDING if approved else outcome)
        fake = FakeAppServer(
            _tool_script({"call_id": "c1", "tool": "read", "arguments": {"path": "a"}})
        )
        session = await started(fake, guard, approvals=approvals)
        try:
            sending = asyncio.ensure_future(session.send("read it"))
            if approved:
                await wait_for_approval(approvals)
                guard.finish(pending_final("executed", outcome))
            assert await asyncio.wait_for(sending, 2) == "done"
            result = reply_result(fake)
            assert result["success"] is False
            assert [item["text"] for item in result["contentItems"]] == ["tool said no"]
            assert session.stop_code is None  # a tool error is not a Task stop
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


PENDING = Pending(
    request_id="request-1", approval_id="ap-1", reasons=(Reason.INTERNAL_TO_EXTERNAL,)
)


def pending_final(cause: str, outcome: object = None, **identity: object) -> PendingFinal:
    if outcome is None:
        outcome = Denied(request_id="request-1", reasons=(Reason.APPROVAL_STALE,))
    fields = {"task_id": TASK.task_id, "epoch": TASK.epoch, **identity}
    return PendingFinal(
        request_id="request-1", approval_id="ap-1", cause=cause, outcome=outcome, **fields
    )


async def wait_for_approval(approvals: ApprovalOutcomes) -> None:
    for _ in range(200):
        if approvals.waiting("ap-1"):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("approval wait never started")


@pytest.mark.parametrize(
    ("cause", "outcome", "success", "text"),
    [
        ("executed", released("approved text"), True, "approved text"),
        ("rejected", None, *fixed_model_message(ModelMessageKind.APPROVAL_REJECTED)),
        ("expired", None, *fixed_model_message(ModelMessageKind.APPROVAL_EXPIRED)),
        ("cancelled", None, *fixed_model_message(ModelMessageKind.APPROVAL_CANCELLED)),
        ("invalidated", None, *fixed_model_message(ModelMessageKind.APPROVAL_INVALIDATED)),
    ],
)
def test_pending_answers_only_from_guards_final_result(cause, outcome, success, text) -> None:
    """NOTIFY-01: the model answer is Guard's PendingFinal, not a human return value."""

    async def run() -> None:
        released_outcome = (
            outcome.model_copy(update={"request_id": "request-1"}) if outcome is not None else None
        )
        guard, approvals = FakeGuard(PENDING), ApprovalOutcomes()
        fake = FakeAppServer(_tool_script({"call_id": "c1", "tool": "read", "arguments": {}}))
        session = await started(fake, guard, approvals=approvals)
        try:
            sending = asyncio.ensure_future(session.send("go"))
            await wait_for_approval(approvals)
            assert approvals.approval_for(TASK.task_id) == "ap-1"
            assert not sending.done()
            guard.finish(pending_final(cause, released_outcome))
            assert await asyncio.wait_for(sending, 2) == "done"
            result = reply_result(fake)
            assert result["success"] is success
            assert result["contentItems"][0]["text"] == text
            assert len(guard.calls) == 1
            assert approvals.approval_for(TASK.task_id) is None
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "final",
    [
        pending_final("failed"),
        pending_final("shutdown"),
        pending_final("rejected", task_id="other-task"),
        pending_final("rejected", epoch=TASK.epoch + 1),
        # Bypasses PendingFinal validation: B's final_model_message refuses it.
        PendingFinal.model_construct(
            task_id=TASK.task_id,
            epoch=TASK.epoch,
            request_id="request-1",
            approval_id="ap-1",
            cause="rejected",
            outcome=released("PRIVATE"),
        ),
        None,
    ],
)
def test_unsettled_foreign_or_unknown_pending_result_stops_the_task(final) -> None:
    async def run() -> None:
        guard, approvals = FakeGuard(PENDING), ApprovalOutcomes()
        if final is None:
            guard.unknown_requests.add("request-1")

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.tool_call(turn_id, "c1", "read", {})  # type: ignore[attr-defined]
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        session = await started(fake, guard, approvals=approvals)
        try:
            sending = asyncio.ensure_future(session.send("go"))
            if final is not None:
                await wait_for_approval(approvals)
                guard.finish(final)
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(sending, 2)
            assert stopped.value.code == "CODEX_TOOL_STOPPED"
            assert reply_result(fake)["contentItems"][0]["text"] == "작업이 정지되었습니다"
        finally:
            await session.aclose()

    asyncio.run(run())


def test_watchdog_cancels_only_the_wait_and_keeps_guards_result() -> None:
    async def run() -> None:
        guard, approvals = FakeGuard(PENDING), ApprovalOutcomes()

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.last_reply = await server.tool_call(turn_id, "c1", "read", {})  # type: ignore[attr-defined]
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        session = await started(
            fake, guard, approvals=approvals, limits=SessionLimits(call_watchdog_s=0.05)
        )
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_WATCHDOG_TIMEOUT"
            assert not approvals.waiting("ap-1")
            assert not guard.results["request-1"].result.cancelled()
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
        asked: list[None] = []
        session.set_user_input_handler(lambda: asked.append(None))
        try:
            assert await session.send("go") == "continued"
            assert reply_result(fake) == {"answers": {}}
            assert session.observations.user_input_requests == 1
            assert len(asked) == 1  # STOP-01 audit hook; no question text is passed
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


# QA fixes (A88): stop handler, deferred stops, caps, watchdog and final answer.


async def wait_for_condition(check) -> None:
    for _ in range(400):
        if check():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def written_replies(fake: FakeAppServer) -> list[dict[str, object]]:
    return [message for message in fake.received if "method" not in message]


def test_l3_item_after_the_turn_reports_the_stop_once_through_the_handler() -> None:
    # T22/I11: no send() is waiting, so only the handler can stop the Task.
    async def run() -> None:
        after = asyncio.Event()

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            await server.agent(turn_id, "done")
            server.complete(turn_id)
            await after.wait()
            server.item(turn_id, "commandExecution")
            server.item(turn_id, "webSearch")

        stops: list[str] = []
        session = await started(FakeAppServer(script), FakeGuard())
        session.set_stop_handler(stops.append)
        try:
            assert await session.send("go") == "done"
            assert stops == []
            after.set()
            await wait_for_condition(lambda: stops)
            await asyncio.sleep(0.02)
            assert stops == ["CODEX_L3_VIOLATION"]
            assert session.stop_code == "CODEX_L3_VIOLATION"
        finally:
            await session.aclose()
        assert stops == ["CODEX_L3_VIOLATION"]

    asyncio.run(run())


def test_stop_while_send_waits_is_raised_by_send_not_the_handler() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.item(turn_id, "commandExecution")
            await asyncio.Event().wait()

        stops: list[str] = []
        session = await started(FakeAppServer(script), FakeGuard())
        session.set_stop_handler(stops.append)
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_L3_VIOLATION"
            await asyncio.sleep(0.02)
            assert stops == []
        finally:
            await session.aclose()
        assert stops == []

    asyncio.run(run())


def test_interrupt_is_quiet_once_the_client_closed() -> None:
    # Exit is confirmed by C's launcher, not by a turn/interrupt that cannot be sent.
    async def run() -> None:
        async def script(server: FakeAppServer, _turn_id: str, _text: str) -> None:
            server.close_stdout()

        fake = FakeAppServer(script)
        session = await started(fake, FakeGuard())
        try:
            with pytest.raises(CodexSessionError):
                await asyncio.wait_for(session.send("go"), 2)
            await session.interrupt()
            assert session.observations.interrupts_sent == 0
            assert fake.interrupts == 0
        finally:
            await session.aclose()

    asyncio.run(run())


def test_deferred_tool_stop_waits_for_the_tool_reply_not_another_reply() -> None:
    # I8/I9: the stopped answer reaches Codex before the stop; a requestUserInput
    # reply written in between must not apply the tool call's deferred stop.
    async def run() -> None:
        guard = FakeGuard(Withheld(request_id="r1", reasons=(Reason.RESULT_UNCLASSIFIED,)))
        hold = asyncio.Event()

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            user_input = asyncio.ensure_future(
                server.request(
                    "item/tool/requestUserInput",
                    {"threadId": THREAD_ID, "turnId": turn_id, "itemId": "i1", "questions": []},
                    hold_reply=hold,
                )
            )
            await asyncio.sleep(0)
            call = asyncio.ensure_future(server.tool_call(turn_id, "c1", "read", {}))
            await asyncio.gather(user_input, call)
            await asyncio.Event().wait()

        fake = FakeAppServer(script)
        stop_at_write: list[tuple[object, str | None]] = []
        session = await started(fake, guard)
        fake.on_write = lambda message: stop_at_write.append(
            (message.get("result"), session.stop_code)
        )
        try:
            sending = asyncio.ensure_future(session.send("go"))
            await wait_for_condition(lambda: session.observations.responses_by_call == {"c1": 1})
            await asyncio.sleep(0.02)
            assert session.stop_code is None
            hold.set()
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(sending, 2)
            assert stopped.value.code == "CODEX_TOOL_STOPPED"
            await wait_for_condition(lambda: len(written_replies(fake)) == 2)
            replies = [(result, code) for result, code in stop_at_write if result is not None]
            assert replies[0] == ({"answers": {}}, None)
            tool_result, code_when_written = replies[1]
            assert tool_result["contentItems"][0]["text"] == "작업이 정지되었습니다"
            assert code_when_written is None  # the stop came after this reply
        finally:
            await session.aclose()

    asyncio.run(run())


def test_streamed_answer_over_the_cap_stops_and_stops_forwarding() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.delta(turn_id, "a" * MAX_ANSWER_CHARS)
            server.delta(turn_id, "b")
            server.delta(turn_id, "c")
            await asyncio.Event().wait()

        partial: list[str] = []
        session = await started(FakeAppServer(script), FakeGuard(), partial=partial)
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_ANSWER_TOO_LARGE"
            assert partial == ["a" * MAX_ANSWER_CHARS]
        finally:
            await session.aclose()

    asyncio.run(run())


def test_streamed_answer_at_the_cap_is_kept() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.delta(turn_id, "a" * (MAX_ANSWER_CHARS - 1))
            server.delta(turn_id, "b")
            server.complete(turn_id)

        partial: list[str] = []
        session = await started(FakeAppServer(script), FakeGuard(), partial=partial)
        try:
            answer = await asyncio.wait_for(session.send("go"), 2)
            assert len(answer) == MAX_ANSWER_CHARS
            assert session.stop_code is None
            assert partial[-1] == "b"
        finally:
            await session.aclose()

    asyncio.run(run())


def test_watchdog_times_each_call_without_its_queue_wait() -> None:
    # c1 0.4 s and c2 0.3 s are each under the 0.6 s watchdog; c2 also waited
    # 0.4 s behind c1, which used to count against it (A80).
    async def run() -> None:
        guard = FakeGuard(released("one"), released("two"), delays=(0.4, 0.3))
        replies: list[dict[str, object]] = []

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            first = asyncio.ensure_future(server.tool_call(turn_id, "c1", "read", {}))
            second = asyncio.ensure_future(server.tool_call(turn_id, "c2", "read", {}))
            replies.extend(await asyncio.gather(first, second))
            await server.agent(turn_id, "both")
            server.complete(turn_id)

        session = await started(
            FakeAppServer(script), guard, limits=SessionLimits(call_watchdog_s=0.6)
        )
        try:
            assert await asyncio.wait_for(session.send("go"), 3) == "both"
            assert [r["result"]["contentItems"][0]["text"] for r in replies] == ["one", "two"]
            assert session.stop_code is None
        finally:
            await session.aclose()

    asyncio.run(run())


def test_calls_queued_behind_a_stop_never_reach_guard() -> None:
    # I1/I8: c2 was admitted before the stop but would run after it.
    async def run() -> None:
        block = asyncio.Event()
        guard = FakeGuard(released("one"), released("two"), block=block)
        holder: dict[str, CodexAppServerSession] = {}
        replies: list[dict[str, object]] = []

        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            first = asyncio.ensure_future(server.tool_call(turn_id, "c1", "read", {}))
            second = asyncio.ensure_future(server.tool_call(turn_id, "c2", "read", {}))
            await wait_for_condition(
                lambda: (
                    holder["session"].observations.tool_calls_received == 2
                    and len(guard.calls) == 1
                )
            )
            server.item(turn_id, "commandExecution")
            replies.extend(await asyncio.gather(first, second))
            await asyncio.Event().wait()

        session = await started(FakeAppServer(script), guard)
        holder["session"] = session
        try:
            with pytest.raises(CodexSessionError) as stopped:
                await asyncio.wait_for(session.send("go"), 2)
            assert stopped.value.code == "CODEX_L3_VIOLATION"
            block.set()
            await wait_for_condition(lambda: len(replies) == 2)
            assert [call.model_call_id for _t, call, _e in guard.calls] == ["c1"]
            assert session.observations.guard_submits == 1
            assert replies[1]["result"]["success"] is False
            assert replies[1]["result"]["contentItems"][0]["text"] == "작업이 정지되었습니다"
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("messages", "answer"),
    [
        ((("thinking", "commentary"), ("the answer", "final_answer")), "the answer"),
        ((("one", None), ("two", None)), "one\n\ntwo"),
        ((("a", "final_answer"), ("note", "commentary"), ("b", "final_answer")), "a\n\nb"),
    ],
    ids=["final-phase", "no-phase", "two-finals"],
)
def test_final_answer_prefers_final_phase_then_joins_with_a_blank_line(messages, answer) -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            for text, phase in messages:
                await server.agent(turn_id, text, phase)
            server.complete(turn_id)

        session = await started(FakeAppServer(script), FakeGuard())
        try:
            assert await asyncio.wait_for(session.send("go"), 2) == answer
        finally:
            await session.aclose()

    asyncio.run(run())


def test_tool_outside_the_codex_plan_is_denied_without_guard() -> None:
    # I11: only tool_refs registered with Codex are relayed.
    async def run() -> None:
        guard = FakeGuard()
        fake = FakeAppServer(_tool_script({"call_id": "c1", "tool": "write", "arguments": {}}))
        plan = build_dynamic_tools(views("docs__read"))
        session = await started(fake, guard, allowed=plan.tool_refs)
        try:
            assert await asyncio.wait_for(session.send("go"), 2) == "done"
            assert guard.calls == []
            result = reply_result(fake)
            assert result["success"] is False
            assert result["contentItems"][0]["text"] == (
                "이 요청은 정책에 의해 차단되었습니다: TOOL_UNSUPPORTED"
            )
            assert session.stop_code is None
        finally:
            await session.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("arguments", [[], ["path"], "a", 1, None])
def test_non_object_arguments_are_answered_invalid_without_stopping(arguments) -> None:
    # I7: model input is never repaired or forwarded; the turn continues.
    async def run() -> None:
        guard = FakeGuard()
        fake = FakeAppServer(
            _tool_script({"call_id": "c1", "tool": "read", "arguments": arguments})
        )
        session = await started(fake, guard)
        try:
            assert await asyncio.wait_for(session.send("go"), 2) == "done"
            assert guard.calls == []
            result = reply_result(fake)
            assert result["success"] is False
            assert result["contentItems"][0]["text"] == (
                "이 요청은 정책에 의해 차단되었습니다: INVALID_ARGUMENTS"
            )
            assert session.stop_code is None
        finally:
            await session.aclose()

    asyncio.run(run())


def test_streamed_pieces_break_between_answer_items_and_the_handler_can_be_set_later() -> None:
    async def run() -> None:
        async def script(server: FakeAppServer, turn_id: str, _text: str) -> None:
            server.delta(turn_id, "first ")
            server.delta(turn_id, "part")
            server.notify(
                "item/agentMessage/delta",
                {"threadId": THREAD_ID, "turnId": turn_id, "itemId": "msg-2", "delta": "second"},
            )
            server.complete(turn_id)

        session = await started(FakeAppServer(script), FakeGuard())
        partial: list[str] = []
        session.set_partial_handler(partial.append)
        try:
            # The answer itself keeps the raw deltas; only the shown pieces get breaks.
            assert await asyncio.wait_for(session.send("go"), 2) == "first partsecond"
            assert partial == ["first ", "part", "\n\nsecond"]
        finally:
            await session.aclose()

    asyncio.run(run())
