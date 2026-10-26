"""Qt main thread and backend ownership for product and explicit demo modes."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from guard_app.services.bridge import BackendBridge
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.window import GuardWindow

if TYPE_CHECKING:
    from guard_app.services.codex_preparation import CodexProcessPreparation


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Runtime Guard")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="소스 checkout에서 선택한 Store + 실제 MCP 합성 서버 사용",
    )
    # Demo-only options default to None so product mode can refuse them.
    parser.add_argument("--scenario", choices=("ask", "allow", "deny"))
    parser.add_argument("--mcp-transport", choices=("stdio", "http"))
    parser.add_argument("--store", choices=("memory", "sqlite"))
    parser.add_argument("--db-path", metavar="PATH", help="기존 부모 디렉터리의 SQLite 파일")
    parser.add_argument(
        "--codex-exe",
        metavar="PATH",
        type=Path,
        help="고정 버전 codex.exe의 절대 경로(기본: tools/install_codex.py 설치 위치)",
    )
    parser.add_argument("--agt-mode", choices=("real", "mock"))
    parser.add_argument(
        "--agt-policy",
        choices=("allow-tools", "deny-send", "deny-all"),
        help="합성 도구의 AGT 허용·차단 시연 정책",
    )
    args = parser.parse_args()
    demo_only = (args.scenario, args.mcp_transport, args.store, args.agt_mode, args.agt_policy)
    if not args.demo and any(value is not None for value in demo_only):
        parser.error("시연 전용 옵션은 --demo와 함께만 쓸 수 있다")
    if args.demo:
        args.scenario = args.scenario or "ask"
        args.mcp_transport = args.mcp_transport or "stdio"
        args.store = args.store or "memory"
        args.agt_policy = args.agt_policy or "allow-tools"
        if importlib.util.find_spec("examples") is None:
            parser.error(
                "합성 시연(--demo)은 소스 checkout에서 python -m guard_app으로만 실행할 수 있다"
            )
    if args.demo and args.agt_mode is None:
        parser.error("합성 시연에는 --agt-mode real 또는 mock을 지정해야 한다")
    if args.demo and (args.store == "sqlite") != (args.db_path is not None):
        parser.error("저장소 종류와 DB 경로 설정이 일치하지 않는다")
    if not args.demo and args.db_path is None:
        parser.error("제품 실행에는 기존 부모 폴더 안의 --db-path SQLite 경로를 지정해야 한다")
    if args.codex_exe is not None and (args.demo or not args.codex_exe.is_absolute()):
        parser.error("--codex-exe는 제품 실행에서 codex.exe의 절대 경로로만 쓸 수 있다")
    store_title = "SQLite Store" if not args.demo or args.store == "sqlite" else "메모리 Store"

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    codex: CodexProcessPreparation | None = None
    if args.demo:
        from examples.synthetic_app import bootstrap

        bridge = BackendBridge(
            lambda: bootstrap(
                args.scenario,
                agt_mode=args.agt_mode,
                agt_policy=args.agt_policy,
                mcp_transport=args.mcp_transport,
                store_kind=args.store,
                db_path=args.db_path,
            )
        )
    else:
        from guard_app.services.codex_preparation import CodexProcessPreparation
        from guard_app.services.session_runtime import bootstrap_product

        # A101: C's app folders and launcher behind each Task's Codex session.
        codex = CodexProcessPreparation(executable=args.codex_exe)
        bridge = BackendBridge(
            lambda: bootstrap_product(args.db_path, preparation=codex.preparation())
        )

    class ApplicationWindow(GuardWindow):
        def closeEvent(self, event: QCloseEvent) -> None:
            self.clear_session_content()
            bridge.close()
            self.hide()
            event.ignore()

    window = ApplicationWindow()
    panel = RuntimePanel(bridge)
    window.attach_runtime(panel)
    window.set_runtime_context(
        f"{'합성 개발 시연' if args.demo else 'Codex 연결 대기'} / {store_title}"
    )
    window.navigate("runs")
    timer = QTimer()
    timer.setInterval(25)
    timer.timeout.connect(
        lambda: app.quit() if bridge.thread_done and not window.isVisible() else None
    )
    timer.start()
    app.aboutToQuit.connect(bridge.close)
    window.show()
    bridge.start()
    try:
        return app.exec()
    finally:
        # The backend has closed its sessions; release the single-instance lock.
        if codex is not None:
            codex.close()


if __name__ == "__main__":
    raise SystemExit(main())
