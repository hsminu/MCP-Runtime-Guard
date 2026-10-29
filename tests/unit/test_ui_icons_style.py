"""Fixed icon artwork and the application style sheet (T18 display baseline)."""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from guard_app.ui import icons, style
from guard_app.ui.icons import line_icon
from guard_app.ui.style import PALETTES, STYLE, TOKENS, apply_style, set_theme, style_sheet

_UI_SOURCES = sorted(
    path for path in Path(style.__file__).parent.glob("*.py") if path.name != "style.py"
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


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
        if "border: 1px solid" in body and "border-radius: 10px" in body:
            boxed.update(re.findall(r"QFrame#(\w+)", selector))
    # Lists are one box of hairline rows; nothing inside a list is boxed again.
    assert boxed == {"placeholderCard", "metricList", "rowList"}


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
# (one radius on every boxed card, an accent spread over everything, eyebrow
# labels, colors and spacing that drift from the tokens).


def _rules() -> list[tuple[str, str]]:
    return [
        (" ".join(selector.split()), body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style._SHEET)
    ]


def test_ui_sources_use_palette_colors_brand_accent_and_grid_spacing() -> None:
    failures, painted = [], {}
    call = re.compile(r"\.(?:setContentsMargins|setSpacing|addSpacing)\(([^()]*)\)")
    allowed = {
        "QLabel#brandMark",
        'QLabel#statusPill[tone="live"]',
        'QLabel#answerLabel[tone="live"]',
    }
    for selector, body in _rules():
        if re.search(r"\$accent(?:_soft|_line)?\b", body) and selector not in allowed:
            failures.append((selector, "accent"))
    for path in _UI_SOURCES:
        text = path.read_text(encoding="utf-8")
        literals = re.findall(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(|QColor\("', text)
        if literals:
            failures.append((path.name, literals))
        if "eyebrow" in text.casefold():
            failures.append((path.name, "eyebrow"))
        uses = re.findall(r'"(?:on_)?accent(?:_soft|_line)?"', text)
        if uses:
            painted[path.name] = len(uses)
        for args in call.findall(text):
            for number in map(int, re.findall(r"\b\d+\b", args)):
                if number not in (0, 2) and number % 4 != 0:
                    failures.append((path.name, args))
    if painted != {"sessions.py": 1, "window.py": 1}:
        failures.append(("accent counts", painted))
    if PALETTES["light"]["selection"] == PALETTES["light"]["accent"]:
        failures.append(("palette", "selection equals accent"))
    assert not failures, failures
