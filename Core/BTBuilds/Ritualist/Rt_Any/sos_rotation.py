"""Shared SoS Spirit Spammer rotation.

Two builds use it — Ritualist-primary and any-primary/Ritualist-secondary — and
legacy duplicated the whole ladder across both files. This is a plain mixin, not
a BldMgrBT subclass, so BuildRegistry does not register it as a build of its own.
"""

from dataclasses import dataclass

from Core import Agent
from Core import Player
from Core import Routines
from Core.Builds.Any.HeroAI import HeroAI as HeroAIBuild
from Core.Builds.Skills import SkillsTemplate
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.Skill import Skill

from ...nodes import cast
from ...nodes import cond
from ...nodes import guarded_cast
from ...nodes import rotation_tree

Signet_of_Spirits_ID = Skill.GetID("Signet_of_Spirits")
Bloodsong_ID = Skill.GetID("Bloodsong")
Painful_Bond_ID = Skill.GetID("Painful_Bond")
Vampirism_ID = Skill.GetID("Vampirism")
Summon_Spirits_kurzick_ID = Skill.GetID("Summon_Spirits_kurzick")
Summon_Spirits_luxon_ID = Skill.GetID("Summon_Spirits_luxon")
Spirit_Siphon_ID = Skill.GetID("Spirit_Siphon")
Great_Dwarf_Weapon_ID = Skill.GetID("Great_Dwarf_Weapon")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Technobabble_ID = Skill.GetID("Technobabble")
Armor_of_Unfeeling_ID = Skill.GetID("Armor_of_Unfeeling")

SOS_TEMPLATE_CODE = "OACiIyk8cNLnVTAAAAAAAAAA"

SOS_REQUIRED_SKILLS = [
    Signet_of_Spirits_ID,
    Bloodsong_ID,
    Painful_Bond_ID,
]

SOS_OPTIONAL_SKILLS = [
    Vampirism_ID,
    Summon_Spirits_kurzick_ID,
    Summon_Spirits_luxon_ID,
    Spirit_Siphon_ID,
    Great_Dwarf_Weapon_ID,
    Ebon_Vanguard_Assassin_Support_ID,
    Technobabble_ID,
    Armor_of_Unfeeling_ID,
]

SOS_BLOCKED_SKILLS = SOS_REQUIRED_SKILLS + SOS_OPTIONAL_SKILLS


@dataclass(slots=True)
class SoSSpiritSpammerBarSnapshot:
    in_aggro: bool = False
    close_to_aggro: bool = False
    player_energy_pct: float = 1.0


class SoSRotationMixin:
    def configure_rotation(self) -> None:
        self.SetFallback("HeroAI", HeroAIBuild(standalone_fallback=True))
        self.SetBlockedSkills(list(SOS_BLOCKED_SKILLS))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def get_bar_snapshot(self) -> SoSSpiritSpammerBarSnapshot:
        snapshot = SoSSpiritSpammerBarSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())
        snapshot.close_to_aggro = snapshot.in_aggro or self.IsCloseToAggro()
        snapshot.player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))
        return snapshot

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["sos_snapshot"] = self.get_bar_snapshot()

    def snapshot(self, node) -> SoSSpiritSpammerBarSnapshot:
        return node.blackboard.get("sos_snapshot") or SoSSpiritSpammerBarSnapshot()

    def build_rotation_tree(self) -> BehaviorTree:
        channeling = lambda: self.skills.Ritualist.ChannelingMagic
        anyskills = lambda: self.skills.Any
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "SoSSpiritSpammer",
            [
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
                cond("CloseToAggro", lambda node: self.snapshot(node).close_to_aggro),
            ],
            [
                guarded_cast(
                    self,
                    "SpiritSiphonEmergency",
                    equipped(Spirit_Siphon_ID),
                    lambda: channeling().Spirit_Siphon(max_self_energy_pct=0.30),
                ),
                guarded_cast(
                    self,
                    "EbonVanguardAssassinSupport",
                    equipped(Ebon_Vanguard_Assassin_Support_ID),
                    lambda: anyskills().PvE.Ebon_Vanguard_Assassin_Support(),
                ),
                guarded_cast(self, "Technobabble", equipped(Technobabble_ID), lambda: anyskills().PvE.Technobabble()),
                guarded_cast(
                    self,
                    "GreatDwarfWeapon",
                    equipped(Great_Dwarf_Weapon_ID),
                    lambda: anyskills().NoAttribute.Great_Dwarf_Weapon(),
                ),
                cast(self, "PainfulBond", lambda: channeling().Painful_Bond()),
                cast(self, "SignetOfSpirits", lambda: channeling().Signet_of_Spirits()),
                guarded_cast(self, "Vampirism", equipped(Vampirism_ID), lambda: anyskills().PvE.Vampirism()),
                cast(self, "Bloodsong", lambda: channeling().Bloodsong()),
                guarded_cast(
                    self, "ArmorOfUnfeeling", equipped(Armor_of_Unfeeling_ID), lambda: channeling().Armor_of_Unfeeling()
                ),
                cast(self, "SummonSpirits", lambda: anyskills().NoAttribute.Summon_Spirits()),
                guarded_cast(
                    self,
                    "SpiritSiphonOpportunistic",
                    equipped(Spirit_Siphon_ID),
                    lambda: channeling().Spirit_Siphon(max_self_energy_pct=0.70),
                ),
            ],
        )
