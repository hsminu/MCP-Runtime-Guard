"""A109: browser-login A wiring; I6/I9/I11, T19/T43 and S02/S04.

The C URL validator/browser launcher is injected. No browser, account,
network, model, real credential or C independent acceptance is exercised.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from guard_app.services.bridge import (
    AccountView,
    BackendBindings,
    BackendBridge,
    BridgeState,
    CommandEvent,
    CommandStage,
)
from guard_app.services.bridge_commands import AccountCommands
from guard_app.services.bridge_ingress import account_command
from guard_app.services.codex_account import (
    AccountError,
    AccountRefusedError,
    CodexAccountClient,
)
from guard_app.services.codex_preparation import CodexProcessPreparation
from guard_app.ui.model_settings import CodexLoginInfo
from guard_app.ui.presentation import ACCOUNT_REQUEST_REFUSED, COMMAND_REFUSALS
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.window import GuardWindow

from .fake_codex_app_server import FakeAppServer
from .test_account_runtime import FakeAccess, product_account
from .test_bridge import FakeControl, attach, close_bridge, pump_until

AUTH_URL = "https://auth.openai.com/oauth/authorize?state=private-login-state"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class BrowserServer(FakeAppServer):
    def __init__(self):
        super().__init__()
        self.browser_reply: object = {
            "type": "chatgpt",
            "loginId": "browser-1",
            "authUrl": AUTH_URL,
        }
        self.cancelled: list[str] = []
        self.refuse_cancel = False

    def _request(self, message):
        if message["method"] == "account/login/start" and message["params"]["type"] == "chatgpt":
            self.login_requests.append(message["params"])
            self.emit({"id": message["id"], "result": self.browser_reply})
        elif message["method"] == "account/login/cancel":
            self.cancelled.append(message["params"]["loginId"])
            if self.refuse_cancel:
                self.emit({"id": message["id"], "error": {"code": -1, "message": AUTH_URL}})
            else:
                super()._request(message)
        else:
            super()._request(message)

    def finish(self, login_id="browser-1", *, success=True, signed_in=True):
        if signed_in:
            self.account = {"type": "chatgpt", "planType": "plus", "email": "PRIVATE"}
        self.notify(
            "account/login/completed",
            {
                "loginId": login_id,
                "success": success,
                "error": None if success else AUTH_URL,
            },
        )


async def opened(_url):
    return True


@pytest.mark.asyncio
async def test_browser_login_keeps_url_backend_only_and_checks_account_after_completion():
    fake = BrowserServer()
    urls = []

    async def open_browser(url):
        urls.append(url)
        return True

    account = CodexAccountClient(fake, version="test", open_browser=open_browser)
    await account.start()
    async with aclosing(account):
        assert (await account.read()).browser_available
        pending = await account.start_browser_login()
        assert pending.state == "PENDING_BROWSER"
        assert pending.login_id == "browser-1"
        assert pending.verification_url is None
        assert urls == [AUTH_URL]
        assert "private-login-state" not in repr(pending)
        assert fake.login_requests == [{"type": "chatgpt"}]
        waiting = asyncio.create_task(account.wait_login(pending.login_id))
        fake.finish()
        view = await asyncio.wait_for(waiting, 2)
        assert (view.state, view.plan) == ("CHATGPT", "plus")
        assert "PRIVATE" not in repr(view)
        assert fake.methods()[-1] == "account/read"


@pytest.mark.asyncio
async def test_browser_without_c_opener_is_unavailable_and_does_not_start_login():
    fake = BrowserServer()
    account = CodexAccountClient(fake, version="test")
    await account.start()
    async with aclosing(account):
        assert not (await account.read()).browser_available
        view = await account.start_browser_login()
        assert view.notice == "BROWSER_LOGIN_UNAVAILABLE"
        assert fake.login_requests == []


def test_preparation_passes_injected_opener_without_launching_or_claiming():
    preparation = CodexProcessPreparation()
    assert preparation.account_access().open_browser is None
    assert preparation.account_access(open_browser=opened).open_browser is opened


@pytest.mark.asyncio
async def test_open_timeout_cancels_callback_and_does_not_report_pending():
    fake = BrowserServer()
    cancelled = asyncio.Event()

    async def stalled(_url):
        try:
            await asyncio.Event().wait()
            return True
        finally:
            cancelled.set()

    account = CodexAccountClient(fake, version="test", request_timeout_s=0.05, open_browser=stalled)
    await account.start()
    async with aclosing(account):
        view = await asyncio.wait_for(account.start_browser_login(), 2)
        assert view.notice == "BROWSER_OPEN_FAILED"
        assert cancelled.is_set()
        assert fake.cancelled == ["browser-1"]


def test_device_rpc_failure_keeps_browser_fallback_available(app):
    class DeviceRefused(BrowserServer):
        def _request(self, message):
            if (
                message["method"] == "account/login/start"
                and message["params"]["type"] == "chatgptDeviceCode"
            ):
                self.emit({"id": message["id"], "error": {"code": -1, "message": "PRIVATE"}})
            else:
                super()._request(message)

    fake, loops, views = DeviceRefused(), [], []
    bridge = browser_bridge(fake, loops)
    bridge.account_changed.connect(views.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        bridge.request_account("account_read")
        pump_until(app, lambda: views[-1].browser_available)
        bridge.request_account("account_login_device")
        pump_until(app, lambda: views[-1].state == "FAILED")
        assert views[-1].browser_available
        bridge.request_account("account_login_browser")
        pump_until(app, lambda: views[-1].state == "PENDING_BROWSER")
    finally:
        close_bridge(app, bridge)


@pytest.mark.asyncio
async def test_malformed_browser_reply_ends_product_account_process():
    for reply in [None, {}, {"type": "chatgpt", "authUrl": AUTH_URL}]:
        server = BrowserServer()
        server.browser_reply = reply
        access = FakeAccess(server=lambda *, server=server: server)
        access.access = lambda *, access=access: replace(
            FakeAccess.access(access), open_browser=opened
        )
        account = product_account(access)
        async with aclosing(account):
            with pytest.raises(AccountError, match="^CODEX_ACCOUNT_FAILED$"):
                await account.start_browser_login()
            assert access.processes[0].closes == 1


@pytest.mark.asyncio
async def test_refused_url_or_open_failure_cancels_the_callback():
    for url in [None, "", AUTH_URL]:
        fake = BrowserServer()
        fake.browser_reply["authUrl"] = url
        calls = []

        async def refused(value, *, calls=calls):
            calls.append(value)
            return False

        account = CodexAccountClient(fake, version="test", open_browser=refused)
        await account.start()
        async with aclosing(account):
            view = await account.start_browser_login()
            assert (view.state, view.notice) == ("FAILED", "BROWSER_OPEN_FAILED")
            assert calls == ([url] if url else [])
            assert fake.cancelled == ["browser-1"]


@pytest.mark.asyncio
async def test_cancelled_opener_preserves_cancellation_even_if_cancel_rpc_fails():
    fake = BrowserServer()
    fake.refuse_cancel = True
    entered = asyncio.Event()

    async def opening(_url):
        entered.set()
        await asyncio.Event().wait()
        return True

    account = CodexAccountClient(fake, version="test", open_browser=opening)
    await account.start()
    async with aclosing(account):
        operation = asyncio.create_task(account.start_browser_login())
        await asyncio.wait_for(entered.wait(), 2)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 2)
        assert fake.cancelled == ["browser-1"]


@pytest.mark.asyncio
async def test_browser_open_or_success_notification_alone_is_not_signed_in():
    for success, signed_in, expected in [
        (False, False, "LOGIN_NOT_COMPLETED"),
        (True, False, "SIGNED_OUT"),
    ]:
        fake = BrowserServer()
        account = CodexAccountClient(fake, version="test", open_browser=opened)
        await account.start()
        async with aclosing(account):
            await account.start_browser_login()
            fake.finish(success=success, signed_in=signed_in)
            view = await asyncio.wait_for(account.wait_login("browser-1"), 2)
            assert view.state == expected


def browser_bridge(fake, loops, opener=opened):
    async def bootstrap():
        loops.append(asyncio.get_running_loop())
        account = CodexAccountClient(fake, version="test", open_browser=opener)
        await account.start()
        return BackendBindings(lambda _: None, FakeControl(), close=account.aclose, account=account)

    return BackendBridge(bootstrap, control_workers=2)


def test_browser_open_failure_offers_device_and_api_key_without_url(app, caplog):
    fake, loops, views = BrowserServer(), [], []
    fake.account = None
    bridge = browser_bridge(fake, loops, refused_open)
    receiver = attach(bridge)
    bridge.account_changed.connect(views.append)
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    form = window.codex_login_info
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        bridge.request_account("account_read")
        pump_until(app, lambda: form.browser_button.isEnabled())
        form.browser_button.click()
        pump_until(app, lambda: form.view.notice == "BROWSER_OPEN_FAILED")
        assert form.view.state == "FAILED"
        assert fake.cancelled == ["browser-1"]
        assert form.login_button.isEnabled()
        assert form.browser_button.isEnabled()
        assert form.api_key_input.isEnabled()
        assert not form.api_key_button.isEnabled()  # empty input stays disabled
        form.api_key_input.setText("sk-test-fallback")
        assert form.api_key_button.isEnabled()
        assert "기기 코드 또는 API 키로 로그인하세요." in form.status.text()
        assert not form.cancel_button.isEnabled()
        assert AUTH_URL not in repr(views)
        assert "private-login-state" not in repr(views)
        assert AUTH_URL not in caplog.text
        assert AUTH_URL not in repr(receiver.commands)
        assert AUTH_URL not in repr(receiver.states)
        assert all(AUTH_URL not in label.text() for label in form.findChildren(QLabel))
    finally:
        window.close()
        close_bridge(app, bridge)


def test_bridge_serializes_duplicate_starts_and_ignores_cancelled_login_completion(app):
    fake, loops, views = BrowserServer(), [], []
    bridge = browser_bridge(fake, loops)
    receiver = attach(bridge)
    bridge.account_changed.connect(views.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        first = bridge.request_account("account_login_browser")
        second = bridge.request_account("account_login_browser")
        pump_until(
            app,
            lambda: all(
                any(e.stage is CommandStage.BACKEND_RETURNED for e in receiver.stages(command))
                for command in (first, second)
            ),
        )
        assert len(fake.login_requests) == 1
        assert views[-1].state == "PENDING_BROWSER"
        bridge.request_account("account_cancel_login", target_id="browser-1")
        pump_until(app, lambda: views[-1].notice == "LOGIN_CANCELLED")
        bridge.request_account("account_login_device")
        pump_until(app, lambda: views[-1].state == "PENDING_DEVICE")
        loops[0].call_soon_threadsafe(lambda: fake.finish(success=False, signed_in=False))
        barrier = bridge.request_account("account_read")
        pump_until(
            app,
            lambda: any(e.stage is CommandStage.BACKEND_RETURNED for e in receiver.stages(barrier)),
        )
        assert views[-1].state == "PENDING_DEVICE"
        assert "ACCOUNT_PENDING_BROWSER" in [e.code for e in receiver.stages(first)]
    finally:
        close_bridge(app, bridge)


class Outlet:
    def __init__(self):
        self.views = []

    def account_changed(self, view):
        self.views.append(view)


class NoCapability:
    def publish_capability(self):
        pass


def consume(future):
    if not future.cancelled():
        future.exception()


async def send(commands, port, action, target_id="account"):
    bindings = BackendBindings(lambda _: None, FakeControl(), account=port)
    return await commands.account(bindings, account_command(action, target_id, None))


class AbsorbingWaitPort:
    """The browser wait absorbs one cancellation, as a shielded inner wait would.

    No product port does this today; it is the only path to the generation check.
    """

    def __init__(self):
        self.absorbed = asyncio.Event()
        self.release = asyncio.Event()
        self.late = asyncio.Event()

    async def read(self):
        return AccountView("SIGNED_OUT", browser_available=True)

    async def start_browser_login(self):
        return AccountView("PENDING_BROWSER", login_id="browser-1", browser_available=True)

    async def start_device_login(self):
        return AccountView(
            "PENDING_DEVICE",
            login_id="device-1",
            verification_url="https://auth.example/device",
            user_code="ABCD-1234",
            browser_available=True,
        )

    async def wait_login(self, login_id):
        if login_id != "browser-1":
            await asyncio.Event().wait()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.absorbed.set()
        await self.release.wait()
        self.late.set()
        return AccountView("CHATGPT", plan="plus")

    async def cancel_login(self, _login_id):
        return AccountView("SIGNED_OUT", browser_available=True)


@pytest.mark.asyncio
async def test_a_wait_returning_after_its_cancel_cannot_overwrite_the_next_login():
    port = AbsorbingWaitPort()
    outlet = Outlet()
    commands = AccountCommands(NoCapability(), outlet, lambda: False, consume)
    try:
        assert await send(commands, port, "account_login_browser") == "ACCOUNT_PENDING_BROWSER"
        await asyncio.sleep(0)  # the waiter enters wait_login
        await send(commands, port, "account_cancel_login", "browser-1")
        await asyncio.wait_for(port.absorbed.wait(), 2)
        assert await send(commands, port, "account_login_device") == "ACCOUNT_PENDING_DEVICE"
        port.release.set()
        await asyncio.wait_for(port.late.wait(), 2)
        for _ in range(3):
            await asyncio.sleep(0)
        states = [view.state for view in outlet.views]
        assert "CHATGPT" not in states
        assert states[-1] == "PENDING_DEVICE"
        # The next login is still the pending one, and cancels normally.
        assert await send(commands, port, "account_read") == "ACCOUNT_PENDING_DEVICE"
        await send(commands, port, "account_cancel_login", "device-1")
        last = outlet.views[-1]
        assert (last.state, last.notice) == ("SIGNED_OUT", "LOGIN_CANCELLED")
    finally:
        commands.cancel_waits()


class RecordedWait:
    """Passes the port through and marks when the login wait has returned."""

    def __init__(self, inner, returned):
        self._inner = inner
        self._returned = returned

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def wait_login(self, login_id):
        try:
            return await self._inner.wait_login(login_id)
        finally:
            self._returned.set()


@pytest.mark.asyncio
async def test_a_completion_inside_the_cancel_rpc_does_not_overwrite_the_cancelled_view():
    """The waiter wakes during the cancel RPC and is cancelled while it waits for the lock."""

    returned = asyncio.Event()
    replies = []

    class CompletesDuringCancel(BrowserServer):
        def _request(self, message):
            if message["method"] != "account/login/cancel":
                super()._request(message)
                return
            self.cancelled.append(message["params"]["loginId"])
            self.finish(success=False, signed_in=False)
            replies.append(asyncio.ensure_future(self.reply_after_wait(message["id"])))

        async def reply_after_wait(self, request_id):
            await returned.wait()
            self.emit({"id": request_id, "result": {"status": "canceled"}})

    fake = CompletesDuringCancel()
    client = CodexAccountClient(fake, version="test", open_browser=opened)
    await client.start()
    outlet = Outlet()
    commands = AccountCommands(NoCapability(), outlet, lambda: False, consume)
    port = RecordedWait(client, returned)
    try:
        assert await send(commands, port, "account_login_browser") == "ACCOUNT_PENDING_BROWSER"
        await asyncio.sleep(0)
        cancelling = send(commands, port, "account_cancel_login", "browser-1")
        assert await asyncio.wait_for(cancelling, 2) == "ACCOUNT_SIGNED_OUT"
        assert returned.is_set()  # the wait returned before the cancel reply
        for _ in range(3):
            await asyncio.sleep(0)
        assert "LOGIN_NOT_COMPLETED" not in [view.state for view in outlet.views]
        last = outlet.views[-1]
        assert (last.state, last.notice) == ("SIGNED_OUT", "LOGIN_CANCELLED")
        assert fake.cancelled == ["browser-1"]
    finally:
        commands.cancel_waits()
        await client.aclose()


def test_browser_timeout_cancels_codex_login(app, monkeypatch):
    monkeypatch.setattr("guard_app.services.bridge_commands.DEVICE_LOGIN_TIMEOUT_S", 0.02)
    fake, loops, views = BrowserServer(), [], []
    bridge = browser_bridge(fake, loops)
    bridge.account_changed.connect(views.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        bridge.request_account("account_login_browser")
        pump_until(app, lambda: views[-1].notice == "LOGIN_TIMEOUT")
        assert views[-1].state == "LOGIN_NOT_COMPLETED"
        assert views[-1].browser_available
        assert fake.cancelled == ["browser-1"]
    finally:
        close_bridge(app, bridge)


def test_browser_panel_uses_backend_state_without_exposing_login_url(app):
    form = CodexLoginInfo()
    requests = []
    form.browser_login_requested.connect(lambda: requests.append("browser"))
    form.set_view(AccountView("SIGNED_OUT"))
    assert not form.browser_button.isEnabled()
    form.set_view(AccountView("SIGNED_OUT", browser_available=True))
    form.browser_button.click()
    assert requests == ["browser"]
    assert not form.login_button.isEnabled()
    form.set_view(AccountView("PENDING_BROWSER", login_id="browser-1", browser_available=True))
    assert not form.api_key_input.isEnabled()
    assert not form.read_button.isEnabled()
    assert form.cancel_button.isEnabled()
    assert form.device.text() == ""
    assert "Guard로 돌아오세요" in form.status.text()
    assert "private-login-state" not in " ".join(w.text() for w in form.findChildren(QLabel))
    form.set_view(AccountView("CHATGPT", browser_available=True))
    assert not form.browser_button.isEnabled()
    assert form.logout_button.isEnabled()
    form.close()


def test_window_routes_browser_command_and_recovers_after_queue_rejection(app):
    async def unused():
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    calls = []
    bridge.request_account = lambda action, **kwargs: calls.append((action, kwargs)) or "c"
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    login = window.codex_login_info
    login.set_view(AccountView("SIGNED_OUT", browser_available=True))
    login.browser_button.click()
    assert calls == [("account_login_browser", {})]
    bridge.command_changed.emit(
        CommandEvent(
            "c",
            "control",
            "account_login_browser",
            "account",
            CommandStage.REJECTED,
            "QUEUE_FULL",
        )
    )
    assert login.browser_button.isEnabled()
    # Nothing ran, so the signed-out state stays and the refusal is named.
    assert login.view.state == "SIGNED_OUT"
    assert "접수하지 않음" in login.status.text()
    window.close()
    bridge.close()


class RefusingServer(BrowserServer):
    """Codex answers every login start with a JSON-RPC error."""

    def _request(self, message):
        if message["method"] == "account/login/start":
            self.login_requests.append(message["params"])
            self.emit({"id": message["id"], "error": {"code": -1, "message": "PRIVATE"}})
        else:
            super()._request(message)


@pytest.mark.asyncio
async def test_a_refused_login_start_keeps_the_account_process():
    # Codex answered, so nothing it refused is left running (A109 follow-up).
    server = RefusingServer()
    access = FakeAccess(server=lambda: server)
    access.access = lambda: replace(FakeAccess.access(access), open_browser=opened)
    account = product_account(access)
    async with aclosing(account):
        for start in (
            lambda: account.login_api_key("sk-refused"),
            account.start_device_login,
            account.start_browser_login,
        ):
            with pytest.raises(AccountRefusedError, match="^CODEX_ACCOUNT_FAILED$"):
                await start()
        assert [params["type"] for params in server.login_requests] == [
            "apiKey",
            "chatgptDeviceCode",
            "chatgpt",
        ]
        assert (await account.read()).state == "API_KEY"
        assert len(access.processes) == 1
        assert access.processes[0].closes == 0


async def refused_open(_url):
    return False


@pytest.mark.asyncio
async def test_a_refused_cancel_ends_the_account_process():
    for opener in [opened, refused_open]:
        # The login callback may still run after Codex refuses to cancel it.
        server = BrowserServer()
        server.refuse_cancel = True
        access = FakeAccess(server=lambda *, server=server: server)
        access.access = lambda *, access=access, opener=opener: replace(
            FakeAccess.access(access), open_browser=opener
        )
        account = product_account(access)

        async def start_and_cancel(*, account=account):
            # A refused opener cancels inside the start; an opened one here.
            pending = await account.start_browser_login()
            await account.cancel_login(pending.login_id)

        async with aclosing(account):
            with pytest.raises(AccountError) as failed:
                await start_and_cancel()
            assert type(failed.value) is AccountError
            assert server.cancelled == ["browser-1"]
            assert access.processes[0].closes == 1


def account_event(action, stage, code):
    return CommandEvent("c", "control", action, "account", stage, code)


def test_an_unconfirmed_account_command_names_what_was_not_confirmed(app):
    for action, notice in [
        ("account_read", "상태 확인 결과를 받지 못함"),
        ("account_logout", "로그아웃 처리를 확인하지 못함"),
        ("account_cancel_login", "로그인 취소 처리를 확인하지 못함"),
        ("account_login_api_key", "로그인 요청 처리를 확인하지 못함"),
        ("account_login_device", "로그인 요청 처리를 확인하지 못함"),
        ("account_login_browser", "로그인 요청 처리를 확인하지 못함"),
    ]:
        form = CodexLoginInfo()
        form.set_view(AccountView("CHATGPT", browser_available=True))
        form.account_command_changed(
            account_event(action, CommandStage.UNCONFIRMED, "BACKEND_UNCONFIRMED")
        )
        assert form.view.state == "FAILED"
        assert form.view.browser_available
        assert notice in form.status.text()
        assert ("로그인 요청" in form.status.text()) == action.startswith("account_login_")
        form.close()


def test_a_refused_account_command_keeps_the_last_state(app):
    form = CodexLoginInfo()
    pending = AccountView("PENDING_BROWSER", login_id="browser-1", browser_available=True)
    form.set_view(pending)
    form.cancel_button.click()
    assert not form.cancel_button.isEnabled()
    form.account_command_changed(
        account_event("account_cancel_login", CommandStage.REJECTED, "QUEUE_FULL")
    )
    # The login is still pending, so it can be cancelled again.
    assert form.view == pending
    assert form.cancel_button.isEnabled()
    assert COMMAND_REFUSALS["QUEUE_FULL"] in form.status.text()
    form.set_view(AccountView("CHATGPT", browser_available=True))
    form.account_command_changed(
        account_event("account_logout", CommandStage.REJECTED, "SHUTTING_DOWN")
    )
    assert form.view.state == "CHATGPT"
    assert form.logout_button.isEnabled()
    assert ACCOUNT_REQUEST_REFUSED in form.status.text()
    assert "확인하지 못함" not in form.status.text()
    form.close()
