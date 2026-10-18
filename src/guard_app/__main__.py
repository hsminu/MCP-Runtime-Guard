"""Qt main thread and backend ownership for product and explicit demo modes."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from guard_app.services.bridge import BackendBridge
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.window import GuardWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Runtime Guard")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="소스 checkout에서 선택한 Store + 실제 MCP 합성 서버 사용",
    )
    parser.add_argument("--scenario", choices=("ask", "allow", "deny"), default="ask")
    parser.add_argument("--mcp-transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--store", choices=("memory", "sqlite"), default="memory")
    parser.add_argument("--db-path", metavar="PATH", help="기존 부모 디렉터리의 SQLite 파일")
    parser.add_argument("--agt-mode", choices=("real", "mock"))
    parser.add_argument(
        "--agt-policy",
        choices=("allow-tools", "deny-send", "deny-all"),
        default="allow-tools",
        help="합성 도구의 AGT 허용·차단 시연 정책",
    )
    args = parser.parse_args()
    if args.demo and args.agt_mode is None:
        parser.error("합성 시연에는 --agt-mode real 또는 mock을 지정해야 한다")
    if args.demo and (args.store == "sqlite") != (args.db_path is not None):
        parser.error("저장소 종류와 DB 경로 설정이 일치하지 않는다")
    if not args.demo and args.db_path is None:
        parser.error("제품 실행에는 기존 부모 폴더 안의 --db-path SQLite 경로를 지정해야 한다")
    store_title = "SQLite Store" if not args.demo or args.store == "sqlite" else "메모리 Store"

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
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
        from guard_app.services.session_runtime import bootstrap_product

        def model_factory(mode, model, key):
            from guard_app.adapters.openai_model import OpenAIModelClient

            if mode != "api":
                raise ValueError("CLI_DEPENDENCIES_UNAVAILABLE")
            return OpenAIModelClient(model=model, api_key=key)

        # C's Windows credential capability is deliberately not substituted by
        # environment variables, files, direct key passing, or a local clone.
        bridge = BackendBridge(lambda: bootstrap_product(args.db_path, model_factory=model_factory))

    class ApplicationWindow(GuardWindow):
        def closeEvent(self, event):
            self.clear_session_content()
            bridge.close()
            self.hide()
            event.ignore()

    window = ApplicationWindow()
    panel = RuntimePanel(bridge)
    window.attach_runtime(panel)
    window.set_runtime_context(
        f"{'합성 개발 시연' if args.demo else '도구 없는 대화'} / {store_title}"
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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
