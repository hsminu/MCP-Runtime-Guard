"""단일 SQLite 소유 스레드와 취소 가능한 제한 큐."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from guard_app.domain.errors import StoreError
from guard_app.domain.reasons import Reason


@dataclass(repr=False)
class _Outcome:
    value: object = None
    error: BaseException | None = None


@dataclass(eq=False, repr=False)
class _Job:
    operation: Callable[[], object]
    write: bool
    loop: asyncio.AbstractEventLoop | None = None
    future: asyncio.Future[_Outcome] | None = None
    event: threading.Event | None = None
    started: bool = False
    outcome: _Outcome | None = None
    test_raw_exceptions: bool = False


class SQLiteWriter:
    """SQL 작업을 daemon 스레드 하나에서 직렬화한다."""

    def __init__(
        self,
        open_connection: Callable[[], None],
        close_connection: Callable[[], bool],
        capacity: int,
    ) -> None:
        self._open_connection = open_connection
        self._close_connection = close_connection
        self._capacity = capacity
        self._condition = threading.Condition()
        self._jobs: deque[_Job] = deque()
        self._closing = False
        self._close_reason = Reason.INTERNAL_ERROR
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._close_ok = False
        self._startup: _Outcome | None = None
        try:
            self._thread = threading.Thread(
                target=self._run, name="sqlite-store-writer", daemon=True
            )
            self._thread.start()
        except (OSError, RuntimeError):
            raise StoreError() from None
        self._ready.wait()
        if self._startup is None or self._startup.error is not None:
            self._closed.wait()
            raise StoreError() from None

    @staticmethod
    def _execute(operation: Callable[[], object], *, test_raw_exceptions: bool = False) -> _Outcome:
        # 작업 하나의 모든 실패를 호출자에게 전달해야 다음 큐 작업을 처리할 수 있다.
        # 예외 메시지와 traceback은 스레드 경계를 넘어가지 않는다.
        # writer 작업 경계의 넓은 예외 처리다(#58 합의, Semgrep 예외, 약점 대장).
        try:  # nosemgrep: broad-except-only-in-allowed
            return _Outcome(value=operation())
        except BaseException as error:  # noqa: BLE001
            if test_raw_exceptions:
                return _Outcome(error=error)
            failure = StoreError(error.reason) if isinstance(error, StoreError) else StoreError()
            return _Outcome(error=failure)

    @staticmethod
    def _set_result(future: asyncio.Future[_Outcome], outcome: _Outcome) -> None:
        if not future.done():
            future.set_result(outcome)

    def _publish(self, job: _Job, outcome: _Outcome) -> None:
        if job.event is not None:
            job.outcome = outcome
            job.event.set()
        elif job.loop is not None and job.future is not None:
            # 결과를 받을 루프가 이미 닫혔으면 이 결과만 버린다.
            with suppress(RuntimeError):
                job.loop.call_soon_threadsafe(self._set_result, job.future, outcome)

    def _run(self) -> None:
        self._startup = self._execute(self._open_connection)
        self._ready.set()
        if self._startup.error is None:
            while True:
                with self._condition:
                    while not self._jobs and not self._closing:
                        self._condition.wait()
                    if not self._jobs and self._closing:
                        break
                    job = self._jobs.popleft()
                    job.started = True
                self._publish(
                    job, self._execute(job.operation, test_raw_exceptions=job.test_raw_exceptions)
                )
        result = self._execute(self._close_connection)
        self._close_ok = result.error is None and result.value is True
        self._closed.set()

    def _enqueue(self, job: _Job) -> None:
        with self._condition:
            if self._closing:
                raise StoreError(self._close_reason)
            if len(self._jobs) >= self._capacity:
                raise StoreError()
            self._jobs.append(job)
            self._condition.notify()

    async def submit[T](
        self, operation: Callable[[], T], *, write: bool, test_raw_exceptions: bool = False
    ) -> T:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[_Outcome] = loop.create_future()
        job = _Job(
            operation=operation,
            write=write,
            loop=loop,
            future=future,
            test_raw_exceptions=test_raw_exceptions,
        )
        self._enqueue(job)
        try:
            outcome = await asyncio.shield(future)
        except asyncio.CancelledError:
            with self._condition:
                if not job.started:
                    if job in self._jobs:
                        self._jobs.remove(job)
                    raise
                wait_for_write = job.write
            if wait_for_write:
                # 시작된 쓰기는 커밋/롤백 완료 전에 취소를 호출자에게 돌리지 않는다.
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
            raise
        if outcome.error is not None:
            raise outcome.error from None
        return cast(T, outcome.value)

    def submit_sync[T](self, operation: Callable[[], T]) -> T:
        if threading.get_ident() == self._thread.ident:
            return operation()
        event = threading.Event()
        job = _Job(operation=operation, write=False, event=event, test_raw_exceptions=True)
        self._enqueue(job)
        event.wait()
        if job.outcome is None:
            raise StoreError()
        if job.outcome.error is not None:
            raise job.outcome.error from None
        return cast(T, job.outcome.value)

    def close(self, reason: Reason = Reason.INTERNAL_ERROR) -> None:
        with self._condition:
            if self._closing:
                return
            self._closing = True
            self._close_reason = reason
            pending = tuple(self._jobs)
            self._jobs.clear()
            self._condition.notify_all()
        for job in pending:
            self._publish(job, _Outcome(error=StoreError(reason)))

    async def wait_closed(self, timeout: float) -> bool:  # noqa: ASYNC109
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not self._closed.is_set():
            remaining = end - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.01, remaining))
        return self._close_ok

    def wait_closed_sync(self, timeout: float) -> bool:
        return self._closed.wait(timeout) and self._close_ok
