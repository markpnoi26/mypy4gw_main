"""BT port of Builds/Necromancer/N_D/Soul Taker Scythe.py.

Partially decomposed on purpose. The four opening upkeep rungs are a clean
ladder and become nodes. The scythe spike block is NOT decomposed: it reassigns
`target_agent_id` mid-flow, recomputes `active_flash_enchants` inside the attack
loop, and picks attack order from state that changes as it goes. Expressing that
as a Selector would change which attack fires. It stays one generator, driven by
`self.drive` so it still reports RUNNING/SUCCESS/FAILURE correctly to the tree.
"""

from __future__ import annotations

from Core import AgentArray, BldMgrBT, Profession, Range, Routines
from Core.Agent import Agent
from Core.Builds.Skills import SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Utils import Utils
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree

MASOCHISM_ID = Skill.GetID("Masochism")
SOUL_TAKER_ID = Skill.GetID("Soul_Taker")
TWIN_MOON_SWEEP_ID = Skill.GetID("Twin_Moon_Sweep")
EREMITES_ATTACK_ID = Skill.GetID("Eremites_Attack")
STAGGERING_FORCE_ID = Skill.GetID("Staggering_Force")
DUST_CLOAK_ID = Skill.GetID("Dust_Cloak")
DRUNKEN_MASTER_ID = Skill.GetID("Drunken_Master")
I_AM_UNSTOPPABLE_ID = Skill.GetID("I_Am_Unstoppable")


class Soul_Taker_Scythe(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Soul Taker Scythe",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Dervish,
            template_code="OApjYwpzKTbhf1PXNXaXZXqi0kA",
            required_skills=[
                MASOCHISM_ID,
                SOUL_TAKER_ID,
                TWIN_MOON_SWEEP_ID,
                EREMITES_ATTACK_ID,
                STAGGERING_FORCE_ID,
                DUST_CLOAK_ID,
                DRUNKEN_MASTER_ID,
                I_AM_UNSTOPPABLE_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skillbook: SkillsTemplate = SkillsTemplate(self)

    def get_player_contact_count(self) -> int:
        player_x, player_y = Player.GetXY()
        enemy_array = Routines.Agents.GetFilteredEnemyArray(player_x, player_y, Range.Adjacent.value)
        enemy_array = AgentArray.Filter.ByCondition(
            enemy_array,
            lambda agent_id: Agent.IsValid(agent_id) and not Agent.IsDead(agent_id),
        )
        return len(enemy_array or [])

    def is_in_melee_contact(self, target_agent_id: int) -> bool:
        if not target_agent_id or not Agent.IsValid(target_agent_id) or Agent.IsDead(target_agent_id):
            return False
        return Utils.Distance(Player.GetXY(), Agent.GetXY(target_agent_id)) <= Range.Adjacent.value

    def auto_attack_cluster(self):
        return (yield from self.AutoAttack(target_type="EnemyClustered"))

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["soul_taker_contact_count"] = self.get_player_contact_count()

    def contact_count(self, node) -> int:
        return int(node.blackboard.get("soul_taker_contact_count", 0))

    def scythe_spike(self, contact_count: int):
        """Preserved verbatim from the legacy ladder's second half."""
        target_agent_id = self.current_target_id
        if not self.is_in_melee_contact(target_agent_id):
            if (yield from self.auto_attack_cluster()):
                return True
            target_agent_id = self.current_target_id

        target_cluster_size = 0
        if target_agent_id and Agent.IsValid(target_agent_id) and not Agent.IsDead(target_agent_id):
            target_cluster_size = 1 + Routines.Targeting.CountNearbyEnemies(
                target_agent_id,
                Range.Adjacent.value,
            )

        cluster_size = max(target_cluster_size, contact_count)
        flash_chain_floor = 0.35 if cluster_size >= 2 else 0.15

        if self.IsSkillEquipped(DUST_CLOAK_ID) and (
            yield from self.skillbook.Dervish.EarthPrayers.Dust_Cloak(
                refresh_window_ms=1200,
                min_self_energy_pct=flash_chain_floor,
            )
        ):
            return True

        if self.IsSkillEquipped(STAGGERING_FORCE_ID) and (
            yield from self.skillbook.Dervish.EarthPrayers.Staggering_Force(
                refresh_window_ms=1200,
                min_self_energy_pct=flash_chain_floor,
            )
        ):
            return True

        if not self.is_in_melee_contact(target_agent_id):
            if (yield from self.auto_attack_cluster()):
                return True
            return False

        active_flash_enchants = self.skillbook.Dervish.ScytheMastery.Count_Active_Dervish_Enchantments(
            (DUST_CLOAK_ID, STAGGERING_FORCE_ID)
        )

        twin_ready = self.CanCastSkillID(TWIN_MOON_SWEEP_ID)
        prefer_eremites_first = not twin_ready or (active_flash_enchants == 1 and cluster_size >= 3)
        attack_order = (
            (EREMITES_ATTACK_ID, TWIN_MOON_SWEEP_ID)
            if prefer_eremites_first
            else (TWIN_MOON_SWEEP_ID, EREMITES_ATTACK_ID)
        )

        for attack_skill_id in attack_order:
            if not self.IsSkillEquipped(attack_skill_id):
                continue

            if attack_skill_id == EREMITES_ATTACK_ID:
                if active_flash_enchants <= 0 and cluster_size < 2:
                    continue
                min_energy_pct = 0.10 if cluster_size < 2 else 0.0
                cast_skill = self.skillbook.Dervish.ScytheMastery.Eremites_Attack
            else:
                min_energy_pct = 0.0
                if active_flash_enchants <= 0:
                    min_energy_pct = max(min_energy_pct, 0.20)
                if cluster_size < 2:
                    min_energy_pct = max(min_energy_pct, 0.15)
                cast_skill = self.skillbook.Dervish.ScytheMastery.Twin_Moon_Sweep

            if (
                yield from cast_skill(
                    target_agent_id,
                    cluster_size=cluster_size,
                    min_self_energy_pct=min_energy_pct,
                )
            ):
                return True

            active_flash_enchants = self.skillbook.Dervish.ScytheMastery.Count_Active_Dervish_Enchantments(
                (DUST_CLOAK_ID, STAGGERING_FORCE_ID)
            )

        if (yield from self.auto_attack_cluster()):
            return True

        return False

    def build_rotation_tree(self) -> BehaviorTree:
        return rotation_tree(
            "SoulTakerScythe",
            [cond("NearAggro", lambda: self.IsInAggro() or self.IsCloseToAggro())],
            [
                guarded_cast(
                    self,
                    "IAmUnstoppable",
                    lambda: self.IsSkillEquipped(I_AM_UNSTOPPABLE_ID),
                    lambda: self.skillbook.Any.NoAttribute.I_Am_Unstoppable(
                        contact_count=self.get_player_contact_count(),
                        min_adjacent_enemies=2,
                        refresh_window_ms=1000,
                        aftercast_delay=150,
                    ),
                ),
                guarded_cast(
                    self,
                    "Masochism",
                    lambda: self.IsSkillEquipped(MASOCHISM_ID),
                    lambda: self.skillbook.Necromancer.SoulReaping.Masochism(),
                ),
                cast(
                    self, "SoulTaker", lambda: self.skillbook.Necromancer.SoulReaping.Soul_Taker(refresh_window_ms=2000)
                ),
                cast(self, "DrunkenMaster", lambda: self.skillbook.Any.PvE.Drunken_Master(refresh_window_ms=2000)),
                cast(self, "ScytheSpike", lambda: self.scythe_spike(self.get_player_contact_count())),
            ],
        )
