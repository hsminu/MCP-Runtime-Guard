"""A184: default Profile startup and first save (CFG-01 change, I6/I11).

Real Guard, SQLite and Windows settings I/O; synthetic L2 and Codex streams.
No real Codex process or independent C acceptance claim.
"""

from __future__ import annotations

import asyncio

import pytest

from guard_app.services.bridge_types import DestinationEdit, SessionOptions
from guard_app.services.catalog import CatalogConfigError
from guard_app.services.product_settings import (
    REGISTRY_UNAVAILABLE,
    SETTINGS_FILE_DEFAULT,
    SETTINGS_LOADED,
    SETTINGS_PROFILE_INVALID,
    SettingsFile,
    default_settings_document,
    load_settings_profile,
    read_settings,
)
from guard_app.services.session_admission import SessionAdmission
from guard_app.services.session_policy import TaskPolicy
from guard_app.services.session_runtime import (
    SessionRuntime,
    SessionRuntimeError,
    bootstrap_product,
)
from guard_app.storage.sqlite_store import SQLiteStore

from .fake_codex_app_server import answer_only
from .test_codex_product_flow import advance_session, create_session, preparation
from .test_product_assembly import closing_backend
from .test_product_settings import FailingRegistryStore, windows_only
from .test_product_settings import folders as folders
from .test_session_runtime import synthetic_preparation


@windows_only
@pytest.mark.asyncio
async def test_first_run_connects_product_factory_without_writing_settings(folders):
    async with asyncio.timeout(15):
        servers = []
        settings = SettingsFile(folders, None, None)
        bindings = await bootstrap_product(
            folders,
            session_factory=None,
            preparation=preparation(servers, answer_only),
            settings=settings,
        )
        async with closing_backend(bindings):
            assert bindings.settings_status == SETTINGS_FILE_DEFAULT
            assert bindings.session_blocker() is None
            assert bindings.policy.profile_id == "local"
            assert bindings.policy.servers == bindings.policy.destinations == ()
            assert bindings.policy.allow == bindings.policy.deny == ()
            assert bindings.policy.change_available
            assert await bindings.policy_destinations(None) == bindings.policy
            assert not folders.paths.settings_file.exists()
            assert servers == []
            created = await create_session(bindings)
            assert not created.tools_available
            update = await advance_session(bindings, created, "hello")
            assert update.status == "complete"
            assert update.assistant_text == "echo:hello"
            assert len(servers) == 1
            assert servers[0].thread_params["dynamicTools"] == []
        assert settings.document is None
        assert settings.generation is None
        assert not folders.paths.settings_file.exists()


@windows_only
@pytest.mark.asyncio
async def test_first_user_save_creates_file_and_next_start_loads_it(folders):
    async with asyncio.timeout(15):
        bindings = await bootstrap_product(
            folders,
            session_factory=None,
            preparation=preparation([], answer_only),
            settings=SettingsFile(folders, None, None),
        )
        async with closing_backend(bindings):
            assert not folders.paths.settings_file.exists()
            changed = await bindings.policy_destinations(
                DestinationEdit("add", "vault", "internal", "CONFIDENTIAL", True)
            )
            assert changed.destinations[0].destination_id == "vault"
            assert changed.destinations[0].selected
            saved = read_settings(folders)
            assert saved.generation is not None
            assert saved.document["profile"]["destinations"] == ["vault"]
            assert saved.document["destinations"] == [
                {"id": "vault", "tier": "internal", "max_label": "CONFIDENTIAL"}
            ]
            # The same saver must follow the generation from its first write.
            changed = await bindings.policy_destinations(
                DestinationEdit("update", "vault", "internal", "INTERNAL", True)
            )
            saved = read_settings(folders)
            assert saved.document["destinations"][0]["max_label"] == "INTERNAL"
        restarted = await bootstrap_product(
            folders,
            session_factory=None,
            preparation=preparation([], answer_only),
            settings=saved,
        )
        async with closing_backend(restarted):
            assert restarted.settings_status == SETTINGS_LOADED
            assert restarted.session_blocker() is None
            assert restarted.policy == changed


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SETTINGS_PROFILE_INVALID, SETTINGS_PROFILE_INVALID),
        (REGISTRY_UNAVAILABLE, REGISTRY_UNAVAILABLE),
        (SETTINGS_FILE_DEFAULT, "GUARD_PROFILE_UNAVAILABLE"),
        (SETTINGS_LOADED, "GUARD_PROFILE_UNAVAILABLE"),
    ],
)
@pytest.mark.parametrize("create", [False, True], ids=["blocker", "create"])
@pytest.mark.asyncio
async def test_policy_failure_precedes_missing_factory_without_task_rows(
    tmp_path, status, expected, create
):
    async with asyncio.timeout(10):
        with SQLiteStore(tmp_path / "empty.db", audit_id_factory=lambda: "audit") as store:
            runtime = SessionRuntime(
                store,
                session_factory=None,
                preparation=synthetic_preparation(),
                settings_status=status,
            )
            async with closing_backend(runtime.bindings()):
                if create:
                    with pytest.raises(SessionRuntimeError) as refused:
                        await runtime.create_session(SessionOptions())
                    assert refused.value.code == expected
                else:
                    assert runtime.creation_blocker() == expected
                assert await store.list_tasks() == ()


def test_default_document_is_shared_definition_but_never_shared_mutable_state():
    policy = TaskPolicy(None, SessionAdmission())
    settings = SettingsFile(object(), None, None)
    expected = {"profile": {"id": "local", "servers": []}, "servers": []}
    for make in (
        default_settings_document,
        policy.registration_document,
        lambda: settings.profile_document,
    ):
        first = make()
        assert first == expected == default_settings_document()
        first["profile"]["id"] = "changed"
        first["profile"]["servers"].append("changed")
        first["servers"].append({"id": "changed"})
        assert make() == expected
    assert settings.document is None
    document = default_settings_document()
    assert SettingsFile(object(), document, None).profile_document is document


@pytest.mark.asyncio
async def test_default_profile_keeps_registry_and_catalog_failure_codes(tmp_path, monkeypatch):
    from guard_app.services import product_settings

    async with asyncio.timeout(10):
        settings = SettingsFile(object(), None, None)
        assert await load_settings_profile(settings, FailingRegistryStore()) == (
            REGISTRY_UNAVAILABLE,
            None,
        )

        def invalid(document, registry):
            assert document == default_settings_document()
            raise CatalogConfigError("invalid")

        monkeypatch.setattr(product_settings, "load_profile_settings", invalid)
        with SQLiteStore(tmp_path / "invalid.db", audit_id_factory=lambda: "audit") as store:
            assert await load_settings_profile(settings, store) == (SETTINGS_PROFILE_INVALID, None)
