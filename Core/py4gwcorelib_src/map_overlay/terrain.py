"""Map Overlay terrain — DXOverlay pathing/terrain render for each mode.

Holds the shared ``DXOverlay`` renderers and draws the pathing geometry with the mask + world
transform each mode needs:

- :meth:`Terrain.draw_mission` — rectangular mask clamped to the mission-map frame, world-space
  pan/zoom/scale from the axis-aligned projection, plus the mega-zoom fill + second renderer.
- :meth:`Terrain.draw_compass` — circular mask (radius = compass size · culling / Compass), the
  rotated world transform from ``ComputedPathingGeometryToScreen``.

Both paths reconfigure the mask type each frame so switching modes at runtime is clean; the
host calls :meth:`invalidate` on a mode/colour change so geometry rebuilds.
"""

from typing import Optional

from Core.DXOverlay import DXOverlay
from Core.Map import Map
from Core.enums import Range
from Core.py4gwcorelib_src.Color import Color

from .model import RGBA
from .model import TerrainConfig
from .projection import AxisAlignedProjection
from .projection import RotatingProjection


def _dx(rgba: RGBA) -> int:
    return Color(int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])).to_dx_color()


def _packed(rgba: RGBA) -> int:
    return Color(int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])).to_color()


class Terrain:
    """Owns the pathing renderers.

    The two modes configure a renderer **incompatibly** — the mission map drives it in
    world space (``set_world_space(True)``, pan/zoom/scale, rectangular mask) while the
    compass drives it with a rotated transform and a circular mask and never enables world
    space at all. Sharing one renderer let mission-mode state leak into compass mode and
    threw its alignment off, so each mode gets its own instance and is configured exactly
    the way its original widget did.
    """

    def __init__(self) -> None:
        # mission-map renderers (world space)
        self.renderer = DXOverlay()
        self.mega_renderer = DXOverlay()
        self.renderer.world_space.set_world_space(True)
        self.mega_renderer.world_space.set_world_space(True)

        # compass renderer — deliberately left in its default transform mode, as Compass+ did
        self.compass_renderer = DXOverlay()

        self._built_map_id: int = -1
        self._built_color: Optional[RGBA] = None
        self._compass_primitives_set: bool = False

    def invalidate(self) -> None:
        """Force geometry to rebuild (mode switch / terrain colour change)."""
        self._built_map_id = -1
        self._built_color = None
        self._compass_primitives_set = False

    # ── mission-map ──────────────────────────────────────────────────────────────────────
    def draw_mission(self, proj: AxisAlignedProjection, terrain: TerrainConfig) -> None:
        if not terrain.enabled:
            return

        map_id = Map.GetMapID()
        if self._built_map_id != map_id or self._built_color != terrain.color:
            color_dx = _dx(terrain.color)
            self.renderer.build_pathing_trapezoid_geometry(color_dx)
            self.mega_renderer.build_pathing_trapezoid_geometry(color_dx)
            self._built_map_id = map_id
            self._built_color = terrain.color

        self.renderer.inverse_rendering(terrain.inverted)
        self.mega_renderer.inverse_rendering(terrain.inverted)

        self.renderer.mask.set_rectangle_mask(True)
        self.mega_renderer.mask.set_rectangle_mask(True)
        self.renderer.mask.set_rectangle_mask_bounds(proj.left, proj.top, proj.width, proj.height)
        self.mega_renderer.mask.set_rectangle_mask_bounds(proj.left, proj.top, proj.width, proj.height)

        origin_x, origin_y = proj.game_to_screen(0.0, 0.0)
        self.renderer.world_space.set_pan(origin_x, origin_y)
        self.mega_renderer.world_space.set_pan(origin_x, origin_y)
        zoom = Map.MissionMap.GetAdjustedZoom(proj.zoom, zoom_offset=proj.mega_zoom)
        self.renderer.world_space.set_zoom(zoom / 100.0)
        self.mega_renderer.world_space.set_zoom(zoom / 100.0)
        self.renderer.world_space.set_scale(proj.scale_x)
        self.mega_renderer.world_space.set_scale(proj.scale_x)

        if (proj.zoom + proj.mega_zoom) > 3.5:
            self.mega_renderer.DrawQuadFilled(
                proj.left,
                proj.top,
                proj.right,
                proj.top,
                proj.right,
                proj.bottom,
                proj.left,
                proj.bottom,
                _packed(terrain.zoom_fill_color),
            )
            self.mega_renderer.render()
        else:
            self.renderer.render()

    # ── compass ──────────────────────────────────────────────────────────────────────────
    def draw_compass(self, proj: RotatingProjection, terrain: TerrainConfig) -> None:
        if not terrain.enabled or not Map.IsMapReady():
            return

        r = self.compass_renderer
        if not self._compass_primitives_set:
            r.build_pathing_trapezoid_geometry(_dx(terrain.color))
            self._compass_primitives_set = True

        r.inverse_rendering(terrain.inverted)

        map_bounds = Map.GetMapBoundaries()
        x_off, y_off, zoom = Map.MiniMap.MapProjection.ComputedPathingGeometryToScreen(
            map_bounds,
            proj.player_pos[0],
            proj.player_pos[1],
            proj.center[0],
            proj.center[1],
            proj.size,
            proj.rotation,
        )
        r.world_space.set_zoom(zoom)
        r.world_space.set_rotation(-proj.rotation)
        r.world_space.set_pan(proj.center[0] + x_off, proj.center[1] - y_off)

        r.mask.set_circular_mask(True)
        r.mask.set_mask_radius(proj.size * proj.position.culling / float(Range.Compass.value))
        r.mask.set_mask_center(proj.center[0], proj.center[1])

        r.render()
