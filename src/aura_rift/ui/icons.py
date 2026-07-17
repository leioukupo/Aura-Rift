"""Navigation & decorative icons rendered with QPainter (no QtSvg dependency).

Earlier versions rasterised SVG strings via ``QSvgRenderer``.  QtSvg's stroke
rendering of ``<line>`` / ``<polyline>`` elements varies between Qt builds and
machines, which produced icons that were only *partially* visible on some
hosts.  The glyphs are now drawn directly with ``QPainter`` + ``QPainterPath``
on a fixed design grid, so the output is identical everywhere QtGui runs.
"""

from __future__ import annotations

import math
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

# A draw operation: (path, mode, color, stroke_width).
#   mode: "stroke" or "fill"
#   color: "" = use the caller-supplied color, otherwise an exact hex string
#   stroke_width: in design units (applies to "stroke" ops)
_Op = tuple[QPainterPath, str, str, float]


# --- small QPainterPath builders --------------------------------------------


def _line(x0: float, y0: float, x1: float, y1: float, width: float = 2.0) -> _Op:
    p = QPainterPath()
    p.moveTo(x0, y0)
    p.lineTo(x1, y1)
    return (p, "stroke", "", width)


def _polyline(pts: list[tuple[float, float]], width: float = 2.0) -> _Op:
    p = QPainterPath()
    p.moveTo(*pts[0])
    for x, y in pts[1:]:
        p.lineTo(x, y)
    return (p, "stroke", "", width)


def _ellipse(cx: float, cy: float, rx: float, ry: float | None = None) -> _Op:
    if ry is None:
        ry = rx
    p = QPainterPath()
    p.addEllipse(QPointF(cx, cy), rx, ry)
    return (p, "stroke", "", 2.0)


def _fill_ellipse(cx: float, cy: float, rx: float,
                  ry: float | None = None, color: str = "") -> _Op:
    if ry is None:
        ry = rx
    p = QPainterPath()
    p.addEllipse(QPointF(cx, cy), rx, ry)
    return (p, "fill", color, 0.0)


def _fill_round_rect(x: float, y: float, w: float, h: float, rx: float,
                     color: str = "") -> _Op:
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), rx, rx)
    return (p, "fill", color, 0.0)


# --- nav glyphs (lucide-style, 24x24 grid) ----------------------------------
# Shared 24x24 design grid with a uniform 2px rounded stroke.


def _build_rocket() -> list[_Op]:
    body = QPainterPath()
    body.moveTo(9.5, 16.0)
    body.lineTo(9.5, 7.8)
    body.cubicTo(9.5, 3.6, 14.5, 3.6, 14.5, 7.8)  # rounded nose
    body.lineTo(14.5, 16.0)
    left_fin = _polyline([(9.5, 12.5), (6.0, 17.0), (9.5, 14.5)])
    right_fin = _polyline([(14.5, 12.5), (18.0, 17.0), (14.5, 14.5)])
    flame = _polyline([(10.3, 16.6), (12.0, 20.2), (13.7, 16.6)])
    window = _ellipse(12.0, 9.2, 1.5)
    return [(body, "stroke", "", 2.0), left_fin, right_fin, flame, window]


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
    return [
        _line(6, 3, 6, 15),
        _ellipse(18, 6, 2.6),
        _ellipse(6, 18, 2.6),
        (curve, "stroke", "", 2.0),
    ]


def _build_wrench() -> list[_Op]:
    # Ring-spanner head (upper-right) + a solid bar handle down to the left.
    head_outer = _ellipse(17.5, 6.5, 4.2)
    head_inner = _ellipse(17.5, 6.5, 2.2)
    bar = _line(15.2, 9.4, 5.4, 18.9, width=3.3)
    return [head_outer, head_inner, bar]


def _build_terminal() -> list[_Op]:
    chevron = _polyline([(4, 17), (10, 11), (4, 5)])
    underscore = _line(12, 19, 20, 19)
    return [chevron, underscore]


def _build_settings() -> list[_Op]:
    cx, cy = 12.0, 12.0
    r_root, r_tip, hub = 6.6, 9.0, 2.6
    ops: list[_Op] = []
    for k in range(8):
        a = math.radians(k * 45) - math.pi / 2          # first tooth at the top
        ang_tl = a - math.radians(8.5)
        ang_tr = a + math.radians(8.5)
        ang_rl = a - math.radians(13.5)
        ang_rr = a + math.radians(13.5)
        bump = _polyline([
            (cx + r_root * math.cos(ang_rl), cy + r_root * math.sin(ang_rl)),
            (cx + r_tip * math.cos(ang_tl), cy + r_tip * math.sin(ang_tl)),
            (cx + r_tip * math.cos(ang_tr), cy + r_tip * math.sin(ang_tr)),
            (cx + r_root * math.cos(ang_rr), cy + r_root * math.sin(ang_rr)),
        ])
        ops.append(bump)
    ops.append(_ellipse(cx, cy, r_root))   # gear ring
    ops.append(_ellipse(cx, cy, hub))      # center hub
    return ops


_NAV_BUILDERS: dict[str, Callable[[], list[_Op]]] = {
    "rocket": _build_rocket,
    "sliders": _build_sliders,
    "git-branch": _build_git_branch,
    "wrench": _build_wrench,
    "terminal": _build_terminal,
    "settings": _build_settings,
}


def _render_icons(ops: list[_Op], color: str, pixel_size: int,
                  design_size: float) -> QIcon:
    """Render stroke/fill ops into a DPR-aware transparent pixmap."""
    dpr = 1.0
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        dpr = screen.devicePixelRatio()
    if dpr < 1.0:
        dpr = 1.0
    phys = int(round(max(1.0, dpr) * pixel_size))
    pm = QPixmap(phys, phys)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    scale = pixel_size / design_size
    painter.scale(scale, scale)

    caller = QColor(color)
    pen = QPen()
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    for path, mode, col, width in ops:
        c = caller if col == "" else QColor(col)
        if mode == "fill":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(c))
        else:
            painter.setBrush(Qt.NoBrush)
            pen.setColor(c)
            pen.setWidthF(width if width else 2.0)
            painter.setPen(pen)
        painter.drawPath(path)
    painter.end()
    return QIcon(pm)


def make_nav_icon(name: str, color: str, pixel_size: int = 28) -> QIcon:
    """Draw a navigation glyph with QPainter (no SVG / QSvg dependency)."""
    builder = _NAV_BUILDERS.get(name)
    if builder is None:
        builder = _build_terminal          # safe fallback for unknown names
    return _render_icons(builder(), color, pixel_size, design_size=24.0)


# --- lightbulb (theme toggle) on a 32x32 grid --------------------------------
# Reproduces the previous inline SVG: a warm bulb + short rays + a screw base
# drawn in the caller's text color.
_BULB_GLOW = "#f4b942"


def _build_lightbulb() -> list[_Op]:
    glow = _fill_ellipse(16, 13, 9, color=_BULB_GLOW)
    top_ray = (_line(16, 1, 16, 3, 1.6)[0], "stroke", _BULB_GLOW, 1.6)
    left_ray = (_line(5.5, 3, 7, 4.5, 1.6)[0], "stroke", _BULB_GLOW, 1.6)
    right_ray = (_line(26.5, 3, 25, 4.5, 1.6)[0], "stroke", _BULB_GLOW, 1.6)
    base1 = _fill_round_rect(12, 21, 8, 2.5, 1.0, color="")   # caller-color base
    base2 = _fill_round_rect(13, 24.5, 6, 2, 1.0, color="")
    base3 = _fill_round_rect(14, 27.5, 4, 2, 0.5, color="")
    return [glow, top_ray, left_ray, right_ray, base1, base2, base3]


def make_lightbulb_icon(color: str, pixel_size: int = 24) -> QIcon:
    """Draw the theme-toggle lightbulb with QPainter (no SVG dependency)."""
    return _render_icons(_build_lightbulb(), color, pixel_size, design_size=32.0)
