"""Qt shell with navigation and backend-confirmed overview rendering."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
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
from guard_app.services.bridge import BridgeState, CommandStage, LifecycleEvent, TaskProjection

from .approvals import ApprovalListPage, ApprovalPage, ApprovalQuery
from .call_logs import CallLogPage, DetailRequest, PublicCallDetail, PublicCallSummary
from .icons import line_icon
from .model_settings import CodexLoginInfo
from .models import (
    OverviewSnapshot,
    RuntimeIdentity,
    RuntimeMode,
    ServerSummary,
    Surface,
)
from .overview import OverviewPage
from .policy import PolicyPage
from .presentation import PAGES
from .runtime import RuntimePanel
from .scan_status import ScanStatusProjection
from .scanner_key import ScannerKeyForm
from .server_add import LocalFolderRulesForm, ServerAddForm
from .servers import ServerListPage
from .sessions import SessionWorkspace
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
        "백엔드 확정 조회 계약을 연결한 뒤 표시할 화면이야. "
        "현황으로 돌아가면 현재 제공된 상태를 볼 수 있어.",
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
        frame_allowance = 2 * self.style().pixelMetric(QStyle.PixelMetric.PM_MdiSubWindowFrameWidth)
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
        self._runtime_panel: RuntimePanel | None = None
        self._runtime_context = ""
        self.sessions = SessionWorkspace()

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(224)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 22, 14, 14)
        side.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = plain_label(name="brandMark")
        mark.setFixedSize(40, 40)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(line_icon("shield", "#ffffff").pixmap(QSize(24, 24)))
        brand.addWidget(mark)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(2)
        brand_copy.addWidget(plain_label("MCP Guard", name="brandTitle"))
        brand_copy.addWidget(plain_label("나의 도구, 안전한 실행", name="brandCaption"))
        brand.addLayout(brand_copy, 1)
        side.addLayout(brand)
        side.addSpacing(16)
        self.new_session_button = KeyboardActionButton("새 세션")
        self.new_session_button.set_visual(icon="plus", variant="soft")
        self.new_session_button.clicked.connect(self._new_session)
        side.addWidget(self.new_session_button)
        side.addWidget(self.sessions.search)
        for page in ("runs", "results", "approvals"):
            side.addWidget(self._nav_button(page))
        side.addWidget(plain_label("세션 · 이 앱에서만 유지", name="navCaption"))
        side.addWidget(self.sessions.list, 1)
        side.addWidget(plain_label("관리", name="navCaption"))
        management = QWidget()
        management_layout = QVBoxLayout(management)
        management_layout.setContentsMargins(0, 0, 0, 0)
        management_layout.setSpacing(0)
        for page in ("servers", "server_add", "overview", "logs", "policy", "audit", "settings"):
            management_layout.addWidget(self._nav_button(page))
        management_scroll = _scroll_page(management)
        management_scroll.setMinimumHeight(80)
        management_scroll.setMaximumHeight(220)
        management_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side.addWidget(management_scroll)
        footer = QFrame()
        footer.setObjectName("sidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(8, 16, 0, 0)
        footer_layout.addWidget(plain_label("백엔드 확정 조회만 표시", name="secondary"))
        side.addWidget(footer)
        root.addWidget(self.sidebar)
        # Start with the compact shell so a 320 px resize cannot be constrained
        # by the sidebar before the first resize event has reflowed the layout.
        self.sidebar.hide()

        workspace = QWidget()
        workspace.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
        self.menu_button.set_visual(icon="menu")
        self.menu_button.setAccessibleName("화면 이동 메뉴")
        self.menu_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._nav_menu = QMenu(self.menu_button)
        self._nav_menu.addAction("새 세션", self._new_session)
        self._session_menu = self._nav_menu.addMenu("세션 선택")
        self._session_menu.aboutToShow.connect(self._populate_session_menu)
        self._nav_menu.addSeparator()
        for page in PAGES:
            action = self._nav_menu.addAction(PAGES[page])
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, key=page: self.navigate(key))
            self._menu_actions[page] = action
        self.menu_button.setMenu(self._nav_menu)
        self.menu_button.hide()
        self.breadcrumb = plain_label("Workspace / 현황", name="cardTitle", wrap=False)
        top.addWidget(self.breadcrumb, 0, 0)
        self.runtime_identity = plain_label(name="modeBadge", wrap=False)
        top.addWidget(self.runtime_identity, 0, 1)
        top.setColumnStretch(0, 1)
        self.set_runtime_identity(RuntimeIdentity())
        work.addWidget(topbar)

        self.back_to_conversation = KeyboardActionButton("← 대화로 돌아가기")
        self.back_to_conversation.clicked.connect(lambda: self.navigate("runs"))
        work.addWidget(self.back_to_conversation, 0, Qt.AlignmentFlag.AlignLeft)

        self.stack = QStackedWidget()
        self.overview = OverviewPage()
        self.overview.navigate_requested.connect(self.navigate)
        self._page_indices["overview"] = self.stack.addWidget(_scroll_page(self.overview))
        self.server_page = ServerListPage()
        self.server_page.scan_snapshot_requested.connect(self.scan_snapshot_requested.emit)
        self._page_indices["servers"] = self.stack.addWidget(_scroll_page(self.server_page))
        self.log_page = CallLogPage()
        self.log_page.detail_requested.connect(self.call_log_detail_requested.emit)
        self._page_indices["logs"] = self.stack.addWidget(_scroll_page(self.log_page))
        self.approval_page = ApprovalListPage()
        self.approval_page.query_requested.connect(self.approval_query_requested.emit)
        # No product bridge serves approval listings yet (B6-02); a composition
        # root that does calls set_approval_query_connected(True).
        self.approval_page.set_source_connected(False)
        self._page_indices["approvals"] = self.stack.addWidget(_scroll_page(self.approval_page))
        self.server_add_form = ServerAddForm()
        server_add_page = QWidget()
        server_add_layout = QVBoxLayout(server_add_page)
        server_add_layout.setContentsMargins(0, 0, 0, 0)
        server_add_layout.addWidget(self.server_add_form)
        self.folder_rules_form = LocalFolderRulesForm()
        server_add_layout.addWidget(self.folder_rules_form)
        server_add_layout.addWidget(
            plain_label(
                "폴더 등급은 입력 초안이야. Registry·경로 검증 연결 전에는 적용되지 않아.",
                name="secondary",
            )
        )
        self._page_indices["server_add"] = self.stack.addWidget(_scroll_page(server_add_page))
        task_page = QWidget()
        task_layout = QVBoxLayout(task_page)
        task_layout.setContentsMargins(26, 28, 26, 42)
        task_layout.setSpacing(16)
        task_layout.addWidget(plain_label("결과 · 실행 활동", name="pageTitle"))
        task_layout.addWidget(
            plain_label("Task 상태는 백엔드에서 확인된 값만 표시해.", name="secondary")
        )
        self.task_status_card = TaskStatusCard()
        task_layout.addWidget(self.task_status_card)
        self.runtime_layout = task_layout
        task_layout.addStretch(1)
        self._page_indices["results"] = self.stack.addWidget(_scroll_page(task_page))
        self._page_indices["runs"] = self.stack.addWidget(_scroll_page(self.sessions))
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(26, 28, 26, 28)
        settings_layout.addWidget(plain_label("설정", name="pageTitle"))
        settings_layout.addWidget(
            plain_label(
                "모델 연결과 Scanner 설정을 확인해.",
                name="secondary",
            )
        )
        self.scanner_key_form = ScannerKeyForm()
        self.codex_login_info = CodexLoginInfo()
        settings_layout.addWidget(self.codex_login_info)
        settings_layout.addWidget(self.scanner_key_form)
        settings_layout.addStretch(1)
        self._page_indices["settings"] = self.stack.addWidget(_scroll_page(settings))
        self.policy_page = PolicyPage()
        self._page_indices["policy"] = self.stack.addWidget(_scroll_page(self.policy_page))
        for page in PAGES:
            if page not in (
                "policy",
                "overview",
                "servers",
                "logs",
                "approvals",
                "server_add",
                "runs",
                "results",
                "settings",
            ):
                self._page_indices[page] = self.stack.addWidget(
                    _scroll_page(_placeholder_page(page))
                )
        work.addWidget(self.stack, 1)
        root.addWidget(workspace, 1)
        apply_style(self)
        self._update_shell_layout()
        self.sessions.results_requested.connect(lambda: self.navigate("results"))
        self.sessions.selection_changed.connect(self._session_selected)
        self.navigate("runs", "")

    def _new_session(self) -> None:
        self.sessions.new_session()
        self.navigate("runs")

    def _populate_session_menu(self) -> None:
        self._session_menu.clear()
        for key, view in self.sessions.sessions.items():
            action = self._session_menu.addAction(view.title)
            action.triggered.connect(lambda checked=False, target=key: self.sessions.select(target))

    def _session_selected(self, _key: str) -> None:
        panel = self._runtime_panel
        if panel is not None:
            state = self.sessions.current.snapshot
            panel.set_scope(None if state is None else (state.task_id, state.epoch))
        if hasattr(self, "stack"):
            self.navigate("runs")

    def attach_runtime(self, panel: RuntimePanel) -> None:
        """Keep the original human-control panel behind the session's detail view."""
        if self._runtime_panel is not None:
            raise ValueError("Runtime is already attached")
        self._runtime_panel = panel
        self.runtime_layout.insertWidget(2, panel)
        panel.set_scope(None)
        panel.state_changed.connect(lambda: self.sessions.set_busy_scopes(panel.busy_scopes))
        panel.selected_snapshot_changed.connect(self._sync_selected_task)
        bridge = panel.bridge
        bridge.task_changed.connect(self.sessions.set_snapshot)
        bridge.session_created.connect(self.sessions.session_created)
        bridge.session_capability_changed.connect(self.sessions.set_session_blocker)
        bridge.account_changed.connect(self.codex_login_info.set_view)
        bridge.policy_changed.connect(self.policy_page.set_view)
        bridge.policy_changed.connect(self._policy_changed)
        self.policy_page.allow_change_requested.connect(
            lambda server_id, allowed, denied: self.policy_page.set_change_pending(
                bridge.request_policy_access(server_id, allowed, denied)
            )
        )
        login = self.codex_login_info
        login.read_requested.connect(lambda: bridge.request_account("account_read"))
        login.device_login_requested.connect(lambda: bridge.request_account("account_login_device"))
        login.cancel_requested.connect(
            lambda login_id: bridge.request_account("account_cancel_login", target_id=login_id)
        )
        login.logout_requested.connect(lambda: bridge.request_account("account_logout"))
        login.set_api_key_sender(
            lambda key: bridge.request_account("account_login_api_key", secret=key)
        )
        self.sessions.new_session_requested.connect(
            lambda key, options, command: bridge.request_new_session(
                key, options, command_id=command
            )
        )
        bridge.conversation_ready.connect(self.sessions.conversation)
        bridge.conversation_partial.connect(self.sessions.conversation_partial)
        self.sessions.message_requested.connect(panel.send_message)
        panel.message_rejected.connect(
            lambda command: self.sessions.settle_command(command, "MESSAGE_UNAVAILABLE")
        )
        bridge.command_changed.connect(
            lambda event: (
                self.sessions.settle_command(event.command_id, event.code)
                if event.stage
                in (CommandStage.REJECTED, CommandStage.UNCONFIRMED, CommandStage.BACKEND_RETURNED)
                else None
            )
        )
        bridge.command_changed.connect(
            lambda event: (
                self.policy_page.settle_change(event.command_id, event.code)
                if event.stage
                in (CommandStage.REJECTED, CommandStage.UNCONFIRMED, CommandStage.BACKEND_RETURNED)
                else None
            )
        )

        def lifecycle(event: LifecycleEvent) -> None:
            self.sessions.set_ready(event.state is BridgeState.READY)
            if event.state in (BridgeState.FAILED, BridgeState.CLOSING, BridgeState.CLOSED):
                self.sessions.clear_content()

        bridge.lifecycle_changed.connect(lifecycle)

    def set_runtime_context(self, context: str) -> None:
        self._runtime_context = context
        self._sync_selected_task(
            None if self._runtime_panel is None else self._runtime_panel.current
        )

    def _sync_selected_task(self, snapshot: TaskProjection | None) -> None:
        """One source for the selected Task's status, mode badge and title."""
        self.set_task_status(None if snapshot is None else snapshot.status)
        if snapshot is None:
            identity = RuntimeIdentity()
            mode = None
        else:
            try:
                identity = RuntimeIdentity(
                    RuntimeMode(snapshot.agt_mode), RuntimeMode(snapshot.model_mode)
                )
            except ValueError:
                identity = RuntimeIdentity()
            mode = snapshot.model_mode
        self.set_runtime_identity(identity)
        context = f" · {self._runtime_context}" if self._runtime_context else ""
        model = "" if mode is None else f" · 모델 {mode}"
        self.setWindowTitle(f"MCP Runtime Guard{context}{model}")

    def clear_session_content(self) -> None:
        self.sessions.clear_content()
        if self._runtime_panel is not None:
            self._runtime_panel._clear_review()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "menu_button"):
            self._update_shell_layout()

    def _update_shell_layout(self) -> None:
        compact = self.width() < self._COMPACT_NAV_WIDTH or self.height() < self._COMPACT_NAV_HEIGHT
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
        button.setIcon(line_icon(self._nav_icon(page)))
        button.setIconSize(QSize(18, 18))
        button.setObjectName("navButton")
        button.setAccessibleName(f"{PAGES[page]} 화면")
        button.clicked.connect(lambda: self.navigate(page, ""))
        self._nav_buttons[page] = button
        return button

    @staticmethod
    def _nav_icon(page: str) -> str:
        return {"policy": "shield", "runs": "play", "server_add": "plus"}.get(page, page)

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

    def _policy_changed(self, view: object) -> None:
        ttl = getattr(view, "approval_ttl_seconds", None)
        if type(ttl) is int and ttl > 0:
            self.approval_page.set_approval_ttl_seconds(ttl)

    def set_approval_query_connected(self, connected: bool) -> None:
        """Enable approval listing only when something answers its queries."""

        self.approval_page.set_source_connected(connected)

    def set_approval_page(self, page: ApprovalPage) -> bool:
        """Accept only the current approval query response."""

        return self.approval_page.set_page(page)

    def set_approval_error(self, query: ApprovalQuery) -> bool:
        return self.approval_page.set_error(query)

    def set_call_log_snapshot(self, snapshot: Surface[tuple[PublicCallSummary, ...]]) -> None:
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
            RuntimeMode.CODEX: "Codex",
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
        if self._page == "runs" and page != "runs":
            self.sessions.leaving_conversation()
        self._page = page
        self.stack.setCurrentIndex(self._page_indices[page])
        if page == "servers" and target_id:
            self.server_page.select_server(target_id)
        elif page == "logs" and target_id:
            self.log_page.select_call(target_id)
        elif page == "approvals" and target_id:
            self.approval_page.select_approval(target_id)
        self.breadcrumb.setText(f"Workspace / {PAGES[page]}")
        self.back_to_conversation.setVisible(page != "runs")
        if page == "runs":
            self.sessions.return_to_conversation()
        for key, button in self._nav_buttons.items():
            button.setProperty("selected", key == page)
            button.setIcon(line_icon(self._nav_icon(key), "#6658d7" if key == page else "#7b8296"))
            button.style().unpolish(button)
            button.style().polish(button)
        for key, action in self._menu_actions.items():
            action.setChecked(key == page)
        self.navigation_requested.emit(page, target_id)
