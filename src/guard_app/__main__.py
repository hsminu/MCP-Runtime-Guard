"""Qt main thread and backend ownership; explicit source-checkout demo only."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from guard_app.services.bridge import BackendBridge
from guard_app.ui.models import RuntimeIdentity, RuntimeMode
from guard_app.ui.runtime import RuntimePanel
from guard_app.ui.window import GuardWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Runtime Guard 합성 개발 앱")
    parser.add_argument(
        "--demo",
        action="store_true",
        required=True,
        help="소스 checkout에서 B 메모리 대역 + 실제 stdio 합성 서버 사용",
    )
    parser.add_argument("--scenario", choices=("ask", "allow", "deny"), default="ask")
    args = parser.parse_args()
    from examples.synthetic_app import bootstrap

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    bridge = BackendBridge(lambda: bootstrap(args.scenario))

    class ApplicationWindow(GuardWindow):
        def closeEvent(self, event):
            panel._clear_review()
            bridge.close()
            self.hide()
            event.ignore()

    window = ApplicationWindow()
    panel = RuntimePanel(bridge)
    window.runtime_layout.insertWidget(2, panel)
    bridge.task_changed.connect(lambda state: window.set_task_status(state.status))
    bridge.task_changed.connect(
        lambda state: window.set_runtime_identity(
            RuntimeIdentity(RuntimeMode(state.agt_mode), RuntimeMode(state.model_mode))
        )
    )
    window.setWindowTitle("MCP Runtime Guard · 합성 개발 시연 / 메모리 Store / AGT·모델 mock")
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
