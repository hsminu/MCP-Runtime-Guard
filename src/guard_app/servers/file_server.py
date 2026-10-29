"""읽기 전용 파일 MCP 서버(C2.11·C2.4, 2026-10-15 결정). stdio 줄 단위 JSON-RPC, 표준 라이브러리만.

로컬 파일은 이 서버를 Guard에 등록해서만 읽는다. `python -I -m guard_app.servers.file_server
--root <절대 경로>`로 C2.6 실행 입구가 띄운다. 설계 규칙상 `mcp` SDK는 A transport와 fixture만
쓰므로 여기서는 MCP의 필요한 부분(`initialize`·`ping`·`tools/list`·`tools/call`)만 직접 처리한다.

도구는 셋이고 모두 READ다.

- `list_root()`: 루트 바로 아래 목록. 도구 단위 READ라 자기 출처 규칙(`prefix: []`)으로 등급이
  정해진다. B Catalog는 일치 규칙의 최고 등급을 쓰므로 루트 규칙을 `list_folder`에 걸면 모든 목록의
  하한이 된다. 그래서 따로 둔다.
- `list_folder(path)`·`read_file(path)`: 자원형 READ(`resource_arg: path`). 결과의
  `structuredContent.resource_id`는 C2.3 `safe_path`로 계산한 루트 기준 POSIX ID이고, Guard가
  요청 인자로 계산한 기대 ID와 대조한다(T33).

`read_file`은 UTF-8 텍스트만 돌려준다(BOM은 뗀다). 192KiB를 넘거나(B 응답 게이트 256KiB 안에
들도록) 디코딩되지 않거나 NUL이 있으면 고정 코드 오류다. 목록은 `safe_path`가 받는 이름만
보여 주고 1,000개까지다.

검사와 열기 사이의 경쟁(C2.4): `safe_path` 뒤에 링크를 따라가지 않는 핸들로 열고, 핸들의 실제
경로가 검사한 경로와 같고 reparse point가 아니며 파일이면 하드링크가 하나일 때만 읽는다. 파일은
쓰기 금지로, 폴더는 삭제·이름 변경 금지로 연 채 읽거나 목록을 만든다.

오류는 고정 코드이고 요청 경로나 파일 내용을 싣지 않는다. 표준 출력에는 JSON-RPC 응답만 쓴다.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from guard_app.security.paths import SafePathError, real_root, safe_path

FILE_SERVER_MODULE = "guard_app.servers.file_server"
SERVER_NAME = "guard-file-server"
SERVER_VERSION = "1"
LIST_ROOT = "list_root"
LIST_FOLDER = "list_folder"
READ_FILE = "read_file"
MAX_TEXT_BYTES = 192 * 1024
MAX_ENTRIES = 1000
MAX_SCANNED_ENTRIES = 10_000
MAX_REQUEST_BYTES = 64 * 1024
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_UTF8_BOM = b"\xef\xbb\xbf"

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_PATH_INPUT = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "Path inside the shared folder, relative, separated by '/'.",
        }
    },
    "required": ["path"],
    "additionalProperties": False,
}
TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": LIST_ROOT,
        "description": "List the folders and files at the top of the shared folder.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _READ_ONLY,
    },
    {
        "name": LIST_FOLDER,
        "description": "List the folders and files in a folder of the shared folder.",
        "inputSchema": _PATH_INPUT,
        "annotations": _READ_ONLY,
    },
    {
        "name": READ_FILE,
        "description": "Read a UTF-8 text file (up to 192 KiB) in the shared folder.",
        "inputSchema": _PATH_INPUT,
        "annotations": _READ_ONLY,
    },
)
# 설정 레코드의 `tools`(B `ServerSettings`). 등록 화면(A)이 레코드를 만들 때 쓴다.
FILE_SERVER_TOOL_SETTINGS: tuple[dict[str, str], ...] = (
    {"name": LIST_ROOT, "kind": "read"},
    {"name": LIST_FOLDER, "kind": "read", "resource_arg": "path"},
    {"name": READ_FILE, "kind": "read", "resource_arg": "path"},
)

_GENERIC_READ = 0x80000000
_FILE_LIST_DIRECTORY = 0x0001
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_MAX_PATH_CHARS = 32768

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _HandleInfo(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
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

    _CreateFileW = _api(
        "CreateFileW",
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _CloseHandle = _api("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    _GetFinalPathNameByHandleW = _api(
        "GetFinalPathNameByHandleW",
        wintypes.DWORD,
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _GetFileInformationByHandle = _api(
        "GetFileInformationByHandle", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_HandleInfo)
    )
    _ReadFile = _api(
        "ReadFile",
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )


class FileToolError(ValueError):
    """도구 결과 오류의 고정 코드. 경로·내용을 싣지 않는다."""


def file_server_args(root: Path) -> tuple[str, ...]:
    """설정 레코드의 `args`. 루트는 실제 경로로 푼 절대 경로다(`file_server_root`가 다시 읽는다)."""
    return ("-I", "-m", FILE_SERVER_MODULE, "--root", str(real_root(root)))


def file_server_root(args: Sequence[str]) -> Path | None:
    """설정 레코드의 `args`가 이 서버의 형식이면 그 루트, 아니면 None이다.

    형식만 본다. 이 서버로 인정해 자원형 출처 규칙을 쓸지는 `trusted_file_server_root`로 정한다.
    서버와 Guard가 같은 레코드에서 같은 루트를 읽는다.
    """
    if type(args) not in (tuple, list) or len(args) != 5:
        return None
    if tuple(args[:4]) != ("-I", "-m", FILE_SERVER_MODULE, "--root") or type(args[4]) is not str:
        return None
    root = Path(args[4])
    return root if root.is_absolute() else None


def trusted_file_server_root(
    transport: object, command: object, args: Sequence[str]
) -> Path | None:
    """이 앱이 띄우는 파일 서버의 설정 레코드면 그 루트, 아니면 None이다(#94 3번, C2.11-2).

    자원형 출처 규칙(비어 있지 않은 prefix)은 Guard가 기대 자원 ID를 계산하고 신뢰 코드가 응답
    ID를 만드는 서버에만 쓴다. 그래서 args 형식에 더해 stdio이고 `command`가 실행 중인 앱의
    Python(`sys.executable`)일 때만 인정한다. A는 이 값으로 C2.3
    `file_resource_resolver({서버 ID: 루트})`를 만든다.

    비교는 `os.path.normcase` 문자열 비교이고 파일 시스템을 읽지 않는다. 같은 파일을 다른
    표기(짧은 이름·정션 등)로 적으면 인정하지 않는다. 그 서버는 일반 서버가 되어 자원형 READ가
    예약 전에 거부되는 닫힌 쪽이다. 실행 파일 내용은 EXE-01 지문으로 실행마다 대조한다.
    """
    if transport != "stdio" or type(command) is not str or not command:
        return None
    if os.path.normcase(command) != os.path.normcase(sys.executable):
        return None
    return file_server_root(args)


class FileServer:
    """루트 하나를 읽기 전용으로 내보내는 MCP 서버. `handle_line`은 응답 한 줄이나 None이다."""

    def __init__(self, root: Path) -> None:
        self._root = real_root(root)

    def handle_line(self, line: bytes) -> bytes | None:
        text = line.strip()
        if not text:
            return None
        try:
            message = json.loads(text, parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            return _encode(_error(None, -32700, "Parse error"))
        response = self.handle(message)
        return None if response is None else _encode(response)

    def handle(self, message: object) -> dict[str, Any] | None:
        if type(message) is not dict or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Invalid Request")
        method = message.get("method")
        if type(method) is not str:
            return _error(None, -32600, "Invalid Request")
        if "id" not in message:
            return None  # 알림(notifications/initialized 등)에는 답하지 않는다.
        request_id = message["id"]
        if type(request_id) not in (str, int):
            return _error(None, -32600, "Invalid Request")
        params = message.get("params", {})
        if type(params) is not dict:
            return _error(request_id, -32602, "Invalid params")
        if method == "initialize":
            return _result(request_id, self._initialize(params))
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": list(TOOLS)})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if type(name) is not str or name not in {tool["name"] for tool in TOOLS}:
                return _error(request_id, -32602, "Unknown tool")
            return _result(request_id, self._call(name, arguments))
        return _error(request_id, -32601, "Method not found")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _call(self, name: str, arguments: object) -> dict[str, Any]:
        try:
            if type(arguments) is not dict:
                raise FileToolError("ARGUMENT_INVALID")
            if name == LIST_ROOT:
                if arguments:
                    raise FileToolError("ARGUMENT_INVALID")
                return self._list(self._root, "")
            if set(arguments) != {"path"} or type(arguments["path"]) is not str:
                raise FileToolError("ARGUMENT_INVALID")
            target = safe_path(self._root, arguments["path"])
            resource_id = "/".join(target.relative_to(self._root).parts)
            if name == LIST_FOLDER:
                if not target.is_dir():
                    raise FileToolError("PATH_NOT_FOLDER")
                return self._list(target, resource_id)
            if target.is_dir():
                raise FileToolError("PATH_NOT_FILE")
            return self._read(target, resource_id)
        except (FileToolError, SafePathError) as error:
            return _tool_error(str(error))
        except OSError:
            # 예상 밖 OS 오류도 고정 코드로 답하고 서버는 계속 돈다(B77-D1). OS 메시지는 경로를
            # 담을 수 있어 싣지 않는다.
            return _tool_error("FILE_READ_FAILED" if name == READ_FILE else "FOLDER_READ_FAILED")

    def _read(self, target: Path, resource_id: str) -> dict[str, Any]:
        with _opened(target, directory=False) as handle:
            data = _read_handle(handle, MAX_TEXT_BYTES + len(_UTF8_BOM) + 1)
        if data.startswith(_UTF8_BOM):
            data = data[len(_UTF8_BOM) :]
        if len(data) > MAX_TEXT_BYTES:
            raise FileToolError("FILE_TOO_LARGE")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileToolError("FILE_NOT_TEXT") from None
        if "\0" in text:
            raise FileToolError("FILE_NOT_TEXT")
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"resource_id": resource_id, "bytes": len(data)},
            "isError": False,
        }

    def _list(self, folder: Path, resource_id: str) -> dict[str, Any]:
        entries: list[dict[str, str]] = []
        truncated = False
        with _opened(folder, directory=True):
            try:
                with os.scandir(folder) as scan:
                    for scanned, entry in enumerate(scan):
                        if scanned >= MAX_SCANNED_ENTRIES:
                            truncated = True
                            break
                        child = f"{resource_id}/{entry.name}" if resource_id else entry.name
                        try:
                            path = safe_path(self._root, child)
                        except SafePathError:
                            continue  # 링크·하드링크·쓸 수 없는 이름은 보이지 않는다.
                        entries.append(
                            {"name": path.name, "kind": "folder" if path.is_dir() else "file"}
                        )
            except OSError:
                raise FileToolError("FOLDER_READ_FAILED") from None
        entries.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        if len(entries) > MAX_ENTRIES:
            entries, truncated = entries[:MAX_ENTRIES], True
        lines = [item["name"] + ("/" if item["kind"] == "folder" else "") for item in entries]
        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
            "structuredContent": {
                "resource_id": resource_id,
                "entries": entries,
                "truncated": truncated,
            },
            "isError": False,
        }


@contextmanager
def _opened(path: Path, *, directory: bool) -> Iterator[int]:
    """링크를 따라가지 않고 열어, 핸들이 가리키는 것이 검사한 그 경로인지 대조한다(C2.4).

    파일은 쓰기 금지, 폴더는 삭제·이름 변경 금지로 연다. 대조에 실패하면 `FILE_CHANGED`다.
    """
    if directory:
        access = _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES
        share = _FILE_SHARE_READ | _FILE_SHARE_WRITE
        flags = _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS
    else:
        access = _GENERIC_READ
        share = _FILE_SHARE_READ
        flags = _FILE_FLAG_OPEN_REPARSE_POINT
    handle = _CreateFileW(str(path), access, share, None, _OPEN_EXISTING, flags, None)
    if handle is None or handle == _INVALID_HANDLE:
        raise FileToolError("FILE_OPEN_FAILED")
    try:
        info = _HandleInfo()
        if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise FileToolError("FILE_OPEN_FAILED")
        is_directory = bool(info.dwFileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
        if (
            info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or is_directory != directory
            or (not directory and info.nNumberOfLinks != 1)
            or os.path.normcase(_final_path(handle)) != os.path.normcase(str(path))
        ):
            raise FileToolError("FILE_CHANGED")
        yield handle
    finally:
        _CloseHandle(handle)


def _read_handle(handle: int, limit: int) -> bytes:
    """핸들에서 `limit` 바이트까지 읽는다(B77-D1).

    CRT fd(`msvcrt.open_osfhandle`)를 만들지 않는다. 그 fd는 핸들을 닫아도 풀리지 않아 읽을
    때마다 하나씩 남았고, 8,189번쯤 뒤에는 열기가 실패했다. 핸들을 닫는 것은 `_opened` 하나다.
    """
    buffer = ctypes.create_string_buffer(limit)
    done = wintypes.DWORD()
    total = 0
    while total < limit:
        if not _ReadFile(
            handle, ctypes.byref(buffer, total), limit - total, ctypes.byref(done), None
        ):
            raise FileToolError("FILE_READ_FAILED")
        if done.value == 0:
            break
        total += done.value
    return buffer.raw[:total]


def _final_path(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
    length = _GetFinalPathNameByHandleW(handle, buffer, _MAX_PATH_CHARS, 0)
    if not 0 < length < _MAX_PATH_CHARS:
        raise FileToolError("FILE_CHANGED")
    name = buffer.value
    unc, local = "\\\\?\\UNC\\", "\\\\?\\"
    if name.startswith(unc):
        return "\\\\" + name[len(unc) :]
    return name[len(local) :] if name.startswith(local) else name


def _reject_constant(value: str) -> object:
    raise ValueError("JSON constant")


def _tool_error(code: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": code}], "isError": True}


def _result(request_id: str | int, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _encode(response: dict[str, Any]) -> bytes:
    return (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def serve(root: Path, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """입력이 끝날 때까지 한 줄에 요청 하나를 처리한다. 너무 긴 줄은 버리고 오류 하나로 답한다."""
    server = FileServer(root)
    while True:
        line = stdin.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_REQUEST_BYTES:
            while line and not line.endswith(b"\n"):
                line = stdin.readline(MAX_REQUEST_BYTES)
            response: bytes | None = _encode(_error(None, -32600, "Request too large"))
        else:
            response = server.handle_line(line)
        if response is not None:
            stdout.write(response)
            stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--root":
        sys.stderr.write("FILE_SERVER_USAGE\n")
        return 2
    try:
        root = real_root(Path(args[1]))
    except SafePathError:
        sys.stderr.write("FILE_SERVER_ROOT_INVALID\n")
        return 2
    return serve(root, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
