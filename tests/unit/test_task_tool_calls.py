"""A-local synthetic FIFO/state checks (10.4; I6/I7/I8, T21/T42 intent).

These count injected handler entries, not Guard dispatch/wire/receiver effects;
they do not claim Codex, Pending/STOP, T21/T42 or T46 acceptance.
"""

from __future__ import annotations

import asyncio

import pytest

from guard_app.services.task_tool_calls import TaskToolCallQueue, ToolCallQueueError


@pytest.mark.asyncio
async def test_same_task_fifo_holds_second_call_until_complete_result():
    async with asyncio.timeout(30):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(task_id, payload):
            calls.append((task_id, payload))
            if payload == "approval-wait":
                entered.set()
                await release.wait()
            return f"result:{payload}"

        queue = TaskToolCallQueue("trusted-task", handler)
        first = queue.enqueue("codex-call-1", "approval-wait")
        second = queue.enqueue("codex-call-2", "next")
        await entered.wait()
        assert calls == [("trusted-task", "approval-wait")]
        assert queue.pending_count == 2
        assert not second.done()
        release.set()
        assert await asyncio.gather(first, second) == ["result:approval-wait", "result:next"]
        assert calls == [("trusted-task", "approval-wait"), ("trusted-task", "next")]
        assert queue.pending_count == 0
        await queue.aclose()


@pytest.mark.asyncio
async def test_task_queues_overlap_and_call_ids_are_task_local():
    async with asyncio.timeout(30):
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        calls = []

        async def handler(task_id, payload):
            calls.append((task_id, payload))
            entered[int(task_id[-1])].set()
            await release.wait()
            return payload

        queues = [TaskToolCallQueue(f"trusted-{i}", handler) for i in range(2)]
        results = [queue.enqueue("same-codex-call-id", i) for i, queue in enumerate(queues)]
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
        assert calls == [("trusted-0", 0), ("trusted-1", 1)]
        assert not any(result.done() for result in results)
        release.set()
        assert await asyncio.gather(*results) == [0, 1]
        await asyncio.gather(*(queue.aclose() for queue in queues))


@pytest.mark.asyncio
async def test_duplicate_active_queued_and_completed_ids_never_reexecute():
    async with asyncio.timeout(30):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(task_id, payload):
            calls.append(payload)
            entered.set()
            await release.wait()
            return payload

        queue = TaskToolCallQueue("trusted", handler)
        first = queue.enqueue("call-active", "original")
        second = queue.enqueue("call-queued", "queued")
        await entered.wait()
        for call_id in ("call-active", "call-queued"):
            with pytest.raises(ToolCallQueueError, match="TOOL_CALL_DUPLICATE"):
                queue.enqueue(call_id, "changed-private-arguments")
        release.set()
        assert await asyncio.gather(first, second) == ["original", "queued"]
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_DUPLICATE"):
            queue.enqueue("call-active", "retry")
        assert calls == ["original", "queued"]
        await queue.aclose()


@pytest.mark.asyncio
async def test_trusted_task_identity_is_separate_from_model_payload():
    async with asyncio.timeout(30):
        calls = []

        async def handler(task_id, payload):
            calls.append((task_id, payload["task_id"]))
            return "synthetic-result"

        queue = TaskToolCallQueue("trusted-task", handler)
        assert await queue.enqueue("tool-call-id", {"task_id": "model-task"}) == "synthetic-result"
        assert calls == [("trusted-task", "model-task")]
        await queue.aclose()


@pytest.mark.asyncio
async def test_handler_failure_stops_queue_with_fixed_errors_and_no_retry(subtests):
    async with asyncio.timeout(30):

        async def check(error):
            calls = []

            async def handler(task_id, payload):
                calls.append(payload)
                raise error

            queue = TaskToolCallQueue("trusted", handler)
            first = queue.enqueue("one", "first")
            second = queue.enqueue("two", "second")
            results = await asyncio.gather(first, second, return_exceptions=True)
            assert all(isinstance(result, ToolCallQueueError) for result in results)
            assert [str(result) for result in results] == ["TOOL_CALL_HANDLER_FAILED"] * 2
            assert calls == ["first"]
            assert queue.closed
            assert queue.pending_count == 0
            with pytest.raises(ToolCallQueueError, match="TOOL_CALL_CLOSED"):
                queue.enqueue("three", "retry")
            await queue.aclose()

        for error in [OSError("private-error-text"), asyncio.CancelledError()]:
            with subtests.test(error=error):
                await check(error)


@pytest.mark.asyncio
async def test_close_before_scheduled_handler_starts_clears_all_work():
    async with asyncio.timeout(30):
        calls = []

        async def handler(task_id, payload):
            calls.append(payload)
            return payload

        queue = TaskToolCallQueue("trusted", handler)
        results = [queue.enqueue(str(i), i) for i in range(3)]
        queue.close()
        await queue.aclose()
        failures = await asyncio.gather(*results, return_exceptions=True)
        assert [str(result) for result in failures] == ["TOOL_CALL_CLOSED"] * 3
        assert not calls
        assert queue.pending_count == 0
        assert not queue._seen
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_CLOSED"):
            queue.enqueue("late", "late")


@pytest.mark.asyncio
async def test_future_cancel_before_handler_starts_never_enters_handler(subtests):
    async with asyncio.timeout(30):

        async def check(cancel_index):
            calls = []

            async def handler(task_id, payload):
                calls.append(payload)
                return payload

            queue = TaskToolCallQueue("trusted", handler)
            results = [queue.enqueue(str(i), i) for i in range(3)]
            results[cancel_index].cancel()
            outcomes = await asyncio.gather(*results, return_exceptions=True)
            assert isinstance(outcomes[cancel_index], asyncio.CancelledError)
            assert not calls
            assert queue.closed
            await queue.aclose()

        for cancel_index in [0, 1, 2]:
            with subtests.test(cancel_index=cancel_index):
                await check(cancel_index)


@pytest.mark.asyncio
async def test_cancellation_discards_late_result_and_never_starts_queued_work(subtests):
    async with asyncio.timeout(30):

        async def check(cancel_index):
            entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            calls = []

            async def handler(task_id, payload):
                calls.append(payload)
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                return "late-private-result"

            queue = TaskToolCallQueue("trusted", handler)
            results = [queue.enqueue(str(i), i) for i in range(3)]
            await entered.wait()
            results[cancel_index].cancel()
            await asyncio.wait_for(cancelled.wait(), 1)
            outcomes = await asyncio.gather(*results, return_exceptions=True)
            assert isinstance(outcomes[cancel_index], asyncio.CancelledError)
            assert all(isinstance(outcome, BaseException) for outcome in outcomes)
            release.set()
            await queue.aclose()
            assert calls == [0]
            assert queue.pending_count == 0

        for cancel_index in [0, 1, 2]:
            with subtests.test(cancel_index=cancel_index):
                await check(cancel_index)


@pytest.mark.asyncio
async def test_cancelled_future_rejects_new_admission_before_done_callback():
    async with asyncio.timeout(30):
        calls = []

        async def handler(task_id, payload):
            calls.append(payload)
            return payload

        queue = TaskToolCallQueue("trusted", handler)
        result = queue.enqueue("one", 1)
        result.cancel()
        # Deliberately no await: the Future's done callback has not run yet.
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_CANCELLED"):
            queue.enqueue("two", 2)
        assert queue.closed
        await queue.aclose()
        assert not calls


@pytest.mark.asyncio
async def test_shutdown_is_bounded_when_handler_suppresses_cancellation():
    async with asyncio.timeout(30):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        unhandled = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: unhandled.append(context))

        async def handler(task_id, payload):
            calls.append(payload)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            raise OSError("late-private-exception")

        queue = TaskToolCallQueue("trusted", handler)
        results = [queue.enqueue(str(i), i) for i in range(2)]
        await entered.wait()
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_CLEANUP_UNCONFIRMED"):
            await asyncio.wait_for(queue.aclose(cleanup_timeout_s=0.01), 1)
        outcomes = await asyncio.gather(*results, return_exceptions=True)
        assert [str(result) for result in outcomes] == ["TOOL_CALL_CLOSED"] * 2
        assert queue.closed
        release.set()
        await queue.aclose()
        assert queue.pending_count == 0
        assert calls == [0]
        assert not unhandled


@pytest.mark.asyncio
async def test_pending_limit_closes_queue_instead_of_skipping_dropped_call():
    async with asyncio.timeout(30):
        calls = []

        async def handler(task_id, payload):
            calls.append(payload)
            return payload

        queue = TaskToolCallQueue("trusted", handler, max_pending=2)
        results = [queue.enqueue(str(i), i) for i in range(2)]
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_LIMIT"):
            queue.enqueue("overflow", "private-payload")
        outcomes = await asyncio.gather(*results, return_exceptions=True)
        assert [str(result) for result in outcomes] == ["TOOL_CALL_LIMIT"] * 2
        assert not calls
        assert queue.closed
        await queue.aclose()


@pytest.mark.asyncio
async def test_lifetime_id_limit_never_evicts_an_id_and_allows_reexecution():
    async with asyncio.timeout(30):
        calls = []

        async def handler(task_id, payload):
            calls.append(payload)
            return payload

        queue = TaskToolCallQueue("trusted", handler, max_calls=2)
        assert await queue.enqueue("one", 1) == 1
        assert await queue.enqueue("two", 2) == 2
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_DUPLICATE"):
            queue.enqueue("one", "retry")
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_LIMIT"):
            queue.enqueue("three", 3)
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_CLOSED"):
            queue.enqueue("one", "retry-after-limit")
        assert calls == [1, 2]
        assert not queue._seen
        await queue.aclose()


@pytest.mark.asyncio
async def test_invalid_tool_call_id_closes_without_executing(subtests):
    async with asyncio.timeout(30):

        async def check(call_id):
            calls = []

            async def handler(task_id, payload):
                calls.append(payload)
                return payload

            queue = TaskToolCallQueue("trusted", handler)
            with pytest.raises(ToolCallQueueError, match="TOOL_CALL_INVALID_ID"):
                queue.enqueue(call_id, "private-payload")
            assert queue.closed
            assert not calls
            await queue.aclose()

        for call_id in ["", "x" * 257, 12, None]:
            with subtests.test(call_id=call_id):
                await check(call_id)


@pytest.mark.asyncio
async def test_failure_or_close_of_one_task_does_not_stop_another():
    async with asyncio.timeout(30):
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(task_id, payload):
            if task_id == "failed-task":
                raise OSError("private-failure")
            entered.set()
            await release.wait()
            return payload

        failed = TaskToolCallQueue("failed-task", handler)
        healthy = TaskToolCallQueue("healthy-task", handler)
        first, second = failed.enqueue("same", 1), healthy.enqueue("same", 2)
        await entered.wait()
        with pytest.raises(ToolCallQueueError, match="TOOL_CALL_HANDLER_FAILED"):
            await first
        await failed.aclose()
        assert not healthy.closed
        assert not second.done()
        release.set()
        assert await second == 2
        await healthy.aclose()
