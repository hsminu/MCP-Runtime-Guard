"""Codex 브라우저 로그인 URL 검증과 기본 브라우저 열기(C2.21, A109).

URL 원문은 어디에도 남기지 않는다.
A의 계정 클라이언트는 `account/login/start {type: "chatgpt"}`가 돌려준 `authUrl`을 이 모듈의
`open_login_browser`(A `BrowserLoginOpener`)에 넘긴다. 결과는 True(열기 요청 성공)·False뿐이고,
예외는 `CancelledError`만 올린다. 실패하면 A가 Codex 로그인을 취소한다.

- **URL:** 고정 Codex 0.162.1이 만드는 모양만 받는다(2026-10-29 실제 app-server로 확인).
  - `https://auth.openai.com/oauth/authorize?` 뒤 쿼리 하나. 다른 scheme·호스트·포트·사용자
    정보·경로·fragment는 거부한다. 원문은 `[A-Za-z0-9-._~:/?&=+%]`만 받아 공백·따옴표·제어
    문자가 셸에 닿지 않는다.
  - 쿼리 키는 알려진 것만 한 번씩 받는다. `client_id`는 Codex 앱 값과 같아야 하고,
    `redirect_uri`는 `http://127.0.0.1:<포트>/auth/callback`(또는 `localhost`)만, `scope`는
    Codex가 요청하는 항목 안에서만 받는다. 다른 OAuth 앱·외부 콜백·넓힌 권한 동의를 막는다.
- **열기:** `ShellExecuteExW`(`open`, `SEE_MASK_NOASYNC`·`SEE_MASK_FLAG_NO_UI`)를 스레드에서
  부른다. `cmd`·`subprocess`·`os.startfile`을 쓰지 않는다. 스레드는 COM을 단일 스레드 아파트로
  열고 닫는다.
- **한계:** 취소는 기다림만 끝낸다. 이미 시작한 `ShellExecuteExW`는 끝까지 실행되므로 취소 직후
  브라우저가 열릴 수 있다. 그때도 A가 Codex 로그인을 취소해 콜백은 남지 않는다.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from urllib.parse import unquote_plus

MAX_LOGIN_URL_LENGTH = 4096
# 고정 Codex 0.162.1(rust-v0.162.1)의 ChatGPT 로그인 OAuth 클라이언트.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_SCOPES = frozenset(
    {
        "openid",
        "profile",
        "email",
        "offline_access",
        "api.connectors.read",
        "api.connectors.invoke",
    }
)

_PREFIX = "https://auth.openai.com/oauth/authorize?"
_RAW = re.compile(r"[A-Za-z0-9\-._~:/?&=+%]+")
_ESCAPES = re.compile(r"%(?![0-9A-Fa-f]{2})")
_KEY = re.compile(r"[a-z_]{1,64}")
_REDIRECT = re.compile(r"http://(?:127\.0\.0\.1|localhost):([1-9][0-9]{0,4})/auth/callback")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]+")
_VALUES: dict[str, Callable[[str], bool]] = {
    "response_type": lambda v: v == "code",
    "client_id": lambda v: v == CODEX_CLIENT_ID,
    "redirect_uri": lambda v: _redirect_port(v) is not None,
    "scope": lambda v: "openid" in v.split(" ") and set(v.split(" ")) <= CODEX_SCOPES,
    "code_challenge": lambda v: 43 <= len(v) <= 128 and _TOKEN.fullmatch(v) is not None,
    "code_challenge_method": lambda v: v == "S256",
    "state": lambda v: 32 <= len(v) <= 256 and _TOKEN.fullmatch(v) is not None,
    "id_token_add_organizations": lambda v: v == "true",
    "codex_cli_simplified_flow": lambda v: v == "true",
    "originator": lambda v: re.fullmatch(r"[a-z0-9_]{1,64}", v) is not None,
}
_REQUIRED = frozenset(
    {
        "response_type",
        "client_id",
        "redirect_uri",
        "scope",
        "code_challenge",
        "code_challenge_method",
        "state",
    }
)


class LoginUrlError(ValueError):
    """`LOGIN_URL_INVALID` 하나다. 어떤 부분이 틀렸는지와 원문은 싣지 않는다."""

    def __init__(self) -> None:
        super().__init__("LOGIN_URL_INVALID")


def _redirect_port(value: str) -> int | None:
    match = _REDIRECT.fullmatch(value)
    if match is None or not 1 <= int(match.group(1)) <= 65535:
        return None
    return int(match.group(1))


def check_login_url(url: object) -> str:
    """받을 수 있는 Codex 로그인 URL이면 그 문자열을 그대로 돌려준다. 아니면 `LoginUrlError`."""
    if (
        type(url) is not str
        or not len(_PREFIX) < len(url) <= MAX_LOGIN_URL_LENGTH
        or not url.startswith(_PREFIX)
        or _RAW.fullmatch(url) is None
        or _ESCAPES.search(url) is not None
    ):
        raise LoginUrlError
    seen: set[str] = set()
    for pair in url[len(_PREFIX) :].split("&"):
        key, sep, raw = pair.partition("=")
        if not sep or _KEY.fullmatch(key) is None or key in seen or key not in _VALUES:
            raise LoginUrlError
        seen.add(key)
        try:
            value = unquote_plus(raw, errors="strict")
        except UnicodeDecodeError:
            raise LoginUrlError from None
        if not _VALUES[key](value):
            raise LoginUrlError
    if not seen.issuperset(_REQUIRED):
        raise LoginUrlError
    return url


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _ShellExecuteInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        )

    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _ole32 = ctypes.WinDLL("ole32")
    _ShellExecuteExW = _shell32.ShellExecuteExW
    _ShellExecuteExW.argtypes = (ctypes.POINTER(_ShellExecuteInfo),)
    _ShellExecuteExW.restype = wintypes.BOOL
    _CoInitializeEx = _ole32.CoInitializeEx
    _CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    _CoInitializeEx.restype = ctypes.c_long
    _CoUninitialize = _ole32.CoUninitialize
    _CoUninitialize.argtypes = ()
    _CoUninitialize.restype = None

    _SEE_MASK_NOASYNC = 0x00000100
    _SEE_MASK_FLAG_NO_UI = 0x00000400
    _SW_SHOWNORMAL = 1
    _COINIT_APARTMENTTHREADED = 0x2
    _COINIT_DISABLE_OLE1DDE = 0x4

    def _shell_open(url: str) -> bool:
        result = _CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
        try:
            info = _ShellExecuteInfo()
            info.cbSize = ctypes.sizeof(_ShellExecuteInfo)
            info.fMask = _SEE_MASK_NOASYNC | _SEE_MASK_FLAG_NO_UI
            info.lpVerb = "open"
            info.lpFile = url
            info.nShow = _SW_SHOWNORMAL
            return bool(_ShellExecuteExW(ctypes.byref(info)))
        finally:
            # S_OK·S_FALSE만 짝을 맞춘다. 다른 모드로 이미 열린 스레드는 닫지 않는다.
            if result in (0, 1):
                _CoUninitialize()

else:

    def _shell_open(url: str) -> bool:
        return False


def make_login_opener(
    shell: Callable[[str], bool] | None = None,
) -> Callable[[str], Awaitable[bool]]:
    """A `BrowserLoginOpener`를 만든다. `shell`은 시험용이며 기본은 `ShellExecuteExW`다."""
    run = _shell_open if shell is None else shell

    async def open_login_browser(url: str) -> bool:
        try:
            checked = check_login_url(url)
        except LoginUrlError:
            return False
        try:
            return await asyncio.get_running_loop().run_in_executor(None, run, checked) is True
        except (OSError, RuntimeError):
            # RuntimeError: 닫히는 중인 실행기. OSError: 바인딩·호출 실패. 원문을 버린다.
            return False

    return open_login_browser


open_login_browser = make_login_opener()
