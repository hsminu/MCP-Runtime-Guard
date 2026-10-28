"""stdio MCP 서버 실행(C2.6, EXE-01) 단위 테스트. 서버 대신 이 Python의 python.exe를 띄운다.

실행 핵심(Job·이미지 대조·실패 정리)은 test_launcher가 본다. 여기서는 설정 레코드 값 검사, 등록
지문, 서버 폴더·환경·stderr, 지문 대조, A transport로 tools/list까지 이어지는지를 본다.
"""

import _winapi
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

from guard_app.adapters.mcp_transport import (
    McpTransport,
    ToolListLimits,
    validated_stdio_stream_factory,
)
from guard_app.security.launcher import (
    MAX_SERVER_ARG_CHARS,
    MAX_SERVER_ARGS,
    MAX_SERVER_ENV_ALLOW,
    MCP_SERVER_ENV_BASE,
    MCP_SERVER_TEMP_DIR,
    ExecutablePin,
    LaunchError,
    StdioServerValues,
    check_stdio_values,
    launch_mcp_server,
    pin_executable,
)
from guard_app.security.paths import AppPathError, prepare_app_folders
from guard_app.services.registry import listing_fingerprints

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="launcher는 Windows 전용이다")

PYTHON = Path(sys.base_prefix) / "python.exe"
MARKER = "mcp-launch-test-planted-marker"
ALLOWED_VALUE = "env-allow-passed-value"
STDERR_NAME = re.compile(r"stderr-[0-9a-f]{32}\.log")

DUMP_ENV = (
    "import json, os, sys\n"
    "print(json.dumps({'env': dict(os.environ), 'cwd': os.getcwd()}), flush=True)\n"
    "sys.stdin.read()\n"
)

# 표준 라이브러리만 쓰는 최소 MCP 서버(줄 단위 JSON-RPC). 요청한 프로토콜 버전을 그대로 답한다.
MCP_SERVER = (
    "import json, sys\n"
    "TOOLS = [{'name': 'read', 'description': 'Read a document',"
    " 'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}]\n"
    "for raw in sys.stdin.buffer:\n"
    "    message = json.loads(raw)\n"
    "    if 'id' not in message:\n"
    "        continue\n"
    "    method = message.get('method')\n"
    "    if method == 'initialize':\n"
    "        result = {'protocolVersion': message['params']['protocolVersion'],"
    " 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'c26', 'version': '1'}}\n"
    "    elif method == 'tools/list':\n"
    "        result = {'tools': TOOLS}\n"
    "    else:\n"
    "        result = {}\n"
    "    reply = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}\n"
    "    sys.stdout.buffer.write(json.dumps(reply).encode() + b'\\n')\n"
    "    sys.stdout.buffer.flush()\n"
)


@pytest.fixture(scope="module")
def pin():
    data = PYTHON.read_bytes()
    return ExecutablePin(len(data), hashlib.sha256(data).hexdigest())


@pytest.fixture
def folders(tmp_path):
    prepared = prepare_app_folders(tmp_path / "app")
    yield prepared
    prepared.close()


def _code(name: str) -> str:
    return str(PYTHON) if name == "python" else name


def _rejected(code, *values):
    with pytest.raises(LaunchError) as raised:
        check_stdio_values(*values)
    assert raised.value.args == (code,)


async def _read_line(launched):
    buffer = b""
    while b"\n" not in buffer:
        chunk = await asyncio.wait_for(launched.read_bytes(4096), 10)
        assert chunk, "EOF before a line"
        buffer += chunk
    return buffer.split(b"\n", 1)[0].rstrip(b"\r")


# 실행 값 검사


def test_values_pass_and_come_back_as_tuples():
    values = check_stdio_values(str(PYTHON), ["-I", "-c", "pass"], ["PATH", "my_token"])
    assert values == StdioServerValues(PYTHON, ("-I", "-c", "pass"), ("PATH", "my_token"))
    forward = check_stdio_values(str(PYTHON).replace("\\", "/"), (), ())
    assert forward.command == PYTHON


@pytest.mark.parametrize(
    "command",
    [
        None,
        b"C:\\python.exe",
        "",
        "python.exe",
        "C:python.exe",
        "C:\\",
        "\\python.exe",
        "\\\\server\\share\\python.exe",
        "\\\\?\\C:\\python.exe",
        "\\\\.\\C:\\python.exe",
        "C:\\a\\..\\python.exe",
        "C:\\a\\.\\python.exe",
        "C:\\a\\\\python.exe",
        "C:\\a\\python.exe.",
        "C:\\a\\python.exe ",
        "C:\\a\\python.exe:stream",
        "C:\\a\\file:python.exe",
        "C:\\a\\con.exe",
        "C:\\a\\NUL",
        "C:\\a\\COM1.exe",
        "C:\\a\\py?.exe",
        "C:\\a\\py*.exe",
        'C:\\a\\"py".exe',
        "C:\\a\\py\x01.exe",
        "C:\\a\\\ud800.exe",
        pytest.param("C:\\a\\" + "a" * 40000 + ".exe", id="too-long"),
        "C:\\Windows\\System32\\cmd.bat",
    ],
)
def test_command_must_be_a_plain_absolute_exe_path(command):
    _rejected("LAUNCH_EXECUTABLE_INVALID", command, (), ())


def test_command_file_must_exist_as_a_regular_file(tmp_path):
    (tmp_path / "tool.bat").write_bytes(b"@echo off")
    (tmp_path / "folder.exe").mkdir()
    target = tmp_path / "target"
    target.mkdir()
    _winapi.CreateJunction(str(target), str(tmp_path / "junction.exe"))
    for name in ("missing.exe", "tool.bat", "folder.exe", "junction.exe"):
        _rejected("LAUNCH_EXECUTABLE_INVALID", str(tmp_path / name), (), ())
    link = tmp_path / "link.exe"
    try:
        link.symlink_to(PYTHON)
    except OSError:
        return  # 심볼릭 링크 권한이 없는 환경
    _rejected("LAUNCH_EXECUTABLE_INVALID", str(link), (), ())


@pytest.mark.parametrize(
    "args",
    [
        "-I -c pass",
        ("-I", 1),
        ("a\0b",),
        ("\ud800",),
        ("x",) * (MAX_SERVER_ARGS + 1),
        ("a" * (MAX_SERVER_ARG_CHARS + 1),),
        ("a" * 4000,) * 9,  # 명령줄 32,767자 초과
    ],
    ids=["string", "not-str", "nul", "surrogate", "count", "length", "command-line"],
)
def test_args_are_bounded(args):
    _rejected("LAUNCH_ARGS_INVALID", str(PYTHON), args, ())


def test_args_at_the_bounds_pass():
    assert len(check_stdio_values(str(PYTHON), ("x",) * MAX_SERVER_ARGS, ()).args) == 256
    longest = ("a" * MAX_SERVER_ARG_CHARS,)
    assert check_stdio_values(str(PYTHON), longest, ()).args == longest
    # CR·LF·따옴표·빈 문자열은 셸 없이 그대로 전달되므로 막지 않는다.
    assert check_stdio_values(str(PYTHON), ("", "a\nb", 'q"'), ()).args == ("", "a\nb", 'q"')


@pytest.mark.parametrize(
    "env_allow",
    [
        "PATH",
        ("",),
        ("1BAD",),
        ("A-B",),
        ("A B",),
        ("PATH", "path"),
        ("SystemRoot",),
        ("temp",),
        ("TMP",),
        ("USERPROFILE",),
        tuple(f"V{number}" for number in range(MAX_SERVER_ENV_ALLOW + 1)),
    ],
)
def test_env_allow_names_are_checked(env_allow):
    _rejected("LAUNCH_ENV_ALLOW_INVALID", str(PYTHON), (), env_allow)


# 등록 지문


@pytest.mark.asyncio
async def test_pin_is_the_size_and_sha256_of_the_file(pin):
    assert await pin_executable(str(PYTHON)) == pin


@pytest.mark.asyncio
async def test_pin_refuses_empty_and_open_for_writing_files(tmp_path):
    empty = tmp_path / "empty.exe"
    empty.write_bytes(b"")
    with pytest.raises(LaunchError) as raised:
        await pin_executable(str(empty))
    assert str(raised.value) == "LAUNCH_EXECUTABLE_INVALID"
    busy = tmp_path / "busy.exe"
    busy.write_bytes(b"MZ" + b"\0" * 64)
    # 다른 쪽이 쓰기 핸들을 쥐고 있으면 지문을 만들지 않는다
    with busy.open("r+b"), pytest.raises(LaunchError) as raised:
        await pin_executable(str(busy))
    assert str(raised.value) == "LAUNCH_EXECUTABLE_INVALID"
    with pytest.raises(LaunchError) as raised:
        await pin_executable("python.exe")
    assert str(raised.value) == "LAUNCH_EXECUTABLE_INVALID"


# 실행


@pytest.mark.asyncio
async def test_server_runs_in_its_folder_with_only_the_allowed_environment(
    folders, pin, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", MARKER)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("PATH", MARKER)
    monkeypatch.setenv("MY_TOKEN", ALLOWED_VALUE)
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    folder = folders.server_folder("docs")
    launched = await launch_mcp_server(
        folder, str(PYTHON), ("-I", "-c", DUMP_ENV), ("MY_TOKEN", "NOT_SET_ANYWHERE"), pin
    )
    seen = json.loads(await _read_line(launched))
    assert await launched.close() is True
    env = {name.upper(): value for name, value in seen["env"].items()}
    expected = {name for name in MCP_SERVER_ENV_BASE if name in os.environ}
    assert set(env) == expected | {"TEMP", "TMP", "MY_TOKEN"}
    assert env["MY_TOKEN"] == ALLOWED_VALUE
    assert env["TEMP"] == env["TMP"] == str(folder / MCP_SERVER_TEMP_DIR)
    assert Path(seen["cwd"]) == folder
    assert MARKER not in json.dumps(seen)


@pytest.mark.asyncio
async def test_each_connection_gets_its_own_isolated_stderr(folders, pin, capfd):
    folder = folders.server_folder("docs")
    code = f"import sys; sys.stderr.write('{MARKER}'); sys.stderr.flush(); sys.stdin.read()"
    first = await launch_mcp_server(folder, str(PYTHON), ("-I", "-c", code), (), pin)
    second = await launch_mcp_server(folder, str(PYTHON), ("-I", "-c", code), (), pin)
    assert await first.close() is True
    assert await second.close() is True
    logs = [entry for entry in folder.iterdir() if entry.is_file()]
    assert len(logs) == 2
    assert all(STDERR_NAME.fullmatch(log.name) for log in logs)
    assert all(MARKER in log.read_text(encoding="utf-8") for log in logs)
    captured = capfd.readouterr()
    assert MARKER not in captured.err + captured.out


@pytest.mark.asyncio
async def test_unpinned_server_is_not_started(folders):
    folder = folders.server_folder("docs")
    with pytest.raises(LaunchError) as raised:
        await launch_mcp_server(folder, str(PYTHON), ("-I", "-c", "pass"), (), None)
    assert str(raised.value) == "LAUNCH_EXECUTABLE_UNPINNED"
    assert list(folder.iterdir()) == []


@pytest.mark.asyncio
async def test_changed_executable_is_not_started(folders, tmp_path):
    """등록 뒤 실행 파일이 바뀌면 띄우지 않는다. 바뀐 파일은 실행되지 않는다(해시가 먼저다)."""
    tool = tmp_path / "tool.exe"
    tool.write_bytes(b"MZ" + b"\0" * 64)
    registered = await pin_executable(str(tool))
    tool.write_bytes(b"MZ" + b"\1" * 64)
    folder = folders.server_folder("docs")
    with pytest.raises(LaunchError) as raised:
        await launch_mcp_server(folder, str(tool), (), (), registered)
    assert str(raised.value) == "LAUNCH_EXECUTABLE_MISMATCH"
    assert not any(entry.name.startswith("stderr-") for entry in folder.iterdir())


@pytest.mark.asyncio
async def test_values_are_checked_again_at_each_connection(folders, pin, tmp_path):
    folder = folders.server_folder("docs")
    cases = [
        ((folder, "python.exe", (), (), pin), "LAUNCH_EXECUTABLE_INVALID"),
        ((folder, str(PYTHON), ("a\0",), (), pin), "LAUNCH_ARGS_INVALID"),
        ((folder, str(PYTHON), (), ("TEMP",), pin), "LAUNCH_ENV_ALLOW_INVALID"),
        ((folder, str(PYTHON), (), (), (pin.size, pin.sha256)), "LAUNCH_SPEC_INVALID"),
        ((Path("relative"), str(PYTHON), (), (), pin), "LAUNCH_SERVER_DIR_INVALID"),
        ((tmp_path / "missing", str(PYTHON), (), (), pin), "LAUNCH_SERVER_DIR_INVALID"),
    ]
    for arguments, code in cases:
        with pytest.raises(LaunchError) as raised:
            await launch_mcp_server(*arguments)
        assert str(raised.value) == code


@pytest.mark.asyncio
async def test_tools_list_reaches_the_registry_through_a_transport(folders, pin):
    """C 실행 입구 → A `validated_stdio_stream_factory`·McpTransport → C 지문까지 한 번에."""
    launched = await launch_mcp_server(
        folders.server_folder("docs"), str(PYTHON), ("-I", "-c", MCP_SERVER), (), pin
    )
    transport = McpTransport(
        {
            "docs": validated_stdio_stream_factory(
                launched.read_bytes, launched.write_bytes, launched.drain
            )
        },
        tool_list_limits=ToolListLimits(),
    )
    try:
        await transport.start()
        listing = transport.tool_listing("docs")
    finally:
        await transport.close()
        assert await launched.close() is True
    assert listing is not None
    assert listing.complete
    assert [tool.get("name") for tool in listing.tools] == ["read"]
    assert set(listing_fingerprints(listing)) == {"read"}


# 서버 폴더


def test_server_folder_name_is_a_digest_of_the_id(folders):
    servers = folders.paths.servers
    docs = folders.server_folder("docs")
    assert docs == servers / hashlib.sha256(b"docs").hexdigest()[:32]
    assert folders.server_folder("docs") == docs
    for server_id in ("../escape", "a\\b", "C:", "con", "이름 있는 서버"):
        folder = folders.server_folder(server_id)
        assert folder.parent == servers
        assert re.fullmatch(r"[0-9a-f]{32}", folder.name)
        assert folder != docs


def test_server_folder_refuses_bad_ids_links_and_closed_folders(folders, tmp_path):
    for server_id in ("", 1, None, "\ud800"):
        with pytest.raises(AppPathError) as raised:
            folders.server_folder(server_id)
        assert str(raised.value) == "SERVER_ID_INVALID"
    target = tmp_path / "elsewhere"
    target.mkdir()
    name = hashlib.sha256(b"linked").hexdigest()[:32]
    _winapi.CreateJunction(str(target), str(folders.paths.servers / name))
    with pytest.raises(AppPathError) as raised:
        folders.server_folder("linked")
    assert str(raised.value) == "SERVER_FOLDER_INVALID"
    folders.close()
    with pytest.raises(AppPathError) as raised:
        folders.server_folder("docs")
    assert str(raised.value) == "APP_FOLDERS_CLOSED"


def test_next_start_removes_server_folders(tmp_path):
    first = prepare_app_folders(tmp_path / "app")
    folder = first.server_folder("docs")
    (folder / "stderr-0.log").write_text(MARKER, encoding="utf-8")
    (folder / MCP_SERVER_TEMP_DIR).mkdir()
    first.server_folder("other")
    first.close()
    second = prepare_app_folders(tmp_path / "app")
    try:
        assert second.stale_server_folders_removed == 2
        assert list(second.paths.servers.iterdir()) == []
    finally:
        second.close()
