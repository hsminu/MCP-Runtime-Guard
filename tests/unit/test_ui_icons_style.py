"""Fixed icon artwork and the application style sheet (T18 display baseline)."""

from __future__ import annotations

import ast
import io
import os
import re
import sys
import tokenize
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    # Offscreen Qt finds no fonts without this (CI sets none), so layout checks measured
    # fallback glyphs (#114 A review). Use the Windows fonts the app runs with.
    os.environ.setdefault(
        "QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    )

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget

from guard_app.ui import icons, style
from guard_app.ui.icons import line_icon
from guard_app.ui.style import PALETTES, STYLE, TOKENS, apply_style, set_theme, style_sheet
from guard_app.ui.widgets import (
    Disclosure,
    FitStack,
    LabelPill,
    ResponsiveRow,
    SecretLineEdit,
    SectionCard,
    StatusPill,
    plain_label,
)

_UI_SOURCES = sorted(
    path for path in Path(style.__file__).parent.glob("*.py") if path.name != "style.py"
)


def _without_comments(text: str) -> str:
    """Code and strings only: issue numbers in comments such as #101 paint nothing."""
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return " ".join(token.string for token in tokens if token.type != tokenize.COMMENT)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@contextmanager
def closing_widgets(*widgets):
    """Close in the supplied order, including when an assertion fails."""
    try:
        yield
    finally:
        for widget in widgets:
            widget.close()


def show_widget(app, widget, width, height):
    widget.resize(width, height)
    widget.show()
    app.processEvents()


def resize_widget(app, widget, width, height):
    widget.resize(width, height)
    app.processEvents()


def assert_key_focus(page, source, key, expected):
    QTest.keyClick(source, key)
    assert page.focusWidget() is expected


def test_every_fixed_icon_renders_normal_and_disabled_pixmaps(app):
    failures = []
    for name in sorted(icons._SHAPES):
        try:
            shape = icons._SHAPES[name]
            assert "href" not in shape
            assert "url(" not in shape
            assert "<script" not in shape
            icon = line_icon(name)
            assert not icon.isNull()
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled):
                image = icon.pixmap(QSize(40, 40), mode).toImage()
                painted = sum(
                    1
                    for x in range(image.width())
                    for y in range(image.height())
                    if image.pixelColor(x, y).alpha() > 0
                )
                assert painted > 0, (name, mode)
        except AssertionError as error:
            failures.append((name, str(error)))
    assert not failures, failures


def test_ui_sources_do_not_imply_openai_affiliation():
    """A148 branding decision; I9/T19 UI support, not a trademark/legal audit."""
    banned = (
        "공식",
        "OpenAI와",
        "OpenAI 제공",
        "OpenAI 제휴",
        "OpenAI 파트너",
        "OpenAI 로고",
        "official",
        "powered by OpenAI",
        "OpenAI partner",
        "OpenAI logo",
        "openai_logo",
        "openaiLogo",
    )
    failures = []
    for path in sorted(Path(style.__file__).parent.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        # Include adjacent literal concatenation and names in icons/styles/titles.
        strings = [source] + [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        normalized = [re.sub(r"\s+", "", text).casefold() for text in strings]
        for phrase in banned:
            if any(re.sub(r"\s+", "", phrase).casefold() in text for text in normalized):
                failures.append((path.name, phrase))
    assert not failures, failures


def test_style_applies_font_and_sheet_without_remote_resources(app: QApplication) -> None:
    widget = QWidget()
    apply_style(widget)
    assert widget.styleSheet() == STYLE
    assert widget.font().pixelSize() == 14
    assert "url(" not in STYLE
    assert "http" not in STYLE


def _stroke(icon: QIcon) -> str:
    image = icon.pixmap(QSize(24, 24)).toImage()
    return next(
        image.pixelColor(x, y).name()
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() == 255
    )


def test_light_and_dark_palettes_define_the_same_roles() -> None:
    with pytest.raises(KeyError):
        line_icon("not-an-icon")
    with pytest.raises(KeyError):
        line_icon("shield", "#ffffff")
    assert PALETTES["light"].keys() == PALETTES["dark"].keys()
    for theme in PALETTES:
        sheet = style_sheet(theme)
        assert "$" not in sheet
        assert "url(" not in sheet
        assert "http" not in sheet
    with pytest.raises(KeyError):
        style_sheet("sepia")
    sheet = style_sheet("light")
    for banned in ("text-transform", "letter-spacing", "font-variant"):
        assert banned not in sheet
    boxed = set()
    for selector, body in _rules():
        if "border: 1px solid" in body and "border-radius: 8px" in body:
            boxed.update(re.findall(r"QFrame#(\w+)", selector))
    # Lists are one box of hairline rows; nothing inside a list is boxed again.
    assert boxed == {"placeholderCard", "metricList", "rowList", "sectionCard", "approvalBand"}


def test_icons_follow_a_theme_switch_without_being_recreated(app: QApplication) -> None:
    icon = line_icon("shield", "ink")
    try:
        for theme in ("dark", "light"):
            set_theme(theme)
            assert TOKENS["canvas"] == PALETTES[theme]["canvas"]
            assert _stroke(icon) == PALETTES[theme]["ink"]
    finally:
        set_theme("light")


# Style audit: deterministic checks that keep generated-looking defaults out
# (semantic colors spread over everything, eyebrow
# labels, colors and spacing that drift from the tokens).


def _rules() -> list[tuple[str, str]]:
    return [
        (" ".join(selector.split()), body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style._SHEET)
    ]


def test_ui_sources_use_palette_colors_and_grid_spacing() -> None:
    failures = []
    call = re.compile(r"\.(?:setContentsMargins|setSpacing|addSpacing)\(([^()]*)\)")
    for path in _UI_SOURCES:
        text = path.read_text(encoding="utf-8")
        literals = re.findall(
            r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(|QColor\("', _without_comments(text)
        )
        if literals:
            failures.append((path.name, literals))
        if "eyebrow" in text.casefold():
            failures.append((path.name, "eyebrow"))
        if "accent" in text:
            failures.append((path.name, "obsolete palette role"))
        for args in call.findall(text):
            for number in map(int, re.findall(r"\b\d+\b", args)):
                if number not in (0, 2) and number % 4 != 0:
                    failures.append((path.name, args))
    assert not failures, failures


def test_semantic_colors_are_limited_to_explicit_selectors():
    """I7/T18: selection/actions, state and grade keep separate visual meanings."""
    primary = {
        "QLabel#brandMark",
        "QLabel#policyChanged",
        'QLabel#statusPill[tone="live"]',
        'QLabel#answerLabel[tone="live"]',
        'QPushButton[variant="primary"]',
        'QPushButton[variant="primary"]:hover',
        'QPushButton[variant="primary"]:pressed',
        'QPushButton[variant="primary"]:focus',
        "QPushButton:focus",
        "QPushButton#newSessionButton:focus",
        "QPushButton#modelChip:focus",
        "QPushButton#navButton:focus",
        "QPushButton#ghostButton:focus",
        "QPushButton#iconButton:focus",
        "QPushButton#linkButton:focus",
        'QPushButton#navButton[selected="true"]',
        'QPushButton#ghostButton[selected="true"]',
        "QPushButton#linkButton",
        "QPushButton#linkButton:hover",
        "QLineEdit:focus",
        "QComboBox:focus",
        "QPlainTextEdit:focus",
        "QLineEdit#sidebarSearch:focus",
        "QTabBar::tab:focus",
        "QTabBar::tab:selected",
        "QMenu::item:selected",
    }
    state = {
        'QLabel#statusPill[tone="attn"]',
        'QLabel#statusPill[tone="deny"]',
        'QLabel#statusPill[tone="ok"]',
        'QLabel#answerLabel[tone="stopped"]',
        "QLabel#unverifiedNotice",
        "QFrame#approvalBand",
        'QFrame#approvalCard[attention="true"]',
        'QLabel[validationError="true"]',
        "QLabel#folderRootError",
        "QLabel#folderRulesError",
        "QLabel#folderRuleError",
        'QPushButton[variant="danger"]',
        'QPushButton[variant="danger"]:hover',
    }
    for selectors, body in _rules():
        for selector in (part.strip() for part in selectors.split(",")):
            if re.search(r"\$(?:primary(?:_\w+)?|on_primary)\b", body):
                assert selector in primary, selector
            if re.search(r"\$(?:ok|warn|danger)(?:_\w+)?\b", body):
                assert selector in state, selector
    assert "accent" not in style._SHEET


def _contrast(first, second):
    def luminance(color):
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    bright, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("theme", PALETTES)
def test_palette_contrast_and_separate_security_colors(theme):
    palette = PALETTES[theme]
    pairs = [
        (text, background, threshold)
        for texts, backgrounds, threshold in (
            (("ink", "body"), ("canvas", "surface"), 7),
            (("mute",), ("canvas", "surface", "sidebar", "raised"), 4.5),
            (("subtle",), ("canvas", "surface"), 4.5),
            (("on_primary",), ("primary", "primary_hover", "primary_pressed"), 4.5),
            (("primary_text",), ("canvas", "selected"), 4.5),
            (("public", "internal", "confidential"), ("surface",), 4.5),
            (("field_line",), ("surface",), 1.8),
        )
        for text in texts
        for background in backgrounds
    ]
    pairs += [
        (tone, background, 4.5)
        for tone in ("ok", "warn", "danger")
        for background in (tone + "_soft", "surface")
    ]
    for text, background, threshold in pairs:
        ratio = _contrast(palette[text], palette[background])
        assert ratio >= threshold, (theme, text, background, ratio, threshold)
    assert {palette[role] for role in ("public", "internal", "confidential")}.isdisjoint(
        palette[role] for role in ("ok", "warn", "danger")
    )


def test_disclosure_hides_body_and_returns_focus_to_toggle(app):
    disclosure = Disclosure("<b>자세히</b>")
    body = QLineEdit()
    disclosure.set_body(body)
    with closing_widgets(disclosure):
        show_widget(app, disclosure, 300, 100)
        assert body.isHidden()
        assert "펼치기" in disclosure.button.accessibleName()
        disclosure.button.click()
        assert body.isVisible()
        assert "접기" in disclosure.button.accessibleName()
        body.setFocus()
        disclosure.set_open(False)
        assert body.isHidden()
        assert disclosure.focusWidget() is disclosure.button
        assert not disclosure.is_open()


def test_fit_stack_follows_current_page_height(app):
    stack = FitStack()
    for height in (300, 40):
        page = QWidget()
        layout = QVBoxLayout(page)
        content = plain_label("내용")
        content.setFixedHeight(height)
        layout.addWidget(content)
        stack.addWidget(page)
    with closing_widgets(stack):
        tall = stack.sizeHint().height()
        tall_min = stack.minimumSizeHint().height()
        stack.setCurrentIndex(1)
        assert stack.sizeHint().height() < tall
        assert stack.minimumSizeHint().height() < tall_min


def test_secret_field_never_copies_or_cuts_even_while_revealed(app):
    """I9/T19: clipboard contains only the synthetic sentinel, never the key."""
    field = SecretLineEdit()
    with closing_widgets(field):
        show_widget(app, field, 300, 60)
        field.setText("synthetic-key")
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert field.visibility_action.text() == "키 표시"
        field.visibility_action.trigger()
        assert field.echoMode() == QLineEdit.EchoMode.Normal
        assert field.findChild(QToolButton).accessibleName() == "키 숨기기"
        assert not field.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        assert not field.dragEnabled()
        assert field.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
        clipboard = QApplication.clipboard()
        clipboard.setText("sentinel")
        field.selectAll()
        for key, modifier in (
            (Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_Insert, Qt.KeyboardModifier.ControlModifier),
            (Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier),
        ):
            QTest.keyClick(field, key, modifier)
            assert clipboard.text() == "sentinel"
            assert field.text() == "synthetic-key"
        field.copy()
        field.cut()
        assert clipboard.text() == "sentinel"
        field.hide()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        field.show()
        field.visibility_action.trigger()
        field.clear()
        assert not field.text()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        clipboard.clear()


def test_status_and_grade_widgets_render_plain_text_in_both_themes(app):
    pill, grade = StatusPill(), LabelPill("INTERNAL")
    try:
        for theme in PALETTES:
            set_theme(theme)
            for widget in (pill, grade):
                apply_style(widget)
            for tone in ("live", "attn", "deny", "ok", "neutral"):
                pill.set_status("<b>상태</b>", tone)
                assert pill.textFormat() == Qt.TextFormat.PlainText
                assert not pill.grab().isNull()
            assert not grade.grab().isNull()
            assert grade.textFormat() == Qt.TextFormat.PlainText
    finally:
        set_theme("light")
        pill.close()
        grade.close()


def test_responsive_row_reflows_and_card_has_plain_title(app):
    row = ResponsiveRow(400)
    field = QLineEdit()
    row.add_widget(field, 1)
    with closing_widgets(row):
        show_widget(app, row, 600, 100)
        assert row.row.direction() == QBoxLayout.Direction.LeftToRight
        field.setFocus()
        resize_widget(app, row, 300, 100)
        assert row.row.direction() == QBoxLayout.Direction.TopToBottom
        assert row.focusWidget() is field
    card = SectionCard("<b>제목</b>")
    with closing_widgets(card):
        assert card.title.textFormat() == Qt.TextFormat.PlainText
        assert card.content.contentsMargins().left() == 16
        with pytest.raises(ValueError, match="cannot contain"):
            card.add_widget(SectionCard("중첩"))


def test_sheet_typography_has_no_text_below_twelve_pixels():
    for _, body in _rules():
        assert all(int(size) >= 12 for size in re.findall(r"font-size: (\d+)px", body))
