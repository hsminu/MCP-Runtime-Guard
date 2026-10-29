"""security.login_browser(C2.21, A109) 단위 테스트. 실제 브라우저는 열지 않는다(`shell` 대역).

기준 URL은 고정 Codex 0.162.1 app-server가 2026-10-29에 돌려준 `authUrl`과 같은 모양이다.
`code_challenge`·`state`만 합성 값으로 바꿨다.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import threading

import pytest

from guard_app.security import login_browser
from guard_app.security.login_browser import (
    CODEX_CLIENT_ID,
    MAX_LOGIN_URL_LENGTH,
    LoginUrlError,
    check_login_url,
    make_login_opener,
)

CHALLENGE = "c" * 43
STATE = "S" * 43
QUERY = {
    "response_type": "code",
    "client_id": CODEX_CLIENT_ID,
    "redirect_uri": "http%3A%2F%2F127.0.0.1%3A1455%2Fauth%2Fcallback",
    "scope": "openid+profile+email+offline_access+api.connectors.read+api.connectors.invoke",
    "code_challenge": CHALLENGE,
    "code_challenge_method": "S256",
    "id_token_add_organizations": "true",
    "codex_cli_simplified_flow": "true",
    "state": STATE,
    "originator": "mcp_runtime_guard",
}
BASE = "https://auth.openai.com/oauth/authorize?"


def url(**changes: str | None) -> str:
    query = dict(QUERY)
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return BASE + "&".join(f"{key}={value}" for key, value in query.items())


GOOD = url()


def test_codex_shaped_url_is_accepted_unchanged():
    assert check_login_url(GOOD) == GOOD
    assert len(GOOD) < MAX_LOGIN_URL_LENGTH


@pytest.mark.parametrize(
    "changes",
    [
        {"redirect_uri": "http%3A%2F%2F127.0.0.1%3A50123%2Fauth%2Fcallback"},
        {"scope": "openid"},
        {"originator": None, "id_token_add_organizations": None, "codex_cli_simplified_flow": None},
    ],
)
def test_variants_codex_may_produce_are_accepted(changes):
    assert check_login_url(url(**changes))


@pytest.mark.parametrize(
    "bad",
    [
        None,
        b"https://auth.openai.com/oauth/authorize?x=1",
        "",
        BASE,
        "http://auth.openai.com/oauth/authorize?" + GOOD[len(BASE) :],
        "https://auth.openai.com.evil.example/oauth/authorize?" + GOOD[len(BASE) :],
        "https://evil.example/oauth/authorize?" + GOOD[len(BASE) :],
        "https://AUTH.openai.com/oauth/authorize?" + GOOD[len(BASE) :],
        "https://auth.openai.com:443/oauth/authorize?" + GOOD[len(BASE) :],
        "https://user@auth.openai.com/oauth/authorize?" + GOOD[len(BASE) :],
        "https://auth.openai.com/oauth/authorize/?" + GOOD[len(BASE) :],
        "https://auth.openai.com/oauth/token?" + GOOD[len(BASE) :],
        "https://auth.openai.com/oauth/authorize" + GOOD[len(BASE) :],
        GOOD + "#frag",
        GOOD + " ",
        GOOD + "\t",
        GOOD.replace("&", "\n&", 1),
        GOOD + '&x="',
        GOOD + "&x='",
        GOOD + "^",
        GOOD + "|calc",
        GOOD + "\\",
        GOOD + "é",
        GOOD + "%",
        GOOD + "%zz",
        GOOD + "&",
        GOOD + "&originator=again",
        GOOD + "&prompt=login",
        GOOD + "&Response_type=code",
        url(response_type="token"),
        url(client_id="app_other"),
        url(client_id=None),
        url(redirect_uri="https%3A%2F%2F127.0.0.1%3A1455%2Fauth%2Fcallback"),
        url(redirect_uri="http%3A%2F%2Fevil.example%3A1455%2Fauth%2Fcallback"),
        # 고정 Codex가 준 값은 127.0.0.1이다. localhost 이름은 받지 않는다(#77 B 리뷰).
        url(redirect_uri="http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"),
        url(redirect_uri="http%3A%2F%2F127.0.0.1%3A1455%2Fother"),
        url(redirect_uri="http%3A%2F%2F127.0.0.1%3A0%2Fauth%2Fcallback"),
        url(redirect_uri="http%3A%2F%2F127.0.0.1%3A65536%2Fauth%2Fcallback"),
        url(redirect_uri="http%3A%2F%2F127.0.0.1%2Fauth%2Fcallback"),
        url(redirect_uri=None),
        url(scope="openid+admin"),
        url(scope="profile"),
        url(code_challenge="short"),
        url(code_challenge="c" * 42 + "%2B"),
        url(code_challenge_method="plain"),
        url(code_challenge=None),
        url(state="s" * 31),
        url(state=None),
        url(**{"id_token_add_organizations": "false"}),
        url(originator="Evil-Name"),
        url(originator="%FF"),
        GOOD + "&x" * 2000,
    ],
)
def test_other_urls_are_rejected_without_their_text(bad):
    with pytest.raises(LoginUrlError) as raised:
        check_login_url(bad)
    assert str(raised.value) == "LOGIN_URL_INVALID"
    assert raised.value.__cause__ is None
    if isinstance(bad, str) and bad:
        assert bad not in repr(raised.value)


def test_length_is_capped():
    largest = url(code_challenge="c" * 128, state="S" * 256, originator="a" * 64)
    assert check_login_url(largest)
    with pytest.raises(LoginUrlError):
        check_login_url(BASE + "x" * MAX_LOGIN_URL_LENGTH)


def test_opener_passes_only_the_checked_url_to_the_shell():
    calls: list[tuple[str, int]] = []

    def shell(target: str) -> bool:
        calls.append((target, threading.get_ident()))
        return True

    loop_thread = threading.get_ident()
    assert asyncio.run(make_login_opener(shell)(GOOD)) is True
    assert [target for target, _ in calls] == [GOOD]
    # 셸 호출은 이벤트 루프 밖 스레드에서 한다.
    assert calls[0][1] != loop_thread


@pytest.mark.parametrize("result", [False, None, 1, "yes"])
def test_opener_is_true_only_when_the_shell_says_true(result):
    assert asyncio.run(make_login_opener(lambda _target: result)(GOOD)) is False


@pytest.mark.parametrize("bad", [None, "", "https://evil.example/", GOOD + " "])
def test_rejected_url_never_reaches_the_shell(bad):
    calls: list[str] = []
    assert asyncio.run(make_login_opener(lambda target: calls.append(target) or True)(bad)) is False
    assert calls == []


@pytest.mark.parametrize("error", [OSError(GOOD), RuntimeError(GOOD)])
def test_shell_failure_is_false_and_its_text_is_dropped(error, caplog):
    caplog.set_level(logging.DEBUG)

    def shell(_target: str) -> bool:
        raise error

    assert asyncio.run(make_login_opener(shell)(GOOD)) is False
    assert STATE not in caplog.text


def test_cancellation_propagates_while_the_shell_runs():
    entered = threading.Event()
    release = threading.Event()

    def shell(_target: str) -> bool:
        entered.set()
        release.wait(5)
        return True

    async def scenario() -> None:
        task = asyncio.create_task(make_login_opener(shell)(GOOD))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    asyncio.run(scenario())


def test_module_never_logs_or_prints(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    asyncio.run(make_login_opener(lambda _target: True)(GOOD))
    asyncio.run(make_login_opener(lambda _target: True)(GOOD + "#x"))
    out, err = capsys.readouterr()
    assert STATE not in caplog.text + out + err
    assert not hasattr(login_browser, "logger")


@pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteExW 바인딩은 Windows 전용이다")
def test_windows_binding_shape_without_opening_anything():
    info = login_browser._ShellExecuteInfo
    assert ctypes.sizeof(info) == (112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 60)
    assert login_browser._ShellExecuteExW.argtypes == (ctypes.POINTER(info),)
    assert login_browser._SEE_MASK_NOASYNC == 0x100
    assert login_browser._SEE_MASK_FLAG_NO_UI == 0x400
    assert login_browser.open_login_browser.__name__ == "open_login_browser"
