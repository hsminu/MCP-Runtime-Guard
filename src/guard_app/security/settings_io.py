"""설정 파일 읽기와 원자적 교체(C2.14, CFG-01 #33). 오류에 경로·내용을 싣지 않는다.

설정은 `%LOCALAPPDATA%\\MCPRuntimeGuard\\config\\settings.json` 파일 하나다(C2.13
`AppPaths.settings_file`). 여기서는 바이트만 다룬다. 해석과 직렬화는 B의
`parse_settings_bytes`·`serialize_settings_bytes`(B3.11)가 맡고, 크기 상한은 호출자가 B의
`SETTINGS_MAX_BYTES`를 넘긴다. 상한을 한 곳에만 두기 위해서다.

- **읽기:** 파일이 없으면 None(첫 실행)이다. 있으면 링크를 따라가지 않고 쓰기·이름 변경·삭제를
  막는 핸들로 연다. 하드링크가 하나인 일반 파일이고 권한이 사용자·SYSTEM뿐인지 본 뒤 상한+1바이트
  까지만 읽는다. 초과 판정은 B가 한다.
- **교체:** 읽을 때 받은 세대(크기·SHA-256)가 지금 파일과 같을 때만 쓴다. 앱 실행 중 누가 파일을
  고쳤으면 덮어쓰지 않는다(직접 편집은 앱을 닫은 상태에서만, CFG-01). 같은 폴더에 임시 파일을
  배타 생성해 쓰고 fsync한 뒤 `MoveFileExW(WRITE_THROUGH)`로 바꾼다. 실패는 파일이 그대로인
  `SETTINGS_FILE_WRITE_FAILED`와 바뀌었을 수도 있는 `SETTINGS_FILE_WRITE_UNKNOWN`으로 나눈다.
  남은 임시 파일은 다음 앱 시작 때 `prepare_app_folders`가 지운다.

모두 블로킹 파일 연산이므로 이벤트 루프에서는 `asyncio.to_thread`로 부른다. Windows 전용이다.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from guard_app.security.paths import SETTINGS_FILE, AppFolders, AppPathError, is_private_file

_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5
_READ_CHUNK = 1 << 16

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _FileInformation(ctypes.Structure):  # BY_HANDLE_FILE_INFORMATION
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
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
    _GetFileInformationByHandle = _api(
        "GetFileInformationByHandle", _BOOL, _HANDLE, ctypes.POINTER(_FileInformation)
    )
    _MoveFileExW = _api("MoveFileExW", _BOOL, wintypes.LPCWSTR, wintypes.LPCWSTR, _DWORD)


class SettingsIOError(RuntimeError):
    """경로·내용을 공개하지 않는 고정 오류 코드."""


@dataclass(frozen=True, slots=True)
class SettingsGeneration:
    """읽거나 쓴 설정 파일 내용의 표지. 교체할 때 지금 파일과 같은지 대조한다."""

    size: int
    sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class SettingsSnapshot:
    """읽은 바이트(상한+1바이트까지)와 그 세대. repr에 내용을 싣지 않는다."""

    data: bytes
    generation: SettingsGeneration

    def __repr__(self) -> str:
        return "SettingsSnapshot(<redacted>)"


def read_settings_file(folders: AppFolders, *, max_bytes: int) -> SettingsSnapshot | None:
    """설정 파일을 읽는다. 없으면 None이다.

    링크·폴더·하드링크가 둘 이상인 파일·사용자와 SYSTEM 밖에 권한이 있는 파일은
    `SETTINGS_FILE_INVALID`, 그 밖의 열기·읽기 실패는 `SETTINGS_FILE_READ_FAILED`다. 둘 다 A가
    "손상"으로 다룬다(앱 시작 거부, 파일 덮어쓰지 않음, #33).
    """
    _check_call(folders, max_bytes)
    return _read(folders.paths.settings_file, max_bytes)


def replace_settings_file(
    folders: AppFolders,
    data: bytes,
    *,
    expected: SettingsGeneration | None,
    max_bytes: int,
) -> SettingsGeneration:
    """`data`로 설정 파일을 바꾸고 새 세대를 돌려준다.

    `expected`는 마지막으로 읽거나 쓴 세대다. 첫 저장(파일 없음)이면 None이다. 지금 파일이 그와
    다르면 쓰지 않고 `SETTINGS_FILE_CHANGED`다. 교체가 확실히 일어나지 않았으면
    `SETTINGS_FILE_WRITE_FAILED`(A는 적용하지 않음), 일어났을 수도 있으면
    `SETTINGS_FILE_WRITE_UNKNOWN`(A는 재시작을 요구)이다. 대조하려고 지금 파일을 읽다가
    `SETTINGS_FILE_INVALID`·`SETTINGS_FILE_READ_FAILED`가 날 수도 있다(쓰지 않음).

    호출자가 한 번에 하나씩 부르도록 직렬화한다. 같은 `expected`로 동시에 부르면 둘 다 대조를
    통과해 둘 다 성공 세대를 돌려주고, 디스크에는 나중 것만 남는다(#44 B 리뷰 2번). A는
    정책 잠금(`TaskPolicy.lock`) 안에서 부른다.
    """
    _check_call(folders, max_bytes)
    if (
        type(data) is not bytes
        or not data
        or len(data) > max_bytes
        or (expected is not None and type(expected) is not SettingsGeneration)
    ):
        raise SettingsIOError("SETTINGS_FILE_DATA_INVALID")
    target = folders.paths.settings_file
    before = _generation_at(target, max_bytes)
    if before != expected:
        raise SettingsIOError("SETTINGS_FILE_CHANGED")
    written = _generation(len(data), data)
    temporary = target.with_name(f"{SETTINGS_FILE}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        _discard(temporary)
        raise SettingsIOError("SETTINGS_FILE_WRITE_FAILED") from None
    # 첫 저장은 같은 이름이 생겼으면 덮어쓰지 않는다. 그 사이에 생긴 파일은 아래에서 CHANGED가 된다.
    flags = _MOVEFILE_WRITE_THROUGH | (0 if expected is None else _MOVEFILE_REPLACE_EXISTING)
    if _MoveFileExW(str(temporary), str(target), flags):
        return written
    _discard(temporary)
    raise SettingsIOError(_failed_move_outcome(target, before, written, max_bytes))


def _check_call(folders: AppFolders, max_bytes: int) -> None:
    if sys.platform != "win32":
        raise SettingsIOError("SETTINGS_FILE_UNSUPPORTED")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise SettingsIOError("SETTINGS_FILE_LIMIT_INVALID")
    folders.require_open()


def _read(path: Path, max_bytes: int) -> SettingsSnapshot | None:
    handle = _CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        error = ctypes.get_last_error()
        if error == _ERROR_FILE_NOT_FOUND:
            return None
        if error == _ERROR_ACCESS_DENIED:
            # 같은 이름의 폴더는 이 플래그로 열 수 없어 접근 거부가 된다.
            raise SettingsIOError("SETTINGS_FILE_INVALID")
        raise SettingsIOError("SETTINGS_FILE_READ_FAILED")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        _CloseHandle(handle)
        raise SettingsIOError("SETTINGS_FILE_READ_FAILED") from None
    with open(descriptor, "rb", closefd=True) as file:
        size = _checked_size(path, file)
        data = _read_limited(file, max_bytes + 1)
    return SettingsSnapshot(data, _generation(size, data))


def _checked_size(path: Path, file: BinaryIO) -> int:
    """핸들로 종류·하드링크 수를, 경로로 DACL을 본다. 핸들이 이름 변경·삭제를 막고 있다."""
    info = _FileInformation()
    if not _GetFileInformationByHandle(msvcrt.get_osfhandle(file.fileno()), ctypes.byref(info)):
        raise SettingsIOError("SETTINGS_FILE_READ_FAILED")
    if (
        info.dwFileAttributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
        or info.nNumberOfLinks != 1
    ):
        raise SettingsIOError("SETTINGS_FILE_INVALID")
    try:
        private = is_private_file(path)
    except AppPathError:
        raise SettingsIOError("SETTINGS_FILE_INVALID") from None
    if not private:
        raise SettingsIOError("SETTINGS_FILE_INVALID")
    return (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)


def _read_limited(file: BinaryIO, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    try:
        while remaining > 0 and (chunk := file.read(min(_READ_CHUNK, remaining))):
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        raise SettingsIOError("SETTINGS_FILE_READ_FAILED") from None
    return b"".join(chunks)


def _generation(size: int, data: bytes) -> SettingsGeneration:
    return SettingsGeneration(size, hashlib.sha256(data).hexdigest())


def _generation_at(path: Path, max_bytes: int) -> SettingsGeneration | None:
    snapshot = _read(path, max_bytes)
    return None if snapshot is None else snapshot.generation


def _failed_move_outcome(
    target: Path,
    before: SettingsGeneration | None,
    written: SettingsGeneration,
    max_bytes: int,
) -> str:
    """교체 호출이 실패한 뒤 지금 파일을 다시 읽어, 바뀌지 않았는지 확인할 수 있을 때만 FAILED다."""
    try:
        after = _generation_at(target, max_bytes)
    except SettingsIOError:
        return "SETTINGS_FILE_WRITE_UNKNOWN"
    if after == before:
        return "SETTINGS_FILE_WRITE_FAILED"
    if after == written:
        # 새 내용이 자리에 있지만 교체가 실패로 끝나 디스크 반영을 확인하지 못했다.
        return "SETTINGS_FILE_WRITE_UNKNOWN"
    return "SETTINGS_FILE_CHANGED"


def _discard(temporary: Path) -> None:
    """임시 파일을 지운다. 못 지우면 다음 앱 시작 때 `prepare_app_folders`가 지운다."""
    with contextlib.suppress(OSError):
        temporary.unlink(missing_ok=True)
