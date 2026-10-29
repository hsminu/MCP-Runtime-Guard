"""A48/A147 composition only: fake credentials and folders (I9, S04)."""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from guard_app import __main__ as entry
from guard_app.security import secrets
from guard_app.security.paths import AppPathError, AppPaths
from guard_app.services import codex_preparation, product_settings
from guard_app.ui.scanner_key import NO_SAVED_KEY, NOT_CONNECTED, SAVE_FAILED
from guard_app.ui.window import GuardWindow

from .logging_state import preserve_logging_state  # noqa: F401

pytestmark = pytest.mark.usefixtures("preserve_logging_state")
KEY = "sk-proj-TEST_SECRET_A48_9z7Q"


@pytest.fixture
def owner(monkeypatch, tmp_path):
    paths = AppPaths(tmp_path)
    paths.data.mkdir()
    events = []
    owner = SimpleNamespace(
        folders=SimpleNamespace(paths=paths),
        db_file=paths.db_file,
        claim=lambda: events.append("claim"),
        close=lambda: events.append("close"),
        preparation=lambda: None,
        account_access=lambda **kw: None,
        set_progress_listener=lambda callback: setattr(owner, "progress", callback),
        events=events,
    )
    monkeypatch.setattr(codex_preparation, "CodexProcessPreparation", lambda **kw: owner)
    monkeypatch.setattr(product_settings, "read_settings", lambda folders: None)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    return owner


@pytest.mark.parametrize("outcome", ["normal", "exception", "unconfirmed"])
def test_product_log_settings_redaction_and_cleanup(monkeypatch, owner, outcome):
    root = logging.getLogger()
    before = root.handlers[:], root.level
    captured = []

    def run(*args):
        assert owner.events == ["claim"]
        handlers = [h for h in root.handlers if h not in before[0]]
        assert len(handlers) == 1
        handler = handlers[0]
        captured.append(handler)
        assert isinstance(handler, RotatingFileHandler)
        assert (handler.maxBytes, handler.backupCount, handler.level) == (1048576, 2, logging.INFO)
        assert handler.encoding == "utf-8"
        assert handler.formatter._fmt == "%(asctime)s %(levelname)s %(name)s %(message)s"
        logging.getLogger("guard_app.unit").info("KEY_SAVED TEST_SECRET_PRODUCT")
        logging.getLogger("library").warning("TEST_SECRET_LIBRARY")
        if outcome == "exception":
            raise ValueError("synthetic")
        return 0, outcome != "unconfirmed"

    monkeypatch.setattr(entry, "_run", run)
    monkeypatch.setattr(entry, "_end_unconfirmed", lambda: None)
    if outcome == "exception":
        with pytest.raises(ValueError, match="^synthetic$"):
            entry.main()
    else:
        assert entry.main() == 0
    assert (root.handlers, root.level) == before
    assert captured[0].stream is None
    text = (owner.folders.paths.data / "guard.log").read_text(encoding="utf-8")
    assert "KEY_SAVED" in text
    assert "LOG_WITHHELD" in text
    assert "TEST_SECRET_" not in text
    assert owner.events == (["claim"] if outcome == "unconfirmed" else ["claim", "close"])


@pytest.mark.parametrize("mode", ["demo", "locked", "arguments"])
def test_no_log_before_product_claim(monkeypatch, owner, mode):
    def refused():
        raise AppPathError("APP_ALREADY_RUNNING")

    if mode == "locked":
        owner.claim = refused
    monkeypatch.setattr(entry, "_run", lambda *args: (0, True))
    monkeypatch.setattr(
        sys,
        "argv",
        ["guard-app"]
        + {"demo": ["--demo"], "locked": [], "arguments": ["--store", "sqlite"]}[mode],
    )
    if mode == "arguments":
        with pytest.raises(SystemExit):
            entry.main()
    else:
        assert entry.main() == (1 if mode == "locked" else 0)
    assert list(owner.folders.paths.data.iterdir()) == []


def test_log_open_failure_releases_claim(monkeypatch, owner, capsys):
    (owner.folders.paths.data / "guard.log").mkdir()
    monkeypatch.setattr(entry, "_run", lambda *args: pytest.fail("must not run"))
    assert entry.main() == 1
    assert owner.events == ["claim", "close"]
    assert capsys.readouterr().err == "로그 파일을 열지 못해 앱을 시작하지 않음\n"


@pytest.mark.parametrize("mode", ["saved", "missing", "error", "invalid", "demo"])
def test_run_wires_real_window_without_keeping_key(monkeypatch, owner, mode, caplog):
    from guard_app.services.bridge import BackendBridge

    app = QApplication.instance() or QApplication([])
    previous = app.quitOnLastWindowClosed()
    tails = []
    saves = []
    windows = []

    def tail():
        tails.append(True)
        if mode == "error":
            raise secrets.ScannerKeyError("TEST_SECRET_FAILURE")
        return {"saved": "9z7Q", "missing": None, "invalid": KEY}.get(mode)

    def save(key):
        saves.append(key == KEY)
        return len(saves) == 1

    class StopBeforeBackendError(Exception):
        pass

    def start(bridge):
        if mode != "demo":
            assert owner.progress == bridge.codex_progress
            assert bridge._startup_timeout >= 60 + 50 + 20
            assert bridge.shutdown_timeout >= 20 + 5
        window = next(
            w for w in app.topLevelWidgets() if isinstance(w, GuardWindow) and w.isVisible()
        )
        windows.append(window)
        form = window.scanner_key_form
        assert tails == ([] if mode == "demo" else [True])
        expected = NOT_CONNECTED if mode == "demo" else NO_SAVED_KEY
        if mode == "saved":
            assert form.saved_suffix == "9z7Q"
            assert "끝 4자리 9z7Q" in form.status.text()
        else:
            assert form.status.text() == expected
        emitted = []
        form.key_saved.connect(lambda *args: emitted.append(args))
        for _ in range(2):
            form.key_input.setText(KEY)
            form._save()
        assert saves == ([] if mode == "demo" else [True, True])
        assert emitted == ([] if mode == "demo" else [()])
        assert form.status.text() == (NOT_CONNECTED if mode == "demo" else SAVE_FAILED)
        assert form.key_input.text() == ""
        assert form.saved_suffix == ""
        assert KEY not in repr(vars(form))
        assert all("TEST_SECRET_" not in label.text() for label in window.findChildren(QLabel))
        assert "TEST_SECRET_" not in caplog.text
        raise StopBeforeBackendError

    monkeypatch.setattr(secrets, "scanner_key_tail", tail)
    monkeypatch.setattr(secrets, "save_scanner_key", save)
    monkeypatch.setattr(BackendBridge, "start", start)
    args = argparse.Namespace(demo=mode == "demo", store="memory")
    try:
        with pytest.raises(StopBeforeBackendError):
            entry._run(args, None if mode == "demo" else owner)
    finally:
        for window in windows:
            window.hide()
            window.deleteLater()
        app.sendPostedEvents()
        app.setQuitOnLastWindowClosed(previous)
