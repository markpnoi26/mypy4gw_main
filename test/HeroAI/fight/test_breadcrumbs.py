"""Trail sampling, distance budgeting, and the direction path_back walks.

Config is passed explicitly rather than read from BREADCRUMB_CFG: the defaults
are compass range, and a test that asserts against them moves whenever they do.
"""

import math

from HeroAI.fight import breadcrumbs
from HeroAI.fight.breadcrumbs import BreadcrumbConfig
from HeroAI.fight.breadcrumbs import Breadcrumbs

CFG = BreadcrumbConfig(max_path_length=1000.0, min_sample_distance=100.0, max_step_distance=2000.0)


def trail(*points):
    return Breadcrumbs(points=[(float(x), float(y)) for x, y in points])


def walked_east(count, spacing=100.0):
    return trail(*((index * spacing, 0.0) for index in range(count)))


def test_first_sample_is_recorded_unconditionally():
    crumbs = Breadcrumbs()
    breadcrumbs.sample(crumbs, CFG, (50.0, 50.0))
    assert crumbs.points == [(50.0, 50.0)]


def test_movement_below_the_threshold_is_not_recorded():
    """Standing still must not fill the buffer with near-identical points — the
    lookback would then terminate a few units behind the party."""
    crumbs = trail((0.0, 0.0))
    breadcrumbs.sample(crumbs, CFG, (CFG.min_sample_distance - 1.0, 0.0))
    assert crumbs.points == [(0.0, 0.0)]


def test_movement_above_the_threshold_is_recorded():
    crumbs = trail((0.0, 0.0))
    breadcrumbs.sample(crumbs, CFG, (CFG.min_sample_distance + 1.0, 0.0))
    assert len(crumbs.points) == 2


def test_a_teleport_discards_the_whole_trail():
    """Everything recorded belongs to a place the party is no longer in, so
    keeping it would plot a retreat through the previous map."""
    crumbs = walked_east(5)
    landed = (crumbs.points[-1][0] + CFG.max_step_distance + 1.0, 0.0)
    breadcrumbs.sample(crumbs, CFG, landed)
    assert crumbs.points == [landed]


def test_pruning_is_budgeted_by_distance_not_by_point_count():
    """The guarantee is 'at least max_path_length of history'. Pruning to the
    budget must not overshoot it by more than the step that crossed it."""
    crumbs = walked_east(21)
    breadcrumbs.prune(crumbs, CFG)
    length = breadcrumbs.path_length(crumbs.points)
    assert length <= CFG.max_path_length
    assert length > CFG.max_path_length - CFG.min_sample_distance


def test_pruning_drops_the_oldest_and_keeps_where_the_party_is():
    crumbs = walked_east(21)
    newest = crumbs.points[-1]
    breadcrumbs.prune(crumbs, CFG)
    assert crumbs.points[-1] == newest
    assert crumbs.points[0] != (0.0, 0.0)


def test_pruning_never_leaves_fewer_than_two_points():
    """One point is not a path. A trail whose two crumbs already blow the budget
    still has to describe a direction."""
    crumbs = trail((0.0, 0.0), (5000.0, 0.0))
    breadcrumbs.prune(crumbs, CFG)
    assert len(crumbs.points) == 2


def test_sampling_holds_the_budget_over_a_long_walk():
    crumbs = Breadcrumbs()
    for step in range(60):
        breadcrumbs.sample(crumbs, CFG, (step * 150.0, 0.0))
    assert breadcrumbs.path_length(crumbs.points) <= CFG.max_path_length


def test_sample_at_walks_the_requested_distance():
    assert breadcrumbs.sample_at([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)], 150.0) == (100.0, 50.0)


def test_sample_at_clamps_to_the_far_end():
    """Asking beyond the polyline returns its end rather than extrapolating past
    the last place known to be walkable."""
    assert breadcrumbs.sample_at([(0.0, 0.0), (100.0, 0.0)], 999.0) == (100.0, 0.0)


def test_sample_at_tolerates_an_empty_path():
    assert breadcrumbs.sample_at([], 100.0) == (0.0, 0.0)


def test_nearest_index_finds_the_closest_crumb():
    assert breadcrumbs.nearest_index(walked_east(5), (260.0, 40.0)) == 3


def test_path_back_starts_where_the_party_stands():
    """Up to min_sample_distance of real movement is never recorded, so a route
    beginning at the newest crumb starts somewhere nobody is."""
    route = breadcrumbs.path_back(walked_east(5), (450.0, 0.0), 300.0)
    assert route[0] == (450.0, 0.0)


def test_path_back_joins_at_the_nearest_crumb_and_walks_toward_older():
    """The regression this function exists for. The newest crumb is wherever the
    party last was, which after a withdrawal is between it and the fight —
    starting there marches the route forward into the enemies before turning
    around, and the first step of the retreat follows it straight back in."""
    fight_x = 1000.0
    route = breadcrumbs.path_back(walked_east(5, spacing=250.0), (600.0, 0.0), 400.0)
    assert all(point[0] <= 600.0 + 1e-6 for point in route), "route stepped toward the fight at x=%s" % fight_x


def test_path_back_delivers_the_distance_asked_for():
    route = breadcrumbs.path_back(walked_east(5, spacing=250.0), (600.0, 0.0), 400.0)
    assert math.isclose(breadcrumbs.path_length(route), 400.0, abs_tol=0.001)


def test_path_back_is_capped_by_the_trail_it_has():
    """A short trail cannot invent history; the route just ends early."""
    route = breadcrumbs.path_back(walked_east(3, spacing=100.0), (200.0, 0.0), 5000.0)
    assert breadcrumbs.path_length(route) < 5000.0
    assert route[-1] == (0.0, 0.0)


def test_path_back_with_nothing_wanted_is_just_the_party():
    assert breadcrumbs.path_back(walked_east(5), (400.0, 0.0), 0.0) == [(400.0, 0.0)]


def test_path_back_without_a_trail_is_just_the_party():
    assert breadcrumbs.path_back(Breadcrumbs(), (400.0, 0.0), 500.0) == [(400.0, 0.0)]
