"""Codex 고정 버전 설치(C6.13, T44). 프로젝트 가상환경에서 실행한다.

npm 레지스트리의 고정 tarball을 받아 sha512 무결성을 확인하고, 그 안의 codex.exe 하나만
SHA-256·크기를 대조해 버전별 폴더에 둔다. code mode 호스트·명령 실행기·샌드박스 설정기·
음성 호스트·rg는 설치하지 않는다(L1에서 끈 기능의 실행 파일). 폴더 권한은 C2.13이다.

    python tools/install_codex.py [--dest DIR] [--tarball FILE]

--dest 기본값: %LOCALAPPDATA%\\MCPRuntimeGuard\\codex\\<codex_version>
--tarball: 미리 받은 tarball을 쓴다. 같은 무결성 검사를 거친다.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path

from guard_app.security.codex_home import CodexConfigError, CodexPins, load_codex_pins

_CHUNK = 1 << 20


class InstallError(RuntimeError):
    """경로·응답 원문을 싣지 않는 고정 오류 코드."""


def default_dest(pins: CodexPins) -> Path:
    base = os.environ.get("LOCALAPPDATA", "")
    if not base or not Path(base).is_absolute():
        raise InstallError("CODEX_INSTALL_DEST_UNAVAILABLE")
    return Path(base) / "MCPRuntimeGuard" / "codex" / pins.codex_version


def download(url: str, target: Path) -> None:
    if not url.startswith("https://registry.npmjs.org/"):
        raise InstallError("CODEX_INSTALL_URL_REJECTED")
    # URL은 위에서 고정값 파일의 npm 레지스트리 https 주소로 제한했다.
    request = urllib.request.Request(url, headers={"User-Agent": "mcp-runtime-guard-install"})  # noqa: S310
    try:
        response = urllib.request.urlopen(request, timeout=600)  # noqa: S310
        with response, target.open("xb") as out:
            shutil.copyfileobj(response, out, _CHUNK)
    except OSError:
        raise InstallError("CODEX_INSTALL_DOWNLOAD_FAILED") from None


def verify_integrity(tarball: Path, integrity: str) -> None:
    algorithm, _, expected = integrity.partition("-")
    digest = hashlib.new(algorithm)
    try:
        with tarball.open("rb") as source:
            while chunk := source.read(_CHUNK):
                digest.update(chunk)
    except OSError:
        raise InstallError("CODEX_INSTALL_TARBALL_UNREADABLE") from None
    if base64.b64encode(digest.digest()).decode("ascii") != expected:
        raise InstallError("CODEX_INSTALL_INTEGRITY_MISMATCH")


def install_executable(tarball: Path, pins: CodexPins, dest: Path) -> Path:
    """무결성을 확인한 tarball에서 고정 멤버 하나만 꺼낸다. 이미 맞는 파일이 있으면 그대로 둔다."""
    verify_integrity(tarball, pins.package_integrity)
    target = dest / pins.executable_file
    if _matches(target, pins):
        return target
    dest.mkdir(parents=True, exist_ok=True)
    temporary = dest / f"{pins.executable_file}.{uuid.uuid4().hex}.tmp"
    try:
        with tarfile.open(tarball, "r:gz") as archive:
            member = archive.getmember(pins.executable_member)
            source = archive.extractfile(member) if member.isfile() else None
            if source is None:
                raise InstallError("CODEX_INSTALL_MEMBER_INVALID")
            with source, temporary.open("xb") as out:
                shutil.copyfileobj(source, out, _CHUNK)
        if not _matches(temporary, pins):
            raise InstallError("CODEX_INSTALL_EXECUTABLE_MISMATCH")
        os.replace(temporary, target)
    except KeyError:
        raise InstallError("CODEX_INSTALL_MEMBER_INVALID") from None
    except (OSError, tarfile.TarError):
        raise InstallError("CODEX_INSTALL_WRITE_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)
    if not _matches(target, pins):
        raise InstallError("CODEX_INSTALL_EXECUTABLE_MISMATCH")
    return target


def _matches(path: Path, pins: CodexPins) -> bool:
    """읽을 수 없거나 링크인 파일은 맞지 않는 것으로 본다."""
    digest = hashlib.sha256()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != pins.executable_size:
            return False
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == pins.executable_sha256


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex 고정 버전 설치")
    parser.add_argument("--dest", type=Path, help="설치 폴더(기본: 앱 폴더의 버전별 폴더)")
    parser.add_argument("--tarball", type=Path, help="미리 받은 npm tarball")
    args = parser.parse_args(argv)
    try:
        pins = load_codex_pins()
        dest = args.dest if args.dest is not None else default_dest(pins)
        if args.tarball is not None:
            target = install_executable(args.tarball, pins, dest)
        else:
            with tempfile.TemporaryDirectory(prefix="codex-install-") as work:
                tarball = Path(work) / "codex.tgz"
                download(pins.package_tarball, tarball)
                target = install_executable(tarball, pins, dest)
    except (CodexConfigError, InstallError) as error:
        print(f"설치 실패: {error}", file=sys.stderr)
        return 1
    print(f"codex {pins.codex_version} 설치 확인: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
