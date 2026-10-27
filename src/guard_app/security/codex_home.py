"""Codex L1 설정·모델 정보·고정값 경계(C6.13, I11·T22·T44). 오류에 경로·내용을 싣지 않는다.

저장소 `config/codex/`의 파일을 `codex.lock.json`의 SHA-256과 대조한 뒤 앱 전용 CODEX_HOME에
쓴다. Task 시작 때는 CODEX_HOME의 같은 파일을 다시 대조하고, 최상위에 허용 항목만 있는지 본다
(C6.19, C-CODEX-06). CODEX_HOME 경로·권한(C2.13)과 codex.exe 실행 때의 해시 대조(C2.12
launcher)는 별도다. 경로 기본값은 소스 checkout 기준이며 설치판 위치는 C6.20이다.

로그인 자격증명(2026-10-27 결정, C2.15): Codex 0.162.1은 keyring 모드에서도 토큰을
`secrets/*.age` 암호문으로 CODEX_HOME에 두고 그 암호만 Windows 자격증명 관리자에 둔다
(`codex-rs/secrets/src/local.rs`·`lib.rs`). `secrets/`는 알려진 파일만 허용하고 해시 대조에서
뺀다. 로그아웃 뒤에는 `clear_codex_credentials`가 암호문과 자격증명 항목을 지운다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

CODEX_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "codex"
LOCK_FILE = "codex.lock.json"
HOME_FILES = ("config.toml", "model_catalog.json")
# Codex 0.162.1이 CODEX_HOME에 스스로 만드는 항목. 그 밖의 항목은 Codex가 지시문·설정으로 읽을 수
# 있어(AGENTS.md·AGENTS.override.md·rules·prompts·skills 등) 시작을 거부한다.
CODEX_OWNED_FILES = ("installation_id", ".sandbox_migration")
CODEX_OWNED_DIRS = ("tmp",)
# 로그인 암호문 폴더(2026-10-27 결정). 안쪽은 아래 이름의 일반 파일만 받는다.
CODEX_SECRETS_DIR = "secrets"
CODEX_SECRET_FILES = ("codex_auth.age", "local.age", "mcp_oauth.age", "gateway_oauth.age")
# Codex가 암호문을 바꿀 때 먼저 쓰고 이름을 바꾸는 임시 파일: `.<이름>.tmp-<pid>-<nonce>`
# (`codex-rs/secrets/src/local.rs`). 동시 실행 중이거나 쓰다 끊긴 경우에 보인다.
_SECRET_TEMP = re.compile(r"\.(?:codex_auth|local|mcp_oauth|gateway_oauth)\.age\.tmp-[0-9]+-[0-9]+")
# keyring 계정 `secrets|<h16>`(gateway는 `|gateway-oauth`)과 서비스 `codex`. Windows 자격증명의
# 대상 이름은 `<계정>.<서비스>`다(`codex-rs/secrets/src/lib.rs` `compute_keyring_account`).
_KEYRING_SERVICE = "codex"
_KEYRING_SUFFIXES = ("", "|gateway-oauth")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_INTEGRITY = re.compile(r"sha512-[A-Za-z0-9+/]{86}==")
_REGISTRY = "https://registry.npmjs.org/"


class CodexConfigError(ValueError):
    """경로·파일 내용을 공개하지 않는 고정 오류 코드."""


@dataclass(frozen=True, slots=True)
class CodexPins:
    """`codex.lock.json`의 검증된 사본. 바꿀 때는 L2 통과 뒤 PR로 올린다."""

    codex_version: str
    package_version: str
    package_tarball: str
    package_integrity: str
    executable_member: str
    executable_file: str
    executable_size: int
    executable_sha256: str
    home_sha256: tuple[tuple[str, str], ...]
    allowed_models: tuple[str, ...]


def load_codex_pins(source_dir: Path = CODEX_CONFIG_DIR) -> CodexPins:
    """고정값 파일을 엄격하게 읽는다. 키 누락·추가·형식 오류는 모두 실패다."""
    try:
        value = json.loads((source_dir / LOCK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CodexConfigError("CODEX_LOCK_INVALID") from None
    try:
        return _parse_pins(value)
    except (KeyError, TypeError, ValueError):
        raise CodexConfigError("CODEX_LOCK_INVALID") from None


def write_codex_home(codex_home: Path, *, source_dir: Path = CODEX_CONFIG_DIR) -> str:
    """앱 시작 때 한 번 호출한다. 고정값과 같은 파일만 쓰고 다시 읽어 확인한 묶음 해시를 돌려준다.

    원본이 고정값과 다르거나 CODEX_HOME에 허용 밖 항목이 있으면 아무것도 쓰지 않는다. 각 파일은
    같은 폴더의 임시 파일에 쓴 뒤 교체한다. 돌려준 값은 Task 시작마다 `verify_codex_home`에 넘긴다.
    """
    pins = load_codex_pins(source_dir)
    home = _checked_home(codex_home)
    contents: list[tuple[str, bytes]] = []
    for name, expected in pins.home_sha256:
        try:
            data = (source_dir / name).read_bytes()
        except OSError:
            raise CodexConfigError("CODEX_CONFIG_SOURCE_MISMATCH") from None
        if hashlib.sha256(data).hexdigest() != expected:
            raise CodexConfigError("CODEX_CONFIG_SOURCE_MISMATCH")
        contents.append((name, data))
    _check_entries(home)
    for name, data in contents:
        _replace_file(home, name, data)
    return _verified_digest(home, pins)


def verify_codex_home(
    codex_home: Path, expected_digest: str, *, source_dir: Path = CODEX_CONFIG_DIR
) -> None:
    """Task 시작 전 확인. 파일·묶음 해시가 다르거나 허용 밖 항목이 있으면 시작을 거부한다."""
    pins = load_codex_pins(source_dir)
    if _verified_digest(_checked_home(codex_home), pins) != expected_digest:
        raise CodexConfigError("CODEX_HOME_MISMATCH")


def _parse_pins(value: object) -> CodexPins:
    if type(value) is not dict or set(value) != {
        "codex_version",
        "package",
        "executable",
        "codex_home",
        "allowed_models",
    }:
        raise ValueError
    package, executable, home = value["package"], value["executable"], value["codex_home"]
    if type(package) is not dict or set(package) != {"name", "version", "tarball", "integrity"}:
        raise ValueError
    if type(executable) is not dict or set(executable) != {"member", "file", "size", "sha256"}:
        raise ValueError
    if type(home) is not dict or tuple(home) != HOME_FILES:
        raise ValueError
    models = value["allowed_models"]
    if type(models) is not list or not models:
        raise ValueError
    pins = CodexPins(
        codex_version=_text(value["codex_version"]),
        package_version=_text(package["version"]),
        package_tarball=_text(package["tarball"]),
        package_integrity=_text(package["integrity"]),
        executable_member=_text(executable["member"]),
        executable_file=_text(executable["file"]),
        executable_size=executable["size"],
        executable_sha256=_text(executable["sha256"]),
        home_sha256=tuple((name, _text(home[name])) for name in HOME_FILES),
        allowed_models=tuple(_text(model) for model in models),
    )
    if (
        package["name"] != "@openai/codex"
        or not pins.package_version.startswith(f"{pins.codex_version}-")
        or not pins.package_tarball.startswith(_REGISTRY)
        or not _INTEGRITY.fullmatch(pins.package_integrity)
        or pins.executable_file != "codex.exe"
        or type(pins.executable_size) is not int
        or pins.executable_size <= 0
        or not _SHA256.fullmatch(pins.executable_sha256)
        or any(not _SHA256.fullmatch(digest) for _, digest in pins.home_sha256)
    ):
        raise ValueError
    return pins


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise ValueError
    return value


def _checked_home(codex_home: Path) -> Path:
    """앱이 만든 폴더만 받는다. 상대 경로·링크·junction은 따라가지 않는다."""
    try:
        if (
            not isinstance(codex_home, Path)
            or not codex_home.is_absolute()
            or codex_home.is_symlink()
            or codex_home.is_junction()
            or not codex_home.is_dir()
        ):
            raise CodexConfigError("CODEX_HOME_INVALID")
    except OSError:
        raise CodexConfigError("CODEX_HOME_INVALID") from None
    return codex_home


def _replace_file(home: Path, name: str, data: bytes) -> None:
    temporary = home / f"{name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, home / name)
    except OSError:
        raise CodexConfigError("CODEX_HOME_WRITE_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)


def _check_entries(home: Path) -> None:
    """최상위 항목이 허용 목록과 종류(파일·폴더)에 맞는지 본다. 링크·junction은 거부한다."""
    files = {*HOME_FILES, *CODEX_OWNED_FILES}
    try:
        for entry in home.iterdir():
            if entry.is_symlink() or entry.is_junction():
                raise CodexConfigError("CODEX_HOME_UNEXPECTED_ENTRY")
            if (entry.name in files and entry.is_file()) or (
                entry.name in CODEX_OWNED_DIRS and entry.is_dir()
            ):
                continue
            if entry.name == CODEX_SECRETS_DIR and entry.is_dir():
                _secret_entries(entry)
                continue
            raise CodexConfigError("CODEX_HOME_UNEXPECTED_ENTRY")
    except OSError:
        raise CodexConfigError("CODEX_HOME_UNEXPECTED_ENTRY") from None


def _secret_entries(secrets: Path) -> list[Path]:
    """`secrets/` 안이 알려진 암호문·임시 파일뿐인지 보고 그 목록을 돌려준다. 내용은 읽지 않는다."""
    found: list[Path] = []
    for entry in secrets.iterdir():
        if (
            entry.is_symlink()
            or entry.is_junction()
            or not entry.is_file()
            or not (entry.name in CODEX_SECRET_FILES or _SECRET_TEMP.fullmatch(entry.name))
        ):
            raise CodexConfigError("CODEX_HOME_UNEXPECTED_ENTRY")
        found.append(entry)
    return found


def clear_codex_credentials(codex_home: Path) -> None:
    """로그아웃 뒤 A가 부른다(2026-10-27 결정, C2.15). 암호문 파일과 자격증명 관리자 항목을 지운다.

    `account/logout`은 `secrets/codex_auth.age`를 지우지 않고 keyring 항목도 남긴다(A108 관찰).
    `secrets/` 안에 모르는 항목이 있으면 아무것도 지우지 않고 실패한다. keyring 항목이 이미
    없으면 성공으로 본다. Codex 프로세스가 끝난 뒤에 부른다.
    """
    if sys.platform != "win32":
        raise CodexConfigError("CODEX_CREDENTIAL_UNSUPPORTED")
    home = _checked_home(codex_home)
    account = keyring_account(home)
    secrets = home / CODEX_SECRETS_DIR
    try:
        if secrets.is_symlink() or secrets.is_junction():
            raise CodexConfigError("CODEX_HOME_UNEXPECTED_ENTRY")
        entries = _secret_entries(secrets) if secrets.exists() else []
        for entry in entries:
            entry.unlink()
    except OSError:
        raise CodexConfigError("CODEX_CREDENTIAL_DELETE_FAILED") from None
    for suffix in _KEYRING_SUFFIXES:
        _delete_credential(f"{account}{suffix}.{_KEYRING_SERVICE}")


def keyring_account(codex_home: Path) -> str:
    """Codex와 같은 방식의 keyring 계정 이름: `secrets|` + SHA-256(정규 경로)의 앞 16자.

    Rust `canonicalize`는 Windows에서 `\\\\?\\C:\\…` 형태를 돌려준다. 같은 값을 얻으려고
    `GetFinalPathNameByHandleW`(정규화 이름·DOS 볼륨 이름)를 쓴다.
    """
    canonical = _final_path(_checked_home(codex_home))
    return "secrets|" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_ALL = 0x00000007
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _CRED_TYPE_GENERIC = 1
    _ERROR_NOT_FOUND = 1168

    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.restype = wintypes.HANDLE
    _CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _GetFinalPathNameByHandleW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.restype = wintypes.BOOL
    _CloseHandle.argtypes = (wintypes.HANDLE,)
    _CredDeleteW = _advapi32.CredDeleteW
    _CredDeleteW.restype = wintypes.BOOL
    _CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)


def _final_path(path: Path) -> str:
    if sys.platform != "win32":
        raise CodexConfigError("CODEX_CREDENTIAL_UNSUPPORTED")
    handle = _CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle is None or handle == _INVALID_HANDLE:
        raise CodexConfigError("CODEX_HOME_INVALID")
    try:
        size = 512
        while True:
            buffer = ctypes.create_unicode_buffer(size)
            length = _GetFinalPathNameByHandleW(handle, buffer, size, 0)
            if length == 0:
                raise CodexConfigError("CODEX_HOME_INVALID")
            if length < size:
                return buffer.value
            size = length + 1
    finally:
        _CloseHandle(handle)


def _delete_credential(target: str) -> None:
    if (
        not _CredDeleteW(target, _CRED_TYPE_GENERIC, 0)
        and ctypes.get_last_error() != _ERROR_NOT_FOUND
    ):
        raise CodexConfigError("CODEX_CREDENTIAL_DELETE_FAILED")


def _verified_digest(home: Path, pins: CodexPins) -> str:
    """CODEX_HOME의 실제 파일을 읽어 고정값과 대조한다. 링크로 바뀐 파일은 거부한다."""
    _check_entries(home)
    summary = hashlib.sha256()
    for name, expected in pins.home_sha256:
        target = home / name
        try:
            if target.is_symlink() or not target.is_file():
                raise CodexConfigError("CODEX_HOME_MISMATCH")
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            raise CodexConfigError("CODEX_HOME_MISMATCH") from None
        if actual != expected:
            raise CodexConfigError("CODEX_HOME_MISMATCH")
        summary.update(f"{name}\0{actual}\n".encode())
    return summary.hexdigest()
