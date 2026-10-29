"""Shared Qt tokens: blue actions and selection, distinct status and grade shapes."""

from __future__ import annotations

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QWidget

# Light keeps the neutral gray structure referenced by Orca and Paradev.
# Blue identifies primary actions, focus and selection in both themes.
# Status uses green success, orange attention and red refusal/error, with
# distinct shapes and explicit words. Security grades use outlined shield
# tags in teal, purple and magenta, separate from rounded status pills.
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "canvas": "#ffffff",
        "surface": "#ffffff",
        "raised": "#f8fafc",
        "popover": "#ffffff",
        "sidebar": "#f8fafc",
        "hover": "#f1f5f9",
        "selected": "#eff6ff",
        "selected_line": "#bfdbfe",
        "selection": "#bfdbfe",
        "bubble": "#f1f5f9",
        "bubble_line": "#e2e8f0",
        "disabled_bg": "#f1f5f9",
        "hairline": "#e5e7eb",
        "hairline_strong": "#d1d5db",
        "field_line": "#b6bfcc",
        "line_hover": "#94a3b8",
        "scroll": "#cbd5e1",
        "scroll_hover": "#94a3b8",
        "ink": "#111827",
        "ink_hover": "#1f2937",
        "ink_pressed": "#030712",
        "on_ink": "#ffffff",
        "body": "#1f2937",
        "mute": "#475569",
        "subtle": "#64748b",
        "disabled_text": "#94a3b8",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_pressed": "#1e40af",
        "on_primary": "#ffffff",
        "primary_text": "#1d4ed8",
        "primary_soft": "#eff6ff",
        "primary_line": "#bfdbfe",
        "ok": "#15803d",
        "ok_soft": "#f0fdf4",
        "ok_line": "#bbf7d0",
        "warn": "#c2410c",
        "warn_soft": "#fff7ed",
        "warn_line": "#fed7aa",
        "danger": "#b91c1c",
        "danger_soft": "#fef2f2",
        "danger_line": "#fecaca",
        "untrusted_bg": "#f8fafc",
        "public": "#0f766e",
        "public_soft": "#f0fdfa",
        "public_line": "#99f6e4",
        "internal": "#6d28d9",
        "internal_soft": "#f5f3ff",
        "internal_line": "#ddd6fe",
        "confidential": "#a21caf",
        "confidential_soft": "#fdf4ff",
        "confidential_line": "#f5d0fe",
    },
    "dark": {
        "canvas": "#0f1115",
        "surface": "#16191f",
        "raised": "#1b1f26",
        "popover": "#1b1f26",
        "sidebar": "#13161b",
        "hover": "#1f242c",
        "selected": "#18233a",
        "selected_line": "#2a4170",
        "selection": "#2a4170",
        "bubble": "#1f242c",
        "bubble_line": "#2c323c",
        "disabled_bg": "#1a1d23",
        "hairline": "#252a33",
        "hairline_strong": "#313844",
        "field_line": "#4a5361",
        "line_hover": "#6b7483",
        "scroll": "#343b47",
        "scroll_hover": "#4b5563",
        "ink": "#f3f4f6",
        "ink_hover": "#ffffff",
        "ink_pressed": "#e5e7eb",
        "on_ink": "#0f1115",
        "body": "#e2e8f0",
        "mute": "#aab4c3",
        "subtle": "#8b95a5",
        "disabled_text": "#5b6473",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_pressed": "#1e40af",
        "on_primary": "#ffffff",
        "primary_text": "#8ab4ff",
        "primary_soft": "#172341",
        "primary_line": "#294a85",
        "ok": "#4ade80",
        "ok_soft": "#10241a",
        "ok_line": "#1e4d33",
        "warn": "#fb923c",
        "warn_soft": "#2a1a0c",
        "warn_line": "#5c3511",
        "danger": "#f87171",
        "danger_soft": "#2b1215",
        "danger_line": "#5c2228",
        "untrusted_bg": "#12151a",
        "public": "#2dd4bf",
        "public_soft": "#0d2622",
        "public_line": "#1a4d46",
        "internal": "#a78bfa",
        "internal_soft": "#1e1838",
        "internal_line": "#3d2f72",
        "confidential": "#e879f9",
        "confidential_soft": "#2a1230",
        "confidential_line": "#5a2566",
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
    border-radius: 6px; padding: 7px 10px; min-height: 18px; font-size: 13px; color: $body; }
QLineEdit#sidebarSearch:hover { background: $hover; border-color: transparent; }
QLineEdit#sidebarSearch:focus { background: $surface; border: 2px solid $primary;
    padding: 6px 9px; }
QPushButton#newSessionButton { background: transparent; border: 1px solid transparent;
    border-radius: 6px; text-align: left; padding: 6px 8px; min-height: 18px;
    font-size: 13px; font-weight: 500; color: $ink; }
QPushButton#newSessionButton:hover { background: $hover; border-color: transparent; }
QPushButton#newSessionButton:focus { border-color: $primary; }
QWidget#sessionHead { border-bottom: 1px solid $hairline; }
QScrollArea#conversationTranscript, QWidget#transcriptContent {
    background: $canvas; border: none; }
QFrame#userBubble { background: $bubble; border: 1px solid $bubble_line; border-radius: 12px; }
QLabel#userText { font-size: 14px; color: $ink; }
QLabel#answerHeading { font-size: 14px; font-weight: 600; color: $body; }
QLabel#answerHeading[level="1"] { font-size: 16px; }
QLabel#answerHeading[level="2"] { font-size: 15px; }
QLabel#answerQuote { border-left: 3px solid $hairline_strong; padding-left: 10px; color: $body; }
QFrame#answerRule { background: $hairline_strong; }
QFrame#codeBlock { background: $raised; border: 1px solid $hairline_strong; border-radius: 6px; }
QPlainTextEdit#codeText { background: $raised; border: none; font-size: 13px; }
QPushButton#jumpLatest { background: $raised; color: $body; border: 1px solid $hairline_strong;
    border-radius: 12px; padding: 6px 12px; }
QLabel#answerText { font-size: 14px; color: $body; }
QLabel#answerLabel { font-size: 12px; color: $mute; }
QLabel#answerLabel[tone="live"] { color: $primary_text; }
QLabel#answerLabel[tone="stopped"] { color: $danger; }
QLabel#emptyTitle { font-size: 15px; font-weight: 600; color: $ink; }
QLabel#emptyDetail { font-size: 13px; color: $mute; }
QLabel#sessionTitle { font-size: 13px; font-weight: 600; color: $ink; }
QLabel#sessionMeta { color: $mute; font-size: 12px; }
QLabel#statusPill { color: $mute; background: $bubble; border: 1px solid $hairline_strong;
    border-radius: 10px; padding: 1px 8px 1px 17px; font-size: 12px; font-weight: 500; }
QLabel#statusPill[tone="live"] { color: $primary_text; background: $primary_soft;
    border-color: $primary_line; }
QLabel#statusPill[tone="attn"] { color: $warn; background: $warn_soft;
    border-color: $warn_line; }
QLabel#statusPill[tone="deny"] { color: $danger; background: $danger_soft;
    border-color: $danger_line; }
QLabel#statusPill[tone="ok"] { color: $ok; background: $ok_soft;
    border-color: $ok_line; }
QLabel#unverifiedNotice { color: $warn; font-size: 12px; font-weight: 600; }
QPlainTextEdit#untrustedBody { background: $untrusted_bg; border: 1px dashed $line_hover;
    border-radius: 8px; font-family: $sans; font-size: 12px; }
QLabel#untrustedOmission { color: $mute; font-size: 12px; }
QLabel#sessionNotice { color: $mute; font-size: 12px; padding: 0 2px; }
QFrame#approvalBand { background: $warn_soft; border: 1px solid $warn_line;
    border-left: 3px solid $warn; border-radius: 8px; }
QLabel#approvalBandTitle { font-size: 13px; font-weight: 600; color: $ink; }
QLabel#approvalBandDetail { font-size: 12px; color: $body; }
QLabel#sessionFootnote { color: $subtle; font-size: 12px; padding: 0 2px; }
QFrame#composer { border: 1px solid $hairline_strong; background: $surface; border-radius: 12px; }
QPlainTextEdit#messageDraft { border: none; padding: 0; background: transparent;
    font-family: $sans; font-size: 14px; }
QPushButton#modelChip { color: $body; background: transparent; border: 1px solid $hairline_strong;
    border-radius: 6px; padding: 6px 8px; font-size: 12px; min-height: 18px; }
QPushButton#modelChip:hover { background: $hover; color: $ink; }
QPushButton#modelChip:focus { border-color: $primary; }
QPushButton#modelChip:disabled { background: $disabled_bg; color: $disabled_text;
    border-color: $hairline; }
QPushButton#modelChip::menu-indicator { width: 0; image: none; }
QLabel { background: transparent; font-size: 14px; }
QLabel#pageTitle { font-size: 26px; font-weight: 600; color: $ink; }
QLabel#sectionTitle { font-size: 17px; font-weight: 600; }
QLabel#secondary, QLabel#stateMessage { color: $mute; font-size: 13px; }
QLabel#navCaption { color: $mute; font-size: 12px; font-weight: 500; padding: 0 8px; }
QLabel#brandTitle { font-size: 13px; font-weight: 600; }
QLabel#brandMark { background: $primary; border-radius: 6px; }
QFrame#metricList, QFrame#rowList {
    background: $surface; border: 1px solid $hairline; border-radius: 8px; }
QFrame#metricRow, QFrame#listRow { border-bottom: 1px solid $hairline; }
QFrame#metricRow[last="true"], QFrame#listRow[last="true"] { border-bottom: none; }
QLabel#labelPill { color: $mute; background: $surface; border: 1px solid $hairline_strong;
    border-radius: 4px; padding: 2px 8px 2px 24px; font-size: 12px; font-weight: 500; }
QLabel#labelPill[labelLevel="public"] { color: $public; background: $surface;
    border-color: $public_line; }
QLabel#labelPill[labelLevel="internal"] { color: $internal; background: $surface;
    border-color: $internal_line; }
QLabel#labelPill[labelLevel="confidential"] { color: $confidential;
    background: $surface; border-color: $confidential_line; }
QLabel#metricValue { font-size: 14px; font-weight: 600; color: $ink; }
QLabel#metricValue[unavailable="true"] { font-weight: 400; color: $mute; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
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
    border-radius: 8px;
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
QFrame#securityBadge { border-radius: 4px; border: 1px solid $hairline_strong;
    background: $surface; }
QFrame#securityBadge[labelLevel="public"] { border-color: $public_line; background: $surface; }
QFrame#securityBadge[labelLevel="internal"] {
    border-color: $internal_line; background: $surface;
}
QFrame#securityBadge[labelLevel="confidential"] {
    border-color: $confidential_line; background: $surface;
}
QLabel#securityValue { font-size: 17px; font-weight: 600; }
QLabel#securityValue[labelLevel="public"] { color: $public; }
QLabel#securityValue[labelLevel="internal"] { color: $internal; }
QLabel#securityValue[labelLevel="confidential"] { color: $confidential; }
QWidget#infoField { background: transparent; }
QWidget#detailBody { background: $canvas; }
QPushButton {
    min-height: 18px;
    padding: 6px 12px;
    border: 1px solid $field_line;
    border-radius: 6px;
    background: $surface;
    color: $ink;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover { background: $hover; border-color: $line_hover; }
QPushButton:pressed { background: $selected; }
QPushButton[variant="primary"] { background: $primary; color: $on_primary; border-color: $primary; }
QPushButton[variant="primary"]:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton[variant="primary"]:pressed { background: $primary_pressed;
    border-color: $primary_pressed; }
QPushButton[variant="soft"] { color: $ink; background: $hover; border-color: transparent; }
QPushButton[variant="soft"]:hover { background: $selected; border-color: transparent; }
QPushButton[variant="danger"] { color: $danger; background: $surface; border-color: $danger_line; }
QPushButton[variant="danger"]:hover { background: $danger_soft; border-color: $danger; }
QPushButton:focus { border: 1px solid $primary; }
QPushButton[variant="primary"]:focus { border: 1px solid $primary_pressed; }
QPushButton:disabled, QPushButton[variant]:disabled {
    color: $disabled_text; background: $disabled_bg; border-color: $hairline;
}
QPushButton#navButton { border: 1px solid transparent; background: transparent;
    text-align: left; min-height: 18px; padding: 6px 8px; color: $body; font-size: 13px;
    font-weight: 400; border-radius: 6px; }
QPushButton#navButton:hover { background: $hover; color: $ink; }
QPushButton#navButton[selected="true"] { color: $primary_text; background: $selected;
    border-color: $selected_line; font-weight: 600; }
QPushButton#navButton:focus { border-color: $primary; }
QPushButton#ghostButton, QPushButton#iconButton { border: 1px solid transparent;
    background: transparent; color: $body; min-height: 18px; padding: 6px 8px;
    font-size: 13px; border-radius: 6px; }
QPushButton#iconButton { padding: 6px; }
QPushButton#ghostButton:hover, QPushButton#iconButton:hover { background: $hover; color: $ink; }
QPushButton#ghostButton[selected="true"] { background: $selected; border-color: $selected_line;
    color: $primary_text; font-weight: 600; }
QPushButton#ghostButton:focus, QPushButton#iconButton:focus { border-color: $primary; }
QPushButton#ghostButton:disabled { background: $disabled_bg; color: $disabled_text;
    border-color: $hairline; }
QPushButton#sendButton { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
    padding: 0; border-radius: 6px; }
QPushButton#composerButton { min-height: 18px; padding: 6px 10px; border-radius: 6px;
    font-size: 12px; }
QPushButton#addServerButton { background: $hover; color: $ink; border-color: transparent; }
QPushButton#addServerButton:hover, QPushButton#addServerButton[selected="true"] {
    background: $selected; border-color: transparent;
}
QPushButton#linkButton { color: $primary_text; border: 1px solid transparent;
    background: transparent;
    text-align: left; min-height: 18px; padding: 6px 0; font-size: 12px;
    text-decoration: underline; }
QPushButton#linkButton:hover { color: $primary_text; background: transparent; }
QPushButton#linkButton:focus { border-color: $primary; }
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
    border: 1px solid $field_line; border-radius: 6px; background: $surface;
    padding: 7px 10px; font-size: 14px; placeholder-text-color: $subtle;
}
QLineEdit, QComboBox { min-height: 18px; }
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover { border-color: $line_hover; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 2px solid $primary; padding: 6px 9px; background: $surface;
}
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {
    color: $disabled_text; background: $disabled_bg; }
QPlainTextEdit { font-family: $mono; font-size: 13px; }
QPlainTextEdit[readOnly="true"] { background: $raised; }
QComboBox { padding-right: 30px; }
QComboBox:focus { padding-right: 29px; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: $popover; border: 1px solid $hairline_strong;
    padding: 4px; selection-background-color: $selected; selection-color: $ink; outline: 0; }
QTableWidget { background: $surface; alternate-background-color: $surface;
    border: 1px solid $hairline_strong; border-radius: 8px; font-size: 14px;
    qproperty-showGrid: false; }
QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid $hairline; }
QTableWidget::item:selected { background: $selected; color: $ink; }
QHeaderView { background: transparent; border: none;
    qproperty-defaultAlignment: "AlignLeft|AlignVCenter"; }
QHeaderView::section { background: $raised; color: $mute; padding: 8px 10px;
    border: none; border-bottom: 1px solid $hairline_strong; font-size: 13px;
    font-weight: 600; }
QTableCornerButton::section { background: transparent; border: none; }
QTabBar::tab { background: transparent; color: $mute; border: 1px solid transparent;
    border-radius: 6px; padding: 6px 12px; margin-right: 4px; font-size: 13px; }
QTabBar::tab:hover { background: $hover; color: $ink; }
QTabBar::tab:selected { background: $selected; color: $primary_text; font-weight: 600;
    border-color: $selected_line; }
QTabBar::tab:focus { border-color: $primary; }
QMenu { background: $popover; border: 1px solid $hairline_strong; border-radius: 8px;
    padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 5px; }
QMenu::item:selected { background: $selected; color: $primary_text; }
QMenu::item:disabled { color: $disabled_text; font-size: 12px; }
QMenu::separator { height: 1px; background: $hairline; margin: 4px 6px; }
QToolTip { background: $popover; color: $ink; border: 1px solid $hairline_strong;
    padding: 4px 8px; }
QFrame#sectionCard { background: $surface; border: 1px solid $hairline_strong; border-radius: 8px; }
QLabel#policyTool, QLabel#policyValue { font-weight: 600; }
QLabel#policyDestination { font-family: $mono; font-size: 12px; font-weight: 600;
    padding: 6px 10px; }
QLabel#policyChanged { color: $primary_text; font-size: 12px; font-weight: 600; }
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
