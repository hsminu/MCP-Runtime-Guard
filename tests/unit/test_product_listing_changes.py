"""A140 / LIST-01, I2/I7/I10, T10: notices after a received page cannot be lost."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from guard_app.adapters.mcp_transport import McpTransport, validated_stdio_stream_factory
from guard_app.domain.contracts import Denied, Released
from guard_app.domain.enums import RegStatus, TaskStatus
from guard_app.domain.models import TaskState
from guard_app.domain.reasons import Reason
from guard_app.security.digest import tool_fingerprint
from guard_app.services.product_assembly import LiveCheckedRegistry, assemble_guard
from guard_app.storage.sqlite_store import SQLiteStore
from tests.unit.test_product_assembly import (
    PUBLIC_SOURCE,
    READ_DEFINITION,
    Peer,
    Registry,
    document,
    inputs,
    read_call,
    submit_call,
)


class ChangedListingPeer(Peer):
    """The notice follows a page in the same byte batch, before its waiter resumes."""

    def __init__(self, *, paged: bool = False) -> None:
        super().__init__([READ_DEFINITION])
        self.paged = paged
        self.list_calls = 0

    async def write(self, frame: bytes) -> None:
        request = json.loads(frame)
        if request.get("method") != "tools/list":
            await super().write(frame)
            return
        self.list_calls += 1
        if self.paged:
            result = (
                {"tools": self.definitions, "nextCursor": "next"}
                if self.list_calls == 1
                else {"tools": []}
            )
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            self.output.extend(json.dumps(response).encode() + b"\n")
            self.available.set()
        else:
            await super().write(frame)
        if self.list_calls == 1:
            self.notify_tools_changed()


@pytest.mark.parametrize("paged", [False, True])
def test_a_notice_right_after_a_listing_page_blocks_before_first_call(tmp_path, paged):
    peer = ChangedListingPeer(paged=paged)
    registry = Registry(tool_fingerprint(READ_DEFINITION))
    outcome, _views, view = asyncio.run(submit_call(tmp_path, peer, registry, document()))
    assert type(outcome) is Denied
    assert outcome.reasons == (Reason.REG_NOT_ACTIVE,)
    assert peer.calls == []
    assert peer.list_calls == (2 if paged else 1)  # no automatic collection retry
    assert view.early_list_changes == 0
    assert view.listing_changed == frozenset({"docs"})
    assert view.status("docs") is RegStatus.PENDING
    assert registry.record.status is RegStatus.ACTIVE  # no persistent Registry change


def test_one_servers_notice_survives_waiting_for_another_servers_listing(tmp_path, monkeypatch):
    async def run():
        seen = asyncio.Event()
        changes = []
        original_changed = LiveCheckedRegistry.tools_changed

        def changed(view, server_id):
            # Observation only: prove the notification is handled before seal.
            changes.append((server_id, view.fingerprint(registry.record.tools[0])))
            original_changed(view, server_id)
            seen.set()

        class SlowPeer(Peer):
            async def write(self, frame):
                if json.loads(frame).get("method") == "tools/list":
                    await asyncio.wait_for(seen.wait(), 2)
                await super().write(frame)

        class TwoServers(Registry):
            def registration(self, server_id):
                if server_id == "other":
                    return self.record.model_copy(
                        update={
                            "server_id": "other",
                            "tools": tuple(
                                tool.model_copy(update={"server_id": "other"})
                                for tool in self.record.tools
                            ),
                        }
                    )
                return super().registration(server_id)

            def status(self, server_id):
                return RegStatus.ACTIVE if server_id == "other" else super().status(server_id)

        monkeypatch.setattr(LiveCheckedRegistry, "tools_changed", changed)
        peer = ChangedListingPeer()
        other = SlowPeer([READ_DEFINITION], notify_on_list=True)
        registry = TwoServers(tool_fingerprint(READ_DEFINITION))
        doc = document(sources=(PUBLIC_SOURCE, {**PUBLIC_SOURCE, "server": "other"}))
        doc["profile"]["servers"].append("other")
        doc["servers"].append({**doc["servers"][0], "id": "other"})
        doc["access_rules"]["allow"].append(["other", "read"])
        product = inputs(peer, registry, doc)
        product = replace(
            product,
            server_streams={
                **product.server_streams,
                "other": validated_stdio_stream_factory(other.read, other.write, other.drain),
            },
        )
        audit_ids = iter(range(100))
        store = SQLiteStore(
            tmp_path / "two.db", audit_id_factory=lambda: f"audit-{next(audit_ids)}"
        )
        assembled = None
        try:
            assembled = await assemble_guard(store, product)
            await store.create_task(
                TaskState(
                    task_id="task-1",
                    epoch=1,
                    profile_id=assembled.settings.profile_id,
                    policy_version=assembled.settings.policy_version,
                    status=TaskStatus.RUNNING,
                ),
                created_at_utc=datetime.now(UTC),
            )
            denied = await assembled.guard.submit("task-1", read_call("c1"), expected_epoch=1)
            allowed = await assembled.guard.submit(
                "task-1",
                read_call("c2").model_copy(update={"tool_ref": "other__read"}),
                expected_epoch=1,
            )
            assert type(denied) is Denied
            assert denied.reasons == (Reason.REG_NOT_ACTIVE,)
            assert type(allowed) is Released
            assert peer.calls == []
            assert len(other.calls) == 1
            assert changes == [("docs", None), ("other", None)]
            assert assembled.registry.early_list_changes == 1
            assert assembled.registry.listing_changed == frozenset({"docs"})
            assert [tool.tool_ref for tool in assembled.offered_tool_views()] == ["other__read"]
        finally:
            if assembled is not None:
                await assembled.aclose()
            store.close()
            assert await store.wait_closed(5.0)

    asyncio.run(run())


def test_listing_received_callback_must_be_callable():
    with pytest.raises(TypeError, match=r"listing received callback must be callable"):
        McpTransport({}, on_listing_received="invalid")
