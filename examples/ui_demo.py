"""화면 시연: 백엔드 조회가 연결되지 않은 화면도 직접 눌러 볼 수 있는 데모.

The session's run, approval review and stop controls drive the real synthetic
Guard backend (``examples.synthetic_app``). The read-only management screens
have no backend query yet, so this demo answers them with fixed, clearly fake
rows: overview, MCP servers and their scan status, call logs and their stages,
the approval list, the policy rules and the Codex account state. Policy saves
and account logins only change these fake rows. Nothing here reaches a real
Registry, Store or Codex.

Run from the repository root::

    uv run python -m examples.ui_demo
"""

from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from guard_app.domain.enums import ApprovalStatus
from guard_app.services.bridge import BackendBindings, BackendBridge
from guard_app.services.codex_account import AccountView
from guard_app.services.policy_view import PolicyServer, PolicyTool, PolicyView
from guard_app.ui.approvals import (
    PAGE_SIZE,
    ApprovalLane,
    ApprovalPage,
    ApprovalQuery,
    ApprovalSort,
    ApprovalSummary,
)
from guard_app.ui.call_logs import (
    DetailRequest,
    PublicCallDetail,
    PublicCallStage,
    PublicCallSummary,
)
from guard_app.ui.models import (
    AttentionItem,
    FlowBucket,
    LoadState,
    OverviewSnapshot,
    OverviewTotals,
    RecentCall,
    ServerSummary,
    Surface,
)
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.scan_status import ScanStatusProjection
from guard_app.ui.window import GuardWindow

CONTEXT = "화면 시연 / 가짜 조회 데이터"
# Queries answer after a short pause so loading states are visible too.
REPLY_DELAY_MS = 250
DEVICE_LOGIN_MS = 4000

SERVERS = (
    ServerSummary("docs", "사내 문서", "INTERNAL", "internal", "active", "connected", "stdio"),
    ServerSummary(
        "mail",
        "외부 메일",
        "INTERNAL",
        "general_external",
        "active",
        "connected",
        "streamable_http",
    ),
    ServerSummary(
        "drive",
        "협력사 드라이브",
        "INTERNAL",
        "approved_external",
        "pending",
        "connecting",
        "streamable_http",
    ),
    ServerSummary("crm", "고객 관리", "CONFIDENTIAL", "internal", "blocked", "error", "stdio"),
    ServerSummary("wiki", "팀 위키", "PUBLIC", "internal", "active", "disconnected", "stdio"),
)

# (server, tool, kind, tier, decision, reason, approval, status, minutes ago)
_CALLS = (
    ("docs", "read", "read", "internal", "ALLOW", None, "NOT_REQUIRED", "SUCCEEDED", 4),
    (
        "mail",
        "send",
        "send",
        "general_external",
        "ASK",
        "INTERNAL_TO_EXTERNAL",
        "PENDING",
        "WAITING_APPROVAL",
        9,
    ),
    (
        "mail",
        "send",
        "send",
        "general_external",
        "ASK",
        "INTERNAL_TO_EXTERNAL",
        "APPROVED",
        "SUCCEEDED",
        18,
    ),
    ("crm", "read", "read", "internal", "DENY", "REG_NOT_ACTIVE", "NOT_REQUIRED", "DENIED", 25),
    (
        "drive",
        "send",
        "send",
        "approved_external",
        "ASK",
        "LABEL_EXCEEDS_BOUNDARY",
        "REJECTED",
        "DENIED",
        37,
    ),
    (
        "docs",
        "read",
        "read",
        "internal",
        "ALLOW",
        "RESULT_UNCLASSIFIED",
        "NOT_REQUIRED",
        "WITHHELD",
        46,
    ),
    (
        "mail",
        "send",
        "send",
        "general_external",
        "ASK",
        "CONFIDENTIAL_TO_EXTERNAL",
        "EXPIRED",
        "DENIED",
        58,
    ),
    (
        "wiki",
        "read",
        "read",
        "internal",
        "ALLOW",
        "POST_DISPATCH_ERROR",
        "NOT_REQUIRED",
        "FAILED",
        71,
    ),
    (
        "mail",
        "send",
        "send",
        "general_external",
        "ALLOW",
        None,
        "NOT_REQUIRED",
        "UNKNOWN_OUTCOME",
        83,
    ),
    ("docs", "send", "send", "internal", "DENY", "AGT_DENY", "NOT_REQUIRED", "DENIED", 96),
    ("drive", "read", "read", "approved_external", "ALLOW", None, "NOT_REQUIRED", "SUCCEEDED", 110),
    (
        "mail",
        "send",
        "send",
        "general_external",
        "ASK",
        "INTERNAL_TO_EXTERNAL",
        "PENDING",
        "WAITING_APPROVAL",
        124,
    ),
    ("docs", "read", "read", "internal", "ALLOW", None, "NOT_REQUIRED", "DISPATCHING", 2),
    (
        "crm",
        "send",
        "send",
        "internal",
        "DENY",
        "TOOL_FINGERPRINT_CHANGED",
        "NOT_REQUIRED",
        "DENIED",
        150,
    ),
    ("wiki", "read", "read", "internal", "ALLOW", None, "NOT_REQUIRED", "SUCCEEDED", 190),
    ("docs", "read", "read", "internal", "ALLOW", None, "NOT_REQUIRED", "SUCCEEDED", 240),
)

# (server, destination, status, reason, minutes ago, minutes to resolve)
_APPROVALS = (
    ("mail", "external · 일반 외부", ApprovalStatus.PENDING, "INTERNAL_TO_EXTERNAL", 9, None),
    ("mail", "external · 일반 외부", ApprovalStatus.PENDING, "INTERNAL_TO_EXTERNAL", 124, None),
    ("drive", "partner · 승인된 외부", ApprovalStatus.PENDING, "LABEL_EXCEEDS_BOUNDARY", 3, None),
    ("mail", "external · 일반 외부", ApprovalStatus.CONSUMED, "INTERNAL_TO_EXTERNAL", 18, 2),
    ("drive", "partner · 승인된 외부", ApprovalStatus.REJECTED, "LABEL_EXCEEDS_BOUNDARY", 37, 1),
    ("mail", "external · 일반 외부", ApprovalStatus.EXPIRED, "CONFIDENTIAL_TO_EXTERNAL", 58, 5),
    ("mail", "external · 일반 외부", ApprovalStatus.APPROVED, "INTERNAL_TO_EXTERNAL", 160, 3),
    ("docs", "archive · 내부", ApprovalStatus.CANCELLED, "POLICY_CHANGED", 210, 4),
    ("mail", "external · 일반 외부", ApprovalStatus.INVALIDATED, "APPROVAL_STALE", 260, 6),
    ("drive", "partner · 승인된 외부", ApprovalStatus.CONSUMED, "INTERNAL_TO_EXTERNAL", 300, 2),
    ("mail", "external · 일반 외부", ApprovalStatus.REJECTED, "INTERNAL_TO_EXTERNAL", 340, 1),
    ("mail", "external · 일반 외부", ApprovalStatus.EXPIRED, "INTERNAL_TO_EXTERNAL", 400, 5),
    ("drive", "partner · 승인된 외부", ApprovalStatus.CONSUMED, "INTERNAL_TO_EXTERNAL", 460, 2),
    ("mail", "external · 일반 외부", ApprovalStatus.REJECTED, "CONFIDENTIAL_TO_EXTERNAL", 520, 1),
)

_POLICY_SERVERS = (
    PolicyServer("docs", (PolicyTool("read", "read", True), PolicyTool("send", "send", True))),
    PolicyServer(
        "mail",
        (
            PolicyTool("send", "send", True),
            PolicyTool("list_folders", "read", True),
            PolicyTool("purge", "other", False),
        ),
    ),
    PolicyServer("drive", (PolicyTool("read", "read", True), PolicyTool("send", "send", True))),
    PolicyServer("wiki", (PolicyTool("read", "read", True),)),
)


def calls(now: datetime) -> tuple[PublicCallSummary, ...]:
    rows = []
    for index, (server, tool, kind, tier, decision, reason, approval, status, ago) in enumerate(
        _CALLS, 1
    ):
        rows.append(
            PublicCallSummary(
                call_id=f"call-{index:04d}",
                request_id=f"request-{index:04d}",
                task_id="task-demo-a" if index % 2 else "task-demo-b",
                server_id=server,
                tool_name=tool,
                kind=kind,
                boundary_tier=tier,
                initial_decision=decision,
                reason_code=reason,
                approval_status=approval,
                current_status=status,
                occurred_at=now - timedelta(minutes=ago),
            )
        )
    return tuple(sorted(rows, key=lambda row: row.occurred_at or now, reverse=True))


def call_stages(call: PublicCallSummary) -> tuple[PublicCallStage, ...]:
    """Stages a call of this status would have passed; later ones never ran."""
    start = call.occurred_at
    reached = {
        "DENIED": 1,
        "WAITING_APPROVAL": 2,
        "DISPATCHING": 3,
        "UNKNOWN_OUTCOME": 4,
        "FAILED": 4,
        "WITHHELD": 6,
        "SUCCEEDED": 7,
    }.get(call.current_status or "", 1)
    codes = ["DECIDED", "WAITING_APPROVAL", "DISPATCHING", "DISPATCHED", "OBSERVED", "COMMITTED"]
    codes.append("WITHHELD" if call.current_status == "WITHHELD" else "RELEASED")
    if call.initial_decision != "ASK":
        codes.remove("WAITING_APPROVAL")
        reached -= 1 if reached > 1 else 0
    stages = []
    for index, code in enumerate(codes):
        if index < reached and start is not None:
            stages.append(PublicCallStage(code, "OCCURRED", start + timedelta(seconds=index * 3)))
        else:
            stages.append(PublicCallStage(code, "NOT_RUN"))
    return tuple(stages)


def approvals(now: datetime) -> tuple[ApprovalSummary, ...]:
    rows = []
    for index, (server, destination, status, reason, ago, resolve) in enumerate(_APPROVALS, 1):
        created = now - timedelta(minutes=ago)
        rows.append(
            ApprovalSummary(
                approval_id=f"approval-demo-{index:02d}",
                task_id="task-demo-a" if index % 2 else "task-demo-b",
                request_id=f"request-demo-{index:02d}",
                server_id=server,
                destination_summary=destination,
                status=status,
                created_at=created,
                resolved_at=None if resolve is None else created + timedelta(minutes=resolve),
                reason_code=reason,
                row_revision=1,
            )
        )
    return tuple(rows)


def approval_page(query: ApprovalQuery, rows: tuple[ApprovalSummary, ...]) -> ApprovalPage:
    """Filter, sort and page the fake rows the way the backend query will."""
    pending = query.lane is ApprovalLane.PENDING
    matched = [row for row in rows if (row.status is ApprovalStatus.PENDING) == pending]
    needle = query.search.casefold()
    if needle:
        matched = [
            row
            for row in matched
            if needle
            in " ".join(
                (
                    row.approval_id,
                    row.request_id,
                    row.task_id,
                    row.server_id,
                    row.destination_summary,
                )
            ).casefold()
        ]
    if query.server_id:
        matched = [row for row in matched if row.server_id.casefold() == query.server_id.casefold()]
    matched.sort(
        key=lambda row: (row.created_at, row.approval_id),
        reverse=query.sort is ApprovalSort.NEWEST,
    )
    start = (query.page - 1) * PAGE_SIZE
    return ApprovalPage(
        query, tuple(matched[start : start + PAGE_SIZE]), len(matched), as_of=datetime.now(UTC)
    )


def scans(now: datetime) -> dict[str, ScanStatusProjection]:
    both = ("yara", "llm")
    return {
        "docs": ScanStatusProjection(
            "active",
            "connected",
            "completed",
            both,
            both,
            (),
            (),
            True,
            False,
            "1.4.2",
            now - timedelta(hours=2),
            "LOW",
            "설명문에 외부 링크 1건 · 위험도 낮음",
        ),
        "mail": ScanStatusProjection(
            "active",
            "connected",
            "completed",
            both,
            both,
            (),
            (),
            True,
            False,
            "1.4.2",
            now - timedelta(hours=5),
            "MEDIUM",
            "send 도구 설명에 수신처 지정 지시문 있음",
        ),
        "drive": ScanStatusProjection(
            "pending",
            "connecting",
            "running",
            both,
            ("yara",),
            (),
            (),
            True,
            None,
            "1.4.2",
        ),
        "crm": ScanStatusProjection(
            "blocked",
            "error",
            "failed",
            both,
            ("yara",),
            (),
            ("llm",),
            True,
            True,
            "1.4.2",
            now - timedelta(days=1),
            "HIGH",
            "도구 지문이 바뀌어 재검사 필요",
        ),
        "wiki": ScanStatusProjection("active", "disconnected", "not_started"),
    }


def overview(now: datetime, call_rows: tuple[PublicCallSummary, ...]) -> OverviewSnapshot:
    pending = sum(1 for row in _APPROVALS if row[2] is ApprovalStatus.PENDING)
    blocked = sum(1 for row in call_rows if row.current_status == "DENIED")
    active = sum(1 for server in SERVERS if server.registry_status == "active")
    attention = (
        AttentionItem(
            "attention-approval-1",
            "approval",
            "INTERNAL_TO_EXTERNAL",
            "mail",
            "task-demo-a",
            "request-demo-01",
            waiting_since=now - timedelta(minutes=9),
            target_page="approvals",
            target_id="approval-demo-01",
            action_available=True,
        ),
        AttentionItem(
            "attention-approval-2",
            "approval",
            "LABEL_EXCEEDS_BOUNDARY",
            "drive",
            "task-demo-a",
            "request-demo-03",
            waiting_since=now - timedelta(minutes=3),
            target_page="approvals",
            target_id="approval-demo-03",
            action_available=True,
        ),
        AttentionItem(
            "attention-unknown-1",
            "outcome_unknown",
            None,
            "mail",
            "task-demo-a",
            "request-0009",
            occurred_at=now - timedelta(minutes=83),
            target_page="logs",
            target_id="call-0009",
        ),
        AttentionItem(
            "attention-blocked-1",
            "blocked",
            "REG_NOT_ACTIVE",
            "crm",
            "task-demo-b",
            "request-0004",
            occurred_at=now - timedelta(minutes=25),
            target_page="logs",
            target_id="call-0004",
        ),
        AttentionItem(
            "attention-fingerprint-1",
            "fingerprint_changed",
            "TOOL_FINGERPRINT_CHANGED",
            "crm",
            occurred_at=now - timedelta(days=1),
            target_page="servers",
            target_id="crm",
        ),
        AttentionItem(
            "attention-registration-1",
            "registration",
            None,
            "drive",
            occurred_at=now - timedelta(hours=3),
            target_page="servers",
            target_id="drive",
        ),
    )
    recent = tuple(
        RecentCall(
            row.call_id,
            row.server_id or "",
            row.tool_name or "",
            row.occurred_at,
            row.initial_decision,
            row.current_status,
        )
        for row in call_rows[:8]
    )
    hour = now.replace(minute=0, second=0, microsecond=0)
    flow = tuple(
        FlowBucket(hour - timedelta(hours=11 - index), amount)
        for index, amount in enumerate((2, 0, 1, 4, 3, 6, 2, 5, 8, 3, 4, 2))
    )
    return OverviewSnapshot(
        totals=Surface(
            LoadState.READY,
            OverviewTotals(active, pending, blocked, now - timedelta(hours=24), now),
            now,
        ),
        servers=Surface(LoadState.READY, SERVERS, now),
        attention=Surface(LoadState.READY, attention, now),
        recent_calls=Surface(LoadState.READY, recent, now),
        flow=Surface(LoadState.READY, flow, now),
    )


class DemoData:
    """Answers the window's read-only queries with the fake rows above."""

    def __init__(self, window: GuardWindow, bridge: BackendBridge) -> None:
        self.window = window
        now = datetime.now(UTC)
        self.calls = calls(now)
        self.approvals = approvals(now)
        self.scans = scans(now)
        self.policy = PolicyView(
            profile_id="demo-profile",
            policy_version="demo-v1",
            approval_ttl_seconds=900,
            taint_mode="sticky",
            allow=(("docs", "read"), ("docs", "send"), ("mail", "list_folders"), ("wiki", "read")),
            deny=(("mail", "send"),),
            servers=_POLICY_SERVERS,
            change_available=True,
            codex_excluded=(("crm__export.all", "TOOL_NAME"),),
        )
        self.account = AccountView("SIGNED_OUT")
        self._commands = count(1)
        # The synthetic backend has no policy or account; it would reset these
        # screens at startup, so the demo answers them alone.
        bridge.policy_changed.disconnect()
        bridge.account_changed.disconnect()
        login = window.codex_login_info
        for signal in (
            login.read_requested,
            login.device_login_requested,
            login.cancel_requested,
            login.logout_requested,
            window.policy_page.allow_change_requested,
        ):
            signal.disconnect()
        login.read_requested.connect(lambda: self._account(self.account))
        login.device_login_requested.connect(self._device_login)
        login.cancel_requested.connect(
            lambda _id: self._account(AccountView("LOGIN_NOT_COMPLETED"))
        )
        login.logout_requested.connect(lambda: self._account(AccountView("SIGNED_OUT")))
        login.set_api_key_sender(lambda _key: self._account(AccountView("API_KEY")))
        window.policy_page.allow_change_requested.connect(self._policy_change)
        window.approval_query_requested.connect(self._approval_query)
        window.call_log_detail_requested.connect(self._call_detail)
        window.scan_snapshot_requested.connect(self._scan)

    def fill(self) -> None:
        now = datetime.now(UTC)
        window = self.window
        window.set_overview_snapshot(overview(now, self.calls))
        window.set_server_snapshot(Surface(LoadState.READY, SERVERS, now))
        window.set_call_log_snapshot(Surface(LoadState.READY, self.calls, now))
        window.policy_page.set_view(self.policy)
        window.approval_page.set_approval_ttl_seconds(self.policy.approval_ttl_seconds)
        window.codex_login_info.set_view(self.account)
        window.set_approval_query_connected(True)
        window.approval_page.refresh()

    def _later(self, callback: Callable[[], object]) -> None:
        QTimer.singleShot(REPLY_DELAY_MS, callback)

    def _approval_query(self, query: ApprovalQuery) -> None:
        self._later(lambda: self.window.set_approval_page(approval_page(query, self.approvals)))

    def _call_detail(self, request: DetailRequest) -> None:
        call = next((row for row in self.calls if row.call_id == request.call_id), None)
        if call is None:
            surface = Surface[PublicCallDetail](LoadState.ERROR)
        else:
            detail = PublicCallDetail(call, call_stages(call))
            surface = Surface(LoadState.READY, detail, datetime.now(UTC))
        self._later(lambda: self.window.set_call_log_detail(request, surface))

    def _scan(self, server_id: str, token: int) -> None:
        scan = self.scans.get(server_id)
        surface = (
            Surface[ScanStatusProjection](LoadState.ERROR)
            if scan is None
            else Surface(LoadState.READY, scan, datetime.now(UTC))
        )
        self._later(lambda: self.window.set_scan_snapshot(server_id, token, surface))

    def _account(self, view: AccountView) -> None:
        self.account = view
        self.window.codex_login_info.set_view(view)

    def _device_login(self) -> None:
        self._account(
            AccountView(
                "PENDING_DEVICE",
                login_id="demo-login",
                verification_url="https://auth.example.invalid/device",
                user_code="DEMO-1234",
            )
        )

        def finish() -> None:
            if self.account.state == "PENDING_DEVICE":
                self._account(AccountView("CHATGPT", plan="Plus"))

        QTimer.singleShot(DEVICE_LOGIN_MS, finish)

    def _policy_change(
        self, server_id: str, allowed: tuple[str, ...], denied: tuple[str, ...]
    ) -> None:
        page = self.window.policy_page
        command = f"demo-policy-{next(self._commands)}"
        page.set_change_pending(command)
        others_allow = tuple(pair for pair in self.policy.allow if pair[0] != server_id)
        others_deny = tuple(pair for pair in self.policy.deny if pair[0] != server_id)
        self.policy = replace(
            self.policy,
            allow=others_allow + tuple((server_id, tool) for tool in allowed),
            deny=others_deny + tuple((server_id, tool) for tool in denied),
        )

        def settle() -> None:
            page.set_view(self.policy)
            page.settle_change(command, "POLICY_CHANGED")

        self._later(settle)


def synthetic_backend() -> Awaitable[BackendBindings]:
    from examples.synthetic_app import bootstrap

    return bootstrap("ask")


def build(bridge: BackendBridge, window: GuardWindow) -> DemoData:
    """Wire the window like the app does, then let the demo answer its queries."""
    window.attach_runtime(RuntimePanel(bridge))
    window.set_runtime_context(CONTEXT)
    data = DemoData(window, bridge)
    data.fill()
    window.navigate("runs")
    return data


def main() -> int:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    bridge: BackendBridge = BackendBridge(synthetic_backend)
    closing_since: list[float] = []
    close_limit_s = bridge.shutdown_timeout + 2.0

    class DemoWindow(GuardWindow):
        @override
        def closeEvent(self, event: QCloseEvent) -> None:
            self.clear_session_content()
            if not closing_since:
                closing_since.append(time.monotonic())
            bridge.close()
            self.hide()
            event.ignore()

    window = DemoWindow()
    build(bridge, window)

    def quit_when_closed() -> None:
        if window.isVisible():
            return
        overdue = bool(closing_since) and time.monotonic() - closing_since[0] > close_limit_s
        if bridge.thread_done or overdue:
            app.quit()

    timer = QTimer()
    timer.setInterval(25)
    timer.timeout.connect(quit_when_closed)
    timer.start()
    app.aboutToQuit.connect(bridge.close)
    window.show()
    bridge.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
