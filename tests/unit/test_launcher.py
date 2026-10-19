"""security.launcher(C2.12) 단위 테스트. codex.exe 대신 이 Python의 python.exe로 실행한다.

실제 codex.exe 0.162.1 확인은 완료 기록의 근거 JSON에 있다.
"""

import asyncio
import ctypes
import hashlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from guard_app.security import launcher
from guard_app.security.codex_home import CODEX_CONFIG_DIR, HOME_FILES, LOCK_FILE, write_codex_home
from guard_app.security.launcher import (
    CODEX_ENV_ALLOWLIST,
    CODEX_STDERR_FILE,
    CodexLauncher,
    LaunchError,
    LaunchSpec,
    launch_process,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="launcher는 Windows 전용이다")

PYTHON = Path(sys.base_prefix) / "python.exe"
MARKER = "launcher-test-planted-marker"


@pytest.fixture(scope="module")
def pin():
    data = PYTHON.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _task(tmp_path, name="task"):
    task = tmp_path / name
    task.mkdir()
    return task


def _spec(task, pin, code, *args, env=None, sha256=None, size=None, executable=PYTHON):
    return LaunchSpec(
        executable=executable,
        executable_size=pin[0] if size is None else size,
        executable_sha256=pin[1] if sha256 is None else sha256,
        args=("-I", "-c", code, *args),
        env=(("SYSTEMROOT", os.environ["SYSTEMROOT"]),) if env is None else env,
        cwd=task,
        stderr_path=task / "stderr.log",
    )


async def _read_line(launched):
    buffer = b""
    while b"\n" not in buffer:
        chunk = await asyncio.wait_for(launched.read_bytes(4096), 10)
        assert chunk, "EOF before a line"
        buffer += chunk
    return buffer.split(b"\n", 1)[0].rstrip(b"\r")


def _alive(pid):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


ECHO_UNTIL_EOF = (
    "import sys\n"
    "line = sys.stdin.buffer.readline()\n"
    "sys.stdout.buffer.write(b'echo:' + line)\n"
    "sys.stdout.flush()\n"
    "sys.stdin.buffer.read()\n"
)


@pytest.mark.asyncio
async def test_pipes_round_trip_and_eof_ends_process(tmp_path, pin):
    launched = await launch_process(_spec(_task(tmp_path), pin, ECHO_UNTIL_EOF))
    await launched.write_bytes(b"hello\n")
    await launched.drain()
    assert await _read_line(launched) == b"echo:hello"
    assert await launched.close() is True
    assert launched.returncode == 0


@pytest.mark.asyncio
async def test_stderr_goes_only_to_the_isolated_file(tmp_path, pin, capfd):
    task = _task(tmp_path)
    code = f"import sys; sys.stderr.write('{MARKER}'); sys.stderr.flush()"
    launched = await launch_process(_spec(task, pin, code))
    assert await launched.close() is True
    assert MARKER in (task / "stderr.log").read_text(encoding="utf-8")
    captured = capfd.readouterr()
    assert MARKER not in captured.err
    assert MARKER not in captured.out


@pytest.mark.asyncio
async def test_arguments_reach_the_child_literally_without_a_shell(tmp_path, pin):
    args = ("a b", 'q"uote', "amp&echo pwned", "%PATH%", "^caret", "trailing\\", "", "|pipe")
    code = "import json, sys; print(json.dumps(sys.argv[1:]), flush=True); sys.stdin.read()"
    launched = await launch_process(_spec(_task(tmp_path), pin, code, *args))
    assert json.loads(await _read_line(launched)) == list(args)
    assert await launched.close() is True


@pytest.mark.asyncio
async def test_child_sees_only_the_given_environment(tmp_path, pin, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", MARKER)
    monkeypatch.setenv("CODEX_SQLITE_HOME", MARKER)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    home = _task(tmp_path, "home")
    task = _task(tmp_path)
    env = CodexLauncher(PYTHON, home, "0" * 64).spec(task).env
    names = {name for name, _ in env}
    assert names <= {*CODEX_ENV_ALLOWLIST, "CODEX_HOME"}
    assert dict(env)["CODEX_HOME"] == str(home)
    code = "import json, os; print(json.dumps(dict(os.environ)), flush=True); input()"
    launched = await launch_process(_spec(task, pin, code, env=env))
    seen = json.loads(await _read_line(launched))
    assert {name.upper() for name in seen} <= {*CODEX_ENV_ALLOWLIST, "CODEX_HOME"}
    assert MARKER not in json.dumps(seen)
    assert await launched.close() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["sha256", "size"])
async def test_mismatched_executable_is_never_started(tmp_path, pin, field):
    task = _task(tmp_path)
    marker = tmp_path / "started"
    code = f"open({str(marker)!r}, 'w').close()"
    changed = {"sha256": "0" * 64} if field == "sha256" else {"size": pin[0] + 1}
    with pytest.raises(LaunchError) as raised:
        await launch_process(_spec(task, pin, code, **changed))
    assert str(raised.value) == "LAUNCH_EXECUTABLE_MISMATCH"
    await asyncio.sleep(0.3)
    assert not marker.exists()
    assert not (task / "stderr.log").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["relative", "cmd", "missing", "directory"])
async def test_unusable_executable_is_rejected(tmp_path, pin, kind):
    task = _task(tmp_path)
    if kind == "relative":
        executable = Path("python.exe")
    elif kind == "cmd":
        executable = tmp_path / "run.cmd"
        executable.write_text("@echo off\n", encoding="ascii")
    elif kind == "missing":
        executable = tmp_path / "missing.exe"
    else:
        executable = tmp_path / "folder.exe"
        executable.mkdir()
    with pytest.raises(LaunchError) as raised:
        await launch_process(_spec(task, pin, "pass", executable=executable))
    assert str(raised.value) == "LAUNCH_EXECUTABLE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env",
    [
        (("SYSTEMROOT", "a"), ("SystemRoot", "b")),
        (("BAD=NAME", "x"),),
        (("NAME", "nul\0value"),),
        (("ONLY_NAME",),),
    ],
)
async def test_malformed_environment_is_rejected(tmp_path, pin, env):
    with pytest.raises(LaunchError) as raised:
        await launch_process(_spec(_task(tmp_path), pin, "pass", env=env))
    assert str(raised.value) == "LAUNCH_SPEC_INVALID"


@pytest.mark.asyncio
async def test_existing_stderr_file_is_not_reused(tmp_path, pin):
    task = _task(tmp_path)
    (task / "stderr.log").write_bytes(b"old")
    with pytest.raises(LaunchError) as raised:
        await launch_process(_spec(task, pin, "pass"))
    assert str(raised.value) == "LAUNCH_STDERR_INVALID"
    assert (task / "stderr.log").read_bytes() == b"old"


def test_verified_executable_cannot_be_changed_until_released(tmp_path, pin):
    folder = tmp_path / "install"
    folder.mkdir()
    image_path = folder / "tool.exe"
    shutil.copyfile(PYTHON, image_path)
    other = tmp_path / "other.exe"
    other.write_bytes(b"replacement")
    image = launcher._open_verified(image_path, pin[0], pin[1])
    try:
        with pytest.raises(PermissionError):
            image_path.open("r+b")
        with pytest.raises(PermissionError):
            os.replace(other, image_path)
        with pytest.raises(PermissionError):
            image_path.unlink()
        with pytest.raises(PermissionError):
            folder.rename(tmp_path / "moved")
    finally:
        image.close()
    folder.rename(tmp_path / "moved")


def test_mismatch_releases_the_executable(tmp_path, pin):
    image_path = tmp_path / "tool.exe"
    shutil.copyfile(PYTHON, image_path)
    with pytest.raises(LaunchError):
        launcher._open_verified(image_path, pin[0], "0" * 64)
    image_path.open("r+b").close()


@pytest.mark.asyncio
async def test_close_ends_grandchildren_through_the_job(tmp_path, pin):
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    launched = await launch_process(_spec(_task(tmp_path), pin, code))
    grandchild = int(await _read_line(launched))
    assert _alive(grandchild)
    assert await launched.close(grace_s=0.2) is True
    assert launched.returncode == launcher._TERMINATED_EXIT_CODE
    assert not _alive(grandchild)


@pytest.mark.asyncio
async def test_closing_the_job_handle_ends_the_process(tmp_path, pin):
    # 앱이 비정상 종료해 Job 핸들이 닫히는 경우를 Job 핸들만 닫아 흉내 낸다.
    launched = await launch_process(_spec(_task(tmp_path), pin, "import time; time.sleep(60)"))
    launched._job.close()
    for _ in range(100):
        if launched.returncode is not None:
            break
        await asyncio.sleep(0.05)
    assert launched.returncode is not None
    # Job을 잃은 뒤에는 남은 프로세스 수를 볼 수 없어 종료를 확인했다고 하지 않는다.
    assert await launched.close() is False


@pytest.mark.asyncio
async def test_close_is_shared_and_pipes_refuse_use_after_close(tmp_path, pin):
    launched = await launch_process(_spec(_task(tmp_path), pin, ECHO_UNTIL_EOF))
    first, second = await asyncio.gather(launched.close(), launched.close())
    assert first is second is True
    assert await launched.close() is True
    with pytest.raises(LaunchError) as raised:
        await launched.write_bytes(b"late\n")
    assert str(raised.value) == "LAUNCH_PIPE_CLOSED"
    with pytest.raises(LaunchError):
        await launched.drain()


@pytest.mark.asyncio
async def test_read_rejects_non_positive_limit(tmp_path, pin):
    launched = await launch_process(_spec(_task(tmp_path), pin, ECHO_UNTIL_EOF))
    with pytest.raises(LaunchError):
        await launched.read_bytes(0)
    assert await launched.close() is True


@pytest.mark.asyncio
async def test_cancel_during_verification_closes_the_late_handle(tmp_path, pin, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class LateImage:
        closed = False

        def close(self):
            LateImage.closed = True

    def slow_open(path, size, sha256):
        entered.set()
        release.wait(10)
        return LateImage()

    monkeypatch.setattr(launcher, "_open_verified", slow_open)
    task = _task(tmp_path)
    launching = asyncio.create_task(launch_process(_spec(task, pin, "pass")))
    await asyncio.to_thread(entered.wait, 10)
    launching.cancel()
    with pytest.raises(asyncio.CancelledError):
        await launching
    release.set()
    for _ in range(100):
        if LateImage.closed:
            break
        await asyncio.sleep(0.02)
    assert LateImage.closed
    assert not (task / "stderr.log").exists()


def _source_for(tmp_path, pin):
    """python.exe를 codex.exe 자리에 고정한 고정값 사본. 설정·모델 정보 파일은 저장소 그대로다."""
    source = tmp_path / "source"
    source.mkdir()
    for name in HOME_FILES:
        shutil.copyfile(CODEX_CONFIG_DIR / name, source / name)
    lock = json.loads((CODEX_CONFIG_DIR / LOCK_FILE).read_text(encoding="utf-8"))
    lock["executable"]["size"], lock["executable"]["sha256"] = pin
    (source / LOCK_FILE).write_text(json.dumps(lock), encoding="utf-8")
    return source


def test_codex_spec_passes_only_the_task_sqlite_home(tmp_path):
    home, task = _task(tmp_path, "home"), _task(tmp_path)
    spec = CodexLauncher(PYTHON, home, "0" * 64).spec(task)
    assert spec.args == ("app-server", "-c", f"sqlite_home='{task}'")
    assert spec.cwd == task
    assert spec.stderr_path == task / CODEX_STDERR_FILE
    assert spec.executable == PYTHON


@pytest.mark.parametrize("kind", ["quote", "relative", "missing"])
def test_codex_spec_rejects_unusable_task_folder(tmp_path, kind):
    home = _task(tmp_path, "home")
    if kind == "quote":
        task = _task(tmp_path, "it's")
    elif kind == "relative":
        task = Path("task")
    else:
        task = tmp_path / "missing"
    with pytest.raises(LaunchError) as raised:
        CodexLauncher(PYTHON, home, "0" * 64).spec(task)
    assert str(raised.value) == "LAUNCH_TASK_DIR_INVALID"


def test_codex_spec_rejects_junction_task_folder(tmp_path):
    import _winapi

    target = _task(tmp_path, "target")
    link = tmp_path / "link"
    _winapi.CreateJunction(str(target), str(link))
    with pytest.raises(LaunchError) as raised:
        CodexLauncher(PYTHON, _task(tmp_path, "home"), "0" * 64).spec(link)
    assert str(raised.value) == "LAUNCH_TASK_DIR_INVALID"


def test_codex_launcher_rejects_invalid_pins(tmp_path):
    with pytest.raises(LaunchError) as raised:
        CodexLauncher(PYTHON, tmp_path, "0" * 64, source_dir=tmp_path)
    assert str(raised.value) == "LAUNCH_PINS_INVALID"


@pytest.mark.asyncio
async def test_codex_launch_refuses_changed_codex_home(tmp_path):
    home, task = _task(tmp_path, "home"), _task(tmp_path)
    write_codex_home(home)
    codex = CodexLauncher(PYTHON, home, "0" * 64)
    with pytest.raises(LaunchError) as raised:
        await codex.launch(task)
    assert str(raised.value) == "LAUNCH_CODEX_HOME_MISMATCH"
    assert codex.launches == 0


@pytest.mark.asyncio
async def test_codex_launch_refuses_executable_other_than_pinned_codex(tmp_path):
    home, task = _task(tmp_path, "home"), _task(tmp_path)
    codex = CodexLauncher(PYTHON, home, write_codex_home(home))
    with pytest.raises(LaunchError) as raised:
        await codex.launch(task)
    assert str(raised.value) == "LAUNCH_EXECUTABLE_MISMATCH"
    assert codex.launches == 0
    assert not (task / CODEX_STDERR_FILE).exists()


@pytest.mark.asyncio
async def test_codex_launch_runs_pinned_executable_and_counts(tmp_path, pin, capfd):
    source = _source_for(tmp_path, pin)
    home, task = _task(tmp_path, "home"), _task(tmp_path)
    codex = CodexLauncher(
        PYTHON, home, write_codex_home(home, source_dir=source), source_dir=source
    )
    launched = await codex.launch(task)
    assert codex.launches == 1
    assert await launched.close() is True
    # python.exe는 `app-server`를 스크립트로 찾다 실패한다. 그 오류는 격리 파일에만 남는다.
    assert launched.returncode == 2
    assert "app-server" in (task / CODEX_STDERR_FILE).read_text(encoding="utf-8", errors="replace")
    assert "app-server" not in capfd.readouterr().err
