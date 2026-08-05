"""Take turns at something only one account can do at a time.

A dungeon reward chest is a mutex: a second account interacting while the first
still has it open gets nothing. Broadcasting the order to eight accounts loses
seven rewards and does it silently — every message reports as delivered, and the
run looks healthy.

Pure state machine, so the sequencing is testable without a client. The caller
supplies the clock and one reading — is the current account still working — and
performs the send. Nothing here touches shared memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import IntEnum


class Turn(IntEnum):
    START = 0  # send the order to state.current
    WAIT = 1
    FINISHED = 2


@dataclass(slots=True)
class TurnConfig:
    # An account's "busy" reading dips through idle BETWEEN the interact finishing
    # and its follow-up loot appearing. Acting on the first idle frame hands the
    # chest to the next account while this one is still picking up. Require the
    # quiet to hold instead — same shape as latching on an observed change.
    quiet_ms: int = 2500
    # Between one account retiring and the next being ordered.
    settle_ms: int = 1500
    # One account that never reports done must not wedge the queue. Expiry moves
    # on and records why; it never fails the run.
    turn_timeout_ms: int = 60000


@dataclass(slots=True)
class TurnState:
    queue: list[str] = field(default_factory=list)
    current: str = ""
    started_ms: int = 0
    # 0 means "not currently idle" — the quiet window has not started.
    idle_since_ms: int = 0
    finished_ms: int = 0
    done: list[str] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)


def begin(state: TurnState, emails) -> None:
    state.queue = [str(email) for email in emails if email]
    state.current = ""
    state.started_ms = 0
    state.idle_since_ms = 0
    state.finished_ms = 0
    state.done = []
    state.timed_out = []


def retire(state: TurnState, now_ms: int, timed_out: bool) -> None:
    if not state.current:
        return
    (state.timed_out if timed_out else state.done).append(state.current)
    state.current = ""
    state.idle_since_ms = 0
    state.finished_ms = now_ms


def next_turn(state: TurnState, cfg: TurnConfig, now_ms: int, busy: bool) -> Turn:
    """One account at a time, and never two.

    START is returned exactly once per account, on the tick its turn opens, so a
    caller can send on START without needing its own guard against re-sending.
    """
    if state.current:
        if busy:
            state.idle_since_ms = 0
            if now_ms - state.started_ms >= cfg.turn_timeout_ms:
                retire(state, now_ms, timed_out=True)
            return Turn.WAIT
        if state.idle_since_ms == 0:
            state.idle_since_ms = now_ms
            return Turn.WAIT
        if now_ms - state.idle_since_ms < cfg.quiet_ms:
            return Turn.WAIT
        retire(state, now_ms, timed_out=False)
        return Turn.WAIT

    # The settle gap is measured from the last retirement, so it does not apply
    # before the first account — nothing has held the chest yet.
    if state.finished_ms and (now_ms - state.finished_ms) < cfg.settle_ms:
        return Turn.WAIT
    if not state.queue:
        return Turn.FINISHED

    state.current = state.queue.pop(0)
    state.started_ms = now_ms
    state.idle_since_ms = 0
    return Turn.START


def remaining(state: TurnState) -> int:
    return len(state.queue) + (1 if state.current else 0)


def summary(state: TurnState) -> str:
    line = f"{len(state.done)} done"
    if state.timed_out:
        line += f", {len(state.timed_out)} timed out ({', '.join(state.timed_out)})"
    if remaining(state):
        line += f", {remaining(state)} left"
    return line
