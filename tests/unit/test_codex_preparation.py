"""A101: SessionPreparation over C2.12 launcher, C2.13 folders and C6.13 CODEX_HOME.

Like C's launcher tests, this Python's python.exe stands in for codex.exe with a
pinned copy of `codex.lock.json`. Real folders, DACLs, the app lock and a real
child process run; L2 (C6.14) is not connected, so sessions stay refused (I11,
T22/T43 A part). The real codex.exe is C's evidence, not exercised here.

A102: the product claims the single-instance lock before its DB opens and does
not start without it (REC-01, C2.13 part).

A106: that DB is fixed in the app folder (C2.17), and the demo, which takes no
lock, cannot open it or anything else in that folder.

A52 (#21): the login screen's app-server goes through the same launcher in a
folder of its own, and logout cleanup calls C's `clear_codex_credentials` on the
app's CODEX_HOME. The real keyring deletion is C's test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import guard_app.__main__ as entry
from guard_app.security import paths
from guard_app.security.codex_home import (
    CODEX_CONFIG_DIR,
    HOME_FILES,
    LOCK_FILE,
    CodexConfigError,
)
from guard_app.security.launcher import LaunchError
from guard_app.security.paths import AppPathError, prepare_app_folders
from guard_app.services import codex_preparation, session_runtime
from guard_app.services.account_runtime import AccountAccess, AccountUnavailableError
from guard_app.services.bridge import SessionOptions
from guard_app.services.codex_account import AccountError
from guard_app.services.codex_preparation import (
    CodexPreparationError,
    CodexProcessPreparation,
)
from guard_app.services.codex_session import CodexPipes
from guard_app.services.session_preparation import valid_configuration
from guard_app.services.session_runtime import SessionRuntimeError, bootstrap_product

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="C2.12·C2.13은 Windows 전용이다")

PYTHON = Path(sys.base_prefix) / "python.exe"
TASK_ID = "session-0123456789abcdef"


@pytest.fixture(scope="module")
def pin() -> tuple[int, str]:
    data = PYTHON.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def source_for(tmp_path: Path, size: int, sha256: str) -> Path:
    """The repository's Codex config with python.exe pinned in place of codex.exe."""
    source = tmp_path / "source"
    source.mkdir()
    for name in HOME_FILES:
        shutil.copyfile(CODEX_CONFIG_DIR / name, source / name)
    lock = json.loads((CODEX_CONFIG_DIR / LOCK_FILE).read_text(encoding="utf-8"))
    lock["executable"]["size"], lock["executable"]["sha256"] = size, sha256
    (source / LOCK_FILE).write_text(json.dumps(lock), encoding="utf-8")
    return source


def preparation(
    tmp_path: Path, pin: tuple[int, str], *, claim: bool = True, **kwargs: object
) -> CodexProcessPreparation:
    """An owner that already holds the app lock, as `main` hands it over."""
    options: dict[str, object] = {"executable": PYTHON, "root": tmp_path / "app"}
    options.update(kwargs)
    owner = CodexProcessPreparation(
        source_dir=source_for(tmp_path, *pin),
        **options,  # type: ignore[arg-type]
    )
    if claim:
        owner.claim()
    return owner


def assert_lock_held(root: Path) -> None:
    with pytest.raises(AppPathError, match="^APP_ALREADY_RUNNING$"):
        prepare_app_folders(root)


def test_startup_prepares_folders_and_codex_home_and_reports_no_l2(tmp_path, pin) -> None:
    owner = preparation(tmp_path, pin)

    async def run():
        startup = owner.preparation().prepare_startup
        receipt = await startup()
        with pytest.raises(CodexPreparationError, match="^CODEX_STARTUP_REPEATED$"):
            await startup()
        return receipt

    try:
        receipt = asyncio.run(run())
    finally:
        owner.close()
    lock = json.loads((CODEX_CONFIG_DIR / LOCK_FILE).read_text(encoding="utf-8"))
    assert receipt.l2_configuration is None
    assert receipt.l2_passed is False
    assert receipt.configuration.executable_digest == pin[1]
    assert receipt.configuration.model_digest == lock["codex_home"]["model_catalog.json"]
    home = tmp_path / "app" / "codex_home"
    for name in HOME_FILES:
        assert (home / name).read_bytes() == (CODEX_CONFIG_DIR / name).read_bytes()
    # close() released the single-instance lock, so the next start takes it.
    again = CodexProcessPreparation(
        executable=PYTHON, root=tmp_path / "app", source_dir=tmp_path / "source"
    )
    again.claim()
    try:
        asyncio.run(again.preparation().prepare_startup())
    finally:
        again.close()


def test_the_claim_comes_first_and_holds_the_lock_until_close(tmp_path, pin) -> None:
    """A102: startup needs the claim, and a second claim is refused while it holds."""
    root = tmp_path / "app"
    unclaimed = preparation(tmp_path, pin, claim=False)
    with pytest.raises(CodexPreparationError, match="^CODEX_FOLDERS_UNCLAIMED$"):
        asyncio.run(unclaimed.preparation().prepare_startup())
    assert not root.exists()

    owner = CodexProcessPreparation(executable=PYTHON, root=root, source_dir=tmp_path / "source")
    owner.claim()
    try:
        with pytest.raises(CodexPreparationError, match="^CODEX_FOLDERS_CLAIMED$"):
            owner.claim()
        other = CodexProcessPreparation(root=root)
        with pytest.raises(AppPathError, match="^APP_ALREADY_RUNNING$"):
            other.claim()
        other.close()
        assert_lock_held(root)
    finally:
        owner.close()
    prepare_app_folders(root).close()


def test_task_runs_in_its_own_folder_which_is_removed_only_after_exit(tmp_path, pin) -> None:
    owner = preparation(tmp_path, pin)
    steps = owner.preparation()
    folder = tmp_path / "app" / "tasks" / TASK_ID

    async def run():
        receipt = await steps.prepare_startup()
        assert receipt.configuration == await steps.verify_configuration(receipt.configuration)
        resources = await steps.prepare_task(TASK_ID, receipt.configuration)
        assert resources.configuration == receipt.configuration
        assert isinstance(resources.handle, CodexPipes)
        assert folder.is_dir()
        with pytest.raises(CodexPreparationError, match="^CODEX_EXIT_UNCONFIRMED$"):
            await steps.cleanup_task(resources)
        assert folder.is_dir()
        assert await steps.confirm_exit(resources) is True
        await steps.cleanup_task(resources)
        assert not folder.exists()
        with pytest.raises(CodexPreparationError, match="^CODEX_TASK_UNKNOWN$"):
            await steps.cleanup_task(resources)

    try:
        asyncio.run(run())
    finally:
        owner.close()


def test_task_folder_is_removed_off_the_event_loop(tmp_path, pin, monkeypatch) -> None:
    """A103 (PR #35 B review): deleting the Codex log DB must not stall other Tasks."""
    owner = preparation(tmp_path, pin)
    steps = owner.preparation()
    folder = tmp_path / "app" / "tasks" / TASK_ID
    removed_on: list[int] = []
    remove = paths.AppFolders.remove_task_folder

    def recording_remove(self: paths.AppFolders, task_dir: Path) -> None:
        removed_on.append(threading.get_ident())
        remove(self, task_dir)

    monkeypatch.setattr(paths.AppFolders, "remove_task_folder", recording_remove)

    async def run() -> int:
        receipt = await steps.prepare_startup()
        resources = await steps.prepare_task(TASK_ID, receipt.configuration)
        assert await steps.confirm_exit(resources) is True
        await steps.cleanup_task(resources)
        return threading.get_ident()

    try:
        loop_thread = asyncio.run(run())
    finally:
        owner.close()
    assert len(removed_on) == 1
    assert removed_on[0] != loop_thread
    assert not folder.exists()


def test_a_refused_launch_removes_its_task_folder(tmp_path, pin) -> None:
    """Same size, different SHA-256: startup passes, the launcher refuses."""
    owner = preparation(tmp_path, (pin[0], "0" * 64))
    steps = owner.preparation()

    async def run():
        receipt = await steps.prepare_startup()
        with pytest.raises(LaunchError, match="^LAUNCH_EXECUTABLE_MISMATCH$"):
            await steps.prepare_task(TASK_ID, receipt.configuration)

    try:
        asyncio.run(run())
    finally:
        owner.close()
    assert not (tmp_path / "app" / "tasks" / TASK_ID).exists()


def test_account_app_server_runs_in_its_own_folder_removed_after_exit(tmp_path, pin) -> None:
    owner = preparation(tmp_path, pin)
    access = owner.account_access()
    tasks = tmp_path / "app" / "tasks"

    async def run() -> None:
        # Before startup there is no launcher: unavailable, and no folder is left.
        with pytest.raises(AccountUnavailableError):
            await access.launch()
        assert not list(tasks.glob("account-*"))
        await owner.preparation().prepare_startup()
        process = await access.launch()
        assert isinstance(process.pipes, CodexPipes)
        [folder] = tasks.glob("account-*")
        assert await process.close() is True
        assert not folder.exists()

    try:
        asyncio.run(run())
    finally:
        owner.close()


def test_a_refused_account_launch_removes_its_folder(tmp_path, pin) -> None:
    owner = preparation(tmp_path, (pin[0], "0" * 64))

    async def run() -> None:
        await owner.preparation().prepare_startup()
        with pytest.raises(AccountError, match="^CODEX_ACCOUNT_FAILED$") as refused:
            await owner.account_access().launch()
        # The launcher refused this executable; a later launch may still pass.
        assert type(refused.value) is AccountError

    try:
        asyncio.run(run())
    finally:
        owner.close()
    assert not list((tmp_path / "app" / "tasks").glob("account-*"))


def test_logout_cleanup_calls_c_on_the_app_codex_home_off_the_loop(
    tmp_path, pin, monkeypatch
) -> None:
    calls: list[tuple[Path, int]] = []
    failure: list[CodexConfigError] = []

    def clear(codex_home: Path) -> None:
        calls.append((codex_home, threading.get_ident()))
        if failure:
            raise failure[0]

    monkeypatch.setattr(codex_preparation, "clear_codex_credentials", clear)
    owner = preparation(tmp_path, pin, claim=False)
    access = owner.account_access()

    async def run() -> tuple[bool, bool, int]:
        cleared = await access.clear_credentials()
        failure.append(CodexConfigError("CODEX_CREDENTIAL_DELETE_FAILED"))
        return cleared, await access.clear_credentials(), threading.get_ident()

    # Without the claimed folders there is no CODEX_HOME to clean.
    assert asyncio.run(access.clear_credentials()) is False
    assert calls == []
    owner.claim()
    try:
        cleared, failed, loop_thread = asyncio.run(run())
    finally:
        owner.close()
    assert (cleared, failed) == (True, False)
    home = tmp_path / "app" / "codex_home"
    assert [path for path, _ in calls] == [home, home]
    assert all(thread != loop_thread for _, thread in calls)


@pytest.mark.parametrize(
    ("executable", "size", "code"),
    [
        (None, None, "CODEX_EXECUTABLE_MISSING"),  # nothing installed in the app folder
        ("missing", None, "CODEX_EXECUTABLE_MISSING"),
        (PYTHON, 1, "CODEX_EXECUTABLE_MISMATCH"),
    ],
    ids=["default-not-installed", "given-path-missing", "size-differs"],
)
def test_startup_refuses_an_executable_that_is_not_the_pinned_build(
    tmp_path, pin, executable, size, code
) -> None:
    path = tmp_path / "codex.exe" if executable == "missing" else executable
    owner = preparation(tmp_path, (size or pin[0], pin[1]), executable=path)
    try:
        with pytest.raises(CodexPreparationError, match=f"^{code}$") as raised:
            asyncio.run(owner.preparation().prepare_startup())
        # A102: the app keeps running on its DB, so the lock stays held.
        assert_lock_held(tmp_path / "app")
    finally:
        owner.close()
    assert str(tmp_path) not in str(raised.value)
    (tmp_path / "again").mkdir()
    again = preparation(tmp_path / "again", pin, root=tmp_path / "app")
    try:
        asyncio.run(again.preparation().prepare_startup())
    finally:
        again.close()


def test_verification_refuses_a_codex_home_changed_after_startup(tmp_path, pin) -> None:
    owner = preparation(tmp_path, pin)
    steps = owner.preparation()

    async def run():
        receipt = await steps.prepare_startup()
        (tmp_path / "app" / "codex_home" / "config.toml").write_text("model = 'other'\n")
        with pytest.raises(CodexConfigError, match="^CODEX_HOME_MISMATCH$"):
            await steps.verify_configuration(receipt.configuration)

    try:
        asyncio.run(run())
    finally:
        owner.close()


@pytest.mark.parametrize(
    ("pinned_size", "blocker"),
    [(None, "CODEX_L2_UNAVAILABLE"), (1, "CODEX_PREPARATION_FAILED")],
    ids=["linked", "wrong-executable"],
)
def test_product_start_shows_why_sessions_are_refused(tmp_path, pin, pinned_size, blocker) -> None:
    """With the local codex linked, only the missing L2 refuses sessions; a
    wrong executable is a preparation failure instead. The app starts either way."""
    owner = preparation(tmp_path, (pinned_size or pin[0], pin[1]))

    async def run():
        bindings = await bootstrap_product(
            str(tmp_path / "product.db"), preparation=owner.preparation()
        )
        try:
            assert bindings.session_blocker is not None
            assert bindings.create_session is not None
            seen = bindings.session_blocker()
            with pytest.raises(SessionRuntimeError, match=f"^{blocker}$"):
                await bindings.create_session(SessionOptions())
            return seen
        finally:
            assert bindings.close is not None
            await bindings.close()

    try:
        assert asyncio.run(run()) == blocker
    finally:
        owner.close()
    assert list((tmp_path / "app" / "tasks").iterdir()) == []


def test_receipt_has_the_shape_session_runtime_accepts(tmp_path, pin) -> None:
    owner = preparation(tmp_path, pin)
    try:
        receipt = asyncio.run(owner.preparation().prepare_startup())
    finally:
        owner.close()
    assert valid_configuration(receipt.configuration)


def no_bridge(*args: object, **kwargs: object) -> None:
    raise AssertionError("the backend must not start")


def test_main_does_not_start_while_another_instance_holds_the_lock(
    monkeypatch, capsys, tmp_path
) -> None:
    """A102: no DB, backend or window without the single-instance lock."""
    root = tmp_path / "app"
    db = root / "data" / "guard.sqlite3"
    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(entry, "BackendBridge", no_bridge)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    first = prepare_app_folders(root)
    try:
        assert entry.main() == 1
    finally:
        first.close()
    error = capsys.readouterr().err
    assert "이미 실행 중이라 시작하지 않는다" in error
    assert str(tmp_path) not in error
    assert not db.exists()


class _ProcessEndedError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@pytest.mark.parametrize("confirmed", [True, False])
def test_main_keeps_the_lock_until_the_process_ends_when_cleanup_is_unconfirmed(
    monkeypatch, capsys, tmp_path, confirmed
) -> None:
    """A110 (B6.17 8.1, #37): with cleanup unconfirmed the process ends at once
    while it still holds the lock, so a second instance cannot open the same DB
    meanwhile. With cleanup confirmed main releases the lock and returns."""
    root = tmp_path / "app"
    claimed: list[CodexProcessPreparation] = []
    claim = CodexProcessPreparation.claim

    def recording_claim(self: CodexProcessPreparation) -> None:
        claim(self)
        claimed.append(self)

    def ran(_args: object, codex: object, settings: object = None) -> tuple[int, bool]:
        assert_lock_held(root)
        return 0, confirmed

    def ended(code: int) -> None:
        assert_lock_held(root)  # never released before the process ends
        raise _ProcessEndedError(code)

    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(CodexProcessPreparation, "claim", recording_claim)
    monkeypatch.setattr(entry, "_run", ran)
    monkeypatch.setattr(entry.os, "_exit", ended)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    try:
        if confirmed:
            assert entry.main() == 0
            prepare_app_folders(root).close()  # the lock was released
        else:
            with pytest.raises(_ProcessEndedError) as stopped:
                entry.main()
            assert stopped.value.code == entry.EXIT_CLEANUP_UNCONFIRMED
            assert "정리 완료를 확인하지 못한 채" in capsys.readouterr().err
    finally:
        for owner in claimed:
            owner.close()


def test_main_holds_the_lock_before_the_db_opens_and_releases_it_after(
    monkeypatch, tmp_path
) -> None:
    """A102: the lock is held when the backend is built, before its bootstrap
    opens the DB, and main releases it on the way out. A106: that bootstrap
    opens the product DB fixed in the claimed app folder (C2.17)."""
    root = tmp_path / "app"
    db = root / "data" / "guard.sqlite3"
    seen: list[bool] = []
    opened: list[str] = []
    accounts: list[object] = []

    class StoppedError(Exception):
        pass

    def bridge(bootstrap: Callable[[], object]) -> None:
        assert_lock_held(root)
        seen.append(db.exists())
        bootstrap()
        raise StoppedError

    def recording_bootstrap(db_path: str, **kwargs: object) -> None:
        opened.append(db_path)
        accounts.append(kwargs.get("account"))

    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(entry, "BackendBridge", bridge)
    monkeypatch.setattr(session_runtime, "bootstrap_product", recording_bootstrap)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    quit_on_close = app.quitOnLastWindowClosed()
    try:
        with pytest.raises(StoppedError):
            entry.main()
    finally:
        app.setQuitOnLastWindowClosed(quit_on_close)
    assert seen == [False]
    assert opened == [str(db)]
    # A52: the login screen reaches C's launcher through the same claimed owner.
    assert len(accounts) == 1
    assert isinstance(accounts[0], AccountAccess)
    prepare_app_folders(root).close()


def test_db_file_is_the_product_db_in_the_claimed_folder(tmp_path, pin) -> None:
    """A106 (C2.17, REC-01): no DB path before the lock, then the fixed one."""
    owner = preparation(tmp_path, pin, claim=False)
    with pytest.raises(CodexPreparationError, match="^CODEX_FOLDERS_UNCLAIMED$"):
        _ = owner.db_file
    owner.claim()
    try:
        assert owner.db_file == tmp_path / "app" / "data" / "guard.sqlite3"
        assert owner.db_file.parent.is_dir()
    finally:
        owner.close()
    with pytest.raises(CodexPreparationError, match="^CODEX_FOLDERS_UNCLAIMED$"):
        _ = owner.db_file


def test_db_file_refuses_a_db_with_a_second_hard_link(tmp_path, pin) -> None:
    """A106: the path comes from C's `db_path()` (C2.19), so a DB file that is
    also reachable from elsewhere is refused instead of opened."""
    owner = preparation(tmp_path, pin)
    try:
        db = tmp_path / "app" / "data" / "guard.sqlite3"
        db.write_bytes(b"SQLite format 3\x00")
        os.link(db, tmp_path / "elsewhere.sqlite3")
        with pytest.raises(AppPathError, match="^APP_DB_INVALID$"):
            _ = owner.db_file
    finally:
        owner.close()


def test_main_does_not_start_when_the_db_file_is_refused(monkeypatch, capsys, tmp_path) -> None:
    """A106: a refused product DB stops the start before the backend or the
    window, with fixed text and no path, and the lock is released."""
    root = tmp_path / "app"
    prepared = prepare_app_folders(root)
    db = prepared.paths.db_file
    prepared.close()
    db.write_bytes(b"SQLite format 3\x00")
    os.link(db, tmp_path / "elsewhere.sqlite3")
    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(entry, "_run", lambda *_args: pytest.fail("the window must not open"))
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    assert entry.main() == 1
    error = capsys.readouterr().err
    assert "제품 DB 파일이 링크이거나 권한이 달라 앱을 시작하지 않음(APP_DB_INVALID)" in error
    assert str(tmp_path) not in error
    prepare_app_folders(root).close()  # the lock was released


def _junction(target: Path, link: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.parametrize("kind", ["product", "other", "case", "junction", "hardlink"])
def test_demo_refuses_a_db_in_the_product_app_folder(monkeypatch, capsys, tmp_path, kind) -> None:
    """A106 (C2.17, REC-01): the demo does not take the app lock, so it must not
    open the product DB, by any name, or anything else in the app folder (I9:
    no path in the message)."""
    root = tmp_path / "app"
    prepare_app_folders(root).close()
    product = root / "data" / "guard.sqlite3"
    product.write_bytes(b"")
    target = {
        "product": product,
        "other": root / "demo.db",
        "case": Path(str(product).upper()),
        # A new file through a junction: only resolving the path finds the folder.
        "junction": tmp_path / "alias" / "demo.db",
        "hardlink": tmp_path / "hardlink.db",
    }[kind]
    if kind == "junction":
        _junction(root / "data", tmp_path / "alias")
    if kind == "hardlink":
        os.link(product, target)
    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(entry, "BackendBridge", no_bridge)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "guard-app",
            "--demo",
            "--store",
            "sqlite",
            "--db-path",
            str(target),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        entry.main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "시연 DB는 제품 앱 폴더 밖이라고 확인된 경로만" in output.err
    assert str(tmp_path) not in output.err + output.out
    assert product.read_bytes() == b""


def test_demo_db_outside_the_app_folder_is_allowed(monkeypatch, tmp_path) -> None:
    """A106: a separate demo file stays usable; an unknown app folder refuses."""
    root = tmp_path / "app"
    prepare_app_folders(root).close()
    (root / "data" / "guard.sqlite3").write_bytes(b"")
    monkeypatch.setattr(paths, "app_root", lambda: root)
    assert entry._outside_app_folder(str(tmp_path / "demo.db"))
    assert entry._outside_app_folder(str(tmp_path / "app-demo" / "demo.db"))

    def unavailable() -> Path:
        raise AppPathError("APP_ROOT_UNAVAILABLE")

    monkeypatch.setattr(paths, "app_root", unavailable)
    assert not entry._outside_app_folder(str(tmp_path / "demo.db"))
