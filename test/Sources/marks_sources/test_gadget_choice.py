"""Target the gadget we meant, not whichever one stands closest.

Loaded by path: the module is stdlib-only by contract so the choice can be driven
from a plain interpreter, which is the only place it is verifiable at all.
"""

import pathload

choice = pathload.load("Sources/marks_sources/gadget_choice.py")

CHEST = 9284
LEVER = 1234
SCAN = (0.0, 0.0)


def at(agent_id, gadget_id, x, y=0.0):
    return choice.Candidate(agent_id=agent_id, gadget_id=gadget_id, x=x, y=y)


def test_a_wanted_gadget_beats_a_nearer_one_that_is_not():
    """The whole point. Nearest-wins is what silently opens a lever instead of
    the chest and reports it as a good run."""
    picked, reason = choice.pick([at(1, LEVER, 10.0), at(2, CHEST, 300.0)], SCAN, [CHEST])
    assert picked == 2
    assert str(CHEST) in reason


def test_the_nearest_match_wins_when_several_qualify():
    picked, _ = choice.pick([at(1, CHEST, 500.0), at(2, CHEST, 120.0), at(3, CHEST, 900.0)], SCAN, [CHEST])
    assert picked == 2


def test_no_wanted_ids_falls_back_to_nearest():
    """Levers and res shrines have nothing to match against, so the old
    behaviour has to stay reachable."""
    picked, _ = choice.pick([at(1, LEVER, 10.0), at(2, CHEST, 300.0)], SCAN, [])
    assert picked == 1


def test_an_empty_room_reports_nothing_in_range():
    picked, reason = choice.pick([], SCAN, [CHEST])
    assert picked == 0
    assert "no gadget in range" in reason


def test_a_miss_names_the_ids_that_were_actually_there():
    """A wrong id guess has to report itself. Without this the failure looks
    exactly like an empty room."""
    picked, reason = choice.pick([at(1, LEVER, 10.0), at(2, 777, 20.0)], SCAN, [CHEST])
    assert picked == 0
    assert str(LEVER) in reason
    assert "777" in reason
    assert str(CHEST) in reason


def test_duplicate_ids_are_counted_rather_than_repeated():
    _, reason = choice.pick([at(1, LEVER, 10.0), at(2, LEVER, 20.0)], SCAN, [CHEST])
    assert f"{LEVER} x2" in reason


def test_several_wanted_ids_are_all_eligible():
    picked, _ = choice.pick([at(1, LEVER, 10.0), at(2, 8932, 50.0)], SCAN, [CHEST, 8932])
    assert picked == 2


def test_ties_resolve_the_same_way_every_run():
    """Two chests at equal distance must not alternate between passes — the
    turn-taker is keyed on the id it was handed."""
    tied = [at(9, CHEST, 100.0), at(4, CHEST, 100.0)]
    assert choice.pick(tied, SCAN, [CHEST])[0] == 4
    assert choice.pick(list(reversed(tied)), SCAN, [CHEST])[0] == 4


def test_distance_is_measured_from_the_scan_point_not_the_origin():
    far_from_origin = (1000.0, 1000.0)
    picked, _ = choice.pick(
        [at(1, CHEST, 0.0, 0.0), at(2, CHEST, 1000.0, 1050.0)],
        far_from_origin,
        [CHEST],
    )
    assert picked == 2
