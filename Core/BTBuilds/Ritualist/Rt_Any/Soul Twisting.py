"""BT port of Builds/Ritualist/Rt_Any/Soul Twisting.py.

Two gate tiers: `close_to_aggro` gates the whole rotation, then a nested
`in_aggro` Sequence gates the final four rungs.
"""

from dataclasses import dataclass

from Core import Agent
from Core import BldMgrBT
from Core import Player
from Core import Profession
from Core import Routines
from Core.Builds.Skills import HexRemovalPriority
from Core.Builds.Skills import SkillsTemplate
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.Skill import Skill
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast
from ...nodes import cond
from ...nodes import guarded_cast
from ...nodes import rotation_tree
from ...nodes import selector
from ...nodes import sequence

Soul_Twisting_ID = Skill.GetID("Soul_Twisting")
Boon_of_Creation_ID = Skill.GetID("Boon_of_Creation")
Shelter_ID = Skill.GetID("Shelter")
Union_ID = Skill.GetID("Union")
Displacement_ID = Skill.GetID("Displacement")
Summon_Spirits_kurzick_ID = Skill.GetID("Summon_Spirits_kurzick")
Summon_Spirits_luxon_ID = Skill.GetID("Summon_Spirits_luxon")
Armor_of_Unfeeling_ID = Skill.GetID("Armor_of_Unfeeling")
Spirits_Gift_ID = Skill.GetID("Spirits_Gift")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
I_Am_Unstoppable_ID = Skill.GetID("I_Am_Unstoppable")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Remove_Hex_ID = Skill.GetID("Remove_Hex")
Edge_of_Extinction_ID = Skill.GetID("Edge_of_Extinction")


@dataclass(slots=True)
class SoulTwistingSnapshot:
    in_aggro: bool = False
    close_to_aggro: bool = False
    player_energy_pct: float = 1.0


class Soul_Twisting(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Soul Twisting",
            required_primary=Profession.Ritualist,
            template_code="OAOj4MgMJPYTr3jDAAAAAAAAAA",
            required_skills=[
                Soul_Twisting_ID,
                Shelter_ID,
                Union_ID,
            ],
            optional_skills=[
                Boon_of_Creation_ID,
                Displacement_ID,
                Summon_Spirits_kurzick_ID,
                Summon_Spirits_luxon_ID,
                Armor_of_Unfeeling_ID,
                Spirits_Gift_ID,
                Breath_of_the_Great_Dwarf_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                I_Am_Unstoppable_ID,
                Air_of_Superiority_ID,
                Remove_Hex_ID,
                Edge_of_Extinction_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.SetBlockedSkills(
            [
                Soul_Twisting_ID,
                Boon_of_Creation_ID,
                Shelter_ID,
                Union_ID,
                Displacement_ID,
                Summon_Spirits_kurzick_ID,
                Summon_Spirits_luxon_ID,
                Armor_of_Unfeeling_ID,
                Spirits_Gift_ID,
                Breath_of_the_Great_Dwarf_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                I_Am_Unstoppable_ID,
                Air_of_Superiority_ID,
                Remove_Hex_ID,
                Edge_of_Extinction_ID,
            ]
        )
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def get_bar_snapshot(self) -> SoulTwistingSnapshot:
        snapshot = SoulTwistingSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())
        snapshot.close_to_aggro = snapshot.in_aggro or self.IsCloseToAggro()
        snapshot.player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))
        return snapshot

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["soul_twisting_snapshot"] = self.get_bar_snapshot()

    def snapshot(self, node) -> SoulTwistingSnapshot:
        return node.blackboard.get("soul_twisting_snapshot") or SoulTwistingSnapshot()

    def build_rotation_tree(self) -> BehaviorTree:
        communing = lambda: self.skills.Ritualist.Communing
        spawning = lambda: self.skills.Ritualist.SpawningPower
        anyskills = lambda: self.skills.Any
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "SoulTwisting",
            [
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
                cond("CloseToAggro", lambda node: self.snapshot(node).close_to_aggro),
            ],
            [
                cast(
                    self,
                    "RemoveHexHigh",
                    lambda: self.skills.Monk.NoAttribute.Remove_Hex(min_priority=HexRemovalPriority.HIGH),
                ),
                guarded_cast(
                    self,
                    "AirOfSuperiority",
                    lambda node: self.IsSkillEquipped(Air_of_Superiority_ID)
                    and (self.snapshot(node).in_aggro or self.IsCloseToAggro()),
                    lambda: anyskills().PvE.Air_of_Superiority(),
                ),
                guarded_cast(
                    self,
                    "EdgeOfExtinction",
                    equipped(Edge_of_Extinction_ID),
                    lambda: self.skills.Ranger.BeastMastery.Edge_of_Extinction(),
                ),
                guarded_cast(
                    self,
                    "IAmUnstoppable",
                    lambda node: self.snapshot(node).in_aggro,
                    lambda: anyskills().NoAttribute.I_Am_Unstoppable(),
                ),
                cast(self, "BoonOfCreation", lambda: spawning().Boon_of_Creation()),
                cast(self, "SoulTwisting", lambda: spawning().Soul_Twisting()),
                guarded_cast(
                    self,
                    "SummonSpiritsKurzick",
                    equipped(Summon_Spirits_kurzick_ID),
                    lambda: anyskills().NoAttribute.Summon_Spirits_kurzick(),
                ),
                guarded_cast(
                    self,
                    "SummonSpiritsLuxon",
                    equipped(Summon_Spirits_luxon_ID),
                    lambda: anyskills().NoAttribute.Summon_Spirits_luxon(),
                ),
                cast(self, "Shelter", lambda: communing().Shelter()),
                cast(self, "Union", lambda: communing().Union()),
                cast(self, "Displacement", lambda: communing().Displacement()),
                guarded_cast(
                    self,
                    "RemoveHexMedium",
                    lambda node: self.snapshot(node).player_energy_pct >= 0.50,
                    lambda: self.skills.Monk.NoAttribute.Remove_Hex(min_priority=HexRemovalPriority.MEDIUM),
                ),
                cast(self, "ArmorOfUnfeeling", lambda: communing().Armor_of_Unfeeling()),
                cast(self, "SpiritsGift", lambda: spawning().Spirits_Gift()),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda node: self.snapshot(node).in_aggro),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            lambda node: self.snapshot(node).player_energy_pct >= 0.40,
                            lambda: anyskills().PvE.Ebon_Vanguard_Assassin_Support(),
                        ),
                        cast(
                            self,
                            "EbonBattleStandardOfWisdom",
                            lambda: anyskills().NoAttribute.Ebon_Battle_Standard_of_Wisdom(),
                        ),
                        cast(
                            self, "BreathOfTheGreatDwarf", lambda: anyskills().NoAttribute.Breath_of_the_Great_Dwarf()
                        ),
                        guarded_cast(
                            self,
                            "RemoveHex",
                            lambda node: self.snapshot(node).player_energy_pct >= 0.70,
                            lambda: self.skills.Monk.NoAttribute.Remove_Hex(),
                        ),
                    ),
                ),
            ],
        )
