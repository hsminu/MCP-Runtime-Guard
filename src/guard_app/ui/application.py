"""Qt window lifetime, imported only after entry log protection is installed."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from guard_app.services.bridge import BackendBridge

    from .scanner_key import KeySaver, KeyTail


def run_application(
    app: QApplication,
    bridge: BackendBridge,
    *,
    context: str,
    close_observation_s: float,
    scanner_key_saver: KeySaver | None = None,
    scanner_key_tail: KeyTail | None = None,
) -> tuple[int, bool]:
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtGui import QCloseEvent

    from guard_app.services.bridge import BackendCleanupUnconfirmedError, BridgeState

    from .runtime import RuntimePanel
    from .window import GuardWindow

    close_limit_s = bridge.shutdown_timeout + close_observation_s
    closing_since: list[float] = []
    start_failed: list[bool] = []
    bridge.lifecycle_changed.connect(
        lambda event: start_failed.append(True) if event.state is BridgeState.FAILED else None
    )

    class ApplicationWindow(GuardWindow):
        def closeEvent(self, event: QCloseEvent) -> None:
            self.clear_session_content()
            if not closing_since:
                closing_since.append(time.monotonic())
            bridge.close()
            self.hide()
            event.ignore()

    def quit_when_closed() -> None:
        if window.isVisible():
            return
        overdue = bool(closing_since) and time.monotonic() - closing_since[0] > close_limit_s
        if bridge.thread_done or overdue:
            app.quit()

    window = ApplicationWindow(
        scanner_key_saver=scanner_key_saver,
        scanner_key_tail=scanner_key_tail,
    )
    panel = RuntimePanel(bridge)
    window.attach_runtime(panel)
    window.set_runtime_context(context)
    window.navigate("runs")
    timer = QTimer()
    timer.setInterval(25)
    timer.timeout.connect(quit_when_closed)
    timer.start()
    app.aboutToQuit.connect(bridge.close)

    def dispose_window() -> None:
        # Drain final lifecycle signals while widgets are alive (A182, A172).
        timer.stop()
        QCoreApplication.sendPostedEvents()
        QCoreApplication.processEvents()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    finished = False
    try:
        window.show()
        bridge.start()
        code = app.exec()
        finished = True
    finally:
        # close was already requested on quit, or is requested below on error.
        app.aboutToQuit.disconnect(bridge.close)
        # #101, REC-01/REC-05: even an exception must confirm Store shutdown
        # before the entry point can release the single-owner lock.
        if not finished:
            bridge.close()
            waited_s = time.monotonic() - closing_since[0] if closing_since else 0.0
            done = bridge.wait_done(close_limit_s - waited_s)
            if not done or bridge.cleanup_unconfirmed:
                # Live backends may still signal the window before process exit (A182, A172).
                raise BackendCleanupUnconfirmedError
            dispose_window()
    # A quit from elsewhere (session end) also gets the observation window.
    waited_s = time.monotonic() - closing_since[0] if closing_since else 0.0
    done = bridge.wait_done(close_limit_s - waited_s)
    confirmed = done and not bridge.cleanup_unconfirmed
    if confirmed:
        dispose_window()
    return (1 if start_failed else code), confirmed
