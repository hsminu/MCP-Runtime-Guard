"""The approval list and call log on B's Store queries (B6-05, #86).

The bridge answers with B's pages unchanged; this module turns them into the
screens' public rows. Server, tool, Task and destination IDs stay untrusted
text that the screens escape. Nothing here is an authority for a command: a
command from the list sends the row's revision and B decides.

- The destination is shown by its ID only. Its tier and label are not stored
  with the request, so the current Profile's could misread an old row.
- The boundary tier at decision time is not stored either; no tier is shown.
- ``COMMITTED`` means the result was recorded; success is ``status`` only.
"""

from __future__ import annotations

from PySide6.QtCore import QObject

from guard_app.domain.enums import Decision
from guard_app.domain.queries import (
    ApprovalListLane,
    ApprovalListPage,
    ApprovalListQuery,
    ApprovalRow,
    CallDetail,
    CallListPage,
    CallListQuery,
    CallRow,
    ListSort,
)
from guard_app.services.bridge import BackendBridge, BridgeState, LifecycleEvent, QueryReply

from .approval_presentation import (
    ApprovalLane,
    ApprovalPage,
    ApprovalQuery,
    ApprovalSort,
    ApprovalSummary,
    valid_query_text,
)
from .approvals import ApprovalListPage as ApprovalListView
from .call_logs import (
    CallLogPage,
    CallLogQuery,
    CallLogResult,
    DetailRequest,
    PublicCallDetail,
    PublicCallStage,
    PublicCallSummary,
)
from .models import LoadState, Surface

_MAX_OFFSET = 10_000


def approval_list_query(query: ApprovalQuery) -> ApprovalListQuery | None:
    """B's query for one screen intent, or None when B would refuse it."""

    offset = (query.page - 1) * query.page_size
    if (
        not valid_query_text(query.search)
        or not valid_query_text(query.server_id)
        or not 0 <= offset <= _MAX_OFFSET
    ):
        return None
    try:
        return ApprovalListQuery(
            lane=(
                ApprovalListLane.PENDING
                if query.lane is ApprovalLane.PENDING
                else ApprovalListLane.HISTORY
            ),
            search=query.search,
            server_id=query.server_id or None,
            sort=ListSort.NEWEST if query.sort is ApprovalSort.NEWEST else ListSort.OLDEST,
            offset=offset,
            limit=query.page_size,
        )
    except ValueError:
        return None


def approval_summary(row: ApprovalRow) -> ApprovalSummary:
    return ApprovalSummary(
        approval_id=row.approval_id,
        task_id=row.task_id,
        request_id=row.request_id,
        server_id=row.server_id,
        destination_summary=row.destination_id or "",
        status=row.status,
        created_at=row.created_at_utc,
        resolved_at=row.decided_at_utc,
        reason_code=None if row.reason_code is None else row.reason_code.value,
        row_revision=row.row_revision,
        expires_at=row.expires_at_utc,
    )


def approval_page(query: ApprovalQuery, page: ApprovalListPage) -> ApprovalPage:
    return ApprovalPage(
        query, tuple(approval_summary(row) for row in page.rows), page.total_count, page.as_of_utc
    )


def call_list_query(query: CallLogQuery) -> CallListQuery | None:
    offset = (query.page - 1) * query.page_size
    if not valid_query_text(query.search) or not 0 <= offset <= _MAX_OFFSET:
        return None
    try:
        return CallListQuery(
            search=query.search, sort=ListSort.NEWEST, offset=offset, limit=query.page_size
        )
    except ValueError:
        return None


def call_summary(row: CallRow) -> PublicCallSummary:
    # A Store row has no Codex call ID; the request ID identifies the call.
    if row.approval_status is not None:
        approval: str | None = row.approval_status.value
    elif row.decision in (Decision.ALLOW, Decision.DENY):
        approval = "NOT_REQUIRED"
    else:
        approval = None
    reasons = tuple(reason.value for reason in row.reason_codes)
    return PublicCallSummary(
        call_id=row.request_id,
        request_id=row.request_id,
        task_id=row.task_id,
        server_id=row.server_id,
        tool_name=row.tool_name,
        kind=None if row.kind is None else row.kind.value,
        initial_decision=None if row.decision is None else row.decision.name,
        reason_code=reasons[0] if reasons else None,
        approval_status=approval,
        current_status=row.status.value,
        occurred_at=row.decided_at_utc,
        reason_codes=reasons,
    )


def call_result(query: CallLogQuery, page: CallListPage) -> CallLogResult:
    return CallLogResult(
        query, tuple(call_summary(row) for row in page.rows), page.total_count, page.as_of_utc
    )


def call_detail(detail: CallDetail) -> PublicCallDetail:
    return PublicCallDetail(
        call_summary(detail.row),
        tuple(
            PublicCallStage(stage.stage_code, "OCCURRED", stage.at_utc) for stage in detail.stages
        ),
    )


class StoreQueryLink(QObject):
    """Qt thread: sends the two pages' queries and applies only current answers."""

    def __init__(
        self,
        bridge: BackendBridge,
        approvals: ApprovalListView,
        calls: CallLogPage,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._approvals = approvals
        self._calls = calls
        self._available = False
        self._detail: DetailRequest | None = None
        bridge.query_source_changed.connect(self._source_changed)
        bridge.lifecycle_changed.connect(self._lifecycle_changed)
        bridge.query_answered.connect(self._answered)
        approvals.query_requested.connect(self._approval_query)
        calls.query_requested.connect(self._call_query)
        calls.detail_requested.connect(self._call_detail)

    def _source_changed(self, available: bool) -> None:
        self._set_available(available is True and self._bridge.state is BridgeState.READY)

    def _lifecycle_changed(self, event: LifecycleEvent) -> None:
        if event.state is not BridgeState.READY and self._available:
            self._set_available(False)

    def _set_available(self, available: bool) -> None:
        self._available = available
        self._approvals.set_source_connected(available)
        self._calls.set_source_connected(available)
        if available:
            self._approvals.refresh()
            self._calls.refresh()

    def _approval_query(self, query: object) -> None:
        if not self._available or type(query) is not ApprovalQuery:
            return
        request = approval_list_query(query)
        if request is None:
            self._approvals.set_error(query)
        else:
            self._bridge.request_query("approvals", query.token, request)

    def _call_query(self, query: object) -> None:
        if not self._available or type(query) is not CallLogQuery:
            return
        request = call_list_query(query)
        if request is None:
            self._calls.set_error(query)
        else:
            self._bridge.request_query("calls", query.token, request)

    def _call_detail(self, request: object) -> None:
        if type(request) is not DetailRequest:
            return
        self._detail = request
        if not self._available or not request.call_id:
            self._calls.set_detail(request, Surface(LoadState.UNAVAILABLE))
        else:
            self._bridge.request_query("call", request.token, request.call_id)

    def _answered(self, reply: object) -> None:
        if type(reply) is not QueryReply:
            return
        if reply.kind == "approvals":
            current = self._approvals.current_query
            if current is None or current.token != reply.token:
                return
            if reply.failed or type(reply.page) is not ApprovalListPage:
                self._approvals.set_error(current)
            else:
                self._approvals.set_page(approval_page(current, reply.page))
        elif reply.kind == "calls":
            calls = self._calls.current_query
            if calls is None or calls.token != reply.token:
                return
            if reply.failed or type(reply.page) is not CallListPage:
                self._calls.set_error(calls)
            else:
                self._calls.set_page(call_result(calls, reply.page))
        elif reply.kind == "call":
            request = self._detail
            if request is None or request.token != reply.token:
                return
            if reply.failed:
                self._calls.set_detail(request, Surface(LoadState.ERROR))
            elif type(reply.page) is CallDetail:
                detail = call_detail(reply.page)
                self._calls.set_detail(request, Surface(LoadState.READY, detail))
            else:
                # The Store has no row for this request ID.
                self._calls.set_detail(request, Surface(LoadState.READY))
