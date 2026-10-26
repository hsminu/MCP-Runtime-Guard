"""Local vector icons with no font glyphs, remote assets, or runtime I/O."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SHAPES = {
    "send": '<path d="m3 3 18 9-18 9 4-9Z"/><path d="M7 12h14"/>',
    "results": '<path d="M8 6h12M8 12h12M8 18h12M3 6h.01M3 12h.01M3 18h.01"/>',
    "overview": '<rect x="3" y="3" width="7" height="7" rx="2"/>'
    '<rect x="14" y="3" width="7" height="7" rx="2"/>'
    '<rect x="3" y="14" width="7" height="7" rx="2"/>'
    '<rect x="14" y="14" width="7" height="7" rx="2"/>',
    "logs": '<path d="M8 6h12M8 12h12M8 18h12M3 6h.01M3 12h.01M3 18h.01"/>',
    "approvals": '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="m8 12 3 3 5-6"/>',
    "servers": '<rect x="3" y="3" width="18" height="7" rx="2"/>'
    '<rect x="3" y="14" width="18" height="7" rx="2"/>'
    '<path d="M7 6.5h.01M7 17.5h.01M12 6.5h5M12 17.5h5"/>',
    "shield": '<path d="m12 3 8 3v6c0 5-4 8-8 10-4-2-8-5-8-10V6Z"/><path d="m8 12 3 3 5-6"/>',
    "audit": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/>'
    '<path d="M14 3v6h6M8 13h8M8 17h5"/>',
    "play": '<path d="m8 4 12 8-12 8Z"/>',
    "settings": '<path d="M4 7h16M4 17h16"/>'
    '<rect x="7" y="4" width="4" height="6" rx="1"/>'
    '<rect x="14" y="14" width="4" height="6" rx="1"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="3"/>',
    "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/>'
    '<circle cx="12" cy="12" r="3"/>',
    "refresh": '<path d="M20 7v5h-5M4 17v-5h5"/>'
    '<path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
}


def line_icon(name: str, color: str = "#70798c") -> QIcon:
    """Render fixed artwork at 2x resolution, including a disabled state."""
    icon = QIcon()
    for mode, stroke in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, "#a4aabb")):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{stroke}" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round">{_SHAPES[name]}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode("ascii")))
        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon.addPixmap(pixmap, mode)
    return icon
