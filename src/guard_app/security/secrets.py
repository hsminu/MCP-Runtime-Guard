"""Scanner 환경 파일·키 로딩 경계(I9). 키를 프로세스 환경이나 로그에 복사하지 않는다.

**제품 키(C2.10, 2026-10-15 결정).** Scanner LLM API 키는 사용자가 앱 화면(A)에 넣고 Windows
자격증명 관리자에 저장한다. 현재 사용자의 일반 자격증명(`CRED_PERSIST_LOCAL_MACHINE`)이라
Windows가 사용자 DPAPI로 암호화하고 다른 사용자는 읽지 못한다. 제품 경로는 이 저장소만 읽고
`.env`(`load_scanner_environment`)는 개발·실험용이다. 키는 로그·예외·repr·프로세스 환경에
싣지 않는다. 같은 사용자 권한의 프로세스는 읽을 수 있다(위협 모델 밖). `CredWriteW`에 넘기는
ctypes 버퍼는 쓴 뒤 0으로 지우지만, 호출자의 문자열과 그 `bytes` 사본은 Python에서 지울 수 없어
GC 때까지 메모리에 남는다.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import sys
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

_SDK_IMPORT_LOCK = threading.Lock()

SCANNER_KEY_TARGET = "MCPRuntimeGuard/ScannerLLMKey"
# OpenAI 키에 쓰는 문자만 받는다. 공백·따옴표·제어 문자는 붙여 넣기 실수다.
_KEY_FORMAT = re.compile(r"[A-Za-z0-9_-]{20,512}")
_TARGET_FORMAT = re.compile(r"MCPRuntimeGuard/[A-Za-z0-9._-]{1,64}")
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_ERROR_NO_SUCH_LOGON_SESSION = 1312

if sys.platform == "win32":
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", _FileTime),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    def _api(name: str, restype: object, *argtypes: object) -> Any:
        function = getattr(_advapi32, name)
        function.restype = restype
        function.argtypes = argtypes
        return function

    _CredentialPointer = ctypes.POINTER(_Credential)
    _CredWriteW = _api("CredWriteW", wintypes.BOOL, ctypes.POINTER(_Credential), wintypes.DWORD)
    _CredReadW = _api(
        "CredReadW",
        wintypes.BOOL,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_CredentialPointer),
    )
    _CredDeleteW = _api(
        "CredDeleteW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD
    )
    _CredFree = _api("CredFree", None, ctypes.c_void_p)


class ScannerKeyError(RuntimeError):
    """키·저장소 오류 원문을 싣지 않는 고정 코드."""


# tiktoken 0.14.0의 캐시 키(sha1(URL)) → 기대 SHA-256(tiktoken_ext.openai_public).
# 순서대로 cl100k_base, o200k_base, p50k_base. litellm은 import 때 cl100k_base를 읽는다.
_TOKENIZER_SHA256 = {
    "9b5ad71b2ce5302211f9c61530b329a4922fc6a4": (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    ),
    "fb374d419588a4632f3f557e76b4b70aebbca790": (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
    ),
    "ec7223a39ce59f226a68acc30dc1af2788490e15": (
        "94b5ca7dff4d00767bc256fdd1b27e5b17361d7b8a5f968547f9f23eb70d2069"
    ),
}

_SCANNER_KEYS = (
    "OPENAI_API_KEY",
    "MCP_SCANNER_LLM_API_KEY",
    "MCP_SCANNER_LLM_MODEL",
    "MCP_SCANNER_LLM_REASONING_EFFORT",
    "MCP_SCANNER_LLM_BASE_URL",
    "MCP_SCANNER_LLM_TIMEOUT",
    "MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS",
)


class SecretLoadError(ValueError):
    """경로·파일 내용·키를 노출하지 않는 설정 로딩 오류."""


class TokenizerCacheError(ValueError):
    """경로·파일 내용을 노출하지 않는 tokenizer 사본 검증 오류."""


def scanner_tokenizer_cache_dir() -> Path:
    """사용자별 앱 캐시 아래의 tokenizer 사본 위치. site-packages에는 쓰지 않는다."""
    name = "LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME"
    base = os.environ.get(name, "")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".cache"
    return root / "mcp-runtime-guard" / "tiktoken"


def _bundled_tokenizer(cache_key: str) -> bytes:
    # 경로만 찾는다. litellm 패키지를 import하면 바로 tokenizer를 읽으므로 피한다.
    located = distribution("litellm").locate_file(
        f"litellm/litellm_core_utils/tokenizers/{cache_key}"
    )
    return Path(str(located)).read_bytes()


def prepare_tokenizer_cache(
    cache_dir: Path, *, read_bundled: Callable[[str], bytes] = _bundled_tokenizer
) -> None:
    """litellm 내장 tokenizer를 기대 해시로 검증해 cache_dir에 둔다.

    litellm 1.93.2 Windows wheel의 내장 파일은 줄끝이 CRLF라 tiktoken 해시 검사에
    실패한다. 그러면 tiktoken이 파일을 지우고 외부에서 다시 받는다. 여기서는 CRLF만
    LF로 되돌려 같은 해시가 될 때만 쓰고, 맞지 않으면 다운로드 대신 실패한다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    for cache_key, expected in _TOKENIZER_SHA256.items():
        target = cache_dir / cache_key
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        data = read_bundled(cache_key)
        if hashlib.sha256(data).hexdigest() != expected:
            data = data.replace(b"\r\n", b"\n")
            if hashlib.sha256(data).hexdigest() != expected:
                raise TokenizerCacheError("SCAN_SDK_TOKENIZER_INVALID")
        temporary = cache_dir / f"{cache_key}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def scanner_sdk_import_environment() -> Iterator[None]:
    """SDK 첫 import의 암묵적 load_dotenv·외부 조회를 제한하고 기존 플래그를 복원한다.

    Cisco 4.8.6의 패키지 import는 HTTP 서버 모듈도 불러오며 그 모듈이
    load_dotenv를 실행한다. 같은 import가 litellm을 불러와 가격표와 tokenizer를
    읽으므로 둘 다 로컬 자료로 고정한다. litellm은 tokenizer 위치를 프로세스 동안
    유지하므로 두 설정은 복원하지 않는다. 이 동기 구간에는 await를 넣지 않는다.

    어댑터는 이벤트 루프를 막지 않도록 이 구간을 작업 스레드에서 실행한다. 구간은
    프로세스 환경변수를 바꾸므로 lock으로 직렬화해 동시 준비의 저장·복원이 섞이지
    않게 한다. 같은 스레드에서 중첩해 쓰지 않는다.
    """
    with _SDK_IMPORT_LOCK:
        cache_dir = scanner_tokenizer_cache_dir()
        prepare_tokenizer_cache(cache_dir)
        previous = os.environ.get("PYTHON_DOTENV_DISABLED")
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = str(cache_dir)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("PYTHON_DOTENV_DISABLED", None)
            else:
                os.environ["PYTHON_DOTENV_DISABLED"] = previous


def load_scanner_environment(
    env_file: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """명시한 파일 위에 프로세스 설정을 적용한다. 자동 파일 탐색은 하지 않는다.

    키 별칭은 병합한 OPENAI_API_KEY로 확장한다. 임의 환경변수 참조나 외부
    명령 확장은 허용하지 않는다. 반환값은 비밀값이므로 로그에 공개하지 않는다.
    """
    values: dict[str, str] = {}
    if env_file is not None:
        try:
            if not env_file.is_file():
                raise SecretLoadError("SCAN_LLM_ENV_UNAVAILABLE")
            file_values = dotenv_values(env_file, interpolate=False, encoding="utf-8")
        except (OSError, UnicodeError):
            raise SecretLoadError("SCAN_LLM_ENV_UNAVAILABLE") from None
        values.update(
            {
                key: value
                for key, value in file_values.items()
                if key in _SCANNER_KEYS and value is not None
            }
        )
    source = os.environ if environ is None else environ
    values.update({key: source[key] for key in _SCANNER_KEYS if key in source})
    alias = values.get("MCP_SCANNER_LLM_API_KEY", "${OPENAI_API_KEY}")
    if alias == "${OPENAI_API_KEY}":
        alias = values.get("OPENAI_API_KEY", "")
    if "${" in alias:
        raise SecretLoadError("SCAN_LLM_KEY_REFERENCE_INVALID")
    values["MCP_SCANNER_LLM_API_KEY"] = alias
    return values


def store_scanner_key(key: object, *, target: str = SCANNER_KEY_TARGET) -> None:
    """키를 자격증명 관리자에 저장한다(같은 대상은 덮어쓴다).

    `CredWriteW`에 넘기는 ctypes 버퍼만 쓴 뒤 0으로 지운다. `key`와 그 `bytes` 사본은 지울 수 없다.

    실패 코드: `SCAN_KEY_INVALID`(형식), `SCAN_KEY_STORE_UNAVAILABLE`(로그온 세션 없음, 서비스
    계정 등), `SCAN_KEY_STORE_FAILED`(그 밖의 Windows 오류), `SCAN_KEY_STORE_UNSUPPORTED`.
    """
    checked = _checked_target(target)
    if type(key) is not str or not _KEY_FORMAT.fullmatch(key):
        raise ScannerKeyError("SCAN_KEY_INVALID")
    secret = key.encode("ascii")
    blob = (ctypes.c_ubyte * len(secret)).from_buffer_copy(secret)
    try:
        credential = _Credential(
            Type=_CRED_TYPE_GENERIC,
            TargetName=checked,
            CredentialBlobSize=len(secret),
            CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=_CRED_PERSIST_LOCAL_MACHINE,
            UserName="MCPRuntimeGuard",
        )
        if not _CredWriteW(ctypes.byref(credential), 0):
            raise ScannerKeyError(_store_error(ctypes.get_last_error()))
    finally:
        ctypes.memset(blob, 0, len(secret))


def save_scanner_key(key: object) -> bool:
    """A 키 입력 화면의 `KeySaver`. 저장에 성공했을 때만 True다."""
    try:
        store_scanner_key(key)
    except ScannerKeyError:
        return False
    return True


def load_scanner_key(*, target: str = SCANNER_KEY_TARGET) -> str | None:
    """저장한 키. 없으면 None이다. 저장된 값이 형식에 맞지 않으면 `SCAN_KEY_STORED_INVALID`다."""
    checked = _checked_target(target)
    pointer = _CredentialPointer()
    if not _CredReadW(checked, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise ScannerKeyError(_store_error(error))
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
    finally:
        _CredFree(pointer)
    try:
        key = raw.decode("ascii")
    except UnicodeDecodeError:
        raise ScannerKeyError("SCAN_KEY_STORED_INVALID") from None
    if not _KEY_FORMAT.fullmatch(key):
        raise ScannerKeyError("SCAN_KEY_STORED_INVALID")
    return key


def scanner_key_tail(*, target: str = SCANNER_KEY_TARGET) -> str | None:
    """화면 표시용 끝 4자리(결정: 끝 4자리만 보여 준다). 키가 없으면 None이다."""
    key = load_scanner_key(target=target)
    return None if key is None else key[-4:]


def delete_scanner_key(*, target: str = SCANNER_KEY_TARGET) -> bool:
    """저장한 키를 지운다. 지웠거나 원래 없었으면 True다."""
    checked = _checked_target(target)
    if _CredDeleteW(checked, _CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == _ERROR_NOT_FOUND:
        return True
    raise ScannerKeyError(_store_error(error))


def scanner_key_environ(*, target: str = SCANNER_KEY_TARGET) -> dict[str, str]:
    """검사기 연결에 넘길 값(`OPENAI_API_KEY`). 키가 없거나 읽지 못하면 빈 dict다.

    빈 dict이면 LLM 분석기는 `SCAN_LLM_KEY_MISSING_OR_INVALID`로 준비 실패하고 YARA만 돈다.
    프로세스 환경이나 `.env`는 보지 않는다.
    """
    try:
        key = load_scanner_key(target=target)
    except ScannerKeyError:
        return {}
    return {} if key is None else {"OPENAI_API_KEY": key}


def _checked_target(target: object) -> str:
    if sys.platform != "win32":
        raise ScannerKeyError("SCAN_KEY_STORE_UNSUPPORTED")
    if type(target) is not str or not _TARGET_FORMAT.fullmatch(target):
        raise ScannerKeyError("SCAN_KEY_TARGET_INVALID")
    return target


def _store_error(error: int) -> str:
    if error == _ERROR_NO_SUCH_LOGON_SESSION:
        return "SCAN_KEY_STORE_UNAVAILABLE"
    return "SCAN_KEY_STORE_FAILED"
