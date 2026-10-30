"""The login screen's own Codex app-server and logout cleanup (A52, #21).

The settings screen talks to one app-server that C's launcher starts in a
folder of its own the first time the screen asks (C2.12, C2.13). It runs no
turn and registers no tool, only `account/*` methods, so it waits for no L2
result. It ends with the app or with a logout.

`account/logout` leaves `secrets/*.age` and the keyring entries behind (A108).
After Codex accepts the logout, the app-server is ended, and only once its exit
is confirmed does C's `clear_codex_credentials` delete them (contracts
2026-10-27, C2.15). A Task app-server could refresh its token and write them
again, so logout is refused while any session may still run, and no session
starts while a logout is in progress.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol

from guard_app.services.codex_account import (
    AccountError,
    AccountRefusedError,
    AccountView,
    BrowserLoginOpener,
    CodexAccountClient,
)
from guard_app.services.codex_session import CodexPipes
from guard_app.services.session_preparation import CODEX_INSTALL_CODES

LOGOUT_SESSIONS_RUNNING = "LOGOUT_SESSIONS_RUNNING"
LOGOUT_EXIT_UNCONFIRMED = "LOGOUT_EXIT_UNCONFIRMED"
LOGOUT_CLEANUP_FAILED = "LOGOUT_CLEANUP_FAILED"


class AccountUnavailableError(AccountError):
    """Startup refused Codex this run (no pinned codex.exe or CODEX_HOME): nothing to launch."""

    def __init__(self, code: str | None = None) -> None:
        self.code = code
        super().__init__()


class AccountProcess(Protocol):
    """One launched app-server: its pipes, and `close()` True only once its exit is confirmed."""

    @property
    def pipes(self) -> CodexPipes: ...

    async def close(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccountAccess:
    """What the login screen needs from C (A101 preparation).

    `launch` raises AccountError when C refuses, AccountUnavailableError when
    startup left nothing to launch. `clear_credentials` returns False when C
    could not delete the saved credentials; nothing is retried.
    """

    launch: Callable[[], Awaitable[AccountProcess]]
    clear_credentials: Callable[[], Awaitable[bool]]
    # A109: C's validated browser opener; absent until the C entry point exists.
    open_browser: BrowserLoginOpener | None = None


class ProductAccount:
    """CodexAccountPort over one app-server launched when first needed.

    `sessions_alive` tells whether a Task app-server may still run. `signing_out`
    is True from the moment a logout passed that check until it returns;
    SessionRuntime refuses new sessions meanwhile. An app-server whose exit was
    not confirmed is never replaced: every later request fails.
    """

    def __init__(
        self, access: AccountAccess, *, version: str, sessions_alive: Callable[[], bool]
    ) -> None:
        self._access = access
        self._version = version
        self._sessions_alive = sessions_alive
        self._lock = asyncio.Lock()
        self._process: AccountProcess | None = None
        self._client: CodexAccountClient | None = None
        self._signing_out = False
        self._closed = False
        self._exit_unconfirmed = False

    @property
    def signing_out(self) -> bool:
        return self._signing_out

    async def read(self) -> AccountView:
        return await self._request(lambda client: client.read())

    async def login_api_key(self, key: str) -> AccountView:
        return await self._request(lambda client: client.login_api_key(key))

    async def start_device_login(self) -> AccountView:
        return await self._request(lambda client: client.start_device_login())

    async def start_browser_login(self) -> AccountView:
        return await self._request(lambda client: client.start_browser_login())

    async def wait_login(self, login_id: str) -> AccountView:
        # Waiting for the user takes no lock. An app-server that ends meanwhile
        # fails the wait.
        client = self._client
        if client is None:
            return AccountView("FAILED")
        return await client.wait_login(login_id)

    async def cancel_login(self, login_id: str) -> AccountView:
        return await self._request(lambda client: client.cancel_login(login_id))

    async def logout(self) -> AccountView:
        async with self._lock:
            # Nothing is awaited between the check and the flag, so a session
            # creator either is already counted here or sees the flag.
            if self._sessions_alive():
                try:
                    view = await (await self._connect()).read()
                except AccountUnavailableError as error:
                    notice = error.code if error.code in CODEX_INSTALL_CODES else None
                    return AccountView("UNAVAILABLE", notice=notice)
                return replace(view, notice=LOGOUT_SESSIONS_RUNNING)
            self._signing_out = True
            try:
                return await self._sign_out()
            except AccountUnavailableError as error:
                notice = error.code if error.code in CODEX_INSTALL_CODES else None
                return AccountView("UNAVAILABLE", notice=notice)
            finally:
                self._signing_out = False

    async def aclose(self) -> bool:
        """End the app-server at shutdown. False when an exit was not confirmed."""
        async with self._lock:
            self._closed = True
            return await self._disconnect() and not self._exit_unconfirmed

    async def _request(
        self, call: Callable[[CodexAccountClient], Awaitable[AccountView]]
    ) -> AccountView:
        async with self._lock:
            try:
                client = await self._connect()
            except AccountUnavailableError as error:
                notice = error.code if error.code in CODEX_INSTALL_CODES else None
                return AccountView("UNAVAILABLE", notice=notice)
            known = False
            try:
                view = await call(client)
                known = True
                return view
            except AccountRefusedError:
                # Codex answered with an error, such as a refused API key or
                # device code, so the app-server is kept for the next request.
                known = True
                raise
            finally:
                # A109: a lost/malformed login reply or cancellation may leave
                # a callback alive. End its process before another account RPC.
                if not known:
                    await self._disconnect()

    async def _sign_out(self) -> AccountView:
        await (await self._connect()).sign_out()
        if not await self._disconnect():
            return AccountView("SIGNED_OUT", notice=LOGOUT_EXIT_UNCONFIRMED)
        if not await self._access.clear_credentials():
            return AccountView("SIGNED_OUT", notice=LOGOUT_CLEANUP_FAILED)
        return AccountView("SIGNED_OUT")

    async def _connect(self) -> CodexAccountClient:
        if self._closed or self._exit_unconfirmed:
            raise AccountError()
        client = self._client
        if client is not None and not client.closed:
            return client
        if client is not None and not await self._disconnect():
            raise AccountError()
        process = await self._access.launch()
        self._process = process
        client = CodexAccountClient(
            process.pipes, version=self._version, open_browser=self._access.open_browser
        )
        self._client = client
        try:
            await client.start()
        except AccountError:
            await self._disconnect()
            raise
        return client

    async def _disconnect(self) -> bool:
        client, self._client = self._client, None
        process, self._process = self._process, None
        exited = True
        try:
            if client is not None:
                await client.aclose()
        finally:
            if process is not None:
                exited = await process.close()
                self._exit_unconfirmed = self._exit_unconfirmed or not exited
        return exited
