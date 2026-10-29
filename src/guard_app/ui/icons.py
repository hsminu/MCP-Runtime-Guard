"""Local vector icons with no font glyphs, remote assets, or runtime I/O."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .style import TOKENS

_SHAPES = {
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/>'
    '<circle cx="19" cy="12" r="1"/>',
    "copy": '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    "eye_off": '<path d="m3 3 18 18M9 5a12 12 0 0 1 13 7l-3 4M6 6a18 18 0 0 0-4 6s4 7 10 7l4-1'
    'M10 10l4 4"/>',
    "alert": '<path d="m12 3 10 18H2Z M12 9v5M12 17h.01"/>',
    "edit": '<path d="m16 3 5 5-12 12H4v-5ZM13 6l5 5"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
    "arrow_down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "filter": '<path d="M3 4h18l-7 8v7l-4 2v-9Z"/>',
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
    "chat": '<path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-5 4v-4'
    'a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z"/>',
    "arrow_up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M15 4v16"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/>',
    "compose": '<path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6"/>'
    '<path d="m17 3 4 4-8 8H9v-4Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
    'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/>',
}


class _LineIconEngine(QIconEngine):
    """Draw fixed artwork in a palette role resolved at paint time.

    A theme switch updates the shared tokens, so every icon follows it on
    its next paint without being recreated."""

    def __init__(self, name: str, role: str) -> None:
        super().__init__()
        self._name = name
        self._role = role
        self._cache: dict[tuple[str, int, int], QPixmap] = {}

    def _stroke(self, mode: QIcon.Mode) -> str:
        return TOKENS["disabled_text" if mode is QIcon.Mode.Disabled else self._role]

    def _render(self, size: QSize, stroke: str) -> QPixmap:
        key = (stroke, size.width(), size.height())
        cached = self._cache.get(key)
        if cached is None:
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
                f'fill="none" stroke="{stroke}" stroke-width="1.7" '
                f'stroke-linecap="round" stroke-linejoin="round">{_SHAPES[self._name]}</svg>'
            )
            renderer = QSvgRenderer(QByteArray(svg.encode("ascii")))
            cached = QPixmap(size)
            cached.fill(Qt.GlobalColor.transparent)
            painter = QPainter(cached)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, size.width(), size.height()))
            painter.end()
            self._cache[key] = cached
        return cached

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        scale = painter.device().devicePixelRatioF() if painter.device() else 1.0
        pixmap = self.scaledPixmap(rect.size(), mode, state, scale)
        painter.drawPixmap(rect, pixmap)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(  # noqa: N802 - Qt virtual
        self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float
    ) -> QPixmap:
        pixels = QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale)))
        pixmap = QPixmap(self._render(pixels, self._stroke(mode)))
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:  # noqa: N802
        return size

    def clone(self) -> QIconEngine:
        return _LineIconEngine(self._name, self._role)


def line_icon(name: str, role: str = "body") -> QIcon:
    """Fixed artwork stroked in a palette role, with a disabled state."""
    _SHAPES[name]  # unknown names are refused, never drawn blank
    TOKENS[role]
    return QIcon(_LineIconEngine(name, role))
