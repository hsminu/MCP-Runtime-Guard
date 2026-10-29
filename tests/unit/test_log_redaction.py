"""security.redact.install_log_redaction(C2.8, I9, S04) 단위 테스트.

로그 레코드를 만들 때 정리되므로 root handler·라이브러리가 직접 붙인 handler 모두 원문을 받지
않는다. asyncio의 실제 경로는 방어를 끈 대조군에서 표지가 실제로 나가는 것도 확인한다.
"""

import asyncio
import gc
import io
import logging
import sys
import threading
import warnings

import pytest

from guard_app.adapters.mcp_transport.sdk_logging import SDK_LOG_SCOPE, install_sdk_log_containment
from guard_app.security import redact
from guard_app.security.redact import (
    LOG_FORMAT_INVALID,
    LOG_WITHHELD,
    MAX_LOG_CHARS,
    install_log_redaction,
    mask_secrets,
)

MARKER = "TEST_SECRET_c28_planted"
PLAIN_MARKER = "plain-document-text-c28"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.stream = io.StringIO()
        self.setFormatter(logging.Formatter("%(name)s|%(levelname)s|%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.stream.write(self.format(record) + "\n")

    @property
    def lines(self) -> list[str]:
        return self.stream.getvalue().splitlines()


@pytest.fixture
def capture():
    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


@pytest.fixture
def redaction():
    hooks = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    install_log_redaction()
    yield
    redact._uninstall_for_tests(hooks)


def test_guard_logs_keep_their_text_but_not_secrets(redaction, capture):
    logging.getLogger("guard_app.test").warning(
        "call %s api_key=%s Authorization: Bearer %s %s",
        "read_file",
        "sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        "eyJ.token.value",
        MARKER,
    )
    assert capture.lines == [
        "guard_app.test|WARNING|call read_file api_key=*** Authorization: *** ***"
    ]


def test_guard_log_exceptions_keep_only_the_type(redaction, capture):
    try:
        raise ValueError(MARKER)
    except ValueError:
        logging.getLogger("guard_app.test").exception("STORE_FAILED")
    assert capture.lines == ["guard_app.test|ERROR|STORE_FAILED [ValueError]"]
    assert "Traceback" not in capture.stream.getvalue()


@pytest.mark.parametrize(
    "name", [f"Leak {MARKER}", f"{MARKER}é", "E" * 65], ids=["space", "non-ascii", "long"]
)
def test_exception_type_names_follow_the_audit_rule(redaction, capture, name):
    kind = type(name, (ValueError,), {})
    try:
        raise kind("x")
    except ValueError:
        logging.getLogger("guard_app.test").exception("STORE_FAILED")
        logging.getLogger("asyncio").error("Task exception", exc_info=True)
    assert capture.lines == [
        "guard_app.test|ERROR|STORE_FAILED [Exception]",
        f"asyncio|ERROR|{LOG_WITHHELD} [Exception]",
    ]
    assert MARKER not in capture.stream.getvalue()


def test_library_logs_are_withheld(redaction, capture):
    logging.getLogger("httpx").warning("POST %s body=%s", "https://example.test/x", PLAIN_MARKER)
    logging.getLogger("mcp.client.streamable_http").warning("Raw result: %s", PLAIN_MARKER)
    assert capture.lines == [
        f"httpx|WARNING|{LOG_WITHHELD}",
        f"mcp.client.streamable_http|WARNING|{LOG_WITHHELD}",
    ]


def test_handlers_that_libraries_attach_themselves_get_the_cleaned_record(redaction):
    """litellm처럼 자기 로거에 handler를 붙이고 전파를 끈 경우."""
    own = _Capture()
    logger = logging.getLogger("LiteLLM-c28-test")
    logger.addHandler(own)
    logger.propagate = False
    try:
        logger.warning("request messages=%s", PLAIN_MARKER)
    finally:
        logger.removeHandler(own)
    assert own.lines == [f"LiteLLM-c28-test|WARNING|{LOG_WITHHELD}"]


def test_long_and_malformed_messages(redaction, capture):
    logger = logging.getLogger("guard_app.test")
    logger.warning("x" * (MAX_LOG_CHARS * 3))
    logger.warning("count %d", "not-a-number")
    first, second = capture.lines
    assert first == "guard_app.test|WARNING|" + "x" * MAX_LOG_CHARS
    assert second == f"guard_app.test|WARNING|{LOG_FORMAT_INVALID}"


def test_uncaught_exceptions_reach_the_log_without_their_text(redaction, capture, capfd):
    def fail():
        raise RuntimeError(MARKER)

    worker = threading.Thread(target=fail)
    worker.start()
    worker.join()
    try:
        raise KeyError(MARKER)
    except KeyError:
        sys.excepthook(*sys.exc_info())

    class Broken:
        def __del__(self):
            raise OSError(MARKER)

    Broken()
    gc.collect()
    assert capture.lines == [
        "guard_app.uncaught|CRITICAL|UNCAUGHT_THREAD_EXCEPTION [RuntimeError]",
        "guard_app.uncaught|CRITICAL|UNCAUGHT_EXCEPTION [KeyError]",
        "guard_app.uncaught|ERROR|UNRAISABLE_EXCEPTION [OSError]",
    ]
    captured = capfd.readouterr()
    assert MARKER not in captured.err + captured.out


def test_warnings_are_withheld(redaction, capture):
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(PLAIN_MARKER, UserWarning, stacklevel=1)
    assert capture.lines == [f"py.warnings|WARNING|{LOG_WITHHELD}"]


def _drop_failed_task() -> None:
    async def fail():
        raise ValueError(PLAIN_MARKER)

    async def scenario():
        task = asyncio.get_running_loop().create_task(fail())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert task.done()
        del task
        gc.collect()

    asyncio.run(scenario())
    gc.collect()


def test_asyncio_task_exception_leaks_without_redaction(capture):
    """대조군: 방어가 없으면 asyncio가 예외 원문을 메시지와 traceback에 싣는다."""
    _drop_failed_task()
    assert PLAIN_MARKER in capture.stream.getvalue()


def test_asyncio_task_exception_is_withheld(redaction, capture):
    _drop_failed_task()
    assert capture.lines == [f"asyncio|ERROR|{LOG_WITHHELD} [ValueError]"]


def test_works_with_the_sdk_containment_in_either_order(redaction, capture):
    install_sdk_log_containment()  # A: SDK 작업 범위의 레코드를 지운다
    install_log_redaction()  # 다시 불러도 한 번만 감싼다
    token = SDK_LOG_SCOPE.set(True)
    try:
        logging.getLogger("mcp.shared.session").warning("Message was: %s", PLAIN_MARKER)
        logging.getLogger("guard_app.test").warning("inside %s", MARKER)
    finally:
        SDK_LOG_SCOPE.reset(token)
    text = capture.stream.getvalue()
    assert PLAIN_MARKER not in text
    assert MARKER not in text
    assert len(capture.lines) == 2


def test_install_wraps_the_record_factory_once(redaction):
    factory = logging.getLogRecordFactory()
    install_log_redaction()
    assert logging.getLogRecordFactory() is factory


def test_mask_secrets_shapes():
    cases = {
        "api_key=abc123": "api_key=***",
        '{"api_key": "abc", "n": 1}': '{"api_key": "***", "n": 1}',
        "secret='s3'": "secret='***'",
        "Authorization: Basic dXNlcjpw": "Authorization: ***",
        "Bearer xyz": "Bearer ***",
        "key sk-ABCDEFGHIJKLMNOPQRST tail": "key *** tail",
        f"x {MARKER} y": "x *** y",
        "tokenizer ok": "tokenizer ok",
        "passwords: 3": "passwords: 3",
    }
    for text, masked in cases.items():
        assert mask_secrets(text) == masked
