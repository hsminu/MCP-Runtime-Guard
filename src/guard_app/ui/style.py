"""Shared Qt components using the colors and hierarchy of ui-preview."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

STYLE = """
QWidget {
    background: transparent;
    color: #25293d;
    font-family: "Segoe UI", "Malgun Gothic", sans-serif;
    font-size: 14px;
    selection-background-color: #e9e5ff;
    selection-color: #35297d;
}
QMainWindow, QScrollArea { background: #ffffff; }
QWidget#conversationPage { background: #ffffff; }
QFrame#sidebar { background: #f5f5f8; }
QListWidget#sessionList { border: none; background: transparent; outline: none; }
QListWidget#sessionList::item { border-radius: 9px; padding: 12px 8px; color: #656775; }
QListWidget#sessionList::item:selected { color: #6848b8; background: #eee8fa; }
QPlainTextEdit#conversationTranscript { border: none; background: #ffffff;
    font-family: "Segoe UI", "Malgun Gothic"; font-size: 15px; padding: 8px 0; }
QFrame#composer { border: 1px solid #e6e6ed; background: #ffffff; border-radius: 14px; }
QPlainTextEdit#messageDraft { border: none; padding: 0; background: #ffffff;
    font-family: "Segoe UI", "Malgun Gothic"; font-size: 15px; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #6b5bd4; font-size: 11px; font-weight: 700; }
QLabel#pageTitle { font-size: 28px; font-weight: 700; color: #24283e; }
QLabel#sectionTitle { font-size: 19px; font-weight: 700; }
QLabel#secondary, QLabel#stateMessage { color: #6f788a; font-size: 12px; }
QLabel#navCaption { color: #8b92a3; font-size: 11px; font-weight: 600; padding: 0 12px; }
QLabel#brandTitle { font-size: 18px; font-weight: 700; }
QLabel#brandCaption { color: #8890a2; font-size: 10px; }
QLabel#brandMark { background: #6658d7; border-radius: 12px; }
QLabel#metricValue { font-size: 30px; font-weight: 700; color: #292d43; }
QLabel#metricValue[unavailable="true"] { font-size: 24px; color: #737b8f; }
QLabel#metricIcon { background: #f2efff; border-radius: 10px; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; }
QLabel#mono { font-family: Consolas, monospace; font-size: 12px; }
QLabel#modeBadge { color: #6556c8; background: #f1effc; border-radius: 10px;
    padding: 6px 10px; font-size: 11px; }
QLabel[validationError="true"] { color: #b34251; font-size: 12px; }
QFrame#sidebar, QFrame#topbar { background: #ffffff; }
QFrame#sidebar { border-right: 1px solid #e9eaf1; }
QFrame#topbar { border-bottom: 1px solid #e9eaf1; }
QFrame#sidebarFooter { border-top: 1px solid #ececf3; }
QFrame#metricCard, QFrame#serverCard, QFrame#attentionCard,
QFrame#recentCard, QFrame#flowCard, QFrame#placeholderCard,
QFrame#runtimeCard, QFrame#approvalCard {
    background: #ffffff;
    border: 1px solid #e4e6ef;
    border-radius: 14px;
}
QFrame#runtimeCard { border-top: 2px solid #8c7fe4; }
QFrame#stopCard { background: #f9f9fc; border: 1px solid #e4e6ef; border-radius: 12px; }
QLabel#runtimeMessage { color: #655b8d; background: #f5f3fc; border-radius: 8px;
    padding: 10px 12px; font-size: 12px; }
QFrame#securityBadge { border-radius: 10px; border: 1px solid #e0e3ec; background: #f5f6fa; }
QFrame#securityBadge[labelLevel="public"] { border-color: #d7e7f4; background: #eff7ff; }
QFrame#securityBadge[labelLevel="internal"] { border-color: #f0dfba; background: #fff8e9; }
QFrame#securityBadge[labelLevel="confidential"] { border-color: #f0d2db; background: #fff2f5; }
QLabel#securityValue { font-size: 18px; font-weight: 700; }
QLabel#securityValue[labelLevel="public"] { color: #24638a; }
QLabel#securityValue[labelLevel="internal"] { color: #855b16; }
QLabel#securityValue[labelLevel="confidential"] { color: #9b263f; }
QWidget#infoField { background: transparent; }
QWidget#detailBody { background: #ffffff; }
QPushButton {
    min-height: 22px;
    padding: 8px 14px;
    border: 1px solid #dfe2eb;
    border-radius: 9px;
    background: #ffffff;
    color: #535c70;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background: #f8f7fd; border-color: #c1b9ed; color: #5949c0; }
QPushButton:pressed { background: #eeeafa; border-color: #a398df; }
QPushButton[variant="primary"] { background: #6658d7; color: #ffffff; border-color: #6658d7; }
QPushButton[variant="primary"]:hover { background: #594bc8; border-color: #594bc8; }
QPushButton[variant="primary"]:pressed { background: #4d3fb6; }
QPushButton[variant="soft"] { color: #6251cc; background: #f3f0ff; border-color: #e0dafb; }
QPushButton[variant="soft"]:hover { background: #ebe5ff; border-color: #c7bdf4; }
QPushButton[variant="danger"] { color: #b74455; background: #fff; border-color: #ecced5; }
QPushButton[variant="danger"]:hover { background: #fff0f3; border-color: #d899a7; }
QPushButton:focus { border: 1px solid #8071df; }
QPushButton[variant="primary"]:focus { border: 1px solid #30236e; }
QPushButton:disabled, QPushButton[variant]:disabled {
    color: #8c93a5; background: #f2f3f7; border-color: #e7e9ef;
}
QPushButton#navButton { border: 1px solid transparent; background: transparent;
    text-align: left; min-height: 26px; padding: 8px 12px; color: #626a7f; font-weight: 400; }
QPushButton#navButton:hover { background: #f7f6fc; color: #594bc8; }
QPushButton#navButton[selected="true"] { color: #594bc8; background: #eeebfc; font-weight: 600; }
QPushButton#navButton:focus { border-color: #a69be8; }
QPushButton#addServerButton { background: #f6f3ff; color: #6251cc; border-color: #e1dcf7; }
QPushButton#addServerButton:hover, QPushButton#addServerButton[selected="true"] {
    background: #ece7ff; border-color: #c9bff1;
}
QPushButton#linkButton { color: #6554cb; border: 1px solid transparent;
    background: transparent; text-align: left; min-height: 20px; padding: 6px 0; font-size: 12px; }
QPushButton#linkButton:hover { color: #4936b2; background: #f8f6ff; }
QPushButton#linkButton:focus { border-color: #a69be8; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 3px 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0 3px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #cdd1df;
    border-radius: 4px; }
QScrollBar::handle:vertical { min-height: 28px; }
QScrollBar::handle:horizontal { min-width: 28px; }
QScrollBar::handle:hover { background: #a8adc4; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent;
    border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QLineEdit, QComboBox, QPlainTextEdit {
    border: 1px solid #dde1eb; border-radius: 9px; background: #ffffff;
    padding: 9px 12px; font-size: 13px;
}
QLineEdit, QComboBox { min-height: 22px; }
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover { border-color: #c0badb; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #8776e2;
    background: #fefeff; }
QLineEdit:disabled, QComboBox:disabled { color: #9299a9; background: #f4f5f8; }
QPlainTextEdit { font-family: Consolas, "Malgun Gothic", monospace; font-size: 13px; }
QPlainTextEdit[readOnly="true"] { background: #fafbfe; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #e1ddec;
    padding: 6px; selection-background-color: #efebff; selection-color: #5543ba; outline: 0; }
QTableWidget { background: #ffffff; alternate-background-color: #fafbfe;
    border: 1px solid #e4e6ef; border-radius: 10px; gridline-color: #f0f1f6; font-size: 13px; }
QTableWidget::item { padding: 9px; border-bottom: 1px solid #f0f1f6; }
QTableWidget::item:selected { background: #f0edff; color: #493d8f; }
QHeaderView::section { background: #f8f9fc; color: #7b8396; padding: 10px;
    border: none; border-bottom: 1px solid #e7e9f1; font-size: 11px; font-weight: 600; }
QTableCornerButton::section { background: #f8f9fc; border: none; }
QTabBar::tab { background: #f0f1f6; color: #747d92; border: 1px solid transparent;
    border-radius: 8px; padding: 9px 18px; margin-right: 5px; font-size: 13px; }
QTabBar::tab:selected { background: #eeebfc; color: #594bc8; border-color: #ded8f8; }
QTabBar::tab:hover { background: #f5f2ff; }
QTabBar::tab:focus { border-color: #a69be8; }
QMenu { background: #ffffff; border: 1px solid #e3dfef; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 22px; border-radius: 6px; }
QMenu::item:selected { background: #efebff; color: #594bc8; }
QToolTip { background: #303047; color: #ffffff; border: none; padding: 6px 10px; }
QDialog { background: #ffffff; }
"""


def apply_style(window: QWidget) -> None:
    font = QFont("Segoe UI")
    font.setPixelSize(14)
    window.setFont(font)
    window.setStyleSheet(STYLE)
