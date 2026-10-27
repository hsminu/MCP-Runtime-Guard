"""외부 프로세스 실행의 단일 지점(C2.12, I9·I11·T19·T22·T43). 오류에 경로·인자·원문을 싣지 않는다.

`launch_process`는 절대 경로의 `.exe`만 인자 리스트와 명시한 환경으로 띄운다. shell을 쓰지
않고 `.bat`·`.cmd`는 받지 않는다(Windows가 cmd.exe로 인자를 다시 해석한다). 실행 파일은
쓰기·이름 변경·삭제를 막는 핸들로 연 채 크기·SHA-256을 대조하고, 그 핸들을 프로세스 생성까지
쥔다. 프로세스는 멈춘 채 만들어 Job Object에 넣고, 실제로 올라간 이미지가 검사한 그 파일인지
대조한 뒤에만 재개한다(C2.18). Job 핸들이 닫히면(앱 종료 포함) 남은 자식까지 함께 끝나고,
`close`는 Job에 남은 프로세스가 없을 때만 종료를 확인한다. stderr는 지정한 격리 파일로만
보낸다. 해시 없는 실행은 받지 않는다.

`CodexLauncher`는 codex.exe 전용 입구다. 실행마다 `codex.lock.json`의 고정값과 CODEX_HOME 묶음
해시를 대조하고, 허용 목록 환경과 `app-server -c sqlite_home='<Task 폴더>'`만 쓴다. Task 폴더
생성·권한·삭제는 C2.13, L2 시작 시험은 C6.14, stdio MCP 서버 등록 값의 검증과 그 실행 파일
해시의 출처는 C2.6이다. Windows 전용이다.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from guard_app.security.codex_home import (
    CODEX_CONFIG_DIR,
    CodexConfigError,
    load_codex_pins,
    verify_codex_home,
)

# 10-18 결정(개발문서 10.4절)의 허용 목록. CODEX_HOME은 부모 환경이 아니라 앱 경로로 채운다.
CODEX_ENV_ALLOWLIST = (
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
)
CODEX_STDERR_FILE = "codex-stderr.log"
# L2 시작 시험(C6.14) 전용 가짜 모델 제공자. 제품 세션 입구는 이 값을 쓰지 않는다.
L2_PROVIDER = "guard_l2"
L2_TOKEN_ENV = "GUARD_L2_TOKEN"  # noqa: S105 - 토큰이 아니라 토큰을 담을 환경변수 이름
_L2_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HASH_CHUNK = 1 << 20
_STREAM_LIMIT = 1 << 16
_POLL_S = 0.05
_REAP_S = 2.0
_TERMINATED_EXIT_CODE = 1

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
_JOB_BASIC_ACCOUNTING = 1
_JOB_EXTENDED_LIMIT = 9
_JOB_KILL_ON_CLOSE = 0x00002000
_JOB_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_NAME_NATIVE = 1
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_ALL = 0x00000007
_FILE_NAME_OPENED_NT = 0x8 | 0x2  # FILE_NAME_OPENED | VOLUME_NAME_NT
_FILE_ID_INFO = 18
_NT_PATH_PREFIX = "\\\\?\\GLOBALROOT"
_MAX_PATH_CHARS = 32768

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    class _FileIdInfo(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_uint64),
            ("FileId", ctypes.c_ubyte * 16),
        ]

    def _api(name: str, restype: object, *argtypes: object) -> Any:
        function = getattr(_kernel32, name)
        function.restype = restype
        function.argtypes = argtypes
        return function

    _HANDLE, _DWORD, _BOOL = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    _CreateFileW = _api(
        "CreateFileW",
        _HANDLE,
        wintypes.LPCWSTR,
        _DWORD,
        _DWORD,
        wintypes.LPVOID,
        _DWORD,
        _DWORD,
        _HANDLE,
    )
    _CloseHandle = _api("CloseHandle", _BOOL, _HANDLE)
    _CreateJobObjectW = _api("CreateJobObjectW", _HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
    _SetInformationJobObject = _api(
        "SetInformationJobObject", _BOOL, _HANDLE, ctypes.c_int, wintypes.LPVOID, _DWORD
    )
    _QueryInformationJobObject = _api(
        "QueryInformationJobObject",
        _BOOL,
        _HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        _DWORD,
        wintypes.LPDWORD,
    )
    _AssignProcessToJobObject = _api("AssignProcessToJobObject", _BOOL, _HANDLE, _HANDLE)
    _TerminateJobObject = _api("TerminateJobObject", _BOOL, _HANDLE, wintypes.UINT)
    _OpenProcess = _api("OpenProcess", _HANDLE, _DWORD, _BOOL, _DWORD)
    _OpenThread = _api("OpenThread", _HANDLE, _DWORD, _BOOL, _DWORD)
    _ResumeThread = _api("ResumeThread", _DWORD, _HANDLE)
    _CreateToolhelp32Snapshot = _api("CreateToolhelp32Snapshot", _HANDLE, _DWORD, _DWORD)
    _Thread32First = _api("Thread32First", _BOOL, _HANDLE, ctypes.POINTER(_ThreadEntry))
    _Thread32Next = _api("Thread32Next", _BOOL, _HANDLE, ctypes.POINTER(_ThreadEntry))
    _QueryFullProcessImageNameW = _api(
        "QueryFullProcessImageNameW",
        _BOOL,
        _HANDLE,
        _DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(_DWORD),
    )
    _GetFinalPathNameByHandleW = _api(
        "GetFinalPathNameByHandleW", _DWORD, _HANDLE, wintypes.LPWSTR, _DWORD, _DWORD
    )
    _GetFileInformationByHandleEx = _api(
        "GetFileInformationByHandleEx", _BOOL, _HANDLE, ctypes.c_int, wintypes.LPVOID, _DWORD
    )


class LaunchError(RuntimeError):
    """경로·인자·예외 원문을 공개하지 않는 고정 오류 코드."""


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """실행 하나의 전체 명세. 환경은 이 값만 쓰고 부모 환경을 물려주지 않는다."""

    executable: Path
    executable_size: int
    executable_sha256: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    cwd: Path
    stderr_path: Path


class LaunchedProcess:
    """실행된 프로세스 하나. A의 `CodexPipes` 형식(read_bytes·write_bytes·drain)을 그대로 제공한다.

    파이프와 프로세스 수명은 이 객체가 소유한다. 쓰는 쪽은 끝낼 때 `close`를 부르고, 그 결과가
    True일 때만 Task 폴더를 지운다.
    """

    def __init__(
        self,
        transport: asyncio.SubprocessTransport,
        process: asyncio.subprocess.Process,
        job: _Job,
    ) -> None:
        self._transport = transport
        self._process = process
        self._job = job
        self._closing: asyncio.Future[bool] | None = None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def read_bytes(self, limit: int) -> bytes:
        """stdout에서 최대 `limit`바이트를 읽는다. 끝이면 빈 bytes다."""
        stdout = self._process.stdout
        if type(limit) is not int or limit < 1 or stdout is None:
            raise LaunchError("LAUNCH_READ_INVALID")
        return await stdout.read(limit)

    async def write_bytes(self, frame: bytes) -> None:
        """stdin 버퍼에 전부 넣거나 실패한다. 닫힌 파이프에 조용히 버리지 않는다."""
        stdin = self._writable()
        if type(frame) is not bytes:
            raise LaunchError("LAUNCH_WRITE_INVALID")
        stdin.write(frame)

    async def drain(self) -> None:
        await self._writable().drain()

    async def close(self, *, grace_s: float = 5.0, kill_s: float = 5.0) -> bool:
        """stdin을 닫고 기다린 뒤 남은 프로세스를 Job째 끝낸다. Job이 빈 것을 본 때만 True다.

        여러 번 불러도 정리는 한 번이고 같은 결과를 돌려준다. 부른 쪽이 취소되어도 정리는 끝까지
        한다.
        """
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._shutdown(grace_s, kill_s))
        return await asyncio.shield(self._closing)

    def _writable(self) -> asyncio.StreamWriter:
        stdin = self._process.stdin
        if self._closing is not None or stdin is None or stdin.is_closing():
            raise LaunchError("LAUNCH_PIPE_CLOSED")
        return stdin

    async def _shutdown(self, grace_s: float, kill_s: float) -> bool:
        try:
            stdin = self._process.stdin
            if stdin is not None:
                stdin.close()
            if not await self._exited(grace_s):
                self._job.terminate()
                await self._exited(kill_s)
            return self._process.returncode is not None and self._job.active() == 0
        finally:
            self._transport.close()
            self._job.close()

    async def _exited(self, timeout_s: float) -> bool:
        """주 프로세스가 끝나고 Job이 빌 때까지 기다린다. Job을 볼 수 없으면 기다리지 않는다."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            active = self._job.active()
            if active is None:
                return False
            if active == 0 and self._process.returncode is not None:
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(_POLL_S)


class CodexLauncher:
    """codex.exe 실행 입구(C2.12). 실행마다 실행 파일과 CODEX_HOME을 고정값과 대조한다.

    `home_digest`는 앱 시작 때 `write_codex_home`이 돌려준 값이다. `launches`는 실제로 만든 프로세스
    수다(C1.9 관측). Task 폴더는 C2.13이 만든 빈 폴더를 받는다.
    """

    def __init__(
        self,
        executable: Path,
        codex_home: Path,
        home_digest: str,
        *,
        source_dir: Path = CODEX_CONFIG_DIR,
    ) -> None:
        try:
            pins = load_codex_pins(source_dir)
        except CodexConfigError:
            raise LaunchError("LAUNCH_PINS_INVALID") from None
        self._executable = executable
        self._size = pins.executable_size
        self._sha256 = pins.executable_sha256
        self._codex_home = codex_home
        self._home_digest = home_digest
        self._source_dir = source_dir
        self.launches = 0
        self.l2_launches = 0

    def spec(self, task_dir: Path) -> LaunchSpec:
        """Task 하나의 실행 명세. Task별 값은 `sqlite_home` 하나다(L2 `config/read` 대조 기준)."""
        task = _checked_dir(task_dir, "LAUNCH_TASK_DIR_INVALID")
        text = str(task)
        # TOML 리터럴 문자열로 넘긴다. 큰따옴표 문자열이면 `\U`·`\t` 같은 경로 조각이 이스케이프다.
        if "'" in text or any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
            raise LaunchError("LAUNCH_TASK_DIR_INVALID")
        home = _checked_dir(self._codex_home, "LAUNCH_CODEX_HOME_INVALID")
        env = tuple((name, os.environ[name]) for name in CODEX_ENV_ALLOWLIST if name in os.environ)
        return LaunchSpec(
            executable=self._executable,
            executable_size=self._size,
            executable_sha256=self._sha256,
            args=("app-server", "-c", f"sqlite_home='{text}'"),
            env=(*env, ("CODEX_HOME", str(home))),
            cwd=task,
            stderr_path=task / CODEX_STDERR_FILE,
        )

    async def launch(self, task_dir: Path) -> LaunchedProcess:
        spec = self.spec(task_dir)
        try:
            verify_codex_home(self._codex_home, self._home_digest, source_dir=self._source_dir)
        except CodexConfigError:
            raise LaunchError("LAUNCH_CODEX_HOME_MISMATCH") from None
        launched = await launch_process(spec)
        self.launches += 1
        return launched

    @property
    def home_digest(self) -> str:
        return self._home_digest

    @property
    def executable_sha256(self) -> str:
        return self._sha256

    @property
    def source_dir(self) -> Path:
        return self._source_dir

    def l2_spec(self, task_dir: Path, port: int, token: str) -> LaunchSpec:
        """L2 시작 시험의 실행 명세. 제품 명세에 고정된 가짜 모델 덮어쓰기와 토큰 하나만 더한다.

        실행 파일·CODEX_HOME·환경 허용 목록·`sqlite_home`은 제품 세션과 같다. 바뀌는 값은
        `127.0.0.1`의 포트와 이번 실행의 토큰뿐이다. `l2_overrides`가 L2의 `config/read`
        대조 기준이다.
        """
        if type(port) is not int or not 1 <= port <= 65535:
            raise LaunchError("LAUNCH_L2_INVALID")
        if type(token) is not str or not _L2_TOKEN.fullmatch(token):
            raise LaunchError("LAUNCH_L2_INVALID")
        base = self.spec(task_dir)
        overrides: list[str] = []
        for key, value in l2_overrides(port).items():
            text = f'"{value}"' if isinstance(value, str) else str(value).lower()
            overrides += ["-c", f"{key}={text}"]
        return LaunchSpec(
            executable=base.executable,
            executable_size=base.executable_size,
            executable_sha256=base.executable_sha256,
            args=(*base.args, *overrides),
            env=(*base.env, (L2_TOKEN_ENV, token)),
            cwd=base.cwd,
            stderr_path=base.stderr_path,
        )

    async def launch_l2(self, task_dir: Path, port: int, token: str) -> LaunchedProcess:
        """L2 시작 시험용 실행. `launches`가 아니라 `l2_launches`로 센다."""
        spec = self.l2_spec(task_dir, port, token)
        try:
            verify_codex_home(self._codex_home, self._home_digest, source_dir=self._source_dir)
        except CodexConfigError:
            raise LaunchError("LAUNCH_CODEX_HOME_MISMATCH") from None
        launched = await launch_process(spec)
        self.l2_launches += 1
        return launched


def l2_overrides(port: int) -> dict[str, str | int | bool]:
    """L2가 더하는 `-c` 키와 값. 재시도를 끄고 `127.0.0.1`의 가짜 모델에만 붙는다."""
    prefix = f"model_providers.{L2_PROVIDER}"
    return {
        "model_provider": L2_PROVIDER,
        f"{prefix}.name": L2_PROVIDER,
        f"{prefix}.base_url": f"http://127.0.0.1:{port}/v1",
        f"{prefix}.wire_api": "responses",
        f"{prefix}.env_key": L2_TOKEN_ENV,
        f"{prefix}.request_max_retries": 0,
        f"{prefix}.stream_max_retries": 0,
        f"{prefix}.supports_websockets": False,
    }


async def launch_process(spec: LaunchSpec) -> LaunchedProcess:
    """명세를 검증하고 실행한다. 실패하면 만든 프로세스·Job을 정리하고 고정 코드만 올린다."""
    if sys.platform != "win32":
        raise LaunchError("LAUNCH_UNSUPPORTED")
    _check_spec(spec)
    try:
        stderr = spec.stderr_path.open("xb")
    except OSError:
        raise LaunchError("LAUNCH_STDERR_INVALID") from None
    image: BinaryIO | None = None
    job: _Job | None = None
    transport: asyncio.SubprocessTransport | None = None
    launched = False
    try:
        image = await _verified_image(spec)
        job = _Job()
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.subprocess_exec(
                lambda: asyncio.subprocess.SubprocessStreamProtocol(_STREAM_LIMIT, loop),
                str(spec.executable),
                *spec.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                env=dict(spec.env),
                cwd=str(spec.cwd),
                creationflags=_CREATE_SUSPENDED | _CREATE_NO_WINDOW,
            )
        except (OSError, ValueError, NotImplementedError):
            raise LaunchError("LAUNCH_FAILED") from None
        pid = transport.get_pid()
        job.adopt(pid)
        _check_image(pid, image)
        _resume_initial_thread(pid)
        process = asyncio.subprocess.Process(transport, protocol, loop)
        result = LaunchedProcess(transport, process, job)
        launched = True
        return result
    finally:
        if image is not None:
            image.close()
        stderr.close()
        if not launched:
            if transport is not None:
                transport.close()
            if job is not None:
                job.close()
            if transport is not None:
                await _reaped(transport)
            with contextlib.suppress(OSError):
                spec.stderr_path.unlink(missing_ok=True)


async def _reaped(transport: asyncio.SubprocessTransport) -> None:
    """실패 정리에서 끝낸 프로세스가 실제로 사라질 때까지 잠깐 기다린다.

    프로세스가 남아 있으면 물려받은 stderr 핸들 때문에 stderr 파일을 지울 수 없다. 멈춘 채 끝낸
    프로세스는 곧바로 사라지므로 상한은 짧게 둔다. 넘으면 파일은 Task 폴더 삭제가 지운다.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REAP_S
    while True:
        if transport.get_returncode() is not None or loop.time() >= deadline:
            return
        await asyncio.sleep(_POLL_S / 5)


def _check_spec(spec: LaunchSpec) -> None:
    if type(spec) is not LaunchSpec:
        raise LaunchError("LAUNCH_SPEC_INVALID")
    executable = spec.executable
    try:
        if (
            not isinstance(executable, Path)
            or not executable.is_absolute()
            or executable.suffix.casefold() != ".exe"
            or executable.is_symlink()
            or executable.is_junction()
            or not executable.is_file()
        ):
            raise LaunchError("LAUNCH_EXECUTABLE_INVALID")
    except OSError:
        raise LaunchError("LAUNCH_EXECUTABLE_INVALID") from None
    if (
        type(spec.executable_size) is not int
        or spec.executable_size <= 0
        or type(spec.executable_sha256) is not str
        or not _SHA256.fullmatch(spec.executable_sha256)
        or type(spec.args) is not tuple
        or any(type(arg) is not str or "\0" in arg for arg in spec.args)
        or type(spec.env) is not tuple
    ):
        raise LaunchError("LAUNCH_SPEC_INVALID")
    names: set[str] = set()
    for item in spec.env:
        if type(item) is not tuple or len(item) != 2:
            raise LaunchError("LAUNCH_SPEC_INVALID")
        name, value = item
        if (
            type(name) is not str
            or not _ENV_NAME.fullmatch(name)
            or name.upper() in names
            or type(value) is not str
            or "\0" in value
        ):
            raise LaunchError("LAUNCH_SPEC_INVALID")
        names.add(name.upper())
    _checked_dir(spec.cwd, "LAUNCH_CWD_INVALID")
    stderr_path = spec.stderr_path
    if not isinstance(stderr_path, Path) or not stderr_path.is_absolute():
        raise LaunchError("LAUNCH_STDERR_INVALID")
    _checked_dir(stderr_path.parent, "LAUNCH_STDERR_INVALID")


def _checked_dir(path: Path, code: str) -> Path:
    """앱이 만든 폴더만 받는다. 상대 경로·링크·junction은 따라가지 않는다."""
    try:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or path.is_symlink()
            or path.is_junction()
            or not path.is_dir()
        ):
            raise LaunchError(code)
    except OSError:
        raise LaunchError(code) from None
    return path


async def _verified_image(spec: LaunchSpec) -> BinaryIO:
    """해시 계산은 스레드에서 한다. 취소되면 늦게 열린 핸들도 닫는다."""
    opening = asyncio.ensure_future(
        asyncio.to_thread(
            _open_verified, spec.executable, spec.executable_size, spec.executable_sha256
        )
    )
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError:
        opening.add_done_callback(_close_late_image)
        raise


def _close_late_image(opening: asyncio.Future[BinaryIO]) -> None:
    if not opening.cancelled() and opening.exception() is None:
        opening.result().close()


def _open_verified(path: Path, size: int, sha256: str) -> BinaryIO:
    """쓰기·이름 변경·삭제를 막는 핸들로 열어 크기·SHA-256을 대조하고, 열린 채로 돌려준다."""
    handle = _CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_SEQUENTIAL_SCAN,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        raise LaunchError("LAUNCH_EXECUTABLE_INVALID")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        _CloseHandle(handle)
        raise LaunchError("LAUNCH_EXECUTABLE_INVALID") from None
    # 프로세스 생성까지 핸들을 쥐어야 해서 with로 닫지 않는다. 실패 경로는 아래 finally가 닫는다.
    image = open(descriptor, "rb", closefd=True)  # noqa: SIM115
    verified = False
    try:
        digest = hashlib.sha256()
        total = 0
        while chunk := image.read(_HASH_CHUNK):
            total += len(chunk)
            if total > size:
                raise LaunchError("LAUNCH_EXECUTABLE_MISMATCH")
            digest.update(chunk)
        if total != size or digest.hexdigest() != sha256:
            raise LaunchError("LAUNCH_EXECUTABLE_MISMATCH")
        verified = True
    except OSError:
        raise LaunchError("LAUNCH_EXECUTABLE_INVALID") from None
    finally:
        if not verified:
            image.close()
    return image


def _check_image(pid: int, image: BinaryIO) -> None:
    """재개 전에, 멈춘 프로세스가 올린 이미지가 검사한 그 파일인지 본다(#35 B 리뷰 1번).

    CreateProcess는 경로 문자열을 다시 해석한다. 검사 핸들이 파일과 상위 폴더의 이름 변경은
    막지만, 상위 경로에 있는 junction의 대상은 그 사이에 바뀔 수 있다(C2.18 실측). 이미지 이름은
    junction을 푼 실제 경로라서, 검사 핸들의 경로(NT 형식)와 같아야 하고 그 이름으로 다시 연
    파일의 ID(볼륨 일련번호·128비트 파일 ID)도 검사 핸들과 같아야 한다.
    """
    held = msvcrt.get_osfhandle(image.fileno())
    name = _image_name(pid)
    if name != _handle_name(held) or _file_id(held) != _file_id_at(name):
        raise LaunchError("LAUNCH_EXECUTABLE_MISMATCH")


def _image_name(pid: int) -> str:
    process = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise LaunchError("LAUNCH_IMAGE_UNVERIFIED")
    try:
        buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
        size = _DWORD(_MAX_PATH_CHARS)
        if not _QueryFullProcessImageNameW(
            process, _PROCESS_NAME_NATIVE, buffer, ctypes.byref(size)
        ):
            raise LaunchError("LAUNCH_IMAGE_UNVERIFIED")
    finally:
        _CloseHandle(process)
    if not buffer.value.startswith("\\"):
        raise LaunchError("LAUNCH_IMAGE_UNVERIFIED")
    return buffer.value


def _handle_name(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
    length = _GetFinalPathNameByHandleW(handle, buffer, _MAX_PATH_CHARS, _FILE_NAME_OPENED_NT)
    if not length or length >= _MAX_PATH_CHARS:
        raise LaunchError("LAUNCH_IMAGE_UNVERIFIED")
    return buffer.value


def _file_id(handle: int) -> tuple[int, bytes]:
    info = _FileIdInfo()
    if not _GetFileInformationByHandleEx(
        handle, _FILE_ID_INFO, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise LaunchError("LAUNCH_IMAGE_UNVERIFIED")
    return int(info.VolumeSerialNumber), bytes(info.FileId)


def _file_id_at(nt_name: str) -> tuple[int, bytes]:
    """NT 경로로 속성만 읽는 핸들을 열어 파일 ID를 읽는다. 마지막 구성요소 링크는 따르지 않는다."""
    handle = _CreateFileW(
        _NT_PATH_PREFIX + nt_name,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        raise LaunchError("LAUNCH_IMAGE_UNVERIFIED")
    try:
        return _file_id(handle)
    finally:
        _CloseHandle(handle)


class _Job:
    """프로세스와 그 자식을 묶는 Job Object. 핸들을 닫으면 남은 프로세스가 모두 끝난다."""

    def __init__(self) -> None:
        handle = _CreateJobObjectW(None, None)
        if not handle:
            raise LaunchError("LAUNCH_JOB_FAILED")
        self._handle: int | None = handle
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = (
            _JOB_KILL_ON_CLOSE | _JOB_DIE_ON_UNHANDLED_EXCEPTION
        )
        if not _SetInformationJobObject(
            handle, _JOB_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise LaunchError("LAUNCH_JOB_FAILED")

    def adopt(self, pid: int) -> None:
        """멈춘 채 만든 프로세스를 Job에 넣는다. 재개는 호출자가 이미지를 대조한 뒤에 한다."""
        process = _OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            raise LaunchError("LAUNCH_JOB_FAILED")
        try:
            if not _AssignProcessToJobObject(self._handle, process):
                raise LaunchError("LAUNCH_JOB_FAILED")
        finally:
            _CloseHandle(process)

    def active(self) -> int | None:
        if self._handle is None:
            return None
        info = _Accounting()
        if not _QueryInformationJobObject(
            self._handle, _JOB_BASIC_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            return None
        return int(info.ActiveProcesses)

    def terminate(self) -> None:
        if self._handle is not None:
            _TerminateJobObject(self._handle, _TERMINATED_EXIT_CODE)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            _CloseHandle(handle)


def _resume_initial_thread(pid: int) -> None:
    """CREATE_SUSPENDED로 만든 프로세스에는 멈춘 스레드가 정확히 하나 있다. 그 하나만 재개한다."""
    snapshot = _CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot is None or snapshot == _INVALID_HANDLE:
        raise LaunchError("LAUNCH_RESUME_FAILED")
    resumed = 0
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = _Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = _OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if not thread:
                    raise LaunchError("LAUNCH_RESUME_FAILED")
                try:
                    previous = _ResumeThread(thread)
                finally:
                    _CloseHandle(thread)
                if previous != 1:
                    raise LaunchError("LAUNCH_RESUME_FAILED")
                resumed += 1
            more = _Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _CloseHandle(snapshot)
    if resumed != 1:
        raise LaunchError("LAUNCH_RESUME_FAILED")
