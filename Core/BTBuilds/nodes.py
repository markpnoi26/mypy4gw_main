"""Authoring helpers for BldMgrBT rotations.

Rungs are ConditionNodes, not ActionNodes. ActionNode latches
(BehaviorTree.py:447-486): it returns RUNNING on the tick the action runs and
delivers the result on the NEXT tick, which halves rotation cadence. Reach for
`act()` only where that extra frame is intended.
"""

from typing import Callable

from ..py4gwcorelib_src.BehaviorTree import BehaviorTree

NodeState = BehaviorTree.NodeState


def selector(name: str, *children) -> BehaviorTree.SelectorNode:
    return BehaviorTree.SelectorNode(name=name, children=list(children))


def sequence(name: str, *children) -> BehaviorTree.SequenceNode:
    return BehaviorTree.SequenceNode(name=name, children=list(children))


def tree(name: str, *children) -> BehaviorTree:
    return BehaviorTree(selector(name, *children))


def rotation_tree(name: str, gates: list, rungs: list) -> BehaviorTree:
    """The shape every ported ladder takes.

    `gates` are the leading `if not X: return False` checks — they belong in a
    Sequence, so a passing gate falls through to the next check instead of
    short-circuiting. `rungs` are the priority ladder — a Selector, first hit
    wins. Putting gates in the Selector would make a satisfied gate return
    SUCCESS and skip the rotation entirely.
    """
    body = selector(f"{name}Rotation", *rungs)
    if not gates:
        return BehaviorTree(body)
    return BehaviorTree(sequence(name, *gates, body))


def cond(name: str, fn: Callable) -> BehaviorTree.ConditionNode:
    return BehaviorTree.ConditionNode(name=name, condition_fn=fn)


def step(name: str, cast_fn: Callable) -> BehaviorTree.ConditionNode:
    """One rotation rung: attempt a cast, succeed if it fired.

    Replaces `if (yield from self.skills...): return True` from generator builds.
    """
    return BehaviorTree.ConditionNode(name=name, condition_fn=cast_fn)


def rung(name: str, guard_fn: Callable, cast_fn: Callable) -> BehaviorTree.SequenceNode:
    """Guarded rotation rung.

    Replaces `if guard and (yield from cast()): return True`.
    """
    return sequence(name, cond(f"{name}?", guard_fn), step(f"{name}!", cast_fn))


def gate(name: str, fn: Callable) -> BehaviorTree.ConditionNode:
    """Inverted guard used as a leading Selector child: fails the whole rotation
    when `fn` is False. Replaces an early `return False` in a generator ladder."""
    return BehaviorTree.ConditionNode(
        name=name,
        condition_fn=lambda node: NodeState.FAILURE if fn_result(fn, node) else NodeState.SUCCESS,
    )


def fn_result(fn: Callable, node):
    try:
        return fn(node)
    except TypeError:
        return fn()


def cast(build, name: str, factory: Callable) -> BehaviorTree.ConditionNode:
    """A skill-layer call as a rotation rung.

    `factory` returns the generator, e.g.
        cast(self, "DwaynasKiss", lambda: self.skills.Monk.HealingPrayers.Dwaynas_Kiss())

    Driven one step per frame by BldMgrBT.drive, so ordinary casts resolve
    same-frame and spirit casts report RUNNING until done.
    """
    return BehaviorTree.ConditionNode(
        name=name,
        condition_fn=lambda: build.drive(name, factory),
    )


def guarded_cast(build, name: str, guard_fn: Callable, factory: Callable) -> BehaviorTree.SequenceNode:
    """Replaces `if guard and (yield from cast()): return True`."""
    return sequence(name, cond(f"{name}?", guard_fn), cast(build, f"{name}!", factory))


def act(name: str, fn: Callable, aftercast_ms: int = 0) -> BehaviorTree.ActionNode:
    return BehaviorTree.ActionNode(name=name, action_fn=fn, aftercast_ms=aftercast_ms)


def succeeder(name: str) -> BehaviorTree.SucceederNode:
    return BehaviorTree.SucceederNode(name=name)


def optional(child, name: str = "Optional") -> BehaviorTree.SelectorNode:
    """Absorb a child's FAILURE so a parent Sequence keeps going."""
    return selector(name, child, succeeder(f"{name}:Skip"))
