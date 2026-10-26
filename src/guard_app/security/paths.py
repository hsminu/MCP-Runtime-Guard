"""앱 폴더 경로·권한과 Task 폴더 수명(C2.13, I9·T43). 오류에 경로를 싣지 않는다.

앱 폴더는 `%LOCALAPPDATA%\\MCPRuntimeGuard`다. 루트와 관리 폴더(`codex`·`codex_home`·`tasks`·
`config`)에는 현재 사용자와 SYSTEM만 접근하는 보호된 DACL을 걸고, 그 안의 파일은 이 권한을
물려받는다. 이미 있는 폴더는 소유자가 현재 사용자일 때만 쓰고 권한을 다시 건다.

`prepare_app_folders`는 앱 시작 때 한 번 부른다. 단일 실행 잠금(`app.lock`)을 잡은 뒤, 이전
실행이 남긴 Task 폴더를 모두 지운다. Task 폴더에는 Codex 로그 DB가 생기고 그 DB는 도구 결과
원문을 담으므로(C-CODEX-01), Task가 끝나면 `remove_task_folder`로 지운다. 삭제한 파일의 디스크
복구 가능성은 남는 한계다. 링크·junction은 따라가지 않는다. Windows 전용이다.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_DIR_NAME = "MCPRuntimeGuard"
APP_LOCK_FILE = "app.lock"
SETTINGS_FILE = "settings.json"
MANAGED_DIRS = ("codex", "codex_home", "tasks", "config")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{device}{number}" for device in ("COM", "LPT") for number in range(10)}
)
_ACE = re.compile(r"\(([^;()]*);([^;()]*);([^;()]*);([^;()]*);([^;()]*);([^;()]*)\)")

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_ERROR_SHARING_VIOLATION = 32
_ERROR_ALREADY_EXISTS = 183

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    def _api(library: Any, name: str, restype: object, *argtypes: object) -> Any:
        function = getattr(library, name)
        function.restype = restype
        function.argtypes = argtypes
        return function

    _PVOID, _DWORD, _BOOL, _HANDLE = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.HANDLE,
    )
    _SHGetKnownFolderPath = _api(
        _shell32,
        "SHGetKnownFolderPath",
        ctypes.c_long,
        ctypes.POINTER(_Guid),
        _DWORD,
        _HANDLE,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    _CoTaskMemFree = _api(_ole32, "CoTaskMemFree", None, _PVOID)
    _GetCurrentProcess = _api(_kernel32, "GetCurrentProcess", _HANDLE)
    _CloseHandle = _api(_kernel32, "CloseHandle", _BOOL, _HANDLE)
    _LocalFree = _api(_kernel32, "LocalFree", _PVOID, _PVOID)
    _CreateDirectoryW = _api(
        _kernel32,
        "CreateDirectoryW",
        _BOOL,
        wintypes.LPCWSTR,
        ctypes.POINTER(_SecurityAttributes),
    )
    _CreateFileW = _api(
        _kernel32,
        "CreateFileW",
        _HANDLE,
        wintypes.LPCWSTR,
        _DWORD,
        _DWORD,
        _PVOID,
        _DWORD,
        _DWORD,
        _HANDLE,
    )
    _OpenProcessToken = _api(
        _advapi32, "OpenProcessToken", _BOOL, _HANDLE, _DWORD, ctypes.POINTER(_HANDLE)
    )
    _GetTokenInformation = _api(
        _advapi32,
        "GetTokenInformation",
        _BOOL,
        _HANDLE,
        ctypes.c_int,
        _PVOID,
        _DWORD,
        ctypes.POINTER(_DWORD),
    )
    _ConvertSidToStringSidW = _api(
        _advapi32, "ConvertSidToStringSidW", _BOOL, _PVOID, ctypes.POINTER(wintypes.LPWSTR)
    )
    _ConvertStringSecurityDescriptorToSecurityDescriptorW = _api(
        _advapi32,
        "ConvertStringSecurityDescriptorToSecurityDescriptorW",
        _BOOL,
        wintypes.LPCWSTR,
        _DWORD,
        ctypes.POINTER(_PVOID),
        _PVOID,
    )
    _ConvertSecurityDescriptorToStringSecurityDescriptorW = _api(
        _advapi32,
        "ConvertSecurityDescriptorToStringSecurityDescriptorW",
        _BOOL,
        _PVOID,
        _DWORD,
        _DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        _PVOID,
    )
    _GetSecurityDescriptorDacl = _api(
        _advapi32,
        "GetSecurityDescriptorDacl",
        _BOOL,
        _PVOID,
        ctypes.POINTER(_BOOL),
        ctypes.POINTER(_PVOID),
        ctypes.POINTER(_BOOL),
    )
    _GetNamedSecurityInfoW = _api(
        _advapi32,
        "GetNamedSecurityInfoW",
        _DWORD,
        wintypes.LPCWSTR,
        ctypes.c_int,
        _DWORD,
        ctypes.POINTER(_PVOID),
        ctypes.POINTER(_PVOID),
        ctypes.POINTER(_PVOID),
        ctypes.POINTER(_PVOID),
        ctypes.POINTER(_PVOID),
    )
    _SetNamedSecurityInfoW = _api(
        _advapi32,
        "SetNamedSecurityInfoW",
        _DWORD,
        wintypes.LPWSTR,
        ctypes.c_int,
        _DWORD,
        _PVOID,
        _PVOID,
        _PVOID,
        _PVOID,
    )
    # FOLDERID_LocalAppData {F1B32785-6FBA-4FCF-9D55-7B8E7F157091}
    _FOLDERID_LOCAL_APP_DATA = _Guid(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )


class AppPathError(RuntimeError):
    """경로를 공개하지 않는 고정 오류 코드."""


@dataclass(frozen=True, slots=True)
class AppPaths:
    """앱 폴더 배치. 경로 계산만 하고 파일 시스템을 건드리지 않는다."""

    root: Path

    @property
    def codex_home(self) -> Path:
        return self.root / "codex_home"

    @property
    def tasks(self) -> Path:
        return self.root / "tasks"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def settings_file(self) -> Path:
        return self.config / SETTINGS_FILE

    def codex_install(self, version: str) -> Path:
        if type(version) is not str or not _VERSION.fullmatch(version):
            raise AppPathError("APP_CODEX_VERSION_INVALID")
        return self.root / "codex" / version


class AppFolders:
    """앱 실행 동안의 폴더 소유. `close` 전까지 단일 실행 잠금을 쥔다."""

    def __init__(self, paths: AppPaths, lock: int, stale_removed: int) -> None:
        self.paths = paths
        self.stale_removed = stale_removed
        self._lock: int | None = lock

    def create_task_folder(self, task_id: str) -> Path:
        """Task 하나의 빈 폴더를 새로 만든다. 같은 이름이 있으면 다시 쓰지 않는다."""
        self._require_open()
        folder = self.paths.tasks / _checked_task_id(task_id)
        try:
            folder.mkdir()
        except FileExistsError:
            raise AppPathError("TASK_FOLDER_EXISTS") from None
        except OSError:
            raise AppPathError("TASK_FOLDER_CREATE_FAILED") from None
        return folder

    def remove_task_folder(self, task_dir: Path) -> None:
        """`tasks` 바로 아래의 Task 폴더를 지운다. 하나라도 남으면 고정 오류를 낸다.

        Codex 프로세스가 끝난 것을 확인한 뒤에만 부른다(`LaunchedProcess.close`가 True).
        """
        self._require_open()
        if (
            not isinstance(task_dir, Path)
            or task_dir.parent != self.paths.tasks
            or not _TASK_ID.fullmatch(task_dir.name)
        ):
            raise AppPathError("TASK_FOLDER_INVALID")
        try:
            if task_dir.is_symlink() or task_dir.is_junction() or not task_dir.is_dir():
                raise AppPathError("TASK_FOLDER_INVALID")
        except OSError:
            raise AppPathError("TASK_FOLDER_INVALID") from None
        if not _remove_tree(task_dir):
            raise AppPathError("TASK_FOLDER_CLEANUP_FAILED")

    def close(self) -> None:
        """단일 실행 잠금을 푼다. 여러 번 불러도 된다."""
        lock, self._lock = self._lock, None
        if lock is not None:
            _CloseHandle(lock)

    def _require_open(self) -> None:
        if self._lock is None:
            raise AppPathError("APP_FOLDERS_CLOSED")


def app_root() -> Path:
    """`%LOCALAPPDATA%\\MCPRuntimeGuard`. 환경변수가 아니라 Windows 알려진 폴더로 구한다."""
    if sys.platform != "win32":
        raise AppPathError("APP_PATHS_UNSUPPORTED")
    buffer = wintypes.LPWSTR()
    result = _SHGetKnownFolderPath(
        ctypes.byref(_FOLDERID_LOCAL_APP_DATA), 0, None, ctypes.byref(buffer)
    )
    try:
        if result != 0 or not buffer.value:
            raise AppPathError("APP_ROOT_UNAVAILABLE")
        base = Path(buffer.value)
    finally:
        _CoTaskMemFree(ctypes.cast(buffer, _PVOID))
    if not base.is_absolute():
        raise AppPathError("APP_ROOT_UNAVAILABLE")
    return base / APP_DIR_NAME


def prepare_app_folders(root: Path | None = None) -> AppFolders:
    """앱 시작 때 한 번 부른다. 폴더·권한을 맞추고 잠금을 잡은 뒤 남은 Task 폴더를 지운다.

    `root`는 테스트용이다. 제품은 기본값(`app_root()`)을 쓴다. 다른 인스턴스가 실행 중이면
    `APP_ALREADY_RUNNING`, 남은 Task 폴더를 다 지우지 못하면 `APP_STALE_TASK_CLEANUP_FAILED`다.
    """
    if sys.platform != "win32":
        raise AppPathError("APP_PATHS_UNSUPPORTED")
    base = app_root() if root is None else root
    if not isinstance(base, Path) or not base.is_absolute():
        raise AppPathError("APP_ROOT_INVALID")
    user = _current_user_sid()
    _ensure_private_dir(base, user, "APP_ROOT")
    lock = _acquire_lock(base / APP_LOCK_FILE)
    folders = AppFolders(AppPaths(base), lock, 0)
    try:
        for name in MANAGED_DIRS:
            _ensure_private_dir(base / name, user, "APP_FOLDER")
        folders.stale_removed = _clear_task_folders(folders.paths.tasks)
    except AppPathError:
        folders.close()
        raise
    return folders


def _checked_task_id(task_id: object) -> str:
    if (
        type(task_id) is not str
        or not _TASK_ID.fullmatch(task_id)
        or task_id.upper() in _RESERVED_NAMES
    ):
        raise AppPathError("TASK_ID_INVALID")
    return task_id


def _ensure_private_dir(path: Path, user: str, prefix: str) -> None:
    """없으면 보호된 DACL로 만들고, 있으면 소유자를 확인한 뒤 같은 DACL을 다시 건다."""
    try:
        if path.is_symlink() or path.is_junction():
            raise AppPathError(f"{prefix}_INVALID")
        exists = path.exists()
        if exists and not path.is_dir():
            raise AppPathError(f"{prefix}_INVALID")
    except OSError:
        raise AppPathError(f"{prefix}_INVALID") from None
    descriptor = _private_descriptor(user)
    try:
        if not exists:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if not _CreateDirectoryW(str(path), ctypes.byref(attributes)):
                if ctypes.get_last_error() != _ERROR_ALREADY_EXISTS:
                    raise AppPathError(f"{prefix}_CREATE_FAILED")
                exists = True
        if exists:
            if _owner_sid(path) != user:
                raise AppPathError(f"{prefix}_NOT_OWNED")
            present, dacl, defaulted = _BOOL(), _PVOID(), _BOOL()
            if not _GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            ):
                raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
            if _SetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            ):
                raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    finally:
        _LocalFree(descriptor)
    if not _is_private(path, user, protected=True):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")


def _private_descriptor(user: str) -> int:
    # 소유자를 현재 사용자로 적는다. 관리자 권한으로 실행하면 기본 소유자가 Administrators가 된다.
    sddl = f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)"
    descriptor = _PVOID()
    if not _ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    if descriptor.value is None:
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    return descriptor.value


def _security_sddl(path: Path, information: int) -> str:
    descriptor = _PVOID()
    if _GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        information,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    ):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    text = wintypes.LPWSTR()
    try:
        if not _ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, _SDDL_REVISION_1, information, ctypes.byref(text), None
        ):
            raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
        return text.value or ""
    finally:
        _LocalFree(descriptor)
        if text:
            _LocalFree(ctypes.cast(text, _PVOID))


def _owner_sid(path: Path) -> str:
    sddl = _security_sddl(path, _OWNER_SECURITY_INFORMATION)
    if not sddl.startswith("O:"):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    return sddl[2:]


def _dacl_entries(path: Path) -> tuple[str, list[tuple[str, str, str, str]]]:
    """DACL 머리 표지(`P`=상속 차단)와 ACE 목록(종류·상속 표지·권한·SID)."""
    sddl = _security_sddl(path, _DACL_SECURITY_INFORMATION)
    if not sddl.startswith("D:"):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    body = sddl[2:]
    head = body.split("(", 1)[0]
    entries = [(kind, flags, rights, sid) for kind, flags, rights, _, _, sid in _ACE.findall(body)]
    return head, entries


def _is_private(path: Path, user: str, *, protected: bool) -> bool:
    """허용 ACE만 있고 그 대상이 현재 사용자와 SYSTEM뿐인지 본다. 사용자는 전체 권한이어야 한다."""
    head, entries = _dacl_entries(path)
    if protected and "P" not in head:
        return False
    if not entries or any(kind != "A" or sid not in (user, "SY") for kind, _, _, sid in entries):
        return False
    return any(sid == user and rights == "FA" for _, _, rights, sid in entries)


def _current_user_sid() -> str:
    token = _HANDLE()
    if not _OpenProcessToken(_GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise AppPathError("APP_USER_UNAVAILABLE")
    try:
        size = _DWORD()
        _GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not size.value or not _GetTokenInformation(
            token, _TOKEN_USER, buffer, size, ctypes.byref(size)
        ):
            raise AppPathError("APP_USER_UNAVAILABLE")
        sid = ctypes.cast(buffer, ctypes.POINTER(_PVOID))[0]
        text = wintypes.LPWSTR()
        if not _ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise AppPathError("APP_USER_UNAVAILABLE")
        try:
            value = text.value or ""
        finally:
            _LocalFree(ctypes.cast(text, _PVOID))
    finally:
        _CloseHandle(token)
    if not value.startswith("S-1-"):
        raise AppPathError("APP_USER_UNAVAILABLE")
    return value


def _acquire_lock(path: Path) -> int:
    """공유를 허용하지 않는 핸들로 잠금 파일을 연다. 다른 인스턴스가 쥐고 있으면 실패한다."""
    handle = _CreateFileW(
        str(path),
        _GENERIC_READ | _GENERIC_WRITE,
        0,
        None,
        _OPEN_ALWAYS,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        if ctypes.get_last_error() == _ERROR_SHARING_VIOLATION:
            raise AppPathError("APP_ALREADY_RUNNING")
        raise AppPathError("APP_LOCK_FAILED")
    return int(handle)


def _clear_task_folders(tasks: Path) -> int:
    """이전 실행이 남긴 `tasks` 아래 항목을 모두 지운다. 링크는 대상이 아니라 링크만 지운다."""
    removed = 0
    try:
        entries = list(tasks.iterdir())
    except OSError:
        raise AppPathError("APP_STALE_TASK_CLEANUP_FAILED") from None
    for entry in entries:
        try:
            if entry.is_junction() or (entry.is_symlink() and entry.is_dir()):
                entry.rmdir()
                ok = True
            elif entry.is_dir() and not entry.is_symlink():
                ok = _remove_tree(entry)
            else:
                _unlink(entry)
                ok = True
        except OSError:
            ok = False
        if not ok:
            raise AppPathError("APP_STALE_TASK_CLEANUP_FAILED")
        removed += 1
    return removed


def _remove_tree(folder: Path) -> bool:
    """읽기 전용 표시만 풀어 다시 시도한다. 그 밖의 실패는 남겨 두고 False를 돌려준다."""
    failures: list[str] = []

    def retry(function: Callable[..., object], target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            failures.append(target)
            return
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            failures.append(target)

    shutil.rmtree(folder, onexc=retry)
    try:
        return not failures and not os.path.lexists(folder)
    except OSError:
        return False


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()
