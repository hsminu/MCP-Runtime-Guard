"""A's SessionPreparation over C's app folders, CODEX_HOME and codex.exe launcher (A101).

The order is the one C2.13 lists for A: at app start `prepare_app_folders()`
(single-instance lock, stale Task folders removed) and `write_codex_home`; per
Task a new folder, then `CodexLauncher.launch`; `confirm_exit` is
`LaunchedProcess.close()`; the folder is removed only after that returned True.

codex.exe is the pinned build (`config/codex/codex.lock.json`). By default it is
the copy `tools/install_codex.py` puts in the app folder; a developer may point
at another copy. Startup checks that it exists with the pinned size, and the
launcher compares its size and SHA-256 on every launch.

L2 (C6.14) is not connected yet, so the startup receipt carries no L2 result
and every session is refused with CODEX_L2_UNAVAILABLE (10.4: no development
skip). Errors carry fixed codes only, never paths.

Folder operations run on the backend loop, not a thread: a cleanup that outlives
SessionRuntime's bound would be reported unconfirmed and block new Tasks.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from guard_app.security.codex_home import (
    CODEX_CONFIG_DIR,
    CodexPins,
    load_codex_pins,
    verify_codex_home,
    write_codex_home,
)
from guard_app.security.launcher import CodexLauncher, LaunchedProcess
from guard_app.security.paths import AppFolders, AppPathError, prepare_app_folders
from guard_app.services.session_preparation import (
    ConfigurationReceipt,
    SessionPreparation,
    StartupReceipt,
    TaskResources,
)


class CodexPreparationError(RuntimeError):
    """Fixed code only; SessionRuntime reports it as CODEX_PREPARATION_FAILED."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _TaskProcess:
    folder: Path
    process: LaunchedProcess
    exited: bool = False


def _check_executable(executable: Path, pins: CodexPins) -> None:
    """An early sign that the configured copy is the pinned build. The launcher
    still verifies size and SHA-256 with the file held open on every launch."""
    try:
        found = executable.is_absolute() and executable.is_file()
        size = executable.stat().st_size if found else None
    except OSError:
        size = None
    if size is None:
        raise CodexPreparationError("CODEX_EXECUTABLE_MISSING")
    if size != pins.executable_size:
        raise CodexPreparationError("CODEX_EXECUTABLE_MISMATCH")


def configuration_receipt(home_digest: str, pins: CodexPins) -> ConfigurationReceipt:
    """CODEX_HOME bundle digest, pinned codex.exe SHA-256 and model catalog SHA-256."""
    return ConfigurationReceipt(
        config_digest=home_digest,
        executable_digest=pins.executable_sha256,
        model_digest=dict(pins.home_sha256)["model_catalog.json"],
    )


class CodexProcessPreparation:
    """App-lifetime owner of the folders and launcher behind Task sessions.

    `executable` overrides the installed copy. `root` and `source_dir` are for
    tests. Call `close` after the backend has closed every session.
    """

    def __init__(
        self,
        *,
        executable: Path | None = None,
        root: Path | None = None,
        source_dir: Path = CODEX_CONFIG_DIR,
    ) -> None:
        self._executable = executable
        self._root = root
        self._source_dir = source_dir
        self._folders: AppFolders | None = None
        self._launcher: CodexLauncher | None = None
        self._home_digest = ""
        self._configuration: ConfigurationReceipt | None = None
        self._tasks: dict[str, _TaskProcess] = {}
        self._started = False

    def preparation(self) -> SessionPreparation:
        return SessionPreparation(
            self._prepare_startup,
            self._verify_configuration,
            self._prepare_task,
            self._confirm_exit,
            self._cleanup_task,
        )

    def close(self) -> None:
        """Release the single-instance lock. Safe to call more than once."""
        folders, self._folders = self._folders, None
        self._launcher = None
        if folders is not None:
            folders.close()

    async def _prepare_startup(self) -> StartupReceipt:
        if self._started:
            raise CodexPreparationError("CODEX_STARTUP_REPEATED")
        self._started = True
        pins = load_codex_pins(self._source_dir)
        folders = prepare_app_folders(self._root)
        prepared = False
        try:
            paths = folders.paths
            home_digest = write_codex_home(paths.codex_home, source_dir=self._source_dir)
            executable = self._executable or (
                paths.codex_install(pins.codex_version) / pins.executable_file
            )
            _check_executable(executable, pins)
            launcher = CodexLauncher(
                executable, paths.codex_home, home_digest, source_dir=self._source_dir
            )
            prepared = True
        finally:
            if not prepared:
                folders.close()
        self._folders, self._launcher = folders, launcher
        self._home_digest = home_digest
        self._configuration = configuration_receipt(home_digest, pins)
        # C6.14 is not connected: no L2 result, so SessionRuntime refuses sessions.
        return StartupReceipt(self._configuration, None, False)

    async def _verify_configuration(self, expected: ConfigurationReceipt) -> ConfigurationReceipt:
        folders, _ = self._ready()
        verify_codex_home(folders.paths.codex_home, self._home_digest, source_dir=self._source_dir)
        return configuration_receipt(self._home_digest, load_codex_pins(self._source_dir))

    async def _prepare_task(self, task_id: str, expected: ConfigurationReceipt) -> TaskResources:
        folders, launcher = self._ready()
        configuration = self._configuration
        if configuration is None:
            raise CodexPreparationError("CODEX_NOT_STARTED")
        folder = folders.create_task_folder(task_id)
        launched = False
        try:
            process = await launcher.launch(folder)
            launched = True
        finally:
            if not launched:
                # The launcher already ended its own process; the folder may stay
                # until the next app start clears it.
                with contextlib.suppress(AppPathError):
                    folders.remove_task_folder(folder)
        token = uuid4().hex
        self._tasks[token] = _TaskProcess(folder, process)
        return TaskResources(task_id, configuration, token, process)

    async def _confirm_exit(self, resources: TaskResources) -> bool:
        task = self._task(resources)
        task.exited = await task.process.close()
        return task.exited

    async def _cleanup_task(self, resources: TaskResources) -> None:
        task = self._task(resources)
        if not task.exited:
            raise CodexPreparationError("CODEX_EXIT_UNCONFIRMED")
        folders, _ = self._ready()
        folders.remove_task_folder(task.folder)
        del self._tasks[resources.owner_token]

    def _ready(self) -> tuple[AppFolders, CodexLauncher]:
        if self._folders is None or self._launcher is None:
            raise CodexPreparationError("CODEX_NOT_STARTED")
        return self._folders, self._launcher

    def _task(self, resources: TaskResources) -> _TaskProcess:
        task = self._tasks.get(resources.owner_token)
        if task is None or task.process is not resources.handle:
            raise CodexPreparationError("CODEX_TASK_UNKNOWN")
        return task
