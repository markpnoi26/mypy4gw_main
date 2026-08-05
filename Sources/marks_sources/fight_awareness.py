"""What a leader-side bot must know about the fight its own party is having.

`leader_publish` skips party position 0, so the fight formation places every
member EXCEPT the leader. The leader is the anchor, not part of the blob — which
means a bot that keeps walking its route while the formation falls back leaves
its own body out in front of the mob the party just backed away from, and once it
passes `abandon_distance` the zone re-drops the pin onto wherever it got to.
That is "ran forward into the fight" in its purest form, and no amount of tuning
inside the zone can fix it, because the zone does not own the leader.

Pure over the snapshot dict so the decision is testable without a client;
`read()` is the only thing here that touches the game.
"""

from __future__ import annotations

import math
from enum import IntEnum

TRAVELING = "TRAVELING"
WITHDRAW_VERDICT = "WITHDRAW"

# Anything closer than this and the leader is already standing in the formation.
# Walking a shorter distance than the zone's own give_ground_step would have the
# leader chasing rounding error across the floor.
DEFAULT_REPOSITION_TOLERANCE = 150.0


class Stance(IntEnum):
    CLEAR = 0
    HOLD = 1
    WITHDRAW = 2


def read() -> dict | None:
    """The live snapshot, or None when nothing is publishing one.

    Published in-process by the fight publisher, which runs on the leader — the
    same process a leader-side bot runs in. No shared memory hop needed.
    """
    try:
        import HeroAI.globals as hero_globals
    except Exception:
        return None
    snapshot = getattr(hero_globals, "fight_zone_debug_snapshot", None)
    return snapshot if isinstance(snapshot, dict) else None


def stance(snapshot: dict | None) -> Stance:
    """CLEAR unless a zone is actually driving the party.

    `driving` is the publisher's own word for enabled AND active. With the
    feature off, or in dry-run with the overlay on, the snapshot still describes
    a zone nobody is standing in — a bot that held for that would never move
    again.
    """
    if not snapshot or not snapshot.get("driving"):
        return Stance.CLEAR
    if str(snapshot.get("state") or TRAVELING) == TRAVELING:
        return Stance.CLEAR
    if snapshot.get("giving_ground"):
        return Stance.WITHDRAW
    # The verdict is published whether or not health retreat is switched on, so
    # reading it unguarded would report a withdrawal the zone is never going to
    # perform.
    if snapshot.get("health_enabled") and str(snapshot.get("health_verdict") or "") == WITHDRAW_VERDICT:
        return Stance.WITHDRAW
    return Stance.HOLD


def anchor(snapshot: dict | None) -> tuple[float, float] | None:
    if not snapshot:
        return None
    point = snapshot.get("anchor")
    if not point or len(point) < 2:
        return None
    return (float(point[0]), float(point[1]))


def reposition_target(
    snapshot: dict | None,
    leader_xy: tuple[float, float],
    tolerance: float = DEFAULT_REPOSITION_TOLERANCE,
) -> tuple[float, float] | None:
    """Where the leader should walk to, or None to stand still.

    Only ever answers on WITHDRAW. Chasing the anchor on every stance would walk
    the leader FORWARD onto a freshly dropped engagement pin, which is the exact
    behaviour this module exists to prevent. Backwards is safe because the anchor
    only moves away from the enemies once ground has been given.
    """
    if stance(snapshot) is not Stance.WITHDRAW:
        return None
    point = anchor(snapshot)
    if point is None:
        return None
    if math.hypot(point[0] - leader_xy[0], point[1] - leader_xy[1]) <= tolerance:
        return None
    return point


def party_health(snapshot: dict | None) -> float:
    """Living-only mean, 0..1. Corpses are excluded upstream."""
    if not snapshot:
        return 1.0
    try:
        return float(snapshot.get("party_health", 1.0))
    except (TypeError, ValueError):
        return 1.0


def describe(snapshot: dict | None) -> str:
    """One line for the bot window, naming the reason and not just the state."""
    current = stance(snapshot)
    if current is Stance.CLEAR:
        return "route running"
    health = party_health(snapshot) * 100.0
    if current is Stance.WITHDRAW:
        deaths = int((snapshot or {}).get("health_pending_deaths", 0) or 0)
        because = f"{deaths} down" if deaths else f"party at {health:.0f}%"
        return f"giving ground - {because}"
    state = str((snapshot or {}).get("state") or "").lower()
    return f"holding - {state}, party at {health:.0f}%"
