"""Is the party losing badly enough to give ground, and can it still afford to.

Health cannot drive the pin the way the trigger rings do. A ring releases itself
— one step back moves it off the mob — while a health threshold is still true
after the step, because backing up heals nobody. Left as a condition it simply
ratchets, which is how a withdrawal turns into a rout that never stops.

So health gets a finite BUDGET instead: a fixed number of steps per episode,
refilled only on an observed recovery past a higher threshold, and worth nothing
at all once spent. Termination is structural rather than tuned — no timer decides
anything here, because a timer is what rebuilds the ratchet one refill at a time.

Three calls, deliberately separate. `observe` runs every tick and cannot be
missed; `verdict` is idempotent and answers every tick; `spend` runs ONLY when
the step is really taken. Folding them together is a live bug rather than a
tidiness question: the ground controller sits behind a 5-18s dwell and evaluates
every frame in between, so a verdict that spent on evaluation would empty the
budget in three frames and buy the party one 250u step for the whole fight.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import IntEnum


@dataclass(slots=True)
class HealthRetreatConfig:
    # Opt-in like the zone itself. Off still computes and publishes a verdict, so
    # the controller can be watched through the tab before it moves anything.
    enabled: bool = False
    arm_fraction: float = 0.60
    # Strictly above arm_fraction, and the gap IS the release condition: health
    # hovering on the arm threshold must not be able to cycle the budget.
    release_fraction: float = 0.75
    # The hard bound on everything below. Three steps is 750u at the authored
    # give_ground_step, which is a real withdrawal and not a rout.
    max_steps: int = 3
    # How far the mean must climb before the last step counts as working. Paired
    # with the latch in HealthRetreatState, this is what stops a flat line
    # spending the whole budget in three consecutive dwells. Never zero: at zero
    # any upward flicker reads as the withdrawal working.
    recover_margin: float = 0.05


HEALTH_CFG = HealthRetreatConfig()


class HealthVerdict(IntEnum):
    CLEAR = 0
    WITHDRAW = 1
    # Losing, budget spent. Vetoes the advance and moves nothing — the party
    # stands and fights where it got to rather than creeping back into a fight it
    # is losing or continuing to run from one it cannot outrun.
    HOLD = 2


@dataclass(slots=True)
class HealthRetreatState:
    armed: bool = False
    steps_used: int = 0
    health_at_last_step: float = 1.0
    # Party positions currently reading zero, so a death is answered once rather
    # than every tick the corpse is on the ground.
    dead_positions: set[int] = field(default_factory=set)
    # Deaths seen since the last step was taken. Accumulated rather than read
    # live because the two clocks differ: deaths land on the observation tick,
    # steps land after a dwell, and a death that happened in between must be
    # answered exactly once.
    pending_deaths: int = 0
    last_mean: float = 1.0
    alive: int = 0
    dead: int = 0

    def release(self) -> None:
        """End the episode: budget back, latch back, nothing armed.

        `dead_positions` deliberately survives. It is a live observation, not
        episode state — clearing it would make every corpse already on the ground
        read as a fresh death on the next tick and arm the next fight instantly.
        """
        self.armed = False
        self.steps_used = 0
        self.health_at_last_step = 1.0
        self.pending_deaths = 0


def health_fraction(health) -> float | None:
    """Read one member's health out of a shared-memory HealthStruct, or None
    when the slot has not reported yet.

    `Current` is ALREADY a fraction — `Agent.GetHealth` returns `living.hp`,
    which is 0..1, and `draw_health_bar` feeds it straight to a progress bar.
    `Max` is absolute HP and is only a has-this-slot-reported check. Dividing
    the two is the obvious-looking mistake and yields about 0.002, which reads
    as 0% and can never cross any threshold in either direction.

    Duck-typed rather than imported so this module stays stdlib-only and its
    tests stay free of the shared-memory stack.
    """
    if float(getattr(health, "Max", 0.0) or 0.0) <= 0.0:
        return None
    return float(getattr(health, "Current", 0.0) or 0.0)


def alive_mean(party_health: dict[int, float]) -> float:
    """Mean over members who are not dead. 1.0 when nothing is known: an absent
    reading must never argue for retreat.

    "Alive" here means hp above zero, which is NOT what `is_living` means in this
    framework — that is the agent TYPE, and a living agent is still living when
    it is dead. Named away from it deliberately.

    Corpses are excluded rather than averaged in at zero. A dead member's zero
    never recovers during a fight, so two of them cap an eight-man mean at 0.75
    and hold any threshold below that down permanently — the retreat then has no
    release condition left at all. Deaths are answered as events in `verdict`
    instead, which is bounded by the budget where a depressed mean is not.
    """
    standing = [value for value in party_health.values() if value > 0.0]
    if not standing:
        return 1.0
    return sum(standing) / len(standing)


def newly_dead(state: HealthRetreatState, party_health: dict[int, float]) -> int:
    """Members who reached zero since the last observation.

    A resurrected member leaves the set, so dying twice counts twice.
    """
    down = {position for position, value in party_health.items() if value <= 0.0}
    fresh = len(down - state.dead_positions)
    state.dead_positions = down
    return fresh


def observe(state: HealthRetreatState, party_health: dict[int, float]) -> None:
    """Take the reading. Every tick, exactly once — a death seen on no tick at
    all is a death never answered."""
    state.last_mean = alive_mean(party_health)
    state.pending_deaths += newly_dead(state, party_health)
    state.dead = len(state.dead_positions)
    state.alive = len(party_health) - state.dead


def verdict(state: HealthRetreatState, cfg: HealthRetreatConfig) -> HealthVerdict:
    """What health wants, given the last observation. Idempotent: it moves the
    arm/release latch but never the budget."""
    # A death arms regardless of the mean. Excluding corpses means a death RAISES
    # the average, so the level test alone would disarm at the exact moment it
    # matters most: the monk spiked while everyone else reads 90%.
    if state.pending_deaths:
        state.armed = True
    elif not state.armed:
        if state.last_mean >= cfg.arm_fraction:
            return HealthVerdict.CLEAR
        state.armed = True

    # Not while a death is still unanswered, for the reason above — releasing
    # there would refill the budget in the same breath as the step owed to it.
    if not state.pending_deaths and state.last_mean >= cfg.release_fraction:
        state.release()
        return HealthVerdict.CLEAR

    if state.steps_used >= cfg.max_steps:
        return HealthVerdict.HOLD

    if not state.pending_deaths:
        # Back inside the band: whatever ground has been given is doing its job,
        # so hold it rather than spending more of a budget that does not refill
        # until the release threshold.
        if state.last_mean >= cfg.arm_fraction:
            return HealthVerdict.HOLD
        # Still below the threshold but climbing against the value latched at the
        # last step. The pre-action latch from runtime-behaviour.md: require an
        # observed change rather than trusting a deadline. Deliberately not
        # load-bearing — an unreadable trend falls through to the budget, which
        # stops it anyway.
        if state.last_mean > state.health_at_last_step + cfg.recover_margin:
            return HealthVerdict.HOLD

    return HealthVerdict.WITHDRAW


def spend(state: HealthRetreatState) -> None:
    """Charge the budget for a step that was actually taken."""
    state.steps_used += 1
    state.health_at_last_step = state.last_mean
    state.pending_deaths = 0
