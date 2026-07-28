"""BT port of Builds/Dervish/D_A/VoS_Grenths_Aura_Farmer.py.

Kept in the matchable tree rather than FarmBuilds: despite the name, the legacy
build carries no exclusion flag, so it is contract-matchable today. Moving it
would be a behaviour change, not a translation.

Every rung in the legacy ladder ends in a bare `return` (None, falsy) rather
than `return True`, so the build reported a failed tick after every successful
cast and fell through to the HeroAI fallback in the same frame. Ported as normal
rungs (SUCCESS). Flagged for review — this one is systemic, all nine rungs.

Ebon Battle Standard goes through CastSpiritSkillID, the one genuinely
multi-frame cast path; `drive()` reports RUNNING across those frames.
"""

from dataclasses import dataclass

from Core import Agent
from Core import BldMgrBT
from Core import GLOBAL_CACHE
from Core import Player
from Core import Profession
from Core import Range
from Core import Routines
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cond, guarded_cast, rotation_tree

Sand_Shards_ID = Skill.GetID("Sand_Shards")
Vow_of_Strength_ID = Skill.GetID("Vow_of_Strength")
Grenths_Aura_ID = Skill.GetID("Grenths_Aura")
Mystic_Regeneration_ID = Skill.GetID("Mystic_Regeneration")
Mirage_Cloak_ID = Skill.GetID("Mirage_Cloak")
Deaths_Charge_ID = Skill.GetID("Deaths_Charge")
I_Am_Unstoppable_ID = Skill.GetID("I_Am_Unstoppable")
Ebon_Battle_Standard_of_Honor_ID = Skill.GetID("Ebon_Battle_Standard_of_Honor")


@dataclass(slots=True)
class VoSSnapshot:
    in_combat: bool = False
    enemy_array: tuple[int, ...] = ()
    nearby_enemy_count: int = 0
    player_hp: float = 1.0
    has_sand_shards: bool = False
    has_vow_of_strength: bool = False
    has_mystic_regeneration: bool = False
    has_mirage_cloak: bool = False
    has_grenths_aura: bool = False
    has_i_am_unstoppable: bool = False
    has_battle_standard: bool = False
    mirage_remaining: float = 0.0


class VoS_Grenths_Aura_Farmer(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="VoS Grenth's Aura Farmer",
            required_primary=Profession.Dervish,
            required_secondary=Profession.Assassin,
            template_code="OgejoqrMLSmXfbdfsXcX4O0k5iA",
            required_skills=[
                Sand_Shards_ID,
                Vow_of_Strength_ID,
                Grenths_Aura_ID,
                Mystic_Regeneration_ID,
                Mirage_Cloak_ID,
                Deaths_Charge_ID,
                I_Am_Unstoppable_ID,
                Ebon_Battle_Standard_of_Honor_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetBlockedSkills(
            [
                Sand_Shards_ID,
                Vow_of_Strength_ID,
                Grenths_Aura_ID,
                Mirage_Cloak_ID,
                Deaths_Charge_ID,
                Ebon_Battle_Standard_of_Honor_ID,
                I_Am_Unstoppable_ID,
            ]
        )

    def get_enemy_array(self, distance: float = Range.Spellcast.value) -> list[int]:
        player_x, player_y = Player.GetXY()
        return list(Routines.Agents.GetFilteredEnemyArray(player_x, player_y, distance) or [])

    def count_enemies_near_agent(self, center_agent_id: int, enemy_array, radius: float = Range.Area.value) -> int:
        if not center_agent_id:
            return 0

        center_x, center_y = Agent.GetXY(center_agent_id)
        radius_sq = radius * radius
        count = 0
        for agent_id in enemy_array:
            enemy_x, enemy_y = Agent.GetXY(agent_id)
            dx = enemy_x - center_x
            dy = enemy_y - center_y
            if dx * dx + dy * dy <= radius_sq:
                count += 1
        return count

    def count_enemies_near_player(self, enemy_array, radius: float = Range.Area.value) -> int:
        player_x, player_y = Player.GetXY()
        radius_sq = radius * radius
        count = 0
        for agent_id in enemy_array:
            enemy_x, enemy_y = Agent.GetXY(agent_id)
            dx = enemy_x - player_x
            dy = enemy_y - player_y
            if dx * dx + dy * dy <= radius_sq:
                count += 1
        return count

    def get_best_deaths_charge_target(self, enemy_array) -> int:
        player_x, player_y = Player.GetXY()
        best_target = 0
        best_cluster = 0

        for agent_id in enemy_array:
            enemy_x, enemy_y = Agent.GetXY(agent_id)
            dx = enemy_x - player_x
            dy = enemy_y - player_y
            if dx * dx + dy * dy <= Range.Adjacent.value * Range.Adjacent.value:
                continue

            cluster_count = self.count_enemies_near_agent(agent_id, enemy_array, Range.Nearby.value)
            if cluster_count > best_cluster:
                best_cluster = cluster_count
                best_target = agent_id

        if best_cluster >= 3:
            return best_target
        return 0

    def get_snapshot(self) -> VoSSnapshot:
        player_agent_id = Player.GetAgentID()
        enemy_array = self.get_enemy_array()
        has_mirage_cloak = Routines.Checks.Effects.HasBuff(player_agent_id, Mirage_Cloak_ID)
        return VoSSnapshot(
            in_combat=bool(self.IsInAggro()),
            enemy_array=tuple(enemy_array),
            nearby_enemy_count=self.count_enemies_near_player(enemy_array, Range.Area.value),
            player_hp=Agent.GetHealth(player_agent_id),
            has_sand_shards=Routines.Checks.Effects.HasBuff(player_agent_id, Sand_Shards_ID),
            has_vow_of_strength=Routines.Checks.Effects.HasBuff(player_agent_id, Vow_of_Strength_ID),
            has_mystic_regeneration=Routines.Checks.Effects.HasBuff(player_agent_id, Mystic_Regeneration_ID),
            has_mirage_cloak=has_mirage_cloak,
            has_grenths_aura=Routines.Checks.Effects.HasBuff(player_agent_id, Grenths_Aura_ID),
            has_i_am_unstoppable=Routines.Checks.Effects.HasBuff(player_agent_id, I_Am_Unstoppable_ID),
            has_battle_standard=Routines.Checks.Effects.HasBuff(player_agent_id, Ebon_Battle_Standard_of_Honor_ID),
            mirage_remaining=(
                GLOBAL_CACHE.Effects.GetEffectTimeRemaining(player_agent_id, Mirage_Cloak_ID) if has_mirage_cloak else 0
            ),
        )

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["vos_snapshot"] = self.get_snapshot()

    def snapshot(self, node) -> VoSSnapshot:
        return node.blackboard.get("vos_snapshot") or VoSSnapshot()

    def current_snapshot(self) -> VoSSnapshot:
        return self.current_tree().blackboard.get("vos_snapshot") or VoSSnapshot()

    def deaths_charge(self):
        target = self.get_best_deaths_charge_target(self.current_snapshot().enemy_array)
        if not target or not self.CanCastSkillID(Deaths_Charge_ID):
            return False
        return (yield from self.CastSkillIDAndRestoreTarget(Deaths_Charge_ID, target, aftercast_delay=350))

    def build_rotation_tree(self) -> BehaviorTree:
        simple_cast = lambda skill_id, delay=250: (lambda: self.CastSkillID(skill_id, aftercast_delay=delay))
        return rotation_tree(
            "VoSGrenthsAuraFarmer",
            [],
            [
                guarded_cast(
                    self,
                    "VowOfStrengthPrecombat",
                    lambda node: not self.snapshot(node).in_combat and not self.snapshot(node).has_vow_of_strength,
                    simple_cast(Vow_of_Strength_ID),
                ),
                guarded_cast(
                    self,
                    "MysticRegenerationPrecombat",
                    lambda node: not self.snapshot(node).in_combat and not self.snapshot(node).has_mystic_regeneration,
                    simple_cast(Mystic_Regeneration_ID),
                ),
                guarded_cast(
                    self,
                    "DeathsCharge",
                    lambda node: self.snapshot(node).in_combat,
                    lambda: self.deaths_charge(),
                ),
                guarded_cast(
                    self,
                    "EbonBattleStandardOfHonor",
                    lambda node: self.snapshot(node).in_combat
                    and self.snapshot(node).nearby_enemy_count >= 3
                    and not self.snapshot(node).has_battle_standard,
                    lambda: self.CastSpiritSkillID(Ebon_Battle_Standard_of_Honor_ID, aftercast_delay=250),
                ),
                guarded_cast(
                    self,
                    "IAmUnstoppable",
                    lambda node: self.snapshot(node).in_combat and not self.snapshot(node).has_i_am_unstoppable,
                    simple_cast(I_Am_Unstoppable_ID, 150),
                ),
                guarded_cast(
                    self,
                    "VowOfStrength",
                    lambda node: self.snapshot(node).in_combat and not self.snapshot(node).has_vow_of_strength,
                    simple_cast(Vow_of_Strength_ID),
                ),
                guarded_cast(
                    self,
                    "MirageCloak",
                    lambda node: self.snapshot(node).in_combat
                    and (not self.snapshot(node).has_mirage_cloak or self.snapshot(node).mirage_remaining <= 1500),
                    simple_cast(Mirage_Cloak_ID),
                ),
                guarded_cast(
                    self,
                    "GrenthsAuraSpike",
                    lambda node: not self.snapshot(node).has_grenths_aura
                    and self.snapshot(node).in_combat
                    and self.snapshot(node).nearby_enemy_count >= 3
                    and self.snapshot(node).player_hp <= 0.85,
                    simple_cast(Grenths_Aura_ID),
                ),
                guarded_cast(
                    self,
                    "SandShards",
                    lambda node: not self.snapshot(node).has_sand_shards,
                    simple_cast(Sand_Shards_ID),
                ),
                guarded_cast(
                    self,
                    "GrenthsAura",
                    lambda node: not self.snapshot(node).has_grenths_aura,
                    simple_cast(Grenths_Aura_ID),
                ),
            ],
        )
