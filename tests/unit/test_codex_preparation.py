"""A101: SessionPreparation over C2.12 launcher, C2.13 folders and C6.13 CODEX_HOME.

Like C's launcher tests, this Python's python.exe stands in for codex.exe with a
pinned copy of `codex.lock.json`. Real folders, DACLs, the app lock and a real
child process run; L2 (C6.14) is not connected, so sessions stay refused (I11,
T22/T43 A part). The real codex.exe is C's evidence, not exercised here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from guard_app.security.codex_home import (
    CODEX_CONFIG_DIR,
    HOME_FILES,
    LOCK_FILE,
    CodexConfigError,
)
from guard_app.security.launcher import LaunchError
from guard_app.services.bridge import SessionOptions
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


def preparation(tmp_path: Path, pin: tuple[int, str], **kwargs: object) -> CodexProcessPreparation:
    options: dict[str, object] = {"executable": PYTHON, "root": tmp_path / "app"}
    options.update(kwargs)
    return CodexProcessPreparation(
        source_dir=source_for(tmp_path, *pin),
        **options,  # type: ignore[arg-type]
    )


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
    try:
        asyncio.run(again.preparation().prepare_startup())
    finally:
        again.close()


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
    finally:
        owner.close()
    assert str(tmp_path) not in str(raised.value)
    # The failed start released the app lock.
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
