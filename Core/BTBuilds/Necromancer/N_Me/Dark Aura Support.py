"""BT port of Builds/Necromancer/N_Me/Dark Aura Support.py.

The mid-ladder `if not self.IsInAggro(): return False` becomes a nested
Sequence, so the four upkeep rungs stay ungated and the four combat rungs
remain aggro-gated.
"""

from __future__ import annotations

from Core import BldMgrBT, Profession, Routines
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cond, guarded_cast, rotation_tree, selector, sequence

DARK_AURA_ID = Skill.GetID("Dark_Aura")
MASOCHISM_ID = Skill.GetID("Masochism")
GREAT_DWARF_WEAPON_ID = Skill.GetID("Great_Dwarf_Weapon")
EVAS_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
FOUL_FEAST_ID = Skill.GetID("Foul_Feast")
TECHNOBABBLE_ID = Skill.GetID("Technobabble")
EXPEL_HEXES_ID = Skill.GetID("Expel_Hexes")
PUTRID_EXPLOSION_ID = Skill.GetID("Putrid_Explosion")
SOUL_TAKER_ID = Skill.GetID("Soul_Taker")


class Dark_Aura_Support(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Dark Aura Support",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Mesmer,
            template_code="OAVCUslEdwW4q4uYCYbpuzXA",
            required_skills=[
                DARK_AURA_ID,
                MASOCHISM_ID,
                GREAT_DWARF_WEAPON_ID,
                EVAS_ID,
                FOUL_FEAST_ID,
                TECHNOBABBLE_ID,
                EXPEL_HEXES_ID,
                PUTRID_EXPLOSION_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def build_rotation_tree(self) -> BehaviorTree:
        necro = lambda: self.skills.Necromancer
        return rotation_tree(
            "DarkAuraSupport",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "Masochism",
                    lambda: self.IsSkillEquipped(MASOCHISM_ID),
                    lambda: necro().SoulReaping.Masochism(),
                ),
                guarded_cast(
                    self,
                    "DarkAura",
                    lambda: self.IsSkillEquipped(DARK_AURA_ID),
                    lambda: necro().DeathMagic.Dark_Aura(
                        required_skill_id=SOUL_TAKER_ID,
                        other_ally=True,
                    ),
                ),
                guarded_cast(
                    self,
                    "FoulFeast",
                    lambda: self.IsSkillEquipped(FOUL_FEAST_ID),
                    lambda: necro().SoulReaping.Foul_Feast(),
                ),
                guarded_cast(
                    self,
                    "ExpelHexes",
                    lambda: self.IsSkillEquipped(EXPEL_HEXES_ID),
                    lambda: self.skills.Mesmer.NoAttribute.Expel_Hexes(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "GreatDwarfWeapon",
                            lambda: self.IsSkillEquipped(GREAT_DWARF_WEAPON_ID),
                            lambda: self.skills.Any.NoAttribute.Great_Dwarf_Weapon(),
                        ),
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            lambda: self.IsSkillEquipped(EVAS_ID),
                            lambda: self.skills.Any.PvE.Ebon_Vanguard_Assassin_Support(min_self_energy_pct=0.35),
                        ),
                        guarded_cast(
                            self,
                            "Technobabble",
                            lambda: self.IsSkillEquipped(TECHNOBABBLE_ID),
                            lambda: self.skills.Any.PvE.Technobabble(),
                        ),
                        guarded_cast(
                            self,
                            "PutridExplosion",
                            lambda: self.IsSkillEquipped(PUTRID_EXPLOSION_ID),
                            lambda: necro().DeathMagic.Putrid_Explosion(),
                        ),
                    ),
                ),
            ],
        )
