"""BT port of Builds/Monk/Mo_Any/Martyr.py.

The generator ladder becomes a Selector; rung order and guards are preserved
exactly. Per-tick locals that the ladder computed once at the top (the support
snapshot, player energy) move to seed_blackboard so every rung reads the same
values, matching the original's single evaluation per pass.
"""

from dataclasses import dataclass

from Core import BldMgrBT
from Core import Profession
from Core import Range
from Core import Routines
from Core.Agent import Agent
from Core.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine
from HeroAI.targeting import GetAllAlliesArray

from ...nodes import cast, cond, guarded_cast, rotation_tree

Martyr_ID = Skill.GetID("Martyr")
Dwaynas_Kiss_ID = Skill.GetID("Dwaynas_Kiss")
Seed_of_Life_ID = Skill.GetID("Seed_of_Life")
Draw_Conditions_ID = Skill.GetID("Draw_Conditions")
Vigorous_Spirit_ID = Skill.GetID("Vigorous_Spirit")
Remove_Hex_ID = Skill.GetID("Remove_Hex")
Cure_Hex_ID = Skill.GetID("Cure_Hex")


@dataclass(slots=True)
class RequiredSupportSnapshot:
    martyr_target_id: int = 0
    dwaynas_kiss_needed: bool = False
    seed_of_life_needed: bool = False
    draw_conditions_needed: bool = False

    @property
    def any_required_support_needed(self) -> bool:
        return (
            bool(self.martyr_target_id)
            or self.dwaynas_kiss_needed
            or self.seed_of_life_needed
            or self.draw_conditions_needed
        )


class Martyr(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Martyr",
            required_primary=Profession.Monk,
            template_code="OwUUMoG/CoSeRbE5g3EAAAAAAAAA",
            required_skills=[
                Martyr_ID,
                Dwaynas_Kiss_ID,
                Draw_Conditions_ID,
            ],
            optional_skills=[
                Seed_of_Life_ID,
                Vigorous_Spirit_ID,
                Remove_Hex_ID,
                Cure_Hex_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def snapshot(self, node) -> RequiredSupportSnapshot:
        return node.blackboard.get("martyr_snapshot") or RequiredSupportSnapshot()

    def player_energy(self, node) -> float:
        return float(node.blackboard.get("martyr_player_energy", 0.0))

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["martyr_snapshot"] = self.get_required_support_snapshot()
        blackboard["martyr_player_energy"] = float(Agent.GetEnergy(Player.GetAgentID()))

    def build_rotation_tree(self) -> BehaviorTree:
        monk = lambda: self.skills.Monk
        return rotation_tree(
            "Martyr",
            [
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
                cond("SupportNeeded", lambda node: self.snapshot(node).any_required_support_needed),
            ],
            [
                guarded_cast(
                    self,
                    "Martyr",
                    lambda node: bool(self.snapshot(node).martyr_target_id),
                    lambda: self.CastSkillIDAndRestoreTarget(
                        Martyr_ID,
                        self.current_tree().blackboard["martyr_snapshot"].martyr_target_id,
                        aftercast_delay=100,
                    ),
                ),
                cast(
                    self, "RemoveHexHigh", lambda: monk().NoAttribute.Remove_Hex(min_priority=HexRemovalPriority.HIGH)
                ),
                cast(self, "CureHexHigh", lambda: monk().HealingPrayers.Cure_Hex(min_priority=HexRemovalPriority.HIGH)),
                guarded_cast(
                    self,
                    "DwaynasKiss",
                    lambda node: self.snapshot(node).dwaynas_kiss_needed,
                    lambda: monk().HealingPrayers.Dwaynas_Kiss(),
                ),
                guarded_cast(
                    self,
                    "SeedOfLife",
                    lambda node: self.snapshot(node).seed_of_life_needed,
                    lambda: monk().NoAttribute.Seed_of_Life(),
                ),
                guarded_cast(
                    self,
                    "RemoveHexMedium",
                    lambda node: self.player_energy(node) >= 0.50,
                    lambda: monk().NoAttribute.Remove_Hex(min_priority=HexRemovalPriority.MEDIUM),
                ),
                guarded_cast(
                    self,
                    "CureHexMedium",
                    lambda node: self.player_energy(node) >= 0.50,
                    lambda: monk().HealingPrayers.Cure_Hex(min_priority=HexRemovalPriority.MEDIUM),
                ),
                guarded_cast(
                    self,
                    "DrawConditions",
                    lambda node: self.snapshot(node).draw_conditions_needed,
                    lambda: monk().ProtectionPrayers.Draw_Conditions(),
                ),
                guarded_cast(
                    self,
                    "RemoveHexLow",
                    lambda node: self.player_energy(node) >= 0.70,
                    lambda: monk().NoAttribute.Remove_Hex(),
                ),
                guarded_cast(
                    self,
                    "CureHexLow",
                    lambda node: self.player_energy(node) >= 0.70,
                    lambda: monk().HealingPrayers.Cure_Hex(),
                ),
                guarded_cast(
                    self,
                    "VigorousSpirit",
                    lambda: self.IsInAggro() and self.IsSkillEquipped(Vigorous_Spirit_ID),
                    lambda: monk().HealingPrayers.Vigorous_Spirit(),
                ),
            ],
        )

    def get_required_support_snapshot(self) -> RequiredSupportSnapshot:
        martyr = self.GetEquippedCustomSkill(Martyr_ID)
        dwaynas_kiss = self.GetEquippedCustomSkill(Dwaynas_Kiss_ID)
        seed_of_life = self.GetEquippedCustomSkill(Seed_of_Life_ID)
        draw_conditions = self.GetEquippedCustomSkill(Draw_Conditions_ID)

        required_skills = [
            skill for skill in (martyr, dwaynas_kiss, seed_of_life, draw_conditions) if skill is not None
        ]
        snapshot = RequiredSupportSnapshot()
        if not required_skills:
            return snapshot

        player_id = Player.GetAgentID()
        party_area = max(
            (
                int(skill.Conditions.PartyWideArea)
                for skill in required_skills
                if skill.Conditions.IsPartyWide and skill.Conditions.PartyWideArea
            ),
            default=Range.SafeCompass.value,
        )
        ally_array = list(GetAllAlliesArray(party_area) or [])
        if not ally_array:
            return snapshot

        snapshot.martyr_target_id = (
            self.ResolveRankedPartyAllyTarget(
                Martyr_ID,
                martyr,
                validator=lambda agent_id: agent_id != player_id,
            )
            if martyr is not None
            else 0
        )

        dwaynas_kiss_threshold = (
            float(dwaynas_kiss.Conditions.LessLife)
            if dwaynas_kiss is not None and dwaynas_kiss.Conditions.LessLife > 0
            else 0.0
        )
        seed_of_life_threshold = (
            float(seed_of_life.Conditions.LessLife)
            if seed_of_life is not None and seed_of_life.Conditions.LessLife > 0
            else 0.0
        )
        max_other_ally_heal_threshold = max(dwaynas_kiss_threshold, seed_of_life_threshold)
        needs_seed_party_average = bool(
            seed_of_life is not None and seed_of_life.Conditions.IsPartyWide and seed_of_life_threshold > 0
        )

        alive_count = 0
        total_health = 0.0

        for agent_id in ally_array:
            if not Routines.Checks.Agents.IsAlive(agent_id):
                continue

            health = float(Routines.Checks.Agents.GetHealth(agent_id))
            is_other_ally = agent_id != player_id

            alive_count += 1
            total_health += health

            if is_other_ally and max_other_ally_heal_threshold > 0 and health <= max_other_ally_heal_threshold:
                if not snapshot.dwaynas_kiss_needed and dwaynas_kiss_threshold > 0 and health <= dwaynas_kiss_threshold:
                    snapshot.dwaynas_kiss_needed = True

                if not snapshot.seed_of_life_needed and seed_of_life_threshold > 0 and health <= seed_of_life_threshold:
                    snapshot.seed_of_life_needed = True

            if (
                is_other_ally
                and not snapshot.draw_conditions_needed
                and draw_conditions is not None
                and draw_conditions.Conditions.HasCondition
            ):
                if Routines.Checks.Agents.IsConditioned(agent_id):
                    snapshot.draw_conditions_needed = True

            if (
                snapshot.martyr_target_id
                and snapshot.dwaynas_kiss_needed
                and snapshot.draw_conditions_needed
                and (not needs_seed_party_average or snapshot.seed_of_life_needed is False)
            ):
                if not needs_seed_party_average:
                    return snapshot

        if needs_seed_party_average and alive_count > 0:
            average_group_life = total_health / alive_count
            snapshot.seed_of_life_needed = (
                snapshot.seed_of_life_needed and average_group_life <= seed_of_life.Conditions.LessLife
                if seed_of_life is not None
                else False
            )

        return snapshot
