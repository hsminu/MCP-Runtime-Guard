"""Shared Qt type scale and colors based on the HTML review prototype."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

STYLE = """
QWidget {
    background: #f7f8fa;
    color: #202537;
    font-family: "Segoe UI", "Malgun Gothic", sans-serif;
    font-size: 11pt;
}
QLabel { background: transparent; }
QLabel#eyebrow { color: #6658db; font-size: 10pt; font-weight: 700; }
QLabel#pageTitle { font-size: 22pt; font-weight: 700; }
QLabel#sectionTitle { font-size: 15pt; font-weight: 700; }
QLabel#secondary, QLabel#stateMessage { color: #586477; font-size: 10pt; }
QLabel#metricValue { font-size: 25pt; font-weight: 700; }
QLabel#cardTitle { font-size: 12pt; font-weight: 700; }
QLabel#mono { font-family: Consolas, monospace; font-size: 10pt; }
QFrame#sidebar, QFrame#topbar { background: #ffffff; }
QFrame#sidebar { border-right: 1px solid #e1e5ed; }
QFrame#topbar { border-bottom: 1px solid #e1e5ed; }
QFrame#metricCard, QFrame#serverCard, QFrame#attentionCard,
QFrame#recentCard, QFrame#flowCard, QFrame#placeholderCard {
    background: #ffffff;
    border: 1px solid #dce2ea;
    border-radius: 10px;
}
QFrame#securityBadge { border-radius: 8px; border: 1px solid #d4dbe6; background: #f2f4f8; }
QFrame#securityBadge[labelLevel="public"] { border: 1px solid #c1d9ed; border-left: 4px solid #175e88; background: #eaf5ff; }
QFrame#securityBadge[labelLevel="internal"] { border: 1px solid #ead09c; border-left: 4px solid #855b16; background: #fff4dc; }
QFrame#securityBadge[labelLevel="confidential"] { border: 1px solid #edbec8; border-left: 4px solid #9b263f; background: #fff0f3; }
QLabel#securityValue { font-size: 16pt; font-weight: 700; }
QLabel#securityValue[labelLevel="public"] { color: #175e88; }
QLabel#securityValue[labelLevel="internal"] { color: #855b16; }
QLabel#securityValue[labelLevel="confidential"] { color: #9b263f; }
QWidget#infoField { background: transparent; }
QWidget#detailBody { background: #ffffff; }
QPushButton {
    min-height: 38px;
    padding: 4px 12px;
    border: 1px solid #cfd6e2;
    border-radius: 7px;
    background: #ffffff;
    color: #344157;
    font-size: 11pt;
    font-weight: 600;
}
QPushButton:hover { background: #f4f2ff; border-color: #a99cf0; }
QPushButton:focus { border: 2px solid #6658db; }
QPushButton:disabled { color: #768093; background: #f2f4f8; border-color: #e1e5ed; }
QPushButton#navButton { border: none; background: transparent; text-align: left; min-height: 42px; }
QPushButton#navButton:hover { background: #f4f2ff; }
QPushButton#navButton[selected="true"] { color: #5143be; background: #f0eeff; font-weight: 700; }
QPushButton#linkButton { color: #5143be; border: none; background: transparent; text-align: left; }
QPushButton#linkButton:hover { text-decoration: underline; }
QScrollArea { border: none; background: #f7f8fa; }
QScrollBar:vertical { width: 12px; }
QPlainTextEdit { background: #ffffff; border: 1px solid #cfd6e2; font-family: Consolas, monospace; font-size: 11pt; }
QLineEdit, QComboBox { min-height: 38px; padding: 3px 9px; border: 1px solid #cfd6e2; border-radius: 6px; background: #ffffff; font-size: 11pt; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #6658db; }
QTableWidget { background: #ffffff; border: 1px solid #dce2ea; border-radius: 8px; gridline-color: #e8ebf0; font-size: 10pt; }
QTableWidget::item { padding: 8px; }
QTableWidget::item:selected { background: #e9e5ff; color: #202537; }
QHeaderView::section { background: #f4f6f9; color: #344157; padding: 8px; border: 1px solid #e1e5ed; font-size: 10pt; font-weight: 700; }
QDialog { background: #ffffff; }
"""


def apply_style(window: QWidget) -> None:
    font = QFont("Segoe UI")
    font.setPointSize(11)
    window.setFont(font)
    window.setStyleSheet(STYLE)
