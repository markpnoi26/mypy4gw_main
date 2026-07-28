"""BT port of Builds/Necromancer/N_Rt/Blood is Power Healer.py.

The longest ladder in the set (25 rungs across two tiers). Rung order is the
whole build here — emergency heal tiers descend by threshold, and several
skills appear more than once at different thresholds — so the Selector keeps
the original sequence verbatim, including the duplicate Recuperation and
Signet_of_Lost_Souls entries.
"""

from Core import BldMgrBT
from Core import Profession
from Core import Routines
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Blood_is_Power_ID = Skill.GetID("Blood_is_Power")
Signet_of_Lost_Souls_ID = Skill.GetID("Signet_of_Lost_Souls")
Mend_Body_and_Soul_ID = Skill.GetID("Mend_Body_and_Soul")
Spirit_Light_ID = Skill.GetID("Spirit_Light")
Protective_Was_Kaolai_ID = Skill.GetID("Protective_Was_Kaolai")
Vital_Weapon_ID = Skill.GetID("Vital_Weapon")
Wielders_Boon_ID = Skill.GetID("Wielders_Boon")
Mending_Grip_ID = Skill.GetID("Mending_Grip")
Spirit_Transfer_ID = Skill.GetID("Spirit_Transfer")
Life_ID = Skill.GetID("Life")
You_Are_All_Weaklings_ID = Skill.GetID("You_Are_All_Weaklings")
Enfeebling_Blood_ID = Skill.GetID("Enfeebling_Blood")
Recovery_ID = Skill.GetID("Recovery")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Recuperation_ID = Skill.GetID("Recuperation")
Blood_Bond_ID = Skill.GetID("Blood_Bond")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")


class Blood_is_Power_Healer(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Blood is Power Healer",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Ritualist,
            template_code="OAhjQkGZIP3hqq0EAAAAAAAAAA",
            required_skills=[
                Blood_is_Power_ID,
                Signet_of_Lost_Souls_ID,
                Mend_Body_and_Soul_ID,
            ],
            optional_skills=[
                Spirit_Light_ID,
                Protective_Was_Kaolai_ID,
                Vital_Weapon_ID,
                Wielders_Boon_ID,
                Mending_Grip_ID,
                Spirit_Transfer_ID,
                Life_ID,
                You_Are_All_Weaklings_ID,
                Enfeebling_Blood_ID,
                Recovery_ID,
                Breath_of_the_Great_Dwarf_ID,
                Recuperation_ID,
                Blood_Bond_ID,
                Ebon_Vanguard_Assassin_Support_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def build_rotation_tree(self) -> BehaviorTree:
        resto = lambda: self.skills.Ritualist.RestorationMagic
        necro = lambda: self.skills.Necromancer
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "BloodIsPowerHealer",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                cast(self, "SpiritTransferEmergency", lambda: resto().Spirit_Transfer(health_threshold=0.30)),
                cast(self, "SpiritLightEmergency", lambda: resto().Spirit_Light(health_threshold=0.30)),
                cast(self, "MendBodyAndSoul40", lambda: resto().Mend_Body_and_Soul(health_threshold=0.40)),
                guarded_cast(
                    self,
                    "DropHeldBundle",
                    equipped(Protective_Was_Kaolai_ID),
                    lambda: self.skills.Ritualist.NoAttribute.Drop_Held_Bundle(health_threshold=0.75),
                ),
                cast(
                    self,
                    "SignetOfLostSouls30",
                    lambda: necro().SoulReaping.Signet_of_Lost_Souls(max_self_energy_pct=0.30),
                ),
                guarded_cast(
                    self,
                    "RecuperationDamaged6a",
                    equipped(Recuperation_ID),
                    lambda: resto().Recuperation(min_party_damaged_count=6),
                ),
                guarded_cast(
                    self,
                    "RecuperationDegen6",
                    equipped(Recuperation_ID),
                    lambda: resto().Recuperation(min_degen_count=6),
                ),
                cast(self, "BloodIsPower", lambda: necro().BloodMagic.Blood_is_Power()),
                guarded_cast(self, "WieldersBoon", equipped(Wielders_Boon_ID), lambda: resto().Wielders_Boon()),
                guarded_cast(self, "MendingGrip", equipped(Mending_Grip_ID), lambda: resto().Mending_Grip()),
                guarded_cast(self, "SpiritTransfer", equipped(Spirit_Transfer_ID), lambda: resto().Spirit_Transfer()),
                cast(
                    self, "MendBodyAndSoulCleanseBlind", lambda: resto().Mend_Body_and_Soul(cleanse_blind_martial=True)
                ),
                cast(
                    self,
                    "MendBodyAndSoulCleanseCripple",
                    lambda: resto().Mend_Body_and_Soul(cleanse_cripple_melee=True),
                ),
                guarded_cast(
                    self,
                    "RecuperationDamaged6b",
                    equipped(Recuperation_ID),
                    lambda: resto().Recuperation(min_party_damaged_count=6),
                ),
                guarded_cast(
                    self,
                    "RecuperationDegen4",
                    equipped(Recuperation_ID),
                    lambda: resto().Recuperation(min_degen_count=4),
                ),
                guarded_cast(
                    self,
                    "EbonVanguardAssassinSupport",
                    equipped(Ebon_Vanguard_Assassin_Support_ID),
                    lambda: self.skills.Any.PvE.Ebon_Vanguard_Assassin_Support(min_self_energy_pct=0.40),
                ),
                cast(
                    self,
                    "SignetOfLostSouls60",
                    lambda: necro().SoulReaping.Signet_of_Lost_Souls(max_self_energy_pct=0.60),
                ),
                guarded_cast(self, "BloodBond", equipped(Blood_Bond_ID), lambda: necro().BloodMagic.Blood_Bond()),
                guarded_cast(self, "Life", equipped(Life_ID), lambda: resto().Life()),
                cast(self, "MendBodyAndSoul75", lambda: resto().Mend_Body_and_Soul(health_threshold=0.75)),
                guarded_cast(
                    self,
                    "ProtectiveWasKaolai",
                    equipped(Protective_Was_Kaolai_ID),
                    lambda: resto().Protective_Was_Kaolai(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "VitalWeapon",
                            equipped(Vital_Weapon_ID),
                            lambda: self.skills.Ritualist.Communing.Vital_Weapon(),
                        ),
                        guarded_cast(self, "Recovery", equipped(Recovery_ID), lambda: resto().Recovery()),
                        cast(self, "SignetOfLostSouls", lambda: necro().SoulReaping.Signet_of_Lost_Souls()),
                        guarded_cast(
                            self,
                            "RecuperationDamaged6c",
                            equipped(Recuperation_ID),
                            lambda: resto().Recuperation(min_party_damaged_count=6),
                        ),
                        guarded_cast(
                            self,
                            "RecuperationDegen2",
                            equipped(Recuperation_ID),
                            lambda: resto().Recuperation(min_degen_count=2),
                        ),
                        cast(self, "MendBodyAndSoul85", lambda: resto().Mend_Body_and_Soul(health_threshold=0.85)),
                        guarded_cast(
                            self,
                            "BreathOfTheGreatDwarf",
                            equipped(Breath_of_the_Great_Dwarf_ID),
                            lambda: self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf(),
                        ),
                        guarded_cast(
                            self,
                            "YouAreAllWeaklings",
                            equipped(You_Are_All_Weaklings_ID),
                            lambda: self.skills.Any.NoAttribute.You_Are_All_Weaklings(),
                        ),
                        guarded_cast(
                            self,
                            "EnfeeblingBlood",
                            equipped(Enfeebling_Blood_ID),
                            lambda: necro().Curses.Enfeebling_Blood(),
                        ),
                        cast(self, "SpiritLight", lambda: resto().Spirit_Light()),
                    ),
                ),
            ],
        )
