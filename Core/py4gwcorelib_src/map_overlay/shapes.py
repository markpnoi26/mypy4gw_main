"""Map Overlay shapes — the unified, rotation-capable marker renderer.

One shape registry for both modes. Geometry is ported verbatim from Mission Map's proven
``Shape`` classes (Triangle / Circle / Teardrop / Square / Penta / Tear / SignPost / Lock /
Scale) plus Compass's ``Star`` and ``Tear2``. Every shape takes two angles:

- ``offset_angle`` — the agent's facing (radians). Reproduces each widget's original look.
- ``map_rotation`` — a **final rigid rotation** of the whole glyph around its centre, using
  the *same* formula the projection uses for positions. It is ``0`` for the mission map (so
  mission markers are pixel-identical to before) and the compass rotation for the compass (so
  glyphs rotate together with their projected positions).

Colours arrive as ``RGBA`` tuples (model form) and are packed to draw-list ints through a
small cache. This module is render-only: it imports ``PyImGui`` and ``Color`` and nothing
from the overlay's data model beyond the ``RGBA`` alias.
"""

import math
from typing import Callable
from typing import Optional

import PyImGui

from Core.py4gwcorelib_src.Color import Color

from .model import RGBA

MATH_PI = math.pi
BASE_ANGLE = -MATH_PI / 2
SQRT_2 = math.sqrt(2)


# ── draw-list helpers ────────────────────────────────────────────────────────────────────
def DLLine(x1, y1, x2, y2, color, thickness=1.0):
    PyImGui.draw_list_add_line(float(x1), float(y1), float(x2), float(y2), color, float(thickness))


def DLCircle(x, y, radius, color, segments=16, thickness=1.0):
    PyImGui.draw_list_add_circle(float(x), float(y), float(radius), color, int(segments), float(thickness))


def DLCircleFilled(x, y, radius, color, segments=16):
    PyImGui.draw_list_add_circle_filled(float(x), float(y), float(radius), color, int(segments))


def DLTriangle(x1, y1, x2, y2, x3, y3, color, thickness=1.0):
    PyImGui.draw_list_add_triangle(
        float(x1), float(y1), float(x2), float(y2), float(x3), float(y3), color, float(thickness)
    )


def DLTriangleFilled(x1, y1, x2, y2, x3, y3, color):
    PyImGui.draw_list_add_triangle_filled(float(x1), float(y1), float(x2), float(y2), float(x3), float(y3), color)


def DLQuad(x1, y1, x2, y2, x3, y3, x4, y4, color, thickness=1.0):
    PyImGui.draw_list_add_quad(
        float(x1), float(y1), float(x2), float(y2), float(x3), float(y3), float(x4), float(y4), color, float(thickness)
    )


def DLQuadFilled(x1, y1, x2, y2, x3, y3, x4, y4, color):
    PyImGui.draw_list_add_quad_filled(
        float(x1), float(y1), float(x2), float(y2), float(x3), float(y3), float(x4), float(y4), color
    )


# ── colour packing cache ─────────────────────────────────────────────────────────────────
_PACK_CACHE: dict[RGBA, int] = {}


def shift_rgba(base: RGBA, target: RGBA, amount: float) -> RGBA:
    """Blend ``base`` toward ``target`` by ``amount`` (0..1), keeping ``base``'s alpha."""
    c = Color(int(base[0]), int(base[1]), int(base[2]), int(base[3])).shift(
        Color(int(target[0]), int(target[1]), int(target[2]), int(target[3])), amount
    )
    return (c.r, c.g, c.b, c.a)


def pack(rgba: RGBA) -> int:
    """Pack an ``RGBA`` tuple to a draw-list colour int (cached)."""
    cached = _PACK_CACHE.get(rgba)
    if cached is not None:
        return cached
    packed = Color(int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])).to_color()
    _PACK_CACHE[rgba] = packed
    return packed


# ── final rigid map rotation ─────────────────────────────────────────────────────────────
Transform = Callable[[float, float], tuple[float, float]]


def _identity(px: float, py: float) -> tuple[float, float]:
    return (px, py)


def _make_rot(cx: float, cy: float, angle: float) -> Transform:
    """A point transform: rotate ``angle`` about ``(cx, cy)`` — identity when ``angle`` is 0.

    Uses the same convention as the projections' position rotation
    (``x' = cx + cos*dx - sin*dy``), so glyphs rotate together with their positions.
    The zero-rotation case returns a shared function so the mission map (rotation 0) does not
    allocate a closure per marker.
    """
    if not angle:
        return _identity
    s = math.sin(angle)
    c = math.cos(angle)

    def f(px: float, py: float) -> tuple[float, float]:
        dx = px - cx
        dy = py - cy
        return (cx + c * dx - s * dy, cy + s * dx + c * dy)

    return f


# ── shape draws (name → callable) ────────────────────────────────────────────────────────
# Every draw signature: (x, y, size, color_int, accent_int, offset_angle, R) where R is the
# rigid-rotation Transform produced by _make_rot.

_TRI_UNIT = tuple((math.cos(i * (2 * MATH_PI / 3)), math.sin(i * (2 * MATH_PI / 3))) for i in range(3))
_TEARDROP_ARROW_UNIT = ((0.0, -SQRT_2), (-SQRT_2 / 2, -SQRT_2 / 2), (SQRT_2 / 2, -SQRT_2 / 2))
_TEAR_UNIT = ((0.0, -SQRT_2), (SQRT_2 / 2, 0.0), (0.0, SQRT_2 / 2), (-SQRT_2 / 2, 0.0))


def _draw_triangle(x, y, size, color, accent, offset_angle, R):
    cos_o = math.cos(offset_angle)
    sin_o = math.sin(offset_angle)
    pts = []
    for ux, uy in _TRI_UNIT:
        rx = ux * cos_o - uy * sin_o
        ry = ux * sin_o + uy * cos_o
        pts.append((x + rx * size, y + ry * size))
    if R is not _identity:
        pts = [R(p[0], p[1]) for p in pts]
    (x1, y1), (x2, y2), (x3, y3) = pts
    PyImGui.draw_list_add_triangle_filled(x1, y1, x2, y2, x3, y3, color)
    PyImGui.draw_list_add_triangle(x1, y1, x2, y2, x3, y3, accent, 2.0)


def _draw_circle(x, y, size, color, accent, offset_angle, R, segments=16):
    DLCircleFilled(x, y, size, color, segments)
    DLCircle(x, y, size, accent, segments, 2)


def _draw_penta(x, y, size, color, accent, offset_angle, R):
    DLCircleFilled(x, y, size, color, 5)
    DLCircle(x, y, size, accent, 5, 2)


def _draw_teardrop(x, y, size, color, accent, offset_angle, R):
    DLCircleFilled(x, y, size, color, 16)
    DLCircle(x, y, size, accent, 16, 2)
    angle = -(BASE_ANGLE + offset_angle)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    def rot(px, py):
        return R(px * cos_a - py * sin_a + x, px * sin_a + py * cos_a + y)

    p1 = rot(_TEARDROP_ARROW_UNIT[0][0] * size, _TEARDROP_ARROW_UNIT[0][1] * size)
    p2 = rot(_TEARDROP_ARROW_UNIT[1][0] * size, _TEARDROP_ARROW_UNIT[1][1] * size)
    p3 = rot(_TEARDROP_ARROW_UNIT[2][0] * size, _TEARDROP_ARROW_UNIT[2][1] * size)
    DLTriangleFilled(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1], color)
    DLLine(p1[0], p1[1], p2[0], p2[1], accent, 2.0)
    DLLine(p1[0], p1[1], p3[0], p3[1], accent, 2.0)


def _draw_tear(x, y, size, color, accent, offset_angle, R):
    # Hottest shape in the profile: avoid the identity transform calls and the DL wrapper layer.
    angle = -(BASE_ANGLE + offset_angle)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    pts = []
    for ux, uy in _TEAR_UNIT:
        px = ux * size
        py = uy * size
        pts.append((px * cos_a - py * sin_a + x, px * sin_a + py * cos_a + y))
    if R is not _identity:
        pts = [R(p[0], p[1]) for p in pts]
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = pts
    PyImGui.draw_list_add_quad_filled(x1, y1, x2, y2, x3, y3, x4, y4, color)
    PyImGui.draw_list_add_quad(x1, y1, x2, y2, x3, y3, x4, y4, accent, 2.0)


def _draw_square(x, y, size, color, accent, offset_angle, R):
    h = (size * SQRT_2) / 2
    x1, y1 = R(x - h, y - h)
    x2, y2 = R(x + h, y - h)
    x3, y3 = R(x + h, y + h)
    x4, y4 = R(x - h, y + h)
    DLQuadFilled(x1, y1, x2, y2, x3, y3, x4, y4, color)
    DLQuad(x1, y1, x2, y2, x3, y3, x4, y4, accent, 2.0)


def _draw_lock(x, y, size, color, accent, offset_angle, R):
    h = (size * SQRT_2) / 2
    e = h / 4
    shackle = R(x, y - h - e)
    DLCircle(shackle[0], shackle[1], size / 2, accent, 12, 3)
    x1, y1 = R(x - h, y - h)
    x2, y2 = R(x + h, y - h)
    x3, y3 = R(x + h, y + h)
    x4, y4 = R(x - h, y + h)
    DLQuadFilled(x1, y1, x2, y2, x3, y3, x4, y4, color)
    DLQuad(x1, y1, x2, y2, x3, y3, x4, y4, accent, 2.0)
    k1, l1 = R(x - e, y - e)
    k2, l2 = R(x + e, y - e)
    k3, l3 = R(x + e, y + e)
    k4, l4 = R(x - e, y + e)
    DLQuadFilled(k1, l1, k2, l2, k3, l3, k4, l4, accent)


def _draw_signpost(x, y, size, color, accent, offset_angle, R):
    h = (size * SQRT_2) / 2
    q = h / 2
    tq = h + q
    x1, y1 = R(x - tq, y - h)
    x2, y2 = R(x + tq, y - h)
    x3, y3 = R(x + tq, y + h)
    x4, y4 = R(x - tq, y + h)
    DLQuadFilled(x1, y1, x2, y2, x3, y3, x4, y4, color)
    DLQuad(x1, y1, x2, y2, x3, y3, x4, y4, accent, 2.0)
    a1 = R(x - h, y - q)
    a2 = R(x + h, y - q)
    DLLine(a1[0], a1[1], a2[0], a2[1], accent, 1.0)
    b1 = R(x - h, y)
    b2 = R(x + h, y)
    DLLine(b1[0], b1[1], b2[0], b2[1], accent, 1.0)


def _draw_scale(x, y, size, color, accent, offset_angle, R):
    h = (size * SQRT_2) / 2
    a1 = R(x, y + h)
    a2 = R(x, y - h)
    DLLine(a1[0], a1[1], a2[0], a2[1], color, 2.0)
    b1 = R(x - h, y - h)
    b2 = R(x + h, y - h)
    DLLine(b1[0], b1[1], b2[0], b2[1], color, 2.0)


def _draw_star(x, y, size, color, accent, offset_angle, R):
    scale = 1.2

    def p(angle_deg):
        a = math.radians(angle_deg) + offset_angle
        return R(math.cos(a) * scale * size + x, math.sin(a) * scale * size + y)

    q1 = [p(0), p(90), p(180), p(270)]
    q2 = [p(45), p(135), p(225), p(315)]
    u1 = (q1[0][0], q1[0][1], q1[1][0], q1[1][1], q1[2][0], q1[2][1], q1[3][0], q1[3][1])
    u2 = (q2[0][0], q2[0][1], q2[1][0], q2[1][1], q2[2][0], q2[2][1], q2[3][0], q2[3][1])
    DLQuad(*u1, accent, 3.0)
    DLQuad(*u2, accent, 3.0)
    DLQuadFilled(*u1, color)
    DLQuadFilled(*u2, color)


def _draw_tear2(x, y, size, color, accent, offset_angle, R):
    # Fuller teardrop (Compass "Tear2"): round body + a long triangular tail pointing along
    # offset_angle. Built from stubbed draw-list primitives (no ImGui path arcs).
    apex = R(math.cos(offset_angle) * size * 2 + x, math.sin(offset_angle) * size * 2 + y)
    ba = R(math.cos(offset_angle + math.pi / 2) * size + x, math.sin(offset_angle + math.pi / 2) * size + y)
    bb = R(math.cos(offset_angle - math.pi / 2) * size + x, math.sin(offset_angle - math.pi / 2) * size + y)
    DLCircleFilled(x, y, size, color, 16)
    DLTriangleFilled(apex[0], apex[1], ba[0], ba[1], bb[0], bb[1], color)
    DLCircle(x, y, size, accent, 16, 2.0)
    DLLine(apex[0], apex[1], ba[0], ba[1], accent, 2.0)
    DLLine(apex[0], apex[1], bb[0], bb[1], accent, 2.0)


_SHAPES: dict[str, Callable] = {
    "Triangle": _draw_triangle,
    "Circle": _draw_circle,
    "Teardrop": _draw_teardrop,
    "Square": _draw_square,
    "Penta": _draw_penta,
    "Tear": _draw_tear,
    "SignPost": _draw_signpost,
    "Lock": _draw_lock,
    "Scale": _draw_scale,
    "Star": _draw_star,
    "Tear2": _draw_tear2,
}

#: Shape names offered in the config editor.
SHAPE_NAMES: tuple[str, ...] = tuple(_SHAPES.keys())


def draw_marker(
    shape: str,
    x: float,
    y: float,
    size: float,
    color: RGBA,
    accent: RGBA,
    offset_angle: float = 0.0,
    map_rotation: float = 0.0,
) -> None:
    """Draw ``shape`` at screen ``(x, y)``. Unknown shapes fall back to a circle."""
    fn = _SHAPES.get(shape, _draw_circle)
    # Hot path (called once per agent): inline the pack cache lookups and skip building a
    # rotation transform entirely when the map does not rotate.
    c = _PACK_CACHE.get(color)
    if c is None:
        c = pack(color)
    a = _PACK_CACHE.get(accent)
    if a is None:
        a = pack(accent)
    R = _identity if not map_rotation else _make_rot(x, y, map_rotation)
    fn(x, y, size, c, a, offset_angle, R)


def draw_aura(x: float, y: float, radius: float, color: RGBA, segments: int = 24) -> None:
    """Draw a translucent range/aura ring (outline + fill) at screen ``(x, y)``."""
    c = pack(color)
    DLCircle(x, y, radius - 2, c, segments, 1.0)
    DLCircleFilled(x, y, radius, c, segments)


def segments_for_radius(radius: float) -> int:
    """Adaptive circle segment count by pixel radius (Mission Map perf detail)."""
    if radius < 18:
        return 12
    if radius < 35:
        return 16
    if radius < 70:
        return 24
    if radius < 130:
        return 32
    if radius < 220:
        return 48
    return 64
