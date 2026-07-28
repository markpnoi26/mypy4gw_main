"""BehaviorTree-native builds.

Mirrors the `Builds/` tree. Every class here subclasses `BldMgrBT` and is
discovered by `BuildRegistry` alongside legacy `Builds/` classes, but only
these are matchable under the BT combat engine
(`HeroAI/bt/bt_engine.py: matchable_bt_builds`).

Porting guide: `docs/build_port_to_bldmgrbt.md`.
"""

from .nodes import (
    act,
    cast,
    cond,
    gate,
    guarded_cast,
    optional,
    rotation_tree,
    rung,
    selector,
    sequence,
    step,
    succeeder,
    tree,
)

__all__ = [
    "act",
    "cast",
    "cond",
    "gate",
    "guarded_cast",
    "optional",
    "rotation_tree",
    "rung",
    "selector",
    "sequence",
    "step",
    "succeeder",
    "tree",
]
