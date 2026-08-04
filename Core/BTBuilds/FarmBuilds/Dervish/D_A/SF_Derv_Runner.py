"""BT port of Builds/CombatAutomatorExcluded/SF_Derv_Runner.py — Shadow Form Dervish runner.

Hosted as one generator node rather than a decomposed tree, for the same reasons
as SF_Ass_vaettir: the watchers are multi-frame (ChangeTarget handshakes, casts
with aftercasts up to 1000 ms), they run as a full sweep rather than a priority
ladder, and shadow_form_watcher publishes `has_sf`, `sf_about_to_expire` and
`enemies_nearby` for stance_watcher to read later in the same sweep. A Selector
of rungs would reorder that and drop Shadow Form on a running character.

The legacy body was a `while True` loop whose guards used `continue`. One tree
tick is one sweep, so those became early returns — same cadence, and the tree
now owns the repetition.

Lives under FarmBuilds so build_registry.is_purpose_specific_build keeps it out
of contract matching: OutpostRunnerV2 instantiates it directly and steers it
through SetRoutineFinished/SetLootingSignal, none of which exists in a HeroAI
party context.
"""

import math

from Core import GLOBAL_CACHE
from Core import Agent
from Core import BldMgrBT
from Core import Player
from Core import Profession
from Core import Range
from Core import Routines
from Core.BTBuilds.build_danger_helper import BuildDangerHelper
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ....nodes import cast
from ....nodes import rotation_tree

DEFAULT_CRIPPLE_KD_TABLE = (
    ([6480, 6481, 6482, 6483], "Jotun"),
    ([6475, 6476, 6473], "Modniir"),
    ([6478, 331], "Elementals"),
    ([4400, 4930, 4396, 4402, 4932, 4401, 4931, 4307, 4306, 6658, 6657], "Mandragor"),
    ([1802, 4323, 7326, 6491, 2547, 2598], "Wurms"),
    ([6488], "Mountain Pinesoul"),
    ([7038, 7040, 2740], "Skeletons"),
    ([7043, 7094], "Zombie"),
    ([6862, 1866, 6869], "Enchanted"),
    ([6337, 6338, 6339, 6340, 6390], "Quetzal"),
    ([2646], "Stone Summit Scout"),
    ([1797, 2493, 2486], "Minotaur"),
    ([2657], "Summit Giant"),
    ([4678], "Skree"),
    ([2312], "Spiders"),
    ([2307], "Roots"),
    ([2732, 2731], "Ghouls"),
    ([2535], "Asura"),
    ([6487], "Bison"),
    ([6678], "Tumbled Elementalist"),
    ([6627], "Charr Axemaster"),
    ([2593], "Grawl"),
    ([5099, 5110, 5094, 5102, 5101, 5080, 5083, 5081], "Corsair"),
    ([4955], "Mesa"),
    ([2530, 2581], "Tundra Giant"),
)

DEFAULT_EXTREME_KD_CATEGORIES = ["Tundra Giant"]


class SF_Derv_Runner(BldMgrBT):
    def __init__(self, build_danger_helper: BuildDangerHelper | None = None, match_only: bool = False):
        super().__init__(
            name="SF_Derv_Runner",
            required_primary=Profession.Dervish,
            required_secondary=Profession.Assassin,
            template_code="Ogei8xsMxjozMudgdXiAdARTCA",
            is_combat_automator_compatible=False,
            required_skills=[
                GLOBAL_CACHE.Skill.GetID("Deadly_Paradox"),
                GLOBAL_CACHE.Skill.GetID("Shadow_Form"),
                GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress"),
                GLOBAL_CACHE.Skill.GetID("Pious_Haste"),
                GLOBAL_CACHE.Skill.GetID("Dwarven_Stability"),
                GLOBAL_CACHE.Skill.GetID("Zealous_Renewal"),
                GLOBAL_CACHE.Skill.GetID("I_Am_Unstoppable"),
            ],
        )
        if match_only:
            return

        self.deadly_paradox = GLOBAL_CACHE.Skill.GetID("Deadly_Paradox")
        self.shadow_form = GLOBAL_CACHE.Skill.GetID("Shadow_Form")
        self.shroud_of_distress = GLOBAL_CACHE.Skill.GetID("Shroud_of_Distress")
        self.pious_haste = GLOBAL_CACHE.Skill.GetID("Pious_Haste")
        self.dwarven_stability = GLOBAL_CACHE.Skill.GetID("Dwarven_Stability")
        self.heart_of_shadow = GLOBAL_CACHE.Skill.GetID("Heart_of_Shadow")
        self.deaths_charge = GLOBAL_CACHE.Skill.GetID("Deaths_Charge")
        self.zealous_renewal = GLOBAL_CACHE.Skill.GetID("Zealous_Renewal")
        self.i_am_unstoppable = GLOBAL_CACHE.Skill.GetID("I_Am_Unstoppable")
        self.muddy_terrain = GLOBAL_CACHE.Skill.GetID("Muddy_Terrain")

        self.is_looting = False
        self.routine_finished = False

        self.has_sf = False
        self.sf_about_to_expire = False
        self.enemies_nearby = False

        self.build_danger_helper = build_danger_helper or BuildDangerHelper(
            cripple_kd_table=DEFAULT_CRIPPLE_KD_TABLE,
            extreme_kd_categories=DEFAULT_EXTREME_KD_CATEGORIES,
        )

    # ---- external control surface: OutpostRunnerV2 drives these ----

    def SetRoutineFinished(self, routine_finished: bool):
        self.routine_finished = routine_finished

    def SetLootingSignal(self, is_looting: bool):
        self.is_looting = is_looting

    # ---- targeted escapes ----

    def enemy_angles(self):
        """Each live enemy in spellcast range with its angle off the player's facing."""
        player_pos = Player.GetXY()
        heading = Agent.GetRotationAngle(Player.GetAgentID())
        facing = (math.cos(heading), math.sin(heading))

        for enemy in Routines.Agents.GetFilteredEnemyArray(player_pos[0], player_pos[1], Range.Spellcast.value):
            if Agent.IsDead(enemy):
                continue
            enemy_pos = Agent.GetXY(enemy)
            dx = enemy_pos[0] - player_pos[0]
            dy = enemy_pos[1] - player_pos[1]
            dot = facing[0] * dx + facing[1] * dy
            det = facing[0] * dy - facing[1] * dx
            yield enemy, abs(math.degrees(math.atan2(det, dot))), math.hypot(dx, dy)

    def cast_heart_of_shadow(self):
        back_narrow, back_narrow_angle = 0, 0.0
        back_wide, back_wide_angle = 0, 0.0
        front_narrow, front_narrow_angle = 0, 181.0
        front_wide, front_wide_angle = 0, 181.0

        for enemy, angle, _ in self.enemy_angles():
            if angle >= 165.0 and angle > back_narrow_angle:
                back_narrow, back_narrow_angle = enemy, angle
            elif angle >= 120.0 and angle > back_wide_angle:
                back_wide, back_wide_angle = enemy, angle
            elif angle <= 15.0 and angle < front_narrow_angle:
                front_narrow, front_narrow_angle = enemy, angle
            elif angle <= 60.0 and angle < front_wide_angle:
                front_wide, front_wide_angle = enemy, angle

        target = back_narrow or back_wide or front_narrow or front_wide
        if target:
            yield from Routines.Yield.Agents.ChangeTarget(target)
        else:
            yield from Routines.Yield.Agents.TargetNearestEnemy(Range.Earshot.value)

        yield from self.CastSkillID(self.heart_of_shadow, log=False, aftercast_delay=125)

    def cast_deaths_charge(self):
        """Death's Charge to the farthest enemy within the forward cone."""
        best_narrow, best_narrow_dist = 0, -1.0
        best_wide, best_wide_dist = 0, -1.0

        for enemy, angle, dist in self.enemy_angles():
            if angle <= 15.0 and dist > best_narrow_dist:
                best_narrow, best_narrow_dist = enemy, dist
            elif angle <= 60.0 and dist > best_wide_dist:
                best_wide, best_wide_dist = enemy, dist

        target = best_narrow or best_wide
        if not target:
            return

        yield from Routines.Yield.Agents.ChangeTarget(target)
        yield from self.CastSkillID(self.deaths_charge, log=False, aftercast_delay=125)

    # ---- watchers, in sweep order ----

    def shadow_form_watcher(self):
        player_agent_id = Player.GetAgentID()
        self.has_sf = Routines.Checks.Effects.HasBuff(player_agent_id, self.shadow_form)
        is_sf_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.shadow_form)
        self.sf_about_to_expire = (
            self.has_sf and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.shadow_form) <= 3000
        )

        px, py = Player.GetXY()
        self.enemies_nearby = len(Routines.Agents.GetFilteredEnemyArray(px, py, max_distance=2000.0)) > 0

        if self.has_sf and not self.sf_about_to_expire:
            yield None
            return

        if self.enemies_nearby and (not self.has_sf or self.sf_about_to_expire) and is_sf_ready:
            yield from self.CastSkillID(self.deadly_paradox, log=False, aftercast_delay=0)
            yield from self.CastSkillID(self.shadow_form, log=False, aftercast_delay=1000)
            self.has_sf = True

    def shroud_of_distress_watcher(self):
        player_agent_id = Player.GetAgentID()
        has_sod = Routines.Checks.Effects.HasBuff(player_agent_id, self.shroud_of_distress)
        is_sod_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.shroud_of_distress)
        is_sod_about_to_expire = (
            has_sod and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.shroud_of_distress) <= 2000
        )
        is_sf_expiring = (
            Routines.Checks.Effects.HasBuff(player_agent_id, self.shadow_form)
            and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.shadow_form) <= 1000
        )
        is_low_health = Agent.GetHealth(player_agent_id) <= 0.55

        if is_sf_expiring:
            return

        if is_sod_ready and is_low_health and (is_sod_about_to_expire or not has_sod):
            yield from self.CastSkillID(self.shroud_of_distress, log=False, aftercast_delay=1000)

    def stability_watcher(self):
        player_agent_id = Player.GetAgentID()
        is_stability_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.dwarven_stability)
        has_stability = Routines.Checks.Effects.HasBuff(player_agent_id, self.dwarven_stability)
        is_stability_expiring = (
            has_stability
            and GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, self.dwarven_stability) <= 1000
        )

        if (is_stability_expiring or not has_stability) and is_stability_ready:
            yield from self.CastSkillID(self.dwarven_stability, log=False, aftercast_delay=125)

    def stance_watcher(self):
        player_agent_id = Player.GetAgentID()
        has_mt = Routines.Checks.Effects.HasBuff(player_agent_id, self.muddy_terrain)

        is_pious_haste_ready = Routines.Checks.Skills.IsSkillIDReady(self.pious_haste)
        is_zealous_renewal_ready = Routines.Checks.Skills.IsSkillIDReady(self.zealous_renewal)

        if (Agent.GetEnergy(player_agent_id) * Agent.GetMaxEnergy(player_agent_id)) < 10:
            yield None
            return

        # Never spend the combo while Shadow Form is down with enemies around.
        if self.enemies_nearby and (self.sf_about_to_expire or not self.has_sf):
            yield None
            return

        if is_pious_haste_ready and is_zealous_renewal_ready and not has_mt:
            yield from self.CastSkillID(self.zealous_renewal, log=False, aftercast_delay=0)
            yield from self.CastSkillID(self.pious_haste, log=False, aftercast_delay=0)

    def unstoppable_watcher(self):
        player_agent_id = Player.GetAgentID()
        px, py = Player.GetXY()

        if not (
            Agent.IsCrippled(player_agent_id)
            or Agent.IsKnockedDown(player_agent_id)
            or self.build_danger_helper.check_cripple_kd(px, py)
        ):
            return

        has_iau = Routines.Checks.Effects.HasBuff(player_agent_id, self.i_am_unstoppable)
        is_iau_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.i_am_unstoppable)

        if is_iau_ready and not has_iau:
            yield from self.CastSkillID(self.i_am_unstoppable, aftercast_delay=0)

    def defensive_watcher(self):
        player_agent_id = Player.GetAgentID()
        is_hos_ready = Routines.Checks.Skills.IsSkillIDReady(self.heart_of_shadow)
        is_emergency_health = Agent.GetHealth(player_agent_id) <= 0.2

        if is_emergency_health and is_hos_ready:
            yield from self.cast_heart_of_shadow()

    def blocked_escape_watcher(self):
        is_stuck = self.build_danger_helper.body_block_detection(seconds=4)
        is_hos_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.heart_of_shadow)
        is_dc_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.deaths_charge)

        if is_stuck and is_hos_ready:
            yield from self.cast_heart_of_shadow()
        elif is_stuck and is_dc_ready:
            yield from self.cast_deaths_charge()

    # ---- one sweep; the tree owns the repetition ----

    def skill_routine(self):
        if not Routines.Checks.Map.MapValid():
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

        if self.is_looting:
            yield from Routines.Yield.wait(1000)
            return

        yield from self.shadow_form_watcher()
        yield from self.shroud_of_distress_watcher()
        yield from self.stability_watcher()
        yield from self.stance_watcher()
        yield from self.unstoppable_watcher()
        yield from self.defensive_watcher()
        yield from self.blocked_escape_watcher()

        yield from Routines.Yield.wait(100)

    def build_rotation_tree(self) -> BehaviorTree:
        return rotation_tree(
            "SFDervRunner",
            [],
            [cast(self, "SFDervRunnerSweep", lambda: self.skill_routine())],
        )
