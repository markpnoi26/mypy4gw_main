"""Key resolution, and the consistency the four generated tables have to keep.

frame_ids.py, frame_registry.py, frame_aliases.py and frame_names.py are all
generated from each other. Nothing at runtime notices when a regeneration leaves
them disagreeing — an anchor that stopped resolving is silently dropped from the
reverse maps rather than raising — so the disagreement is what gets asserted here.
"""

import pytest

from Core.FrameTree import DYNAMIC_KEYS
from Core.FrameTree import FRAME_NAMES
from Core.FrameTree import NAME_TO_HASH
from Core.FrameTree import REGISTRY
from Core.FrameTree import WINDOW_FRAME_KEYS
from Core.FrameTree import FrameId
from Core.FrameTree import FrameKeyError
from Core.FrameTree import alias_by_path
from Core.FrameTree import key_by_path
from Core.FrameTree import resolve_key


def registry_keys():
    """Every dotted key the registry defines, branches and leaves alike."""

    def walk(prefix, kids):
        for name, node in (kids or {}).items():
            key = prefix + "." + name
            yield key
            if not isinstance(node, int):
                yield from walk(key, node[1] if len(node) > 1 else {})

    for top, entry in REGISTRY.items():
        yield top
        if not isinstance(entry, str):
            yield from walk(top, entry[1] if len(entry) > 1 else {})


def frame_id_constants(namespace=FrameId):
    for name, value in vars(namespace).items():
        if name.startswith("__"):
            continue
        if isinstance(value, str):
            yield value
        elif isinstance(value, type):
            yield from frame_id_constants(value)


ALL_REGISTRY_KEYS = sorted(set(registry_keys()))
ALL_FRAME_IDS = sorted(set(frame_id_constants()))


def test_the_registry_is_not_empty():
    """Guards every other test in this file: a registry that failed to generate
    makes all the consistency checks below vacuously true."""
    assert len(ALL_REGISTRY_KEYS) > 100


def test_a_top_level_key_resolves_to_its_anchor_with_no_codes():
    anchor, codes = resolve_key("Compass")
    assert anchor
    assert codes == ()


def test_a_nested_key_resolves_to_the_code_path():
    anchor, codes = resolve_key("ArmorDye.BlueDye")
    assert anchor == resolve_key("ArmorDye")[0]
    assert len(codes) == 1


def test_an_unknown_top_level_key_is_rejected():
    with pytest.raises(FrameKeyError):
        resolve_key("NoSuchWindow")


def test_an_unknown_child_is_rejected():
    with pytest.raises(FrameKeyError):
        resolve_key("ArmorDye.NoSuchChild")


def test_a_child_of_a_leaf_is_rejected():
    """A leaf carries a bare code and no children, so walking past it must fail
    rather than silently resolving to the leaf itself."""
    with pytest.raises(FrameKeyError):
        resolve_key("ArmorDye.BlueDye.Deeper")


def test_every_registry_key_resolves():
    unresolvable = []
    for key in ALL_REGISTRY_KEYS:
        try:
            resolve_key(key)
        except FrameKeyError as exc:
            unresolvable.append("%s: %s" % (key, exc))
    assert unresolvable == []


def test_every_registry_anchor_has_a_frame_name():
    """An anchor missing from NAME_TO_HASH does not raise — _path_of returns
    None and the key vanishes from key_by_path(), so the frame becomes
    unnameable at runtime with no error anywhere."""
    missing = sorted(
        {anchor for key in ALL_REGISTRY_KEYS for anchor in [resolve_key(key)[0]] if anchor not in NAME_TO_HASH}
    )
    assert missing == []


def test_every_frame_id_constant_resolves():
    """frame_ids.py is generated from the registry. If it drifts, the failure is
    an exception in a widget rather than anything the generator reports."""
    unresolvable = []
    for key in ALL_FRAME_IDS:
        try:
            resolve_key(key)
        except FrameKeyError:
            unresolvable.append(key)
    assert unresolvable == []


def test_frame_ids_cover_the_registry():
    assert set(ALL_FRAME_IDS) == set(ALL_REGISTRY_KEYS)


def test_every_branch_class_carries_its_own_key():
    """Frame() takes a branch class via .KEY, so a branch whose KEY points
    somewhere else silently addresses the wrong frame."""
    wrong = []

    def walk(namespace, prefix):
        for name, value in vars(namespace).items():
            if name.startswith("__") or name == "KEY":
                continue
            if isinstance(value, type):
                key = getattr(value, "KEY", None)
                expected = name if not prefix else prefix + "." + name
                if key != expected:
                    wrong.append("%s: KEY=%r expected %r" % (expected, key, expected))
                walk(value, expected)

    walk(FrameId, "")
    assert wrong == []


def test_every_window_frame_key_resolves():
    """Hand-maintained, unlike its neighbours, so it drifts when the generated
    registry moves underneath it."""
    unresolvable = []
    for legacy, key in WINDOW_FRAME_KEYS.items():
        try:
            resolve_key(key)
        except FrameKeyError:
            unresolvable.append("%s -> %s" % (legacy, key))
    assert unresolvable == []


def test_every_dynamic_key_resolves():
    unresolvable = [key for key in sorted(DYNAMIC_KEYS) if key not in set(ALL_REGISTRY_KEYS)]
    assert unresolvable == []


def test_frame_names_invert_without_collisions():
    """NAME_TO_HASH is built by inverting FRAME_NAMES. Two hashes sharing a name
    would drop one of them, and the lost anchor resolves to the other's frame."""
    assert len(NAME_TO_HASH) == len(set(FRAME_NAMES.values()))


def test_key_by_path_round_trips_every_registry_key():
    """Built with setdefault, so two keys folding onto one path drop one of them
    and that frame stops being nameable from a live capture."""
    assert set(key_by_path().values()) == set(ALL_REGISTRY_KEYS)


def test_key_by_path_is_cached():
    assert key_by_path() is key_by_path()


def test_alias_by_path_is_cached():
    assert alias_by_path() is alias_by_path()


def test_paths_are_hash_prefixed_and_comma_joined():
    """Frame.path() produces this shape; the reverse maps have to match it
    exactly or every lookup misses."""
    for path in list(key_by_path())[:50]:
        head = path.split(",")[0]
        assert head.lstrip("-").isdigit()
        assert int(head) in FRAME_NAMES
