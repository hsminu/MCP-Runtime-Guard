"""C6.13 Codex 설치 스크립트(T44 전제). 네트워크 없이 합성 tarball로 검증한다."""

import ast
import base64
import hashlib
import importlib.util
import io
import tarfile
import urllib.request
from dataclasses import replace
from http.client import HTTPMessage
from pathlib import Path

import pytest

from guard_app.security.codex_home import CodexPins

_SPEC = importlib.util.spec_from_file_location(
    "install_codex", Path(__file__).resolve().parents[2] / "tools" / "install_codex.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
install_codex = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(install_codex)

MEMBER = "package/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
PAYLOAD = b"MZ synthetic codex executable"


def _tarball(tmp_path, *, payload=PAYLOAD, member=MEMBER, link=False):
    path = tmp_path / "codex.tgz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member)
        if link:
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside.exe"
            archive.addfile(info)
        else:
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        extra = tarfile.TarInfo("package/vendor/x86_64-pc-windows-msvc/codex-path/rg.exe")
        extra.size = 3
        archive.addfile(extra, io.BytesIO(b"rg!"))
    return path


def _pins(tarball, *, payload=PAYLOAD, member=MEMBER):
    integrity = base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
    return CodexPins(
        codex_version="0.0.1",
        package_version="0.0.1-win32-x64",
        package_tarball="https://registry.npmjs.org/@openai/codex/-/codex-0.0.1-win32-x64.tgz",
        package_integrity=f"sha512-{integrity}",
        executable_member=member,
        executable_file="codex.exe",
        executable_size=len(payload),
        executable_sha256=hashlib.sha256(payload).hexdigest(),
        home_sha256=(),
        allowed_models=("model",),
    )


def test_installs_only_the_pinned_executable(tmp_path):
    tarball = _tarball(tmp_path)
    dest = tmp_path / "dest"

    target = install_codex.install_executable(tarball, _pins(tarball), dest)

    assert target == dest / "codex.exe"
    assert target.read_bytes() == PAYLOAD
    assert [path.name for path in dest.iterdir()] == ["codex.exe"]


def test_rejects_tarball_with_other_integrity(tmp_path):
    tarball = _tarball(tmp_path)
    pins = _pins(tarball)
    tarball.write_bytes(tarball.read_bytes() + b"\0")
    dest = tmp_path / "dest"

    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_INTEGRITY_MISMATCH$"):
        install_codex.install_executable(tarball, pins, dest)
    assert not dest.exists()


def test_rejects_executable_with_other_hash_and_leaves_nothing(tmp_path):
    tarball = _tarball(tmp_path)
    pins = replace(_pins(tarball), executable_sha256=hashlib.sha256(b"other").hexdigest())
    dest = tmp_path / "dest"

    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_EXECUTABLE_MISMATCH$"):
        install_codex.install_executable(tarball, pins, dest)
    assert list(dest.iterdir()) == []


@pytest.mark.parametrize("variant", ["missing", "link"])
def test_rejects_missing_or_linked_member(tmp_path, variant):
    tarball = _tarball(
        tmp_path,
        member="package/other.exe" if variant == "missing" else MEMBER,
        link=variant == "link",
    )
    dest = tmp_path / "dest"

    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_MEMBER_INVALID$"):
        install_codex.install_executable(tarball, _pins(tarball), dest)
    assert list(dest.iterdir()) == []


def test_keeps_matching_install_and_replaces_wrong_file(tmp_path):
    tarball = _tarball(tmp_path)
    pins = _pins(tarball)
    dest = tmp_path / "dest"
    target = install_codex.install_executable(tarball, pins, dest)
    before = target.stat().st_mtime_ns

    assert install_codex.install_executable(tarball, pins, dest) == target
    assert target.stat().st_mtime_ns == before

    target.write_bytes(b"MZ replaced by something else")
    install_codex.install_executable(tarball, pins, dest)
    assert target.read_bytes() == PAYLOAD


@pytest.mark.parametrize(
    "url",
    [
        "http://registry.npmjs.org/x.tgz",
        "https://registry.npmjs.org.example.net/x.tgz",
        "https://user@registry.npmjs.org/x.tgz",
        "https://registry.npmjs.org:8443/x.tgz",
        "https://registry.npmjs.org:bad/x.tgz",
        "https://example.net/registry.npmjs.org/x.tgz",
    ],
)
def test_download_accepts_only_the_npm_registry(tmp_path, url):
    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_URL_REJECTED$"):
        install_codex.download(url, tmp_path / "x.tgz")
    assert not (tmp_path / "x.tgz").exists()


def test_redirect_is_followed_only_within_the_npm_registry():
    """#124: 리다이렉트도 같은 레지스트리 https 주소로만 따른다."""
    handler = install_codex._RegistryOnlyRedirect()
    request = urllib.request.Request("https://registry.npmjs.org/a.tgz")
    followed = handler.redirect_request(
        request, None, 302, "Found", HTTPMessage(), "https://registry.npmjs.org/b.tgz"
    )
    assert followed is not None
    assert followed.full_url == "https://registry.npmjs.org/b.tgz"
    for elsewhere in ("https://cdn.example.net/a.tgz", "http://registry.npmjs.org/a.tgz"):
        with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_REDIRECT_REJECTED$"):
            handler.redirect_request(request, None, 302, "Found", HTTPMessage(), elsewhere)


class _Response(io.BytesIO):
    def __init__(self, body: bytes, length: str | None = None) -> None:
        super().__init__(body)
        self.headers = HTTPMessage()
        if length is not None:
            self.headers["Content-Length"] = length


def _serve(monkeypatch, response):
    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith("https://registry.npmjs.org/")
            return response

    def build_opener(*handlers):
        assert handlers == (install_codex._RegistryOnlyRedirect,)
        return Opener()

    monkeypatch.setattr(install_codex.urllib.request, "build_opener", build_opener)


URL = "https://registry.npmjs.org/@openai/codex/-/codex-0.0.1-win32-x64.tgz"


def test_download_writes_a_body_within_the_bound(tmp_path, monkeypatch):
    _serve(monkeypatch, _Response(b"12345678", "8"))
    install_codex.download(URL, tmp_path / "x.tgz", max_bytes=8)
    assert (tmp_path / "x.tgz").read_bytes() == b"12345678"


@pytest.mark.parametrize("declared", [None, "8", "100"])
def test_download_stops_past_the_bound_and_leaves_nothing(tmp_path, monkeypatch, declared):
    """#124: 선언 길이가 크거나, 선언과 달리 본문이 길면 쓰다가 멈추고 지운다."""
    _serve(monkeypatch, _Response(b"x" * 9, declared))
    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_TOO_LARGE$"):
        install_codex.download(URL, tmp_path / "x.tgz", max_bytes=8)
    assert not (tmp_path / "x.tgz").exists()


def test_download_keeps_a_file_it_did_not_create(tmp_path, monkeypatch):
    existing = tmp_path / "x.tgz"
    existing.write_bytes(b"keep")
    _serve(monkeypatch, _Response(b"new"))
    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_DOWNLOAD_FAILED$"):
        install_codex.download(URL, existing)
    assert existing.read_bytes() == b"keep"


def test_main_reports_fixed_code_without_paths(tmp_path, capsys):
    missing = tmp_path / "secret-folder" / "codex.tgz"

    assert install_codex.main(["--tarball", str(missing), "--dest", str(tmp_path / "d")]) == 1
    error = capsys.readouterr().err
    assert "CODEX_INSTALL_TARBALL_UNREADABLE" in error
    assert "secret-folder" not in error


@pytest.mark.parametrize(
    "message",
    ["C:\\secret-folder\\codex.tgz", "lower_case_code", "TWO", ("A", "B")],
)
def test_main_shows_only_a_fixed_code_shape(tmp_path, capsys, monkeypatch, message):
    """#124·A220: 코드 모양이 아닌 오류 글은 일반 코드로 바꿔 내보낸다."""
    args = message if isinstance(message, tuple) else (message,)

    def fail(*_args, **_kwargs):
        raise install_codex.InstallError(*args)

    monkeypatch.setattr(install_codex, "install_executable", fail)
    tarball = tmp_path / "codex.tgz"
    assert install_codex.main(["--tarball", str(tarball), "--dest", str(tmp_path / "d")]) == 1
    error = capsys.readouterr().err.strip()
    expected = "TWO" if message == "TWO" else "CODEX_INSTALL_FAILED"
    assert error == f"설치 실패: {expected}"


def test_no_caught_error_reaches_print():
    """#124·A220(Sparrow 04.01): `except ... as` 이름은 print에 닿지 않는다."""
    source = Path(install_codex.__file__).read_text(encoding="utf-8")
    for handler in ast.walk(ast.parse(source)):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        for node in ast.walk(handler):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
                pytest.fail(f"print in an except block at line {node.lineno}")
