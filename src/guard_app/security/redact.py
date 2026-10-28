"""로그로 원문이 나가지 않게 한다(I9, C2.1·C2.8).

- `scanner_client_log_scope`: Scanner 호출 중 OpenAI SDK의 요청·응답 원문 디버그 로그를 차단한다.
- `install_log_redaction`: 앱 전체의 마지막 방어. 로그 레코드를 만들 때 정리해서 어느 handler에도
  원문이 가지 않는다. 1차 방어는 원문·인자를 로그에 넘기지 않는 것이고, MCP SDK 로그는 A
  `sdk_logging`이 SDK 작업 범위에서 따로 지운다(이 방어와 함께 감싸도 된다).
"""

import logging
import re
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from types import TracebackType
from typing import Any

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


LOG_WITHHELD = "LOG_WITHHELD"
LOG_FORMAT_INVALID = "LOG_FORMAT_INVALID"
MAX_LOG_CHARS = 500
_GUARD_LOGGER = "guard_app"
_SECRET_SHAPES = (
    # 이름 뒤 값: `api_key=...`, `"token": "..."`, `Authorization: Bearer ...`, `token ...`
    re.compile(
        r"(?i)\b(authorization|bearer|token|api[_-]?key|secret|password)"
        r"([\"']?\s*[:=]\s*[\"']?|\s+)(?:(?:bearer|basic)\s+)?[^\s\"',;]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),  # OpenAI 형식 키
    re.compile(r"TEST_SECRET_\w+"),  # 시험 표지
)
_active = False


def install_log_redaction() -> None:
    """앱 시작 때 한 번 부른다(A 조립). 다시 불러도 한 번만 감싼다.

    - `guard_app` 로거: 메시지를 만든 뒤 인증정보 모양을 가리고 500자로 자른다.
    - 그 밖의 로거(SDK·라이브러리·asyncio·warnings): 메시지 원문을 버리고 `LOG_WITHHELD`만 둔다.
      라이브러리는 서버 응답·요청 원문을 메시지에 넣기도 한다.
    - 모든 레코드: 예외는 형식 이름만 남기고 메시지·traceback·stack을 버린다.
    - 처리되지 않은 예외(`sys`·`threading` excepthook, unraisable)와 `warnings`도 로그로 보내 같은
      규칙을 따르게 한다.

    로그를 어디에 쓸지는 정하지 않는다(handler는 A 몫이다). handler가 없으면 Python 기본
    출력(stderr, WARNING 이상)에도 정리된 레코드만 간다.
    """
    global _active
    previous = logging.getLogRecordFactory()
    if not getattr(previous, "_guard_log_redaction", False):

        def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            if _active:
                _redact(record)
            return record

        record_factory._guard_log_redaction = True  # type: ignore[attr-defined]
        logging.setLogRecordFactory(record_factory)
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_thread_uncaught
    sys.unraisablehook = _log_unraisable
    logging.captureWarnings(True)
    _active = True


def mask_secrets(text: str) -> str:
    """인증정보 모양을 가린다. 이름은 남기고 값만 `***`로 바꾼다."""
    text = _SECRET_SHAPES[0].sub(lambda match: f"{match[1]}{match[2]}***", text)
    for shape in _SECRET_SHAPES[1:]:
        text = shape.sub("***", text)
    return text


def _redact(record: logging.LogRecord) -> None:
    exc_type = record.exc_info[0] if record.exc_info else None
    if record.name == _GUARD_LOGGER or record.name.startswith(_GUARD_LOGGER + "."):
        try:
            text = mask_secrets(record.getMessage())[:MAX_LOG_CHARS]
        except (TypeError, ValueError):
            text = LOG_FORMAT_INVALID
    else:
        text = LOG_WITHHELD
    if exc_type is not None:
        text = f"{text} [{exc_type.__name__}]"
    record.msg, record.args = text, ()
    record.exc_info = record.exc_text = record.stack_info = None


_UNCAUGHT = logging.getLogger("guard_app.uncaught")


def _log_uncaught(
    kind: type[BaseException], error: BaseException, trace: TracebackType | None
) -> None:
    _UNCAUGHT.critical("UNCAUGHT_EXCEPTION", exc_info=(kind, error, trace))


def _log_thread_uncaught(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is not None:
        _UNCAUGHT.critical(
            "UNCAUGHT_THREAD_EXCEPTION",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )


def _log_unraisable(unraisable: Any) -> None:
    error = unraisable.exc_value
    if error is not None:
        _UNCAUGHT.error(
            "UNRAISABLE_EXCEPTION", exc_info=(type(error), error, unraisable.exc_traceback)
        )


def _uninstall_for_tests(
    hooks: tuple[Callable[..., object], Callable[..., object], Callable[..., object]],
) -> None:
    """테스트 전용. 감싼 생성 함수는 그대로 두고(다른 감싸기가 위에 있을 수 있다) 끈다."""
    global _active
    _active = False
    sys.excepthook, threading.excepthook, sys.unraisablehook = hooks
    logging.captureWarnings(False)
