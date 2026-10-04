"""Small, dependency-free icon set for the Aura-Rift launcher.

Icons are painted directly with :class:`~PySide6.QtGui.QPainter` on a 24x24
design grid.  This avoids the optional QtSvg module and, more importantly,
keeps thin strokes crisp on both X11 and Wayland HiDPI screens.  The public
``make_nav_icon`` and ``make_lightbulb_icon`` functions retain their original
signatures; extra glyphs cover folder cards, version actions, dialogs, and the
绘世-style settings pages.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

# A draw operation is ``(path, mode, colour, stroke_width)``.  Empty colour
# means use the caller supplied colour; a non-empty colour is an intentional
# semantic highlight (the warm bulb glow, for example).
_Op = tuple[QPainterPath, str, str, float]


def _line(x0: float, y0: float, x1: float, y1: float, width: float = 2.0) -> _Op:
    path = QPainterPath()
    path.moveTo(x0, y0)
    path.lineTo(x1, y1)
    return path, "stroke", "", width


def _polyline(points: list[tuple[float, float]], width: float = 2.0) -> _Op:
    path = QPainterPath()
    if not points:
        return path, "stroke", "", width
    path.moveTo(*points[0])
    for x, y in points[1:]:
        path.lineTo(x, y)
    return path, "stroke", "", width


def _polygon(points: list[tuple[float, float]], color: str = "") -> _Op:
    path = QPainterPath()
    if points:
        path.moveTo(*points[0])
        for x, y in points[1:]:
            path.lineTo(x, y)
        path.closeSubpath()
    return path, "fill", color, 0.0


def _ellipse(cx: float, cy: float, rx: float, ry: float | None = None, width: float = 2.0) -> _Op:
    if ry is None:
        ry = rx
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), rx, ry)
    return path, "stroke", "", width


def _fill_ellipse(
    cx: float,
    cy: float,
    rx: float,
    ry: float | None = None,
    color: str = "",
) -> _Op:
    if ry is None:
        ry = rx
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), rx, ry)
    return path, "fill", color, 0.0


def _round_rect(x: float, y: float, w: float, h: float, radius: float, width: float = 2.0) -> _Op:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), radius, radius)
    return path, "stroke", "", width


def _fill_round_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    radius: float,
    color: str = "",
) -> _Op:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), radius, radius)
    return path, "fill", color, 0.0


def _arc(cx: float, cy: float, rx: float, ry: float, start: float, span: float, width: float = 2.0) -> _Op:
    path = QPainterPath()
    path.arcMoveTo(QRectF(cx - rx, cy - ry, rx * 2, ry * 2), start)
    path.arcTo(QRectF(cx - rx, cy - ry, rx * 2, ry * 2), start, span)
    return path, "stroke", "", width


# --- glyph builders ---------------------------------------------------------


def _build_rocket() -> list[_Op]:
    body = QPainterPath()
    body.moveTo(9.5, 16.0)
    body.lineTo(9.5, 7.8)
    body.cubicTo(9.5, 3.6, 14.5, 3.6, 14.5, 7.8)
    body.lineTo(14.5, 16.0)
    return [
        (body, "stroke", "", 2.0),
        _polyline([(9.5, 12.5), (6.0, 17.0), (9.5, 14.5)]),
        _polyline([(14.5, 12.5), (18.0, 17.0), (14.5, 14.5)]),
        _polyline([(10.3, 16.6), (12.0, 20.2), (13.7, 16.6)]),
        _ellipse(12.0, 9.2, 1.5),
    ]


def _build_sliders() -> list[_Op]:
    return [
        _line(4, 21, 4, 14), _line(4, 10, 4, 3),
        _line(12, 21, 12, 12), _line(12, 8, 12, 3),
        _line(20, 21, 20, 16), _line(20, 12, 20, 3),
        _line(2, 14, 6, 14), _line(10, 8, 14, 8), _line(18, 16, 22, 16),
    ]


def _build_git_branch() -> list[_Op]:
    curve = QPainterPath()
    curve.moveTo(18.0, 9.0)
    curve.cubicTo(18.0, 14.5, 13.5, 18.0, 9.0, 18.0)
    return [_line(6, 3, 6, 15), _ellipse(18, 6, 2.6), _ellipse(6, 18, 2.6), (curve, "stroke", "", 2.0)]


def _build_wrench() -> list[_Op]:
    return [_ellipse(17.5, 6.5, 4.2), _ellipse(17.5, 6.5, 2.2), _line(15.2, 9.4, 5.4, 18.9, width=3.3)]


def _build_terminal() -> list[_Op]:
    return [_polyline([(4, 17), (10, 11), (4, 5)]), _line(12, 19, 20, 19)]


def _build_settings() -> list[_Op]:
    cx, cy = 12.0, 12.0
    r_root, r_tip, hub = 6.6, 9.0, 2.6
    ops: list[_Op] = []
    for k in range(8):
        a = math.radians(k * 45) - math.pi / 2
        ops.append(_polyline([
            (cx + r_root * math.cos(a - math.radians(13.5)), cy + r_root * math.sin(a - math.radians(13.5))),
            (cx + r_tip * math.cos(a - math.radians(8.5)), cy + r_tip * math.sin(a - math.radians(8.5))),
            (cx + r_tip * math.cos(a + math.radians(8.5)), cy + r_tip * math.sin(a + math.radians(8.5))),
            (cx + r_root * math.cos(a + math.radians(13.5)), cy + r_root * math.sin(a + math.radians(13.5))),
        ]))
    ops.extend((_ellipse(cx, cy, r_root), _ellipse(cx, cy, hub)))
    return ops


def _build_folder() -> list[_Op]:
    return [_round_rect(2.5, 5.5, 19, 15.5, 2.0), _polyline([(3, 6), (3, 4), (4, 3), (9, 3), (11, 6)])]


def _build_folder_open() -> list[_Op]:
    path = QPainterPath()
    path.moveTo(3, 7)
    path.lineTo(5, 4)
    path.lineTo(10, 4)
    path.lineTo(12, 6)
    path.lineTo(21, 6)
    path.lineTo(18.5, 19)
    path.lineTo(4, 19)
    path.closeSubpath()
    return [(path, "stroke", "", 2.0), _polyline([(3, 7), (21, 7)])]


def _build_external() -> list[_Op]:
    return [_polyline([(14, 3), (21, 3), (21, 10)]), _line(21, 3, 10, 14), _polyline([(18, 13), (18, 20), (4, 20), (4, 6), (11, 6)])]


def _build_play() -> list[_Op]:
    return [_polygon([(8, 5), (19, 12), (8, 19)]), _line(8, 5, 8, 19)]


def _build_pause() -> list[_Op]:
    return [_round_rect(6, 4, 4, 16, 1.0), _round_rect(14, 4, 4, 16, 1.0)]


def _build_history() -> list[_Op]:
    return [_arc(12, 12, 8.5, 8.5, 35, 290), _polyline([(3.5, 11), (3.5, 5), (9, 5)]), _line(12, 7, 12, 12), _line(12, 12, 16, 14)]


def _build_briefcase() -> list[_Op]:
    return [_round_rect(3, 7, 18, 13, 2.0), _round_rect(9, 3, 6, 5, 1.0), _line(3, 12, 21, 12), _line(10, 12, 10, 15), _line(14, 12, 14, 15)]


def _build_message() -> list[_Op]:
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(3, 4, 18, 14), 4, 4)
    bubble.moveTo(7, 18)
    bubble.lineTo(6, 21)
    bubble.lineTo(10, 18)
    return [(bubble, "stroke", "", 2.0), _line(8, 11, 16, 11), _line(8, 14, 13, 14)]


def _build_image() -> list[_Op]:
    return [_round_rect(3, 4, 18, 16, 2.0), _polyline([(4, 17), (9, 12), (12, 15), (15, 11), (20, 17)]), _ellipse(9, 9, 1.5)]


def _build_network() -> list[_Op]:
    return [_line(8, 8, 16, 5), _line(8, 10, 16, 18), _line(8, 8, 8, 18), _ellipse(6, 7, 2.5), _ellipse(18, 4, 2.5), _ellipse(18, 19, 2.5), _ellipse(6, 19, 2.5)]


def _build_package() -> list[_Op]:
    return [_polygon([(3, 7), (12, 3), (21, 7), (12, 11)]), _polyline([(3, 7), (3, 17), (12, 21), (21, 17), (21, 7)]), _line(12, 11, 12, 21)]


def _build_download() -> list[_Op]:
    return [_line(12, 3, 12, 16), _polyline([(7, 11), (12, 16), (17, 11)]), _polyline([(5, 20), (19, 20)])]


def _build_upload() -> list[_Op]:
    return [_line(12, 21, 12, 8), _polyline([(7, 13), (12, 8), (17, 13)]), _polyline([(5, 4), (19, 4)])]


def _build_refresh() -> list[_Op]:
    return [_arc(12, 12, 8, 8, 35, 270), _polygon([(19, 4), (21, 10), (15, 8)])]


def _build_help() -> list[_Op]:
    return [_ellipse(12, 12, 9), _arc(9.5, 10, 2.5, 2.5, 200, 220), _line(12, 14, 12, 16), _fill_ellipse(12, 19, 1.0)]


def _build_info() -> list[_Op]:
    return [_ellipse(12, 12, 9), _line(12, 11, 12, 16), _fill_ellipse(12, 7.5, 1.1)]


def _build_check() -> list[_Op]:
    return [_polyline([(4, 12), (9, 17), (20, 6)], width=2.5)]


def _build_close() -> list[_Op]:
    return [_line(5, 5, 19, 19, 2.2), _line(19, 5, 5, 19, 2.2)]


def _build_search() -> list[_Op]:
    return [_ellipse(10.5, 10.5, 6.5), _line(15.5, 15.5, 21, 21, 2.2)]


def _build_link() -> list[_Op]:
    return [_arc(8, 12, 5, 4, 120, 220), _arc(16, 12, 5, 4, 300, 220), _line(8, 12, 16, 12)]


def _build_book() -> list[_Op]:
    return [_polyline([(4, 4), (10, 4), (12, 6), (14, 4), (20, 4), (20, 20), (14, 20), (12, 18), (10, 20), (4, 20), (4, 4)]), _line(12, 6, 12, 18)]


_NAV_BUILDERS: dict[str, Callable[[], list[_Op]]] = {
    "rocket": _build_rocket,
    "launch": _build_rocket,
    "sliders": _build_sliders,
    "tune": _build_sliders,
    "git-branch": _build_git_branch,
    "branch": _build_git_branch,
    "wrench": _build_wrench,
    "terminal": _build_terminal,
    "console": _build_terminal,
    "settings": _build_settings,
    "gear": _build_settings,
    "folder": _build_folder,
    "folder-open": _build_folder_open,
    "external": _build_external,
    "external-link": _build_external,
    "play": _build_play,
    "pause": _build_pause,
    "history": _build_history,
    "clock": _build_history,
    "briefcase": _build_briefcase,
    "tools": _build_briefcase,
    "message": _build_message,
    "message-circle": _build_message,
    "image": _build_image,
    "image-plus": _build_image,
    "network": _build_network,
    "nodes": _build_network,
    "package": _build_package,
    "download": _build_download,
    "upload": _build_upload,
    "refresh": _build_refresh,
    "help": _build_help,
    "question": _build_help,
    "info": _build_info,
    "check": _build_check,
    "close": _build_close,
    "x": _build_close,
    "search": _build_search,
    "link": _build_link,
    "book": _build_book,
}


# Compatibility metadata from the original SVG-backed implementation.  It is
# intentionally only a lookup table; rendering always uses the deterministic
# QPainter builders above.  Consumers that used this mapping for feature
# detection therefore continue to work without importing QtSvg.
NAV_ICON_PATHS: dict[str, str] = {
    "rocket": (
        '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>'
        '<path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>'
        '<path d="M9 12H4s.55-3.03 2-4c1.62-1.16 5-1 5-1"/>'
        '<path d="M12 15v5s3.03-.55 4-2c1.16-1.62 1-5 1-5"/>'
    ),
    "sliders": (
        '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
        '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
        '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
        '<line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/>'
        '<line x1="18" y1="16" x2="22" y2="16"/>'
    ),
    "git-branch": (
        '<line x1="6" y1="3" x2="6" y2="15"/>'
        '<circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>'
        '<path d="M18 9a9 9 0 0 1-9 9"/>'
    ),
    "wrench": (
        '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'
    ),
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "folder": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
}
# Expose every painter glyph through the legacy path map as well.  The values
# for newer names are metadata only (rendering does not parse them), but this
# keeps feature-detection code from treating an available glyph as missing.
for _name in _NAV_BUILDERS:
    NAV_ICON_PATHS.setdefault(_name, "")
del _name


def icon_names() -> tuple[str, ...]:
    """Return stable, sorted names accepted by :func:`make_nav_icon`."""

    return tuple(sorted(_NAV_BUILDERS))


def _render_icons(ops: list[_Op], color: str, pixel_size: int, design_size: float = 24.0) -> QIcon:
    """Render operations into a transparent DPR-aware icon."""

    if QGuiApplication.instance() is None:
        raise RuntimeError("icon rendering requires an active QGuiApplication")
    size = max(1, int(pixel_size))
    dpr = 1.0
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        dpr = max(1.0, float(screen.devicePixelRatio()))
    pixmap = QPixmap(int(round(dpr * size)), int(round(dpr * size)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(size / design_size, size / design_size)
    caller = QColor(color)
    if not caller.isValid():
        caller = QColor("#ffffff")
    pen = QPen()
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    for path, mode, exact_color, width in ops:
        current = caller if not exact_color else QColor(exact_color)
        if mode == "fill":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(current))
        else:
            painter.setBrush(Qt.NoBrush)
            pen.setColor(current)
            pen.setWidthF(width or 2.0)
            painter.setPen(pen)
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def make_nav_icon(name: str, color: str, pixel_size: int = 28) -> QIcon:
    """Draw a named launcher glyph, falling back to the terminal glyph."""

    builder = _NAV_BUILDERS.get(str(name).strip().lower(), _build_terminal)
    return _render_icons(builder(), color, pixel_size)


def make_icon(name: str, color: str = "#ffffff", pixel_size: int = 24) -> QIcon:
    """Short alias useful for action/tool-bar code."""

    return make_nav_icon(name, color, pixel_size)


def make_file_icon(path: str | Path, pixel_size: int = 24) -> QIcon:
    """Load a raster icon from *path*, returning an empty icon on failure."""

    if QGuiApplication.instance() is None:
        raise RuntimeError("icon rendering requires an active QGuiApplication")
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return QIcon()
    return QIcon(pixmap.scaled(pixel_size, pixel_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))


# --- lightbulb (theme toggle) on a 32x32 grid -------------------------------

_BULB_GLOW = "#f4b942"


def _build_lightbulb() -> list[_Op]:
    return [
        _fill_ellipse(16, 13, 9, color=_BULB_GLOW),
        (_line(16, 1, 16, 3, 1.6)[0], "stroke", _BULB_GLOW, 1.6),
        (_line(5.5, 3, 7, 4.5, 1.6)[0], "stroke", _BULB_GLOW, 1.6),
        (_line(26.5, 3, 25, 4.5, 1.6)[0], "stroke", _BULB_GLOW, 1.6),
        _fill_round_rect(12, 21, 8, 2.5, 1.0),
        _fill_round_rect(13, 24.5, 6, 2, 1.0),
        _fill_round_rect(14, 27.5, 4, 2, 0.5),
    ]


def make_lightbulb_icon(color: str, pixel_size: int = 24) -> QIcon:
    """Draw the warm bulb used for dark/light theme switching."""

    return _render_icons(_build_lightbulb(), color, pixel_size, design_size=32.0)


__all__ = [
    "NAV_ICON_PATHS",
    "icon_names",
    "make_file_icon",
    "make_icon",
    "make_lightbulb_icon",
    "make_nav_icon",
]
