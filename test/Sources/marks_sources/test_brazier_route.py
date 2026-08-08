"""Walk, light, relight on buff loss, skip after bounded retries.

Loaded by path: the module is stdlib-only by contract so the sequencing can be
driven from a plain interpreter, which is the only place it is verifiable at all.
"""

import pathload

route = pathload.load("Sources/marks_sources/brazier_route.py")

Step = route.Step
POINTS = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]


def fresh(**overrides):
    cfg = route.RouteConfig(**overrides)
    state = route.RouteState()
    route.begin(state, POINTS)
    return state, cfg


def walk_to_goal(state):
    assert route.next_step(state, arrived=False, buff_active=True) is Step.WALK
    return route.next_step(state, arrived=True, buff_active=True)


def test_happy_path_lights_every_brazier_in_order():
    state, cfg = fresh()
    goals = []
    for _ in POINTS:
        assert not route.finished(state)
        assert walk_to_goal(state) is Step.LIGHT
        goals.append(route.goal(state))
        route.report_light(state, cfg, found_gadget=True, buff_active=True)
    assert goals == POINTS
    assert route.next_step(state, arrived=True, buff_active=True) is Step.DONE
    assert state.failed == []
    # The tick after the last light lands here — the caller must see finished
    # BEFORE asking for a goal, because there is no goal left to give.
    assert route.finished(state)


def test_first_brazier_ignores_the_buff_reading():
    state, cfg = fresh()
    assert route.next_step(state, arrived=True, buff_active=False) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=True, buff_active=False)
    assert state.last_lit == 0
    assert state.idx == 1


def light_first(state, cfg):
    assert route.next_step(state, arrived=True, buff_active=False) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=True, buff_active=False)


def test_buff_loss_mid_walk_diverts_to_last_lit_then_resumes():
    state, cfg = fresh()
    light_first(state, cfg)

    assert route.next_step(state, arrived=False, buff_active=False) is Step.WALK
    assert state.relighting
    assert route.goal(state) == POINTS[0]

    assert route.next_step(state, arrived=True, buff_active=False) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=True, buff_active=True)

    assert not state.relighting
    assert route.goal(state) == POINTS[1]
    assert route.next_step(state, arrived=True, buff_active=True) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=True, buff_active=True)
    assert state.idx == 2


def test_failed_light_retries_via_relight_then_skips():
    state, cfg = fresh(max_retries=2)
    light_first(state, cfg)

    for _ in range(cfg.max_retries):
        assert route.next_step(state, arrived=True, buff_active=True) is Step.LIGHT
        assert route.goal(state) == POINTS[1]
        route.report_light(state, cfg, found_gadget=True, buff_active=False)
        if state.relighting:
            assert route.next_step(state, arrived=True, buff_active=False) is Step.LIGHT
            route.report_light(state, cfg, found_gadget=True, buff_active=True)

    assert state.failed == [1]
    assert state.idx == 2
    assert route.goal(state) == POINTS[2]


def test_missing_gadget_counts_as_a_retry():
    state, cfg = fresh(max_retries=1)
    assert route.next_step(state, arrived=True, buff_active=True) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=False, buff_active=True)
    assert state.failed == [0]
    assert state.idx == 1


def test_first_brazier_failure_has_no_relight_target():
    state, cfg = fresh(max_retries=3)
    assert route.next_step(state, arrived=True, buff_active=True) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=False, buff_active=True)
    assert not state.relighting
    assert route.goal(state) == POINTS[0]


def test_summary_reads_progress_and_failures():
    state, cfg = fresh(max_retries=1)
    light_first(state, cfg)
    assert route.next_step(state, arrived=True, buff_active=True) is Step.LIGHT
    route.report_light(state, cfg, found_gadget=True, buff_active=False)
    assert "failed: [2]" in route.summary(state)
