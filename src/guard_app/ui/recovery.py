"""REC-04: committed facts and a bounded page of metadata, never replay."""

from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from guard_app.services.bridge import BackendBridge, RecoveryItemPage, RecoveryReport

from .widgets import plain_label


class RecoveryPanel(QWidget):
    def __init__(self, bridge: BackendBridge) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.summary = plain_label("")
        self.items = QPlainTextEdit()
        self.items.setReadOnly(True)
        self.items.setMaximumHeight(180)
        self.next_button = QPushButton("다음 100개")
        layout.addWidget(self.summary)
        layout.addWidget(plain_label("저장된 격리 항목 · 재실행하지 않음"))
        layout.addWidget(self.items)
        layout.addWidget(self.next_button)
        self.next_button.clicked.connect(self._next)
        self._bridge = bridge
        bridge.recovery_changed.connect(self.show_report)
        bridge.recovery_page_changed.connect(self.show_page)
        self.hide()

    def show_report(self, report: RecoveryReport | None) -> None:
        if report is None:
            return
        self.summary.setText(
            f"이전 실행에서 끝나지 않은 Task {report.changed_task_count}개를 정지함\n"
            f"요청 {report.changed_request_count}개 · "
            f"승인 {report.changed_approval_count}개 정리함 · "
            f"이번 시작에서 결과 불명 {report.unknown_outcome_count}개 · "
            f"새 격리 {report.quarantined_count}개"
        )
        self.show()

    def show_page(self, page: RecoveryItemPage | None) -> None:
        self.next_button.setEnabled(page is not None and page.next_after is not None)
        if page is None:
            self.items.setPlainText("격리 항목을 읽지 못함. 앱을 다시 시작하세요.")
            return
        self.items.setPlainText(
            "\n\n".join(
                f"서버: {item.server_id} · 도구: {item.tool_name}\n"
                f"목적지: {item.destination_id or '미제공'} · "
                f"종류: {item.kind.value if item.kind is not None else '미제공'} · "
                f"epoch: {item.epoch if item.epoch is not None else '미제공'}\n"
                f"분류: {item.category.value} · 원래 상태: {item.original_status.value}"
                for item in page.items
            )
            or "격리 항목 없음"
        )

    def _next(self) -> None:
        self.next_button.setEnabled(False)
        self._bridge.load_recovery_page()
