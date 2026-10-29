"""Qt shell with navigation and backend-confirmed overview rendering."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QResizeEvent
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
from guard_app.services.bridge import (
    BridgeState,
    CommandStage,
    LifecycleEvent,
    RegistrationView,
    TaskProjection,
)

from .approvals import ApprovalListPage, ApprovalPage, ApprovalQuery
from .call_logs import CallLogPage, DetailRequest, PublicCallDetail, PublicCallSummary
from .icons import line_icon
from .model_settings import CodexLoginInfo
from .models import (
    LoadState,
    OverviewSnapshot,
    RuntimeIdentity,
    RuntimeMode,
    ServerSummary,
    Surface,
)
from .overview import OverviewPage
from .policy import PolicyPage
from .presentation import (
    PAGES,
    SETTINGS_FILE_LOCATION,
    SETTINGS_FILE_NOT_READ,
    SETTINGS_FILE_PENDING,
    SETTINGS_FILE_STATUS,
)
from .runtime import RuntimePanel
from .scan_status import ScanStatusProjection
from .scanner_key import KeySaver, KeyTail, ScannerKeyForm
from .server_add import ServerAddForm
from .servers import ServerListPage
from .sessions import SessionWorkspace
from .style import apply_style, current_theme, set_theme, system_theme
from .task_status import TaskStatusCard
from .widgets import IconMark, KeyboardActionButton, plain_label

# Results read like a document, not a dashboard stretched across the screen.
_RESULTS_MAX_WIDTH = 808
# The docked results pane leaves the conversation most of the window.
_ACTIVITY_PANE_WIDTH = 376


def _scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    return scroll


def _placeholder_page(page: str) -> QWidget:
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(32, 32, 32, 32)
    layout.setSpacing(12)
    layout.addWidget(plain_label(PAGES[page], name="pageTitle"))
    note = plain_label(
        "백엔드 확정 조회 계약을 연결한 뒤 표시할 화면임. "
        "현황으로 돌아가면 현재 제공된 상태를 볼 수 있음.",
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

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scanner_key_saver: KeySaver | None = None,
        scanner_key_tail: KeyTail | None = None,
    ) -> None:
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
        self._activity_docked = False
        self._runtime_panel: RuntimePanel | None = None
        self._runtime_context = ""
        # Follow the platform's light or dark scheme until the user picks one.
        self._theme_pinned = False
        self.sessions = SessionWorkspace()

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # Sessions first (Codex, Claude Code, Conductor): new session, search,
        # three views, the session list, then the management pages below it.
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(224)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 0, 0)
        brand.setSpacing(8)
        brand.addWidget(IconMark("shield", "on_accent", 20, 13, name="brandMark"))
        brand.addWidget(plain_label("MCP Runtime Guard", name="brandTitle"), 1)
        self.theme_button = KeyboardActionButton()
        self.theme_button.setObjectName("iconButton")
        self.theme_button.clicked.connect(self._toggle_theme)
        brand.addWidget(self.theme_button)
        side.addLayout(brand)
        side.addSpacing(8)
        self.new_session_button = KeyboardActionButton("새 세션")
        self.new_session_button.setObjectName("newSessionButton")
        self.new_session_button.set_visual(icon="compose", variant="soft")
        self.new_session_button.clicked.connect(self._new_session)
        side.addWidget(self.new_session_button)
        side.addWidget(self.sessions.search)
        side.addSpacing(2)
        for page in ("runs", "results", "approvals"):
            side.addWidget(self._nav_button(page))
        side.addSpacing(16)
        sessions_caption = plain_label("세션", name="navCaption")
        sessions_caption.setToolTip("세션은 이 앱에서만 유지됨.")
        side.addWidget(sessions_caption)
        side.addSpacing(2)
        side.addWidget(self.sessions.list, 1)
        side.addSpacing(8)
        management_caption = plain_label("관리", name="navCaption")
        management_caption.setToolTip("백엔드 확정 조회만 표시함.")
        side.addWidget(management_caption)
        side.addSpacing(2)
        for page in ("servers", "server_add", "overview", "logs", "policy", "audit", "settings"):
            side.addWidget(self._nav_button(page))
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
        top.setContentsMargins(20, 8, 16, 8)
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
        self.breadcrumb = plain_label(PAGES["overview"], name="breadcrumb", wrap=False)
        top.addWidget(self.breadcrumb, 0, 0)
        self.runtime_identity = plain_label(name="modeBadge", wrap=False)
        top.addWidget(self.runtime_identity, 0, 1)
        top.setColumnStretch(0, 1)
        self.set_runtime_identity(RuntimeIdentity())
        work.addWidget(topbar)

        self._back_row = QWidget()
        back = QHBoxLayout(self._back_row)
        back.setContentsMargins(16, 8, 16, 0)
        self.back_to_conversation = KeyboardActionButton("← 대화로 돌아가기")
        self.back_to_conversation.setObjectName("ghostButton")
        self.back_to_conversation.clicked.connect(lambda: self.navigate("runs"))
        back.addWidget(self.back_to_conversation, 0, Qt.AlignmentFlag.AlignLeft)
        work.addWidget(self._back_row)

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
        # The add form scrolls itself; the folder rules ride in the same scroll,
        # folded, since only the app's local file server needs them.
        self.server_add_form = ServerAddForm()
        self.folder_rules_form = self.server_add_form.file_folders
        self._page_indices["server_add"] = self.stack.addWidget(self.server_add_form)
        # A session's results and activity are one block that moves between
        # two hosts: the chat header's 활동 docks it beside the conversation,
        # and the sidebar's 결과 · 실행 활동 opens it as a page of its own (the
        # compact shell's only form). Status, run controls and approval review
        # scroll; the stop controls stay pinned below them in both.
        self._activity = QWidget()
        self._activity.setObjectName("activityContent")
        activity = QVBoxLayout(self._activity)
        activity.setContentsMargins(0, 0, 0, 0)
        activity.setSpacing(0)
        self._results_body = QWidget()
        self._results_body.setMaximumWidth(_RESULTS_MAX_WIDTH)
        body_layout = QVBoxLayout(self._results_body)
        body_layout.setContentsMargins(24, 24, 24, 24)
        body_layout.setSpacing(0)
        self._results_heading = QWidget()
        heading = QVBoxLayout(self._results_heading)
        heading.setContentsMargins(0, 0, 0, 16)
        heading.setSpacing(4)
        heading.addWidget(plain_label(PAGES["results"], name="pageTitle"))
        self.results_scope = plain_label(name="secondary")
        heading.addWidget(self.results_scope)
        body_layout.addWidget(self._results_heading)
        self.task_status_card = TaskStatusCard()
        body_layout.addWidget(self.task_status_card)
        self.runtime_layout = body_layout
        body_layout.addStretch(1)
        self._results_scroll = _scroll_page(self._results_body)
        self._results_scroll.setObjectName("resultsScroll")
        self._results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        activity.addWidget(self._results_scroll, 1)
        self._results_footer = QFrame()
        self._results_footer.setObjectName("resultsFooter")
        footer = QHBoxLayout(self._results_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self._results_footer_layout = footer
        self._results_footer.hide()
        activity.addWidget(self._results_footer)

        self.results_page = QWidget()
        self.results_page.setObjectName("resultsPage")
        self._results_page_layout = QVBoxLayout(self.results_page)
        self._results_page_layout.setContentsMargins(0, 0, 0, 0)
        self._results_page_layout.addWidget(self._activity)
        self._activity_host = self._results_page_layout

        conversation = QWidget()
        conversation_row = QHBoxLayout(conversation)
        conversation_row.setContentsMargins(0, 0, 0, 0)
        conversation_row.setSpacing(0)
        self._conversation_scroll = _scroll_page(self.sessions)
        conversation_row.addWidget(self._conversation_scroll, 1)
        self.activity_pane = QFrame()
        self.activity_pane.setObjectName("activityPane")
        self.activity_pane.setFixedWidth(_ACTIVITY_PANE_WIDTH)
        pane = QVBoxLayout(self.activity_pane)
        pane.setContentsMargins(0, 0, 0, 0)
        pane.setSpacing(0)
        head = QFrame()
        head.setObjectName("paneHead")
        pane_head = QHBoxLayout(head)
        pane_head.setContentsMargins(24, 8, 12, 8)
        pane_head.addWidget(plain_label(PAGES["results"], name="paneTitle"), 1)
        self.activity_close = KeyboardActionButton()
        self.activity_close.setObjectName("iconButton")
        self.activity_close.set_visual(icon="close")
        self.activity_close.setAccessibleName("결과 · 실행 활동 패널 닫기")
        self.activity_close.setToolTip("패널 닫기")
        self.activity_close.clicked.connect(lambda: self.set_activity_docked(False))
        pane_head.addWidget(self.activity_close)
        pane.addWidget(head)
        self._activity_pane_layout = pane
        self.activity_pane.hide()
        conversation_row.addWidget(self.activity_pane)
        self._page_indices["runs"] = self.stack.addWidget(conversation)
        self._page_indices["results"] = self.stack.addWidget(self.results_page)
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(24, 24, 24, 24)
        settings_layout.addWidget(plain_label("설정", name="pageTitle"))
        settings_layout.addWidget(plain_label("설정 파일", name="cardTitle"))
        self.settings_file_status = plain_label(SETTINGS_FILE_PENDING, name="secondary")
        self.settings_file_status.setAccessibleName("설정 파일 상태")
        settings_layout.addWidget(self.settings_file_status)
        settings_layout.addWidget(plain_label(SETTINGS_FILE_LOCATION, name="secondary"))
        settings_layout.addSpacing(12)
        self.scanner_key_form = ScannerKeyForm(saver=scanner_key_saver, tail=scanner_key_tail)
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
        self.set_theme(system_theme())
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        self._update_shell_layout()
        self.sessions.results_requested.connect(self._toggle_activity)
        self.sessions.approval_review_requested.connect(self._open_approval_review)
        self.sessions.selection_changed.connect(self._session_selected)
        self.navigate("runs", "")
        # Open on the message box, as messengers do.
        self.sessions.current.draft.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_theme(self, theme: str) -> None:
        """Repaint the shell in one palette; icons and painted rows follow it."""
        set_theme(theme)
        apply_style(self)
        dark = theme == "dark"
        self.theme_button.set_visual(icon="sun" if dark else "moon")
        label = "라이트 테마로 전환" if dark else "다크 테마로 전환"
        self.theme_button.setAccessibleName(label)
        self.theme_button.setToolTip(label)
        self.sessions.list.viewport().update()

    def _toggle_theme(self) -> None:
        self._theme_pinned = True
        theme = "light" if current_theme() == "dark" else "dark"
        self.set_theme(theme)
        # The native title bar follows the app's scheme on Windows and macOS.
        QGuiApplication.styleHints().setColorScheme(
            Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light
        )

    def _system_theme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if not self._theme_pinned:
            self.set_theme(system_theme())

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
            # The results page follows the selected session; elsewhere a pick
            # opens its conversation.
            self.navigate("results" if self._page == "results" else "runs")

    def _toggle_activity(self) -> None:
        """The chat header's 활동 docks results beside the conversation; the
        compact shell has no room for a dock, so there it opens the page."""
        if self._compact_navigation:
            self.navigate("results")
        else:
            self.set_activity_docked(not self._activity_docked)

    def _open_approval_review(self) -> None:
        """The conversation's approval band opens the activity block at its
        approval section and asks for the copy, as 승인 사본 확인 there does."""
        if self._compact_navigation:
            self.navigate("results")
        elif not self._activity_docked:
            self.set_activity_docked(True)
        panel = self._runtime_panel
        if panel is None:
            return
        button = panel.review_button
        if button.isEnabled():
            button.setFocus(Qt.FocusReason.OtherFocusReason)
            button.click()
        # Once the dock has laid out at its new size, bring the section's top
        # to the top so the copy below it has the most room.
        QTimer.singleShot(0, lambda: self._scroll_results_to(panel.approval_section))

    def _scroll_results_to(self, section: QWidget) -> None:
        content = self._results_scroll.widget()
        if content is None or not content.isAncestorOf(section):
            return
        top = section.mapTo(content, QPoint(0, 0)).y()
        self._results_scroll.verticalScrollBar().setValue(top)

    def set_activity_docked(self, docked: bool) -> None:
        self._activity_docked = docked
        if docked and self._page != "runs":
            self.navigate("runs")
        else:
            self._place_activity()

    def _place_activity(self) -> None:
        """Move the results block to the conversation dock or the results page."""
        on_page = self._page == "results" or self._compact_navigation
        docked = self._activity_docked and not on_page
        host = self._activity_pane_layout if docked else self._results_page_layout
        if host is not self._activity_host:
            self._activity_host.removeWidget(self._activity)
            host.addWidget(self._activity, 1)
            self._activity_host = host
            self._activity.show()
        self._results_heading.setVisible(not docked)
        self._results_body.setMaximumWidth(16777215 if docked else _RESULTS_MAX_WIDTH)
        shown = docked and self._page == "runs"
        self.activity_pane.setVisible(shown)
        self.sessions.set_activity_open(shown)

    def attach_runtime(self, panel: RuntimePanel) -> None:
        """Keep the original human-control panel behind the session's detail view."""
        if self._runtime_panel is not None:
            raise ValueError("Runtime is already attached")
        self._runtime_panel = panel
        self.runtime_layout.insertWidget(
            self.runtime_layout.indexOf(self.task_status_card) + 1, panel
        )
        stop = panel.pin_stop_section()
        stop.setMaximumWidth(_RESULTS_MAX_WIDTH)
        # Grows to the content width, so the stop buttons line up with the
        # run button above; wider windows leave the rest empty.
        self._results_footer_layout.addWidget(stop, 1)
        self._results_footer_layout.addStretch(0)
        self._results_footer.show()
        panel.set_scope(None)
        panel.state_changed.connect(lambda: self.sessions.set_busy_scopes(panel.busy_scopes))
        panel.selected_snapshot_changed.connect(self._sync_selected_task)
        bridge = panel.bridge
        self.server_add_form.attach_registration(bridge)
        self.server_page.attach_activation(bridge)
        bridge.registration_changed.connect(self._registration_rows)
        bridge.task_changed.connect(self.sessions.set_snapshot)
        bridge.session_created.connect(self.sessions.session_created)
        bridge.session_capability_changed.connect(self.sessions.set_session_blocker)
        bridge.account_changed.connect(self.codex_login_info.set_view)
        bridge.policy_changed.connect(self.policy_page.set_view)
        bridge.policy_changed.connect(self._policy_changed)
        bridge.settings_changed.connect(self.set_settings_status)
        self.policy_page.allow_change_requested.connect(
            lambda server_id, allowed, denied: self.policy_page.set_change_pending(
                bridge.request_policy_access(server_id, allowed, denied)
            )
        )
        login = self.codex_login_info
        login.read_requested.connect(lambda: bridge.request_account("account_read"))
        login.device_login_requested.connect(lambda: bridge.request_account("account_login_device"))
        login.browser_login_requested.connect(
            lambda: bridge.request_account("account_login_browser")
        )
        bridge.command_changed.connect(login.account_command_changed)
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
                identity = RuntimeIdentity(model_mode=RuntimeMode(snapshot.model_mode))
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
            self._top_layout.addWidget(
                self.runtime_identity, 1, 0, 1, 2, Qt.AlignmentFlag.AlignLeft
            )
            self._top_layout.setColumnStretch(0, 0)
            self._top_layout.setColumnStretch(1, 1)
            self.breadcrumb.setWordWrap(True)
            self.runtime_identity.setWordWrap(False)
        else:
            self._top_layout.removeWidget(self.menu_button)
            self._top_layout.setContentsMargins(20, 8, 20, 8)
            self._top_layout.addWidget(self.breadcrumb, 0, 0)
            self._top_layout.addWidget(self.runtime_identity, 0, 1)
            self._top_layout.setColumnStretch(0, 1)
            self._top_layout.setColumnStretch(1, 0)
            self.breadcrumb.setWordWrap(False)
            self.runtime_identity.setWordWrap(False)
        self._update_back_row()
        self._place_activity()

    def _update_back_row(self) -> None:
        back = self._page != "runs"
        self._back_row.setVisible(back)
        self.back_to_conversation.setVisible(back)

    def _nav_button(self, page: str) -> QPushButton:
        button = KeyboardActionButton(PAGES[page])
        button.setIcon(line_icon(self._nav_icon(page), "mute"))
        button.setIconSize(QSize(15, 15))
        button.setObjectName("navButton")
        button.setAccessibleName(f"{PAGES[page]} 화면")
        button.clicked.connect(lambda: self.navigate(page, ""))
        self._nav_buttons[page] = button
        return button

    @staticmethod
    def _nav_icon(page: str) -> str:
        return {
            "policy": "shield",
            "runs": "chat",
            "results": "panel",
            "server_add": "plus",
        }.get(page, page)

    def set_overview_snapshot(self, snapshot: OverviewSnapshot) -> None:
        """Called on the Qt thread with a bridge-approved public projection."""

        self.overview.set_snapshot(snapshot)

    def _registration_rows(self, view: RegistrationView) -> None:
        if view.code in ("REG_REGISTERED", "REG_PIN_SAVED"):
            self.set_settings_status("SETTINGS_REGISTRATION_SAVED")
        if view.rows is None:
            return
        self.set_server_snapshot(
            Surface(
                LoadState.READY,
                tuple(
                    ServerSummary(
                        row.server_id,
                        row.server_id,
                        max_label=row.max_label,
                        tier=row.tier,
                        registry_status=row.status,
                        transport=row.transport,
                        executable_pinned=row.pinned if row.transport == "stdio" else None,
                    )
                    for row in view.rows
                ),
            )
        )
        self.server_add_form.set_known_ids({row.server_id for row in view.rows})

    def set_server_snapshot(self, snapshot: Surface[tuple[ServerSummary, ...]]) -> None:
        """Update Registry rows from their own query, never overview rows."""

        self.server_page.set_servers(snapshot)

    def set_scan_snapshot(
        self, server_id: str, token: int, snapshot: Surface[ScanStatusProjection]
    ) -> bool:
        """Show only the current server detail's confirmed public scan query."""

        return self.server_page.set_scan_snapshot(server_id, token, snapshot)

    def set_settings_status(self, code: object) -> None:
        """Show the settings file status; with no file, open the settings page (CFG-01)."""

        if code is None:
            self.settings_file_status.setText(SETTINGS_FILE_NOT_READ)
            return
        text = SETTINGS_FILE_STATUS.get(code) if type(code) is str else None
        self.settings_file_status.setText(
            text if text is not None else "설정 파일 상태를 확인하지 못함."
        )
        # Only from the start page, so a page the user already chose stays.
        if code == "SETTINGS_FILE_MISSING" and self._page == "runs":
            self.navigate("settings")

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
        """Show only the model mode explicitly confirmed by trusted app bootstrap."""

        labels = {
            RuntimeMode.UNCONFIGURED: "미설정",
            RuntimeMode.MOCK: "mock",
            RuntimeMode.REAL: "real",
            RuntimeMode.CODEX: "Codex",
        }
        self.runtime_identity.setText(f"모델: {labels.get(identity.model_mode, '미확인')}")

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
        self.breadcrumb.setText(PAGES[page])
        if page == "results":
            self.results_scope.setText(self.sessions.current.title)
        self._update_back_row()
        self._place_activity()
        if page == "runs":
            self.sessions.return_to_conversation()
        for key, button in self._nav_buttons.items():
            button.setProperty("selected", key == page)
            button.setIcon(line_icon(self._nav_icon(key), "ink" if key == page else "mute"))
            button.style().unpolish(button)
            button.style().polish(button)
        for key, action in self._menu_actions.items():
            action.setChecked(key == page)
        self.navigation_requested.emit(page, target_id)
