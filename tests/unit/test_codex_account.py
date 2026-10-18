"""A89: Codex login through the app-server's own account methods (A52, I9).

No key or email reaches the Guard's display; the fake app-server stands in for
an app-server launched by C (C2.12), which is not exercised here.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from guard_app.services.bridge import BackendBindings, BackendBridge, BridgeState
from guard_app.services.codex_account import AccountView, CodexAccountClient
from guard_app.ui.model_settings import CodexLoginInfo

from .fake_codex_app_server import FakeAppServer
from .test_bridge import FakeControl, close_bridge, pump_until


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


async def client(fake: FakeAppServer) -> CodexAccountClient:
    account = CodexAccountClient(fake, version="0.1.0")
    await account.start()
    return account


def _fields(view: AccountView) -> str:
    return " ".join(
        str(getattr(view, name))
        for name in ("state", "plan", "login_id", "verification_url", "user_code")
    )


def test_device_code_login_shows_url_and_code_then_reads_the_plan_without_email() -> None:
    async def run() -> None:
        fake = FakeAppServer()
        account = await client(fake)
        try:
            assert (await account.read()).state == "SIGNED_OUT"
            pending = await account.start_device_login()
            assert pending.state == "PENDING_DEVICE"
            assert pending.verification_url == "https://auth.example/device"
            assert pending.user_code == "ABCD-1234"
            waiting = asyncio.ensure_future(account.wait_login("login-1"))
            await asyncio.sleep(0)
            fake.finish_device_login()
            done = await asyncio.wait_for(waiting, 2)
            assert (done.state, done.plan) == ("CHATGPT", "plus")
            assert "PRIVATE" not in _fields(done)
            assert (await account.logout()).state == "SIGNED_OUT"
        finally:
            await account.aclose()

    asyncio.run(run())


def test_cancelled_or_failed_device_login_is_not_completed() -> None:
    async def run() -> None:
        fake = FakeAppServer()
        account = await client(fake)
        try:
            await account.start_device_login()
            waiting = asyncio.ensure_future(account.wait_login("login-1"))
            await asyncio.sleep(0)
            fake.finish_device_login(success=False)
            assert (await asyncio.wait_for(waiting, 2)).state == "LOGIN_NOT_COMPLETED"
            await account.start_device_login()
            waiting = asyncio.ensure_future(account.wait_login("login-1"))
            await asyncio.sleep(0)
            assert (await account.cancel_login("login-1")).state == "SIGNED_OUT"
            assert (await asyncio.wait_for(waiting, 2)).state == "LOGIN_NOT_COMPLETED"
        finally:
            await account.aclose()

    asyncio.run(run())


def test_api_key_login_is_passed_to_codex_trimmed_and_reads_the_kind() -> None:
    async def run() -> None:
        fake = FakeAppServer()
        account = await client(fake)
        try:
            assert (await account.login_api_key("  sk-unit  ")).state == "API_KEY"
            assert fake.login_requests == [{"type": "apiKey", "apiKey": "sk-unit"}]
            assert (await account.login_api_key("   ")).state == "FAILED"
        finally:
            await account.aclose()

    asyncio.run(run())


def test_login_panel_never_has_a_secret_field_and_follows_the_state(app) -> None:
    panel = CodexLoginInfo()
    assert not panel.findChildren(QLineEdit)
    assert not panel.login_button.isEnabled()
    assert "준비 중" in panel.status.text()
    panel.set_view(AccountView("SIGNED_OUT"))
    assert panel.login_button.isEnabled()
    assert not panel.logout_button.isEnabled()
    cancelled: list[str] = []
    panel.cancel_requested.connect(cancelled.append)
    panel.set_view(
        AccountView(
            "PENDING_DEVICE", login_id="login-1", verification_url="https://u", user_code="C-1"
        )
    )
    assert "https://u" in panel.device.text()
    assert "C-1" in panel.device.text()
    assert not panel.login_button.isEnabled()
    panel.cancel_button.click()
    assert cancelled == ["login-1"]
    panel.set_view(AccountView("CHATGPT", plan="plus"))
    assert "요금제 plus" in panel.status.text()
    assert panel.logout_button.isEnabled()
    assert not panel.device.isVisible()
    texts = " ".join(label.text() for label in panel.findChildren(QLabel))
    assert "@" not in texts
    panel.close()


def test_bridge_routes_account_commands_and_reports_unavailable(app) -> None:
    fake = FakeAppServer()
    views: list[AccountView] = []
    loops: list[asyncio.AbstractEventLoop] = []

    async def bootstrap():
        loops.append(asyncio.get_running_loop())
        account = CodexAccountClient(fake, version="0.1.0")
        await account.start()

        async def close():
            await account.aclose()

        return BackendBindings(lambda task_id: None, FakeControl(), close=close, account=account)

    bridge = BackendBridge(bootstrap)
    bridge.account_changed.connect(views.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(views))
        assert views[-1].state == "UNKNOWN"
        bridge.request_account("account_read")
        pump_until(app, lambda: views[-1].state == "SIGNED_OUT")
        bridge.request_account("account_login_device")
        pump_until(app, lambda: views[-1].state == "PENDING_DEVICE")
        # The fake lives on the backend loop; drive it from that loop.
        loops[0].call_soon_threadsafe(fake.finish_device_login)
        pump_until(app, lambda: views[-1].state == "CHATGPT")
        with pytest.raises(ValueError, match="Account action"):
            bridge.request_account("account_steal")
    finally:
        close_bridge(app, bridge)

    unavailable: list[AccountView] = []

    async def bare():
        return BackendBindings(lambda task_id: None, FakeControl())

    other = BackendBridge(bare)
    other.account_changed.connect(unavailable.append)
    other.start()
    try:
        pump_until(app, lambda: other.state is BridgeState.READY and bool(unavailable))
        assert unavailable[-1].state == "UNAVAILABLE"
    finally:
        close_bridge(app, other)
