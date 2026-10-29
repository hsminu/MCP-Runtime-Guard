"""Qt item rendering, separate from approval query/selection ownership."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem


class ApprovalTable(QTableWidget):
    def insert_summary(self, index: int, columns: tuple[str, ...]) -> None:
        self.insertRow(index)
        for column, value in enumerate(columns):
            self.setItem(index, column, QTableWidgetItem(value))

    def update_status(self, index: int, display: tuple[str, int, str, str]) -> None:
        item = self.item(index, 4)
        if item is None:
            return
        text, height, tooltip, accessible = display
        item.setText(text)
        self.setRowHeight(index, height)
        item.setToolTip(tooltip)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible)
