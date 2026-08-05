"""Steering a runner past the mobs it refuses to fight.

Terrain is already solved by the time this module is asked anything: the mover
autopaths every leg, so the waypoints it hands out are on walkable ground. What
a path computed a second ago cannot know is that a foe has since parked on it.
Guild Wars bodies are solid, so the runner stops dead against one while the
mover happily re-issues the same move command into the obstruction, and the leg
expires with the character standing still.

Two questions, then, and nothing else:

    detour()   is something standing on the next stride, and which way round?
    dwell_ms() has the character actually stopped, or is it merely slow?

Both are pure over explicit inputs so the geometry is testable without a client.
Reading the agents, checking an answer against the navmesh and issuing the move
all stay with the caller.

Angles are measured off the heading toward the waypoint, and `side` is positive
to the LEFT of it. Every function here shares that frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Blocker:
    x: float
    y: float
    radius: float


@dataclass
class AvoidanceConfig:
    """How much daylight to insist on, and how far to go to get it.

    `clearance` is measured between hitbox edges, so the room a single blocker
    demands is `clearance + blocker.radius`.
    """

    lookahead: float = 700.0
    clearance: float = 144.0
    min_detour: float = 90.0
    max_detour: float = 480.0
    retreat: float = 320.0
    dwell_radius: float = 60.0


DEFAULT = AvoidanceConfig()


@dataclass(frozen=True)
class Detour:
    x: float
    y: float
    reason: str
    blockers: int
    shift: float


@dataclass
class Dwell:
    anchor: Point | None = None
    since_ms: float = 0.0


def heading(origin: Point, waypoint: Point) -> Point | None:
    dx = waypoint[0] - origin[0]
    dy = waypoint[1] - origin[1]
    length = math.hypot(dx, dy)
    if length < 1.0:
        return None
    return (dx / length, dy / length)


def frame(origin: Point, waypoint: Point, point: Point) -> tuple[float, float] | None:
    """`point` expressed as (along, side) relative to the stride."""
    direction = heading(origin, waypoint)
    if direction is None:
        return None
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    return (dx * direction[0] + dy * direction[1], dx * -direction[1] + dy * direction[0])


def offenders(
    origin: Point,
    waypoint: Point,
    blockers: list[Blocker],
    config: AvoidanceConfig = DEFAULT,
) -> list[tuple[Blocker, float, float]]:
    """Every blocker standing on the next stride, as (blocker, along, side).

    A blocker slightly BEHIND the character still counts while its body overlaps
    hers — that is the shape of being shoved from behind into a doorway, and it
    is the one case where refusing to sidestep leaves the run wedged forever.
    """
    direction = heading(origin, waypoint)
    if direction is None:
        return []

    reach = min(math.dist(origin, waypoint), config.lookahead)
    found: list[tuple[Blocker, float, float]] = []
    for blocker in blockers:
        dx = blocker.x - origin[0]
        dy = blocker.y - origin[1]
        along = dx * direction[0] + dy * direction[1]
        side = dx * -direction[1] + dy * direction[0]
        room = config.clearance + blocker.radius
        if along > reach or along < -room:
            continue
        if abs(side) >= room:
            continue
        found.append((blocker, along, side))
    return found


def retreat_point(origin: Point, waypoint: Point, config: AvoidanceConfig = DEFAULT) -> Point | None:
    direction = heading(origin, waypoint)
    if direction is None:
        return None
    return (origin[0] - direction[0] * config.retreat, origin[1] - direction[1] * config.retreat)


def sidestep(
    origin: Point,
    waypoint: Point,
    distance: float,
    to_the_left: bool,
    config: AvoidanceConfig = DEFAULT,
) -> Point | None:
    """A step across the heading, for a stall no blocker explains.

    Scenery, a hero, a ledge lip — the scan cannot see why, only that nothing is
    moving, so the answer is to try one side and then the other rather than to
    reason about a cause we do not have.
    """
    direction = heading(origin, waypoint)
    if direction is None:
        return None
    sign = 1.0 if to_the_left else -1.0
    normal = (-direction[1], direction[0])
    forward = config.clearance
    return (
        origin[0] + direction[0] * forward + normal[0] * sign * distance,
        origin[1] + direction[1] * forward + normal[1] * sign * distance,
    )


def mirror(origin: Point, waypoint: Point, point: Point) -> Point | None:
    """The same step taken round the other side of the stride."""
    direction = heading(origin, waypoint)
    offsets = frame(origin, waypoint, point)
    if direction is None or offsets is None:
        return None
    along, side = offsets
    normal = (-direction[1], direction[0])
    return (
        origin[0] + direction[0] * along - normal[0] * side,
        origin[1] + direction[1] * along - normal[1] * side,
    )


def is_clear(point: Point, blockers: list[Blocker], config: AvoidanceConfig = DEFAULT) -> bool:
    return all(math.dist(point, (blocker.x, blocker.y)) >= config.clearance + blocker.radius for blocker in blockers)


def candidate_shifts(config: AvoidanceConfig = DEFAULT) -> list[float]:
    step = max(config.min_detour, 1.0)
    return [step * n for n in range(1, int(config.max_detour / step) + 1)]


def detour(
    origin: Point,
    waypoint: Point,
    blockers: list[Blocker],
    config: AvoidanceConfig = DEFAULT,
) -> Detour | None:
    """Where to aim instead, or None when the stride is already clear.

    Two separate decisions, and conflating them was the first version's bug.
    `offenders` decides WHETHER to leave the path — only a body actually in the
    corridor counts. The lane search below decides WHERE, and it tests the aim
    point against EVERY blocker, not just the ones in the corridor: stepping
    round the foe in the doorway into the one beside it is not avoidance. That
    is also what makes "boxed in" reachable — a shift that clears nobody at any
    distance means there is no lane, and the answer is to back off.

    The aim is placed beside the NEAREST offender rather than beside the
    waypoint. Aiming at the far end would leave the lateral offset at the point
    of passing only a fraction of the shift, which is how a runner clips the
    very body it thought it had gone round. The gate re-asks every tick, so the
    aim swings wider as the offender is approached.
    """
    found = offenders(origin, waypoint, blockers, config)
    if not found:
        return None

    direction = heading(origin, waypoint)
    if direction is None:
        return None
    normal = (-direction[1], direction[0])
    forward = max(min(along for _, along, _ in found), config.clearance)

    for shift in candidate_shifts(config):
        for sign, reason in ((1.0, "left"), (-1.0, "right")):
            point = (
                origin[0] + direction[0] * forward + normal[0] * sign * shift,
                origin[1] + direction[1] * forward + normal[1] * sign * shift,
            )
            if is_clear(point, blockers, config):
                return Detour(point[0], point[1], reason, len(found), shift)

    point = retreat_point(origin, waypoint, config)
    if point is None:
        return None
    return Detour(point[0], point[1], "retreat", len(found), config.retreat)


def dwell_ms(state: Dwell, position: Point, now_ms: float, config: AvoidanceConfig = DEFAULT) -> float:
    """How long the character has stayed inside `dwell_radius` of one spot.

    Latched on an observed position rather than timed from the move command. A
    throttled ACTION queue and a long path leg both look like a stall to a timer
    and neither is one; a character that has not moved has not moved.
    """
    if state.anchor is None or math.dist(position, state.anchor) > config.dwell_radius:
        state.anchor = position
        state.since_ms = now_ms
        return 0.0
    return max(0.0, now_ms - state.since_ms)


def describe(plan: Detour | None, dwell: float = 0.0) -> str:
    if plan is None:
        return "route clear" if dwell < 1000.0 else f"clear but not moving for {dwell / 1000.0:.1f}s"
    if plan.reason == "retreat":
        return f"boxed in by {plan.blockers} - backing off"
    return f"{plan.blockers} in the way - stepping {plan.reason} {plan.shift:.0f}"
