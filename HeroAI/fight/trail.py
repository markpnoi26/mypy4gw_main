"""Where the leader came from — a breadcrumb trail sampled while travelling."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from dataclasses import field


@dataclass(slots=True)
class TrailConfig:
    # Don't record standing still; a cluster of near-identical points would make
    # the lookback walk terminate a few units behind the leader.
    min_sample_distance: float = 120.0
    max_points: int = 32
    # How far back along the walked path counts as "came from". Long enough to
    # survive the last few steps of a pull, short enough not to reach around a
    # corner into a direction the party never actually approached from.
    approach_lookback: float = 900.0
    # A trail shorter than this cannot describe an approach direction.
    min_usable_approach: float = 300.0
    # Older than this and the party has been standing around; the direction they
    # arrived from is no longer where they would retreat to.
    max_point_age_ms: int = 30000
    # A jump larger than this is a map change or a teleport, not walking.
    max_step_distance: float = 2000.0


TRAIL_CFG = TrailConfig()


@dataclass(slots=True)
class LeaderTrail:
    points: deque = field(default_factory=lambda: deque(maxlen=TRAIL_CFG.max_points))

    def clear(self) -> None:
        self.points.clear()


def sample_trail(trail: LeaderTrail, cfg: TrailConfig, leader_xy: tuple[float, float], now_ms: int) -> None:
    x = float(leader_xy[0])
    y = float(leader_xy[1])

    if not trail.points:
        trail.points.append((x, y, now_ms))
        return

    last_x, last_y, last_ms = trail.points[-1]
    step = math.hypot(x - last_x, y - last_y)

    if step > cfg.max_step_distance or now_ms < last_ms:
        trail.clear()
        trail.points.append((x, y, now_ms))
        return

    if step < cfg.min_sample_distance:
        return

    trail.points.append((x, y, now_ms))


def approach_point(trail: LeaderTrail, cfg: TrailConfig, now_ms: int) -> tuple[float, float] | None:
    """Walk back along the trail until `approach_lookback` units have been
    covered. Returns None when the trail is too short or too stale to mean
    anything — callers must have a fallback."""
    if len(trail.points) < 2:
        return None

    points = list(trail.points)
    travelled = 0.0
    for index in range(len(points) - 1, 0, -1):
        current_x, current_y, _ = points[index]
        previous_x, previous_y, previous_ms = points[index - 1]
        if (now_ms - previous_ms) > cfg.max_point_age_ms:
            break
        travelled += math.hypot(current_x - previous_x, current_y - previous_y)
        if travelled >= cfg.approach_lookback:
            return (previous_x, previous_y)

    oldest_x, oldest_y, oldest_ms = points[0]
    if (now_ms - oldest_ms) > cfg.max_point_age_ms:
        return None
    newest_x, newest_y, _ = points[-1]
    if math.hypot(newest_x - oldest_x, newest_y - oldest_y) < cfg.min_usable_approach:
        return None
    return (oldest_x, oldest_y)
