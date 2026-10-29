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
    try:  # nosemgrep: broad-except-only-in-allowed
        return await operation()
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001
        if on_containment_error is None:
            return await on_error(error)
        try:  # nosemgrep: broad-except-only-in-allowed
            return await on_error(error)
        except (Exception, asyncio.CancelledError) as containment_error:  # noqa: BLE001
            return on_containment_error(containment_error)


async def after_dispatch[T](
    operation: Callable[[], Awaitable[T]],
    on_error: Callable[[BaseException], Awaitable[T]],
    on_containment_error: Callable[[BaseException], T],
    *,
    on_cancelled: Callable[[T], None] | None = None,
) -> T:
    """예약 후 실패와 격리 기록 실패를 분리한다. 재전송 fallback은 없다.

    승인 실행은 on_cancelled로 격리 결과를 알린 뒤 취소를 다시 전파한다.
    콜백 실패는 이 after_dispatch 경계에서 즉시 on_containment_error로 넘겨
    전역 정지를 요청한다. 그 처리의 실패도 원래 취소를 대체하지 않는다.
    예외 원문은 기록하지 않으며 콜백을 재시도하지 않는다.
    격리 중 취소와 취소 콜백 실패가 겹치면 on_containment_error가 두 번 불릴 수
    있으므로 on_containment_error는 멱등이어야 한다.
    콜백이 없는 기존 호출의 결과 변환 의미는 유지한다.
    """
    try:  # nosemgrep: broad-except-only-in-allowed
        return await operation()
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001
        cancelled = error if isinstance(error, asyncio.CancelledError) else None
        try:  # nosemgrep: broad-except-only-in-allowed
            result = await on_error(error)
        except (Exception, asyncio.CancelledError) as containment_error:  # noqa: BLE001
            result = on_containment_error(containment_error)
            if cancelled is None and isinstance(containment_error, asyncio.CancelledError):
                cancelled = containment_error
        if cancelled is not None and on_cancelled is not None:
            try:  # nosemgrep: broad-except-only-in-allowed
                on_cancelled(result)
            except (Exception, asyncio.CancelledError) as callback_error:  # noqa: BLE001
                try:
                    on_containment_error(callback_error)
                finally:
                    raise cancelled from None
            raise cancelled from None
        return result
