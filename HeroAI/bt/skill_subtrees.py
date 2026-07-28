"""Tier 3: per-skill BT subtrees for skills whose execution genuinely spans
frames (weapon-swap-then-cast, wait-for-adrenaline, multi-step combos).
Opt-in and empty at launch; the Cast node checks here before the plain cast."""

from typing import Callable

from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

SKILL_SUBTREES: dict[int, Callable[[], BehaviorTree]] = {}


def register(skill_id: int, factory: Callable[[], BehaviorTree]) -> None:
    SKILL_SUBTREES[int(skill_id)] = factory


def get_subtree_factory(skill_id: int) -> Callable[[], BehaviorTree] | None:
    return SKILL_SUBTREES.get(int(skill_id))
