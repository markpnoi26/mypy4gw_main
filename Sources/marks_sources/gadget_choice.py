"""Which gadget at these coordinates is the one we meant.

`Routines.Agents.GetNearestGadgetXY` sorts every gadget in range by distance and
returns the first, with no filter on what kind of gadget it is. In a room that
also holds a lever, a door or a signpost, the bot targets whichever happens to be
nearer and then interacts with it perfectly — no error, no reward, and a run that
reads as healthy afterwards.

Pure over a candidate list so the choice is testable without a client. The caller
reads the gadget array and does the targeting. Every outcome carries a reason
string, because the failure this exists to fix is invisible without one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    agent_id: int
    gadget_id: int
    x: float
    y: float


def gap(candidate: Candidate, scan_xy: tuple[float, float]) -> float:
    return math.hypot(candidate.x - scan_xy[0], candidate.y - scan_xy[1])


def seen(candidates) -> str:
    counts: dict[int, int] = {}
    for candidate in candidates:
        counts[candidate.gadget_id] = counts.get(candidate.gadget_id, 0) + 1
    return ", ".join(f"{gid} x{n}" if n > 1 else str(gid) for gid, n in sorted(counts.items()))


def pick(candidates, scan_xy: tuple[float, float], wanted_ids=()) -> tuple[int, str]:
    """The agent id to target, or 0, plus a reason fit for a log line.

    An empty `wanted_ids` keeps the old nearest-wins behaviour, which is what a
    lever or a res shrine wants — there is nothing to match against. Supplying
    ids is what stops a decoy gadget winning a chest.
    """
    where = f"({scan_xy[0]:.0f}, {scan_xy[1]:.0f})"
    if not candidates:
        return 0, f"no gadget in range of {where}"

    # Ties broken by agent id so the same room does not pick differently run to run.
    ordered = sorted(candidates, key=lambda c: (gap(c, scan_xy), c.agent_id))
    wanted = {int(gadget_id) for gadget_id in wanted_ids}

    if not wanted:
        winner = ordered[0]
        return winner.agent_id, f"nearest gadget {winner.gadget_id} at {gap(winner, scan_xy):.0f} from {where}"

    for candidate in ordered:
        if candidate.gadget_id in wanted:
            return (
                candidate.agent_id,
                f"gadget {candidate.gadget_id} at {gap(candidate, scan_xy):.0f} from {where}",
            )

    return 0, f"no gadget matching {sorted(wanted)} near {where}; saw {seen(ordered)}"
