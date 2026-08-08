"""Sequence a torch run: walk brazier to brazier, relight when the buff dies.

The SoO dark rooms are a chain of braziers lit from a carried torch. The torch
buff expires on a timer, and a brazier reached without the buff cannot be lit —
the recovery is walking back to the last brazier that IS lit, refreshing the
buff there, and resuming. Getting that detour wrong strands the leader mid-room
with a dead torch, which reads as "bot stopped advancing" with no error.

Pure state machine, so the sequencing is testable without a client. The caller
owns movement, gadget interaction and the buff reading; this decides only where
to walk and when a brazier counts as lit. A brazier that will not light is
skipped and recorded, never fatal — the party can usually fight through one dark
pocket, and stopping the run there costs the whole chest.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import IntEnum


class Step(IntEnum):
    WALK = 0  # keep walking toward goal(state)
    LIGHT = 1  # interact with the brazier at goal(state), then report_light
    DONE = 2


@dataclass(slots=True)
class RouteConfig:
    max_retries: int = 3


@dataclass(slots=True)
class RouteState:
    points: list[tuple[float, float]] = field(default_factory=list)
    idx: int = 0
    last_lit: int = -1
    retries: int = 0
    relighting: bool = False
    walking: bool = True
    failed: list[int] = field(default_factory=list)


def begin(state: RouteState, points) -> None:
    state.points = [(float(x), float(y)) for x, y in points]
    state.idx = 0
    state.last_lit = -1
    state.retries = 0
    state.relighting = False
    state.walking = True
    state.failed = []


def finished(state: RouteState) -> bool:
    return state.idx >= len(state.points)


def goal(state: RouteState) -> tuple[float, float]:
    """Only meaningful while the route is unfinished — check `finished` first.
    The last advance() pushes idx past the end, so a caller that reads the goal
    before asking next_step crashes on the tick after the final brazier."""
    if state.relighting:
        return state.points[state.last_lit]
    return state.points[state.idx]


def start_relight(state: RouteState) -> None:
    """Divert to the last lit brazier — or straight to a retry when there is
    none yet, which is the first brazier failing to light."""
    state.walking = True
    state.relighting = state.last_lit >= 0


def advance(state: RouteState) -> None:
    state.idx += 1
    state.retries = 0
    state.relighting = False
    state.walking = True


def next_step(state: RouteState, arrived: bool, buff_active: bool) -> Step:
    """Call each tick while walking. `arrived` is against goal(state)."""
    if state.idx >= len(state.points):
        return Step.DONE

    if state.walking:
        # First brazier is lit from the carried torch, not from the buff, and a
        # relight walk happens BECAUSE the buff is down — neither gets aborted.
        needs_buff = state.idx > 0 and not state.relighting
        if needs_buff and not buff_active and state.last_lit >= 0:
            state.relighting = True
            return Step.WALK
        if not arrived:
            return Step.WALK
        state.walking = False

    return Step.LIGHT


def report_light(state: RouteState, cfg: RouteConfig, found_gadget: bool, buff_active: bool) -> None:
    """Call once after the LIGHT interaction settles."""
    if state.relighting:
        # The refresh itself is not scored; the retry happens back at the
        # brazier that failed.
        state.relighting = False
        state.walking = True
        return

    lit = found_gadget and (state.idx == 0 or buff_active)
    if lit:
        state.last_lit = state.idx
        advance(state)
        return

    state.retries += 1
    if state.retries >= cfg.max_retries:
        state.failed.append(state.idx)
        advance(state)
        return
    start_relight(state)


def summary(state: RouteState) -> str:
    line = f"{state.idx}/{len(state.points)}"
    if state.relighting:
        line += f" (relighting at {state.last_lit + 1})"
    if state.failed:
        line += f", failed: {[i + 1 for i in state.failed]}"
    return line
