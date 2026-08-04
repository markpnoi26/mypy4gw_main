"""child_of falls back to the engine when the snapshot has no answer.

Snapshot misses used to return None, which is indistinguishable from "that
window is closed" — so a wrong tree blanked overlays in silence rather than
failing. See docs/frametree_hierarchy_regression.md §2.
"""

import PyUIManager

from Core.FrameTree.frame import _FrameTree

PARENT = 10
CODE = 3
CHILD = 11


def tree_with(children):
    tree = _FrameTree()
    tree._children = children
    return tree


def native_returning(monkeypatch, value, calls=None):
    def fake(parent_frame_id, child_offset):
        if calls is not None:
            calls.append((parent_frame_id, child_offset))
        return value

    monkeypatch.setattr(PyUIManager.UIManager, "get_child_frame_by_frame_id", staticmethod(fake))


def test_snapshot_hit_does_not_touch_the_engine(monkeypatch):
    """The fast path stays free — the guard is only for misses."""
    calls = []
    native_returning(monkeypatch, 999, calls)
    tree = tree_with({PARENT: {CODE: [CHILD]}})
    assert tree.child_of(PARENT, CODE) == CHILD
    assert calls == []


def test_miss_asks_the_engine(monkeypatch):
    calls = []
    native_returning(monkeypatch, CHILD, calls)
    assert tree_with({}).child_of(PARENT, CODE) == CHILD
    assert calls == [(PARENT, CODE)]


def test_engine_zero_still_means_no_such_child(monkeypatch):
    native_returning(monkeypatch, 0)
    assert tree_with({}).child_of(PARENT, CODE) is None


def test_known_parent_with_unknown_code_is_still_a_miss(monkeypatch):
    """A parent in the snapshot does not make its child list complete."""
    native_returning(monkeypatch, CHILD)
    assert tree_with({PARENT: {0: [99]}}).child_of(PARENT, CODE) == CHILD


def test_engine_failure_is_not_an_exception(monkeypatch):
    def boom(parent_frame_id, child_offset):
        raise RuntimeError("no UI context")

    monkeypatch.setattr(PyUIManager.UIManager, "get_child_frame_by_frame_id", staticmethod(boom))
    assert tree_with({}).child_of(PARENT, CODE) is None


def test_frame_zero_never_reaches_the_engine(monkeypatch):
    """Code 0 is a real offset, but frame 0 is not a frame."""
    calls = []
    native_returning(monkeypatch, CHILD, calls)
    assert tree_with({}).child_of(0, 0) is None
    assert calls == []
