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


@pytest.mark.parametrize("name", sorted(icons._SHAPES))
def test_every_fixed_icon_renders_normal_and_disabled_pixmaps(app: QApplication, name: str) -> None:
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


def test_unknown_icon_name_is_refused_not_drawn_blank() -> None:
    with pytest.raises(KeyError):
        line_icon("not-an-icon")


def test_icon_artwork_carries_no_external_reference() -> None:
    for shape in icons._SHAPES.values():
        assert "href" not in shape
        assert "url(" not in shape
        assert "<script" not in shape


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
    assert PALETTES["light"].keys() == PALETTES["dark"].keys()
    for theme in PALETTES:
        sheet = style_sheet(theme)
        assert "$" not in sheet
        assert "url(" not in sheet
        assert "http" not in sheet
    with pytest.raises(KeyError):
        style_sheet("sepia")


def test_icons_follow_a_theme_switch_without_being_recreated(app: QApplication) -> None:
    icon = line_icon("shield", "ink")
    try:
        for theme in ("dark", "light"):
            set_theme(theme)
            assert TOKENS["canvas"] == PALETTES[theme]["canvas"]
            assert _stroke(icon) == PALETTES[theme]["ink"]
    finally:
        set_theme("light")


def test_icon_colors_are_palette_roles_not_literals() -> None:
    with pytest.raises(KeyError):
        line_icon("shield", "#ffffff")


# Style audit: deterministic checks that keep generated-looking defaults out
# (one radius on every boxed card, an accent spread over everything, eyebrow
# labels, colors and spacing that drift from the tokens).


def _rules() -> list[tuple[str, str]]:
    return [
        (" ".join(selector.split()), body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style._SHEET)
    ]


def test_colors_live_only_in_the_palette() -> None:
    for path in _UI_SOURCES:
        text = path.read_text(encoding="utf-8")
        literals = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(|QColor\(\"", text)
        assert not literals, (path.name, literals)


def test_ember_accent_is_reserved_for_the_brand_and_running_work() -> None:
    allowed = {
        "QLabel#brandMark",
        'QLabel#statusPill[tone="live"]',
        'QLabel#answerLabel[tone="live"]',
    }
    for selector, body in _rules():
        if re.search(r"\$accent(?:_soft|_line)?\b", body):
            assert selector in allowed, selector
    painted = {}
    for path in _UI_SOURCES:
        uses = re.findall(r'"(?:on_)?accent(?:_soft|_line)?"', path.read_text(encoding="utf-8"))
        if uses:
            painted[path.name] = len(uses)
    # The running session mark and the brand mark's glyph.
    assert painted == {"sessions.py": 1, "window.py": 1}
    assert PALETTES["light"]["selection"] != PALETTES["light"]["accent"]


def test_boxed_cards_stay_few_and_never_nest_in_the_activity_panel() -> None:
    boxed = set()
    for selector, body in _rules():
        if "border: 1px solid" in body and "border-radius: 10px" in body:
            boxed.update(re.findall(r"QFrame#(\w+)", selector))
    # Lists are one box of hairline rows; nothing inside a list is boxed again.
    assert boxed == {"placeholderCard", "metricList", "rowList"}


def test_no_eyebrow_labels_or_letter_spacing() -> None:
    sheet = style_sheet("light")
    for banned in ("text-transform", "letter-spacing", "font-variant"):
        assert banned not in sheet
    for path in _UI_SOURCES:
        assert "eyebrow" not in path.read_text(encoding="utf-8").casefold(), path.name


def test_layout_spacing_sits_on_a_4px_grid() -> None:
    call = re.compile(r"\.(?:setContentsMargins|setSpacing|addSpacing)\(([^()]*)\)")
    for path in _UI_SOURCES:
        for args in call.findall(path.read_text(encoding="utf-8")):
            for number in map(int, re.findall(r"\b\d+\b", args)):
                assert number in (0, 2) or number % 4 == 0, (path.name, args)
