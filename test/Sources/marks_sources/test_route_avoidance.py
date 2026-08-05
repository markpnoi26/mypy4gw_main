"""Steering a runner round the bodies parked on its path.

Loaded by path: the module claims a stdlib-only contract so the geometry stays
decidable without a client, and this test should fail the day someone gives it a
framework import at module scope.

Everything is expressed in the stride frame — `along` up the heading toward the
waypoint, `side` positive to the left of it — because that is the frame the
module reasons in, and a test that converts to world coordinates to assert on
would be re-deriving the thing under test.
"""

import math

import pathload

route = pathload.load("Sources/marks_sources/route_avoidance.py")

ORIGIN = (0.0, 0.0)
WAYPOINT = (0.0, 2000.0)
RADIUS = 50.0

CFG = route.AvoidanceConfig()
ROOM = CFG.clearance + RADIUS


def at(along: float, side: float) -> tuple[float, float]:
    """A world point from stride offsets, for the heading (0, 1) used here."""
    return (-side, along)


def blocker(along: float, side: float, radius: float = RADIUS) -> object:
    x, y = at(along, side)
    return route.Blocker(x, y, radius)


def offsets(point) -> tuple[float, float]:
    """Accepts a plain point or a Detour, so assertions read the same either way."""
    x, y = (point.x, point.y) if hasattr(point, "x") else (point[0], point[1])
    return route.frame(ORIGIN, WAYPOINT, (x, y))


def side_of(point) -> float:
    return offsets(point)[1]


def along_of(point) -> float:
    return offsets(point)[0]


def test_the_frame_helper_agrees_with_the_module():
    """If `at` and `frame` disagree every other assertion here is meaningless."""
    along, side = offsets(at(400.0, 120.0))
    assert abs(along - 400.0) < 0.001 and abs(side - 120.0) < 0.001


def test_an_empty_path_is_left_alone():
    assert route.detour(ORIGIN, WAYPOINT, [], CFG) is None, "nothing in the way must not pull the runner off its path"


def test_a_blocker_beside_the_path_is_left_alone():
    """The mover's own waypoints are already walkable. Steering round every foe
    within earshot would abandon a good path for no reason."""
    clear_of_the_lane = blocker(400.0, ROOM + 10.0)
    assert route.detour(ORIGIN, WAYPOINT, [clear_of_the_lane], CFG) is None


def test_a_blocker_beyond_the_lookahead_is_left_alone():
    beyond = blocker(CFG.lookahead + 200.0, 0.0)
    assert (
        route.detour(ORIGIN, WAYPOINT, [beyond], CFG) is None
    ), "a body that far off will have moved by the time we arrive"


def test_a_blocker_behind_and_clear_is_left_alone():
    assert route.detour(ORIGIN, WAYPOINT, [blocker(-ROOM - 10.0, 0.0)], CFG) is None


def test_a_blocker_behind_but_touching_still_forces_a_step():
    """Shoved from behind into a doorway. Refusing to step here is what wedges a
    run permanently, because nothing about the situation changes on its own."""
    assert route.detour(ORIGIN, WAYPOINT, [blocker(-ROOM / 2.0, 0.0)], CFG) is not None


def test_the_aim_point_actually_clears_the_blocker():
    plan = route.detour(ORIGIN, WAYPOINT, [blocker(400.0, 0.0)], CFG)
    assert plan is not None
    gap = math.dist((plan.x, plan.y), at(400.0, 0.0))
    assert gap >= ROOM, f"aimed within {gap:.0f} of a body needing {ROOM:.0f} of room"


def test_the_step_goes_round_the_side_the_blocker_is_not_on():
    on_the_right = blocker(400.0, -60.0)
    plan = route.detour(ORIGIN, WAYPOINT, [on_the_right], CFG)
    assert plan is not None
    assert side_of(plan) > 0.0, "a body to the right must be passed on the left"

    on_the_left = blocker(400.0, 60.0)
    plan = route.detour(ORIGIN, WAYPOINT, [on_the_left], CFG)
    assert plan is not None
    assert side_of(plan) < 0.0, "a body to the left must be passed on the right"


def test_the_step_is_taken_beside_the_nearest_body_not_the_furthest():
    """Aiming past the far one leaves the lateral offset at the near one only a
    fraction of the shift — which is how a runner clips the body it thought it
    had gone round."""
    plan = route.detour(ORIGIN, WAYPOINT, [blocker(200.0, 0.0), blocker(600.0, 0.0)], CFG)
    assert plan is not None
    assert abs(along_of(plan) - 200.0) < 1.0


def test_a_body_on_top_of_the_runner_still_gets_a_forward_component():
    """Aiming at your own feet issues a move nobody can observe completing."""
    plan = route.detour(ORIGIN, WAYPOINT, [blocker(0.0, 0.0)], CFG)
    assert plan is not None
    assert along_of(plan) > 0.0


def test_the_lane_chosen_is_clear_of_bodies_outside_the_corridor_too():
    """Stepping round the foe in the doorway into the one standing beside it is
    not avoidance. Only the corridor decides WHETHER to step; every body decides
    WHERE."""
    in_the_way = blocker(400.0, 0.0)
    # Outside the corridor, so it never becomes an offender — and squarely on
    # the lane the search reaches for first.
    camped_on_the_short_way_round = blocker(400.0, ROOM + 10.0)
    plan = route.detour(ORIGIN, WAYPOINT, [in_the_way, camped_on_the_short_way_round], CFG)
    assert plan is not None
    for body in (in_the_way, camped_on_the_short_way_round):
        gap = math.dist((plan.x, plan.y), (body.x, body.y))
        assert gap >= ROOM, f"aimed within {gap:.0f} of a body needing {ROOM:.0f} of room"


def test_the_smallest_lane_that_works_is_the_one_taken():
    """Leaving the path further than needed is its own way to lose a run."""
    plan = route.detour(ORIGIN, WAYPOINT, [blocker(400.0, 0.0)], CFG)
    assert plan is not None
    assert plan.shift <= ROOM + CFG.min_detour, f"shifted {plan.shift:.0f} to clear {ROOM:.0f} of room"


def test_being_surrounded_backs_off_instead_of_squeezing():
    ring = [blocker(400.0, 0.0)]
    ring += [
        route.Blocker(math.cos(angle) * 200.0, math.sin(angle) * 200.0, 400.0)
        for angle in (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0)
    ]
    plan = route.detour(ORIGIN, WAYPOINT, ring, CFG)
    assert plan is not None
    assert plan.reason == "retreat"
    assert along_of(plan) < 0.0, "backing off means moving away from the waypoint, not toward it"


def test_a_waypoint_underfoot_has_no_heading_to_steer_by():
    """The mover publishes its current waypoint every tick including the one it
    is standing on. Dividing by that zero-length heading would be an exception
    inside a service tree, which takes the whole bot down."""
    assert route.detour(ORIGIN, ORIGIN, [blocker(0.0, 0.0)], CFG) is None
    assert route.sidestep(ORIGIN, ORIGIN, 200.0, True, CFG) is None
    assert route.mirror(ORIGIN, ORIGIN, (10.0, 10.0)) is None
    assert route.retreat_point(ORIGIN, ORIGIN, CFG) is None


def test_a_sidestep_goes_the_way_it_was_asked_to():
    left = route.sidestep(ORIGIN, WAYPOINT, 200.0, True, CFG)
    right = route.sidestep(ORIGIN, WAYPOINT, 200.0, False, CFG)
    assert left is not None and right is not None
    assert side_of(left) > 0.0 and side_of(right) < 0.0
    assert abs(side_of(left) + side_of(right)) < 0.001, "the two sides must be the same step, mirrored"


def test_mirroring_keeps_the_distance_up_the_path_and_flips_the_side():
    point = at(300.0, 120.0)
    flipped = route.mirror(ORIGIN, WAYPOINT, point)
    assert flipped is not None
    along, side = offsets(flipped)
    assert abs(along - 300.0) < 0.001
    assert abs(side + 120.0) < 0.001


def test_retreat_moves_back_down_the_path_by_the_configured_distance():
    point = route.retreat_point(ORIGIN, WAYPOINT, CFG)
    assert point is not None
    assert abs(along_of(point) + CFG.retreat) < 0.001


def test_dwell_starts_at_zero_and_accumulates_while_standing_still():
    state = route.Dwell()
    assert route.dwell_ms(state, (0.0, 0.0), 1000.0, CFG) == 0.0, "the first reading has nothing to compare against"
    assert route.dwell_ms(state, (0.0, 0.0), 3000.0, CFG) == 2000.0


def test_dwell_tolerates_shuffling_inside_the_radius():
    """A body-blocked character still slides a little. Resetting on that would
    mean the stall is never detected at all."""
    state = route.Dwell()
    route.dwell_ms(state, (0.0, 0.0), 1000.0, CFG)
    assert route.dwell_ms(state, (CFG.dwell_radius - 1.0, 0.0), 3000.0, CFG) == 2000.0


def test_dwell_resets_once_the_character_has_actually_moved():
    state = route.Dwell()
    route.dwell_ms(state, (0.0, 0.0), 1000.0, CFG)
    assert route.dwell_ms(state, (CFG.dwell_radius + 1.0, 0.0), 3000.0, CFG) == 0.0
    assert route.dwell_ms(state, (CFG.dwell_radius + 1.0, 0.0), 4000.0, CFG) == 1000.0


def test_describe_names_the_reason_and_not_just_the_state():
    assert "clear" in route.describe(None)
    assert "not moving" in route.describe(None, dwell=4000.0)
    plan = route.detour(ORIGIN, WAYPOINT, [blocker(400.0, 0.0)], CFG)
    assert plan is not None
    assert plan.reason in route.describe(plan)
