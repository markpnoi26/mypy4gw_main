"""Is the party FIGHTING — as distinct from standing near enemies, or bleeding.

The costly mistakes here are both false positives: a fight zone dropped on open
ground stops the party dead, and one dropped on a sacrifice does it with no enemy
in sight.
"""

from HeroAI.fight import engagement
from HeroAI.fight.engagement import EngagementConfig
from HeroAI.fight.engagement import EngagementState

CFG = EngagementConfig()

# A drop the detector is meant to notice, derived so a retune moves with it.
REAL_DROP = CFG.health_drop_fraction * 2.0
# Blood is Power costs a third of maximum health in one cast.
SACRIFICE = 0.33

NO_ENEMIES: list[int] = []
SOME_ENEMY = [4242]


def party(*fractions):
    return {index: float(value) for index, value in enumerate(fractions)}


def settled(state, census):
    """Establish the baseline, the way a tick before the interesting one would."""
    engagement.party_under_fire(state, CFG, census, enemies_present=False)


def test_a_sacrifice_with_no_enemy_around_is_not_incoming_fire():
    """The reported bug: casting BiP in an empty corridor formed a battle line."""
    state = EngagementState()
    settled(state, party(1.0, 1.0, 1.0))
    after = party(1.0 - SACRIFICE, 1.0, 1.0)
    assert engagement.party_under_fire(state, CFG, after, enemies_present=False) is False


def test_the_same_drop_with_an_enemy_present_still_counts():
    """The gate must not cost the thing this channel exists for — ranged, degen
    and spirits, where nothing nearby ever reads as attacking."""
    state = EngagementState()
    settled(state, party(1.0, 1.0, 1.0))
    after = party(1.0 - REAL_DROP, 1.0, 1.0)
    assert engagement.party_under_fire(state, CFG, after, enemies_present=True) is True


def test_the_baseline_keeps_tracking_while_no_enemy_is_around():
    """Otherwise the pre-sacrifice reading stays put, and the first enemy to walk
    into scan range inherits the whole accumulated drop as if it had caused it."""
    state = EngagementState()
    settled(state, party(1.0, 1.0, 1.0))
    sacrificed = party(1.0 - SACRIFICE, 1.0, 1.0)
    engagement.party_under_fire(state, CFG, sacrificed, enemies_present=False)

    # Enemy shows up; health has not moved since.
    assert engagement.party_under_fire(state, CFG, sacrificed, enemies_present=True) is False


def test_a_drop_smaller_than_the_threshold_is_noise():
    state = EngagementState()
    settled(state, party(1.0))
    barely = party(1.0 - (CFG.health_drop_fraction / 2.0))
    assert engagement.party_under_fire(state, CFG, barely, enemies_present=True) is False


def test_healing_up_is_never_under_fire():
    state = EngagementState()
    settled(state, party(0.5))
    assert engagement.party_under_fire(state, CFG, party(1.0), enemies_present=True) is False


def test_an_empty_enemy_array_cannot_start_a_fight_however_much_health_is_lost():
    """update_engagement's own gate, not just the helper's."""
    state = EngagementState()
    engagement.update_engagement(state, CFG, (0.0, 0.0), NO_ENEMIES, party(1.0), {}, now_ms=0)
    bleeding = party(1.0 - SACRIFICE)
    assert engagement.update_engagement(state, CFG, (0.0, 0.0), NO_ENEMIES, bleeding, {}, now_ms=100) is False


class FakeHostileAgent:
    """`hostile_pressure` reads the world by agent id, so there is no struct to
    fill — this is the one place in this module that has to be faked. Reports an
    enemy that is alive, on top of the leader, and swinging.
    """

    @staticmethod
    def IsAlive(agent_id):
        return True

    @staticmethod
    def GetXY(agent_id):
        return (0.0, 0.0)

    @staticmethod
    def IsAggressive(agent_id):
        return True

    @staticmethod
    def IsInCombatStance(agent_id):
        return True


def test_the_health_baseline_is_refreshed_even_when_something_else_declared_the_fight():
    """`or` short-circuits. With party_under_fire inside the chain it never ran
    on a tick another channel answered first, so the baseline froze for the whole
    fight and the next quiet tick read one drop spanning all of it.

    Needs hostile_pressure to answer TRUE, or the chain reaches the health
    channel anyway and the test proves nothing.
    """
    state = EngagementState()
    healthy = party(1.0)
    real_agent = engagement.Agent
    engagement.Agent = FakeHostileAgent
    try:
        assert engagement.hostile_pressure(CFG, (0.0, 0.0), SOME_ENEMY) is True, "fixture must short-circuit the chain"
        assert engagement.update_engagement(state, CFG, (0.0, 0.0), SOME_ENEMY, healthy, {}, now_ms=0) is True
    finally:
        engagement.Agent = real_agent
    assert state.last_party_health == healthy, "the baseline must be taken on every tick"


def test_targeting_an_enemy_is_a_fight_even_before_anything_lands():
    state = EngagementState()
    assert engagement.party_offensive({0: 4242}, SOME_ENEMY) is True


def test_targeting_something_that_is_not_in_the_enemy_array_is_not():
    state = EngagementState()
    assert engagement.party_offensive({0: 99}, SOME_ENEMY) is False
    assert engagement.party_offensive({0: 0}, SOME_ENEMY) is False


def test_the_engagement_falls_only_after_the_disengage_hold():
    """Asymmetric on purpose: tearing the zone down early drops everyone back
    into follow mid-fight."""
    state = EngagementState()
    assert engagement.update_engagement(state, CFG, (0.0, 0.0), SOME_ENEMY, party(1.0), {0: 4242}, now_ms=0) is True

    quiet = CFG.disengage_hold_ms - 1
    assert engagement.update_engagement(state, CFG, (0.0, 0.0), NO_ENEMIES, party(1.0), {}, now_ms=quiet) is True

    expired = CFG.disengage_hold_ms
    assert engagement.update_engagement(state, CFG, (0.0, 0.0), NO_ENEMIES, party(1.0), {}, now_ms=expired) is False


def test_the_drop_threshold_is_not_zero():
    assert CFG.health_drop_fraction > 0.0, "a zero threshold makes every regen tick a fight"
