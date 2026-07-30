"""BT port of Builds/Assassin/A_Me/SF_Ass_vaettir.py — Shadow Form Vaettir farmer.

=============================================================================
WHY THIS LIVES IN FarmBuilds AND WHY IT IS NOT A DECOMPOSED TREE
=============================================================================

This is not a combat rotation. It is a single-purpose farm routine for the
Jaga Moraine Vaettir run, and it differs from every ported combat build in
four ways that together rule out a rung-by-rung Selector:

1. IT IS SCRIPT-DRIVEN, NOT CONTRACT-DRIVEN.
   Callers: Bots/marks_coding_corner/VaettirMarksMods.py and
   Widgets/Automation/Bots/Farmers/Events/YAVB 2.0.py. They instantiate it
   directly and steer it every frame through SetKillingRoutine(),
   SetRoutineFinished() and SetStuckSignal(). None of that state exists in a
   HeroAI party context, so contract matching must never select this build.
   Location under FarmBuilds enforces that structurally — see
   build_registry.is_purpose_specific_build.

   NOTE: this build carries NO is_combat_automator_compatible=False flag, so
   location is the only thing keeping it out of contract matching. A HeroAI
   account holding a Shadow Form bar would otherwise match it and start
   running a Vaettir routine mid-party.

2. IT IS MAP-GATED AND RETURNS EARLY.
   Outside Jaga Moraine the routine runs a *different, shorter* body
   (defensive upkeep only) and returns. That is control flow, not priority.

3. IT IS MULTI-FRAME BY DESIGN.
   33 yield points, including `Routines.Yield.wait(1000)` idles and
   `Routines.Yield.Agents.ChangeTarget` handshakes. Aftercasts run to 2750 ms
   (Arcane Echo). A Selector of same-frame rungs cannot express any of that;
   the waits ARE the behaviour.

4. IT MUTATES SHARED STATE MID-ROUTINE.
   `GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")` is called between
   casts to force queue ordering, and `self.stuck_signal` is cleared on a
   successful Heart of Shadow. Splitting the body would reorder those.

WHAT THE PORT ACTUALLY DOES
---------------------------
The routine body is preserved verbatim as one generator and hosted under a
single BT node driven by BldMgrBT.drive(). drive() advances it one step per
frame and reports RUNNING until it completes, which is exactly the semantics
the generator was written for — so behaviour is unchanged while the build
still presents as a BldMgrBT for bot.AddBuild() and the BT service rail.

Contrast with DervBoneFarmer (the other FarmBuild): that one WAS rewritten as
a real tree, because its phases are genuinely discrete and it owns its own
`status` state machine. This build's phases are interleaved with waits and
external signals, so the honest port is a hosted generator, not a fake tree.

TO REWRITE THIS AS A REAL TREE LATER
------------------------------------
You would need, in order: a phase field like DervBuildFarmStatus; the map gate
as a top-level Selector branch; the defensive-upkeep block as its own subtree;
the Arcane Echo / Wastrel's Demise spike as a Sequence with WaitUntil nodes
replacing the 2750 ms aftercasts; and the ChangeTarget handshakes as RUNNING
actions. That is a redesign with real behaviour risk on a working farm — do it
against a live run, not blind.
"""

import math
from typing import Tuple

import PySystem

from Core import Agent
from Core import BldMgrBT
from Core import ConsoleLog
from Core import GLOBAL_CACHE
from Core import Map, Player
from Core import Profession
from Core import Range
from Core import Routines
from Core import Utils
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ....nodes import cast, rotation_tree


class SF_Ass_vaettir(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Shadow Form Assassin Vaettir",
            required_primary=Profession.Assassin,
            required_secondary=Profession.Mesmer,
            template_code="OwVUI2h5lPP8Id2BkAiAvpLBTAA",
            # Belt-and-braces: FarmBuilds location already excludes this from
            # matching, but the flag documents intent at the call site too.
            is_combat_automator_compatible=False,
            required_skills=[
                GLOBAL_CACHE.Skill.GetID("Deadly_Paradox"),
                GLOBAL_CACHE.Skill.GetID("Shadow_Form"),
                GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress"),
                GLOBAL_CACHE.Skill.GetID("Way_of_Perfection"),
                GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow"),
                GLOBAL_CACHE.Skill.GetID("Wastrels_Demise"),
                GLOBAL_CACHE.Skill.GetID("Arcane_Echo"),
                GLOBAL_CACHE.Skill.GetID("Channeling"),
            ],
        )
        if match_only:
            return

        self.wastrels_demise_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Wastrels_Demise"))
        self.arcane_echo_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Arcane_Echo"))

        self.shadow_form = GLOBAL_CACHE.Skill.GetID("Shadow_Form")
        self.deadly_paradox = GLOBAL_CACHE.Skill.GetID("Deadly_Paradox")
        self.shroud_of_distress = GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress")
        self.channeling = GLOBAL_CACHE.Skill.GetID("Channeling")
        self.way_of_perfection = GLOBAL_CACHE.Skill.GetID("Way_of_Perfection")
        self.heart_of_shadow = GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow")

        self.in_killing_routine = False
        self.routine_finished = False
        self.stuck_signal = False
        self.waypoint = (0, 0)

    # ---- external control surface: the farm scripts call these every frame ----

    def SetKillingRoutine(self, in_killing_routine: bool):
        self.in_killing_routine = in_killing_routine

    def SetRoutineFinished(self, routine_finished: bool):
        self.routine_finished = routine_finished

    def SetStuckSignal(self, stuck_counter: int):
        self.stuck_signal = stuck_counter > 0

    def GetStuckSignal(self) -> bool:
        return self.stuck_signal

    # ---- casting helpers ----
    # These go through Routines.Yield.Skills, NOT BldMgrBT/CombatServices
    # CastSkillID. The routine depends on Yield.Skills' queue behaviour and on
    # ResetQueue("ACTION") interleaving; swapping in the CombatServices cast
    # API would change ordering. Left exactly as the legacy build had them.

    def cast_skill_id(self, skill_id: int, extra_condition: bool = True, log: bool = True, aftercast_delay: int = 1000):
        result = yield from Routines.Yield.Skills.CastSkillID(
            skill_id, extra_condition=extra_condition, log=log, aftercast_delay=aftercast_delay
        )
        return result

    def cast_skill_slot(self, slot: int, extra_condition: bool = True, log: bool = True, aftercast_delay: int = 1000):
        result = yield from Routines.Yield.Skills.CastSkillSlot(
            slot, extra_condition=extra_condition, log=log, aftercast_delay=aftercast_delay
        )
        return result

    def DefensiveActions(self):
        player_agent_id = Player.GetAgentID()
        has_deadly_paradox = Routines.Checks.Effects.HasBuff(player_agent_id, self.deadly_paradox)
        if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shadow_form)):
            if (
                yield from self.cast_skill_id(
                    self.deadly_paradox, extra_condition=(not has_deadly_paradox), log=False, aftercast_delay=100
                )
            ):
                ConsoleLog(self.build_name, "Casting Deadly Paradox.", PySystem.Console.MessageType.Info, log=False)
            if (
                yield from self.cast_skill_id(
                    self.shadow_form, extra_condition=(has_deadly_paradox), log=False, aftercast_delay=1750
                )
            ):
                ConsoleLog(self.build_name, "Casting Shadow Form.", PySystem.Console.MessageType.Info, log=False)
            if (yield from self.cast_skill_id(self.shroud_of_distress, log=False, aftercast_delay=1750)):
                ConsoleLog(self.build_name, "Casting Shroud of Distress.", PySystem.Console.MessageType.Info, log=False)

    def UpkeepShroudOfDistress(self, min_remaining_buff_duration: int = 3000):
        player_agent_id = Player.GetAgentID()
        has_shroud_of_distress = (
            Routines.Checks.Effects.HasBuff(player_agent_id, self.shroud_of_distress)
            and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.shroud_of_distress)
            > min_remaining_buff_duration
        )
        if not has_shroud_of_distress:
            if (yield from self.cast_skill_id(self.shroud_of_distress, log=False, aftercast_delay=1750)):
                ConsoleLog(self.build_name, "Casting Shroud of Distress.", PySystem.Console.MessageType.Info, log=False)

    def CastShroudOfDistress(self):
        player_agent_id = Player.GetAgentID()
        if Agent.GetHealth(player_agent_id) < 0.45:
            ConsoleLog(self.build_name, "Casting Shroud of Distress.", PySystem.Console.MessageType.Info, log=False)
            yield from self.cast_skill_id(self.shroud_of_distress, log=False, aftercast_delay=1750)

    def vector_angle(self, a: Tuple[float, float], b: Tuple[float, float]) -> float:
        """Cosine similarity. 1 = same direction, -1 = opposite."""
        dot = a[0] * b[0] + a[1] * b[1]
        mag_a = math.hypot(*a)
        mag_b = math.hypot(*b)
        if mag_a == 0 or mag_b == 0:
            return 1
        return dot / (mag_a * mag_b)

    def pick_escape_enemy(self) -> int:
        """Enemy most opposite the run goal — Heart of Shadow steps away from it.

        The two centre points are hardcoded Jaga Moraine run anchors; that is
        why this build is map-gated and cannot generalise."""
        center_point1 = (10980, -21532)
        center_point2 = (11461, -17282)
        player_pos = Player.GetXY()

        distance_to_center1 = Utils.Distance(player_pos, center_point1)
        distance_to_center2 = Utils.Distance(player_pos, center_point2)
        goal = center_point1 if distance_to_center1 < distance_to_center2 else center_point2
        to_goal = (goal[0] - player_pos[0], goal[1] - player_pos[1])

        best_enemy = 0
        most_opposite_score = 1

        enemy_array = Routines.Agents.GetFilteredEnemyArray(player_pos[0], player_pos[1], Range.Spellcast.value)
        for enemy in enemy_array:
            if Agent.IsDead(enemy):
                continue
            enemy_pos = Agent.GetXY(enemy)
            to_enemy = (enemy_pos[0] - player_pos[0], enemy_pos[1] - player_pos[1])
            angle_score = self.vector_angle(to_goal, to_enemy)
            if angle_score < most_opposite_score:
                most_opposite_score = angle_score
                best_enemy = enemy
        return best_enemy

    def CastHeartOfShadow(self):
        best_enemy = self.pick_escape_enemy()
        if best_enemy:
            yield from Routines.Yield.Agents.ChangeTarget(best_enemy)
        else:
            yield from Routines.Yield.Agents.TargetNearestEnemy(Range.Earshot.value)

        ConsoleLog(self.build_name, "Forced HoS", PySystem.Console.MessageType.Info, log=False)
        if (yield from self.cast_skill_id(self.heart_of_shadow, log=False, aftercast_delay=350)):
            self.stuck_signal = False

    # ---- the farm routine, preserved verbatim ----

    def farm_routine(self):
        def GetWastrelsTarget():
            player_pos = Player.GetXY()
            enemy_array = Routines.Agents.GetFilteredEnemyArray(player_pos[0], player_pos[1], Range.Spellcast.value)
            for enemy in enemy_array:
                if Agent.IsDead(enemy):
                    continue
                if Agent.IsHexed(enemy):
                    continue
                if not Agent.IsEnchanted(enemy):
                    continue
                return enemy

        if not Routines.Checks.Map.MapValid():
            yield from Routines.Yield.wait(1000)
            return

        min_remaining_buff_duration = 3000

        # Off-map branch: defensive upkeep only, then idle. This early return is
        # why the routine cannot be a flat priority Selector.
        if not Map.GetMapID() == Map.GetMapIDByName("Jaga Moraine"):
            from Core import AgentArray
            from Core.enums import AgentModelID

            agent_array = AgentArray.GetEnemyArray()
            agent_array = AgentArray.Filter.ByCondition(
                agent_array,
                lambda agent: Agent.GetModelID(agent)
                in (AgentModelID.FROZEN_ELEMENTAL.value, AgentModelID.FROST_WURM.value),
            )
            agent_array = AgentArray.Filter.ByDistance(agent_array, Player.GetXY(), Range.Spellcast.value)
            if len(agent_array) > 0:
                yield from self.DefensiveActions()

            if Routines.Checks.Agents.InDanger(Range.Earshot):
                yield from self.DefensiveActions()

            if Routines.Checks.Agents.InDanger(2000):
                yield from self.UpkeepShroudOfDistress(min_remaining_buff_duration)

            yield from Routines.Yield.wait(1000)
            return

        if Agent.IsDead(Player.GetAgentID()):
            yield from Routines.Yield.wait(1000)
            return

        if not Routines.Checks.Skills.CanCast():
            yield from Routines.Yield.wait(100)
            return

        if self.routine_finished:
            yield from Routines.Yield.wait(1000)
            return

        player_agent_id = Player.GetAgentID()
        has_shadow_form = Routines.Checks.Effects.HasBuff(player_agent_id, self.shadow_form)
        shadow_form_buff_time_remaining = (
            GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.shadow_form) if has_shadow_form else 0
        )
        has_shadow_form = has_shadow_form and shadow_form_buff_time_remaining > 1500

        if Routines.Checks.Agents.InDanger(Range.Spellcast):
            has_deadly_paradox = Routines.Checks.Effects.HasBuff(player_agent_id, self.deadly_paradox)
            if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shadow_form)):
                GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
                if (
                    yield from self.cast_skill_id(
                        self.deadly_paradox, extra_condition=(not has_deadly_paradox), log=False, aftercast_delay=200
                    )
                ):
                    ConsoleLog(self.build_name, "Casting Deadly Paradox.", PySystem.Console.MessageType.Info, log=False)

                GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
                if (yield from self.cast_skill_id(self.shadow_form, log=False, aftercast_delay=1950)):
                    ConsoleLog(self.build_name, "Casting Shadow Form.", PySystem.Console.MessageType.Info, log=False)
                    return

        has_shroud_of_distress = (
            Routines.Checks.Effects.HasBuff(player_agent_id, self.shroud_of_distress)
            and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.shroud_of_distress)
            > min_remaining_buff_duration
        )
        if not has_shroud_of_distress:
            ConsoleLog(self.build_name, "Casting Shroud of Distress.", PySystem.Console.MessageType.Info, log=False)
            GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
            if (yield from self.cast_skill_id(self.shroud_of_distress, log=False, aftercast_delay=1950)):
                return

        has_channeling = (
            Routines.Checks.Effects.HasBuff(player_agent_id, self.channeling)
            and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.channeling)
            > min_remaining_buff_duration
        )
        if not has_channeling:
            ConsoleLog(self.build_name, "Casting Channeling.", PySystem.Console.MessageType.Info, log=False)
            if (yield from self.cast_skill_id(self.channeling, log=False, aftercast_delay=1850)):
                return

        if has_shadow_form:
            if (yield from self.cast_skill_id(self.way_of_perfection, log=False, aftercast_delay=1000)):
                ConsoleLog(self.build_name, "Casting Way of Perfection.", PySystem.Console.MessageType.Info, log=False)
                return

        if not self.in_killing_routine or Agent.GetHealth(player_agent_id) < 0.05:
            health = Agent.GetHealth(player_agent_id)
            if health < 0.35 or self.stuck_signal:
                best_enemy = self.pick_escape_enemy()
                if best_enemy:
                    yield from Routines.Yield.Agents.ChangeTarget(best_enemy)
                else:
                    yield from Routines.Yield.Agents.TargetNearestEnemy(Range.Earshot.value)

                if (yield from self.cast_skill_id(self.heart_of_shadow, log=False, aftercast_delay=350)):
                    ConsoleLog(
                        self.build_name,
                        f"Hos life = {health} stuck counter: {self.GetStuckSignal()}",
                        PySystem.Console.MessageType.Info,
                        log=False,
                    )
                    self.stuck_signal = False
                    # Deliberate double cast — the legacy build fires HoS twice
                    # to clear the pack. Kept as-is.
                    yield from self.cast_skill_id(self.heart_of_shadow, log=False, aftercast_delay=350)
                    return

        if self.in_killing_routine and has_shadow_form and has_shroud_of_distress and has_channeling:
            both_ready = Routines.Checks.Skills.IsSkillSlotReady(
                self.wastrels_demise_slot
            ) and Routines.Checks.Skills.IsSkillSlotReady(self.arcane_echo_slot)
            target = GetWastrelsTarget()
            if target and shadow_form_buff_time_remaining >= 5000:
                GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
                Player.ChangeTarget(target)
                if (
                    yield from self.cast_skill_slot(
                        self.arcane_echo_slot, extra_condition=both_ready, log=False, aftercast_delay=2750
                    )
                ):
                    Player.Interact(target, False)
                    ConsoleLog(self.build_name, "Casting Arcane Echo.", PySystem.Console.MessageType.Info, log=False)
                else:
                    if (yield from self.cast_skill_slot(self.arcane_echo_slot, log=False, aftercast_delay=750)):
                        Player.Interact(target, False)
                        ConsoleLog(
                            self.build_name, "Casting Echoed Wastrel.", PySystem.Console.MessageType.Info, log=False
                        )

            target = GetWastrelsTarget()
            if target and not Routines.Checks.Skills.IsSkillSlotReady(self.arcane_echo_slot):
                GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
                Player.ChangeTarget(target)
                if (yield from self.cast_skill_slot(self.wastrels_demise_slot, log=False, aftercast_delay=750)):
                    Player.Interact(target, False)

        yield from Routines.Yield.wait(100)

    def build_rotation_tree(self) -> BehaviorTree:
        """Single hosted node. See the module docstring for why this is not
        decomposed — drive() gives the generator correct RUNNING semantics."""
        return rotation_tree(
            "SFAssassinVaettir",
            [],
            [cast(self, "VaettirFarmRoutine", lambda: self.farm_routine())],
        )
