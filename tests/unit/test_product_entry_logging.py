"""A146: entry-only log protection, before parsing/import effects (I9, S04)."""

import argparse
import builtins
import logging
import runpy
import sys
from pathlib import Path

import pytest

import guard_app
from guard_app.security import redact

from .logging_state import preserve_logging_state  # noqa: F401
from .product_folders import product_folders

pytestmark = pytest.mark.usefixtures("preserve_logging_state")


def test_entry_installs_once_before_parser_and_application_imports(monkeypatch):
    calls = []
    original_import = builtins.__import__
    install = redact.install_log_redaction
    parser_init = argparse.ArgumentParser.__init__

    def importing(name, *args, **kwargs):
        if name.startswith(("PySide6", "guard_app.services", "guard_app.ui")):
            calls.append("application_import")
        return original_import(name, *args, **kwargs)

    def installing():
        calls.append("redact")
        install()

    def parsing(*args, **kwargs):
        calls.append("parser")
        record = logging.getLogger("library").makeRecord(
            "library", logging.WARNING, "", 0, "TEST_SECRET_ENTRY", (), None
        )
        assert record.getMessage() == "LOG_WITHHELD"
        return parser_init(*args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    monkeypatch.setattr(redact, "install_log_redaction", installing)
    monkeypatch.setattr(argparse.ArgumentParser, "__init__", parsing)
    monkeypatch.setattr(sys, "argv", ["guard-app", "--help"])
    # Execute a fresh module namespace, even if other tests imported the entry.
    namespace = runpy.run_path(str(Path(guard_app.__file__).with_name("__main__.py")))
    assert calls == []
    with pytest.raises(SystemExit) as stopped:
        namespace["main"]()
    assert stopped.value.code == 0
    assert calls == ["redact", "parser"]


@pytest.mark.asyncio
async def test_assembly_does_not_install_process_logging(monkeypatch, tmp_path):
    import asyncio

    calls = []
    monkeypatch.setattr(redact, "install_log_redaction", lambda: calls.append("redact"))
    from examples.synthetic_app import bootstrap

    from guard_app.services.session_runtime import bootstrap_product

    from .test_a138_product_startup import read_product

    product = read_product()
    # Also catches an install bound before the patch (e.g. a module-level import).
    process_state = (logging.getLogRecordFactory(), sys.excepthook, redact._active)
    for assemble in (
        lambda: bootstrap_product(product_folders(str(tmp_path / "entry.db")), product=product),
        bootstrap,
    ):
        bindings = await asyncio.wait_for(assemble(), 15)
        try:
            assert calls == []
        finally:
            assert bindings.close is not None
            await asyncio.wait_for(bindings.close(), 15)
    assert calls == []
    assert (logging.getLogRecordFactory(), sys.excepthook, redact._active) == process_state
