"""Codex login inside Codex (A52, 10.4; contracts 2026-10-15/18).

Both ChatGPT device code and API key log in through the app-server's own
`account/*` methods; Codex stores the credential in Windows Credential Manager
(keyring). The Guard never stores, logs or displays a key. This client runs on
pipes of an app-server launched by C (C2.12); without them the UI shows login as
unavailable. Email addresses are not surfaced (privacy), only the account kind
and plan.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol

from guard_app.adapters.codex_protocol import initialize_params
from guard_app.adapters.codex_stream import CodexStreamChannel
from guard_app.adapters.jsonrpc_client import JsonRpcClient, JsonRpcError, JsonRpcRemoteError
from guard_app.services.codex_session import REMOTE_CONTROL_STATUS, CodexPipes

_PLAN_MAX = 40

# C must validate the pinned Codex authorization URL and open the default
# browser. False means validation/opening failed; no URL or exception text
# leaves this callback. It must propagate cancellation. None disables browser
# login before any RPC is sent; A bounds opening by the account RPC timeout.
type BrowserLoginOpener = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, slots=True, repr=False)
class AccountView:
    """What the settings screen may show. No email, key or token.

    `notice` is a fixed code from trusted A code about the last request, such
    as a refused or partly cleaned logout (#21).
    """

    state: str
    plan: str | None = None
    login_id: str | None = None
    verification_url: str | None = None
    user_code: str | None = None
    notice: str | None = None
    browser_available: bool = False


class CodexAccountPort(Protocol):
    async def read(self) -> AccountView: ...
    async def login_api_key(self, key: str) -> AccountView: ...
    async def start_device_login(self) -> AccountView: ...
    async def start_browser_login(self) -> AccountView: ...
    async def wait_login(self, login_id: str) -> AccountView: ...
    async def cancel_login(self, login_id: str) -> AccountView: ...
    async def logout(self) -> AccountView: ...


class CodexAccountClient:
    """One app-server connection used only for account methods."""

    def __init__(
        self,
        pipes: CodexPipes,
        *,
        version: str,
        request_timeout_s: float = 30.0,
        open_browser: BrowserLoginOpener | None = None,
    ) -> None:
        self._channel = CodexStreamChannel(
            pipes.read_bytes, pipes.write_bytes, pipes.drain, max_frame_bytes=1024 * 1024
        )
        self._client = JsonRpcClient(
            self._channel,
            request_timeout_s=request_timeout_s,
            max_frame_bytes=1024 * 1024,
            max_pending=4,
            on_notification=self._notification,
            version_field=False,
        )
        self._version = version
        self._started = False
        self._completed: dict[str, bool] = {}
        self._changed = asyncio.Event()
        self._open_browser = open_browser
        self._open_timeout_s = request_timeout_s

    @property
    def closed(self) -> bool:
        """The connection ended: the process exited or a remote-control notice closed it."""
        return self._client.closed_reason is not None

    async def start(self) -> None:
        if self._started:
            return
        self._client.start()
        self._started = True
        await self._call("initialize", initialize_params(self._version))
        try:
            await self._client.notify("initialized")
        except JsonRpcError:
            raise AccountError() from None

    async def read(self) -> AccountView:
        return replace(await self._read(), browser_available=self._open_browser is not None)

    async def _read(self) -> AccountView:
        result = await self._call("account/read", {"refreshToken": False})
        account = result.get("account") if type(result) is dict else None
        if account is None:
            return AccountView("SIGNED_OUT")
        if type(account) is not dict:
            return AccountView("FAILED")
        kind, plan = account.get("type"), account.get("planType")
        shown_plan = plan if type(plan) is str and 0 < len(plan) <= _PLAN_MAX else None
        if kind == "apiKey":
            return AccountView("API_KEY")
        if kind == "chatgpt":
            return AccountView("CHATGPT", plan=shown_plan)
        return AccountView("OTHER")

    async def login_api_key(self, key: str) -> AccountView:
        if type(key) is not str or not key.strip():
            return AccountView("FAILED")
        result = await self._call("account/login/start", {"type": "apiKey", "apiKey": key.strip()})
        if type(result) is not dict or result.get("type") != "apiKey":
            return AccountView("FAILED")
        return await self.read()

    async def start_device_login(self) -> AccountView:
        self._completed.clear()
        result = await self._call("account/login/start", {"type": "chatgptDeviceCode"})
        if type(result) is not dict:
            return AccountView("FAILED", browser_available=self._open_browser is not None)
        login_id, url, code = (
            result.get(key) for key in ("loginId", "verificationUrl", "userCode")
        )
        if not all(type(value) is str and value for value in (login_id, url, code)):
            return AccountView("FAILED", browser_available=self._open_browser is not None)
        return AccountView(
            "PENDING_DEVICE",
            login_id=str(login_id),
            verification_url=str(url),
            user_code=str(code),
            browser_available=self._open_browser is not None,
        )

    async def start_browser_login(self) -> AccountView:
        opener = self._open_browser
        if opener is None:
            return AccountView("FAILED", notice="BROWSER_LOGIN_UNAVAILABLE")
        self._completed.clear()
        result = await self._call("account/login/start", {"type": "chatgpt"})
        if type(result) is not dict or result.get("type") != "chatgpt":
            raise AccountError()
        login_id, url = result.get("loginId"), result.get("authUrl")
        if type(login_id) is not str or not 0 < len(login_id) <= 256:
            raise AccountError()
        opened = False
        try:
            if type(url) is str and url:
                async with asyncio.timeout(self._open_timeout_s):
                    opened = await opener(url) is True
        except TimeoutError:
            opened = False
        except asyncio.CancelledError:
            # A109: release Codex's callback, then preserve cancellation even
            # if the connection failed. ProductAccount also ends the process.
            with contextlib.suppress(AccountError):
                await self.cancel_login(login_id)
            raise
        if not opened:
            await self.cancel_login(login_id)
            return AccountView("FAILED", notice="BROWSER_OPEN_FAILED", browser_available=True)
        # The URL stays on the backend, never in a Qt event, ledger or log.
        return AccountView("PENDING_BROWSER", login_id=login_id, browser_available=True)

    async def wait_login(self, login_id: str) -> AccountView:
        while login_id not in self._completed:
            if self._client.closed_reason is not None:
                return AccountView("FAILED")
            self._changed.clear()
            changed = asyncio.ensure_future(self._changed.wait())
            closed = asyncio.ensure_future(self._client.wait_closed())
            try:
                await asyncio.wait({changed, closed}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                changed.cancel()
                closed.cancel()
        if not self._completed.pop(login_id):
            return AccountView("LOGIN_NOT_COMPLETED")
        return await self.read()

    async def cancel_login(self, login_id: str) -> AccountView:
        await self._call("account/login/cancel", {"loginId": login_id})
        self._completed.setdefault(login_id, False)
        self._changed.set()
        return await self.read()

    async def logout(self) -> AccountView:
        await self.sign_out()
        return await self.read()

    async def sign_out(self) -> None:
        """`account/logout` alone; the product owner reads nothing before it ends the process."""
        await self._call("account/logout", None)

    async def aclose(self) -> None:
        await self._client.close()
        self._channel.close()

    async def _call(self, method: str, params: object) -> object:
        try:
            return await self._client.request(method, params)
        except (JsonRpcError, JsonRpcRemoteError):
            raise AccountError() from None

    def _notification(self, method: str, params: object) -> None:
        if method == REMOTE_CONTROL_STATUS:
            if type(params) is not dict or params.get("status") != "disabled":
                # C-CODEX-07: never keep talking to an app-server a remote
                # client may drive. Every later call fails; the launcher's owner
                # still closes the process.
                self._channel.close()
            return
        if method != "account/login/completed" or type(params) is not dict:
            return
        login_id = params.get("loginId")
        if type(login_id) is str and login_id:
            self._completed[login_id] = params.get("success") is True
            self._changed.set()


class AccountError(RuntimeError):
    """Fixed code; the app-server's message is never shown."""

    def __init__(self) -> None:
        super().__init__("CODEX_ACCOUNT_FAILED")
