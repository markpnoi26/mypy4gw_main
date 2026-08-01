"""Trigger-ring geometry, escalation order, and the ground-control precedence.

Runs without a client: qa/nativestub.py serves stubs/*.pyi as the native modules.
See .claude/skills/test-harness.md.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qa"))
import nativestub

nativestub.install()

from HeroAI.fight import zone

CFG = zone.ZONE_CFG

# ground_ceiling returns 0 without a distance, which silently zeroes every step
# and lets a movement test pass while asserting nothing.
ROUTE = {"retreat_path": [(0.0, 0.0), (-3000.0, 0.0)], "retreat_distance": 3000.0}


def inputs(enemies, **kw):
    return zone.ZoneInputs(
        leader_xy=(0.0, 0.0),
        enemy_positions=list(enemies),
        party_in_aggro=True,
        leader_local_aggro=True,
        loot_pending=False,
        members_in_position=True,
        now_ms=kw.pop("now_ms", 100000),
        party_xy=kw.pop("party_xy", (0.0, -310.0)),
        **kw,
    )


def zone_at(anchor=(0.0, 0.0), facing=0.0):
    z = zone.FightZone()
    z.state = zone.ZoneState.HOLDING
    z.anchor_x, z.anchor_y = anchor
    z.facing = facing
    z.engagement_x, z.engagement_y = anchor[0] + 400.0, anchor[1]
    return z


def local_to_world(fwd, lat, anchor=(0.0, 0.0), facing=0.0):
    return (
        anchor[0] + (fwd * math.cos(facing)) - (lat * math.sin(facing)),
        anchor[1] + (fwd * math.sin(facing)) + (lat * math.cos(facing)),
    )


def test_midline_tip_sits_at_overrun_depth():
    """Halfway between the front and mid ranks, not at the mid rank itself."""
    inp = inputs([])
    assert abs(zone.midline_ring(CFG, inp).tip() + (inp.midline_depth * 0.5)) < 0.5


def test_backline_ring_is_centred_on_the_rear_rank():
    inp = inputs([])
    assert abs(zone.backline_ring(CFG, inp).centre + inp.backline_depth) < 0.5


def test_frontline_floor_lands_on_the_mid_rank():
    """Nothing level with or behind the casters votes on advancing."""
    inp = inputs([])
    front = zone.frontline_ring(CFG)
    assert abs((front.centre - front.fwd) + inp.midline_depth) < 0.5


def test_escalation_is_ordered_on_the_default_formation():
    inp = inputs([])
    assert zone.midline_ring(CFG, inp).tip() > zone.backline_ring(CFG, inp).tip()


def test_escalation_survives_a_compressed_formation():
    """The clamp that keeps the panic ring behind the soft one.

    backline_ring_fwd is a fixed 322 while the rank it sits on comes from the
    formation, so an unclamped shallow formation puts the emergency trip AHEAD
    of the soft one and the party panics before it ever steps back calmly.
    """
    shallow = inputs([], midline_depth=160.0, backline_depth=310.0)
    assert zone.midline_ring(CFG, shallow).tip() > zone.backline_ring(CFG, shallow).tip()


def test_frontline_ring_stays_inside_the_enemy_scan_radius():
    """No part of the ring may test ground where an enemy could never be seen."""
    front = zone.frontline_ring(CFG)
    worst = max(
        math.hypot(front.centre + (front.fwd * math.cos(a)), front.lat * math.sin(a))
        for a in (math.radians(i * 0.5) for i in range(721))
    )
    assert worst < CFG.engagement_scan_radius


def test_lateral_axis_sees_what_a_depth_plane_cannot():
    """The reason the rings are ellipses.

    Both blobs sit at the same depth. Facing can be up to 24s stale, so the
    off-axis one is exactly the case a projection onto the facing axis misses.
    """
    z = zone_at()
    on_axis = local_to_world(-410.0, 0.0)
    within_lat = local_to_world(-410.0, 600.0)
    beyond_lat = local_to_world(-410.0, 1400.0)

    assert zone.overrun(z, CFG, inputs([on_axis])) is True
    # The one that matters: off-axis but inside the lateral radius still trips.
    # Without it this test only catches WIDENING lat, and narrowing it is the
    # mistake that actually gets made.
    assert zone.overrun(z, CFG, inputs([within_lat])) is True
    assert zone.overrun(z, CFG, inputs([beyond_lat])) is False

    depths = [zone.blob_depth(z, CFG, inputs([p])) for p in (on_axis, within_lat, beyond_lat)]
    assert max(depths) - min(depths) < 0.5, "all three read the same DEPTH: %s" % depths


def test_rings_travel_with_facing():
    for degrees in (0, 37, 90, 175, 268, 359):
        facing = math.radians(degrees)
        anchor = (1500.0, -900.0)
        z = zone_at(anchor=anchor, facing=facing)
        inside = local_to_world(-500.0, 0.0, anchor, facing)
        ahead = local_to_world(600.0, 0.0, anchor, facing)
        # Beside the rear rank, not behind it. The panic ring is deliberately
        # the narrow one - a flanker level with the backline is not a breach.
        beside = local_to_world(-500.0, 1200.0, anchor, facing)
        assert zone.backline_breached(z, CFG, inputs([inside])) is True, degrees
        assert zone.backline_breached(z, CFG, inputs([ahead])) is False, degrees
        assert zone.backline_breached(z, CFG, inputs([beside])) is False, degrees


def test_advance_reads_the_centroid_not_the_nearest_body():
    """A pack straddling the ring keeps closing armed - the accepted trade.

    Spaced inside blob_weld_distance so all three stay one blob; otherwise the
    far one is simply a different blob and this proves nothing.
    """
    z = zone_at()
    straddle = [local_to_world(700.0, 0.0), local_to_world(900.0, 0.0), local_to_world(1300.0, 0.0)]
    centre_fwd = zone.local_frame(zone.blob_centre(CFG, inputs(straddle)), (z.anchor_x, z.anchor_y), z.facing)[0]
    assert centre_fwd > zone.frontline_ring(CFG).tip()
    assert zone.frontline_reached(z, CFG, inputs(straddle)) is False


def test_one_body_in_the_ring_does_not_pin_the_party():
    """The failure that drove the centroid rule: a flanker inside the ring while
    the pack it is there to fight sits out of reach."""
    z = zone_at()
    front = zone.frontline_ring(CFG)
    pack = [local_to_world(900.0, 0.0), local_to_world(1000.0, 0.0), local_to_world(1100.0, 0.0)]
    flanker = local_to_world(front.centre, 900.0)
    assert zone.inside_ring(front, flanker, z) is True
    assert zone.frontline_reached(z, CFG, inputs(pack + [flanker])) is False


def test_empty_field_keeps_closing_armed():
    assert zone.frontline_reached(zone_at(), CFG, inputs([])) is False


def test_breach_outranks_the_dwell_and_a_soft_trip_does_not():
    deep = [local_to_world(-620.0, 0.0)]
    soft = [local_to_world(-200.0, 0.0)]

    held = zone_at()
    held.hold_until_ms = 200000
    zone.adjust_ground(held, CFG, inputs(soft, now_ms=100000, **ROUTE))
    assert held.retreat_steps == []

    breached = zone_at()
    breached.hold_until_ms = 200000
    zone.adjust_ground(breached, CFG, inputs(deep, now_ms=100000, **ROUTE))
    assert len(breached.retreat_steps) == 1
    assert breached.breached is True
    assert breached.hold_until_ms == 100000 + CFG.breach_hold_ms


def test_retreat_releases_itself_without_a_timer():
    """One step must clear the trip it answered, or the zone needs a timer to
    stop and will slide instead."""
    z = zone_at()
    enemy = [local_to_world(-200.0, 0.0)]
    assert zone.overrun(z, CFG, inputs(enemy)) is True
    zone.adjust_ground(z, CFG, inputs(enemy, now_ms=100000, **ROUTE))
    assert abs(zone.given_ground(z) - CFG.give_ground_step) < 1.0
    assert zone.overrun(z, CFG, inputs(enemy)) is False


def test_advance_uses_a_flat_cadence_at_every_blob_size():
    """Flatness is the invariant, not the value.

    Asserting `== CFG.advance_hold_ms` reads the config at assert time and so can
    never fail - it moves with any change. What must hold is that blob size does
    not enter the advance dwell at all, while it very much does enter the retreat
    dwell.
    """
    holds = []
    for size in (1, 2, 3, 6):
        z = zone_at()
        far = [local_to_world(2400.0 + (i * 80), 0.0) for i in range(size)]
        zone.adjust_ground(z, CFG, inputs(far, now_ms=100000, **ROUTE))
        holds.append(z.hold_until_ms - 100000)

    assert len(set(holds)) == 1, "advance dwell varied with blob size: %s" % holds
    retreat = [zone.tier_for(CFG.recover_hold_tiers_ms, size) for size in (1, 2, 3, 6)]
    assert len(set(retreat)) > 1, "retreat dwell should still be tiered: %s" % retreat
