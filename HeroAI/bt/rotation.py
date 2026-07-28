"""Generic 8-slot rotation compiled as a BehaviorTree.

Selector semantics reproduce FindCastableSkill's first-castable-wins loop
(combat.py:1840); the cast leaf ports the HandleCombat tail (combat.py:2017).
Leaves are ConditionNodes: ActionNode's completion latch inserts an extra
RUNNING frame per action, which would halve rotation cadence versus legacy."""

import PySystem

from Core.Agent import Agent
from Core.GlobalCache import GLOBAL_CACHE
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from HeroAI.combat import MAX_SKILLS
from HeroAI.types import SkillNature

from . import skill_subtrees
from .conditions import decide_slot


def get_handler(blackboard: dict):
    return blackboard["cache"].combat_handler


def slot_ready(blackboard: dict, slot: int) -> bool:
    handler = get_handler(blackboard)
    if not handler.IsSkillReady(slot):
        return False
    if blackboard.get("ooc", False) and not handler.IsOOCSkill(slot):
        return False
    return True


def slot_decide(blackboard: dict, slot: int) -> bool:
    handler = get_handler(blackboard)
    ready, target_id = decide_slot(handler, slot)
    blackboard["slot_target"] = target_id
    if not ready or target_id == 0:
        return False
    return Agent.IsLiving(target_id)


def cast_slot(blackboard: dict, slot: int) -> bool:
    cached_data = blackboard["cache"]
    handler = cached_data.combat_handler
    target_id = blackboard.get("slot_target", 0)
    skill = handler.skills[slot]
    skill_id = skill.skill_id

    subtree_factory = skill_subtrees.get_subtree_factory(skill_id)
    if subtree_factory is not None:
        key = f"skill_subtree_{skill_id}"
        subtree = blackboard.get(key)
        if subtree is None:
            subtree = subtree_factory()
            blackboard[key] = subtree
        subtree.blackboard = blackboard
        return subtree.tick() != BehaviorTree.NodeState.FAILURE

    handler.SetSkillPointer(slot)
    handler.in_casting_routine = True
    handler.aftercast = 250
    if skill.custom_skill_data.Nature == SkillNature.Resurrection.value:
        handler.aftercast = 500

    if handler._skill_lock_is_blocked(skill):
        handler.ResetSkillPointer()
        return False

    handler.aftercast_timer.Reset()
    handler._apply_spike_lock(skill, target_id)
    handler._skill_lock_post(skill)

    if skill_id in handler.alcohol_skills:
        drunk_level = handler.GetDrunkLevel()
        if drunk_level <= 1:
            if handler.UseAlcoholIfAvailable():
                handler.ResetSkillPointer()
                return False
            if handler.IsAlcoholTopoffPending():
                handler.ResetSkillPointer()
                return False
            PySystem.Console.Log(
                "HeroAI",
                f"Skipping alcohol skill {skill_id}: drunk level {drunk_level} is below 2 and no alcohol was consumed",
                PySystem.Console.MessageType.Debug,
            )
            handler.ResetSkillPointer()
            return False

    GLOBAL_CACHE.SkillBar.UseSkill(handler.skill_order[slot] + 1, target_id, aftercast_delay=handler.aftercast)
    handler.ResetSkillPointer()
    return True


def call_leader_target(blackboard: dict) -> bool:
    if not blackboard.get("ooc", False):
        handler = get_handler(blackboard)
        handler._maybe_call_leader_selected_target(blackboard["cache"])
    return True


def auto_attack(blackboard: dict) -> bool:
    if blackboard.get("ooc", False):
        return False
    handler = get_handler(blackboard)
    handler.ResetSkillPointer()
    return handler.HandleAutoAttack(blackboard["cache"])


def slot_branch(slot: int) -> BehaviorTree.SequenceNode:
    return BehaviorTree.SequenceNode(
        name=f"Slot{slot}",
        children=[
            BehaviorTree.ConditionNode(
                name=f"Slot{slot}Ready",
                condition_fn=lambda node, slot=slot: slot_ready(node.blackboard, slot),
            ),
            BehaviorTree.ConditionNode(
                name=f"Slot{slot}Decide",
                condition_fn=lambda node, slot=slot: slot_decide(node.blackboard, slot),
            ),
            BehaviorTree.ConditionNode(
                name=f"Slot{slot}Cast",
                condition_fn=lambda node, slot=slot: cast_slot(node.blackboard, slot),
            ),
        ],
    )


def build_rotation_tree() -> BehaviorTree:
    children: list = [slot_branch(slot) for slot in range(MAX_SKILLS)]
    children.append(
        BehaviorTree.ConditionNode(
            name="AutoAttack",
            condition_fn=lambda node: auto_attack(node.blackboard),
        )
    )
    root = BehaviorTree.SequenceNode(
        name="HeroAI_BT_Rotation",
        children=[
            BehaviorTree.ConditionNode(
                name="CallLeaderTarget",
                condition_fn=lambda node: call_leader_target(node.blackboard),
            ),
            BehaviorTree.SelectorNode(name="Skills", children=children),
        ],
    )
    return BehaviorTree(root)
