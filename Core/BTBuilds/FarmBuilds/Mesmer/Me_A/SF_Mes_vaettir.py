"""BT port of Builds/Mesmer/Me_A/SF_Mes_vaettir.py — Shadow Form Mesmer Vaettir farmer.

=============================================================================
FarmBuild — same reasoning as SF_Ass_vaettir; see that file's header for the
full rationale. Short version: script-driven, map-gated, multi-frame, mutates
shared queue state. Hosted as one generator under a single BT node rather than
decomposed into a Selector, because the waits and the ordering ARE the
behaviour.
=============================================================================

Callers: Bots/marks_coding_corner/VaettirMarksMods.py,
         Widgets/Automation/Bots/Farmers/Events/YAVB 2.0.py

DIFFERENCES FROM THE ASSASSIN VARIANT — do not assume they are interchangeable:

  * Mantra of Earth replaces Channeling as the third upkeep buff.
  * Shroud of Distress reapplies opportunistically while Shadow Form has
    >8000 ms left, not just when missing.
  * Way of Perfection is cast unconditionally, not gated on Shadow Form.
  * Deadly Paradox does NOT gate the Shadow Form cast (the Assassin variant
    passes extra_condition=has_deadly_paradox; this one does not).
  * DefensiveActions is HP-tiered (0.7 shroud / 0.8 WoP / 0.25 HoS) instead of
    the Assassin's flat sequence, and there is no UpkeepShroudOfDistress.
  * SetStuckSignal trips at `> 3`, not `> 0`.
  * The Heart of Shadow bail additionally requires the player to be OUTSIDE
    the kill spot (12684, -17184) — it will not bail while parked on the pile.
  * The Wastrel's spike needs >=3 enemies in earshot and Shadow Form >=4000 ms
    (Assassin: no enemy count, >=5000 ms), targets a not-hexed enemy rather
    than a not-hexed AND enchanted one, and uses shorter aftercasts.
  * CastHeartOfShadow clears stuck_signal BEFORE casting, deliberately — the
    legacy comment says this avoids a recast when the value does not reset in
    time.

The kill-spot and centre-point coordinates are hardcoded Jaga Moraine anchors.
That is what makes this build map-specific and unfit for contract matching.
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

KILL_SPOT = (12684, -17184)
CENTER_POINT_1 = (10980, -21532)
CENTER_POINT_2 = (11461, -17282)


class SF_Mes_vaettir(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Shadow Form Mesmer Vaettir",
            required_primary=Profession.Mesmer,
            required_secondary=Profession.Assassin,
            template_code="OQdUAQROqPP8Id2BkAiAvpLBDAA",
            is_combat_automator_compatible=False,
            required_skills=[
                GLOBAL_CACHE.Skill.GetID("Deadly_Paradox"),
                GLOBAL_CACHE.Skill.GetID("Shadow_Form"),
                GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress"),
                GLOBAL_CACHE.Skill.GetID("Way_of_Perfection"),
                GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow"),
                GLOBAL_CACHE.Skill.GetID("Wastrels_Demise"),
                GLOBAL_CACHE.Skill.GetID("Arcane_Echo"),
                GLOBAL_CACHE.Skill.GetID("Mantra_of_Earth"),
            ],
        )
        if match_only:
            return

        self.deadly_paradox_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Deadly_Paradox"))
        self.shadow_form_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Shadow_Form"))
        self.shroud_of_distress_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(
            GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress")
        )
        self.way_of_perfection_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(
            GLOBAL_CACHE.Skill.GetID("Way_of_Perfection")
        )
        self.heart_of_shadow_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow"))
        self.wastrels_demise_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Wastrels_Demise"))
        self.arcane_echo_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Arcane_Echo"))
        self.mantra_of_earth_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(GLOBAL_CACHE.Skill.GetID("Mantra_of_Earth"))

        self.shadow_form = GLOBAL_CACHE.Skill.GetID("Shadow_Form")
        self.deadly_paradox = GLOBAL_CACHE.Skill.GetID("Deadly_Paradox")
        self.shroud_of_distress = GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress")
        self.mantra_of_earth = GLOBAL_CACHE.Skill.GetID("Mantra_of_Earth")
        self.way_of_perfection = GLOBAL_CACHE.Skill.GetID("Way_of_Perfection")
        self.heart_of_shadow = GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow")

        self.stuck_signal = False
        self.waypoint = (0, 0)
        self.in_killing_routine = False
        self.routine_finished = False

    # ---- external control surface, called by the farm scripts ----

    def SetKillingRoutine(self, in_killing_routine: bool):
        self.in_killing_routine = in_killing_routine

    def SetRoutineFinished(self, routine_finished: bool):
        self.routine_finished = routine_finished

    def SetStuckSignal(self, stuck_counter: int):
        # NOTE: threshold is 3 here, 0 in the Assassin variant.
        self.stuck_signal = stuck_counter > 3

    def GetStuckSignal(self) -> bool:
        return self.stuck_signal

    # ---- casting helpers: Routines.Yield.Skills, not CombatServices ----

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
        player_hp = Agent.GetHealth(player_agent_id)

        if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shadow_form)):
            if (
                yield from self.cast_skill_id(
                    self.deadly_paradox, extra_condition=(not has_deadly_paradox), log=False, aftercast_delay=100
                )
            ):
                ConsoleLog(self.build_name, "Casting Deadly Paradox.", PySystem.Console.MessageType.Info, log=False)

            if (yield from self.cast_skill_id(self.shadow_form, log=False, aftercast_delay=1750)):
                ConsoleLog(self.build_name, "Casting Shadow Form.", PySystem.Console.MessageType.Info, log=False)

        if player_hp < 0.7 and (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shroud_of_distress)):
            yield from self.cast_skill_id(self.shroud_of_distress, log=False, aftercast_delay=500)
            ConsoleLog(self.build_name, "Casting Shroud for defense.", PySystem.Console.MessageType.Info, log=False)

        if player_hp < 0.8 and (yield from Routines.Yield.Skills.IsSkillIDUsable(self.way_of_perfection)):
            yield from self.cast_skill_id(self.way_of_perfection, log=False, aftercast_delay=500)
            ConsoleLog(
                self.build_name, "Casting Way of Perfection for defense.", PySystem.Console.MessageType.Info, log=False
            )

        if player_hp < 0.25 and (yield from Routines.Yield.Skills.IsSkillIDUsable(self.heart_of_shadow)):
            yield from self.CastHeartOfShadow()

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
        """Enemy most opposite the nearest run anchor — HoS steps away from it."""
        player_pos = Player.GetXY()
        distance_to_center1 = Utils.Distance(player_pos, CENTER_POINT_1)
        distance_to_center2 = Utils.Distance(player_pos, CENTER_POINT_2)
        goal = CENTER_POINT_1 if distance_to_center1 < distance_to_center2 else CENTER_POINT_2
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

        # Cleared BEFORE the cast, deliberately — legacy comment: avoids
        # recasting when the value does not reset in time.
        self.stuck_signal = False
        yield from self.cast_skill_id(self.heart_of_shadow, log=False, aftercast_delay=350)

    # ---- the farm routine, preserved verbatim ----

    def farm_routine(self):
        def GetNotHexedEnemy():
            player_pos = Player.GetXY()
            enemy_array = Routines.Agents.GetFilteredEnemyArray(player_pos[0], player_pos[1], Range.Spellcast.value)
            for enemy in enemy_array:
                if Agent.IsDead(enemy):
                    continue
                if Agent.IsHexed(enemy):
                    continue
                return enemy

        if not Routines.Checks.Map.MapValid():
            yield from Routines.Yield.wait(1000)
            return

        if not Map.GetMapID() == Map.GetMapIDByName("Jaga Moraine"):
            from Core import AgentArray
            from Core import AgentModelID

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

        has_shroud_of_distress = Routines.Checks.Effects.HasBuff(player_agent_id, self.shroud_of_distress)
        if not has_shroud_of_distress or (
            shadow_form_buff_time_remaining > 8000
            and (yield from Routines.Yield.Skills.IsSkillIDUsable(self.shroud_of_distress))
        ):
            ConsoleLog(self.build_name, "Casting Shroud of Distress.", PySystem.Console.MessageType.Info, log=False)
            GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
            if (yield from self.cast_skill_id(self.shroud_of_distress, log=False, aftercast_delay=1950)):
                return

        has_mantra_of_earth = Routines.Checks.Effects.HasBuff(player_agent_id, self.mantra_of_earth)
        if not has_mantra_of_earth:
            ConsoleLog(self.build_name, "Casting Mantra Of Earth.", PySystem.Console.MessageType.Info, log=False)
            if (yield from self.cast_skill_id(self.mantra_of_earth, log=False, aftercast_delay=200)):
                return

        if (yield from self.cast_skill_id(self.way_of_perfection, log=False, aftercast_delay=1000)):
            ConsoleLog(self.build_name, "Casting Way of Perfection.", PySystem.Console.MessageType.Info, log=False)
            return

        if not self.in_killing_routine:
            player_hp = Agent.GetHealth(player_agent_id)
            kill_spot_x, kill_spot_y = KILL_SPOT
            player_x, player_y = Player.GetXY()
            dx = kill_spot_x - player_x
            dy = kill_spot_y - player_y
            distance_threshold = Range.Area.value * 1.5
            within_range_distance = dx * dx + dy * dy <= distance_threshold * distance_threshold
            if (player_hp < 0.35 and not within_range_distance) or self.stuck_signal:
                best_enemy = self.pick_escape_enemy()
                if best_enemy:
                    yield from Routines.Yield.Agents.ChangeTarget(best_enemy)
                else:
                    yield from Routines.Yield.Agents.TargetNearestEnemy(Range.Earshot.value)

                if (yield from self.cast_skill_id(self.heart_of_shadow, log=False, aftercast_delay=350)):
                    return

        if self.in_killing_routine and has_shadow_form and has_shroud_of_distress and has_mantra_of_earth:
            is_wastrels_slot_ready = Routines.Checks.Skills.IsSkillSlotReady(self.wastrels_demise_slot)
            is_arcane_echo_slot_ready = Routines.Checks.Skills.IsSkillSlotReady(self.arcane_echo_slot)
            target = GetNotHexedEnemy()
            px, py = Player.GetXY()
            num_enemies = len(Routines.Agents.GetFilteredEnemyArray(px, py, Range.Earshot.value))
            if target and shadow_form_buff_time_remaining >= 4000 and num_enemies >= 3:
                GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
                Player.ChangeTarget(target)
                if is_wastrels_slot_ready and is_arcane_echo_slot_ready:
                    yield from self.cast_skill_slot(self.arcane_echo_slot, log=False, aftercast_delay=1200)
                    Player.Interact(target, False)
                    ConsoleLog(self.build_name, "Casting Arcane Echo.", PySystem.Console.MessageType.Info, log=False)
                elif is_arcane_echo_slot_ready:
                    yield from self.cast_skill_slot(self.arcane_echo_slot, log=False, aftercast_delay=500)
                    Player.Interact(target, False)
                    ConsoleLog(self.build_name, "Casting Echoed Wastrel.", PySystem.Console.MessageType.Info, log=False)

            target = GetNotHexedEnemy()
            if target and not Routines.Checks.Skills.IsSkillSlotReady(self.arcane_echo_slot):
                GLOBAL_CACHE._ActionQueueManager.ResetQueue("ACTION")
                Player.ChangeTarget(target)
                if (yield from self.cast_skill_slot(self.wastrels_demise_slot, log=False, aftercast_delay=500)):
                    Player.Interact(target, False)

        yield from Routines.Yield.wait(100)

    def build_rotation_tree(self) -> BehaviorTree:
        """Single hosted node — see module header for why this is not a tree."""
        return rotation_tree(
            "SFMesmerVaettir",
            [],
            [cast(self, "VaettirFarmRoutine", lambda: self.farm_routine())],
        )
