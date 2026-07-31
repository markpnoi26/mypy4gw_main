"""Manual navmesh probe: drop a point, see where a body could actually stand.

The fight overlay draws the WANTED pin. Resolution runs downstream in
leader_publish and its answer only ever reaches shared memory, so a pin drawn
inside a wall says nothing about where the follower ends up. This puts the
wanted point and the resolved one on screen together.
"""

import math

import PyImGui

import HeroAI.globals as hero_globals
from Core import ConsoleLog
from Core import Overlay
from Core import Utils
from Core.Pathing import AutoPathing
from Core.Player import Player
from HeroAI.follow.placement import PlacementConfig
from HeroAI.follow.placement import resolve_placement
from HeroAI.follow.placement import ring_points
from HeroAI.follow.placement import standable
from HeroAI.follow.placement import walkable_from

MARKER_RADIUS = 60.0
RESOLVED_RADIUS = 95.0
SEARCH_RING_SEGMENTS = 24

GREEN = Utils.RGBToColor(60, 220, 90, 230)
AMBER = Utils.RGBToColor(255, 200, 60, 230)
RED = Utils.RGBToColor(255, 60, 60, 230)
BLUE = Utils.RGBToColor(60, 150, 255, 230)
FAINT = Utils.RGBToColor(200, 200, 200, 70)

probes: list[tuple[float, float]] = []
cfg = PlacementConfig()
draw_enabled = True
show_search_rings = False
cached_navmesh = None


def get_navmesh():
    """AutoPathing only populates its cache from get_path() coroutine pumps, so a
    standalone probe has to force the load itself."""
    global cached_navmesh
    if cached_navmesh is not None:
        return cached_navmesh
    autopath = AutoPathing()
    mesh = autopath.get_navmesh()
    if mesh is None:
        try:
            for _ in autopath.load_pathing_maps():
                pass
        except Exception:
            return None
        mesh = autopath.get_navmesh()
    cached_navmesh = mesh
    return cached_navmesh


def fight_anchor() -> tuple[float, float] | None:
    snapshot = getattr(hero_globals, "fight_zone_debug_snapshot", None)
    if not snapshot:
        return None
    anchor = snapshot.get("anchor")
    if not anchor:
        return None
    return (float(anchor[0]), float(anchor[1]))


def verdict(mesh, point: tuple[float, float], origin: tuple[float, float]):
    try:
        on_mesh = standable(mesh, point, cfg)
        reachable = walkable_from(mesh, origin, point, cfg)
        resolved = resolve_placement(mesh, point, origin, cfg)
    except Exception:
        return (False, False, None)
    return (on_mesh, reachable, resolved)


def marker_colour(on_mesh: bool, reachable: bool) -> int:
    if not on_mesh:
        return RED
    return GREEN if reachable else AMBER


def draw_probes(mesh, origin: tuple[float, float]) -> None:
    Overlay().BeginDraw()
    try:
        ox, oy = origin
        oz = Overlay().FindZ(ox, oy, 0)
        for point in probes:
            on_mesh, reachable, resolved = verdict(mesh, point, origin)
            px, py = point
            pz = Overlay().FindZ(px, py, 0)
            Overlay().DrawPoly3D(px, py, pz, MARKER_RADIUS, marker_colour(on_mesh, reachable), 16, 2.0, False)
            Overlay().DrawLine3D(ox, oy, oz, px, py, pz, FAINT, 1.0)

            if show_search_rings and not (on_mesh and reachable):
                for candidate in ring_points(cfg, point):
                    cx, cy = candidate
                    cz = Overlay().FindZ(cx, cy, 0)
                    ok = standable(mesh, candidate, cfg) and walkable_from(mesh, origin, candidate, cfg)
                    Overlay().DrawPoly3D(cx, cy, cz, 20.0, GREEN if ok else FAINT, 8, 1.0, False)

            if resolved is None:
                continue
            rx, ry = resolved
            rz = Overlay().FindZ(rx, ry, 0)
            Overlay().DrawPoly3D(rx, ry, rz, RESOLVED_RADIUS, BLUE, SEARCH_RING_SEGMENTS, 2.0, False)
            if math.hypot(rx - px, ry - py) > 1.0:
                Overlay().DrawLine3D(px, py, pz, rx, ry, rz, BLUE, 2.0)
    finally:
        Overlay().EndDraw()


def draw_controls(mesh, origin: tuple[float, float]) -> None:
    global draw_enabled, show_search_rings, cached_navmesh

    if PyImGui.button("ADD"):
        probes.append((float(origin[0]), float(origin[1])))
        ConsoleLog("PlacementProbe", f"Added ({origin[0]:.0f}, {origin[1]:.0f})")
    PyImGui.same_line(0, -1)
    if PyImGui.button("ADD FIGHT ANCHOR"):
        anchor = fight_anchor()
        if anchor is None:
            ConsoleLog("PlacementProbe", "No fight zone anchor published.")
        else:
            probes.append(anchor)
            ConsoleLog("PlacementProbe", f"Added anchor ({anchor[0]:.0f}, {anchor[1]:.0f})")
    PyImGui.same_line(0, -1)
    if PyImGui.button("RELEASE ALL"):
        probes.clear()

    draw_enabled = PyImGui.checkbox("Draw", draw_enabled)
    PyImGui.same_line(0, -1)
    show_search_rings = PyImGui.checkbox("Show search rings", show_search_rings)
    PyImGui.same_line(0, -1)
    if PyImGui.button("Reload navmesh"):
        cached_navmesh = None

    PyImGui.separator()
    cfg.margin = PyImGui.slider_float("contains margin", cfg.margin, 0.0, 120.0)
    cfg.los_margin = PyImGui.slider_float("LOS margin", cfg.los_margin, 0.0, 200.0)
    cfg.los_step = PyImGui.slider_float("LOS step", cfg.los_step, 20.0, 400.0)
    PyImGui.separator()

    if mesh is None:
        PyImGui.text("NO NAVMESH - nothing can be judged.")
        return

    PyImGui.text(f"Origin (you): {origin[0]:.0f}, {origin[1]:.0f}")
    PyImGui.text(f"Under you: {'ON MESH' if standable(mesh, origin, cfg) else 'OFF MESH'}")
    PyImGui.separator()

    if not PyImGui.begin_table("Probes", 6):
        return
    PyImGui.table_setup_column("X")
    PyImGui.table_setup_column("Y")
    PyImGui.table_setup_column("Stand")
    PyImGui.table_setup_column("Reach")
    PyImGui.table_setup_column("Nudge")
    PyImGui.table_setup_column("")
    PyImGui.table_headers_row()

    remove_index = None
    for index, point in enumerate(probes):
        on_mesh, reachable, resolved = verdict(mesh, point, origin)
        PyImGui.table_next_row()
        PyImGui.table_next_column()
        PyImGui.text(f"{point[0]:.0f}")
        PyImGui.table_next_column()
        PyImGui.text(f"{point[1]:.0f}")
        PyImGui.table_next_column()
        PyImGui.text("yes" if on_mesh else "NO")
        PyImGui.table_next_column()
        PyImGui.text("yes" if reachable else "NO")
        PyImGui.table_next_column()
        if resolved is None:
            PyImGui.text("UNPLACEABLE")
        else:
            shift = math.hypot(resolved[0] - point[0], resolved[1] - point[1])
            PyImGui.text("none" if shift <= 1.0 else f"{shift:.0f}u")
        PyImGui.table_next_column()
        if PyImGui.button(f"Release##{index}"):
            remove_index = index

    PyImGui.end_table()

    if remove_index is not None:
        probes.pop(remove_index)


def main():
    mesh = get_navmesh()
    origin = Player.GetXY()
    if origin is None:
        return
    origin = (float(origin[0]), float(origin[1]))

    if PyImGui.begin("Placement Probe"):
        draw_controls(mesh, origin)
    PyImGui.end()

    if draw_enabled and mesh is not None and probes:
        draw_probes(mesh, origin)


if __name__ == "__main__":
    main()
