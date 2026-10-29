"""B3.17: Registry 설정 불일치를 서버 시작 전에 차단하고 저장으로 복구한다."""

import asyncio
from datetime import UTC, datetime
from itertools import count

from guard_app.domain.enums import RegStatus, ToolKind
from guard_app.services.catalog import load_profile_settings
from guard_app.services.registry import ServerRegistry
from guard_app.storage import SQLiteStore
from tests.unit.test_profile_loading import _document, _Registry


def test_settings_mismatch_is_not_started_and_resave_recovers_without_starting_server(tmp_path):
    async def scenario():
        now = datetime(2026, 10, 30, tzinfo=UTC)
        ids = count()
        store = SQLiteStore(tmp_path / "registry.db", audit_id_factory=lambda: f"audit-{next(ids)}")
        try:
            initial = _Registry().records["docs"].model_copy(update={"status": RegStatus.PENDING})
            inserted = await store.register_server(initial, at_utc=now)
            registry = ServerRegistry(store, clock=lambda: now)
            await registry.load()
            unchanged_file = _document()
            assert load_profile_settings(unchanged_file, registry).server_issue("docs") is None

            original_tool = inserted.registration.tools[0]
            send_tool = original_tool.model_copy(
                update={"kind": ToolKind.SEND, "resource_arg": None, "destination_arg": "path"}
            )
            changed = await store.record_settings_change(
                "docs",
                (send_tool,),
                expected_row_revision=inserted.registration.row_revision,
                at_utc=now,
            )
            await registry.refresh("docs")
            mismatched = load_profile_settings(unchanged_file, registry)
            assert mismatched.server_issue("docs") == "REG_SETTINGS_MISMATCH"
            assert "docs" not in [server.id for server in mismatched.startable_servers]
            assert "docs" in [server.id for server in mismatched.selected_servers]

            await store.record_settings_change(
                "docs",
                (original_tool,),
                expected_row_revision=changed.entry.registration.row_revision,
                at_utc=now,
            )
            await registry.refresh("docs")
            recovered = load_profile_settings(unchanged_file, registry)
            assert recovered.server_issue("docs") is None
            assert "docs" in [server.id for server in recovered.startable_servers]
            assert registry.registration("docs").tools[0].kind is ToolKind.READ
        finally:
            store.close()
            assert await store.wait_closed(5.0)

    asyncio.run(scenario())
