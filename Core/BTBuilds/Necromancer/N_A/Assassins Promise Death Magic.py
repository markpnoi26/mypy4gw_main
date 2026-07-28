"""BT port of Builds/Necromancer/N_A/Assassin's Promise Death Magic.py.

Straight ladder, no mid-ladder gates. Rung order is load-bearing here (the
original comments explain why: Masochism before the burst, Rising Bile first
for its 20s timer, EBSoH before any damage, AP anchor before the spike) so the
Selector preserves it exactly.
"""

from Core import BldMgrBT
from Core import Profession
from Core import Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree

Putrid_Bile_ID = Skill.GetID("Putrid_Bile")
Assassins_Promise_ID = Skill.GetID("Assassins_Promise")
Putrid_Explosion_ID = Skill.GetID("Putrid_Explosion")

Rising_Bile_ID = Skill.GetID("Rising_Bile")
Masochism_ID = Skill.GetID("Masochism")
Ebon_Battle_Standard_of_Honor_ID = Skill.GetID("Ebon_Battle_Standard_of_Honor")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
You_Move_Like_a_Dwarf_ID = Skill.GetID("You_Move_Like_a_Dwarf")
Finish_Him_ID = Skill.GetID("Finish_Him")
Technobabble_ID = Skill.GetID("Technobabble")
Great_Dwarf_Weapon_ID = Skill.GetID("Great_Dwarf_Weapon")


class Assassins_Promise_Death_Magic(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Assassin's Promise Death Magic",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Assassin,
            template_code="OAdDQsNHTKgLQfBAAAAAAAAAAA",
            required_skills=[
                Putrid_Bile_ID,
                Assassins_Promise_ID,
                Putrid_Explosion_ID,
            ],
            optional_skills=[
                Rising_Bile_ID,
                Masochism_ID,
                Ebon_Battle_Standard_of_Honor_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                You_Move_Like_a_Dwarf_ID,
                Finish_Him_ID,
                Technobabble_ID,
                Great_Dwarf_Weapon_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def build_rotation_tree(self) -> BehaviorTree:
        necro = lambda: self.skills.Necromancer
        anyskills = lambda: self.skills.Any
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "AssassinsPromiseDeathMagic",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(self, "Masochism", equipped(Masochism_ID), lambda: necro().SoulReaping.Masochism()),
                guarded_cast(self, "RisingBile", equipped(Rising_Bile_ID), lambda: necro().DeathMagic.Rising_Bile()),
                guarded_cast(
                    self,
                    "EbonBattleStandardOfHonor",
                    equipped(Ebon_Battle_Standard_of_Honor_ID),
                    lambda: anyskills().NoAttribute.Ebon_Battle_Standard_of_Honor(),
                ),
                cast(self, "AssassinsPromise", lambda: self.skills.Assassin.DeadlyArts.Assassins_Promise()),
                guarded_cast(
                    self,
                    "EbonVanguardAssassinSupport",
                    equipped(Ebon_Vanguard_Assassin_Support_ID),
                    lambda: anyskills().PvE.Ebon_Vanguard_Assassin_Support(),
                ),
                cast(self, "PutridBile", lambda: necro().DeathMagic.Putrid_Bile()),
                guarded_cast(
                    self,
                    "YouMoveLikeADwarf",
                    equipped(You_Move_Like_a_Dwarf_ID),
                    lambda: anyskills().NoAttribute.You_Move_Like_a_Dwarf(),
                ),
                guarded_cast(self, "FinishHim", equipped(Finish_Him_ID), lambda: anyskills().NoAttribute.Finish_Him()),
                guarded_cast(self, "Technobabble", equipped(Technobabble_ID), lambda: anyskills().PvE.Technobabble()),
                cast(self, "PutridExplosion", lambda: necro().DeathMagic.Putrid_Explosion()),
                guarded_cast(
                    self,
                    "GreatDwarfWeapon",
                    equipped(Great_Dwarf_Weapon_ID),
                    lambda: anyskills().NoAttribute.Great_Dwarf_Weapon(),
                ),
            ],
        )
