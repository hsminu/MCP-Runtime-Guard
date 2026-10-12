"""Qt shell with navigation and backend-confirmed overview rendering."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from guard_app.domain.enums import TaskStatus

from .approvals import ApprovalListPage, ApprovalPage, ApprovalQuery
from .call_logs import CallLogPage, DetailRequest, PublicCallDetail, PublicCallSummary
from .models import (
    OverviewSnapshot,
    RuntimeIdentity,
    RuntimeMode,
    ServerSummary,
    Surface,
)
from .overview import OverviewPage
from .presentation import PAGES
from .scan_status import ScanStatusProjection
from .server_add import ServerAddForm
from .servers import ServerListPage
from .style import apply_style
from .task_status import TaskStatusCard
from .widgets import KeyboardActionButton, plain_label


def _scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    return scroll


def _placeholder_page(page: str) -> QWidget:
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(30, 30, 30, 30)
    layout.setSpacing(10)
    layout.addWidget(plain_label(PAGES[page], name="pageTitle"))
    note = plain_label(
        "백엔드 확정 조회 계약을 연결한 뒤 표시할 화면이야. 현황으로 돌아가면 현재 제공된 상태를 볼 수 있어.",
        name="secondary",
    )
    layout.addWidget(note)
    layout.addStretch(1)
    return content


class GuardWindow(QMainWindow):
    """Provides view navigation; backend commands belong to the bridge."""

    navigation_requested = Signal(str, str)
    approval_query_requested = Signal(object)
    call_log_detail_requested = Signal(object)
    scan_snapshot_requested = Signal(str, int)

    _COMPACT_NAV_WIDTH = 900
    _COMPACT_NAV_HEIGHT = 700

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MCP Runtime Guard")
        self.setMinimumSize(320, 360)
        available = self.screen().availableGeometry()
        title_height = self.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight)
        frame_allowance = 2 * self.style().pixelMetric(
            QStyle.PixelMetric.PM_MdiSubWindowFrameWidth
        )
        usable_width = max(1, available.width() - frame_allowance)
        usable_height = max(1, available.height() - title_height - frame_allowance)
        self.resize(
            min(1260, usable_width),
            min(820, usable_height),
        )
        self._page = "overview"
        self._nav_buttons: dict[str, QPushButton] = {}
        self._menu_actions: dict[str, QAction] = {}
        self._page_indices: dict[str, int] = {}
        self._compact_navigation = False

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(224)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 24, 14, 16)
        side.setSpacing(5)
        side.addWidget(plain_label("MCP Guard", name="sectionTitle"))
        side.addWidget(plain_label("RUNTIME CONSOLE", name="eyebrow"))
        side.addSpacing(22)
        side.addWidget(plain_label("운영", name="secondary"))
        for page in ("overview", "logs", "approvals", "servers"):
            side.addWidget(self._nav_button(page))
        side.addSpacing(15)
        side.addWidget(plain_label("관리", name="secondary"))
        for page in ("server_add", "policy", "audit"):
            side.addWidget(self._nav_button(page))
        side.addStretch(1)
        for page in ("runs", "settings"):
            side.addWidget(self._nav_button(page))
        side.addWidget(plain_label("백엔드 확정 조회만 표시", name="secondary"))
        root.addWidget(self.sidebar)
        # Start with the compact shell so a 320 px resize cannot be constrained
        # by the sidebar before the first resize event has reflowed the layout.
        self.sidebar.hide()

        workspace = QWidget()
        workspace.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        work = QVBoxLayout(workspace)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        top = QGridLayout(topbar)
        top.setContentsMargins(26, 10, 26, 10)
        top.setHorizontalSpacing(12)
        top.setVerticalSpacing(4)
        self._top_layout = top
        self.menu_button = KeyboardActionButton("메뉴")
        self.menu_button.setAccessibleName("화면 이동 메뉴")
        self.menu_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        self._nav_menu = QMenu(self.menu_button)
        for page in PAGES:
            action = self._nav_menu.addAction(PAGES[page])
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, key=page: self.navigate(key))
            self._menu_actions[page] = action
        self.menu_button.setMenu(self._nav_menu)
        self.menu_button.hide()
        self.breadcrumb = plain_label("Workspace / 현황", name="cardTitle", wrap=False)
        top.addWidget(self.breadcrumb, 0, 0)
        self.runtime_identity = plain_label(name="secondary", wrap=False)
        top.addWidget(self.runtime_identity, 0, 1)
        top.setColumnStretch(0, 1)
        self.set_runtime_identity(RuntimeIdentity())
        work.addWidget(topbar)

        self.stack = QStackedWidget()
        self.overview = OverviewPage()
        self.overview.navigate_requested.connect(self.navigate)
        self._page_indices["overview"] = self.stack.addWidget(
            _scroll_page(self.overview)
        )
        self.server_page = ServerListPage()
        self.server_page.scan_snapshot_requested.connect(
            self.scan_snapshot_requested.emit
        )
        self._page_indices["servers"] = self.stack.addWidget(
            _scroll_page(self.server_page)
        )
        self.log_page = CallLogPage()
        self.log_page.detail_requested.connect(self.call_log_detail_requested.emit)
        self._page_indices["logs"] = self.stack.addWidget(_scroll_page(self.log_page))
        self.approval_page = ApprovalListPage()
        self.approval_page.query_requested.connect(self.approval_query_requested.emit)
        self._page_indices["approvals"] = self.stack.addWidget(
            _scroll_page(self.approval_page)
        )
        self.server_add_form = ServerAddForm()
        self._page_indices["server_add"] = self.stack.addWidget(self.server_add_form)
        task_page = QWidget()
        task_layout = QVBoxLayout(task_page)
        task_layout.setContentsMargins(26, 28, 26, 42)
        task_layout.setSpacing(16)
        task_layout.addWidget(plain_label("에이전트 실행", name="pageTitle"))
        task_layout.addWidget(
            plain_label("Task 상태는 백엔드에서 확인된 값만 표시해.", name="secondary")
        )
        self.task_status_card = TaskStatusCard()
        task_layout.addWidget(self.task_status_card)
        task_layout.addStretch(1)
        self._page_indices["runs"] = self.stack.addWidget(_scroll_page(task_page))
        for page in PAGES:
            if page not in (
                "overview",
                "servers",
                "logs",
                "approvals",
                "server_add",
                "runs",
            ):
                self._page_indices[page] = self.stack.addWidget(
                    _scroll_page(_placeholder_page(page))
                )
        work.addWidget(self.stack, 1)
        root.addWidget(workspace, 1)
        apply_style(self)
        self._update_shell_layout()
        self.navigate("overview", "")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "menu_button"):
            self._update_shell_layout()

    def _update_shell_layout(self) -> None:
        compact = (
            self.width() < self._COMPACT_NAV_WIDTH
            or self.height() < self._COMPACT_NAV_HEIGHT
        )
        if compact == self._compact_navigation and self.sidebar.isHidden() == compact:
            return
        self._compact_navigation = compact
        self.sidebar.setVisible(not compact)
        self.menu_button.setVisible(compact)
        self._top_layout.removeWidget(self.breadcrumb)
        self._top_layout.removeWidget(self.runtime_identity)
        if compact:
            self._top_layout.setContentsMargins(12, 8, 12, 8)
            self._top_layout.addWidget(self.menu_button, 0, 0)
            self._top_layout.addWidget(self.breadcrumb, 0, 1)
            self._top_layout.addWidget(self.runtime_identity, 1, 0, 1, 2)
            self._top_layout.setColumnStretch(0, 0)
            self._top_layout.setColumnStretch(1, 1)
            self.breadcrumb.setWordWrap(True)
            self.runtime_identity.setWordWrap(True)
        else:
            self._top_layout.removeWidget(self.menu_button)
            self._top_layout.setContentsMargins(26, 10, 26, 10)
            self._top_layout.addWidget(self.breadcrumb, 0, 0)
            self._top_layout.addWidget(self.runtime_identity, 0, 1)
            self._top_layout.setColumnStretch(0, 1)
            self._top_layout.setColumnStretch(1, 0)
            self.breadcrumb.setWordWrap(False)
            self.runtime_identity.setWordWrap(False)

    def _nav_button(self, page: str) -> QPushButton:
        button = KeyboardActionButton(PAGES[page])
        button.setObjectName("navButton")
        button.setAccessibleName(f"{PAGES[page]} 화면")
        button.clicked.connect(lambda: self.navigate(page, ""))
        self._nav_buttons[page] = button
        return button

    def set_overview_snapshot(self, snapshot: OverviewSnapshot) -> None:
        """Called on the Qt thread with a bridge-approved public projection."""

        self.overview.set_snapshot(snapshot)

    def set_server_snapshot(self, snapshot: Surface[tuple[ServerSummary, ...]]) -> None:
        """Update Registry rows from their own query, never overview rows."""

        self.server_page.set_servers(snapshot)

    def set_scan_snapshot(
        self, server_id: str, token: int, snapshot: Surface[ScanStatusProjection]
    ) -> bool:
        """Show only the current server detail's confirmed public scan query."""

        return self.server_page.set_scan_snapshot(server_id, token, snapshot)

    def set_approval_page(self, page: ApprovalPage) -> bool:
        """Accept only the current approval query response."""

        return self.approval_page.set_page(page)

    def set_approval_error(self, query: ApprovalQuery) -> bool:
        return self.approval_page.set_error(query)

    def set_call_log_snapshot(
        self, snapshot: Surface[tuple[PublicCallSummary, ...]]
    ) -> None:
        self.log_page.set_calls(snapshot)

    def set_call_log_detail(
        self, request: DetailRequest, snapshot: Surface[PublicCallDetail]
    ) -> None:
        self.log_page.set_detail(request, snapshot)

    def set_runtime_identity(self, identity: RuntimeIdentity) -> None:
        """Show only modes explicitly confirmed by trusted app bootstrap."""

        labels = {
            RuntimeMode.UNCONFIGURED: "미설정",
            RuntimeMode.MOCK: "mock",
            RuntimeMode.REAL: "real",
        }
        self.runtime_identity.setText(
            f"AGT: {labels.get(identity.agt_mode, '미확인')} · "
            f"모델: {labels.get(identity.model_mode, '미확인')}"
        )

    def set_task_status(self, status: TaskStatus | None) -> None:
        """Render a backend-confirmed status; no Task control is inferred here."""

        self.task_status_card.set_status(status)

    def navigate(self, page: str, target_id: str = "") -> None:
        if page not in self._page_indices:
            return
        self._page = page
        self.stack.setCurrentIndex(self._page_indices[page])
        if page == "servers" and target_id:
            self.server_page.select_server(target_id)
        elif page == "logs" and target_id:
            self.log_page.select_call(target_id)
        elif page == "approvals" and target_id:
            self.approval_page.select_approval(target_id)
        self.breadcrumb.setText(f"Workspace / {PAGES[page]}")
        for key, button in self._nav_buttons.items():
            button.setProperty("selected", key == page)
            button.style().unpolish(button)
            button.style().polish(button)
        for key, action in self._menu_actions.items():
            action.setChecked(key == page)
        self.navigation_requested.emit(page, target_id)
