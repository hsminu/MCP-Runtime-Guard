"""앱 폴더 경로·권한과 Task 폴더 수명(C2.13, I9·T43). 오류에 경로를 싣지 않는다.

앱 폴더는 `%LOCALAPPDATA%\\MCPRuntimeGuard`다. 루트와 관리 폴더(`codex`·`codex_home`·`tasks`·
`servers`·`config`·`data`·`data\\backups`)에는 현재 사용자와 SYSTEM만 접근하는 보호된 DACL을
걸고, 그 안의 파일은 이 권한을 물려받는다. 이미 있는 폴더는 소유자가 현재 사용자일 때만 쓰고
권한을 다시 건다. 제품 DB는 `data\\guard.sqlite3`다. `app.lock`이 같은 사용자의 두 번째
실행을, 폴더 권한이 다른 사용자를 막아 이 DB의 단일 소유가 된다(REC-01, #35 A·B 합의).
B는 DB 경로를 `db_path()`로, 이관 전 백업 자리를 `begin_backup()`으로 받는다(C2.19, #37).
설정 파일 `config\\settings.json`의 읽기·교체는 `settings_io`가 맡는다(C2.14).

`prepare_app_folders`는 앱 시작 때 한 번 부른다. 단일 실행 잠금(`app.lock`)을 잡은 뒤, 이전
실행이 남긴 Task 폴더·서버 폴더와 끝나지 않은 백업·설정 임시 파일을 모두 지운다. Task 폴더에는
Codex 로그 DB가 생기고 그 DB는 도구 결과 원문을 담으므로(C-CODEX-01), Task가 끝나면
`remove_task_folder`로 지운다. stdio MCP 서버의 작업 폴더(`server_folder`, C2.6)에는 서버의
stderr와 임시 파일이 쌓인다. 삭제한 파일의 디스크 복구 가능성은 남는 한계다. 링크·junction은
따라가지 않는다. Windows 전용이다.

**파일 서버 경로(C2.3).** `safe_path`는 파일 MCP 서버 루트 아래의 요청 경로를 검사해 실제 경로를
돌려주고, `resource_id`는 그 경로를 루트 기준 POSIX ID로 만든다. Guard는 `file_resource_resolver`로
요청 인자에서 기대 ID를 계산하고(B Catalog `ResourceResolver`), 서버가 보고한 ID와 대조한다(T33).
ID는 디스크의 실제 이름(대소문자·긴 이름)이라 표기만 다른 요청도 같은 ID가 되고, 등록 화면에서
고른 폴더 이름의 출처 규칙과 맞는다. 검사와 열기 사이의 경쟁은 이 함수만으로 막지 못한다(C2.4,
서버가 연 핸들의 실제 경로를 다시 대조한다).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from guard_app.domain.errors import ResolutionError
from guard_app.domain.models import ToolSpec
from guard_app.domain.reasons import Reason

APP_DIR_NAME = "MCPRuntimeGuard"
APP_LOCK_FILE = "app.lock"
SETTINGS_FILE = "settings.json"
DATABASE_FILE = "guard.sqlite3"
BACKUP_SUFFIX = ".sqlite3"
# 부모 폴더가 먼저 오게 둔다.
MANAGED_DIRS = ("codex", "codex_home", "tasks", "servers", "config", "data", "data/backups")
SYSTEM_SID = "S-1-5-18"
# SQLite가 DB 옆에 만드는 파일. 백업 임시 파일에도 같은 이름 규칙으로 생긴다.
_SQLITE_SIDE_SUFFIXES = ("-journal", "-wal", "-shm")
_PARTIAL_SUFFIX = ".partial"
_BACKUP_LABEL = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_BACKUP_LABEL_MAX = 32
_BACKUP_NAME_TRIES = 9
# settings_io가 교체 전에 같은 폴더에 쓰는 임시 파일 이름. 앱 시작 때 남은 것을 지운다.
SETTINGS_TEMP = re.compile(r"settings\.json\.[0-9a-f]{32}\.tmp")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# Windows가 확장자와 상관없이 장치로 여는 이름. launcher의 실행 파일 경로 검사도 쓴다.
RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{device}{number}" for device in ("COM", "LPT") for number in range(10)}
)
_SERVER_FOLDER_CHARS = 32
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
_MOVEFILE_WRITE_THROUGH = 0x00000008
_DRIVE_FIXED = 3

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
    _GetDriveTypeW = _api(_kernel32, "GetDriveTypeW", wintypes.UINT, wintypes.LPCWSTR)
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
    _MoveFileExW = _api(_kernel32, "MoveFileExW", _BOOL, wintypes.LPCWSTR, wintypes.LPCWSTR, _DWORD)
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
    _ConvertStringSidToSidW = _api(
        _advapi32, "ConvertStringSidToSidW", _BOOL, wintypes.LPCWSTR, ctypes.POINTER(_PVOID)
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
    def servers(self) -> Path:
        """stdio MCP 서버별 작업 폴더의 부모(C2.6)."""
        return self.root / "servers"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def settings_file(self) -> Path:
        return self.config / SETTINGS_FILE

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db_file(self) -> Path:
        """제품 SQLite DB. `-wal`·`-shm`·`-journal`도 같은 폴더에 생겨 같은 권한을 받는다."""
        return self.data / DATABASE_FILE

    @property
    def backups(self) -> Path:
        """DB 이관 전 백업 폴더(B6.17 SEQ-03)."""
        return self.data / "backups"

    def codex_install(self, version: str) -> Path:
        if type(version) is not str or not _VERSION.fullmatch(version):
            raise AppPathError("APP_CODEX_VERSION_INVALID")
        return self.root / "codex" / version


class AppFolders:
    """앱 실행 동안의 폴더 소유. `close` 전까지 단일 실행 잠금을 쥔다."""

    def __init__(self, paths: AppPaths, lock: int, stale_removed: int) -> None:
        self.paths = paths
        self.stale_removed = stale_removed
        self.stale_backups_removed = 0
        self.stale_settings_removed = 0
        self.stale_server_folders_removed = 0
        self._lock: int | None = lock

    def db_path(self) -> Path:
        """제품 DB 경로. 이미 있는 DB와 옆 파일이 앱 폴더 권한 안의 일반 파일인지 본 뒤 준다.

        링크·reparse point·폴더, 하드링크가 둘 이상인 파일, 사용자·SYSTEM 밖에 권한이 있는 파일은
        `APP_DB_INVALID`다. 파일 소유자는 보지 않는다(관리자 권한 실행에서는 Administrators가
        된다). SQLite가 경로로 다시 여는 사이의 틈은 남지만, 끼어들려면 사용자·SYSTEM 전용인
        `data` 폴더에 쓸 수 있어야 한다.
        """
        self.require_open()
        db = self.paths.db_file
        user = _current_user_sid()
        for path in (db, *(_side_file(db, suffix) for suffix in _SQLITE_SIDE_SUFFIXES)):
            _check_private_file(path, user)
        return db

    def begin_backup(self, label: str) -> BackupTarget:
        """DB 이관 전 백업의 임시 파일을 `backups` 안에 새로 만들어 준다(SEQ-03, #37).

        이름은 `<label>-<UTC 초>`이고, 빈 `.partial` 파일을 배타 생성해 이름을 선점한다. 같은
        이름이 있으면 `-2`부터 붙인다. B가 그 경로로 SQLite Backup API를 실행하고 연결을 닫아
        확인한 뒤 `publish()` 또는 `discard()`를 부른다.
        """
        self.require_open()
        if (
            type(label) is not str
            or len(label) > _BACKUP_LABEL_MAX
            or not _BACKUP_LABEL.fullmatch(label)
        ):
            raise AppPathError("APP_BACKUP_LABEL_INVALID")
        stem = f"{label}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        for attempt in range(1, _BACKUP_NAME_TRIES + 1):
            name = stem if attempt == 1 else f"{stem}-{attempt}"
            partial = self.paths.backups / f"{name}{_PARTIAL_SUFFIX}"
            final = self.paths.backups / f"{name}{BACKUP_SUFFIX}"
            if os.path.lexists(final):
                continue
            try:
                partial.open("xb").close()
            except FileExistsError:
                continue
            except OSError:
                raise AppPathError("APP_BACKUP_CREATE_FAILED") from None
            return BackupTarget(self, partial, final)
        raise AppPathError("APP_BACKUP_CREATE_FAILED")

    def create_task_folder(self, task_id: str) -> Path:
        """Task 하나의 빈 폴더를 새로 만든다. 같은 이름이 있으면 다시 쓰지 않는다."""
        self.require_open()
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
        self.require_open()
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

    def server_folder(self, server_id: str) -> Path:
        """stdio MCP 서버 하나의 작업 폴더(C2.6). 없으면 만들고, 있으면 그대로 준다.

        서버 ID에는 경로에 쓸 수 없는 문자도 올 수 있어서, 폴더 이름은 ID(UTF-8)의 SHA-256 앞
        32자다. 같은 실행 안에서는 다시 연결해도 같은 폴더를 쓰고, 다음 시작이 모두 지운다.
        """
        self.require_open()
        if type(server_id) is not str or not server_id:
            raise AppPathError("SERVER_ID_INVALID")
        try:
            encoded = server_id.encode("utf-8")
        except UnicodeEncodeError:
            raise AppPathError("SERVER_ID_INVALID") from None
        name = hashlib.sha256(encoded).hexdigest()[:_SERVER_FOLDER_CHARS]
        folder = self.paths.servers / name
        try:
            folder.mkdir(exist_ok=True)
            if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
                raise AppPathError("SERVER_FOLDER_INVALID")
        except OSError:
            raise AppPathError("SERVER_FOLDER_CREATE_FAILED") from None
        return folder

    def close(self) -> None:
        """단일 실행 잠금을 푼다. 여러 번 불러도 된다."""
        lock, self._lock = self._lock, None
        if lock is not None:
            _CloseHandle(lock)

    def require_open(self) -> None:
        """잠금을 쥔 동안만 앱 폴더를 쓴다. 닫혔으면 `APP_FOLDERS_CLOSED`다."""
        if self._lock is None:
            raise AppPathError("APP_FOLDERS_CLOSED")


class BackupTarget:
    """`begin_backup`이 준 백업 임시 파일. `publish`나 `discard` 중 하나로 끝낸다."""

    def __init__(self, folders: AppFolders, path: Path, final: Path) -> None:
        self.path = path
        self._folders = folders
        self._final = final
        self._state = "pending"

    def publish(self) -> Path:
        """디스크에 내린 뒤 최종 이름(`.sqlite3`)으로 바꾼다. 같은 이름이 있으면 덮어쓰지 않는다.

        B가 대상 연결을 닫은 뒤에 부른다. SQLite 옆 파일이 남아 있으면 연결이 닫히지 않았거나
        끝나지 않은 것이라 실패한다. 빈 파일도 실패다. 실패하면 임시 파일은 그대로 두고
        `discard`나 다음 시작 정리가 지운다.
        """
        self._folders.require_open()
        if self._state != "pending":
            raise AppPathError("APP_BACKUP_TARGET_CLOSED")
        try:
            if any(
                os.path.lexists(_side_file(self.path, suffix)) for suffix in _SQLITE_SIDE_SUFFIXES
            ):
                raise AppPathError("APP_BACKUP_PUBLISH_FAILED")
            info = os.lstat(self.path)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or info.st_size == 0
            ):
                raise AppPathError("APP_BACKUP_PUBLISH_FAILED")
            # Windows의 fsync(FlushFileBuffers)는 쓰기 권한 핸들이 있어야 한다.
            with self.path.open("r+b") as file:
                os.fsync(file.fileno())
        except OSError:
            raise AppPathError("APP_BACKUP_PUBLISH_FAILED") from None
        if not _MoveFileExW(str(self.path), str(self._final), _MOVEFILE_WRITE_THROUGH):
            raise AppPathError("APP_BACKUP_PUBLISH_FAILED")
        self._state = "published"
        return self._final

    def discard(self) -> None:
        """임시 파일과 SQLite 옆 파일을 지운다. 여러 번 불러도 되고, publish 뒤에는 아무것도 하지
        않는다(B가 `finally`에서 불러도 완성된 백업은 남는다)."""
        if self._state == "published":
            return
        self._folders.require_open()
        self._state = "discarded"
        failed = False
        sides = (_side_file(self.path, suffix) for suffix in _SQLITE_SIDE_SUFFIXES)
        for path in (self.path, *sides):
            # 이미 없는 파일은 지운 것과 같다(Semgrep 예외, 약점 대장).
            try:  # nosemgrep: no-swallow
                _unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                failed = True
        if failed:
            raise AppPathError("APP_BACKUP_DISCARD_FAILED")


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
    `APP_ALREADY_RUNNING`, 남은 Task 폴더를 다 지우지 못하면 `APP_STALE_TASK_CLEANUP_FAILED`,
    남은 서버 폴더를 다 지우지 못하면 `APP_STALE_SERVER_CLEANUP_FAILED`, 끝나지 않은 백업 임시
    파일을 다 지우지 못하면 `APP_STALE_BACKUP_CLEANUP_FAILED`, 설정 임시 파일을 다 지우지 못하면
    `APP_STALE_SETTINGS_CLEANUP_FAILED`다.

    이미 있는 루트는 잠금 전에는 링크·소유자만 본다. DACL을 다시 걸면 하위 항목 전체에 전파되므로,
    잠금을 잡은 인스턴스만 한다(#35 B 리뷰 2번). 그래서 두 번째 실행은 첫 인스턴스의 파일을
    건드리지 않고 끝난다.
    """
    if sys.platform != "win32":
        raise AppPathError("APP_PATHS_UNSUPPORTED")
    base = app_root() if root is None else root
    if not isinstance(base, Path) or not base.is_absolute():
        raise AppPathError("APP_ROOT_INVALID")
    user = _current_user_sid()
    _ensure_private_dir(base, user, "APP_ROOT", reapply=False)
    lock = _acquire_lock(base / APP_LOCK_FILE)
    folders = AppFolders(AppPaths(base), lock, 0)
    try:
        _ensure_private_dir(base, user, "APP_ROOT")
        for name in MANAGED_DIRS:
            _ensure_private_dir(base / name, user, "APP_FOLDER")
        folders.stale_removed = _clear_all(folders.paths.tasks, "APP_STALE_TASK_CLEANUP_FAILED")
        folders.stale_server_folders_removed = _clear_all(
            folders.paths.servers, "APP_STALE_SERVER_CLEANUP_FAILED"
        )
        folders.stale_backups_removed = _clear_partial_backups(folders.paths.backups)
        folders.stale_settings_removed = _clear_settings_temporaries(folders.paths.config)
    except AppPathError:
        folders.close()
        raise
    return folders


def _checked_task_id(task_id: object) -> str:
    if (
        type(task_id) is not str
        or not _TASK_ID.fullmatch(task_id)
        or task_id.upper() in RESERVED_DEVICE_NAMES
    ):
        raise AppPathError("TASK_ID_INVALID")
    return task_id


def _ensure_private_dir(path: Path, user: str, prefix: str, *, reapply: bool = True) -> None:
    """없으면 보호된 DACL로 만들고, 있으면 소유자를 확인한 뒤 같은 DACL을 다시 건다.

    `reapply=False`이면 이미 있는 폴더는 링크·소유자만 보고 DACL은 건드리지 않는다.
    """
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
            if not reapply:
                return
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


def _canonical_sid(text: str) -> str:
    """SDDL의 SID 표기를 `S-1-…` 문자열로 바꾼다.

    SDDL 변환은 잘 알려진 계정을 약어로 쓴다(SYSTEM은 `SY`, 내장 Administrator(RID 500)는
    `LA`). 문자열 그대로 비교하면 같은 계정도 다르게 보이므로, 비교 전에 항상 이 형태로 맞춘다.
    """
    sid = _PVOID()
    if not text or not _ConvertStringSidToSidW(text, ctypes.byref(sid)) or sid.value is None:
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    try:
        out = wintypes.LPWSTR()
        if not _ConvertSidToStringSidW(sid, ctypes.byref(out)):
            raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
        try:
            value = out.value or ""
        finally:
            _LocalFree(ctypes.cast(out, _PVOID))
    finally:
        _LocalFree(sid)
    if not value.startswith("S-1-"):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    return value


def _owner_sid(path: Path) -> str:
    sddl = _security_sddl(path, _OWNER_SECURITY_INFORMATION)
    if not sddl.startswith("O:"):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    return _canonical_sid(sddl[2:])


def _dacl_entries(path: Path) -> tuple[str, list[tuple[str, str, str, str]]]:
    """DACL 머리 표지(`P`=상속 차단)와 ACE 목록(종류·상속 표지·권한·`S-1-…` SID).

    정규식으로 읽지 못한 ACE(조건부 ACE 등)가 하나라도 있으면 나머지만 보고 판단하지 않는다.
    """
    sddl = _security_sddl(path, _DACL_SECURITY_INFORMATION)
    if not sddl.startswith("D:"):
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    body = sddl[2:]
    head = body.split("(", 1)[0]
    if _ACE.sub("", body) != head:
        raise AppPathError("APP_FOLDER_PERMISSION_FAILED")
    entries = [
        (kind, flags, rights, _canonical_sid(sid))
        for kind, flags, rights, _, _, sid in _ACE.findall(body)
    ]
    return head, entries


def _is_private(path: Path, user: str, *, protected: bool) -> bool:
    """허용 ACE만 있고 그 대상이 현재 사용자와 SYSTEM뿐인지 본다. 사용자는 전체 권한이어야 한다."""
    head, entries = _dacl_entries(path)
    if protected and "P" not in head:
        return False
    if not entries or any(
        kind != "A" or sid not in (user, SYSTEM_SID) for kind, _, _, sid in entries
    ):
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


def _clear_all(folder: Path, code: str) -> int:
    """이전 실행이 남긴 `tasks`·`servers` 아래 항목을 모두 지운다. 링크는 링크만 지운다."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        raise AppPathError(code) from None
    if not all(_remove_entry(entry) for entry in entries):
        raise AppPathError(code)
    return len(entries)


def _clear_partial_backups(backups: Path) -> int:
    """끝나지 않은 백업(`*.partial`과 그 SQLite 옆 파일)만 지운다. 완성된 백업은 남긴다."""
    suffixes = (_PARTIAL_SUFFIX, *(_PARTIAL_SUFFIX + side for side in _SQLITE_SIDE_SUFFIXES))
    try:
        entries = [entry for entry in backups.iterdir() if entry.name.endswith(suffixes)]
    except OSError:
        raise AppPathError("APP_STALE_BACKUP_CLEANUP_FAILED") from None
    if not all(_remove_entry(entry) for entry in entries):
        raise AppPathError("APP_STALE_BACKUP_CLEANUP_FAILED")
    return len(entries)


def _clear_settings_temporaries(config: Path) -> int:
    """설정 교체 도중 끊겨 남은 임시 파일만 지운다. `settings.json`과 그 밖의 파일은 남긴다."""
    try:
        entries = [entry for entry in config.iterdir() if SETTINGS_TEMP.fullmatch(entry.name)]
    except OSError:
        raise AppPathError("APP_STALE_SETTINGS_CLEANUP_FAILED") from None
    if not all(_remove_entry(entry) for entry in entries):
        raise AppPathError("APP_STALE_SETTINGS_CLEANUP_FAILED")
    return len(entries)


def is_private_file(path: Path) -> bool:
    """파일의 DACL이 허용 ACE만 갖고 그 대상이 현재 사용자·SYSTEM뿐인지 본다(상속 ACE 포함).

    읽지 못한 ACE가 있거나 보안 정보를 읽을 수 없으면 `APP_FOLDER_PERMISSION_FAILED`다.
    """
    if sys.platform != "win32":
        raise AppPathError("APP_PATHS_UNSUPPORTED")
    return _is_private(path, _current_user_sid(), protected=False)


def _remove_entry(entry: Path) -> bool:
    """폴더 안 항목 하나를 지운다. 링크는 대상이 아니라 링크만 지운다."""
    try:
        if entry.is_junction() or (entry.is_symlink() and entry.is_dir()):
            entry.rmdir()
            return True
        if entry.is_dir() and not entry.is_symlink():
            return _remove_tree(entry)
        _unlink(entry)
        return True
    except OSError:
        return False


def _side_file(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _check_private_file(path: Path, user: str) -> None:
    """없으면 통과. 있으면 하드링크가 하나인 일반 파일이고 권한이 사용자·SYSTEM뿐이어야 한다."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise AppPathError("APP_DB_INVALID") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or info.st_nlink != 1
    ):
        raise AppPathError("APP_DB_INVALID")
    try:
        private = _is_private(path, user, protected=False)
    except AppPathError:
        raise AppPathError("APP_DB_INVALID") from None
    if not private:
        raise AppPathError("APP_DB_INVALID")


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


class SafePathError(ValueError):
    """경로 원문을 싣지 않는 고정 거부 코드."""


# Windows가 이름으로 받지 않거나 다르게 읽는 문자. `:`는 드라이브와 ADS, `\`는 다른 구분자다.
_PATH_FORBIDDEN = frozenset('<>:"|?*\\')
_MAX_REQUEST_CHARS = 4096


def safe_path(root: Path, user_path: str) -> Path:
    """루트 아래의 파일·폴더 하나를 실제 경로로 돌려준다(C2.3, 개발문서 13.2).

    `user_path`는 `/`로 나눈 상대 경로다. 다음은 `PATH_INVALID`다: 빈 값, 4,096자 초과, 제어 문자,
    역슬래시·`:`·`<>"|?*`, 절대 경로, `.`·`..`·빈 조각, 끝 점·공백, 장치 이름(`CON`·`NUL.txt` 등),
    짝 없는 서로게이트. 루트 아래 조각 중 링크·junction이 있으면 `PATH_LINK`, 없으면
    `PATH_NOT_FOUND`, 풀린 경로가 루트 밖이면 `PATH_OUTSIDE_ROOT`, 일반 파일·폴더가 아니면
    `PATH_NOT_REGULAR`다. 하드링크가 둘 이상인 파일은 루트 밖 내용을 루트 안 이름으로 읽는 길이 될
    수 있어 `PATH_HARDLINK`다. 루트가 로컬 고정 드라이브의 실제 폴더가 아니면 `PATH_ROOT_INVALID`다.
    """
    root_real = real_root(root)
    parts = _request_parts(user_path)
    current = root_real
    try:
        for part in parts:
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or current.is_junction():
                raise SafePathError("PATH_LINK")
        resolved = current.resolve(strict=True)
        info = os.stat(resolved)
    except FileNotFoundError:
        raise SafePathError("PATH_NOT_FOUND") from None
    except OSError:
        raise SafePathError("PATH_NOT_FOUND") from None
    if not resolved.is_relative_to(root_real) or resolved == root_real:
        raise SafePathError("PATH_OUTSIDE_ROOT")
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            raise SafePathError("PATH_HARDLINK")
    elif not stat.S_ISDIR(info.st_mode):
        raise SafePathError("PATH_NOT_REGULAR")
    return resolved


def resource_id(root: Path, user_path: str) -> str:
    """`safe_path`의 결과를 루트 기준 POSIX ID로 만든다. 조각은 디스크의 실제 이름이다."""
    resolved = safe_path(root, user_path)
    return "/".join(resolved.relative_to(real_root(root)).parts)


def file_resource_resolver(roots: dict[str, Path]) -> Callable[[ToolSpec, str], str]:
    """B Catalog의 `ResourceResolver`. 서버 ID별 루트에서 요청 인자의 기대 ID를 계산한다.

    경로가 거부되면 원문 없이 `ResolutionError(INVALID_ARGUMENTS)`다. 루트가 없는 서버나, 부를
    때 다시 확인한 루트가 사라졌거나 바뀌었으면 모델 인자 탓이 아니므로
    `ResolutionError(INTERNAL_ERROR)`다.
    """
    if type(roots) is not dict or any(
        type(server_id) is not str or not server_id or not isinstance(root, Path)
        for server_id, root in roots.items()
    ):
        raise SafePathError("PATH_ROOT_INVALID")
    fixed = dict(roots)

    def resolve(tool: ToolSpec, user_path: str) -> str:
        root = fixed.get(tool.server_id)
        if root is None:
            raise ResolutionError(Reason.INTERNAL_ERROR)
        try:
            return resource_id(root, user_path)
        except SafePathError as error:
            if str(error) == "PATH_ROOT_INVALID":
                raise ResolutionError(Reason.INTERNAL_ERROR) from None
            raise ResolutionError(Reason.INVALID_ARGUMENTS) from None

    return resolve


def real_root(root: object) -> Path:
    """로컬 고정 드라이브의 절대 경로 폴더를 최종 경로로 푼다. 아니면 `PATH_ROOT_INVALID`다.

    파일 서버(C2.11)도 같은 풀이를 쓴다.
    """
    if not isinstance(root, Path) or not root.is_absolute() or not _on_fixed_drive(root):
        raise SafePathError("PATH_ROOT_INVALID")
    try:
        real = root.resolve(strict=True)
        if not real.is_dir() or not _on_fixed_drive(real):
            raise SafePathError("PATH_ROOT_INVALID")
    except OSError:
        raise SafePathError("PATH_ROOT_INVALID") from None
    return real


def _on_fixed_drive(path: Path) -> bool:
    """드라이브 문자가 있는 로컬 고정 드라이브인지. UNC·네트워크·이동식은 아니다.

    해석기는 Guard 이벤트 루프에서 동기로 디스크를 보므로 느린 드라이브는 받지 않는다. 풀기 전에
    먼저 봐서 UNC 루트는 네트워크에 닿지 않고 거부한다.
    """
    drive = path.drive
    if len(drive) != 2 or drive[1] != ":":
        return False
    return bool(_GetDriveTypeW(drive + "\\") == _DRIVE_FIXED)


def _forbidden_path_char(char: str) -> bool:
    return ord(char) < 0x20 or ord(char) == 0x7F or char in _PATH_FORBIDDEN


def _request_parts(user_path: object) -> tuple[str, ...]:
    if type(user_path) is not str or not 1 <= len(user_path) <= _MAX_REQUEST_CHARS:
        raise SafePathError("PATH_INVALID")
    try:
        user_path.encode("utf-8")
    except UnicodeEncodeError:
        raise SafePathError("PATH_INVALID") from None
    parts = tuple(user_path.split("/"))
    for part in parts:
        if (
            not part
            or part in (".", "..")
            or part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in RESERVED_DEVICE_NAMES
            or any(_forbidden_path_char(char) for char in part)
        ):
            raise SafePathError("PATH_INVALID")
    return parts
