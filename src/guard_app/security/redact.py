"""Scanner 호출 중 OpenAI SDK의 요청·응답 원문 디버그 로그를 차단한다(I9)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_SCANNER_LOG_SCOPE: ContextVar[bool] = ContextVar("scanner_sensitive_log_scope", default=False)


class _ScannerClientLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _SCANNER_LOG_SCOPE.get()


_CLIENT_LOG_FILTER = _ScannerClientLogFilter()


@contextmanager
def scanner_client_log_scope() -> Iterator[None]:
    """현재 async 호출의 SDK 로그만 제한한다. 다른 호출의 로그 설정은 유지한다."""
    # 설치 OpenAI 2.54.0의 원문 Request options/HTTP response 로그 위치.
    for name in ("openai._base_client", "openai._client"):
        logger = logging.getLogger(name)
        if _CLIENT_LOG_FILTER not in logger.filters:
            logger.addFilter(_CLIENT_LOG_FILTER)
    token = _SCANNER_LOG_SCOPE.set(True)
    try:
        yield
    finally:
        _SCANNER_LOG_SCOPE.reset(token)
