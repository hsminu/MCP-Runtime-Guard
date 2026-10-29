"""B6-05 approval list and call log on B's Store queries (#86 follow-up)."""

from __future__ import annotations

import asyncio
import gc
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import ApprovalStatus, Decision, RequestStatus, ToolKind
from guard_app.domain.queries import (
    ApprovalListLane,
    ApprovalListPage,
    ApprovalListQuery,
    ApprovalRow,
    CallDetail,
    CallListPage,
    CallListQuery,
    CallRow,
    CallStage,
    ListSort,
)
from guard_app.domain.reasons import Reason
from guard_app.services import bridge as bridge_module
from guard_app.services.bridge import (
    BackendBindings,
    BackendBridge,
    BridgeState,
    LifecycleEvent,
    QueryReply,
)
from guard_app.services.bridge_queries import QueryLane
from guard_app.ui.approval_presentation import (
    PAGE_SIZE,
    QUERY_TEXT_INVALID,
    QUERY_UNKNOWN,
    SOURCE_NOT_CONNECTED,
    ApprovalLane,
    ApprovalQuery,
    ApprovalSort,
)
from guard_app.ui.approvals import ApprovalListPage as ApprovalListView
from guard_app.ui.call_logs import CallLogPage, CallLogQuery, public_calls_csv
from guard_app.ui.models import LoadState
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.store_views import (
    StoreQueryLink,
    approval_list_query,
    approval_summary,
    call_detail,
    call_list_query,
    call_summary,
)
from guard_app.ui.window import GuardWindow

from .test_bridge import FakeControl, close_bridge, pump_until
from .test_bridge import app as app
from .test_call_logs import panel_text

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def cleanup_qt_objects():
    application = QApplication.instance()
    existing = set(application.topLevelWidgets()) if application is not None else set()
    yield
    # Worker GC of page cycles queued double-owned children for main-thread deletion;
    # Qt deleted them with the page first, causing an abort (ownership fixed in A178).
    # Still delete this test's widgets and collect cycles on the main thread.
    assert threading.current_thread() is threading.main_thread()
    application = QApplication.instance()
    if application is not None:
        for widget in application.topLevelWidgets():
            if widget not in existing:
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
    gc.collect()


def approval_row(number: int, *, status: ApprovalStatus = ApprovalStatus.PENDING) -> ApprovalRow:
    created = NOW - timedelta(minutes=number)
    return ApprovalRow(
        approval_id=f"approval-{number:03}",
        task_id=f"task-{number:03}",
        request_id=f"request-{number:03}",
        server_id="drive",
        tool_name="upload",
        destination_id="partner-share",
        status=status,
        created_at_utc=created,
        expires_at_utc=created + timedelta(minutes=5),
        decided_at_utc=None if status is ApprovalStatus.PENDING else NOW,
        reason_code=None,
        row_revision=number + 1,
    )


def call_row(
    number: int,
    *,
    decision: Decision | None = Decision.ALLOW,
    approval_status: ApprovalStatus | None = None,
    reasons: tuple[Reason, ...] = (Reason.OK,),
) -> CallRow:
    return CallRow(
        request_id=f"request-{number:03}",
        task_id="task-001",
        epoch=1,
        call_seq=number,
        server_id="docs",
        tool_name="read",
        kind=ToolKind.READ,
        destination_id=None,
        decision=decision,
        reason_codes=reasons,
        approval_id=None,
        approval_status=approval_status,
        status=RequestStatus.SUCCEEDED,
        decided_at_utc=NOW - timedelta(seconds=number),
        row_revision=1,
    )


def detail_of(row: CallRow) -> CallDetail:
    return CallDetail(
        row=row,
        observed_label=None,
        stages=(
            CallStage(stage_code="DECIDED", at_utc=NOW),
            CallStage(stage_code="COMMITTED", at_utc=NOW),
        ),
    )


def approvals_query(offset: int = 0) -> ApprovalListQuery:
    return ApprovalListQuery(
        lane=ApprovalListLane.PENDING, sort=ListSort.OLDEST, offset=offset, limit=PAGE_SIZE
    )


def calls_query() -> CallListQuery:
    return CallListQuery(sort=ListSort.NEWEST, offset=0, limit=50)


async def until(predicate: Callable[[], bool]) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.002)
    assert predicate()


def test_store_rows_become_screen_rows_without_a_tier_or_a_guessed_expiry() -> None:
    approval = approval_summary(approval_row(1))
    assert approval.row_revision == 2
    assert approval.expires_at == NOW + timedelta(minutes=4)
    assert approval.destination_summary == "partner-share"
    allowed = call_summary(call_row(1))
    assert allowed.call_id == allowed.request_id == "request-001"
    assert allowed.approval_status == "NOT_REQUIRED"
    assert call_summary(call_row(2, decision=Decision.ASK)).approval_status is None
    asked = call_summary(
        call_row(
            3,
            decision=Decision.ASK,
            approval_status=ApprovalStatus.CONSUMED,
            reasons=(Reason.INTERNAL_TO_EXTERNAL, Reason.OK),
        )
    )
    assert asked.initial_decision == "ASK"
    assert asked.approval_status == "CONSUMED"
    assert asked.reason_codes == ("INTERNAL_TO_EXTERNAL", "OK")
    assert asked.reason_code == "INTERNAL_TO_EXTERNAL"
    assert not hasattr(asked, "boundary_tier")
    assert "경계" not in public_calls_csv((asked,))
    detail = call_detail(detail_of(call_row(1)))
    assert [(stage.stage_code, stage.state) for stage in detail.stages or ()] == [
        ("DECIDED", "OCCURRED"),
        ("COMMITTED", "OCCURRED"),
    ]


def test_screen_queries_follow_b_limits_and_keep_the_search_case() -> None:
    def approvals(search: str, server_id: str, page: int) -> ApprovalListQuery | None:
        return approval_list_query(
            ApprovalQuery(1, ApprovalLane.HISTORY, search, server_id, ApprovalSort.NEWEST, page)
        )

    assert approvals("Task-01", "drive", 2) == ApprovalListQuery(
        lane=ApprovalListLane.HISTORY,
        search="Task-01",
        server_id="drive",
        sort=ListSort.NEWEST,
        offset=PAGE_SIZE,
        limit=PAGE_SIZE,
    )
    assert approvals("", "", 1) == ApprovalListQuery(
        lane=ApprovalListLane.HISTORY, sort=ListSort.NEWEST, offset=0, limit=PAGE_SIZE
    )
    for search, server_id, page in (("한글", "", 1), ("", "a__b", 1), ("x" * 65, "", 1)):
        assert approvals(search, server_id, page) is None
    assert approvals("", "", 10_000 // PAGE_SIZE + 2) is None  # offset past 10,000
    assert call_list_query(CallLogQuery(1, "Docs", 3)) == CallListQuery(
        search="Docs", sort=ListSort.NEWEST, offset=100, limit=50
    )
    assert call_list_query(CallLogQuery(1, "docs/read", 1)) is None


@pytest.mark.parametrize("server_id", ["한글-서버", "s" * 65, " a b ", ""])
def test_approval_server_filter_reaches_b_query_unchanged(server_id: str) -> None:
    """A175: exact ID or None, independent of search."""
    assert server_id == "" or bridge_module.valid_server_id(server_id)
    query = ApprovalQuery(1, ApprovalLane.PENDING, "Task-01", server_id, ApprovalSort.OLDEST, 1)
    assert approval_list_query(query) == ApprovalListQuery(
        lane=ApprovalListLane.PENDING,
        search="Task-01",
        server_id=server_id or None,
        sort=ListSort.OLDEST,
        offset=0,
        limit=PAGE_SIZE,
    )


@pytest.mark.parametrize("server_id", ["a__b", "server_", "server\x00", "server\n", "a\u200bb"])
def test_approval_server_filter_refuses_b_invalid_id(server_id: str) -> None:
    """A175: malformed filters never become a backend query."""
    assert not bridge_module.valid_server_id(server_id)
    query = ApprovalQuery(1, ApprovalLane.PENDING, "", server_id, ApprovalSort.OLDEST, 1)
    assert approval_list_query(query) is None


def test_lane_runs_one_read_per_kind_and_keeps_only_the_newest_waiting() -> None:
    async def scenario() -> None:
        replies: list[QueryReply] = []
        seen: list[tuple[str, object]] = []
        release = asyncio.Event()

        class Held:
            async def list_approvals(self, query: ApprovalListQuery) -> ApprovalListPage:
                seen.append(("approvals", query.offset))
                await release.wait()
                return ApprovalListPage(rows=(), total_count=0, as_of_utc=NOW)

            async def list_calls(self, query: CallListQuery) -> CallListPage:
                seen.append(("calls", query.offset))
                return CallListPage(rows=(), total_count=0, as_of_utc=NOW)

            async def get_call(self, request_id: str) -> CallDetail | None:
                seen.append(("call", request_id))
                return None

        lane = QueryLane(replies.append, lambda _task: None)
        lane.open(Held())
        for token in (1, 2, 3):
            lane.submit("approvals", token, approvals_query(token * PAGE_SIZE))
        lane.submit("calls", 9, calls_query())
        await until(lambda: len(replies) == 1)
        assert (replies[0].kind, replies[0].token) == ("calls", 9)  # not behind approvals
        release.set()
        await until(lambda: len(replies) == 3)
        assert [(reply.kind, reply.token) for reply in replies] == [
            ("calls", 9),
            ("approvals", 1),
            ("approvals", 3),
        ]
        assert seen == [("approvals", PAGE_SIZE), ("calls", 0), ("approvals", 3 * PAGE_SIZE)]
        assert all(not reply.failed for reply in replies)

    asyncio.run(scenario())


def test_lane_answers_failed_for_errors_timeouts_wrong_requests_and_no_source() -> None:
    async def scenario() -> None:
        replies: list[QueryReply] = []
        consumed: list[asyncio.Future[object]] = []

        class Broken:
            async def list_approvals(self, query: ApprovalListQuery) -> ApprovalListPage:
                raise RuntimeError("C:/raw/store/path")

            async def list_calls(self, query: CallListQuery) -> CallListPage:
                await asyncio.sleep(10)
                raise AssertionError("must time out")

            async def get_call(self, request_id: str) -> CallDetail | None:
                return None

        lane = QueryLane(replies.append, consumed.append, timeout_s=0.05)
        lane.submit("call", 1, "request-001")  # no source yet
        lane.open(Broken())
        lane.submit("approvals", 2, approvals_query())
        lane.submit("calls", 3, calls_query())
        lane.submit("call", 4, approvals_query())  # the wrong request for the kind
        lane.submit("call", 5, "request-404")
        await until(lambda: len(replies) == 5)
        by_token = {reply.token: reply for reply in replies}
        assert [by_token[token].failed for token in (1, 2, 3, 4, 5)] == [
            True,
            True,
            True,
            True,
            False,
        ]
        assert all(reply.page is None for reply in replies)
        assert len(consumed) == 4  # every finished read is consumed, none left unretrieved

    asyncio.run(scenario())


def test_lane_close_cancels_running_reads_and_refuses_new_ones() -> None:
    async def scenario() -> None:
        replies: list[QueryReply] = []
        started = asyncio.Event()
        cleanup = asyncio.Event()
        cancelled: list[bool] = []

        class Slow:
            async def list_approvals(self, query: ApprovalListQuery) -> ApprovalListPage:
                started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise
                raise AssertionError("must be cancelled")

            async def list_calls(self, query: CallListQuery) -> CallListPage:
                started.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    await cleanup.wait()  # a read that takes long to end
                raise AssertionError("must be cancelled")

            async def get_call(self, request_id: str) -> CallDetail | None:
                return None

        lane = QueryLane(replies.append, lambda _task: None)
        lane.open(Slow())
        lane.submit("approvals", 1, approvals_query())
        lane.submit("approvals", 2, approvals_query())  # waiting; dropped by close
        await started.wait()
        assert await lane.close(1.0)
        assert cancelled == [True]
        lane.submit("approvals", 3, approvals_query())
        await asyncio.sleep(0.01)
        assert replies == []

        stuck = QueryLane(replies.append, lambda _task: None)
        started.clear()
        stuck.open(Slow())
        stuck.submit("calls", 1, calls_query())
        await started.wait()
        assert not await stuck.close(0.05)  # reported, so shutdown marks it unfinished
        cleanup.set()
        await asyncio.sleep(0.01)
        assert replies == []

    asyncio.run(scenario())


def test_bridge_refuses_malformed_queries_and_answers_failed_before_ready(app) -> None:
    async def unused() -> BackendBindings:
        raise AssertionError("must not bootstrap")

    bridge = BackendBridge(unused)
    answers: list[QueryReply] = []
    bridge.query_answered.connect(answers.append)
    try:
        for kind, token, request in (
            ("approvals", 1, calls_query()),
            ("calls", 1, approvals_query()),
            ("call", 1, ""),
            ("call", True, "request-001"),
            ("call", "1", "request-001"),
            ("tasks", 1, "request-001"),
        ):
            with pytest.raises(ValueError, match="QUERY_REQUEST_INVALID"):
                bridge.request_query(kind, token, request)
        assert answers == []
        bridge.request_query("call", 7, "request-001")
        assert [(reply.kind, reply.token, reply.failed) for reply in answers] == [("call", 7, True)]
    finally:
        bridge.close()


class FakeQueries:
    """Backend thread: B's StoreQueries shape with fixed pages."""

    def __init__(self) -> None:
        self.requests: list[object] = []
        self.fail_calls = threading.Event()

    async def list_approvals(self, query: ApprovalListQuery) -> ApprovalListPage:
        self.requests.append(query)
        rows = (approval_row(2), approval_row(1)) if query.lane is ApprovalListLane.PENDING else ()
        return ApprovalListPage(rows=rows, total_count=len(rows), as_of_utc=NOW)

    async def list_calls(self, query: CallListQuery) -> CallListPage:
        self.requests.append(query)
        if self.fail_calls.is_set():
            raise RuntimeError("C:/raw/store/path")
        rows = (call_row(1), call_row(2))
        return CallListPage(rows=rows, total_count=len(rows), as_of_utc=NOW)

    async def get_call(self, request_id: str) -> CallDetail | None:
        self.requests.append(request_id)
        return detail_of(call_row(1)) if request_id == "request-001" else None


def test_link_fills_both_pages_from_the_store_and_shows_failures_as_unknown(
    app: QApplication,
) -> None:
    queries = FakeQueries()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda _task_id: None, FakeControl(), queries=queries)

    bridge = BackendBridge(bootstrap)
    approvals = ApprovalListView()
    calls = CallLogPage()
    StoreQueryLink(bridge, approvals, calls, approvals)
    bridge.start()
    try:
        pump_until(
            app,
            lambda: (
                approvals.state is LoadState.READY
                and calls.count_label.text() == "전체 2건 중 이 페이지 2건 · 필터 후 2건 표시"
            ),
        )
        assert approvals.table.rowCount() == 2
        assert approvals.table.item(0, 0).text() == "approval-002"  # B's oldest-first order
        assert approvals.table.item(0, 3).text() == "partner-share"
        assert approvals_query() in queries.requests
        assert calls.table.rowCount() == 2
        assert not calls.pager.isHidden()

        # A reply for an older intent never replaces the current page.
        bridge.query_answered.emit(
            QueryReply("approvals", -1, ApprovalListPage(rows=(), total_count=0, as_of_utc=NOW))
        )
        assert approvals.table.rowCount() == 2

        # Text B would refuse is not sent; the screen says why.
        sent = len(queries.requests)
        approvals.search.setText("승인-1")
        approvals.search.editingFinished.emit()
        app.processEvents()
        assert approvals.state is LoadState.ERROR
        assert approvals.state_message.message.text() == QUERY_TEXT_INVALID
        assert len(queries.requests) == sent

        # A175: the real UI/link/bridge delivers the exact B query.
        approvals.search.clear()
        for server_id in ("한글-서버", "s" * 65, " a b ", ""):
            sent = len(queries.requests)
            approvals.server_filter.setText(server_id)
            approvals.server_filter.editingFinished.emit()
            pump_until(app, lambda: approvals.state is LoadState.READY)
            assert queries.requests[sent:] == [
                ApprovalListQuery(
                    lane=ApprovalListLane.PENDING,
                    server_id=server_id or None,
                    sort=ListSort.OLDEST,
                    offset=0,
                    limit=PAGE_SIZE,
                )
            ]
        for server_id in ("a__b", "server_", "server\n"):
            sent = len(queries.requests)
            approvals.server_filter.setText(server_id)
            approvals.server_filter.editingFinished.emit()
            app.processEvents()
            assert approvals.state is LoadState.ERROR
            assert len(queries.requests) == sent

        # The search is sent as typed: B matches a case-kept prefix.
        typed = CallListQuery(search="Request-0", sort=ListSort.NEWEST, offset=0, limit=50)
        calls.search.setText("Request-0")
        calls.search.editingFinished.emit()
        pump_until(app, lambda: typed in queries.requests)

        # A Store error is unknown, never an empty log.
        queries.fail_calls.set()
        calls.refresh()
        pump_until(app, lambda: calls.state_message.message.text() == QUERY_UNKNOWN)
        assert calls.table.rowCount() == 0
        assert "불러오지 못함" in calls.count_label.text()
        assert "0건" not in calls.count_label.text()

        queries.fail_calls.clear()
        calls.search.setText("")
        calls.search.editingFinished.emit()
        pump_until(app, lambda: calls.table.rowCount() == 2)
        for call_id, expected in (
            ("request-001", "결과 기록 · 성공 여부는 현재 실행 상태로 확인: 2026년"),
            ("request-002", "해당 호출 없음"),
        ):
            calls.select_call(call_id)
            dialog = calls.detail_panel
            assert dialog is not None
            pump_until(
                app,
                lambda dialog=dialog, expected=expected: expected in panel_text(dialog),
            )
            assert "신뢰 경계" not in panel_text(dialog)
            app.processEvents()

        # Leaving READY disconnects both pages instead of keeping old rows.
        close_bridge(app, bridge)
        assert bridge.state is BridgeState.CLOSED
        assert approvals.state is LoadState.UNAVAILABLE
        assert approvals.state_message.message.text() == SOURCE_NOT_CONNECTED
        assert calls.table.rowCount() == 0
        assert calls.pager.isHidden()
    finally:
        if not bridge.thread_done:
            close_bridge(app, bridge)
        approvals.close()
        calls.close()


def test_window_reads_both_pages_from_the_runtime_store(app: QApplication) -> None:
    queries = FakeQueries()

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda _task_id: None, FakeControl(), queries=queries)

    bridge = BackendBridge(bootstrap)
    shell = GuardWindow()
    shell.attach_runtime(RuntimePanel(bridge))
    bridge.start()
    try:
        pump_until(
            app,
            lambda: (
                shell.approval_page.table.rowCount() == 2 and shell.log_page.table.rowCount() == 2
            ),
        )
    finally:
        close_bridge(app, bridge)
        shell.close()


def test_bridge_close_reports_a_store_read_that_does_not_end(app, monkeypatch) -> None:
    # The unconfirmed close also holds the process start gate; keep it to this test.
    monkeypatch.setattr(bridge_module, "_PROCESS_START_GATE", bridge_module._ProcessStartGate())
    entered = threading.Event()

    class Stuck(FakeQueries):
        async def list_calls(self, query: CallListQuery) -> CallListPage:
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(10)  # ends only when the leftovers are cancelled
            raise AssertionError("must be cancelled")

    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda _task_id: None, FakeControl(), queries=Stuck())

    bridge = BackendBridge(bootstrap, shutdown_timeout=0.05)
    states: list[LifecycleEvent] = []
    bridge.lifecycle_changed.connect(states.append)
    bridge.start()
    try:
        pump_until(app, lambda: bridge.state is BridgeState.READY)
        bridge.request_query("calls", 1, calls_query())
        pump_until(app, entered.is_set)
    finally:
        close_bridge(app, bridge)
    assert states[-1] == LifecycleEvent(BridgeState.CLOSED, "CLEANUP_UNCONFIRMED")


def test_link_keeps_both_pages_unconnected_without_a_store_query_source(
    app: QApplication,
) -> None:
    async def bootstrap() -> BackendBindings:
        return BackendBindings(lambda _task_id: None, FakeControl())

    bridge = BackendBridge(bootstrap)
    approvals = ApprovalListView()
    calls = CallLogPage()
    StoreQueryLink(bridge, approvals, calls, approvals)
    sources: list[bool] = []
    bridge.query_source_changed.connect(sources.append)
    bridge.start()
    try:
        pump_until(app, lambda: sources == [False])
        assert approvals.state is LoadState.UNAVAILABLE
        assert approvals.state_message.message.text() == SOURCE_NOT_CONNECTED
        assert calls.pager.isHidden()
        assert not calls.search.isEnabled()
    finally:
        close_bridge(app, bridge)
        approvals.close()
        calls.close()
