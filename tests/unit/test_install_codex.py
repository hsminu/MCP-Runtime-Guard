"""C6.13 Codex 설치 스크립트(T44 전제). 네트워크 없이 합성 tarball로 검증한다."""

import base64
import hashlib
import importlib.util
import io
import tarfile
from dataclasses import replace
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


def test_download_accepts_only_the_npm_registry(tmp_path):
    with pytest.raises(install_codex.InstallError, match="^CODEX_INSTALL_URL_REJECTED$"):
        install_codex.download("http://registry.npmjs.org/x.tgz", tmp_path / "x.tgz")
    assert not (tmp_path / "x.tgz").exists()


def test_main_reports_fixed_code_without_paths(tmp_path, capsys):
    missing = tmp_path / "secret-folder" / "codex.tgz"

    assert install_codex.main(["--tarball", str(missing), "--dest", str(tmp_path / "d")]) == 1
    error = capsys.readouterr().err
    assert "CODEX_INSTALL_TARBALL_UNREADABLE" in error
    assert "secret-folder" not in error
