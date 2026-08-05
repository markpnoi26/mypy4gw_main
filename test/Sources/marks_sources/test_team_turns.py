"""One account at a time, and never two.

Loaded by path: the module is stdlib-only by contract so the sequencing can be
driven from a plain interpreter, which is the only place it is verifiable at all.
"""

import pathload

turns = pathload.load("Sources/marks_sources/team_turns.py")

Turn = turns.Turn
TEAM = ["a@x", "b@x", "c@x"]


def fresh(**overrides):
    cfg = turns.TurnConfig(**overrides)
    state = turns.TurnState()
    turns.begin(state, TEAM)
    return state, cfg


def drive(state, cfg, script):
    """Run (now_ms, busy) pairs through the machine, collecting who was started."""
    started = []
    for now_ms, busy in script:
        verdict = turns.next_turn(state, cfg, now_ms, busy)
        if verdict is Turn.START:
            started.append(state.current)
        if verdict is Turn.FINISHED:
            started.append("FINISHED")
    return started


def test_the_first_account_starts_immediately():
    """The settle gap is between turns. Applying it before the first one would
    stall every run for no reason."""
    state, cfg = fresh()
    assert turns.next_turn(state, cfg, 0, busy=False) is Turn.START
    assert state.current == "a@x"


def test_only_one_account_holds_a_turn_at_a_time():
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    holder = state.current
    for now_ms in range(100, 5_000, 100):
        turns.next_turn(state, cfg, now_ms, busy=True)
        assert state.current == holder, "a busy account must keep the chest"


def test_a_momentary_idle_between_interact_and_loot_does_not_end_the_turn():
    """The readings dip through idle between the interact finishing and the loot
    message appearing. Ending the turn there hands the chest over mid-pickup."""
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, 500, busy=True)
    turns.next_turn(state, cfg, 1_000, busy=False)
    turns.next_turn(state, cfg, 1_100, busy=True)
    assert state.current == "a@x", "one idle frame is not a finished turn"
    assert state.done == []


def test_the_quiet_window_restarts_after_any_busy_frame():
    """ "Quiet" means continuously quiet. Measuring from the first dip retires the
    turn on schedule even though the account went back to work in between, and
    the next account opens the chest on top of it."""
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, 1_000, busy=False)
    turns.next_turn(state, cfg, 1_100, busy=True)
    turns.next_turn(state, cfg, 1_200, busy=False)
    turns.next_turn(state, cfg, 1_000 + cfg.quiet_ms, busy=False)
    assert state.done == [], "the quiet window must be measured from the LAST busy frame"
    turns.next_turn(state, cfg, 1_200 + cfg.quiet_ms, busy=False)
    assert state.done == ["a@x"]


def test_a_turn_ends_once_the_quiet_holds():
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, 500, busy=True)
    turns.next_turn(state, cfg, 1_000, busy=False)
    turns.next_turn(state, cfg, 1_000 + cfg.quiet_ms, busy=False)
    assert state.done == ["a@x"]
    assert state.current == ""


def test_the_next_account_waits_out_the_settle_gap():
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, 100, busy=True)
    turns.next_turn(state, cfg, 1_000, busy=False)
    retired_at = 1_000 + cfg.quiet_ms
    turns.next_turn(state, cfg, retired_at, busy=False)
    assert turns.next_turn(state, cfg, retired_at + cfg.settle_ms - 1, busy=False) is Turn.WAIT
    assert turns.next_turn(state, cfg, retired_at + cfg.settle_ms, busy=False) is Turn.START
    assert state.current == "b@x"


def test_every_account_gets_exactly_one_turn_and_in_order():
    state, cfg = fresh()
    script = []
    clock = 0
    for _ in TEAM:
        script.append((clock, False))  # START
        script.append((clock + 100, True))  # working
        script.append((clock + 200, False))  # goes quiet
        script.append((clock + 200 + cfg.quiet_ms, False))  # quiet holds, retires
        clock += 200 + cfg.quiet_ms + cfg.settle_ms
    script.append((clock, False))
    assert drive(state, cfg, script) == ["a@x", "b@x", "c@x", "FINISHED"]
    assert state.done == TEAM


def test_an_account_that_never_reports_done_is_dropped_rather_than_wedging_the_queue():
    """A follower whose Messaging widget is off leaves the message Active
    forever. Waiting on that costs every remaining reward, not just its own."""
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, cfg.turn_timeout_ms, busy=True)
    assert state.timed_out == ["a@x"]
    assert state.done == []
    assert state.current == ""


def test_a_timeout_does_not_stop_the_remaining_accounts():
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, cfg.turn_timeout_ms, busy=True)
    after = cfg.turn_timeout_ms + cfg.settle_ms
    assert turns.next_turn(state, cfg, after, busy=False) is Turn.START
    assert state.current == "b@x"


def test_the_timeout_is_generous_enough_to_walk_the_map():
    """It fires only when something is genuinely broken — it must never be the
    thing that decides whether a turn counted."""
    assert turns.TurnConfig().turn_timeout_ms >= 30_000


def test_the_quiet_window_is_not_zero():
    assert turns.TurnConfig().quiet_ms > 0, "a zero quiet window makes every dip a finished turn"


def test_an_empty_team_finishes_rather_than_hanging():
    state = turns.TurnState()
    turns.begin(state, [])
    assert turns.next_turn(state, turns.TurnConfig(), 0, busy=False) is Turn.FINISHED


def test_blank_emails_are_dropped_rather_than_taking_a_turn():
    state = turns.TurnState()
    turns.begin(state, ["a@x", "", None])
    assert state.queue == ["a@x"]


def test_beginning_again_forgets_the_previous_run():
    """The step repeats every loop. Carrying stale results reports last run's
    rewards as this one's, and a carried queue skips accounts outright."""
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, 100, busy=False)
    turns.next_turn(state, cfg, 100 + cfg.quiet_ms, busy=False)
    clock = 100 + cfg.quiet_ms + cfg.settle_ms
    turns.next_turn(state, cfg, clock, busy=False)
    turns.next_turn(state, cfg, clock + cfg.turn_timeout_ms, busy=True)
    turns.next_turn(state, cfg, clock + cfg.turn_timeout_ms + cfg.settle_ms, busy=False)
    assert state.done and state.timed_out, "fixture must record results worth forgetting"
    assert state.current, "fixture must be holding a turn, or the release is untested"

    turns.begin(state, TEAM)
    assert state.current == ""
    assert state.done == []
    assert state.timed_out == []
    assert state.queue == TEAM
    assert turns.next_turn(state, cfg, 0, busy=False) is Turn.START


def test_start_is_reported_once_per_account_so_the_caller_never_double_sends():
    state, cfg = fresh()
    assert turns.next_turn(state, cfg, 0, busy=False) is Turn.START
    assert turns.next_turn(state, cfg, 10, busy=False) is Turn.WAIT
    assert turns.next_turn(state, cfg, 20, busy=True) is Turn.WAIT


def test_remaining_counts_the_account_currently_holding_the_chest():
    state, cfg = fresh()
    assert turns.remaining(state) == 3
    turns.next_turn(state, cfg, 0, busy=False)
    assert turns.remaining(state) == 3, "the holder has not finished, so it still counts"


def test_the_summary_names_the_accounts_that_timed_out():
    state, cfg = fresh()
    turns.next_turn(state, cfg, 0, busy=False)
    turns.next_turn(state, cfg, cfg.turn_timeout_ms, busy=True)
    assert "a@x" in turns.summary(state)
