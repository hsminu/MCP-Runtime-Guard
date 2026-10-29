"""Restore all process-global state touched by the product entrypoint (A146)."""

import logging
import sys
import threading
import warnings

import pytest

from guard_app.security import redact


@pytest.fixture
def preserve_logging_state(monkeypatch):
    # captureWarnings also keeps its previous showwarning in logging itself.
    for owner, name in (
        (logging, "_logRecordFactory"),
        (logging, "_warnings_showwarning"),
        (warnings, "showwarning"),
        (sys, "excepthook"),
        (sys, "unraisablehook"),
        (threading, "excepthook"),
        (redact, "_active"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
