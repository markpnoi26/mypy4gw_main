"""The budget that stops a health retreat becoming a rout.

Every test here exists because a level threshold on party health has no release
condition — backing up heals nobody — and the previous attempt at this backed up
until it hit the distance cap and then sat there.

Runs without a client: test/nativestub.py serves stubs/*.pyi as the native
modules, installed by test/conftest.py before collection.
See .claude/skills/test-harness.md.
"""

from HeroAI.fight import health_retreat
from HeroAI.fight.health_retreat import HealthVerdict

CFG = health_retreat.HEALTH_CFG

# Derived from the thresholds rather than written down, so a retune moves the
# fixtures with it instead of quietly testing the wrong band.
LOSING = CFG.arm_fraction - 0.2
BAND = (CFG.arm_fraction + CFG.release_fraction) / 2.0
RECOVERED = CFG.release_fraction + 0.05


def party(*fractions):
    return {index + 1: float(value) for index, value in enumerate(fractions)}


def fresh():
    return health_retreat.HealthRetreatState()


def act(state, party_health):
    """One full controller cycle: read, decide, and charge the budget only if it
    decided to move. The three are separate in the module for the reason
    test_the_verdict_does_not_spend_the_budget_on_its_own exists."""
    health_retreat.observe(state, party_health)
    answer = health_retreat.verdict(state, CFG)
    if answer is HealthVerdict.WITHDRAW:
        health_retreat.spend(state)
    return answer


class FakeHealth:
    """The three fields of a shared-memory HealthStruct this reads.

    Current is a FRACTION (Agent.GetHealth -> living.hp); Max is absolute HP.
    """

    def __init__(self, current, maximum):
        self.Current = current
        self.Max = maximum


def test_health_is_read_as_a_fraction_not_divided_by_max():
    """The bug that pinned every reading at 0%.

    `Current` is already 0..1 and `Max` is absolute HP, so dividing gives about
    0.002 — which reads as 0%, can never cross a threshold in either direction,
    and takes `engagement.party_under_fire` down with it, since a 0.02 delta is
    unreachable at that scale.
    """
    assert health_retreat.health_fraction(FakeHealth(0.85, 480.0)) == 0.85
    assert health_retreat.health_fraction(FakeHealth(1.0, 620.0)) == 1.0
    assert health_retreat.health_fraction(FakeHealth(0.0, 480.0)) == 0.0

    scaled = health_retreat.health_fraction(FakeHealth(0.85, 480.0))
    assert scaled > CFG.arm_fraction, "a healthy member must not read as an emergency: %r" % scaled


def test_an_unreported_slot_is_absent_rather_than_dead():
    """Max of zero means the account has not published yet. Recording it as 0.0
    would make an empty slot a corpse and arm the retreat on a full party."""
    assert health_retreat.health_fraction(FakeHealth(0.0, 0.0)) is None
    assert health_retreat.health_fraction(FakeHealth(0.9, 0.0)) is None


def test_a_healthy_party_has_no_opinion():
    state = fresh()
    assert act(state, party(1.0, 0.9, 0.95)) is HealthVerdict.CLEAR
    assert state.steps_used == 0
    assert state.armed is False


def test_the_verdict_does_not_spend_the_budget_on_its_own():
    """The structural reason observe/verdict/spend are three calls.

    The ground controller sits behind a 5-18s dwell and evaluates on every frame
    in between. A verdict that charged the budget as it answered would empty it
    in three frames, and the party would get one 250u step for the whole fight
    while the readout claimed the budget was spent.
    """
    state = fresh()
    health_retreat.observe(state, party(LOSING))
    for _ in range(200):
        assert health_retreat.verdict(state, CFG) is HealthVerdict.WITHDRAW
    assert state.steps_used == 0

    health_retreat.spend(state)
    assert state.steps_used == 1


def test_a_death_during_the_dwell_is_answered_once_and_not_lost():
    """Deaths land on the observation tick, steps land after the dwell. A death
    in between must survive the wait and then be spent exactly once."""
    state = fresh()
    spiked = party(1.0, 1.0, 1.0, 0.0)
    health_retreat.observe(state, spiked)
    for _ in range(50):
        assert health_retreat.verdict(state, CFG) is HealthVerdict.WITHDRAW
    health_retreat.spend(state)

    health_retreat.observe(state, spiked)
    assert health_retreat.verdict(state, CFG) is HealthVerdict.CLEAR


def test_a_flat_line_cannot_spend_more_than_the_budget():
    """The anti-rout invariant, and the reason this module exists.

    A level trigger is still true after the step it caused, so without a budget
    this returns WITHDRAW on every tick for the rest of the fight.
    """
    state = fresh()
    losing = party(LOSING, LOSING, LOSING, LOSING)
    verdicts = [act(state, losing) for _ in range(50)]

    assert verdicts.count(HealthVerdict.WITHDRAW) == CFG.max_steps
    assert set(verdicts[CFG.max_steps :]) == {HealthVerdict.HOLD}


def test_a_spent_budget_holds_even_as_it_gets_worse():
    """Worse is not more authority. Past the budget the party stands and fights
    where it got to — there is no outrunning a mob that moves at your speed."""
    state = fresh()
    # Spend exactly the budget, whatever it is. Hardcoding three steps here
    # tested the number rather than the rule, and passed for the wrong reason
    # the moment the budget moved.
    for step in range(CFG.max_steps):
        worsening = LOSING - (step * 0.02)
        assert act(state, party(worsening)) is HealthVerdict.WITHDRAW
    for fraction in (LOSING / 2.0, LOSING / 3.0, 0.05):
        assert act(state, party(fraction)) is HealthVerdict.HOLD


def test_the_budget_refills_only_on_an_observed_recovery():
    """Hysteresis, and the reason there is no timer anywhere in this module.

    Mid-band is recovering but not recovered. Refilling there would let health
    that hovers around the arm threshold cycle the budget indefinitely, which is
    the ratchet rebuilt one refill at a time.
    """
    state = fresh()
    assert act(state, party(LOSING)) is HealthVerdict.WITHDRAW
    assert act(state, party(BAND)) is HealthVerdict.HOLD
    assert state.steps_used == 1, "the band must not refill the budget"

    assert act(state, party(RECOVERED)) is HealthVerdict.CLEAR
    assert state.steps_used == 0
    assert state.armed is False


def test_a_second_spike_after_a_recovery_gets_a_full_budget():
    state = fresh()
    for _ in range(CFG.max_steps):
        assert act(state, party(LOSING)) is HealthVerdict.WITHDRAW
    assert act(state, party(LOSING)) is HealthVerdict.HOLD

    assert act(state, party(RECOVERED)) is HealthVerdict.CLEAR

    for _ in range(CFG.max_steps):
        assert act(state, party(LOSING)) is HealthVerdict.WITHDRAW


def test_dead_members_do_not_permanently_arm_the_retreat():
    """The trap that makes a raw mean unusable as a trigger.

    A corpse reads 0.0 and never recovers during a fight, so two in an eight-man
    party drag the raw mean below the release threshold with the survivors at
    full health — and hold the retreat armed for the rest of the fight.
    """
    census = party(0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.0, 0.0)
    raw = sum(census.values()) / len(census)
    assert raw < CFG.release_fraction, "fixture no longer reproduces the trap: %.3f" % raw
    assert health_retreat.alive_mean(census) > CFG.release_fraction

    state = fresh()
    # The first observation spends a step — the deaths are new, and that half is
    # bounded by the budget. What must not happen is the corpses arguing for a
    # retreat forever afterwards.
    act(state, census)
    for _ in range(20):
        assert act(state, census) is HealthVerdict.CLEAR


def test_a_fresh_death_spends_a_step_even_at_full_health():
    """The monk-spike case, and the reason deaths are an event and not a level.

    Excluding corpses means a death RAISES the mean, so the level test on its own
    disarms at the exact moment it matters most.
    """
    state = fresh()
    assert act(state, party(1.0, 1.0, 1.0, 1.0)) is HealthVerdict.CLEAR

    spiked = party(1.0, 1.0, 1.0, 0.0)
    assert health_retreat.alive_mean(spiked) > CFG.release_fraction
    assert act(state, spiked) is HealthVerdict.WITHDRAW


def test_a_corpse_on_the_ground_is_not_a_new_death():
    state = fresh()
    spiked = party(1.0, 1.0, 1.0, 0.0)
    assert act(state, spiked) is HealthVerdict.WITHDRAW
    for _ in range(10):
        assert act(state, spiked) is HealthVerdict.CLEAR
    assert state.steps_used == 0


def test_a_resurrected_member_can_die_again():
    state = fresh()
    down = party(1.0, 1.0, 1.0, 0.0)
    assert act(state, down) is HealthVerdict.WITHDRAW
    # Up AND healed past the release threshold, so the episode actually ends.
    # A revived member still below release leaves the latch armed, and the
    # second death would then be answered by an episode that never closed.
    assert act(state, party(1.0, 1.0, 1.0, RECOVERED)) is HealthVerdict.CLEAR
    assert act(state, down) is HealthVerdict.WITHDRAW


def test_climbing_health_below_the_threshold_holds_instead_of_spending():
    """The pre-action latch: require an observed change, not an elapsed window.

    The step is working, so the budget is kept for a spike that is not.
    """
    state = fresh()
    assert act(state, party(0.30)) is HealthVerdict.WITHDRAW

    climbing = 0.30 + CFG.recover_margin + 0.05
    assert climbing < CFG.arm_fraction, "must still be below the arm threshold to prove anything"
    assert act(state, party(climbing)) is HealthVerdict.HOLD
    assert state.steps_used == 1


def test_noise_inside_the_margin_is_not_a_recovery():
    """The other side of the latch, and the reason the margin is not zero.

    Health readings flicker — a heal lands, degen ticks — and at zero margin any
    upward twitch reads as the withdrawal working and holds a party that is still
    losing. The jitter below is derived from the margin, so a narrowed margin can
    only be caught by asserting the deadband exists at all.
    """
    assert CFG.recover_margin > 0.0, "a zero margin makes every flicker a recovery"
    state = fresh()
    assert act(state, party(0.30)) is HealthVerdict.WITHDRAW
    jitter = 0.30 + (CFG.recover_margin / 2.0)
    assert act(state, party(jitter)) is HealthVerdict.WITHDRAW


def test_an_absent_reading_never_argues_for_retreat():
    state = fresh()
    assert health_retreat.alive_mean({}) == 1.0
    for _ in range(10):
        assert act(state, {}) is HealthVerdict.CLEAR


def test_a_wipe_keeps_trying_to_withdraw_rather_than_reporting_clear():
    """Degenerate shape, and the one that made the readout unbelievable.

    With nobody standing, the old contract answered 1.0: one step got spent on
    the deaths, the mean then cleared the release threshold, and the controller
    RELEASED — reporting a healthy party in the middle of a wipe. Zero survivors
    is the worst reading there is, not the best.
    """
    state = fresh()
    wiped = party(0.0, 0.0, 0.0, 0.0)
    assert health_retreat.alive_mean(wiped) == 0.0

    verdicts = [act(state, wiped) for _ in range(CFG.max_steps + 5)]
    assert HealthVerdict.CLEAR not in verdicts, "a wiping party must never read as clear"
    assert verdicts.count(HealthVerdict.WITHDRAW) == CFG.max_steps
    assert set(verdicts[CFG.max_steps :]) == {HealthVerdict.HOLD}


def test_releasing_the_episode_keeps_the_corpse_census():
    """release() runs at fight end. Clearing dead_positions there would make every
    corpse still on the ground read as a fresh death on the next tick, and arm the
    following fight before it had started."""
    state = fresh()
    act(state, party(1.0, 0.0))
    assert state.dead_positions == {2}

    state.release()
    assert state.dead_positions == {2}
    assert state.steps_used == 0
    assert state.armed is False


def test_the_census_is_published_for_the_readout():
    """alive/dead is the half that made the old version un-diagnosable."""
    state = fresh()
    act(state, party(1.0, 0.5, 0.0, 0.0))
    assert (state.alive, state.dead) == (2, 2)
    assert abs(state.last_mean - 0.75) < 0.001


def test_a_party_that_is_all_the_way_down_does_not_report_full_health():
    """The readout said 100% during a wipe.

    Excluding corpses from the mean is deliberate — a zero that never recovers
    would cap the mean and destroy the release condition. But with NOBODY left
    standing there is no mean to protect, and "no survivors" was collapsing into
    the same answer as "no readings yet", which is the most optimistic number
    there is.
    """
    assert health_retreat.alive_mean(party(0.0, 0.0, 0.0, 0.0)) == 0.0


def test_an_absent_reading_still_reports_full_health():
    """The other half of that distinction: nothing known must never argue for a
    retreat, or a slot that has not published yet starts one."""
    assert health_retreat.alive_mean({}) == 1.0


def test_survivors_still_set_the_mean_while_others_are_down():
    """The original reason corpses are excluded, which must survive the fix."""
    assert health_retreat.alive_mean(party(1.0, 1.0, 0.0, 0.0)) == 1.0
