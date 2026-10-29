"""A24–A27·A81·B3.12: the CFG-01 settings file at start and on policy saves.

C2.14 reads and replaces the file, B3.11 parses and serializes it, and C4.10's
`ServerRegistry` over B's SQLite store checks the Profile at start and records
each live listing during assembly. Real app folders on Windows; the failure
codes of C's file operations are injected where a real failure cannot be made.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

import guard_app.__main__ as entry
from guard_app.domain.enums import RegStatus
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.reasons import Reason
from guard_app.security import paths
from guard_app.security.paths import AppPathError, prepare_app_folders
from guard_app.security.settings_io import SettingsIOError, replace_settings_file
from guard_app.services import product_settings, session_runtime
from guard_app.services.bridge import SessionOptions
from guard_app.services.catalog import (
    SETTINGS_MAX_BYTES,
    load_profile_settings,
    parse_settings_bytes,
    prepare_server_access_change,
)
from guard_app.services.product_assembly import ProductAssemblyError, assemble_guard
from guard_app.services.product_settings import (
    REGISTRY_UNAVAILABLE,
    SETTINGS_FILE_MISSING,
    SETTINGS_LOADED,
    SETTINGS_PROFILE_INVALID,
    SaveOutcomeUnknownError,
    SettingsFile,
    SettingsFileChangedError,
    SettingsFileSaver,
    SettingsStartError,
    load_settings_profile,
    read_settings,
)
from guard_app.services.registry import ServerRegistry
from guard_app.services.session_runtime import SessionRuntimeError, bootstrap_product
from guard_app.storage.sqlite_store import SQLiteStore
from guard_app.ui.policy import CHANGE_RESULTS, PolicyPage
from guard_app.ui.presentation import SETTINGS_FILE_NOT_READ, SETTINGS_FILE_STATUS
from guard_app.ui.window import GuardWindow

from .fake_codex_app_server import FakeAppServer
from .test_codex_product_flow import preparation, read_then_answer
from .test_product_assembly import READ_DEFINITION, Peer, Registry, document, inputs

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="C2.14 settings file operations are Windows only"
)


def run(operation):
    return asyncio.run(operation)


def envelope(settings: dict[str, object]) -> bytes:
    return json.dumps({"schema_version": 1, "settings": settings}).encode()


@pytest.fixture
def folders(tmp_path):
    prepared = prepare_app_folders(tmp_path / "app")
    yield prepared
    prepared.close()


def create_file(folders, data: bytes):
    """Write the file the way the app does, through C's replacement."""
    return replace_settings_file(folders, data, expected=None, max_bytes=SETTINGS_MAX_BYTES)


def registry_stub() -> Registry:
    from guard_app.security.digest import tool_fingerprint

    return Registry(tool_fingerprint(READ_DEFINITION))


def deny_read(doc) -> object:
    return prepare_server_access_change(
        doc, registry_stub(), server_id="docs", tool_permissions={"read": False}
    )


# Reading at start


@windows_only
def test_settings_file_is_parsed_with_the_generation_it_was_read_at(folders) -> None:
    found = read_settings(folders)
    assert found.document is None
    assert found.generation is None
    assert not folders.paths.settings_file.exists()
    generation = create_file(folders, envelope(document()))
    found = read_settings(folders)
    assert found.document == document()
    assert found.generation == generation
    assert "unit" not in repr(found)
    broken = b'{"schema_version": 1, "settings": {"profile": "private-text"'
    folders.paths.settings_file.write_bytes(broken)
    with pytest.raises(SettingsStartError) as refused:
        read_settings(folders)
    assert refused.value.code == "SETTINGS_JSON_INVALID"
    assert "private" not in str(refused.value)
    assert folders.paths.settings_file.read_bytes() == broken


def test_file_read_failures_keep_c_fixed_code(monkeypatch, subtests):
    def check(error, code, monkeypatch):
        def refuse(_folders, *, max_bytes):
            assert max_bytes == SETTINGS_MAX_BYTES  # B's limit, written in one place
            raise error

        monkeypatch.setattr(product_settings, "read_settings_file", refuse)
        with pytest.raises(SettingsStartError) as refused:
            read_settings(object())  # type: ignore[arg-type]
        assert refused.value.code == code

    for case_index, (error, code) in enumerate(
        [
            (SettingsIOError("SETTINGS_FILE_INVALID"), "SETTINGS_FILE_INVALID"),
            (SettingsIOError("SETTINGS_FILE_READ_FAILED"), "SETTINGS_FILE_READ_FAILED"),
            (AppPathError("APP_FOLDERS_CLOSED"), "APP_FOLDERS_CLOSED"),
        ]
    ):
        with subtests.test(case=case_index), monkeypatch.context() as patch:
            check(error, code, patch)


# Saving a policy change


@windows_only
def test_saver_writes_the_prepared_document_and_follows_its_own_writes(folders) -> None:
    create_file(folders, envelope(document()))
    saver = SettingsFileSaver(read_settings(folders))
    first = deny_read(document())
    assert run(saver(first)) is True
    on_disk = parse_settings_bytes(folders.paths.settings_file.read_bytes())
    assert on_disk == first.document.to_dict()
    assert on_disk["access_rules"] == {"allow": [], "deny": [["docs", "read"]]}
    # The next save starts from the generation this one wrote.
    second = prepare_server_access_change(
        first.document, registry_stub(), server_id="docs", tool_permissions={"read": True}
    )
    assert run(saver(second)) is True
    on_disk = parse_settings_bytes(folders.paths.settings_file.read_bytes())
    assert on_disk["access_rules"] == {"allow": [["docs", "read"]], "deny": []}


@windows_only
def test_saver_never_overwrites_a_file_changed_outside_the_app(folders) -> None:
    generation = create_file(folders, envelope(document()))
    saver = SettingsFileSaver(read_settings(folders))
    edited = envelope({**document(), "policy": {"approval_ttl_seconds": 120}})
    replace_settings_file(folders, edited, expected=generation, max_bytes=SETTINGS_MAX_BYTES)
    for _ in range(2):  # every later save is refused too, until a restart reads it
        with pytest.raises(SettingsFileChangedError):
            run(saver(deny_read(document())))
    assert folders.paths.settings_file.read_bytes() == edited


def test_saver_failure_codes_preserve_write_outcome(monkeypatch, subtests):
    """C2.14: unchanged, changed and unknown writes remain distinct."""
    cases = [
        (SettingsIOError("SETTINGS_FILE_WRITE_FAILED"), None),
        (SettingsIOError("SETTINGS_FILE_DATA_INVALID"), None),
        (AppPathError("APP_FOLDERS_CLOSED"), None),
        (SettingsIOError("SETTINGS_FILE_CHANGED"), SettingsFileChangedError),
        (SettingsIOError("SETTINGS_FILE_INVALID"), SettingsFileChangedError),
        (SettingsIOError("SETTINGS_FILE_READ_FAILED"), SettingsFileChangedError),
        (SettingsIOError("SETTINGS_FILE_WRITE_UNKNOWN"), SaveOutcomeUnknownError),
    ]
    for error, expected in cases:
        with subtests.test(code=error.args), monkeypatch.context() as patch:

            def refuse(*_args, error=error, **_kwargs):
                raise error

            patch.setattr(product_settings, "replace_settings_file", refuse)
            saver = SettingsFileSaver(SettingsFile(object(), document(), None))
            if expected is None:
                assert run(saver(deny_read(document()))) is False
            else:
                with pytest.raises(expected) as refused:
                    run(saver(deny_read(document())))
                if expected is SettingsFileChangedError:
                    assert refused.value.args == error.args


# The Profile at start, against C's Registry on B's store


class FailingRegistryStore:
    async def list_registrations(self):
        raise StoreError(Reason.STATE_COMMIT_FAILED)


def test_settings_profile_status_names_what_the_start_found(tmp_path) -> None:
    folders: Any = object()

    async def statuses():
        with SQLiteStore(tmp_path / "start.db", audit_id_factory=lambda: "a") as store:
            missing = await load_settings_profile(SettingsFile(folders, None, None), store)
            loaded = await load_settings_profile(SettingsFile(folders, document(), None), store)
            invalid = await load_settings_profile(
                SettingsFile(folders, {"profile": {"id": "unit"}}, None), store
            )
        failing = await load_settings_profile(
            SettingsFile(folders, document(), None),
            FailingRegistryStore(),  # type: ignore[arg-type]
        )
        return missing, loaded, invalid, failing

    missing, loaded, invalid, failing = run(statuses())
    assert missing == (SETTINGS_FILE_MISSING, None)
    status, profile = loaded
    assert status == SETTINGS_LOADED
    assert profile is not None
    assert (
        profile.policy_version == load_profile_settings(document(), registry_stub()).policy_version
    )
    assert invalid == (SETTINGS_PROFILE_INVALID, None)
    assert failing == (REGISTRY_UNAVAILABLE, None)


async def refuse_session(*_args):
    raise AssertionError("no session in this test")


def test_product_start_reports_the_settings_and_refuses_tasks_without_a_profile(tmp_path, subtests):
    def check(doc, status, blocker, tmp_path):
        async def start():
            bindings = await bootstrap_product(
                str(tmp_path / "start.db"),
                session_factory=refuse_session,
                preparation=preparation([], read_then_answer),
                settings=SettingsFile(object(), doc, None),  # type: ignore[arg-type]
            )
            try:
                assert bindings.session_blocker is not None
                return bindings.settings_status, bindings.session_blocker()
            finally:
                assert bindings.close is not None
                await bindings.close()

        assert run(start()) == (status, blocker)

    for case_index, (doc, status, blocker) in enumerate(
        [
            (None, SETTINGS_FILE_MISSING, SETTINGS_FILE_MISSING),
            ({"profile": {"id": "unit"}}, SETTINGS_PROFILE_INVALID, SETTINGS_PROFILE_INVALID),
            (document(), SETTINGS_LOADED, None),
        ]
    ):
        with subtests.test(case=case_index):
            case_path = tmp_path / str(case_index)
            case_path.mkdir()
            check(doc, status, blocker, case_path)


def test_save_with_unknown_outcome_closes_guard_until_restart(tmp_path) -> None:
    """C2.14: the file may hold either document, so nothing runs or saves again."""
    saved: list[object] = []

    async def unknown_saver(change) -> bool:
        saved.append(change)
        raise SaveOutcomeUnknownError("SETTINGS_FILE_WRITE_UNKNOWN")

    servers: list[FakeAppServer] = []

    async def scenario():
        bindings = await bootstrap_product(
            str(tmp_path / "policy.db"),
            preparation=preparation(servers, read_then_answer),
            product=inputs(Peer([READ_DEFINITION]), registry_stub(), document()),
            profile_saver=unknown_saver,
        )
        try:
            assert bindings.policy_access is not None
            assert bindings.create_session is not None
            assert bindings.session_blocker is not None
            with pytest.raises(SessionRuntimeError) as refused:
                await bindings.policy_access("docs", (), ("read",))
            with pytest.raises(SessionRuntimeError) as again:
                await bindings.policy_access("docs", (), ("read",))
            with pytest.raises(SessionRuntimeError) as created:
                await bindings.create_session(SessionOptions())
            return (refused.value, again.value, created.value), bindings.session_blocker()
        finally:
            assert bindings.close is not None
            await bindings.close()

    errors, blocker = run(scenario())
    assert [error.code for error in errors] == ["POLICY_SAVE_UNKNOWN"] * 3
    assert blocker == "POLICY_SAVE_UNKNOWN"
    assert len(saved) == 1
    assert servers == []


def test_save_cancelled_mid_write_closes_guard_until_restart(tmp_path) -> None:
    """#52 B review 2: the cancelled write may still land, as with an unknown outcome."""
    servers: list[FakeAppServer] = []
    peer = Peer([READ_DEFINITION])

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def hanging_saver(_change) -> bool:
            entered.set()
            await release.wait()
            return True

        bindings = await bootstrap_product(
            str(tmp_path / "policy.db"),
            preparation=preparation(servers, read_then_answer),
            product=inputs(peer, registry_stub(), document()),
            profile_saver=hanging_saver,
        )
        try:
            assert bindings.policy_access is not None
            assert bindings.create_session is not None
            assert bindings.session_blocker is not None
            old = await bindings.create_session(SessionOptions())
            change = asyncio.ensure_future(bindings.policy_access("docs", (), ("read",)))
            await asyncio.wait_for(entered.wait(), 5)
            change.cancel()
            with pytest.raises(asyncio.CancelledError):
                await change
            blocker = bindings.session_blocker()
            with pytest.raises(SessionRuntimeError) as again:
                await bindings.policy_access("docs", (), ("read",))
            with pytest.raises(SessionRuntimeError) as created:
                await bindings.create_session(SessionOptions())
            runner = bindings.runner_for_task(old.task_id)
            runner.add_user_message("summarize the public document")
            update = await asyncio.wait_for(runner.advance(), 5)
            return blocker, (again.value.code, created.value.code), update
        finally:
            assert bindings.close is not None
            await bindings.close()

    blocker, codes, update = run(scenario())
    assert blocker == "POLICY_SAVE_UNKNOWN"
    assert codes == ("POLICY_SAVE_UNKNOWN", "POLICY_SAVE_UNKNOWN")
    # The old session's READ is refused by the closed Guard, never dispatched.
    assert update.status == "complete"
    assert peer.calls == []
    assert servers[0].last_reply["result"]["success"] is False  # type: ignore[attr-defined]
    assert len(servers) == 1


def test_file_changed_outside_the_app_refuses_saves_and_keeps_guard_running(tmp_path) -> None:
    """C2.14 table: nothing was written, so Guard keeps its rules until a restart."""
    saved: list[object] = []
    servers: list[FakeAppServer] = []
    peer = Peer([READ_DEFINITION])

    async def changed_saver(change) -> bool:
        saved.append(change)
        raise SettingsFileChangedError("SETTINGS_FILE_CHANGED")

    async def scenario():
        bindings = await bootstrap_product(
            str(tmp_path / "policy.db"),
            preparation=preparation(servers, read_then_answer),
            product=inputs(peer, registry_stub(), document()),
            profile_saver=changed_saver,
        )
        try:
            assert bindings.policy_access is not None
            assert bindings.create_session is not None
            assert bindings.session_blocker is not None
            codes = []
            for _ in range(2):
                with pytest.raises(SessionRuntimeError) as refused:
                    await bindings.policy_access("docs", (), ("read",))
                codes.append(refused.value.code)
            blocker = bindings.session_blocker()
            created = await bindings.create_session(SessionOptions())
            runner = bindings.runner_for_task(created.task_id)
            runner.add_user_message("summarize the public document")
            update = await asyncio.wait_for(runner.advance(), 5)
            return codes, blocker, update
        finally:
            assert bindings.close is not None
            await bindings.close()

    codes, blocker, update = run(scenario())
    assert codes == ["POLICY_SAVE_FILE_CHANGED"] * 2
    assert blocker is None
    assert len(saved) == 2
    # The change that would deny the READ was not applied, so the READ runs.
    assert update.status == "complete"
    assert len(peer.calls) == 1


# Recording live listings in C's Registry during assembly (C4.10)


@dataclass(frozen=True)
class Listing:
    complete: bool
    tools: tuple[FrozenJsonObject, ...]


async def registered(store: SQLiteStore, doc, *, activate: bool = True) -> ServerRegistry:
    registry = ServerRegistry(store)
    await registry.load()
    [server] = load_profile_settings(doc, registry).selected_servers
    entry = await registry.register(server, Listing(True, (FrozenJsonObject(READ_DEFINITION),)))
    if activate:
        await registry.activate(
            "docs",
            expected_row_revision=entry.registration.row_revision,
            reviewed_by="operator",
        )
    return registry


async def assemble_and_reload(tmp_path: Path, peer: Peer, registered_doc, run_doc):
    """Blocked servers this run, and the registration B's store holds afterwards."""
    db = tmp_path / f"{uuid4().hex}.db"
    with SQLiteStore(db, audit_id_factory=lambda: f"audit-{uuid4().hex}") as store:
        registry = await registered(store, registered_doc)
        assembled = await assemble_guard(store, inputs(peer, registry, run_doc))
        try:
            blocked = assembled.registry.blocked_servers
        finally:
            await assembled.aclose()
        reloaded = ServerRegistry(store)
        await reloaded.load()
        return blocked, reloaded.registration("docs")


def test_live_listings_preserve_or_invalidate_registered_server(tmp_path, subtests):
    """C4.10, I10: persist changed definitions as PENDING; unchanged stays ACTIVE."""
    from guard_app.security.digest import tool_fingerprint

    changed = {**READ_DEFINITION, "description": "read a document, then more"}
    for definition, blocked_expected, status in (
        (READ_DEFINITION, frozenset(), RegStatus.ACTIVE),
        (changed, frozenset({"docs"}), RegStatus.PENDING),
    ):
        with subtests.test(status=status):
            blocked, stored = run(
                assemble_and_reload(tmp_path, Peer([definition]), document(), document())
            )
            assert blocked == blocked_expected
            assert stored is not None
            assert stored.status is status
            if status is RegStatus.PENDING:
                [tool] = stored.tools
                assert tool.fingerprint == tool_fingerprint(changed)


def test_listing_that_cannot_be_recorded_blocks_the_server_for_this_run(
    tmp_path, monkeypatch
) -> None:
    """C4.10: a failed write keeps the server PENDING for this run; nothing runs.

    The listing is unchanged, so only the failed record blocks the server.
    """

    async def refuse(self, server_id, tools, *, at_utc):
        raise StoreError(Reason.STATE_COMMIT_FAILED)

    async def scenario():
        db = tmp_path / "unrecorded.db"
        with SQLiteStore(db, audit_id_factory=lambda: f"audit-{uuid4().hex}") as store:
            registry = await registered(store, document())
            monkeypatch.setattr(SQLiteStore, "record_fingerprint_change", refuse)
            peer = Peer([READ_DEFINITION])
            assembled = await assemble_guard(store, inputs(peer, registry, document()))
            try:
                return assembled.registry.blocked_servers, registry.status("docs")
            finally:
                await assembled.aclose()

    blocked, status = run(scenario())
    assert blocked == frozenset({"docs"})
    assert status is RegStatus.PENDING


def test_settings_that_differ_from_the_registration_stop_the_assembly(tmp_path) -> None:
    """B's Catalog refuses a boundary that differs from the registration; nothing runs."""
    registered_doc = document()
    run_doc = json.loads(json.dumps(registered_doc))
    run_doc["servers"][0]["max_label"] = "INTERNAL"
    with pytest.raises(ProductAssemblyError, match="^PROFILE_TOOL_INVALID$"):
        run(assemble_and_reload(tmp_path, Peer([READ_DEFINITION]), registered_doc, run_doc))


# Settings screen and the entry point


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


def test_settings_page_shows_the_status_and_opens_when_the_file_is_missing(app) -> None:
    window = GuardWindow()
    try:
        window.navigate("runs")
        window.set_settings_status(SETTINGS_FILE_MISSING)
        assert window._page == "settings"
        assert window.settings_file_status.text() == SETTINGS_FILE_STATUS[SETTINGS_FILE_MISSING]
        window.navigate("logs")
        window.set_settings_status(SETTINGS_FILE_MISSING)
        assert window._page == "logs"  # a page the user chose stays
        window.set_settings_status(SETTINGS_LOADED)
        assert window.settings_file_status.text() == SETTINGS_FILE_STATUS[SETTINGS_LOADED]
        window.set_settings_status(None)
        assert window.settings_file_status.text() == SETTINGS_FILE_NOT_READ
        window.set_settings_status("<b>private</b>")
        assert "private" not in window.settings_file_status.text()
    finally:
        window.close()


def test_policy_page_says_to_restart_after_the_file_changed_outside(app) -> None:
    page = PolicyPage()
    page.set_change_pending("change")
    page.settle_change("change", "POLICY_SAVE_FILE_CHANGED")
    assert page.notice.text() == CHANGE_RESULTS["POLICY_SAVE_FILE_CHANGED"]
    assert "다시 시작" in page.notice.text()
    assert "다시 시작" not in CHANGE_RESULTS["POLICY_SAVE_FAILED"]


@windows_only
def test_main_refuses_a_malformed_settings_file_and_releases_the_lock(
    monkeypatch, capsys, tmp_path
) -> None:
    root = tmp_path / "app"
    prepared = prepare_app_folders(root)
    broken = b'{"schema_version": 1, "settings": {"private-key": 1,'
    prepared.paths.settings_file.write_bytes(broken)
    prepared.close()
    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    monkeypatch.setattr(entry, "_run", lambda *_args: pytest.fail("the window must not open"))
    assert entry.main() == 1
    err = capsys.readouterr().err
    assert "설정 파일 형식이 올바르지 않아 앱을 시작하지 않음(SETTINGS_JSON_INVALID)" in err
    assert "private" not in err
    assert (root / "config" / "settings.json").read_bytes() == broken
    prepare_app_folders(root).close()  # the lock was released


@windows_only
def test_main_starts_without_a_settings_file_and_hands_the_first_run_on(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "app"
    handed: list[object] = []

    class StoppedError(Exception):
        pass

    def bridge(bootstrap) -> None:
        bootstrap()
        raise StoppedError

    def recording_bootstrap(db_path: str, **kwargs: object) -> None:
        handed.append(kwargs.get("settings"))

    monkeypatch.setattr(paths, "app_root", lambda: root)
    monkeypatch.setattr(entry, "BackendBridge", bridge)
    monkeypatch.setattr(session_runtime, "bootstrap_product", recording_bootstrap)
    monkeypatch.setattr(sys, "argv", ["guard-app"])
    instance = QApplication.instance() or QApplication([])
    assert isinstance(instance, QApplication)
    quit_on_close = instance.quitOnLastWindowClosed()
    try:
        with pytest.raises(StoppedError):
            entry.main()
    finally:
        instance.setQuitOnLastWindowClosed(quit_on_close)
    [settings] = handed
    assert isinstance(settings, SettingsFile)
    assert settings.document is None
    assert not (root / "config" / "settings.json").exists()
    prepare_app_folders(root).close()
