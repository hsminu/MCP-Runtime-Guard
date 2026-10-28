"""Shared Qt style with light and dark palettes from Orca's and Paradev's tokens."""

from __future__ import annotations

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QWidget

# Light follows Orca's neutral shadcn scale (stablyai/orca, renderer main.css);
# dark follows Paradev's "ember" theme (ParadevAI/Paradev, desktop globals.css).
# Ink is the primary action in both, and selection stays neutral as in both
# references. The ember accent is reserved for the brand mark and work that is
# running now (a running session, its status pill, a streaming answer); the
# style audit test keeps it there.
# Status and security-label colors keep their meaning in both palettes; a status
# is always words in a tinted pill, never color alone (technical design 12.4).
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "canvas": "#ffffff",
        "surface": "#ffffff",
        "raised": "#fafafa",
        "popover": "#ffffff",
        "sidebar": "#f7f7f8",
        "hover": "#efeff1",
        "selected": "#e9e9ec",
        "selected_line": "#d9d9de",
        "selection": "#dcdce2",
        "bubble": "#f4f4f5",
        "bubble_line": "#e7e7ea",
        "disabled_bg": "#f4f4f5",
        "hairline": "#ececee",
        "hairline_strong": "#e0e0e4",
        "line_hover": "#c4c4cb",
        "scroll": "#d6d6db",
        "scroll_hover": "#a9a9b1",
        "ink": "#18181b",
        "ink_hover": "#2c2c31",
        "ink_pressed": "#09090b",
        "on_ink": "#fafafa",
        "body": "#3f3f46",
        "mute": "#71717a",
        "subtle": "#a1a1aa",
        "accent": "#d9622f",
        "accent_soft": "#fdeee7",
        "accent_line": "#f5cdb9",
        "on_accent": "#ffffff",
        "danger": "#c8312b",
        "danger_soft": "#fdeceb",
        "danger_line": "#f2c7c4",
        "warn": "#b26b00",
        "warn_soft": "#fbf4e4",
        "warn_line": "#ecd8ad",
        "ok": "#2f8a57",
        "ok_soft": "#eaf5ee",
        "untrusted_bg": "#fbfbfc",
        "public": "#2563a6",
        "public_soft": "#eef4fb",
        "public_line": "#c9dcf0",
        "internal": "#8a5a00",
        "internal_soft": "#fbf4e4",
        "internal_line": "#ecd8ad",
        "confidential": "#b4232a",
        "confidential_soft": "#fcedee",
        "confidential_line": "#f1c6c8",
    },
    "dark": {
        "canvas": "#151110",
        "surface": "#1e1a19",
        "raised": "#201e1c",
        "popover": "#201e1c",
        "sidebar": "#1a1716",
        "hover": "#24201f",
        "selected": "#2c2827",
        "selected_line": "#3c3836",
        "selection": "#433e3c",
        "bubble": "#25211f",
        "bubble_line": "#322e2c",
        "disabled_bg": "#1d1a19",
        "hairline": "#2a2827",
        "hairline_strong": "#363230",
        "line_hover": "#4d4846",
        "scroll": "#3a3634",
        "scroll_hover": "#5a5552",
        "ink": "#eae8e6",
        "ink_hover": "#f6f4f2",
        "ink_pressed": "#d4d1ce",
        "on_ink": "#151110",
        "body": "#cfccc9",
        "mute": "#a8a5a3",
        "subtle": "#6f6a67",
        "accent": "#e07850",
        "accent_soft": "#33201a",
        "accent_line": "#5a3324",
        "on_accent": "#151110",
        "danger": "#f07167",
        "danger_soft": "#351b19",
        "danger_line": "#62302c",
        "warn": "#e5c07b",
        "warn_soft": "#2b2316",
        "warn_line": "#4d3d22",
        "ok": "#5fb37f",
        "ok_soft": "#18271e",
        "untrusted_bg": "#191514",
        "public": "#8cc0e6",
        "public_soft": "#17242d",
        "public_line": "#2a4252",
        "internal": "#e5c07b",
        "internal_soft": "#2b2316",
        "internal_line": "#4d3d22",
        "confidential": "#f08a80",
        "confidential_soft": "#331a18",
        "confidential_line": "#5c2c28",
    },
}
_SANS = ["Segoe UI Variable Text", "Segoe UI", "Noto Sans KR", "Malgun Gothic"]
_MONO = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Noto Sans KR", "Malgun Gothic"]
_FONTS = {
    "sans": ", ".join(f'"{family}"' for family in _SANS) + ", sans-serif",
    "mono": ", ".join(f'"{family}"' for family in _MONO) + ", monospace",
}
# The current palette. Painted widgets read it at paint time, so a theme
# change updates it in place instead of rebinding the name.
TOKENS: dict[str, str] = {**PALETTES["light"], **_FONTS}
_current = ["light"]

_SHEET = """
QWidget {
    background: transparent;
    color: $ink;
    font-family: $sans;
    font-size: 14px;
    selection-background-color: $selection;
    selection-color: $ink;
}
QMainWindow, QScrollArea { background: $canvas; }
QWidget#conversationPage { background: $canvas; }
QFrame#sidebar { background: $sidebar; border-right: 1px solid $hairline; }
QFrame#topbar { background: $canvas; border-bottom: 1px solid $hairline; }
QLabel#breadcrumb { color: $mute; font-size: 12px; }
QListWidget#sessionList { border: none; background: transparent; outline: none; }
QLineEdit#sidebarSearch { background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 4px 2px; min-height: 18px; font-size: 13px; color: $body; }
QLineEdit#sidebarSearch:hover { background: $hover; border-color: transparent; }
QLineEdit#sidebarSearch:focus { background: $surface; border-color: $hairline_strong; }
QPushButton#newSessionButton { background: transparent; border: 1px solid transparent;
    border-radius: 6px; text-align: left; padding: 5px 8px; min-height: 18px;
    font-size: 13px; font-weight: 500; color: $ink; }
QPushButton#newSessionButton:hover { background: $hover; border-color: transparent; }
QPushButton#newSessionButton:focus { border-color: $line_hover; }
QWidget#sessionHead { border-bottom: 1px solid $hairline; }
QScrollArea#conversationTranscript, QWidget#transcriptContent {
    background: $canvas; border: none; }
QFrame#userBubble { background: $bubble; border: 1px solid $bubble_line; border-radius: 12px; }
QLabel#userText { font-size: 14px; color: $ink; }
QLabel#answerText { font-size: 14px; color: $body; }
QLabel#answerLabel { font-size: 12px; color: $mute; }
QLabel#answerLabel[tone="live"] { color: $accent; }
QLabel#answerLabel[tone="stopped"] { color: $danger; }
QLabel#emptyTitle { font-size: 15px; font-weight: 600; color: $ink; }
QLabel#emptyDetail { font-size: 13px; color: $mute; }
QLabel#sessionTitle { font-size: 13px; font-weight: 600; color: $ink; }
QLabel#sessionMeta { color: $mute; font-size: 12px; }
QLabel#statusPill { color: $mute; background: $bubble; border: 1px solid $hairline_strong;
    border-radius: 10px; padding: 1px 8px 1px 17px; font-size: 12px; font-weight: 500; }
QLabel#statusPill[tone="live"] { color: $accent; background: $accent_soft;
    border-color: $accent_line; }
QLabel#statusPill[tone="attn"] { color: $warn; background: $warn_soft;
    border-color: $warn_line; }
QLabel#statusPill[tone="deny"] { color: $danger; background: $danger_soft;
    border-color: $danger_line; }
QLabel#statusPill[tone="ok"] { color: $ok; background: $ok_soft;
    border-color: transparent; }
QLabel#unverifiedNotice { color: $warn; font-size: 11px; font-weight: 600; }
QPlainTextEdit#untrustedBody { background: $untrusted_bg; border: 1px dashed $line_hover;
    border-radius: 8px; font-family: $sans; font-size: 12px; }
QLabel#untrustedOmission { color: $mute; font-size: 11px; }
QLabel#sessionNotice { color: $mute; font-size: 12px; padding: 0 2px; }
QFrame#approvalBand { background: $warn_soft; border: 1px solid $warn_line;
    border-left: 3px solid $warn; border-radius: 8px; }
QLabel#approvalBandTitle { font-size: 13px; font-weight: 600; color: $ink; }
QLabel#approvalBandDetail { font-size: 12px; color: $body; }
QLabel#sessionFootnote { color: $subtle; font-size: 11px; padding: 0 2px; }
QFrame#composer { border: 1px solid $hairline_strong; background: $surface; border-radius: 12px; }
QPlainTextEdit#messageDraft { border: none; padding: 0; background: transparent;
    font-family: $sans; font-size: 14px; }
QPushButton#modelChip { color: $body; background: transparent; border: 1px solid $hairline_strong;
    border-radius: 6px; padding: 3px 8px; font-size: 12px; min-height: 0; }
QPushButton#modelChip:hover { background: $hover; color: $ink; }
QPushButton#modelChip:focus { border-color: $mute; }
QPushButton#modelChip:disabled { background: transparent; color: $mute; }
QPushButton#modelChip::menu-indicator { width: 0; image: none; }
QLabel { background: transparent; }
QLabel#pageTitle { font-size: 20px; font-weight: 600; color: $ink; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#secondary, QLabel#stateMessage { color: $mute; font-size: 12px; }
QLabel#navCaption { color: $mute; font-size: 11px; font-weight: 500; padding: 0 8px; }
QLabel#brandTitle { font-size: 13px; font-weight: 600; }
QLabel#brandMark { background: $accent; border-radius: 5px; }
QFrame#metricList, QFrame#rowList {
    background: $surface; border: 1px solid $hairline; border-radius: 10px; }
QFrame#metricRow, QFrame#listRow { border-bottom: 1px solid $hairline; }
QFrame#metricRow[last="true"], QFrame#listRow[last="true"] { border-bottom: none; }
QLabel#labelPill { color: $mute; background: $bubble; border: 1px solid $hairline_strong;
    border-radius: 9px; padding: 0 8px; font-size: 12px; font-weight: 500; }
QLabel#labelPill[labelLevel="public"] { color: $public; background: $public_soft;
    border-color: $public_line; }
QLabel#labelPill[labelLevel="internal"] { color: $internal; background: $internal_soft;
    border-color: $internal_line; }
QLabel#labelPill[labelLevel="confidential"] { color: $confidential;
    background: $confidential_soft; border-color: $confidential_line; }
QLabel#metricValue { font-size: 14px; font-weight: 600; color: $ink; }
QLabel#metricValue[unavailable="true"] { font-weight: 400; color: $mute; }
QLabel#cardTitle { font-size: 13px; font-weight: 600; }
QLabel#mono { font-family: $mono; font-size: 12px; }
QLabel#modeBadge { color: $mute; background: transparent; border: 1px solid $hairline_strong;
    border-radius: 6px; padding: 2px 8px; font-size: 12px; }
QLabel[validationError="true"] { color: $danger; font-size: 12px; }
QLabel#folderRootError, QLabel#folderRulesError, QLabel#folderRuleError {
    color: $danger; font-size: 12px; }
QLabel#draftStatus, QLabel#folderDraftStatus, QLabel#boundaryNotice {
    color: $body; font-size: 12px; }
QFrame#placeholderCard {
    background: $surface;
    border: 1px solid $hairline;
    border-radius: 10px;
}
QFrame#taskStatusSection, QFrame#runtimeCard, QFrame#approvalCard {
    background: transparent; border: none; border-bottom: 1px solid $hairline_strong; }
QFrame#approvalCard[attention="true"] { background: $warn_soft; border-left: 3px solid $warn; }
QFrame#stopCard { background: transparent; border: none; }
QFrame#resultsFooter { border-top: 1px solid $hairline_strong; }
QFrame#activityPane { border-left: 1px solid $hairline; }
QFrame#paneHead { border-bottom: 1px solid $hairline; }
QLabel#paneTitle { font-size: 13px; font-weight: 600; color: $ink; }
QLabel#runtimeMessage { color: $body; font-size: 12px; }
QFrame#securityBadge { border-radius: 8px; border: 1px solid $hairline; background: $raised; }
QFrame#securityBadge[labelLevel="public"] { border-color: $public_line; background: $public_soft; }
QFrame#securityBadge[labelLevel="internal"] {
    border-color: $internal_line; background: $internal_soft;
}
QFrame#securityBadge[labelLevel="confidential"] {
    border-color: $confidential_line; background: $confidential_soft;
}
QLabel#securityValue { font-size: 17px; font-weight: 600; }
QLabel#securityValue[labelLevel="public"] { color: $public; }
QLabel#securityValue[labelLevel="internal"] { color: $internal; }
QLabel#securityValue[labelLevel="confidential"] { color: $confidential; }
QWidget#infoField { background: transparent; }
QWidget#detailBody { background: $canvas; }
QPushButton {
    min-height: 20px;
    padding: 6px 12px;
    border: 1px solid $hairline_strong;
    border-radius: 6px;
    background: $surface;
    color: $ink;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover { background: $hover; border-color: $line_hover; }
QPushButton:pressed { background: $selected; }
QPushButton[variant="primary"] { background: $ink; color: $on_ink; border-color: $ink; }
QPushButton[variant="primary"]:hover { background: $ink_hover; border-color: $ink_hover; }
QPushButton[variant="primary"]:pressed { background: $ink_pressed; border-color: $ink_pressed; }
QPushButton[variant="soft"] { color: $ink; background: $hover; border-color: transparent; }
QPushButton[variant="soft"]:hover { background: $selected; border-color: transparent; }
QPushButton[variant="danger"] { color: $danger; background: $surface; border-color: $danger_line; }
QPushButton[variant="danger"]:hover { background: $danger_soft; border-color: $danger; }
QPushButton:focus { border: 1px solid $mute; }
QPushButton[variant="primary"]:focus { border: 1px solid $subtle; }
QPushButton:disabled, QPushButton[variant]:disabled {
    color: $subtle; background: $disabled_bg; border-color: $hairline;
}
QPushButton#navButton { border: 1px solid transparent; background: transparent;
    text-align: left; min-height: 18px; padding: 4px 8px; color: $body; font-size: 13px;
    font-weight: 400; border-radius: 6px; }
QPushButton#navButton:hover { background: $hover; color: $ink; }
QPushButton#navButton[selected="true"] { color: $ink; background: $selected;
    border-color: $selected_line; font-weight: 500; }
QPushButton#navButton:focus { border-color: $line_hover; }
QPushButton#ghostButton, QPushButton#iconButton { border: 1px solid transparent;
    background: transparent; color: $body; min-height: 18px; padding: 4px 8px;
    font-size: 13px; border-radius: 6px; }
QPushButton#iconButton { padding: 4px; }
QPushButton#ghostButton:hover, QPushButton#iconButton:hover { background: $hover; color: $ink; }
QPushButton#ghostButton[selected="true"] { background: $selected; border-color: $selected_line;
    color: $ink; }
QPushButton#ghostButton:focus, QPushButton#iconButton:focus { border-color: $line_hover; }
QPushButton#ghostButton:disabled { background: transparent; border-color: transparent; }
QPushButton#sendButton { min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    padding: 0; border-radius: 6px; }
QPushButton#composerButton { min-height: 16px; padding: 5px 10px; border-radius: 6px;
    font-size: 12px; }
QPushButton#addServerButton { background: $hover; color: $ink; border-color: transparent; }
QPushButton#addServerButton:hover, QPushButton#addServerButton[selected="true"] {
    background: $selected; border-color: transparent;
}
QPushButton#linkButton { color: $body; border: 1px solid transparent; background: transparent;
    text-align: left; min-height: 20px; padding: 6px 0; font-size: 12px;
    text-decoration: underline; }
QPushButton#linkButton:hover { color: $ink; background: transparent; }
QPushButton#linkButton:focus { border-color: $line_hover; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 3px 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0 3px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: $scroll;
    border-radius: 4px; }
QScrollBar::handle:vertical { min-height: 28px; }
QScrollBar::handle:horizontal { min-width: 28px; }
QScrollBar::handle:hover { background: $scroll_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent;
    border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QLineEdit, QComboBox, QPlainTextEdit {
    border: 1px solid $hairline_strong; border-radius: 6px; background: $surface;
    padding: 7px 10px; font-size: 13px;
}
QLineEdit, QComboBox { min-height: 20px; }
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover { border-color: $line_hover; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border-color: $mute; background: $surface;
}
QLineEdit:disabled, QComboBox:disabled { color: $subtle; background: $disabled_bg; }
QPlainTextEdit { font-family: $mono; font-size: 13px; }
QPlainTextEdit[readOnly="true"] { background: $raised; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: $popover; border: 1px solid $hairline_strong;
    padding: 4px; selection-background-color: $selected; selection-color: $ink; outline: 0; }
QTableWidget { background: $surface; alternate-background-color: $surface;
    border: 1px solid $hairline; border-radius: 10px; font-size: 13px;
    qproperty-showGrid: false; }
QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid $hairline; }
QTableWidget::item:selected { background: $selected; color: $ink; }
QHeaderView { background: transparent; border: none;
    qproperty-defaultAlignment: "AlignLeft|AlignVCenter"; }
QHeaderView::section { background: transparent; color: $mute; padding: 8px 10px;
    border: none; border-bottom: 1px solid $hairline_strong; font-size: 12px;
    font-weight: 500; }
QTableCornerButton::section { background: transparent; border: none; }
QTabBar::tab { background: transparent; color: $mute; border: 1px solid transparent;
    border-radius: 6px; padding: 6px 12px; margin-right: 4px; font-size: 13px; }
QTabBar::tab:hover { background: $hover; color: $ink; }
QTabBar::tab:selected { background: $selected; color: $ink; border-color: $selected_line; }
QTabBar::tab:focus { border-color: $line_hover; }
QMenu { background: $popover; border: 1px solid $hairline_strong; border-radius: 8px;
    padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 5px; }
QMenu::item:selected { background: $hover; color: $ink; }
QMenu::item:disabled { color: $mute; font-size: 11px; }
QMenu::separator { height: 1px; background: $hairline; margin: 4px 6px; }
QToolTip { background: $popover; color: $ink; border: 1px solid $hairline_strong;
    padding: 4px 8px; }
QDialog { background: $canvas; }
"""


def style_sheet(theme: str = "light") -> str:
    """The sheet for one palette; unknown names are refused, not defaulted."""
    return Template(_SHEET).substitute({**PALETTES[theme], **_FONTS})


STYLE = style_sheet("light")


def current_theme() -> str:
    return _current[0]


def system_theme() -> str:
    """The platform's color scheme; anything but an explicit dark is light."""
    hints = QGuiApplication.styleHints()
    return "dark" if hints.colorScheme() is Qt.ColorScheme.Dark else "light"


def set_theme(theme: str) -> None:
    """Switch the shared palette in place; callers re-apply the sheet."""
    TOKENS.update(PALETTES[theme])
    _current[0] = theme


def ui_font(
    pixel_size: int = 14, weight: QFont.Weight = QFont.Weight.Normal, *, mono: bool = False
) -> QFont:
    """Fonts for custom-painted text, matching the families in the sheet."""
    font = QFont()
    font.setFamilies(_MONO if mono else _SANS)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def apply_style(window: QWidget) -> None:
    window.setFont(ui_font())
    window.setStyleSheet(style_sheet(current_theme()))
