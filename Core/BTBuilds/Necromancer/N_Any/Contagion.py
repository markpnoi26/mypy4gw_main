"""BT port of Builds/Necromancer/N_Any/Contagion.py.

The ladder is clean (12 rungs) but most rungs call build-local generator
helpers that pick their own target. Those helpers are carried over verbatim —
each keeps its pick-then-cast pairing inside one generator, so a target cannot
shift between a guard node and a cast node.

`_self_effect_assumed_until` keeps its underscore name: masochism() reads it via
getattr with a default, matching the legacy lazy-init pattern.
"""

from Core import AgentArray, BldMgrBT, GLOBAL_CACHE, Profession, Range, Routines, Utils
from Core.Agent import Agent
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Builds.Skills import SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree

CONDITION_SKILL_IDS = {
    "bleeding": Skill.GetID("Bleeding"),
    "burning": Skill.GetID("Burning"),
    "poison": Skill.GetID("Poison"),
    "disease": Skill.GetID("Disease"),
    "blind": Skill.GetID("Blind"),
    "dazed": Skill.GetID("Dazed"),
    "crippled": Skill.GetID("Crippled"),
    "deep_wound": Skill.GetID("Deep_Wound"),
    "weakness": Skill.GetID("Weakness"),
    "cracked_armor": Skill.GetID("Cracked_Armor"),
}

CONTAGION_ID = Skill.GetID("Contagion")
FOUL_FEAST_ID = Skill.GetID("Foul_Feast")
MASOCHISM_ID = Skill.GetID("Masochism")
DARK_AURA_ID = Skill.GetID("Dark_Aura")

BURNING_SPEED_ID = Skill.GetID("Burning_Speed")
POISONED_HEART_ID = Skill.GetID("Poisoned_Heart")
EBON_ESCAPE_ID = Skill.GetID("Ebon_Escape")
I_AM_UNSTOPPABLE_ID = Skill.GetID("I_Am_Unstoppable")
SHADOW_SANCTUARY_KURZICK_ID = Skill.GetID("Shadow_Sanctuary_kurzick")
SHADOW_SANCTUARY_LUXON_ID = Skill.GetID("Shadow_Sanctuary_luxon")
SIGNET_OF_AGONY_ID = Skill.GetID("Signet_of_Agony")
DEATHS_CHARGE_ID = Skill.GetID("Deaths_Charge")


class Contagion(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Contagion",
            required_primary=Profession.Necromancer,
            required_secondary=Profession(0),
            template_code="OAZDUsx6QJgbhMV3MIN0l0k0BA",
            required_skills=[
                CONTAGION_ID,
                FOUL_FEAST_ID,
                MASOCHISM_ID,
                DARK_AURA_ID,
            ],
            optional_skills=[
                BURNING_SPEED_ID,
                POISONED_HEART_ID,
                EBON_ESCAPE_ID,
                I_AM_UNSTOPPABLE_ID,
                SHADOW_SANCTUARY_KURZICK_ID,
                SHADOW_SANCTUARY_LUXON_ID,
                SIGNET_OF_AGONY_ID,
                DEATHS_CHARGE_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self.melee_hint_logged = False

    def ebon_escape_target(self):
        player_pos = Player.GetXY()
        allies = Routines.Agents.GetFilteredAllyArray(
            player_pos[0],
            player_pos[1],
            Range.Spellcast.value,
            other_ally=True,
        )

        target_agent_id = 0
        best_enemy_count = -1
        for ally_id in allies:
            ally_x, ally_y = Agent.GetXY(ally_id)
            enemies = Routines.Agents.GetFilteredEnemyArray(ally_x, ally_y, Range.Nearby.value)
            enemies = AgentArray.Filter.ByCondition(enemies, lambda eid: Agent.IsAlive(eid))
            enemy_count = len(enemies or [])
            if enemy_count > best_enemy_count:
                best_enemy_count = enemy_count
                target_agent_id = ally_id

        return target_agent_id, best_enemy_count

    def ebon_escape_emergency(self):
        if not self.IsSkillEquipped(EBON_ESCAPE_ID):
            return False

        target_agent_id, _ = self.ebon_escape_target()
        if not target_agent_id:
            return False

        own_low = Agent.GetHealth(Player.GetAgentID()) < 0.40
        ally_low = Agent.GetHealth(target_agent_id) < 0.40
        if not (own_low or ally_low):
            return False

        return (
            yield from self.CastSkillIDAndRestoreTarget(
                skill_id=EBON_ESCAPE_ID,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def ebon_escape_cluster(self):
        if not self.IsSkillEquipped(EBON_ESCAPE_ID):
            return False

        target_agent_id, best_enemy_count = self.ebon_escape_target()
        if not target_agent_id or best_enemy_count <= 0:
            return False

        return (
            yield from self.CastSkillIDAndRestoreTarget(
                skill_id=EBON_ESCAPE_ID,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def i_am_unstoppable(self):
        if not self.IsSkillEquipped(I_AM_UNSTOPPABLE_ID):
            return False
        if not self.IsInAggro():
            return False
        if Routines.Checks.Agents.HasEffect(Player.GetAgentID(), I_AM_UNSTOPPABLE_ID):
            return False

        return (
            yield from self.CastSkillID(
                skill_id=I_AM_UNSTOPPABLE_ID,
                log=False,
                aftercast_delay=250,
            )
        )

    def masochism(self, assume_active_ms: int = 25000):
        if not self.IsSkillEquipped(MASOCHISM_ID):
            return False

        player_agent_id = Player.GetAgentID()
        now_ms = int(Utils.GetBaseTimestamp())
        assumed_effects = getattr(self, "_self_effect_assumed_until", {})

        if int(assumed_effects.get(MASOCHISM_ID, 0) or 0) > now_ms:
            return False

        if Routines.Checks.Agents.HasEffect(player_agent_id, MASOCHISM_ID):
            remaining_ms = int(
                GLOBAL_CACHE.Effects.GetEffectTimeRemaining(
                    player_agent_id,
                    MASOCHISM_ID,
                )
                or 0
            )
            if remaining_ms > 2000:
                assumed_effects.pop(MASOCHISM_ID, None)
                return False

        cast_result = yield from self.CastSkillID(
            skill_id=MASOCHISM_ID,
            log=False,
            aftercast_delay=250,
        )
        if cast_result:
            assumed_effects[MASOCHISM_ID] = now_ms + max(0, int(assume_active_ms))
            setattr(self, "_self_effect_assumed_until", assumed_effects)
            return True

        return False

    def dark_aura(self):
        if not self.IsSkillEquipped(DARK_AURA_ID):
            return False
        if not (self.IsInAggro() or self.IsCloseToAggro()):
            return False

        player_agent_id = Player.GetAgentID()
        target_agent_id = 0

        if not Routines.Checks.Agents.HasEffect(player_agent_id, DARK_AURA_ID):
            target_agent_id = player_agent_id
        else:
            player_pos = Player.GetXY()
            allies = Routines.Agents.GetFilteredAllyArray(
                player_pos[0],
                player_pos[1],
                Range.Spellcast.value,
                other_ally=True,
            )
            for ally_id in allies:
                if Routines.Checks.Agents.HasEffect(ally_id, MASOCHISM_ID) and not Routines.Checks.Agents.HasEffect(
                    ally_id, DARK_AURA_ID
                ):
                    target_agent_id = ally_id
                    break

        if not target_agent_id:
            return False

        return (
            yield from self.CastSkillIDAndRestoreTarget(
                skill_id=DARK_AURA_ID,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def count_distinct_conditions(self, agent_id: int) -> int:
        detected: set[str] = set()

        for name, has_flag in (
            ("bleeding", Agent.IsBleeding),
            ("crippled", Agent.IsCrippled),
            ("deep_wound", Agent.IsDeepWounded),
            ("poison", Agent.IsPoisoned),
        ):
            if has_flag(agent_id):
                detected.add(name)

        for name, condition_id in CONDITION_SKILL_IDS.items():
            if condition_id and name not in detected and Routines.Checks.Agents.HasEffect(agent_id, condition_id):
                detected.add(name)

        count = len(detected)
        if count == 0 and Agent.IsConditioned(agent_id):
            count = 1
        return count

    def foul_feast(self, min_conditions: int = 1):
        if not self.IsSkillEquipped(FOUL_FEAST_ID):
            return False

        player_pos = Player.GetXY()

        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByDistance(enemy_array, player_pos, Range.Nearby.value)
        enemy_array = AgentArray.Filter.ByCondition(enemy_array, lambda aid: Agent.IsAlive(aid))
        if not enemy_array:
            return False

        allies = Routines.Agents.GetFilteredAllyArray(
            player_pos[0],
            player_pos[1],
            Range.Spellcast.value,
            other_ally=True,
        )
        target_agent_id = 0
        best_key = None
        for ally_id in allies:
            condition_count = self.count_distinct_conditions(ally_id)
            if condition_count < min_conditions:
                continue
            key = (condition_count, -Agent.GetHealth(ally_id))
            if best_key is None or key > best_key:
                best_key = key
                target_agent_id = ally_id

        if not target_agent_id:
            return False

        return (
            yield from self.CastSkillIDAndRestoreTarget(
                skill_id=FOUL_FEAST_ID,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def build_rotation_tree(self) -> BehaviorTree:
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "Contagion",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                cast(self, "EbonEscapeEmergency", lambda: self.ebon_escape_emergency()),
                cast(self, "Masochism", lambda: self.masochism()),
                cast(self, "DarkAura", lambda: self.dark_aura()),
                cast(self, "Contagion", lambda: self.skills.Necromancer.DeathMagic.Contagion()),
                cast(self, "FoulFeastOverloaded", lambda: self.foul_feast(min_conditions=4)),
                guarded_cast(
                    self,
                    "BurningSpeed",
                    equipped(BURNING_SPEED_ID),
                    lambda: self.skills.Elementalist.FireMagic.Burning_Speed(),
                ),
                guarded_cast(
                    self,
                    "PoisonedHeart",
                    equipped(POISONED_HEART_ID),
                    lambda: self.skills.Necromancer.Curses.Poisoned_Heart(),
                ),
                guarded_cast(
                    self,
                    "SignetOfAgony",
                    equipped(SIGNET_OF_AGONY_ID),
                    lambda: self.skills.Necromancer.BloodMagic.Signet_of_Agony(),
                ),
                cast(self, "FoulFeast", lambda: self.foul_feast()),
                cast(self, "IAmUnstoppable", lambda: self.i_am_unstoppable()),
                cast(self, "EbonEscapeCluster", lambda: self.ebon_escape_cluster()),
                guarded_cast(
                    self,
                    "DeathsCharge",
                    equipped(DEATHS_CHARGE_ID),
                    lambda: self.skills.Assassin.ShadowArts.Deaths_Charge(),
                ),
                guarded_cast(
                    self,
                    "ShadowSanctuary",
                    lambda: self.IsSkillEquipped(SHADOW_SANCTUARY_KURZICK_ID)
                    or self.IsSkillEquipped(SHADOW_SANCTUARY_LUXON_ID),
                    lambda: self.skills.Assassin.ShadowArts.Shadow_Sanctuary(),
                ),
                cast(self, "AutoAttack", lambda: self.AutoAttack(target_type="EnemyClustered")),
            ],
        )
