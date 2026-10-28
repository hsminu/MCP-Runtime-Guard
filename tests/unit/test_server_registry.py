"""services.registry.ServerRegistry(C4.10·C4.11·C4.18·C4.19) 단위 테스트. B의 SQLiteStore를 쓴다."""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard_app.adapters.mcp_transport import ToolListing
from guard_app.domain.enums import Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.services.catalog import ServerSettings, load_profile_settings
from guard_app.services.registry import RegistryError, ServerRegistry, listing_fingerprints
from guard_app.storage import SQLiteStore

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)

SERVER = {
    "id": "docs",
    "transport": "stdio",
    "command": "C:/PRIVATE_COMMAND/python.exe",
    "tier": "internal",
    "max_label": "CONFIDENTIAL",
    "tools": [
        {"name": "read", "kind": "read", "resource_arg": "path"},
        {"name": "send", "kind": "send", "destination_arg": "dest"},
    ],
}

DEFINITIONS = {
    "read": {
        "name": "read",
        "description": "Read a document",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    "send": {
        "name": "send",
        "description": "Send a message",
        "inputSchema": {"type": "object", "properties": {"dest": {"type": "string"}}},
    },
    # 설정에 없는 도구: OTHER로 등록되고 노출·실행되지 않는다.
    "extra": {"name": "extra", "inputSchema": {"type": "object"}},
}


def run(operation):
    return asyncio.run(operation)


def settings(**updates) -> ServerSettings:
    # 설정 파일을 읽을 때와 같이 JSON에서 엄격 모드로 검증한다(B `load_profile_settings`).
    return ServerSettings.model_validate_json(json.dumps({**SERVER, **updates}), strict=True)


def listing(*names: str, edit: dict[str, dict] | None = None) -> ToolListing:
    chosen = names or tuple(DEFINITIONS)
    tools = []
    for name in chosen:
        definition = dict(DEFINITIONS[name])
        definition.update((edit or {}).get(name, {}))
        tools.append(FrozenJsonObject(definition))
    return ToolListing(complete=True, tools=tuple(tools), reason=None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    sequence = iter(range(10_000))
    with SQLiteStore(
        tmp_path / "guard.db", audit_id_factory=lambda: f"audit-{next(sequence)}"
    ) as instance:
        yield instance


@pytest.fixture
def registry(store) -> ServerRegistry:
    created = ServerRegistry(store, clock=lambda: NOW)
    run(created.load())
    return created


def _active(registry: ServerRegistry) -> None:
    entry = run(registry.register(settings(), listing()))
    run(
        registry.activate(
            "docs", expected_row_revision=entry.registration.row_revision, reviewed_by="operator"
        )
    )


def test_register_combines_settings_and_listing_as_pending(registry, store):
    entry = run(registry.register(settings(), listing()))
    registration = entry.registration
    assert registration.status is RegStatus.PENDING
    assert registration.row_revision == 1  # B가 쓰기마다 revision을 올린다
    assert registration.boundary.tier is TrustTier.INTERNAL
    assert registration.boundary.max_label is Label.CONFIDENTIAL
    tools = {tool.tool_name: tool for tool in registration.tools}
    assert (tools["read"].kind, tools["read"].resource_arg) == (ToolKind.READ, "path")
    assert (tools["send"].kind, tools["send"].destination_arg) == (ToolKind.SEND, "dest")
    assert (tools["extra"].kind, tools["extra"].resource_arg) == (ToolKind.OTHER, None)
    fingerprints = listing_fingerprints(listing())
    assert {name: tool.fingerprint for name, tool in tools.items()} == fingerprints
    assert tools["read"].input_schema.to_dict() == DEFINITIONS["read"]["inputSchema"]
    assert registry.registration("docs") == run(store.load_registration("docs")).registration
    assert registry.status("docs") is RegStatus.PENDING
    assert registry.fingerprint(tools["read"]) == fingerprints["read"]


def test_registered_server_is_not_registered_twice(registry):
    run(registry.register(settings(), listing()))
    with pytest.raises(RegistryError) as raised:
        run(registry.register(settings(), listing()))
    assert str(raised.value) == "REG_SERVER_EXISTS"


def test_server_id_is_checked_again_at_registration(registry):
    """C4.18: ServerSettings 검사를 건너뛴 값도 Registry가 B `valid_server_id`로 거부한다."""
    for server_id in ("a__b", "trailing_"):
        bad = ServerSettings.model_construct(**{**settings().__dict__, "id": server_id})
        with pytest.raises(RegistryError) as raised:
            run(registry.register(bad, listing()))
        assert str(raised.value) == "REG_SERVER_ID_INVALID"
    with pytest.raises(RegistryError) as raised:
        run(registry.register({"id": "docs"}, listing()))
    assert str(raised.value) == "REG_SETTINGS_INVALID"
    assert registry.registration("a__b") is None


@pytest.mark.parametrize(
    ("broken", "code"),
    [
        (
            ToolListing(complete=False, tools=(), reason="TOOLS_LIST_TIMEOUT"),
            "REG_LISTING_INCOMPLETE",
        ),
        (
            ToolListing(complete=True, tools=(FrozenJsonObject({"name": "read"}),), reason=None),
            "REG_TOOL_SCHEMA_INVALID",
        ),
    ],
)
def test_unusable_listing_registers_nothing(registry, store, broken, code):
    with pytest.raises(RegistryError) as raised:
        run(registry.register(settings(), broken))
    assert str(raised.value) == code
    assert run(store.list_registrations()) == ()


def test_activate_uses_the_fingerprints_the_user_saw(registry, store):
    entry = run(registry.register(settings(), listing()))
    active = run(
        registry.activate(
            "docs", expected_row_revision=entry.registration.row_revision, reviewed_by="operator"
        )
    )
    assert active.registration.status is RegStatus.ACTIVE
    assert registry.status("docs") is RegStatus.ACTIVE
    assert run(store.load_registration("docs")) == active
    assert active.registration.tools == entry.registration.tools


def _revision(registry: ServerRegistry) -> int:
    return registry.entry("docs").registration.row_revision


def test_failed_write_closes_the_server_until_reread(registry, store):
    """C4.19: 실패한 제어 쓰기 뒤에는 DB를 다시 읽기 전까지 ACTIVE로 보지 않는다."""
    seen = run(registry.register(settings(), listing())).registration.row_revision
    run(registry.activate("docs", expected_row_revision=seen, reviewed_by="operator"))
    before = registry.entry("docs")
    with pytest.raises(StoreError):
        run(registry.block("docs", expected_row_revision=seen))  # 활성화 전에 본 revision
    assert registry.entry("docs") is before
    assert registry.status("docs") is RegStatus.PENDING
    assert run(registry.refresh("docs")) == run(store.load_registration("docs"))
    assert registry.status("docs") is RegStatus.ACTIVE
    run(registry.block("docs", expected_row_revision=_revision(registry)))
    assert registry.status("docs") is RegStatus.BLOCKED


def test_load_restores_after_restart_without_trusting_it_for_execution(store):
    first = ServerRegistry(store, clock=lambda: NOW)
    run(first.load())
    run(first.register(settings(), listing()))
    run(first.activate("docs", expected_row_revision=_revision(first), reviewed_by="operator"))
    restarted = ServerRegistry(store, clock=lambda: NOW)
    assert restarted.status("docs") is None  # 불러오기 전에는 모른다
    run(restarted.load())
    assert restarted.registration("docs") == first.registration("docs")


def test_unchanged_listing_writes_nothing(registry, store):
    _active(registry)
    before = registry.entry("docs")
    calls = []

    async def invalidate(server_id, tool_name):
        calls.append((server_id, tool_name))
        return 0

    change = run(registry.observe(settings(), listing(), invalidate_tool=invalidate))
    assert change.changed_tools == ()
    assert registry.entry("docs") is before
    assert run(store.load_registration("docs")) == before
    assert calls == []


@pytest.mark.parametrize(
    ("observed", "changed"),
    [
        (listing(edit={"read": {"description": "Read a document."}}), ("read",)),
        (listing("read", "send"), ("extra",)),
        (
            ToolListing(
                complete=True,
                tools=(
                    *listing().tools,
                    FrozenJsonObject({"name": "new_tool", "inputSchema": {"type": "object"}}),
                ),
                reason=None,
            ),
            ("new_tool",),
        ),
    ],
    ids=["definition-changed", "tool-removed", "tool-added"],
)
def test_changed_listing_drops_to_pending_and_invalidates(registry, store, observed, changed):
    """I10: ACTIVE에서 정의가 바뀌면 사람 개입 없이 PENDING, 바뀐 도구의 대기 승인 무효."""
    _active(registry)
    calls = []

    async def invalidate(server_id, tool_name):
        calls.append((server_id, tool_name))
        return 1

    change = run(registry.observe(settings(), observed, invalidate_tool=invalidate))
    assert change.changed_tools == changed
    assert registry.status("docs") is RegStatus.PENDING
    assert registry.entry("docs") == run(store.load_registration("docs"))
    assert calls == [("docs", name) for name in changed]
    added = {tool.tool_name: tool for tool in registry.registration("docs").tools}
    if "new_tool" in added:
        assert added["new_tool"].kind is ToolKind.OTHER


def test_incomplete_listing_is_not_recorded(registry, store):
    _active(registry)
    before = run(store.load_registration("docs"))
    with pytest.raises(RegistryError) as raised:
        run(
            registry.observe(
                settings(), ToolListing(complete=False, tools=(), reason="TOOLS_LIST_TIMEOUT")
            )
        )
    assert str(raised.value) == "REG_LISTING_INCOMPLETE"
    assert run(store.load_registration("docs")) == before
    assert registry.status("docs") is RegStatus.ACTIVE


def test_settings_boundary_must_match_the_registration(registry):
    _active(registry)
    with pytest.raises(RegistryError) as raised:
        run(registry.observe(settings(max_label="PUBLIC"), listing()))
    assert str(raised.value) == "REG_SETTINGS_MISMATCH"
    with pytest.raises(RegistryError) as raised:
        run(registry.observe(settings(id="other"), listing()))
    assert str(raised.value) == "REG_SERVER_UNKNOWN"


def test_unrecorded_change_keeps_the_server_pending_this_run(registry, store, monkeypatch):
    """`RegistryStore`: 기록이 실패하면 그 서버를 쓰지 않는다. DB가 ACTIVE여도 이번엔 PENDING."""
    _active(registry)

    async def fail(*args, **kwargs):
        raise StoreError()

    monkeypatch.setattr(store, "record_fingerprint_change", fail)
    with pytest.raises(StoreError):
        run(registry.observe(settings(), listing(edit={"read": {"description": "x"}})))
    assert run(store.load_registration("docs")).registration.status is RegStatus.ACTIVE
    assert registry.status("docs") is RegStatus.PENDING
    assert registry.registration("docs").status is RegStatus.PENDING
    with pytest.raises(RegistryError) as raised:
        run(
            registry.activate(
                "docs", expected_row_revision=_revision(registry), reviewed_by="operator"
            )
        )
    assert str(raised.value) == "REG_SERVER_UNRECORDED"
    run(registry.refresh("docs"))  # 다시 읽어도 대조 실패 표시는 남는다
    assert registry.status("docs") is RegStatus.PENDING
    monkeypatch.undo()
    run(registry.load())  # 다음 시작은 다시 대조한다
    assert registry.status("docs") is RegStatus.ACTIVE


def test_block_review_and_descriptions_replace_the_cache(registry, store):
    _active(registry)
    blocked = run(registry.block("docs", expected_row_revision=_revision(registry)))
    assert registry.status("docs") is RegStatus.BLOCKED
    pending = run(
        registry.request_review("docs", expected_row_revision=blocked.registration.row_revision)
    )
    assert registry.status("docs") is RegStatus.PENDING
    run(
        registry.save_reviewed_descriptions(
            "docs",
            {"read": "검토한 설명"},
            expected_row_revision=pending.registration.row_revision,
        )
    )
    tools = {tool.tool_name: tool for tool in registry.registration("docs").tools}
    assert tools["read"].reviewed_description == "검토한 설명"
    assert registry.entry("docs") == run(store.load_registration("docs"))


def test_control_operations_run_one_at_a_time(registry, store, monkeypatch):
    _active(registry)
    order = []
    gate = asyncio.Event()
    original = store.record_fingerprint_change

    async def slow(*args, **kwargs):
        order.append("observe-start")
        await gate.wait()
        order.append("observe-end")
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, "record_fingerprint_change", slow)

    seen = _revision(registry)

    async def scenario():
        observing = asyncio.create_task(
            registry.observe(settings(), listing(edit={"read": {"description": "x"}}))
        )
        await asyncio.sleep(0)
        blocking = asyncio.create_task(registry.block("docs", expected_row_revision=seen))
        await asyncio.sleep(0.05)
        order.append("released")
        gate.set()
        await observing
        with pytest.raises(StoreError):
            await blocking  # observe가 revision을 올려 seen은 낡았다

    run(scenario())
    assert order == ["observe-start", "released", "observe-end"]


def _commit_then_hang(store, monkeypatch, method: str) -> asyncio.Event:
    """B6.13 뒤처럼 쓰기는 커밋됐는데 반환 전에 호출자가 취소되는 저장 호출."""
    original = getattr(store, method)
    committed = asyncio.Event()

    async def write(*args, **kwargs):
        await original(*args, **kwargs)
        committed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(store, method, write)
    return committed


def _cancel_after_commit(operation, committed: asyncio.Event) -> None:
    async def scenario():
        task = asyncio.create_task(operation)
        await committed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())


def _registered(registry: ServerRegistry) -> None:
    run(registry.register(settings(), listing()))


@pytest.mark.parametrize(
    ("method", "setup", "operation", "stored"),
    [
        (
            "register_server",
            lambda registry: None,
            lambda registry: registry.register(settings(), listing()),
            RegStatus.PENDING,
        ),
        (
            "activate_server",
            _registered,
            lambda registry: registry.activate(
                "docs", expected_row_revision=_revision(registry), reviewed_by="operator"
            ),
            RegStatus.ACTIVE,
        ),
        (
            "block_server",
            _active,
            lambda registry: registry.block("docs", expected_row_revision=_revision(registry)),
            RegStatus.BLOCKED,
        ),
        (
            "request_review",
            _active,
            lambda registry: registry.request_review(
                "docs", expected_row_revision=_revision(registry)
            ),
            RegStatus.PENDING,
        ),
        (
            "save_reviewed_descriptions",
            _registered,
            lambda registry: registry.save_reviewed_descriptions(
                "docs", {"read": "검토한 설명"}, expected_row_revision=_revision(registry)
            ),
            RegStatus.PENDING,
        ),
    ],
    ids=["register", "activate", "block", "request_review", "save_reviewed_descriptions"],
)
def test_write_cancelled_after_commit_is_closed_until_reread(
    registry, store, monkeypatch, method, setup, operation, stored
):
    """C4.19(#46 B 리뷰): 커밋 뒤 취소되면 캐시가 ACTIVE로 남지 않고, 다시 읽으면 DB와 같다."""
    setup(registry)
    committed = _commit_then_hang(store, monkeypatch, method)
    _cancel_after_commit(operation(registry), committed)
    saved = run(store.load_registration("docs"))
    assert saved.registration.status is stored
    assert registry.status("docs") is not RegStatus.ACTIVE
    assert run(registry.refresh("docs")) == saved
    assert registry.entry("docs") == saved
    assert registry.status("docs") is stored


def test_next_control_operation_rereads_after_a_cancelled_block(registry, store, monkeypatch):
    """B 리뷰의 위험한 경우: DB는 BLOCKED인데 캐시는 ACTIVE. 다음 조작은 DB부터 다시 읽는다."""
    _active(registry)
    old_revision = _revision(registry)
    committed = _commit_then_hang(store, monkeypatch, "block_server")
    _cancel_after_commit(registry.block("docs", expected_row_revision=old_revision), committed)
    assert registry.status("docs") is RegStatus.PENDING
    blocked = run(store.load_registration("docs"))
    assert blocked.registration.status is RegStatus.BLOCKED
    with pytest.raises(StoreError):  # 다시 읽은 revision과 다르다
        run(registry.request_review("docs", expected_row_revision=old_revision))
    assert registry.entry("docs") == blocked
    assert registry.status("docs") is RegStatus.BLOCKED
    run(registry.request_review("docs", expected_row_revision=blocked.registration.row_revision))
    assert registry.status("docs") is RegStatus.PENDING


def test_register_cancelled_after_commit_is_found_again(registry, store, monkeypatch):
    committed = _commit_then_hang(store, monkeypatch, "register_server")
    _cancel_after_commit(registry.register(settings(), listing()), committed)
    assert registry.registration("docs") is None
    with pytest.raises(RegistryError) as raised:
        run(registry.register(settings(), listing()))
    assert str(raised.value) == "REG_SERVER_EXISTS"
    assert registry.entry("docs") == run(store.load_registration("docs"))


def test_observe_cancelled_after_commit_keeps_the_server_pending_this_run(
    registry, store, monkeypatch
):
    _active(registry)
    committed = _commit_then_hang(store, monkeypatch, "record_fingerprint_change")
    _cancel_after_commit(
        registry.observe(settings(), listing(edit={"read": {"description": "x"}})), committed
    )
    assert run(store.load_registration("docs")).registration.status is RegStatus.PENDING
    assert registry.status("docs") is RegStatus.PENDING
    run(registry.refresh("docs"))
    with pytest.raises(RegistryError) as raised:
        run(
            registry.activate(
                "docs", expected_row_revision=_revision(registry), reviewed_by="operator"
            )
        )
    assert str(raised.value) == "REG_SERVER_UNRECORDED"


def test_cancel_during_invalidation_leaves_the_rest_to_the_pending_state(registry, store):
    _active(registry)
    calls = []
    started = asyncio.Event()

    async def invalidate(server_id, tool_name):
        calls.append(tool_name)
        started.set()
        await asyncio.Event().wait()  # 첫 무효화 중에 취소된다
        return 1

    observed = listing(edit={"read": {"description": "x"}, "send": {"description": "y"}})

    async def scenario():
        task = asyncio.create_task(
            registry.observe(settings(), observed, invalidate_tool=invalidate)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert calls == ["read"]
    assert registry.status("docs") is RegStatus.PENDING
    assert registry.entry("docs") == run(store.load_registration("docs"))
    # 기록은 끝났으므로 사용자가 다시 확인하면 올릴 수 있다
    run(
        registry.activate("docs", expected_row_revision=_revision(registry), reviewed_by="operator")
    )
    assert registry.status("docs") is RegStatus.ACTIVE


class _UnexpectedStoreError(RuntimeError):
    """B 계약 밖의 예외. 저장소가 커밋한 뒤 던졌다고 본다."""


def _commit_then_raise(store, monkeypatch, method: str) -> None:
    original = getattr(store, method)

    async def write(*args, **kwargs):
        await original(*args, **kwargs)
        raise _UnexpectedStoreError

    monkeypatch.setattr(store, method, write)


def test_unexpected_store_exception_on_a_write_also_closes_the_server(registry, store, monkeypatch):
    """#53 B 리뷰 2번: `StoreError`가 아닌 예외에서도 커밋 여부는 모른다."""
    _active(registry)
    _commit_then_raise(store, monkeypatch, "block_server")
    with pytest.raises(_UnexpectedStoreError):
        run(registry.block("docs", expected_row_revision=_revision(registry)))
    assert registry.status("docs") is RegStatus.PENDING
    assert run(registry.refresh("docs")) == run(store.load_registration("docs"))
    assert registry.status("docs") is RegStatus.BLOCKED


def test_unexpected_store_exception_on_observe_keeps_the_server_pending_this_run(
    registry, store, monkeypatch
):
    _active(registry)
    _commit_then_raise(store, monkeypatch, "record_fingerprint_change")
    with pytest.raises(_UnexpectedStoreError):
        run(registry.observe(settings(), listing(edit={"read": {"description": "x"}})))
    assert registry.status("docs") is RegStatus.PENDING
    with pytest.raises(RegistryError) as raised:
        run(
            registry.activate(
                "docs", expected_row_revision=_revision(registry), reviewed_by="operator"
            )
        )
    assert str(raised.value) == "REG_SERVER_UNRECORDED"


def test_cancel_while_waiting_for_the_lock_marks_nothing(registry, store, monkeypatch):
    _active(registry)
    gate = asyncio.Event()
    original = store.record_fingerprint_change

    async def slow(*args, **kwargs):
        await gate.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, "record_fingerprint_change", slow)
    seen = _revision(registry)

    async def scenario():
        observing = asyncio.create_task(registry.observe(settings(), listing()))
        await asyncio.sleep(0)
        blocking = asyncio.create_task(registry.block("docs", expected_row_revision=seen))
        await asyncio.sleep(0)
        blocking.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocking
        gate.set()
        await observing

    run(scenario())
    assert run(store.load_registration("docs")).registration.status is RegStatus.ACTIVE
    assert registry.status("docs") is RegStatus.ACTIVE


DOCUMENT = {
    "profile": {
        "id": "p",
        "servers": ["docs"],
        "destinations": ["external"],
        "sources": [{"server": "docs", "tool": "read", "prefix": [], "label": "PUBLIC"}],
    },
    "servers": [SERVER],
    "destinations": [{"id": "external", "tier": "general_external", "max_label": "INTERNAL"}],
    "access_rules": {"allow": [["docs", "read"], ["docs", "send"]], "deny": []},
}


def test_catalog_shows_registered_tools_until_a_change_drops_them(registry):
    """B Catalog와 맞물림: 활성화 뒤 설정에 있는 도구만 보이고, 정의가 바뀌면 빠진다."""
    _active(registry)
    loaded = load_profile_settings(DOCUMENT, registry)
    assert {view.tool_ref for view in loaded.catalog.tool_views(("docs",))} == {
        "docs__read",
        "docs__send",
    }
    run(registry.observe(settings(), listing(edit={"send": {"description": "changed"}})))
    reloaded = load_profile_settings(DOCUMENT, registry)
    assert reloaded.catalog.tool_views(("docs",)) == ()


def test_errors_carry_no_server_text(registry):
    marker = ToolListing(
        complete=True,
        tools=(FrozenJsonObject({"name": "marker-7f3a", "inputSchema": "not-an-object"}),),
        reason=None,
    )
    with pytest.raises(RegistryError) as raised:
        run(registry.register(settings(), marker))
    assert str(raised.value) == "REG_TOOL_SCHEMA_INVALID"
    assert "marker" not in repr(raised.value)
