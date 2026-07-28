"""BT port of Builds/Paragon/P_W/Defensive Refrain.py.

Legacy ran `yield from self.AutoAttack()` unconditionally right after the
CanCast gate, discarding its result, and then again as the final rung. Both
positions are preserved: the leading one as an always-SUCCESS gate, the
trailing one as the last rung.
"""

from Core import BldMgrBT
from Core import Profession
from Core import Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Heroic_Refrain_ID = Skill.GetID("Heroic_Refrain")
Theyre_on_Fire_ID = Skill.GetID("Theyre_on_Fire")
Hasty_Refrain_ID = Skill.GetID("Hasty_Refrain")
Aggressive_Refrain_ID = Skill.GetID("Aggressive_Refrain")
Stand_Your_Ground_ID = Skill.GetID("Stand_Your_Ground")
For_Great_Justice_ID = Skill.GetID("For_Great_Justice")
Theres_Nothing_to_Fear_ID = Skill.GetID("Theres_Nothing_to_Fear")
Save_Yourselves_luxon_ID = Skill.GetID("Save_Yourselves_luxon")
Save_Yourselves_kurzick_ID = Skill.GetID("Save_Yourselves_kurzick")
Never_Surrender_ID = Skill.GetID("Never_Surrender")
Blazing_Finale_ID = Skill.GetID("Blazing_Finale")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Protectors_Defense_ID = Skill.GetID("Protectors_Defense")
Cant_Touch_This_ID = Skill.GetID("Cant_Touch_This")
Make_Your_Time_ID = Skill.GetID("Make_Your_Time")
Angelic_Protection_ID = Skill.GetID("Angelic_Protection")


class Paragon_Refrain(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Defensive Refrain",
            required_primary=Profession.Paragon,
            required_secondary=Profession.Warrior,
            template_code="OQGkUNlnpiy0ZNQYPWNm72G4VhoH",
            required_skills=[
                Heroic_Refrain_ID,
                Theyre_on_Fire_ID,
                Theres_Nothing_to_Fear_ID,
            ],
            optional_skills=[
                Save_Yourselves_luxon_ID,
                Save_Yourselves_kurzick_ID,
                Hasty_Refrain_ID,
                Never_Surrender_ID,
                Aggressive_Refrain_ID,
                Stand_Your_Ground_ID,
                For_Great_Justice_ID,
                Blazing_Finale_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                Protectors_Defense_ID,
                Cant_Touch_This_ID,
                Make_Your_Time_ID,
                Angelic_Protection_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def opening_auto_attack(self) -> bool:
        self.drive("ParagonOpeningAutoAttack", lambda: self.AutoAttack())
        return True

    def build_rotation_tree(self) -> BehaviorTree:
        paragon = lambda: self.skills.Paragon
        anyskills = lambda: self.skills.Any
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "DefensiveRefrain",
            [
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
                cond("OpeningAutoAttack", lambda: self.opening_auto_attack()),
            ],
            [
                guarded_cast(
                    self, "HeroicRefrain", equipped(Heroic_Refrain_ID), lambda: paragon().Leadership.Heroic_Refrain()
                ),
                guarded_cast(
                    self, "TheyreOnFire", equipped(Theyre_on_Fire_ID), lambda: paragon().Leadership.Theyre_on_Fire()
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "AngelicProtection",
                            equipped(Angelic_Protection_ID),
                            lambda: paragon().Leadership.Angelic_Protection(health_threshold=0.30),
                        ),
                        guarded_cast(
                            self,
                            "TheresNothingToFear",
                            equipped(Theres_Nothing_to_Fear_ID),
                            lambda: anyskills().NoAttribute.Theres_Nothing_to_Fear(),
                        ),
                        guarded_cast(
                            self,
                            "AggressiveRefrain",
                            equipped(Aggressive_Refrain_ID),
                            lambda: paragon().Leadership.Aggressive_Refrain(),
                        ),
                        guarded_cast(
                            self,
                            "ForGreatJustice",
                            equipped(For_Great_Justice_ID),
                            lambda: self.skills.Warrior.NoAttribute.For_Great_Justice(),
                        ),
                        guarded_cast(
                            self,
                            "MakeYourTime",
                            equipped(Make_Your_Time_ID),
                            lambda: paragon().Leadership.Make_Your_Time(),
                        ),
                        guarded_cast(
                            self,
                            "SaveYourselvesLuxon",
                            equipped(Save_Yourselves_luxon_ID),
                            lambda: anyskills().NoAttribute.Save_Yourselves_luxon(),
                        ),
                        guarded_cast(
                            self,
                            "SaveYourselvesKurzick",
                            equipped(Save_Yourselves_kurzick_ID),
                            lambda: anyskills().NoAttribute.Save_Yourselves_kurzick(),
                        ),
                        guarded_cast(
                            self,
                            "StandYourGround",
                            equipped(Stand_Your_Ground_ID),
                            lambda: paragon().Command.Stand_Your_Ground(),
                        ),
                        guarded_cast(
                            self,
                            "CantTouchThis",
                            equipped(Cant_Touch_This_ID),
                            lambda: paragon().Command.Cant_Touch_This(),
                        ),
                        guarded_cast(
                            self,
                            "HastyRefrain",
                            equipped(Hasty_Refrain_ID),
                            lambda: paragon().Motivation.Hasty_Refrain(),
                        ),
                        guarded_cast(
                            self,
                            "NeverSurrender",
                            equipped(Never_Surrender_ID),
                            lambda: paragon().Motivation.Never_Surrender(),
                        ),
                        guarded_cast(
                            self,
                            "BlazingFinale",
                            equipped(Blazing_Finale_ID),
                            lambda: paragon().Motivation.Blazing_Finale(),
                        ),
                        guarded_cast(
                            self,
                            "ProtectorsDefense",
                            equipped(Protectors_Defense_ID),
                            lambda: self.skills.Warrior.NoAttribute.Protectors_Defense(),
                        ),
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            equipped(Ebon_Vanguard_Assassin_Support_ID),
                            lambda: anyskills().PvE.Ebon_Vanguard_Assassin_Support(),
                        ),
                        guarded_cast(
                            self,
                            "EbonBattleStandardOfWisdom",
                            equipped(Ebon_Battle_Standard_of_Wisdom_ID),
                            lambda: anyskills().NoAttribute.Ebon_Battle_Standard_of_Wisdom(),
                        ),
                        cast(self, "AutoAttack", lambda: self.AutoAttack()),
                    ),
                ),
            ],
        )
