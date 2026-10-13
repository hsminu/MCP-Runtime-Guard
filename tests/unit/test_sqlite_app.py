"""A SQLite 앱 연결 검증. B 공개 API만 사용하며 C 독립 인수를 대신하지 않는다."""

import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from examples import synthetic_app
from guard_app.__main__ import main
from guard_app.domain.contracts import Denied, Released
from guard_app.domain.enums import ApprovalStatus, Label, TaskStatus
from guard_app.domain.errors import StoreError
from guard_app.services.bridge import BackendBridge, BridgeState, CommandStage
from guard_app.storage.sqlite_store import SQLiteStore

from .test_bridge import pump_until


@pytest.fixture
def observed(monkeypatch, tmp_path):
    """실제 Store·transport 호출과 합성 수신 파일을 관찰한다."""
    seen = SimpleNamespace(
        stores=[],
        starts=[],
        dispatches=[],
        counters=[],
        http_starts=[],
        http_counts=[],
        agt_starts=[],
    )
    original_store = synthetic_app.SQLiteStore
    original_transport_init = synthetic_app.McpTransport.__init__
    original_start = synthetic_app.McpTransport.start
    original_stream = synthetic_app.stdio_stream_factory
    original_http = synthetic_app.http_spike
    original_agt_start = synthetic_app.AgentGovernanceAccessChecker.start

    def store(*args, **kwargs):
        instance = original_store(*args, **kwargs)
        seen.stores.append(instance)
        return instance

    def transport_init(self, *args, **kwargs):
        on_dispatch = kwargs["on_dispatch"]

        def dispatched(request):
            seen.dispatches.append(request)
            on_dispatch(request)

        kwargs["on_dispatch"] = dispatched
        original_transport_init(self, *args, **kwargs)

    async def start(self):
        seen.starts.append(1)
        await original_start(self)

    def stream(params, **kwargs):
        seen.counters.append(Path(params.args[-1]))
        return original_stream(params, **kwargs)

    @asynccontextmanager
    async def http():
        seen.http_starts.append(1)
        async with original_http() as spike:
            seen.http_counts.append(spike.counts)
            yield spike

    async def agt_start(self):
        seen.agt_starts.append(1)
        await original_agt_start(self)

    monkeypatch.setattr(synthetic_app, "SQLiteStore", store)
    monkeypatch.setattr(synthetic_app.McpTransport, "__init__", transport_init)
    monkeypatch.setattr(synthetic_app.McpTransport, "start", start)
    monkeypatch.setattr(synthetic_app, "stdio_stream_factory", stream)
    monkeypatch.setattr(synthetic_app, "http_spike", http)
    monkeypatch.setattr(synthetic_app.AgentGovernanceAccessChecker, "start", agt_start)
    return seen


def counts(observed):
    if observed.http_counts:
        return dict(observed.http_counts[-1])
    return json.loads(observed.counters[-1].read_text(encoding="utf-8"))


def assert_not_started(observed):
    assert observed.starts == observed.dispatches == observed.counters == []
    assert observed.http_starts == observed.http_counts == observed.agt_starts == []
    # stdio counts 파일/stream 구성도 없으므로 서버 spawn·wire·receiver effect가 없다.


async def approve_once(bindings, observed):
    dispatch_start = len(observed.dispatches)
    task_id = bindings.initial_task_id
    runner = bindings.runner_for_task(task_id)
    pending = await runner.advance()
    assert pending.status == "pending" and pending.approval_id is not None
    before = await bindings.snapshot(task_id)
    assert before.approval_id == pending.approval_id
    assert (before.host_dispatch, before.receiver_effect) == (1, 0)  # READ만 실행
    assert counts(observed) == {"receiver_calls": 1, "receiver_effect": 0}
    assert len(observed.dispatches) - dispatch_start == 1  # READ만 실행
    assert await bindings.human_control.review(pending.approval_id) is not None
    result = await bindings.human_control.approve(pending.approval_id)
    assert isinstance(result, Released)
    await runner.resume_approval(task_id, runner.epoch, pending.approval_id, result)
    assert isinstance(await bindings.human_control.approve(pending.approval_id), Denied)
    after = await bindings.snapshot(task_id)
    assert (after.host_dispatch, after.receiver_effect) == (2, 1)
    assert len(observed.dispatches) - dispatch_start == 2  # READ 1 + SEND 1
    assert counts(observed) == {"receiver_calls": 2, "receiver_effect": 1}
    assert after.approval_id is None and after.status is TaskStatus.RUNNING
    return pending.approval_id


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["ask", "allow", "deny"])
@pytest.mark.parametrize("mcp_transport", ["stdio", "http"])
async def test_sqlite_scenario_uses_real_store_and_transport(
    tmp_path, observed, scenario, mcp_transport
):
    """I1/I2/I3/I4/I5, T01/T02/T03/T12의 A 부분: 판정·중복 승인·수신 계수."""
    bindings = await synthetic_app.bootstrap(
        scenario,
        agt_mode="mock",
        mcp_transport=mcp_transport,
        store_kind="sqlite",
        db_path=tmp_path / "app.db",
    )
    try:
        task_id = bindings.initial_task_id
        store = observed.stores[-1]
        assert (await store.load_task(task_id)).status is TaskStatus.RUNNING
        assert (await bindings.snapshot(task_id)).host_dispatch == 0
        assert counts(observed) == {"receiver_calls": 0, "receiver_effect": 0}
        assert bool(observed.http_counts) is (mcp_transport == "http")
        if scenario == "ask":
            approval_id = await approve_once(bindings, observed)
            assert (await store.get_approval(approval_id)).status is ApprovalStatus.CONSUMED
        else:
            update = await bindings.runner_for_task(task_id).advance()
            assert update.status == ("denied" if scenario == "deny" else "continue")
        expected = int(scenario != "deny")
        snapshot = await bindings.snapshot(task_id)
        assert snapshot.host_dispatch == 1 + expected
        assert snapshot.receiver_effect == expected
        assert snapshot.approval_id is None
        assert counts(observed) == {"receiver_calls": 1 + expected, "receiver_effect": expected}
        state = await store.load_task(task_id)
        assert state.status is TaskStatus.RUNNING and not state.unclassified
        assert (
            state.high_watermark
            is {"ask": Label.INTERNAL, "allow": Label.PUBLIC, "deny": Label.CONFIDENTIAL}[scenario]
        )
        assert len(observed.dispatches) == 1 + expected
    finally:
        await bindings.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_parent", "corrupt", "empty", "memory"])
@pytest.mark.parametrize("mcp_transport", ["stdio", "http"])
async def test_open_failure_stops_before_external_resources(
    tmp_path, observed, failure, mcp_transport, capsys, caplog
):
    """A 시작 게이트(I1/I6)는 직접 대응 T/S ID 없음; T19 부분(I9): 오류 유출 0."""
    path = tmp_path / "PRIVATE_DB_MARKER" / "app.db"
    if failure == "corrupt":
        path = tmp_path / "PRIVATE_DB_MARKER.db"
        path.write_bytes(b"NOT_SQLITE_PRIVATE_CONTENT" * 100)
    elif failure == "empty":
        path = ""
    elif failure == "memory":
        path = ":memory:"
    with pytest.raises(StoreError) as caught:
        await synthetic_app.bootstrap(
            agt_mode="real", mcp_transport=mcp_transport, store_kind="sqlite", db_path=path
        )
    assert_not_started(observed)
    assert not observed.stores
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    output = capsys.readouterr()
    public = str(caught.value) + output.out + output.err + caplog.text
    assert "PRIVATE_DB_MARKER" not in public and "NOT_SQLITE_PRIVATE_CONTENT" not in public
    assert str(tmp_path) not in public
    if failure == "missing_parent":
        assert not path.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["create", "reload", "mismatch"])
async def test_task_gate_failure_closes_store_without_start(
    tmp_path, observed, monkeypatch, failure
):
    """직접 대응 T/S ID 없음, A 시작 게이트(I1/I6): Task 저장·확인 실패면 Guard 0."""
    closed, guards = [], []
    original_close = SQLiteStore.close
    original_load = SQLiteStore.load_task

    def close(self):
        original_close(self)
        closed.append(self)

    async def fail_create(self, state, **kwargs):
        raise StoreError()

    async def bad_load(self, task_id):
        if failure == "reload":
            raise StoreError()
        state = await original_load(self, task_id)
        return state.model_copy(update={"epoch": state.epoch + 1})

    def guard(*args, **kwargs):
        guards.append(1)
        raise AssertionError("Guard가 시작 게이트 전에 생성됨")

    monkeypatch.setattr(SQLiteStore, "close", close)
    monkeypatch.setattr(synthetic_app, "GuardService", guard)
    if failure == "create":
        monkeypatch.setattr(SQLiteStore, "create_task", fail_create)
    else:
        monkeypatch.setattr(SQLiteStore, "load_task", bad_load)
    path = tmp_path / "app.db"
    with pytest.raises(StoreError):
        await synthetic_app.bootstrap(agt_mode="real", store_kind="sqlite", db_path=path)
    assert_not_started(observed)
    assert closed == observed.stores and len(closed) == 1 and not guards
    with SQLiteStore(path):
        pass


def test_bridge_open_failure_is_fixed_startup_failure(tmp_path, observed):
    """직접 대응 T/S ID 없음, A 시작 게이트(I1/I6): STARTUP_FAILED 후 run 거부."""
    app = QApplication.instance() or QApplication([])
    events, commands = [], []
    bridge = BackendBridge(
        lambda: synthetic_app.bootstrap(
            agt_mode="mock", store_kind="sqlite", db_path=tmp_path / "absent" / "app.db"
        )
    )
    bridge.lifecycle_changed.connect(events.append)
    bridge.command_changed.connect(commands.append)
    try:
        bridge.start()
        pump_until(app, lambda: bridge.thread_done, timeout=8)
        assert bridge.state is BridgeState.FAILED
        assert events[-1].code == "STARTUP_FAILED"
        bridge.request_run("unavailable")
        pump_until(app, lambda: bool(commands))
        assert commands[-1].stage is CommandStage.REJECTED
        assert commands[-1].code == "NOT_READY"
        assert_not_started(observed)
    finally:
        bridge.close()
        pump_until(app, lambda: bridge.thread_done)


@pytest.mark.asyncio
async def test_restart_does_not_restore_old_approval_or_task(tmp_path, observed):
    """I3/I4/I7/I8, T11 재시작 부분: 이전 승인 실행 0, 새 Task만 사용(대화·캐시 미검증)."""
    path = tmp_path / "app.db"
    first = await synthetic_app.bootstrap(agt_mode="mock", store_kind="sqlite", db_path=path)
    try:
        old_task_id = first.initial_task_id
        pending = await first.runner_for_task(old_task_id).advance()
        assert pending.status == "pending"
        old_approval = await observed.stores[-1].get_approval(pending.approval_id)
        old_state = await observed.stores[-1].load_task(old_task_id)
        assert old_state.status is TaskStatus.WAITING_APPROVAL
        assert counts(observed) == {"receiver_calls": 1, "receiver_effect": 0}
    finally:
        await first.close()
    second = await synthetic_app.bootstrap(agt_mode="mock", store_kind="sqlite", db_path=path)
    try:
        assert observed.stores[0] is not observed.stores[1]
        assert second.human_control is not first.human_control
        assert second.initial_task_id != old_task_id
        assert await second.human_control.review(pending.approval_id) is None
        assert isinstance(await second.human_control.approve(pending.approval_id), Denied)
        snapshot = await second.snapshot(second.initial_task_id)
        assert (snapshot.host_dispatch, snapshot.receiver_effect) == (0, 0)
        assert snapshot.approval_id is None and snapshot.can_advance
        assert snapshot.status is TaskStatus.RUNNING
        assert counts(observed) == {"receiver_calls": 0, "receiver_effect": 0}
        # 테스트만 과거 행을 조회한다. 앱은 이전 행을 재개·정리하지 않는다.
        store = observed.stores[-1]
        assert await store.load_task(old_task_id) == old_state
        assert await store.get_approval(pending.approval_id) == old_approval
        fresh = await store.load_task(second.initial_task_id)
        assert fresh.high_watermark is Label.PUBLIC and not fresh.unclassified
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_restart_ids_do_not_collide_and_file_reopens(tmp_path, observed):
    """I1/I3/I6, T02/T12/T13의 A 부분: 같은 DB의 별개 정상 요청을 각각 1회 처리."""
    path = tmp_path / "app.db"
    task_ids, approval_ids = set(), set()
    for _ in range(2):
        bindings = await synthetic_app.bootstrap(agt_mode="mock", store_kind="sqlite", db_path=path)
        try:
            task_ids.add(bindings.initial_task_id)
            approval_ids.add(await approve_once(bindings, observed))
            assert counts(observed) == {"receiver_calls": 2, "receiver_effect": 1}
        finally:
            await bindings.close()
    assert len(task_ids) == len(approval_ids) == 2
    assert len(observed.dispatches) == 4
    with SQLiteStore(path) as reopened:
        for task_id in task_ids:
            assert (await reopened.load_task(task_id)).status is TaskStatus.RUNNING
        for approval_id in approval_ids:
            assert (await reopened.get_approval(approval_id)).status is ApprovalStatus.CONSUMED


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "agt", "transport"])
async def test_store_closes_last_on_owner_thread(tmp_path, monkeypatch, failure):
    """직접 대응 T/S ID 없음, A 자원 수명(I6): 같은 스레드의 Store를 마지막 정리."""
    events = []
    owner = threading.get_ident()
    original_store = synthetic_app.SQLiteStore

    def opened(*args, **kwargs):
        store = original_store(*args, **kwargs)
        events.append(("store_open", threading.get_ident()))
        return store

    monkeypatch.setattr(synthetic_app, "SQLiteStore", opened)
    for cls, label in [(SQLiteStore, "store"), (synthetic_app.TaskRunner, "runner")]:
        original = cls.close

        def closed(self, original=original, label=label):
            original(self)
            events.append((label, threading.get_ident()))

        monkeypatch.setattr(cls, "close", closed)
    for cls, label in [
        (synthetic_app.McpTransport, "transport"),
        (synthetic_app.AgentGovernanceAccessChecker, "agt"),
    ]:
        original_close, original_start = cls.close, cls.start

        async def closed(self, original=original_close, label=label):
            await original(self)
            events.append((label, threading.get_ident()))

        async def started(self, original=original_start, label=label):
            if label == failure:
                raise ValueError("합성 시작 실패")
            await original(self)

        monkeypatch.setattr(cls, "close", closed)
        monkeypatch.setattr(cls, "start", started)
    path = tmp_path / "app.db"
    if failure:
        with pytest.raises(ValueError, match="합성 시작 실패"):
            await synthetic_app.bootstrap(agt_mode="real", store_kind="sqlite", db_path=path)
    else:
        bindings = await synthetic_app.bootstrap(agt_mode="real", store_kind="sqlite", db_path=path)
        await bindings.close()
    assert events[0][0] == "store_open" and events[-1][0] == "store"
    expected = {
        None: ["store_open", "runner", "transport", "agt", "store"],
        "agt": ["store_open", "agt", "store"],
        "transport": ["store_open", "transport", "agt", "store"],
    }[failure]
    assert [label for label, _ in events] == expected
    assert all(thread == owner for _, thread in events)
    with original_store(path):
        pass


@pytest.mark.parametrize("during_bootstrap", [False, True])
def test_bridge_reports_store_cleanup_failure(tmp_path, monkeypatch, during_bootstrap):
    """직접 대응 T/S ID 없음, A 종료 상태(I6): Store close 실패를 미확인으로 표시."""
    app = QApplication.instance() or QApplication([])
    events, threads = [], []
    original_close = SQLiteStore.close
    original_create = SQLiteStore.create_task

    def close(self):
        threads.append(("close", threading.get_ident()))
        original_close(self)
        raise StoreError()

    async def create(self, state, **kwargs):
        threads.append(("create", threading.get_ident()))
        await original_create(self, state, **kwargs)
        if during_bootstrap:
            raise StoreError()

    monkeypatch.setattr(SQLiteStore, "close", close)
    monkeypatch.setattr(SQLiteStore, "create_task", create)
    bridge = BackendBridge(
        lambda: synthetic_app.bootstrap(
            agt_mode="mock", store_kind="sqlite", db_path=tmp_path / "app.db"
        )
    )
    bridge.lifecycle_changed.connect(events.append)
    try:
        bridge.start()
        if not during_bootstrap:
            pump_until(app, lambda: bridge.state is BridgeState.READY, timeout=8)
            bridge.close()
        pump_until(app, lambda: bridge.thread_done, timeout=8)
        assert bridge.state is (BridgeState.FAILED if during_bootstrap else BridgeState.CLOSED)
        assert events[-1].code == (
            "STARTUP_CLEANUP_UNCONFIRMED" if during_bootstrap else "CLEANUP_UNCONFIRMED"
        )
        assert len(threads) == 2 and threads[0][1] == threads[1][1]
        assert threads[0][1] != threading.get_ident()
    finally:
        bridge.close()
        pump_until(app, lambda: bridge.thread_done)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"store_kind": "sqlite"},
        {"store_kind": "memory", "db_path": "PRIVATE_DB_MARKER"},
        {"store_kind": "unknown"},
    ],
)
async def test_bootstrap_rejects_invalid_store_configuration(observed, kwargs):
    """A 설정 게이트(I1/I6/I7)는 직접 대응 T/S ID 없음; T19 부분(I9): 경로 비노출."""
    with pytest.raises(ValueError) as caught:
        await synthetic_app.bootstrap(agt_mode="mock", **kwargs)
    assert "PRIVATE_DB_MARKER" not in str(caught.value)
    assert_not_started(observed)


@pytest.mark.parametrize("args", [["--db-path", "PRIVATE_DB_MARKER"], ["--store", "sqlite"]])
def test_cli_rejects_invalid_store_configuration(monkeypatch, capsys, args):
    """A CLI 설정(I6/I7)은 직접 대응 T/S ID 없음; T19 부분(I9): DB 경로 비노출."""
    monkeypatch.setattr(sys, "argv", ["guard-app", "--demo", "--agt-mode", "mock", *args])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "저장소 종류와 DB 경로 설정이 일치하지 않는다" in output.err
    assert "PRIVATE_DB_MARKER" not in output.err + output.out
