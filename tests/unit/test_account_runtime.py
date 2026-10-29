"""A52/#21: synthetic logout ordering (C2.12/C2.15); real codex/keyring/T43 excluded."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.services.account_runtime import (
    LOGOUT_CLEANUP_FAILED,
    LOGOUT_EXIT_UNCONFIRMED,
    LOGOUT_SESSIONS_RUNNING,
    AccountAccess,
    AccountUnavailableError,
    ProductAccount,
)
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    ControlAction,
    SessionOptions,
)
from guard_app.services.codex_account import AccountError, AccountView
from guard_app.services.session_runtime import SessionRuntimeError
from guard_app.ui.model_settings import CodexLoginInfo
from guard_app.ui.presentation import codex_failure_notice, session_blocker_notice

from .fake_codex_app_server import FakeAppServer
from .test_bridge import FakeControl, close_bridge, pump_until
from .test_session_runtime import Session, make_runtime

LIFECYCLE = ("launch", "account/logout", "exit", "clear")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class LogoutRefusedServer(FakeAppServer):
    def _request(self, message: dict[str, object]) -> None:
        if message["method"] == "account/logout":
            error = {"code": -32000, "message": "private-detail"}
            self.emit({"id": message["id"], "error": error})
            return
        super()._request(message)


class FakeProcess:
    def __init__(self, server: FakeAppServer, access: FakeAccess) -> None:
        self.pipes = server
        self._access = access
        self.closes = 0

    async def close(self) -> bool:
        self.closes += 1
        self._access.events.append("exit")
        # The login lives in CODEX_HOME, so the next app-server reads it.
        self._access.account = self.pipes.account
        return self._access.exits


class FakeAccess:
    """C's launcher and credential cleanup, recording the order they ran in."""

    def __init__(
        self,
        *,
        server: Callable[[], FakeAppServer] = FakeAppServer,
        exits: bool = True,
        cleared: bool = True,
    ) -> None:
        self.server = server
        self.exits = exits
        self.cleared = cleared
        self.account: dict[str, object] | None = {"type": "apiKey"}
        self.events: list[str] = []
        self.processes: list[FakeProcess] = []
        self.clear_gate: asyncio.Event | None = None
        self.clearing = asyncio.Event()

    async def launch(self) -> FakeProcess:
        server = self.server()
        server.account = self.account
        server.on_write = lambda message: self.events.append(str(message.get("method")))
        process = FakeProcess(server, self)
        self.processes.append(process)
        self.events.append("launch")
        return process

    async def clear_credentials(self) -> bool:
        self.events.append("clear")
        self.clearing.set()
        if self.clear_gate is not None:
            await self.clear_gate.wait()
        return self.cleared

    def access(self) -> AccountAccess:
        return AccountAccess(self.launch, self.clear_credentials)

    def lifecycle(self) -> list[str]:
        return [event for event in self.events if event in LIFECYCLE]


def product_account(
    fake: FakeAccess, sessions_alive: Callable[[], bool] = lambda: False
) -> ProductAccount:
    return ProductAccount(fake.access(), version="0.1.0", sessions_alive=sessions_alive)


@pytest.mark.asyncio
async def test_logout_ends_the_app_server_before_c_deletes_the_credentials() -> None:
    fake = FakeAccess()
    account = product_account(fake)
    assert (await account.read()).state == "API_KEY"
    view = await account.logout()
    assert (view.state, view.notice) == ("SIGNED_OUT", None)
    assert fake.lifecycle() == list(LIFECYCLE)
    assert not account.signing_out
    # The next request starts a new app-server, which reads the signed-out home.
    assert (await account.read()).state == "SIGNED_OUT"
    assert len(fake.processes) == 2
    assert await account.aclose() is True
    assert [process.closes for process in fake.processes] == [1, 1]
    with pytest.raises(AccountError):
        await account.read()
    assert len(fake.processes) == 2


@pytest.mark.asyncio
async def test_logout_is_refused_while_a_session_may_run() -> None:
    fake = FakeAccess()
    alive = [True]
    account = product_account(fake, lambda: alive[0])
    view = await account.logout()
    assert (view.state, view.notice) == ("API_KEY", LOGOUT_SESSIONS_RUNNING)
    assert fake.lifecycle() == ["launch"]
    assert not account.signing_out
    alive[0] = False
    assert (await account.logout()).notice is None
    assert fake.lifecycle() == list(LIFECYCLE)
    await account.aclose()


@pytest.mark.asyncio
async def test_credentials_stay_when_codex_refuses_the_logout() -> None:
    fake = FakeAccess(server=LogoutRefusedServer)
    account = product_account(fake)
    with pytest.raises(AccountError) as refused:
        await account.logout()
    assert str(refused.value) == "CODEX_ACCOUNT_FAILED"
    assert fake.lifecycle() == ["launch", "account/logout"]
    assert not account.signing_out
    # The same app-server keeps answering.
    assert (await account.read()).state == "API_KEY"
    assert len(fake.processes) == 1
    assert await account.aclose() is True


@pytest.mark.asyncio
async def test_credentials_stay_when_the_exit_is_not_confirmed() -> None:
    fake = FakeAccess(exits=False)
    account = product_account(fake)
    view = await account.logout()
    assert (view.state, view.notice) == ("SIGNED_OUT", LOGOUT_EXIT_UNCONFIRMED)
    assert fake.lifecycle() == ["launch", "account/logout", "exit"]
    # A process that may still run is never replaced by another one.
    with pytest.raises(AccountError):
        await account.read()
    assert len(fake.processes) == 1
    assert await account.aclose() is False


@pytest.mark.asyncio
async def test_a_failed_credential_cleanup_is_reported_and_can_be_asked_again() -> None:
    fake = FakeAccess(cleared=False)
    account = product_account(fake)
    view = await account.logout()
    assert (view.state, view.notice) == ("SIGNED_OUT", LOGOUT_CLEANUP_FAILED)
    fake.cleared = True
    assert (await account.logout()).notice is None
    assert fake.lifecycle() == list(LIFECYCLE) * 2
    assert await account.aclose() is True


@pytest.mark.asyncio
async def test_an_app_server_that_reports_remote_control_is_ended_not_reused() -> None:
    # C-CODEX-07: the account client refuses it; the owner ends it and starts afresh next time.
    fake = FakeAccess(server=lambda: FakeAppServer(remote_control="connected"))
    account = product_account(fake)
    for _ in range(2):
        with pytest.raises(AccountError):
            await account.read()
    assert [process.closes for process in fake.processes] == [1, 1]
    assert await account.aclose() is True


@pytest.mark.asyncio
async def test_a_run_whose_startup_refused_codex_shows_login_as_unavailable() -> None:
    launches: list[str] = []

    async def launch() -> FakeProcess:
        launches.append("launch")
        raise AccountUnavailableError()

    async def clear() -> bool:
        raise AssertionError("nothing to clear without an app-server")

    for alive in (False, True):
        account = ProductAccount(
            AccountAccess(launch, clear), version="0.1.0", sessions_alive=lambda a=alive: a
        )
        assert (await account.read()).state == "UNAVAILABLE"
        assert (await account.start_device_login()).state == "UNAVAILABLE"
        assert (await account.logout()).state == "UNAVAILABLE"
        assert not account.signing_out
        assert await account.aclose() is True
    assert len(launches) == 6


@pytest.mark.asyncio
async def test_no_session_starts_while_a_logout_runs(tmp_path) -> None:
    fake = FakeAccess()
    fake.clear_gate = asyncio.Event()
    runtime, sessions = make_runtime(tmp_path, account=fake.access())
    try:
        account = runtime.bindings().account
        assert isinstance(account, ProductAccount)
        logout = asyncio.ensure_future(account.logout())
        await asyncio.wait_for(fake.clearing.wait(), 2)
        assert runtime.creation_blocker() == "CODEX_SIGNING_OUT"
        with pytest.raises(SessionRuntimeError, match="^CODEX_SIGNING_OUT$"):
            await runtime.create_session(SessionOptions())
        assert sessions == []
        fake.clear_gate.set()
        assert (await logout).notice is None
        assert runtime.creation_blocker() is None
        created = await runtime.create_session(SessionOptions())
        # An open session refuses the next logout until it is stopped and cleaned up.
        assert (await account.logout()).notice == LOGOUT_SESSIONS_RUNNING
        await runtime.stop_session(created.task_id, created.epoch, ControlAction.CANCEL)
        assert (await account.logout()).notice is None
        assert fake.lifecycle().count("clear") == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_logout_is_refused_while_a_session_is_being_created(tmp_path) -> None:
    fake = FakeAccess()
    entered, release = asyncio.Event(), asyncio.Event()

    async def factory(state, options, resources):
        entered.set()
        await release.wait()
        return Session()

    runtime, _ = make_runtime(tmp_path, factory=factory, account=fake.access())
    try:
        creating = asyncio.ensure_future(runtime.create_session(SessionOptions()))
        await asyncio.wait_for(entered.wait(), 2)
        account = runtime.bindings().account
        assert account is not None
        assert (await account.logout()).notice == LOGOUT_SESSIONS_RUNNING
        assert "account/logout" not in fake.events
        release.set()
        await creating
    finally:
        await runtime.close()


@pytest.mark.parametrize("exits", [True, False])
@pytest.mark.asyncio
async def test_runtime_close_ends_the_login_app_server(tmp_path, exits) -> None:
    fake = FakeAccess(exits=exits)
    runtime, _ = make_runtime(tmp_path, account=fake.access())
    account = runtime.bindings().account
    assert account is not None
    await account.read()
    if exits:
        await runtime.close()
    else:
        with pytest.raises(SessionRuntimeError, match="^CODEX_CLEANUP_UNCONFIRMED$"):
            await runtime.close()
    assert fake.processes[0].closes == 1


@pytest.mark.asyncio
async def test_runtime_without_account_access_leaves_login_unavailable(tmp_path) -> None:
    runtime, _ = make_runtime(tmp_path)
    try:
        assert runtime.bindings().account is None
    finally:
        await runtime.close()


def test_login_panel_explains_a_refused_or_partial_logout(app) -> None:
    panel = CodexLoginInfo()
    panel.set_view(AccountView("API_KEY", notice=LOGOUT_SESSIONS_RUNNING))
    assert "세션을 모두 끝낸 뒤 다시 시도하세요" in panel.status.text()
    assert panel.logout_button.isEnabled()
    panel.set_view(AccountView("SIGNED_OUT", notice=LOGOUT_CLEANUP_FAILED))
    assert "저장된 로그인 정보를 지우지 못함" in panel.status.text()
    assert panel.logout_button.isEnabled()  # the cleanup can be asked again
    assert panel.login_button.isEnabled()
    panel.set_view(AccountView("SIGNED_OUT", notice=LOGOUT_EXIT_UNCONFIRMED))
    assert "앱을 다시 시작하세요" in panel.status.text()
    assert not panel.logout_button.isEnabled()
    # An unknown code is never echoed.
    panel.set_view(AccountView("SIGNED_OUT", notice="PRIVATE_CODE"))
    assert panel.status.text() == "로그인되어 있지 않음."
    assert not panel.logout_button.isEnabled()
    panel.close()


def test_session_notices_say_a_logout_is_running() -> None:
    assert "로그아웃이 끝난 뒤 새 세션을 시작하세요" in session_blocker_notice("CODEX_SIGNING_OUT")
    created = codex_failure_notice("CODEX_SIGNING_OUT", creating=True)
    assert "세션을 시작하지 않았음" in created
    assert "로그아웃이 끝난 뒤" in created


def test_bridge_republishes_whether_sessions_may_start_after_a_logout(app) -> None:
    blocker: list[str | None] = ["CODEX_SIGNING_OUT"]
    codes: list[str] = []
    views: list[AccountView] = []

    class Account:
        async def logout(self) -> AccountView:
            blocker[0] = None
            return AccountView("SIGNED_OUT")

    async def bootstrap():
        async def create(options):
            raise SessionRuntimeError("CODEX_SIGNING_OUT")

        return BackendBindings(
            lambda task_id: None,
            FakeControl(),
            account=Account(),
            create_session=create,
            session_blocker=lambda: blocker[0],
        )

    bridge = BackendBridge(bootstrap)
    bridge.session_capability_changed.connect(codes.append)
    bridge.account_changed.connect(views.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(codes))
        assert codes[-1] == "CODEX_SIGNING_OUT"
        bridge.request_account("account_logout")
        pump_until(app, lambda: bool(views) and views[-1].state == "SIGNED_OUT")
        pump_until(app, lambda: codes[-1] == "")
    finally:
        close_bridge(app, bridge)
