"""I6/I8: 예약 전후에만 넓은 예외를 도메인 결과로 변환한다.

콜백은 원문을 기록하지 않고 예외 타입만 감사 메타데이터로 사용해야 한다.
CancelledError도 실제 실행 단계에 따라 거부 또는 격리로 확정한다.
"""

import asyncio
from collections.abc import Awaitable, Callable


async def fail_closed[T](
    operation: Callable[[], Awaitable[T]],
    on_error: Callable[[BaseException], Awaitable[T]],
    on_containment_error: Callable[[BaseException], T] | None = None,
) -> T:
    """예약 전 실패를 변환한다. 감사 실패 시 공급된 global-halt 경계를 사용한다."""
    try:
        return await operation()
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001
        if on_containment_error is None:
            return await on_error(error)
        try:
            return await on_error(error)
        except (Exception, asyncio.CancelledError) as containment_error:  # noqa: BLE001
            return on_containment_error(containment_error)


async def after_dispatch[T](
    operation: Callable[[], Awaitable[T]],
    on_error: Callable[[BaseException], Awaitable[T]],
    on_containment_error: Callable[[BaseException], T],
) -> T:
    """예약 후 실패와 격리 기록 실패를 분리한다. 재전송 fallback은 없다."""
    try:
        return await operation()
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001
        try:
            return await on_error(error)
        except (Exception, asyncio.CancelledError) as containment_error:  # noqa: BLE001
            return on_containment_error(containment_error)
