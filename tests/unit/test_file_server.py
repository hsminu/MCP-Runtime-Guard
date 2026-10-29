"""servers.file_server(C2.11·C2.4, T33) 단위 테스트. 실제 NTFS, 끝에서 끝까지는 실제 프로세스.

프로토콜·도구는 프로세스 안에서 `FileServer.handle`로 보고, 마지막 두 테스트는 C2.6 입구로 서버를
띄워 A transport·C Registry·B Catalog까지 잇는다.
"""

import _winapi
import asyncio
import hashlib
import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard_app.adapters.mcp_transport import McpTransport, ToolListLimits
from guard_app.adapters.mcp_transport import validated_stdio_stream_factory as stdio_factory
from guard_app.domain.enums import Label, ToolKind
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.security.launcher import ExecutablePin, launch_mcp_server
from guard_app.security.paths import (
    SafePathError,
    file_resource_resolver,
    prepare_app_folders,
)
from guard_app.servers import file_server
from guard_app.servers.file_server import (
    FILE_SERVER_TOOL_SETTINGS,
    MAX_TEXT_BYTES,
    PROTOCOL_VERSIONS,
    FileServer,
    file_server_args,
    file_server_root,
    main,
    serve,
)
from guard_app.services.catalog import Catalog, ServerSettings
from guard_app.services.catalog_sources import SourceRule
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 파일 서버다")

PLANTED = "planted-secret-marker"


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "share"
    (base / "Public").mkdir(parents=True)
    (base / "Public" / "Report.TXT").write_text("hello report", encoding="utf-8")
    (base / "personal").mkdir()
    (base / "personal" / "note.md").write_text("personal note", encoding="utf-8")
    (base / "top.txt").write_text("top", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "Report.TXT").write_text(PLANTED, encoding="utf-8")
    return base


def call(server, name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    response = server.handle(
        {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}
    )
    return response["result"]


def text_of(result):
    return "".join(block["text"] for block in result["content"])


# 프로토콜


def test_initialize_echoes_a_supported_version(root):
    server = FileServer(root)
    for requested, answered in (
        ("2025-06-18", "2025-06-18"),
        ("2024-11-05", "2024-11-05"),
        ("1999-01-01", PROTOCOL_VERSIONS[0]),
    ):
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {"protocolVersion": requested, "capabilities": {}},
            }
        )
        result = response["result"]
        assert result["protocolVersion"] == answered
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["serverInfo"]["name"] == "guard-file-server"


def test_tools_list_and_ping(root):
    server = FileServer(root)
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["list_root", "list_folder", "read_file"]
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)
    assert tools[0]["inputSchema"]["properties"] == {}
    assert tools[2]["inputSchema"]["required"] == ["path"]
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


@pytest.mark.parametrize(
    ("message", "code", "has_id"),
    [
        ([], -32600, False),
        ({"id": 1, "method": "ping"}, -32600, False),
        ({"jsonrpc": "2.0", "id": 1}, -32600, False),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, -32600, False),
        ({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}, -32602, True),
        ({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, -32601, True),
        (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "rm"}},
            -32602,
            True,
        ),
    ],
    ids=["array", "no-version", "no-method", "bool-id", "params", "method", "tool"],
)
def test_protocol_errors_have_fixed_codes(root, message, code, has_id):
    response = FileServer(root).handle(message)
    assert response["error"]["code"] == code
    assert response["id"] == (1 if has_id else None)


def test_lines_that_are_not_json_are_parse_errors(root):
    server = FileServer(root)
    for line in (b"{not json}\n", b'{"jsonrpc":"2.0","id":1,"method":NaN}\n'):
        assert json.loads(server.handle_line(line))["error"]["code"] == -32700
    assert server.handle_line(b"\n") is None


def test_serve_answers_line_by_line_and_drops_oversized_requests(root):
    big = b'{"jsonrpc":"2.0","id":9,"method":"ping","pad":"' + b"x" * (70 * 1024) + b'"}\n'
    ping = b'{"jsonrpc":"2.0","id":10,"method":"ping"}\n'
    stdout = io.BytesIO()
    assert serve(root, io.BytesIO(big + ping), stdout) == 0
    first, second = (json.loads(line) for line in stdout.getvalue().splitlines())
    assert first["error"]["code"] == -32600
    assert second == {"jsonrpc": "2.0", "id": 10, "result": {}}


# 도구


def test_read_file_returns_text_and_the_disk_name_id(root):
    result = call(FileServer(root), "read_file", {"path": "public/report.txt"})
    assert result["isError"] is False
    assert text_of(result) == "hello report"
    assert result["structuredContent"] == {"resource_id": "Public/Report.TXT", "bytes": 12}


def test_read_file_limits_and_encoding(root):
    server = FileServer(root)
    folder = root / "Public"
    (folder / "max.txt").write_bytes(b"a" * MAX_TEXT_BYTES)
    (folder / "bom.txt").write_bytes(b"\xef\xbb\xbf" + b"b" * MAX_TEXT_BYTES)
    (folder / "big.txt").write_bytes(b"c" * (MAX_TEXT_BYTES + 1))
    (folder / "latin.txt").write_bytes(b"caf\xe9")
    (folder / "zero.txt").write_bytes(b"a\x00b")
    assert call(server, "read_file", {"path": "Public/max.txt"})["structuredContent"]["bytes"] == (
        MAX_TEXT_BYTES
    )
    bom = call(server, "read_file", {"path": "Public/bom.txt"})
    assert text_of(bom) == "b" * MAX_TEXT_BYTES
    for name, code in (
        ("big.txt", "FILE_TOO_LARGE"),
        ("latin.txt", "FILE_NOT_TEXT"),
        ("zero.txt", "FILE_NOT_TEXT"),
    ):
        result = call(server, "read_file", {"path": f"Public/{name}"})
        assert result == {"content": [{"type": "text", "text": code}], "isError": True}


def test_folders_list_readable_names_only(root, tmp_path):
    _winapi.CreateJunction(str(tmp_path / "outside"), str(root / "Public" / "escape"))
    os.link(tmp_path / "outside" / "Report.TXT", root / "Public" / "twin.txt")
    (root / "Public" / "Sub").mkdir()
    (root / "Public" / "a.md").write_text("a", encoding="utf-8")
    result = call(FileServer(root), "list_folder", {"path": "public"})
    structured = result["structuredContent"]
    assert structured["resource_id"] == "Public"
    assert structured["entries"] == [
        {"name": "a.md", "kind": "file"},
        {"name": "Report.TXT", "kind": "file"},
        {"name": "Sub", "kind": "folder"},
    ]
    assert structured["truncated"] is False
    assert text_of(result) == "a.md\nReport.TXT\nSub/"


def test_list_root_and_truncation(root, monkeypatch):
    server = FileServer(root)
    result = call(server, "list_root", {})
    assert result["structuredContent"]["resource_id"] == ""
    assert [entry["name"] for entry in result["structuredContent"]["entries"]] == [
        "personal",
        "Public",
        "top.txt",
    ]
    assert call(server, "list_root")["isError"] is False  # arguments 생략 가능
    monkeypatch.setattr(file_server, "MAX_ENTRIES", 2)
    truncated = call(server, "list_root", {})["structuredContent"]
    assert len(truncated["entries"]) == 2
    assert truncated["truncated"] is True


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("read_file", {"path": "../outside/Report.TXT"}, "PATH_INVALID"),
        ("read_file", {"path": "Public\\Report.TXT"}, "PATH_INVALID"),
        ("read_file", {"path": "Public/missing.txt"}, "PATH_NOT_FOUND"),
        ("read_file", {"path": "Public"}, "PATH_NOT_FILE"),
        ("list_folder", {"path": "top.txt"}, "PATH_NOT_FOLDER"),
        ("read_file", {}, "ARGUMENT_INVALID"),
        ("read_file", {"path": 1}, "ARGUMENT_INVALID"),
        ("read_file", {"path": "top.txt", "extra": True}, "ARGUMENT_INVALID"),
        ("read_file", [], "ARGUMENT_INVALID"),
        ("list_root", {"path": "Public"}, "ARGUMENT_INVALID"),
    ],
)
def test_refusals_are_fixed_codes_without_the_path(root, name, arguments, code):
    result = call(FileServer(root), name, arguments)
    assert result == {"content": [{"type": "text", "text": code}], "isError": True}


def test_link_targets_are_refused(root, tmp_path):
    _winapi.CreateJunction(str(tmp_path / "outside"), str(root / "escape"))
    result = call(FileServer(root), "read_file", {"path": "escape/Report.TXT"})
    assert text_of(result) == "PATH_LINK"
    assert PLANTED not in json.dumps(result)


def test_file_held_open_for_writing_is_not_read(root):
    with (root / "top.txt").open("r+b"):
        result = call(FileServer(root), "read_file", {"path": "top.txt"})
    assert text_of(result) == "FILE_OPEN_FAILED"


def test_repeated_reads_hold_no_crt_descriptors(root, tmp_path):
    """B77-D1: 예전 `open_osfhandle` 경로는 읽을 때마다 CRT fd를 하나씩 남겼다(8,189번 뒤 고갈)."""
    probe = tmp_path / "outside" / "Report.TXT"

    def next_fd():
        fd = os.open(probe, os.O_RDONLY)
        os.close(fd)
        return fd

    server = FileServer(root)
    before = next_fd()
    for _ in range(50):
        assert text_of(call(server, "read_file", {"path": "top.txt"})) == "top"
    assert next_fd() == before


def test_read_and_os_failures_are_fixed_codes_and_serving_continues(root, monkeypatch):
    server = FileServer(root)
    with monkeypatch.context() as patch:
        patch.setattr(file_server, "_ReadFile", lambda *args: False)
        assert text_of(call(server, "read_file", {"path": "top.txt"})) == "FILE_READ_FAILED"
    hidden_path = "C:\\PRIVATE_PATH\\top.txt"

    def exhausted(*args, **kwargs):
        raise OSError(24, "Too many open files", hidden_path)

    for name, arguments, target, code in (
        ("read_file", {"path": "top.txt"}, "_read_handle", "FILE_READ_FAILED"),
        ("list_folder", {"path": "Public"}, "_opened", "FOLDER_READ_FAILED"),
        ("list_root", {}, "_opened", "FOLDER_READ_FAILED"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(file_server, target, exhausted)
            result = call(server, name, arguments)
        assert result == {"content": [{"type": "text", "text": code}], "isError": True}
        assert "PRIVATE_PATH" not in json.dumps(result)
    assert text_of(call(server, "read_file", {"path": "top.txt"})) == "top"


def test_swap_between_check_and_open_is_caught(root, tmp_path, monkeypatch):
    """C2.4: 검사 뒤 상위 폴더를 루트 밖 junction으로 바꿔치기해도 핸들 대조가 막는다."""
    real_check = file_server.safe_path

    def check_then_swap(base, user_path):
        checked = real_check(base, user_path)
        (root / "Public").rename(root / "Public-old")
        _winapi.CreateJunction(str(tmp_path / "outside"), str(root / "Public"))
        return checked

    monkeypatch.setattr(file_server, "safe_path", check_then_swap)
    result = call(FileServer(root), "read_file", {"path": "Public/Report.TXT"})
    assert text_of(result) == "FILE_CHANGED"
    assert PLANTED not in json.dumps(result)


def test_settings_helpers_round_trip(root, tmp_path):
    args = file_server_args(root)
    assert args[:4] == ("-I", "-m", "guard_app.servers.file_server", "--root")
    assert file_server_root(args) == root.resolve()
    entry = tmp_path / "entry"
    _winapi.CreateJunction(str(root), str(entry))
    assert file_server_root(file_server_args(entry)) == root.resolve()
    for bad in (["-m", "other"], ("-I", "-m", "x", "--root", "C:/r"), [*args[:4], "relative"], "x"):
        assert file_server_root(bad) is None


def test_roots_off_local_fixed_drives_are_refused(root, capsys):
    """루트 풀이는 C2.3 `real_root`(#63 B 리뷰 3): UNC·네트워크·이동식은 등록·시작 모두 거부."""
    unc = Path("//guard-test-host.invalid/share/docs")
    with pytest.raises(SafePathError):
        file_server_args(unc)
    assert main(["--root", str(unc)]) == 2
    assert main(["--root", str(root / "top.txt")]) == 2
    assert main([]) == 2
    assert capsys.readouterr().err.split() == [
        "FILE_SERVER_ROOT_INVALID",
        "FILE_SERVER_ROOT_INVALID",
        "FILE_SERVER_USAGE",
    ]


# 끝에서 끝까지: C2.6 실행 → A transport → C Registry → B Catalog

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def _settings(args, pin):
    # A 등록 화면이 저장할 레코드: 루트는 `args`, 등록 지문은 B3.14 `command_sha256`·`command_size`.
    record = {
        "id": "files",
        "transport": "stdio",
        "command": sys.executable,
        "args": list(args),
        "tier": "internal",
        "max_label": "CONFIDENTIAL",
        "tools": [dict(tool) for tool in FILE_SERVER_TOOL_SETTINGS],
        "command_sha256": pin.sha256,
        "command_size": pin.size,
    }
    return ServerSettings.model_validate_json(json.dumps(record), strict=True)


def test_real_process_serves_reads_that_classify_by_source_rules(root, tmp_path):
    data = Path(sys.executable).read_bytes()
    server = _settings(
        file_server_args(root), ExecutablePin(len(data), hashlib.sha256(data).hexdigest())
    )
    # A 연결이 할 변환(B3.14): 레코드의 두 값으로 지문을 만든다.
    pin = ExecutablePin(server.command_size, server.command_sha256)
    folders = prepare_app_folders(tmp_path / "app")
    sequence = iter(range(10_000))

    async def scenario(store):
        launched = await launch_mcp_server(
            folders.server_folder("files"), server.command, server.args, (), pin
        )
        transport = McpTransport(
            {"files": stdio_factory(launched.read_bytes, launched.write_bytes, launched.drain)},
            tool_list_limits=ToolListLimits(),
        )
        try:
            await transport.start()
            listing = transport.tool_listing("files")
            registry = ServerRegistry(store, clock=lambda: NOW)
            await registry.load()
            entry = await registry.register(server, listing)
            await registry.activate(
                "files",
                expected_row_revision=entry.registration.row_revision,
                reviewed_by="operator",
            )
            kinds = {tool.tool_name: tool.kind for tool in registry.registration("files").tools}
            assert kinds == dict.fromkeys(("list_root", "list_folder", "read_file"), ToolKind.READ)
            catalog = Catalog(
                registry,
                profile_id="p",
                source_rules=(
                    SourceRule(
                        server_id="files", tool_name="list_root", prefix=(), label=Label.PUBLIC
                    ),
                    SourceRule(
                        server_id="files",
                        tool_name="read_file",
                        prefix=("Public",),
                        label=Label.PUBLIC,
                    ),
                    SourceRule(
                        server_id="files",
                        tool_name="read_file",
                        prefix=("personal",),
                        label=Label.INTERNAL,
                    ),
                ),
                resource_resolver=file_resource_resolver({"files": file_server_root(server.args)}),
            )
            state = TaskState(task_id="t1", epoch=1, profile_id="p", policy_version="v")
            seen = {}
            for number, (tool, arguments) in enumerate(
                (
                    ("read_file", {"path": "public/report.txt"}),
                    ("read_file", {"path": "personal/note.md"}),
                    ("list_root", {}),
                ),
                start=1,
            ):
                request = catalog.resolve(
                    state,
                    ProposedCall(
                        tool_ref=f"files__{tool}", arguments=arguments, model_call_id=f"c{number}"
                    ),
                    request_id=f"r{number}",
                    call_seq=number,
                )
                raw = await transport.call(request)
                structured = raw.payload["structuredContent"]
                seen[tool, number] = catalog.classify_read(request, structured["resource_id"])
            return seen
        finally:
            await transport.close()
            assert await launched.close() is True

    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(sequence)}"
    ) as store:
        try:
            seen = asyncio.run(scenario(store))
        finally:
            folders.close()
    assert seen == {
        ("read_file", 1): Label.PUBLIC,
        ("read_file", 2): Label.INTERNAL,
        ("list_root", 3): Label.PUBLIC,
    }
