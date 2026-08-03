"""Pin allocation, spill order, and what the latch does and does not recompute."""

from HeroAI.fight import assignment
from HeroAI.fight.assignment import AssignmentLatch
from HeroAI.fight.assignment import MemberLine
from HeroAI.fight.formation import FightFormation
from HeroAI.fight.formation import FightPin
from HeroAI.fight.formation import default_fight_formation
from HeroAI.fight.lines import CombatLine


def members(*lines):
    return [
        MemberLine(party_position=index, character_name="M%d" % index, line=line) for index, line in enumerate(lines)
    ]


def line_of(formation, result, party_position):
    return formation.pins[result.pin_for(party_position)].line


def small_formation():
    return FightFormation(
        pins=[
            FightPin(0.0, 0.0, CombatLine.FRONT),
            FightPin(0.0, -300.0, CombatLine.MID),
            FightPin(0.0, -600.0, CombatLine.BACK),
        ]
    )


def test_everyone_lands_on_their_own_line_when_there_is_room():
    formation = default_fight_formation()
    roster = members(CombatLine.FRONT, CombatLine.MID, CombatLine.BACK)
    result = assignment.assign_pins(formation, roster)
    assert [line_of(formation, result, m.party_position) for m in roster] == [
        CombatLine.FRONT,
        CombatLine.MID,
        CombatLine.BACK,
    ]


def test_no_two_members_share_a_pin():
    """Double-booking puts two bodies on one spot, which reads in game as one of
    them refusing to move."""
    formation = default_fight_formation()
    roster = members(*([CombatLine.FRONT] * 4 + [CombatLine.MID] * 4))
    result = assignment.assign_pins(formation, roster)
    pins = list(result.pin_by_party_position.values())
    assert len(pins) == len(set(pins))


def test_a_front_overflow_spills_to_the_midline_first():
    """Front and back both spill inward — the midline is the most forgiving
    place to stand."""
    formation = small_formation()
    result = assignment.assign_pins(formation, members(CombatLine.FRONT, CombatLine.FRONT))
    assert line_of(formation, result, 1) == CombatLine.MID


def test_a_midline_overflow_spills_to_the_back_before_the_front():
    """Spilling a caster forward is the expensive direction to get wrong."""
    formation = small_formation()
    result = assignment.assign_pins(formation, members(CombatLine.MID, CombatLine.MID))
    assert line_of(formation, result, 1) == CombatLine.BACK


def test_a_back_overflow_spills_to_the_midline_before_the_front():
    formation = small_formation()
    result = assignment.assign_pins(formation, members(CombatLine.BACK, CombatLine.BACK))
    assert line_of(formation, result, 1) == CombatLine.MID


def test_auto_is_treated_as_the_midline():
    formation = default_fight_formation()
    result = assignment.assign_pins(formation, members(CombatLine.AUTO))
    assert line_of(formation, result, 0) == CombatLine.MID


def test_members_beyond_the_pin_count_go_unassigned_rather_than_doubled_up():
    formation = small_formation()
    roster = members(CombatLine.FRONT, CombatLine.MID, CombatLine.BACK, CombatLine.FRONT)
    result = assignment.assign_pins(formation, roster)
    assert result.pin_for(3) is None
    assert len(result.pin_by_party_position) == len(formation.pins)


def test_allocation_follows_party_position_not_scan_order():
    """Otherwise the mapping shuffles whenever the party scan happens to return
    members in a different order."""
    formation = default_fight_formation()
    roster = members(*([CombatLine.FRONT] * 3))
    forward = assignment.assign_pins(formation, roster)
    backward = assignment.assign_pins(formation, list(reversed(roster)))
    assert forward.pin_by_party_position == backward.pin_by_party_position


def test_the_signature_ignores_scan_order():
    roster = members(CombatLine.FRONT, CombatLine.MID)
    assert assignment.composition_signature(roster) == assignment.composition_signature(list(reversed(roster)))


def test_the_signature_moves_when_a_member_changes_line():
    before = members(CombatLine.FRONT, CombatLine.MID)
    after = members(CombatLine.FRONT, CombatLine.BACK)
    assert assignment.composition_signature(before) != assignment.composition_signature(after)


def test_the_latch_does_not_recompute_for_an_unchanged_party():
    """Recomputing per tick would reshuffle the formation mid-fight."""
    latch = AssignmentLatch()
    formation = default_fight_formation()
    roster = members(CombatLine.FRONT, CombatLine.MID, CombatLine.BACK)
    first = latch.get(formation, roster)
    assert latch.get(formation, list(reversed(roster))) is first


def test_the_latch_recomputes_when_a_member_changes_line():
    latch = AssignmentLatch()
    formation = default_fight_formation()
    latch.get(formation, members(CombatLine.FRONT, CombatLine.MID))
    after = latch.get(formation, members(CombatLine.FRONT, CombatLine.BACK))
    assert line_of(formation, after, 1) == CombatLine.BACK


def test_clearing_the_latch_forces_a_fresh_assignment():
    latch = AssignmentLatch()
    formation = default_fight_formation()
    roster = members(CombatLine.FRONT)
    first = latch.get(formation, roster)
    latch.clear()
    assert latch.get(formation, roster) is not first


def test_an_empty_party_assigns_nothing():
    result = assignment.assign_pins(default_fight_formation(), [])
    assert result.pin_by_party_position == {}
    assert result.composition == ()


def test_a_formation_with_no_pins_assigns_nothing():
    """A formation that failed to load must not raise into the publisher tick."""
    result = assignment.assign_pins(FightFormation(), members(CombatLine.FRONT))
    assert result.pin_for(0) is None
