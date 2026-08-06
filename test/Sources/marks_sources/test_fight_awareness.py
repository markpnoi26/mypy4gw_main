"""The leader's read on its own party's fight.

Loaded by path: the module claims a stdlib-only contract so a leader-side bot can
decide without importing the framework, and the test should fail the day someone
gives it a framework import at module scope.
"""

import pathload

awareness = pathload.load("Sources/marks_sources/fight_awareness.py")

Stance = awareness.Stance


def snapshot(**overrides) -> dict:
    """A zone that is driving, holding, and content — the shape everything else
    is a deviation from. Every key the module reads is present, so a test that
    changes one is changing exactly one thing."""
    base = {
        "driving": True,
        "state": "HOLDING",
        "giving_ground": False,
        "health_enabled": True,
        "health_verdict": "CLEAR",
        "health_pending_deaths": 0,
        "anchor": (1000.0, 2000.0),
        "party_health": 0.9,
    }
    base.update(overrides)
    return base


def test_no_snapshot_leaves_the_route_in_charge():
    assert awareness.stance(None) is Stance.CLEAR, "with nothing published the bot must still be able to move"


def test_a_zone_that_is_not_driving_does_not_hold_the_route():
    """Dry run: the overlay draws a zone nobody is standing in. Holding for it
    would wedge the bot for the whole session."""
    assert awareness.stance(snapshot(driving=False)) is Stance.CLEAR


def test_a_travelling_zone_does_not_hold_the_route():
    assert awareness.stance(snapshot(state="TRAVELING")) is Stance.CLEAR


def test_an_engaged_zone_holds_the_route():
    assert awareness.stance(snapshot(state="ENGAGING")) is Stance.HOLD


def test_clearing_holds_the_route_too():
    """CLEARING is the zone holding the pin through looting. Walking off it
    tears the zone down mid-pickup."""
    assert awareness.stance(snapshot(state="CLEARING")) is Stance.HOLD


def test_given_ground_reads_as_a_withdrawal():
    assert awareness.stance(snapshot(giving_ground=True)) is Stance.WITHDRAW


def test_a_health_withdrawal_reads_as_a_withdrawal_before_the_step_is_taken():
    """The verdict arrives a dwell before the zone acts on it. The leader should
    stop advancing then, not after."""
    assert awareness.stance(snapshot(health_verdict="WITHDRAW")) is Stance.WITHDRAW


def test_a_health_verdict_is_ignored_while_health_retreat_is_switched_off():
    """The publisher reports the real verdict regardless of the toggle. Acting on
    it unguarded claims a withdrawal the zone will never perform."""
    assert awareness.stance(snapshot(health_verdict="WITHDRAW", health_enabled=False)) is Stance.HOLD


def test_geometry_and_health_agreeing_is_still_one_withdrawal():
    assert awareness.stance(snapshot(giving_ground=True, health_verdict="WITHDRAW")) is Stance.WITHDRAW


def test_the_leader_is_never_sent_forward_while_merely_holding():
    """The anchor sits ahead of the leader the moment a fight starts, so an
    unconditional chase walks it into the mob."""
    far_behind_the_anchor = (0.0, 0.0)
    assert awareness.reposition_target(snapshot(state="ENGAGING"), far_behind_the_anchor) is None


def test_a_withdrawing_leader_is_sent_to_the_anchor():
    target = awareness.reposition_target(snapshot(giving_ground=True), (0.0, 0.0))
    assert target == (1000.0, 2000.0), "the anchor is where the formation now is"


def test_a_leader_already_standing_in_the_formation_is_left_alone():
    """Re-issuing a move for a few units of drift burns ACTION queue slots the
    party's skills need."""
    almost_there = (1000.0 + awareness.DEFAULT_REPOSITION_TOLERANCE / 2.0, 2000.0)
    assert awareness.reposition_target(snapshot(giving_ground=True), almost_there) is None


def test_the_tolerance_is_a_boundary_not_a_gap():
    exactly_at_tolerance = (1000.0 + awareness.DEFAULT_REPOSITION_TOLERANCE, 2000.0)
    assert awareness.reposition_target(snapshot(giving_ground=True), exactly_at_tolerance) is None
    just_beyond = (1000.0 + awareness.DEFAULT_REPOSITION_TOLERANCE + 1.0, 2000.0)
    assert awareness.reposition_target(snapshot(giving_ground=True), just_beyond) is not None


def test_a_withdrawal_with_no_anchor_published_moves_nobody():
    assert awareness.reposition_target(snapshot(giving_ground=True, anchor=None), (0.0, 0.0)) is None


def test_the_tolerance_is_wider_than_a_single_step_would_be_worth_chasing():
    assert awareness.DEFAULT_REPOSITION_TOLERANCE > 0.0, "a zero tolerance re-issues a move every frame"


def test_a_missing_health_reading_reports_full_rather_than_empty():
    """0.0 is a real reading meaning "everyone is dead". Absence must not
    impersonate it in the readout."""
    assert awareness.party_health({}) == 1.0
    assert awareness.party_health(snapshot(party_health="nonsense")) == 1.0


def test_the_withdrawal_line_names_deaths_over_the_health_level():
    """A level drop and a death want opposite responses from whoever is reading."""
    line = awareness.describe(snapshot(giving_ground=True, health_pending_deaths=2))
    assert "2 down" in line, line


def test_the_withdrawal_line_falls_back_to_the_health_level():
    line = awareness.describe(snapshot(giving_ground=True, party_health=0.42))
    assert "42%" in line, line


def test_the_clear_line_says_the_route_is_running():
    assert awareness.describe(None) == "route running"


def escape_snapshot(**overrides) -> dict:
    base = snapshot(health_enabled=True, escape_terrain_known=True, escape_boxed_in=False)
    base["escape"] = {"distance": 1200.0, "source": "PROBE", "path": []}
    base.update(overrides)
    return base


def test_a_healthy_route_reports_the_room_it_leaves():
    lines = awareness.retreat_blockers(escape_snapshot())
    assert not any("BLOCKED" in line for line in lines), lines
    assert "1000u" in " ".join(lines), "room is the route minus the margin the zone holds back"


def test_a_missing_route_is_named_as_the_blocker():
    """The silent failure: the verdict keeps reading WITHDRAW and nothing moves."""
    lines = awareness.retreat_blockers(escape_snapshot(escape=None))
    assert any("no escape route" in line for line in lines), lines


def test_an_unusable_navmesh_is_distinguished_from_being_boxed_in():
    """Opposite causes, opposite fixes — one is a loading problem, one is terrain."""
    unusable = awareness.retreat_blockers(escape_snapshot(escape=None, escape_terrain_known=False))
    boxed = awareness.retreat_blockers(escape_snapshot(escape=None, escape_boxed_in=True))
    assert "navmesh" in " ".join(unusable), unusable
    assert "boxed in" in " ".join(boxed), boxed
    assert unusable != boxed


def test_a_route_shorter_than_the_margin_cannot_produce_a_step():
    lines = awareness.retreat_blockers(
        escape_snapshot(escape={"distance": awareness.GIVE_GROUND_MARGIN, "source": "PROBE"})
    )
    assert any("BLOCKED" in line for line in lines), lines


def test_the_feature_being_switched_off_is_reported_before_anything_else():
    lines = awareness.retreat_blockers(escape_snapshot(health_enabled=False))
    assert "switched off" in lines[0], lines


def test_a_spent_budget_is_reported_even_with_a_good_route():
    lines = awareness.retreat_blockers(escape_snapshot(health_steps_used=3, health_max_steps=3))
    assert any("budget spent" in line for line in lines), lines


def test_the_margin_matches_the_one_the_zone_actually_enforces():
    """A duplicated constant that drifts turns this readout into a confident lie
    — it would report room the zone will refuse to use, or vice versa."""
    from HeroAI.fight.zone import ZoneConfig

    assert awareness.GIVE_GROUND_MARGIN == ZoneConfig().give_ground_margin
