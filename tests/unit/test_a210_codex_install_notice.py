"""A210: fixed installation notices, I6/I9/I11; T19/T22/T44 unit support.

No real Codex or network calls. Executable refusal precedes launcher construction.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import traceback
from contextlib import closing
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from guard_app.services import codex_preparation
from guard_app.services.account_runtime import (
    AccountAccess,
    AccountUnavailableError,
    ProductAccount,
)
from guard_app.services.bridge import AccountView
from guard_app.services.bridge_types import BackendCleanupError
from guard_app.services.codex_preparation import CodexPreparationError
from guard_app.services.session_admission import SessionAdmission, SessionRuntimeError
from guard_app.services.session_preparation import CODEX_INSTALL_CODES, PreparationRefusedError
from guard_app.services.session_startup import StartupGate, call_preparation
from guard_app.ui.model_settings import UNAVAILABLE, CodexLoginInfo
from guard_app.ui.presentation import (
    CODEX_INSTALL_COMMAND,
    codex_failure_notice,
    codex_install_notice,
    session_blocker_notice,
)
from guard_app.ui.window import GuardWindow

from .test_codex_preparation import PYTHON, preparation
from .test_session_preparation import Harness
from .test_ui_icons_style import closing_widgets

INSTALL_DETAILS = [
    ("CODEX_EXECUTABLE_MISSING", "codex.exe가 설치되지 않음."),
    ("CODEX_EXECUTABLE_MISMATCH", "설치된 codex.exe가 고정 버전과 다름."),
]
INSTALL_STEP = " 프로젝트 폴더에서 아래 명령을 실행한 뒤 앱을 다시 시작하세요."


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(CODEX_INSTALL_CODES))
async def test_preparation_preserves_only_installation_codes(code):
    async def refused():
        raise PreparationRefusedError(code)

    with pytest.raises(SessionRuntimeError) as caught:
        await call_preparation(refused)
    assert caught.value.code == str(caught.value) == code
    assert caught.value.__cause__ is caught.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        PreparationRefusedError("CODEX_FOLDERS_UNCLAIMED"),
        RuntimeError(r"raw text C:\secret"),
        RuntimeError("CODEX_EXECUTABLE_MISSING"),
    ],
)
async def test_preparation_drops_unknown_code_and_raw_exception_chain(failure):
    async def refused():
        raise failure

    with pytest.raises(SessionRuntimeError) as caught:
        await call_preparation(refused)
    assert str(caught.value) == caught.value.code == "CODEX_PREPARATION_FAILED"
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert str(failure) not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_preparation_keeps_cleanup_failure():
    async def refused():
        raise BackendCleanupError()

    with pytest.raises(BackendCleanupError) as caught:
        await call_preparation(refused)
    assert caught.value.__cause__ is caught.value.__context__ is None


@pytest.mark.asyncio
async def test_preparation_propagates_callback_cancellation():
    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await call_preparation(cancelled)


@pytest.mark.asyncio
async def test_preparation_propagates_caller_cancellation_to_callback():
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def waiting():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    operation = asyncio.create_task(call_preparation(waiting))
    await entered.wait()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert exited.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(CODEX_INSTALL_CODES))
async def test_startup_gate_keeps_first_refusal_without_retry(code):
    harness = Harness()
    calls = 0

    async def refused():
        nonlocal calls
        calls += 1
        raise PreparationRefusedError(code)

    gate = StartupGate(replace(harness.preparation, prepare_startup=refused), SessionAdmission())
    assert gate.blocker() is None
    for _ in range(2):
        with pytest.raises(SessionRuntimeError) as caught:
            await gate.check()
        assert caught.value.code == code
        assert gate.blocker() == code
    assert calls == 1
    assert harness.factories == harness.store.creates == 0
    await harness.runtime.close()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="App folders require Windows")
@pytest.mark.parametrize("code", sorted(CODEX_INSTALL_CODES))
async def test_startup_executable_refusal_reaches_account_without_launch(
    tmp_path, monkeypatch, code
):
    data = PYTHON.read_bytes()
    size = 1 if code == "CODEX_EXECUTABLE_MISMATCH" else len(data)
    executable = PYTHON if code == "CODEX_EXECUTABLE_MISMATCH" else None
    launchers = []

    def forbidden_launcher(*args, **kwargs):
        launchers.append(True)
        raise AssertionError("refused executable must not reach launcher")

    monkeypatch.setattr(codex_preparation, "CodexLauncher", forbidden_launcher)
    owner = preparation(tmp_path, (size, hashlib.sha256(data).hexdigest()), executable=executable)
    with closing(owner):
        with pytest.raises(CodexPreparationError) as startup:
            await owner.preparation().prepare_startup()
        assert startup.value.code == code
        with pytest.raises(AccountUnavailableError) as account:
            await owner.account_access().launch()
        assert account.value.code == code
        assert str(tmp_path) not in str(startup.value) + str(account.value)
        assert not list((tmp_path / "app" / "tasks").iterdir())
    assert launchers == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["CODEX_EXECUTABLE_MISSING", "CODEX_EXECUTABLE_MISMATCH", None, "X"]
)
@pytest.mark.parametrize("operation_name", ["read", "logout", "logout_with_sessions"])
async def test_product_account_only_exposes_allowlisted_notice(code, operation_name):
    async def launch():
        if code is None:
            raise AccountUnavailableError()
        raise AccountUnavailableError(code)

    async def clear_credentials():
        raise AssertionError("unavailable account cannot clear credentials")

    account = ProductAccount(
        AccountAccess(launch, clear_credentials),
        version="unit",
        sessions_alive=lambda: operation_name == "logout_with_sessions",
    )
    try:
        view = await (account.read() if operation_name == "read" else account.logout())
        notice = code if code in CODEX_INSTALL_CODES else None
        assert view == AccountView("UNAVAILABLE", notice=notice)
        assert not account.signing_out
    finally:
        assert await account.aclose()


@pytest.mark.parametrize(("code", "detail"), INSTALL_DETAILS)
def test_installation_presentation_matches_exact_guidance(code, detail):
    assert CODEX_INSTALL_COMMAND == r".venv\Scripts\python.exe tools\install_codex.py"
    assert codex_install_notice(code) == detail + INSTALL_STEP
    assert session_blocker_notice(code) == (
        detail + " 새 세션을 시작할 수 없음." + INSTALL_STEP + "\n" + CODEX_INSTALL_COMMAND
    )
    for creating, context in (
        (True, " 세션을 시작하지 않았음."),
        (False, " 이 Task를 실행할 수 없음."),
    ):
        assert codex_failure_notice(code, creating=creating) == (
            detail + context + INSTALL_STEP + "\n" + CODEX_INSTALL_COMMAND
        )


def test_unknown_installation_notice_is_empty():
    assert codex_install_notice(r"raw text C:\secret") == ""
    assert codex_install_notice("CODEX_PREPARATION_FAILED") == ""


@pytest.mark.parametrize(("code", "detail"), INSTALL_DETAILS)
def test_login_installation_command_visibility_selection_and_copy(code, detail):
    app = QApplication.instance() or QApplication([])
    form = CodexLoginInfo()
    form.show()
    with closing_widgets(form):
        form.set_view(AccountView("UNAVAILABLE", notice=code))
        app.processEvents()
        assert form.status.text() == detail + INSTALL_STEP
        assert form.install_row.isVisible()
        assert form.install_command.text() == CODEX_INSTALL_COMMAND
        assert form.install_command.objectName() == "mono"
        assert form.install_command.textFormat() == Qt.TextFormat.PlainText
        assert (
            form.install_command.textInteractionFlags()
            & Qt.TextInteractionFlag.TextSelectableByMouse
        )
        assert form.install_copy.accessibleName() == "설치 명령 복사"
        assert form.install_copy.toolTip() == "설치 명령 복사"
        assert not form.api_key_input.isVisible()
        assert not form.read_button.isVisible()
        clipboard = app.clipboard()
        previous = clipboard.text()
        try:
            clipboard.clear()
            form.install_copy.click()
            assert clipboard.text() == CODEX_INSTALL_COMMAND
        finally:
            clipboard.setText(previous)
        for view in (
            AccountView("UNAVAILABLE"),
            AccountView("UNAVAILABLE", notice="X"),
            AccountView("SIGNED_OUT"),
            AccountView("SIGNED_OUT", notice=code),
        ):
            form.set_view(view)
            assert form.install_row.isHidden()
            if view.state == "UNAVAILABLE":
                assert form.status.text() == UNAVAILABLE


@pytest.mark.parametrize("code", sorted(CODEX_INSTALL_CODES))
def test_chat_notice_keeps_plain_text_line_break(code):
    app = QApplication.instance() or QApplication([])
    window = GuardWindow()
    with closing_widgets(window):
        window.sessions.set_ready(True)
        window.sessions.set_session_blocker(code)
        window.show()
        app.processEvents()
        notice = window.sessions.current.notice
        assert notice.text() == session_blocker_notice(code)
        assert notice.text().splitlines()[-1] == CODEX_INSTALL_COMMAND
        assert notice.textFormat() == Qt.TextFormat.PlainText
        assert notice.wordWrap()
        assert notice.height() >= 2 * notice.fontMetrics().lineSpacing()
