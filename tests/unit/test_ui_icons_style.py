"""Fixed icon artwork and the application style sheet (T18 display baseline)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from guard_app.ui import icons
from guard_app.ui.icons import line_icon
from guard_app.ui.style import STYLE, apply_style


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
