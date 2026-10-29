"""Product CodexTaskRunner: one input at a time, fail-closed responses (I7/I8/I9, T21)."""

from __future__ import annotations

import asyncio

import pytest

from guard_app.services.runner import (
    CodexAuthenticationError,
    CodexSessionError,
    CodexTaskRunner,
    RunnerStateError,
)


class Session:
    def __init__(self, response: object = "answer", error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.sent: list[str] = []
        self.efforts: list[str | None] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def send(self, text: str, effort: str | None = None) -> object:
        self.sent.append(text)
        self.efforts.append(effort)
        self.entered.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.response

    async def interrupt(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


def _runner(session: Session) -> tuple[CodexTaskRunner, list[str]]:
    failures: list[str] = []

    async def failure(code: str) -> None:
        failures.append(code)

    return CodexTaskRunner("task-1", 3, session, failure), failures  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_input_lifecycle_reasoning_and_response_boundary(subtests):
    async with asyncio.timeout(30):
        session = Session("hello")
        runner, failures = _runner(session)
        assert runner.can_accept_message
        assert not runner.can_advance
        with pytest.raises(RunnerStateError):
            await runner.advance()
        with pytest.raises(RunnerStateError):
            await runner.resume_approval("task-1", 3, "approval-1", object())
        for content in ["", "   ", 7, None]:
            with subtests.test(content=content):
                with pytest.raises(RunnerStateError):
                    runner.add_user_message(content)
                assert runner.can_accept_message
        runner.add_user_message("first")
        assert runner.can_advance
        assert not runner.can_accept_message
        update = await runner.advance()
        assert (update.task_id, update.epoch, update.status) == ("task-1", 3, "complete")
        assert update.assistant_text == "hello"
        assert runner.runtime_status == "complete"
        assert runner.can_accept_message
        assert session.sent == ["first"]
        assert failures == []
        session.efforts.clear()
        runner.add_user_message("think hard", "high")
        await runner.advance()
        runner.add_user_message("plain")
        await runner.advance()
        assert session.efforts == ["high", None]
        session.response = "x" * 65_536
        runner.add_user_message("go")
        assert (await runner.advance()).status == "complete"
        assert failures == []


@pytest.mark.asyncio
async def test_non_text_or_oversized_response_halts_and_closes(subtests):

    async with asyncio.timeout(30):
        for response in [None, b"bytes", 42, "x" * 65_537]:
            with subtests.test(response=response):
                runner, failures = _runner(Session(response))
                runner.add_user_message("go")
                update = await runner.advance()
                assert update.status == "halted"
                assert update.assistant_text is None
                assert failures == ["CODEX_SESSION_FAILED"]
                assert not runner.can_accept_message


@pytest.mark.asyncio
async def test_session_failures_halt_only_with_a_fixed_code(subtests):

    async with asyncio.timeout(30):
        for error, code in [
            (CodexAuthenticationError(), "CODEX_AUTH_FAILED"),
            (CodexSessionError(), "CODEX_SESSION_FAILED"),
        ]:
            with subtests.test(error=error, code=code):
                runner, failures = _runner(Session(error=error))
                runner.add_user_message("go")
                update = await runner.advance()
                assert update.status == "halted"
                assert failures == [code]
                assert not runner.can_accept_message


@pytest.mark.asyncio
async def test_unclassified_error_reports_one_failure_and_propagates() -> None:
    async with asyncio.timeout(30):
        runner, failures = _runner(Session(error=OSError("PRIVATE_MARKER")))
        runner.add_user_message("go")
        with pytest.raises(OSError, match="PRIVATE_MARKER"):
            await runner.advance()
        assert failures == ["CODEX_SESSION_FAILED"]
        assert not runner.can_accept_message


@pytest.mark.asyncio
async def test_close_during_send_discards_the_late_answer_without_failure() -> None:
    async with asyncio.timeout(30):
        session = Session("late answer")
        session.release.clear()
        runner, failures = _runner(session)
        runner.add_user_message("go")
        advancing = asyncio.create_task(runner.advance())
        await session.entered.wait()
        assert not runner.can_accept_message
        runner.close()
        session.release.set()
        update = await advancing
        assert update.status == "closed"
        assert update.assistant_text is None
        assert failures == []
