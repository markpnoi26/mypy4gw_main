"""BT port of Builds/Elementalist/E_Mo/Ether Renewal Prot Infuser.py.

`UpdatePartyHealthMonitor` was a bare side-effect statement between the aggro
gate and the combat rungs. It becomes an always-SUCCESS node in the same
position so the monitor still samples once per pass before Infuse Health reads
`GetPartyHealthDelta`.
"""

from Core import BldMgrBT
from Core import Profession
from Core import Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Aura_of_Restoration_ID = Skill.GetID("Aura_of_Restoration")
Ether_Renewal_ID = Skill.GetID("Ether_Renewal")
Protective_Spirit_ID = Skill.GetID("Protective_Spirit")
Reversal_of_Fortune_ID = Skill.GetID("Reversal_of_Fortune")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Great_Dwarf_Weapon_ID = Skill.GetID("Great_Dwarf_Weapon")
Vital_Blessing_ID = Skill.GetID("Vital_Blessing")
Infuse_Health_ID = Skill.GetID("Infuse_Health")


class Ether_Renewal_Prot_Infuser(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Ether Renewal Prot Infuser",
            required_primary=Profession.Elementalist,
            required_secondary=Profession.Monk,
            template_code="OgNDwaTPHzse1iWAAAAAAA",
            required_skills=[
                Aura_of_Restoration_ID,
                Ether_Renewal_ID,
                Protective_Spirit_ID,
                Reversal_of_Fortune_ID,
            ],
            optional_skills=[
                Breath_of_the_Great_Dwarf_ID,
                Great_Dwarf_Weapon_ID,
                Vital_Blessing_ID,
                Infuse_Health_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def vital_blessing_self_upkeep(self):
        not_has_vital_blessing = lambda: not Routines.Checks.Effects.HasBuff(Player.GetAgentID(), Vital_Blessing_ID)

        if not self.IsSkillEquipped(Vital_Blessing_ID):
            return False
        if not not_has_vital_blessing():
            return False

        return (
            yield from self.CastSkillID(
                skill_id=Vital_Blessing_ID,
                extra_condition=not_has_vital_blessing,
                log=False,
                aftercast_delay=250,
                target_agent_id=Player.GetAgentID(),
            )
        )

    def sample_party_health(self) -> bool:
        self.UpdatePartyHealthMonitor(sample_interval_ms=150)
        return True

    def build_rotation_tree(self) -> BehaviorTree:
        monk = lambda: self.skills.Monk
        return rotation_tree(
            "EtherRenewalProtInfuser",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                cast(self, "AuraOfRestoration", lambda: self.skills.Elementalist.EnergyStorage.Aura_of_Restoration()),
                guarded_cast(
                    self,
                    "VitalBlessing",
                    lambda: self.IsSkillEquipped(Vital_Blessing_ID),
                    lambda: self.vital_blessing_self_upkeep(),
                ),
                guarded_cast(
                    self,
                    "BreathOfTheGreatDwarf",
                    lambda: self.IsSkillEquipped(Breath_of_the_Great_Dwarf_ID),
                    lambda: self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    cond("SamplePartyHealth", lambda: self.sample_party_health()),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "InfuseHealth",
                            lambda: self.IsSkillEquipped(Infuse_Health_ID),
                            lambda: monk().HealingPrayers.Infuse_Health(),
                        ),
                        guarded_cast(
                            self,
                            "GreatDwarfWeapon",
                            lambda: self.IsSkillEquipped(Great_Dwarf_Weapon_ID),
                            lambda: self.skills.Any.NoAttribute.Great_Dwarf_Weapon(),
                        ),
                        cast(self, "ProtectiveSpirit", lambda: monk().ProtectionPrayers.Protective_Spirit()),
                        cast(self, "ReversalOfFortune", lambda: monk().ProtectionPrayers.Reversal_of_Fortune()),
                        cast(self, "EtherRenewal", lambda: self.skills.Elementalist.EnergyStorage.Ether_Renewal()),
                    ),
                ),
            ],
        )
