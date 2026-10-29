"""A178: Qt ownership/lifetime regression; I9 diagnostics, no direct T/S ID.

Inspect existing wrappers only: QObject child enumeration can mask the defect.
"""

from __future__ import annotations

import gc
import os
import threading
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from guard_app.domain.enums import TaskStatus
from guard_app.services.bridge import (
    BackendBridge,
    RecoveryReport,
    RegistrationView,
    ToolSettingsRow,
)
from guard_app.ui.approval_detail import ApprovalDetailDialog, ApprovalDetailValue
from guard_app.ui.destinations import DestinationDialog
from guard_app.ui.models import (
    AttentionItem,
    LoadState,
    OverviewSnapshot,
    OverviewTotals,
    RecentCall,
    ServerSummary,
    Surface,
)
from guard_app.ui.overview import OverviewPage
from guard_app.ui.overview_rows import AttentionRow, MetricRow, RecentCallRow, ServerRow
from guard_app.ui.policy import PolicyPage, ToolAllowDialog
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.scan_report import ScanReportPanel
from guard_app.ui.scanner_key import ScannerKeyForm
from guard_app.ui.server_add import LocalFolderRulesForm, ServerAddForm
from guard_app.ui.server_add_folders import FileServerFolders
from guard_app.ui.servers import ServerDetailDialog
from guard_app.ui.sessions import SessionWorkspace
from guard_app.ui.task_status import TaskStatusCard
from guard_app.ui.tool_settings import ToolSettingsDialog
from guard_app.ui.untrusted_text import UntrustedTextView
from guard_app.ui.widgets import PlainDetailDialog
from guard_app.ui.window import GuardWindow

from .test_ui_policy import view as policy_view


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def roots(app):
    widgets = []
    yield widgets
    assert threading.current_thread() is threading.main_thread()
    for widget in widgets:
        if shiboken6.isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    widgets.clear()
    gc.collect()


def double_owned(roots: list[QWidget]) -> list[tuple[str, str, str]]:
    """Scope diagnostics to these roots without creating child wrappers."""
    found = []
    for obj in gc.get_objects():
        if not isinstance(obj, QObject) or not shiboken6.isValid(obj):
            continue
        if not shiboken6.ownedByPython(obj):
            continue
        parent = obj.parent()
        ancestor = parent
        while ancestor is not None:
            if any(ancestor is root for root in roots):
                found.append((type(obj).__name__, type(parent).__name__, obj.objectName()))
                break
            ancestor = ancestor.parent()
    return sorted(found)


def assert_qt_owned(roots):
    failures = double_owned(roots)
    assert not failures, "\n".join(f"{kind} {parent} {name!r}" for kind, parent, name in failures)


def test_detector_distinguishes_orphan_order_and_scopes_roots(roots):
    bad = QWidget()
    roots.append(bad)
    outer = QVBoxLayout(bad)
    orphan = QHBoxLayout()
    label = QLabel()
    orphan.addWidget(label)
    outer.addLayout(orphan)
    assert double_owned([bad]) == [("QLabel", "QWidget", "")]

    good = QWidget()
    roots.append(good)
    outer = QVBoxLayout(good)
    attached = QHBoxLayout()
    outer.addLayout(attached)
    attached.addWidget(QLabel())
    assert double_owned([good]) == []
    assert double_owned(roots) == [("QLabel", "QWidget", "")]


def test_window_without_backend(roots):
    roots.append(GuardWindow())
    assert_qt_owned(roots)


def test_populated_overview(roots):
    page = OverviewPage()
    roots.append(page)
    page.set_snapshot(
        OverviewSnapshot(
            totals=Surface(LoadState.READY, OverviewTotals(1, 1, 1)),
            servers=Surface(
                LoadState.READY,
                (ServerSummary("server", "server", session_status="connected"),),
            ),
            attention=Surface(LoadState.READY, (AttentionItem("item", "approval"),)),
            recent_calls=Surface(LoadState.READY, (RecentCall("call", "server", "tool"),)),
        )
    )
    # Check ownership first: a Python itemAt() call re-runs PySide's layout
    # ownership hook and could hide the defect. Never enumerate QObject children.
    assert_qt_owned(roots)
    assert len(page.metric_rows) == 3
    assert all(isinstance(row, MetricRow) for row in page.metric_rows)
    assert len(page._server_rows) == 1
    assert isinstance(page._server_rows[0], ServerRow)
    assert page.attention_layout.count() == 1
    assert isinstance(page.attention_layout.itemAt(0).widget(), AttentionRow)
    assert page.calls_layout.count() == 1
    assert isinstance(page.calls_layout.itemAt(0).widget(), RecentCallRow)


def test_server_and_folder_forms_with_rows(roots):
    form = ServerAddForm()
    roots.append(form)
    form.file_folders.add_rule()
    local = LocalFolderRulesForm()
    roots.append(local)
    folders = FileServerFolders()
    roots.append(folders)
    folders.add_rule()
    assert len(form.file_folders.rows) >= 1
    assert len(local.rows) >= 1
    assert len(folders.rows) >= 1
    assert_qt_owned(roots)


def test_detail_dialogs(roots):
    approval = ApprovalDetailDialog()
    roots.append(approval)
    approval.show_value(ApprovalDetailValue("destination", b"{}"))
    roots.append(ServerDetailDialog(ServerSummary("server", "server")))
    tools = ToolSettingsDialog(
        RegistrationView(
            "TOOL_SETTINGS_PREVIEW",
            server_id="server",
            tool_settings=(
                ToolSettingsRow("tool", "other", "read", None, "INTERNAL", ("to",), False),
            ),
        )
    )
    roots.append(tools)
    assert len(tools.controls) == 1
    roots.append(DestinationDialog(None, ""))
    view = policy_view(change=True)
    roots.append(ToolAllowDialog(view.servers[0], view))
    detail = PlainDetailDialog()
    roots.append(detail)
    detail.show_detail(title="detail", text="synthetic")
    assert_qt_owned(roots)


def test_runtime_with_visible_recovery(roots):
    async def unused():
        raise AssertionError("unexpected bootstrap")

    bridge = BackendBridge(unused)
    try:
        panel = RuntimePanel(bridge)
        roots.append(panel)
        panel.recovery.show_report(
            RecoveryReport(
                schema_version=3,
                recovery_version=1,
                run_id="run",
                committed_at_utc=datetime(2026, 10, 30, tzinfo=UTC),
                changed_task_count=1,
                changed_request_count=1,
                changed_approval_count=1,
                unknown_outcome_count=1,
                quarantined_count=1,
                unresolved_count=0,
            )
        )
        assert panel.recovery.isVisibleTo(panel)
        assert_qt_owned(roots)
    finally:
        bridge.close()
        bridge.deleteLater()


def test_cards_and_populated_policy(roots):
    scan = ScanReportPanel()
    roots.append(scan)
    scan.set_state(None)
    roots.append(TaskStatusCard(TaskStatus.UNKNOWN_OUTCOME))
    roots.append(ScannerKeyForm())
    untrusted = UntrustedTextView("synthetic", show_unverified_notice=True)
    roots.append(untrusted)
    untrusted.set_text("synthetic")
    policy = PolicyPage()
    roots.append(policy)
    policy.set_view(policy_view(change=True))
    assert len(policy.server_buttons) == 1
    assert_qt_owned(roots)


def test_workspace_with_two_sessions(roots):
    workspace = SessionWorkspace()
    # The shell normally owns the two sidebar controls separately.
    roots.extend((workspace, workspace.search, workspace.list))
    workspace.new_session()
    assert len(workspace.sessions) == 2
    assert_qt_owned(roots)
