"""Log containment for the MCP SDK and its child tasks (I9).

The only module here that touches `logging`: SDK records lose their text
before any handler sees them.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

SDK_LOG_SCOPE: ContextVar[bool] = ContextVar("guard_sdk_log_scope", default=False)


def install_sdk_log_containment() -> None:
    """Sanitize before *any* handler, including MCP's root logging calls.

    SDK worker tasks inherit this ContextVar. Other application tasks retain
    their logging behavior. Keep the wrapper installed to contain late tasks;
    restoring a process-global factory while another worker lives is unsafe.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_guard_sdk_containment", False):
        return

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if SDK_LOG_SCOPE.get():
            record.msg = "MCP_LOG_WITHHELD"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    record_factory._guard_sdk_containment = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)
