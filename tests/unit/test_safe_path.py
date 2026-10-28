"""security.paths.safe_path·resource_id·file_resource_resolver(C2.3, T33) 단위 테스트. 실제 NTFS.

파일 MCP 서버(C2.11)와 Guard가 같은 기대 ID를 계산하는지, B Catalog의 출처 규칙과 맞물리는지 본다.
"""

import _winapi
import asyncio
import ctypes
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard_app.adapters.mcp_transport import ToolListing
from guard_app.domain.enums import Label
from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ProposedCall, TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.paths import (
    SafePathError,
    file_resource_resolver,
    resource_id,
    safe_path,
)
from guard_app.services.catalog import Catalog, ServerSettings
from guard_app.services.catalog_sources import SourceRule, canonical_resource_parts
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 경로 규칙이다")

PLANTED = "planted-path-marker"


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "docs"
    (base / "Public").mkdir(parents=True)
    (base / "Public" / "Report.TXT").write_text("public", encoding="utf-8")
    (base / "personal").mkdir()
    (base / "personal" / "note.md").write_text("personal", encoding="utf-8")
    (base / "Long Folder Name").mkdir()
    (base / "Long Folder Name" / "Document With Long Name.txt").write_text("x", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return base


def _rejected(root, user_path, code):
    with pytest.raises(SafePathError) as raised:
        safe_path(root, user_path)
    assert str(raised.value) == code
    assert raised.value.__cause__ is None
    return raised.value


def test_files_and_folders_resolve_inside_the_root(root):
    assert safe_path(root, "Public/Report.TXT") == (root / "Public" / "Report.TXT").resolve()
    assert resource_id(root, "Public/Report.TXT") == "Public/Report.TXT"
    assert resource_id(root, "personal") == "personal"
    assert canonical_resource_parts(resource_id(root, "personal/note.md")) == (
        "personal",
        "note.md",
    )


def test_id_uses_the_names_on_disk_whatever_the_request_spelling(root):
    """Windows는 대소문자를 가리지 않는다. 표기가 달라도 같은 파일이면 같은 ID다."""
    assert resource_id(root, "public/report.txt") == "Public/Report.TXT"
    assert resource_id(root, "PUBLIC/REPORT.txt") == "Public/Report.TXT"


def test_short_8_3_names_give_the_long_name_id(root):
    long_path = root / "Long Folder Name" / "Document With Long Name.txt"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetShortPathNameW(str(long_path), buffer, 1024):
        pytest.skip("짧은 이름을 구할 수 없다")
    # 전체 경로가 짧은 이름으로 바뀌므로 루트 아래 두 조각만 쓴다.
    short = "/".join(Path(buffer.value).parts[-2:])
    if short == "Long Folder Name/Document With Long Name.txt":
        pytest.skip("이 볼륨은 8.3 이름을 만들지 않는다")
    assert resource_id(root, short) == "Long Folder Name/Document With Long Name.txt"


@pytest.mark.parametrize(
    "user_path",
    [
        "",
        "/Public/Report.TXT",
        "C:/x.txt",
        "C:x.txt",
        "Public\\Report.TXT",
        "Public/../personal/note.md",
        "./Public",
        "Public/./Report.TXT",
        "Public//Report.TXT",
        "Public/",
        "..",
        "Public/Report.TXT:stream",
        "Public/Report.TXT.",
        "Public/Report.TXT ",
        "con",
        "CON.txt",
        "Public/nul.md",
        "COM1",
        "a<b",
        "a>b",
        'a"b',
        "a|b",
        "a?b",
        "a*b",
        "a\x00b",
        "a\x1fb",
        "a\x7fb",
        "\ud800",
        pytest.param("a" * 4097, id="too-long"),
        None,
        b"Public/Report.TXT",
    ],
)
def test_malformed_requests_are_refused_without_touching_disk(root, user_path):
    error = _rejected(root, user_path, "PATH_INVALID")
    assert "Report" not in repr(error)


def test_missing_targets_are_not_found(root):
    _rejected(root, "Public/missing.txt", "PATH_NOT_FOUND")
    _rejected(root, "nowhere/at/all", "PATH_NOT_FOUND")


def test_junctions_inside_the_root_are_refused_wherever_they_point(root, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "keys.txt").write_text(PLANTED, encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(root / "escape"))
    _winapi.CreateJunction(str(root / "personal"), str(root / "alias"))
    _rejected(root, "escape/keys.txt", "PATH_LINK")
    _rejected(root, "escape", "PATH_LINK")
    _rejected(root, "alias/note.md", "PATH_LINK")


def test_symlinks_are_refused(root, tmp_path):
    try:
        (root / "link.txt").symlink_to(tmp_path / "outside.txt")
        (root / "linkdir").symlink_to(root / "personal", target_is_directory=True)
    except OSError:
        pytest.skip("심볼릭 링크를 만들 권한이 없다")
    _rejected(root, "link.txt", "PATH_LINK")
    _rejected(root, "linkdir/note.md", "PATH_LINK")


def test_hard_linked_files_are_refused(root, tmp_path):
    """루트 밖 파일의 하드링크를 루트 안에 두면 이름은 안이어도 내용은 밖이다."""
    os.link(tmp_path / "outside.txt", root / "Public" / "looks-inside.txt")
    _rejected(root, "Public/looks-inside.txt", "PATH_HARDLINK")
    os.link(root / "personal" / "note.md", root / "personal" / "twin.md")
    _rejected(root, "personal/note.md", "PATH_HARDLINK")


def test_root_through_a_junction_gives_the_same_ids(root, tmp_path):
    entry = tmp_path / "entry"
    _winapi.CreateJunction(str(root), str(entry))
    assert resource_id(entry, "Public/Report.TXT") == "Public/Report.TXT"


@pytest.mark.parametrize("bad_root", ["relative", "missing", "file"])
def test_root_must_be_an_existing_absolute_folder(root, tmp_path, bad_root):
    value = {
        "relative": Path("docs"),
        "missing": tmp_path / "nope",
        "file": root / "Public" / "Report.TXT",
    }[bad_root]
    with pytest.raises(SafePathError) as raised:
        safe_path(value, "Public/Report.TXT")
    assert str(raised.value) == "PATH_ROOT_INVALID"
    with pytest.raises(SafePathError):
        safe_path("C:/not-a-path-object", "Public/Report.TXT")


def test_resolver_validates_its_roots(root):
    for roots in ({"": root}, {1: root}, {"docs": str(root)}, [("docs", root)]):
        with pytest.raises(SafePathError) as raised:
            file_resource_resolver(roots)
        assert str(raised.value) == "PATH_ROOT_INVALID"


# B Catalog와 맞물림

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
SERVER = {
    "id": "docs",
    "transport": "stdio",
    "command": "C:/PRIVATE_COMMAND/python.exe",
    "tier": "internal",
    "max_label": "CONFIDENTIAL",
    "tools": [{"name": "read_document", "kind": "read", "resource_arg": "path"}],
}
READ_DOCUMENT = {
    "name": "read_document",
    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
}


@pytest.fixture
def catalog(tmp_path, root):
    sequence = iter(range(10_000))
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(sequence)}"
    ) as store:
        registry = ServerRegistry(store, clock=lambda: NOW)

        async def setup():
            await registry.load()
            server = ServerSettings.model_validate_json(json.dumps(SERVER), strict=True)
            listing = ToolListing(
                complete=True, tools=(FrozenJsonObject(READ_DOCUMENT),), reason=None
            )
            entry = await registry.register(server, listing)
            await registry.activate(
                "docs",
                expected_row_revision=entry.registration.row_revision,
                reviewed_by="operator",
            )

        asyncio.run(setup())
        yield Catalog(
            registry,
            profile_id="p",
            source_rules=(
                SourceRule(
                    server_id="docs",
                    tool_name="read_document",
                    prefix=("Public",),
                    label=Label.PUBLIC,
                ),
                SourceRule(
                    server_id="docs",
                    tool_name="read_document",
                    prefix=("personal",),
                    label=Label.INTERNAL,
                ),
            ),
            resource_resolver=file_resource_resolver({"docs": root}),
        )


def _request(catalog, path):
    state = TaskState(task_id="task-1", epoch=1, profile_id="p", policy_version="v")
    call = ProposedCall(
        tool_ref="docs__read_document", arguments={"path": path}, model_call_id="c1"
    )
    return catalog.resolve(state, call, request_id="r1", call_seq=1)


def test_catalog_classifies_by_the_expected_id(catalog):
    request = _request(catalog, "public/report.txt")
    assert request.resource_id == "Public/Report.TXT"
    assert catalog.classify_read(request, "Public/Report.TXT") is Label.PUBLIC
    assert catalog.classify_read(_request(catalog, "personal/note.md"), "personal/note.md") is (
        Label.INTERNAL
    )
    # 서버가 다른 파일을 읽었다고 보고하면 분류하지 않는다(T33).
    assert catalog.classify_read(request, "personal/note.md") is None
    # 표기만 다른 보고도 정확히 같은 ID가 아니면 분류하지 않는다. 서버도 같은 함수로 계산한다.
    assert catalog.classify_read(request, "public/report.txt") is None


@pytest.mark.parametrize("path", ["../outside.txt", "Public/missing.txt", "Public\\Report.TXT"])
def test_catalog_refuses_bad_paths_before_any_call(catalog, path):
    with pytest.raises(ResolutionError) as raised:
        _request(catalog, path)
    assert raised.value.reason is Reason.INVALID_ARGUMENTS
    assert path not in str(raised.value)


def test_server_without_a_root_is_an_internal_error(root):
    resolver = file_resource_resolver({"other": root})
    tool = type("Tool", (), {"server_id": "docs"})()
    with pytest.raises(ResolutionError) as raised:
        resolver(tool, "Public/Report.TXT")
    assert raised.value.reason is Reason.INTERNAL_ERROR
