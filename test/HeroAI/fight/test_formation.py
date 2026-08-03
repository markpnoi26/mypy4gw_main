"""Depth measurement, the reach clamp, and fight-local to world rotation."""

import math

from HeroAI.fight.formation import CAST_RANGE
from HeroAI.fight.formation import CAST_SAFETY_MARGIN
from HeroAI.fight.formation import FightFormation
from HeroAI.fight.formation import FightPin
from HeroAI.fight.formation import LineTolerances
from HeroAI.fight.formation import clamp_depth
from HeroAI.fight.formation import default_fight_formation
from HeroAI.fight.formation import max_depth_for
from HeroAI.fight.formation import rotate_fight_local_to_world
from HeroAI.fight.lines import CombatLine


def ranks(front_y=0.0, mid_y=-300.0, back_y=-600.0):
    return FightFormation(
        pins=[
            FightPin(0.0, front_y, CombatLine.FRONT),
            FightPin(0.0, mid_y, CombatLine.MID),
            FightPin(0.0, back_y, CombatLine.BACK),
        ]
    )


def test_depth_spans_the_whole_formation():
    assert ranks().depth() == 600.0


def test_midline_and_backline_depths_are_positive_distances_behind_the_front():
    formation = ranks()
    assert formation.midline_depth() == 300.0
    assert formation.backline_depth() == 600.0


def test_midline_depth_falls_back_to_half_the_formation():
    """A formation authored with no MID rank still has to answer 'how far back
    is overrun', and half the depth is the honest guess."""
    formation = FightFormation(pins=[FightPin(0.0, 0.0, CombatLine.FRONT), FightPin(0.0, -400.0, CombatLine.BACK)])
    assert formation.midline_depth() == 200.0


def test_backline_depth_falls_back_to_the_full_depth():
    formation = FightFormation(pins=[FightPin(0.0, 0.0, CombatLine.FRONT), FightPin(0.0, -400.0, CombatLine.MID)])
    assert formation.backline_depth() == 400.0


def test_an_empty_formation_has_no_depth():
    assert FightFormation().depth() == 0.0


def test_the_budget_is_on_worst_case_separation_not_authored_depth():
    """Front and back can each drift a full tolerance apart and the heal still
    has to land at that moment."""
    tolerances = LineTolerances(front=120.0, back=150.0)
    assert max_depth_for(tolerances) == CAST_RANGE - CAST_SAFETY_MARGIN - 270.0


def test_wide_tolerances_cannot_drive_the_budget_to_nothing():
    """The floor wins over the drift subtraction — a formation of zero depth is
    not a usable answer."""
    assert max_depth_for(LineTolerances(front=5000.0, back=5000.0)) == 200.0


def test_a_clamped_formation_stays_inside_cast_range():
    """The invariant the clamp exists for. Exceeding it is what silently kills
    runs: the formation looks fine on screen and people die out of range."""
    formation, clamped = clamp_depth(ranks(back_y=-3000.0))
    assert clamped
    assert formation.worst_case_separation() <= CAST_RANGE - CAST_SAFETY_MARGIN + 0.001


def test_a_formation_already_inside_the_budget_is_left_alone():
    formation = ranks()
    before = [(pin.x, pin.y) for pin in formation.pins]
    result, clamped = clamp_depth(formation)
    assert not clamped
    assert [(pin.x, pin.y) for pin in result.pins] == before


def test_the_default_formation_needs_no_clamping():
    """If the authored default ever stops fitting, every party silently gets a
    compressed formation instead of the one that was drawn."""
    assert not clamp_depth(default_fight_formation())[1]


def test_clamping_anchors_on_the_front_rank():
    """Compressing toward the back would walk the front line off the enemies it
    is supposed to be holding."""
    formation, _ = clamp_depth(ranks(back_y=-3000.0))
    assert max(pin.y for pin in formation.pins) == 0.0


def test_clamping_preserves_rank_order_and_lateral_spacing():
    formation, _ = clamp_depth(ranks(back_y=-3000.0))
    ys = [pin.y for pin in formation.pins]
    assert ys == sorted(ys, reverse=True)
    assert all(pin.x == 0.0 for pin in formation.pins)


def test_clamping_scales_proportionally():
    formation, _ = clamp_depth(ranks(mid_y=-1500.0, back_y=-3000.0))
    front, mid, back = (pin.y for pin in formation.pins)
    assert math.isclose(mid - front, (back - front) / 2.0, abs_tol=0.001)


def test_clamping_an_empty_formation_does_not_divide_by_zero():
    formation, clamped = clamp_depth(FightFormation())
    assert not clamped
    assert formation.pins == []


def test_an_explicit_budget_overrides_the_derived_one():
    formation, clamped = clamp_depth(ranks(), max_depth=100.0)
    assert clamped
    assert math.isclose(formation.depth(), 100.0, abs_tol=0.001)


def test_local_plus_y_points_along_facing():
    """+Y in fight-local space points at the enemies, so at facing 0 it has to
    come out as +X in world space."""
    x, y = rotate_fight_local_to_world(0.0, 100.0, 0.0)
    assert math.isclose(x, 100.0, abs_tol=0.001)
    assert math.isclose(y, 0.0, abs_tol=0.001)


def test_rotation_follows_facing():
    x, y = rotate_fight_local_to_world(0.0, 100.0, math.pi / 2.0)
    assert math.isclose(x, 0.0, abs_tol=0.001)
    assert math.isclose(y, 100.0, abs_tol=0.001)


def test_rotation_preserves_length():
    x, y = rotate_fight_local_to_world(160.0, -320.0, 1.234)
    assert math.isclose(math.hypot(x, y), math.hypot(160.0, -320.0), abs_tol=0.001)


def test_local_plus_x_is_perpendicular_to_facing():
    """Pins are authored with +X across the party's heading. Getting the
    handedness backwards mirrors the whole formation, which stays plausible on
    screen — so pin the side, not just the perpendicularity."""
    x, y = rotate_fight_local_to_world(100.0, 0.0, 0.0)
    assert math.isclose(x, 0.0, abs_tol=0.001)
    assert math.isclose(y, 100.0, abs_tol=0.001)
