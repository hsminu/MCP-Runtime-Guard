"""A89/A52/I9: synthetic account methods; real C2.12 launcher is not exercised."""

from __future__ import annotations

import asyncio
import os
from contextlib import aclosing, asynccontextmanager, contextmanager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from guard_app.services.bridge import BackendBindings, BackendBridge, BridgeState
from guard_app.services.codex_account import AccountError, AccountView, CodexAccountClient
from guard_app.ui.model_settings import CodexLoginInfo

from .fake_codex_app_server import FakeAppServer
from .test_bridge import FakeControl, attach, close_bridge, pump_until


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


async def client(fake: FakeAppServer, *, version="0.1.0", **options) -> CodexAccountClient:
    account = CodexAccountClient(fake, version=version, **options)
    await account.start()
    return account


def _fields(view: AccountView) -> str:
    return " ".join(
        str(getattr(view, name))
        for name in ("state", "plan", "login_id", "verification_url", "user_code")
    )


@pytest.mark.asyncio
async def test_device_code_login_shows_url_and_code_then_reads_the_plan_without_email() -> None:
    fake = FakeAppServer()
    async with running_account(fake) as account:
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
        assert (await account.login_api_key("  sk-unit  ")).state == "API_KEY"
        assert fake.login_requests == [{"type": "chatgptDeviceCode"}] * 3 + [
            {"type": "apiKey", "apiKey": "sk-unit"}
        ]
        assert (await account.login_api_key("   ")).state == "FAILED"


@pytest.mark.asyncio
async def test_remote_control_not_disabled_ends_the_account_connection() -> None:
    for status in ["connected", None]:
        # C-CODEX-07: remote attachment also invalidates the login connection.
        fake = FakeAppServer()
        async with running_account(fake) as account:
            assert (await account.start_device_login()).state == "PENDING_DEVICE"
            waiting = asyncio.ensure_future(account.wait_login("login-1"))
            await asyncio.sleep(0)
            fake.remote_control_status(status)
            fake.finish_device_login()  # never read once remote control is reported
            assert (await asyncio.wait_for(waiting, 2)).state == "FAILED"
            with pytest.raises(AccountError):
                await account.login_api_key("sk-unit")
            assert fake.login_requests == [{"type": "chatgptDeviceCode"}]


@pytest.mark.asyncio
async def test_remote_control_reported_while_starting_fails_the_account_start() -> None:
    account = CodexAccountClient(FakeAppServer(remote_control="connected"), version="0.1.0")
    async with aclosing(account):
        with pytest.raises(AccountError):
            await asyncio.wait_for(account.start(), 2)


def test_login_panel_has_only_the_masked_key_field_and_follows_the_state(app) -> None:
    panel = CodexLoginInfo()
    assert panel.findChildren(QLineEdit) == [panel.api_key_input]
    assert panel.api_key_input.echoMode() == QLineEdit.EchoMode.Password
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

    bridge = _account_bridge(fake, loops, control_workers=4)
    bridge.account_changed.connect(views.append)
    with running_bridge(app, bridge):
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


def _account_bridge(fake, loops, *, control_workers=1):
    async def bootstrap():
        loops.append(asyncio.get_running_loop())
        account = await client(fake)
        return BackendBindings(
            lambda task_id: None, FakeControl(), close=account.aclose, account=account
        )

    # One control worker: the lane approvals and login cancel share (A89 QA).
    return BackendBridge(bootstrap, control_workers=control_workers)


def test_device_login_wait_does_not_hold_the_only_control_worker(app) -> None:
    fake = FakeAppServer()
    views: list[AccountView] = []
    loops: list[asyncio.AbstractEventLoop] = []
    bridge = _account_bridge(fake, loops)
    receiver = attach(bridge)
    bridge.account_changed.connect(views.append)
    with running_bridge(app, bridge):
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(views))
        login = bridge.request_account("account_login_device")
        pump_until(app, lambda: views[-1].state == "PENDING_DEVICE")
        pump_until(
            app, lambda: any(e.stage.value == "backend_returned" for e in receiver.stages(login))
        )
        # The user never finishes the device login; cancel must still run.
        cancel = bridge.request_account("account_cancel_login", target_id="login-1")
        pump_until(
            app, lambda: any(e.stage.value == "backend_returned" for e in receiver.stages(cancel))
        )
        pump_until(app, lambda: views[-1].notice == "LOGIN_CANCELLED")
        states = [view.state for view in views]
        assert "SIGNED_OUT" in states[states.index("PENDING_DEVICE") :]
        assert "CHATGPT" not in states
        assert states[-1] == "SIGNED_OUT"  # cancelled waiter cannot overwrite account/read


def test_unfinished_device_login_times_out_as_not_completed(app, monkeypatch) -> None:
    monkeypatch.setattr("guard_app.services.bridge_commands.DEVICE_LOGIN_TIMEOUT_S", 0.05)
    fake = FakeAppServer()
    views: list[AccountView] = []
    loops: list[asyncio.AbstractEventLoop] = []
    bridge = _account_bridge(fake, loops)
    bridge.account_changed.connect(views.append)
    with running_bridge(app, bridge):
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(views))
        bridge.request_account("account_login_device")
        pump_until(app, lambda: views[-1].state == "LOGIN_NOT_COMPLETED")
        states = [view.state for view in views]
        assert states[-2:] == ["PENDING_DEVICE", "LOGIN_NOT_COMPLETED"]
        # The abandoned wait no longer holds the control lane.
        bridge.request_account("account_read")
        pump_until(app, lambda: len(views) > len(states))
        assert views[-1].state == "SIGNED_OUT"


@pytest.mark.asyncio
async def test_shutdown_cancels_device_wait_before_closing_account_resources(app):
    # A137 M16, I6/T20 support: leftover-loop cancellation must not mask omission.
    account = await client(FakeAppServer())
    checked = []
    waiters = ()

    async def close():
        checked.append(all(task.cancelled() or task.cancelling() for task in waiters))
        await account.aclose()

    async def bootstrap():
        return BackendBindings(lambda _: None, FakeControl(), close=close, account=account)

    bridge = BackendBridge(bootstrap)
    boot = asyncio.create_task(bootstrap())
    bindings = await asyncio.wait_for(boot, 2)
    assert bridge._host._bindings.adopt(boot)
    from guard_app.services.bridge_ingress import account_command

    try:
        await asyncio.wait_for(
            bridge._host._accounts.account(
                bindings, account_command("account_login_device", "account", None)
            ),
            2,
        )
        waiters = tuple(bridge._host._accounts._waits)
        assert len(waiters) == 1
        assert not waiters[0].done()
        await asyncio.wait_for(bridge._host._shut_down(), 2)
        assert checked == [True]
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.wait_for(asyncio.gather(*waiters, return_exceptions=True), 2)
        await asyncio.wait_for(account.aclose(), 2)


@pytest.mark.parametrize("fails", [False, True])
def test_logout_republishes_session_capability_even_on_error(app, fails):
    # A137 M30, I6: readiness is re-read after logout, not a stale startup value.
    capabilities, views = [], []
    blocker = "LOGOUT_IN_PROGRESS"

    class Account:
        async def logout(self):
            nonlocal blocker
            blocker = "LOGIN_REQUIRED"
            if fails:
                raise AccountError()
            return AccountView("SIGNED_OUT")

    async def bootstrap():
        return BackendBindings(
            lambda _: None,
            FakeControl(),
            account=Account(),
            create_session=lambda _: None,
            session_blocker=lambda: blocker,
        )

    bridge = BackendBridge(bootstrap)
    bridge.session_capability_changed.connect(capabilities.append)
    bridge.account_changed.connect(views.append)
    receiver = attach(bridge)
    try:
        bridge.start()
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(capabilities))
        assert capabilities == ["LOGOUT_IN_PROGRESS"]
        command = bridge.request_account("account_logout")
        pump_until(app, lambda: receiver.has_stage(command))
        assert capabilities == ["LOGOUT_IN_PROGRESS", "LOGIN_REQUIRED"]
        assert views[-1].state == ("FAILED" if fails else "SIGNED_OUT")
    finally:
        close_bridge(app, bridge)


@asynccontextmanager
async def running_account(fake, **options):
    async with aclosing(await client(fake, **options)) as account:
        yield account


@contextmanager
def running_bridge(app, bridge, *, window=None):
    bridge.start()
    try:
        yield bridge
    finally:
        if window is not None:
            window.close()
        close_bridge(app, bridge)
