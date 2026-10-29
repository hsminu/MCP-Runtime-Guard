"""A128: I1/I6/I7/I9/I10, T10/T18/T29 and S02 registration boundaries."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import RegStatus
from guard_app.domain.errors import DispatchError, StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.security.endpoints import ENDPOINT_REJECT_CODES
from guard_app.security.ids import new_id
from guard_app.security.launcher import ExecutablePin
from guard_app.security.paths import prepare_app_folders
from guard_app.services import bridge_registration as module
from guard_app.services.bridge import BackendBridge, RegistrationRequest, RegistrationView
from guard_app.services.product_settings import SettingsFileSaver, read_settings
from guard_app.services.registry import ServerRegistry
from guard_app.services.session_admission import SessionAdmission
from guard_app.services.session_policy import TaskPolicy
from guard_app.storage.sqlite_store import SQLiteStore
from guard_app.ui.server_add import URL_MESSAGES, ServerAddForm

HTTP = RegistrationRequest(
    "prepare",
    "remote",
    "streamable_http",
    "general_external",
    "PUBLIC",
    url="HTTPS://EXAMPLE.COM:443/mcp",
)
STDIO = RegistrationRequest(
    "prepare", "local", "stdio", "internal", "CONFIDENTIAL", command=sys.executable
)


@asynccontextmanager
async def harness(tmp_path):
    folders = prepare_app_folders(tmp_path / "app")
    store = SQLiteStore(folders.db_path(), audit_id_factory=lambda: new_id("audit"))
    registry = ServerRegistry(store)
    await registry.load()
    admission = SessionAdmission()
    policy = TaskPolicy(None, admission, saver=SettingsFileSaver(read_settings(folders)))
    collected = []

    async def collect(server):
        collected.append(server)
        return SimpleNamespace(
            complete=True,
            tools=(
                FrozenJsonObject(
                    {"name": "read", "description": "untrusted", "inputSchema": {"type": "object"}}
                ),
            ),
        )

    async def stop(ids):
        assert ids == ()

    commands = module.RegistrationCommands(registry, store, policy, collect, stop)
    try:
        yield SimpleNamespace(
            commands=commands,
            registry=registry,
            store=store,
            policy=policy,
            folders=folders,
            collected=collected,
            admission=admission,
        )
    finally:
        store.close()
        assert await store.wait_closed(timeout=5)
        folders.close()


@contextmanager
def registration_form():
    app = QApplication.instance() or QApplication([])
    form = ServerAddForm()
    try:
        yield form
    finally:
        form.close()
        app.processEvents()


async def confirm(h, preview):
    return await h.commands(RegistrationRequest("confirm", token=preview.token))


@pytest.mark.asyncio
async def test_registration_requires_confirmation_and_persists_canonical_settings(tmp_path):
    async with harness(tmp_path) as h:
        preview = await h.commands(HTTP)
        assert preview.url == "https://example.com/mcp"
        assert preview.token
        assert not h.collected
        assert h.registry.entry("remote") is None
        assert (
            await confirm(h, replace(preview, token=new_id("forged")))
        ).code == "REG_CONFIRM_EXPIRED"
        result = await confirm(h, preview)
        assert result.code == "REG_REGISTERED"
        assert result.rows[0].status == "pending"
        entry = await h.store.load_registration("remote")
        assert entry.registration.status is RegStatus.PENDING
        assert entry.registration.boundary.max_label.name == "PUBLIC"
        assert entry.registration.tools[0].kind.value == "other"
        saved = read_settings(h.folders).document
        assert saved["servers"][0]["url"] == preview.url
        assert saved["profile"]["servers"] == []
        assert len(h.collected) == 1
        assert (await confirm(h, preview)).code == "REG_CONFIRM_EXPIRED"
        assert (await h.commands(HTTP)).code == "REG_SERVER_EXISTS"
        assert len(h.collected) == 1


@pytest.mark.asyncio
async def test_validation_rejection_has_no_registration_or_collection(tmp_path):
    async with harness(tmp_path) as h:
        for request, code in [
            (replace(HTTP, server_id="bad__id"), "REG_SETTINGS_INVALID"),
            (replace(HTTP, max_label="CONFIDENTIAL"), "REG_SETTINGS_INVALID"),
            (replace(HTTP, url="http://example.com"), "URL_HTTP_NOT_LOOPBACK"),
            (replace(STDIO, command="relative.exe"), "REG_EXECUTABLE_INVALID"),
        ]:
            assert (await h.commands(request)).code == code
        assert not h.collected
        assert await h.store.list_registrations() == ()


@pytest.mark.asyncio
async def test_real_executable_pin_is_displayed_and_saved(tmp_path):
    async with harness(tmp_path) as h:
        preview = await h.commands(STDIO)
        assert preview.command == sys.executable
        assert preview.size > 0
        assert len(preview.sha256) == 64
        assert not h.collected
        result = await confirm(h, preview)
        assert result.code == "REG_REGISTERED"
        assert result.rows[0].pinned
        saved = read_settings(h.folders).document["servers"][0]
        assert (saved["command_size"], saved["command_sha256"]) == (
            preview.size,
            preview.sha256,
        )


@pytest.mark.asyncio
async def test_changed_pin_requires_new_confirmation_and_recheck_stops_admission(
    tmp_path, monkeypatch
):
    current = ExecutablePin(10, "a" * 64)

    async def pin(command):
        return current

    monkeypatch.setattr(module, "pin_executable", pin)
    async with harness(tmp_path) as h:
        first = await h.commands(STDIO)
        current = ExecutablePin(20, "b" * 64)
        changed = await confirm(h, first)
        assert changed.code == "REG_PIN_CHANGED"
        assert changed.token != first.token
        assert changed.sha256 == "b" * 64
        assert changed.size == 20
        assert not h.collected
        assert h.registry.entry("local") is None
        assert (await confirm(h, first)).code == "REG_CONFIRM_EXPIRED"
        assert (await confirm(h, changed)).code == "REG_REGISTERED"
        current = ExecutablePin(30, "c" * 64)
        rows = await h.commands(RegistrationRequest("list"))
        assert rows.rows[0].pinned is False
        review = await h.commands(RegistrationRequest("recheck", server_id="local"))
        result = await confirm(h, review)
        assert result.code == "REG_PIN_SAVED"
        assert h.admission.restart_code == "REG_PIN_RESTART_REQUIRED"
        assert read_settings(h.folders).document["servers"][0]["command_sha256"] == "c" * 64


@pytest.mark.asyncio
async def test_store_failure_is_not_retried_or_reported_as_registered(tmp_path, monkeypatch):
    async with harness(tmp_path) as h:
        calls = []

        async def fail(*args, **kwargs):
            calls.append(1)
            raise StoreError()

        monkeypatch.setattr(h.store, "register_server", fail)
        preview = await h.commands(HTTP)
        assert (await confirm(h, preview)).code == "REG_STORE_UNKNOWN"
        assert (await h.commands(HTTP)).code == "REG_STORE_UNKNOWN"
        assert calls == [1]
        assert read_settings(h.folders).document is None


@pytest.mark.asyncio
async def test_a_server_that_never_starts_is_refused_before_any_write(tmp_path):
    """#83 C review 3.1: only a confirmed start failure is a refusal; teardown stays unknown."""

    def failing(kind):
        async def collect(server):
            raise DispatchError(kind)

        return collect

    async with harness(tmp_path) as h:
        collect = h.commands.collect
        for kind in ("SessionStartFailed", "SessionStartTimeout"):
            h.commands.collect = failing(kind)
            preview = await h.commands(HTTP)
            assert (await confirm(h, preview)).code == "REG_SERVER_UNREACHABLE"
        assert await h.store.list_registrations() == ()
        assert read_settings(h.folders).document is None
        h.commands.collect = failing("SessionCloseFailed")
        preview = await h.commands(HTTP)
        with pytest.raises(DispatchError):
            await confirm(h, preview)
        h.commands.collect = collect
        preview = await h.commands(HTTP)
        assert (await confirm(h, preview)).code == "REG_REGISTERED"
    with registration_form() as form:
        for code, shown in [
            ("REG_SERVER_UNREACHABLE", "서버에 연결하지 못해 등록하지 않음."),
            ("REG_LISTING_INVALID", "도구 목록을 끝까지 받지 못했거나"),
        ]:
            form._registration_id = code
            form._registration_result(RegistrationView(code, command_id=code))
            assert form.status.text().startswith(shown)


@pytest.mark.asyncio
async def test_settings_failure_leaves_pending_and_requires_restart(tmp_path):
    async with harness(tmp_path) as h:

        async def fail(change):
            return False

        h.policy._saver = fail
        preview = await h.commands(HTTP)
        result = await confirm(h, preview)
        assert result.code == "REG_SETTINGS_SAVE_FAILED"
        assert h.registry.status("remote") is RegStatus.PENDING
        assert read_settings(h.folders).document is None
        assert h.admission.restart_code == "POLICY_SAVE_UNKNOWN"


def test_url_code_messages_and_paste_use_c_normalization():
    with registration_form() as form:
        assert set(URL_MESSAGES) == ENDPOINT_REJECT_CODES
        for url, code in [
            (" https://example.com", "URL_INVALID"),
            ("ftp://example.com", "URL_SCHEME"),
            ("https://user:secret@example.com", "URL_USERINFO"),
            ("https://example.com/?secret", "URL_QUERY"),
            ("https://example.com/#secret", "URL_FRAGMENT"),
            ("https://example.com.", "URL_HOST"),
            ("https://a_b.test", "URL_IDNA"),
            ("https://example.com:0", "URL_PORT"),
            ("https://example.com/a/../b", "URL_PATH"),
            ("https://169.254.169.254", "URL_ADDRESS_FORBIDDEN"),
            ("http://localhost", "URL_HTTP_NOT_LOOPBACK"),
        ]:
            assert module.endpoint_input(url) == (None, code)
            form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
            form.url.setText(url)
            assert form._errors["url"].text() == URL_MESSAGES[code]
            assert not form.prepare_button.isEnabled()
        form.quick_input.setPlainText("https://faß.de:443/mcp")
        form.fill_from_paste()
        form.server_id.setText("remote")
        form.tier.setCurrentIndex(form.tier.findData("general_external"))
        form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
        draft, errors = form._build_draft()
        assert not errors
        assert draft.url == "https://xn--fa-hia.de/mcp"


def test_form_confirmation_ignores_stale_reply_and_clears_on_edit():
    with registration_form() as form:
        form._registration_id = "current"
        preview = RegistrationView(
            "REG_CONFIRM_REQUIRED",
            command_id="old",
            token=new_id("preview"),
            command="C:/server.exe",
            size=10,
            sha256="a" * 64,
        )
        form._registration_result(preview)
        assert not form.confirm_button.isEnabled()
        form._registration_result(replace(preview, command_id="current"))
        assert form.confirm_button.isEnabled()
        assert "C:/server.exe" in form.preview.toPlainText()
        assert "a" * 64 in form.preview.toPlainText()
        assert "10 bytes" in form.preview.toPlainText()
        form.command.setText("C:/other.exe")
        assert not form.confirm_button.isEnabled()
        assert not form._registration_token


def test_product_bridge_registration_updates_window_and_survives_restart(tmp_path, monkeypatch):
    from guard_app.services import product_startup, session_runtime
    from guard_app.services.bridge import BridgeState
    from guard_app.ui.runtime import RuntimePanel
    from guard_app.ui.window import GuardWindow

    from .test_bridge import close_bridge, pump_until

    app = QApplication.instance() or QApplication([])
    folders = prepare_app_folders(tmp_path / "product")
    calls = []

    async def collect(server, selected_folders):
        assert selected_folders is folders
        calls.append(server)
        return SimpleNamespace(complete=True, tools=())

    monkeypatch.setattr(product_startup, "collect_registration_listing", collect)

    async def boot():
        return await session_runtime.bootstrap_product(folders, settings=read_settings(folders))

    bridge = BackendBridge(boot)
    window = GuardWindow()
    window.attach_runtime(RuntimePanel(bridge))
    form = window.server_add_form
    views = []
    bridge.registration_changed.connect(views.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY and bool(views), timeout=10)
        form.transport.setCurrentIndex(form.transport.findData("streamable_http"))
        form.server_id.setText("remote")
        form.tier.setCurrentIndex(form.tier.findData("general_external"))
        form.max_label.setCurrentIndex(form.max_label.findData("PUBLIC"))
        form.url.setText(HTTP.url)
        form.prepare_button.click()
        pump_until(app, lambda: form.confirm_button.isEnabled(), timeout=10)
        assert not calls
        form.confirm_button.click()
        pump_until(app, lambda: any(v.code == "REG_REGISTERED" for v in views), timeout=10)
        assert "등록·설정 저장 완료" in form.status.text()
        assert "서버 등록 설정을 저장함" in window.settings_file_status.text()
        assert window.server_page.table.rowCount() == 1
        assert window.server_page.table.item(0, 4).text() == "등록 검토 중"
        assert len(calls) == 1
    finally:
        close_bridge(app, bridge)
        window.close()
    # A new product start reads the real C-written file and B SQLite registration.
    second = BackendBridge(boot)
    restored = []
    second.registration_changed.connect(restored.append)
    second.start()
    try:
        pump_until(app, lambda: second.state is BridgeState.READY, timeout=10)
        second.request_registration(RegistrationRequest("list"))
        pump_until(app, lambda: bool(restored), timeout=10)
        assert restored[-1].rows[0].server_id == "remote"
        assert restored[-1].rows[0].status == "pending"
        assert len(calls) == 1
    finally:
        close_bridge(app, second)
        folders.close()


@pytest.mark.asyncio
async def test_registration_collector_lists_without_tool_calls(tmp_path, monkeypatch):
    from guard_app.adapters.mcp_transport import validated_stdio_stream_factory
    from guard_app.services import product_assembly
    from guard_app.services.catalog import ServerSettings

    from .test_product_assembly import READ_DEFINITION, Peer

    peer = Peer([READ_DEFINITION])
    chosen = []

    def factory(folder, command, args, env_allow, pin):
        chosen.append((command, args, env_allow, pin))
        return validated_stdio_stream_factory(peer.read, peer.write, peer.drain)

    monkeypatch.setattr(product_assembly, "launcher_stdio_stream_factory", factory)
    folders = prepare_app_folders(tmp_path / "collector")
    server = ServerSettings.model_validate_json(
        '{"id":"local","transport":"stdio","command":"C:/server.exe",'
        '"command_sha256":"' + "a" * 64 + '","command_size":10,'
        '"tier":"internal","max_label":"PUBLIC","tools":[]}'
    )
    try:
        listing = await asyncio.wait_for(
            product_assembly.collect_registration_listing(server, folders), 5
        )
        assert listing.complete
        assert listing.tools[0]["name"] == "read"
        assert peer.calls == []
        assert chosen == [("C:/server.exe", (), (), ExecutablePin(10, "a" * 64))]
    finally:
        folders.close()


@pytest.mark.asyncio
async def test_registration_collector_reports_a_closed_port_as_a_start_failure(tmp_path):
    """#83 C review 3.1 with a real connection: a closed loopback port is a
    confirmed start failure, so registration refuses it before any write."""
    import json
    import socket

    from guard_app.services import product_assembly
    from guard_app.services.catalog import ServerSettings

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    folders = prepare_app_folders(tmp_path / "app")
    data = {
        "id": "closed",
        "transport": "streamable_http",
        "url": f"http://127.0.0.1:{port}/mcp",
        "tier": "internal",
        "max_label": "PUBLIC",
        "tools": [],
    }
    server = ServerSettings.model_validate_json(json.dumps(data), strict=True)
    try:
        with pytest.raises(DispatchError) as raised:
            await asyncio.wait_for(
                product_assembly.collect_registration_listing(server, folders), 30
            )
    finally:
        folders.close()
    assert raised.value.exception_type in module._SERVER_START_FAILURES


@pytest.mark.asyncio
async def test_repin_changes_policy_version_and_stops_persisted_task(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from guard_app.domain.enums import TaskStatus
    from guard_app.services.product_assembly import ProductInputs, assemble_guard

    current = ExecutablePin(10, "a" * 64)

    async def pin(command):
        return current

    monkeypatch.setattr(module, "pin_executable", pin)
    async with harness(tmp_path) as h:
        assert (await confirm(h, await h.commands(STDIO))).code == "REG_REGISTERED"

        async def audit(event):
            raise AssertionError("no dispatch")

        assembled = await assemble_guard(
            h.store,
            ProductInputs(
                h.policy.registration_document(),
                h.registry,
                h.store.allocate_call_seq,
                audit,
                "local-user",
                server_streams={},
            ),
        )
        h.policy._assembled = assembled
        h.policy._settings = assembled.settings
        h.policy._task_source = h.store.list_tasks
        stopped = []

        async def close_sessions(ids):
            stopped.extend(ids)

        h.commands.stop_sessions = close_sessions
        state = h.policy.task_state("before-repin")
        await h.store.create_task(state, created_at_utc=datetime.now(UTC))
        current = ExecutablePin(20, "b" * 64)
        try:
            result = await confirm(
                h, await h.commands(RegistrationRequest("recheck", server_id="local"))
            )
            assert result.code == "REG_PIN_SAVED"
            assert assembled.settings.policy_version != state.policy_version
            assert stopped == ["before-repin"]
            assert (await h.store.load_task("before-repin")).status is TaskStatus.HALTED
            assert h.admission.restart_code == "REG_PIN_RESTART_REQUIRED"
        finally:
            await assembled.aclose()
