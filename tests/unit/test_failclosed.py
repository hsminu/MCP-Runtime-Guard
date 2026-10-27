"""I6/I8의 두 변환 경계 단위 검증. 실제 dispatch·감사 내구성은 C 인수 항목이다."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from guard_app.security.failclosed import after_dispatch, fail_closed


@pytest.mark.parametrize("boundary", [fail_closed, after_dispatch])
def test_success_returns_unchanged_without_error_callbacks(
    boundary: Callable[
        [
            Callable[[], Awaitable[object]],
            Callable[[BaseException], Awaitable[object]],
            Callable[[BaseException], object],
        ],
        Awaitable[object],
    ],
) -> None:
    result = object()

    async def operation() -> object:
        return result

    async def on_error(error: BaseException) -> object:
        pytest.fail("성공 경로가 오류 변환을 호출했다")

    def fallback(error: BaseException) -> object:
        pytest.fail("성공 경로가 최종 정지를 호출했다")

    assert asyncio.run(boundary(operation, on_error, fallback)) is result


@pytest.mark.parametrize("boundary", [fail_closed, after_dispatch])
@pytest.mark.parametrize("exception", [ValueError, RuntimeError, asyncio.CancelledError])
def test_failure_is_transformed_once_without_retry(
    boundary: Callable[..., Awaitable[str]], exception: type[BaseException]
) -> None:
    events: list[str] = []

    async def operation() -> str:
        events.append("operation")
        raise exception("SECRET_MARKER")

    async def on_error(error: BaseException) -> str:
        events.append(type(error).__name__)
        return "contained"

    def fallback(error: BaseException) -> str:
        events.append("fallback")
        return "global_halt"

    assert asyncio.run(boundary(operation, on_error, fallback)) == "contained"
    assert events == ["operation", exception.__name__]
    assert "SECRET_MARKER" not in repr(events)


@pytest.mark.parametrize("boundary", [fail_closed, after_dispatch])
@pytest.mark.parametrize("exception", [RuntimeError, asyncio.CancelledError])
def test_containment_failure_uses_immediate_final_halt(
    boundary: Callable[..., Awaitable[str]], exception: type[BaseException]
) -> None:
    events: list[str] = []

    async def operation() -> str:
        raise ValueError("PRIVATE_SOURCE")

    async def on_error(error: BaseException) -> str:
        events.append("containment")
        raise exception("PRIVATE_PERSISTENCE")

    def fallback(error: BaseException) -> str:
        events.append(type(error).__name__)
        return "global_halt"

    assert asyncio.run(boundary(operation, on_error, fallback)) == "global_halt"
    assert events == ["containment", exception.__name__]


def test_fail_closed_without_final_handler_does_not_swallow_audit_failure() -> None:
    async def operation() -> str:
        raise ValueError("PRIVATE_REQUEST")

    async def on_error(error: BaseException) -> str:
        raise RuntimeError("PRIVATE_AUDIT")

    with pytest.raises(RuntimeError):
        asyncio.run(fail_closed(operation, on_error))


@pytest.mark.parametrize("boundary", [fail_closed, after_dispatch])
def test_actual_task_cancellation_is_contained(
    boundary: Callable[..., Awaitable[str]],
) -> None:
    async def exercise() -> str:
        started = asyncio.Event()
        pending: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def operation() -> str:
            started.set()
            return await pending

        async def on_error(error: BaseException) -> str:
            assert isinstance(error, asyncio.CancelledError)
            # 취소를 받은 task에서도 예약 후 상태 커밋 await를 실행할 수 있다.
            await asyncio.sleep(0)
            return "contained"

        task = asyncio.create_task(boundary(operation, on_error, lambda error: "global_halt"))
        await started.wait()
        task.cancel()
        return await task

    assert asyncio.run(exercise()) == "contained"


@pytest.mark.parametrize("callback_error", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("containment_cancel", [False, True])
@pytest.mark.parametrize("fallback_fails", [False, True])
def test_cancel_callback_failure_halts_and_preserves_original_cancellation(
    callback_error, containment_cancel, fallback_fails, caplog
):
    cancelled = asyncio.CancelledError()
    events = []
    halted = False

    async def operation():
        if containment_cancel:
            raise RuntimeError("PRIVATE_OPERATION")
        raise cancelled

    async def contain(error):
        if containment_cancel:
            raise cancelled
        return "withheld"

    def notify(result):
        events.append("notify")
        assert result in ("withheld", "halted")
        raise callback_error("PRIVATE_CALLBACK")

    def halt(error):
        nonlocal halted
        halted = True
        events.append(type(error).__name__)
        if fallback_fails and "notify" in events:
            raise RuntimeError("PRIVATE_HALT")
        return "halted"

    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(after_dispatch(operation, contain, halt, on_cancelled=notify))
    assert caught.value is cancelled
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert halted
    assert events.count("notify") == 1
    assert events[-1] == callback_error.__name__
    assert "PRIVATE" not in repr(caught.value) + repr(events) + caplog.text
