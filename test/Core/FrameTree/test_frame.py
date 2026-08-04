"""Folding a hierarchy dump into FrameTree's lookup maps.

The native call needs a live client; folding its rows does not, which is the
whole reason it is a separate function.

These rows are hand-written, so this suite proves the FOLD is right and proves
nothing about what ``get_frame_hierarchy()`` actually returns. It passed while
that assumption was blanking every path-resolved overlay. Before rebuild() goes
back on the one-call path, replace these fixtures with a dump captured from a
live client — then the layout is evidence rather than a premise.
"""

from Core.FrameTree.frame import fold_hierarchy

# (frame_id, parent_id, code, hash) — the ASSUMED layout, not a confirmed one.
ROOT = (1, 0, 0, 0xAAAA)
CHILD_A = (2, 1, 0, 0xBBBB)
CHILD_B = (3, 1, 1, 0)
TWIN = (4, 1, 1, 0xBBBB)

KEYS = ("parent", "code", "hash", "children", "order", "by_hash")


def fold(*rows):
    return dict(zip(KEYS, fold_hierarchy(rows)))


def test_maps_every_row():
    out = fold(ROOT, CHILD_A, CHILD_B)
    assert out["parent"] == {1: 0, 2: 1, 3: 1}
    assert out["code"] == {1: 0, 2: 0, 3: 1}
    assert out["hash"] == {1: 0xAAAA, 2: 0xBBBB, 3: 0}


def test_order_is_the_native_row_order():
    """all_ids() and children_map() both promise native frame-array order, so
    the fold must not sort or dedupe."""
    assert fold(CHILD_B, ROOT, CHILD_A)["order"] == [3, 1, 2]


def test_colliding_sibling_codes_both_survive():
    """A code is normally unique per parent but siblings can collide. Keeping
    only one silently drops a frame from enumeration."""
    assert fold(ROOT, CHILD_B, TWIN)["children"][1][1] == [3, 4]


def test_a_zero_hash_is_not_indexed():
    """Dynamically created frames carry hash 0. Indexing them would collapse
    every popup in the UI onto a single key."""
    out = fold(ROOT, CHILD_A, CHILD_B, TWIN)
    assert 0 not in out["by_hash"]
    assert out["by_hash"][0xBBBB] == [2, 4]


def test_no_rows_yields_empty_maps():
    """rebuild() reads an empty order as 'cannot see the UI this tick' and holds
    the previous tree, so the fold must report empty rather than raise."""
    out = fold()
    assert out["order"] == []
    assert out["children"] == {}
    assert out["by_hash"] == {}
