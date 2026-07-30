"""BT port of Builds/Mesmer/Me_Mo/Holy Inept.py.

Two behavioural notes, both flagged for review rather than silently carried:

- The legacy ladder has a mid-ladder `if not self.IsInAggro(): return False`
  after the Air of Superiority rung. That becomes a nested Sequence so the
  later rungs stay aggro-gated while Air of Superiority does not.
- Legacy `Signet_of_Clumsiness` ends in a bare `return` (None, i.e. falsy)
  rather than `return True`, so the build reported a failed tick after
  successfully casting it and fell through to the HeroAI fallback. Ported as a
  normal rung (SUCCESS). If that fall-through was intentional, say so and it
  can be restored.
"""

from Core import BldMgrBT
from Core import Profession
from Core import Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Ineptitude_ID = Skill.GetID("Ineptitude")
Judges_Insight_ID = Skill.GetID("Judges_Insight")
Wandering_Eye_ID = Skill.GetID("Wandering_Eye")
Arcane_Conundrum_ID = Skill.GetID("Arcane_Conundrum")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Signet_of_Clumsiness_ID = Skill.GetID("Signet_of_Clumsiness")
Power_Drain_ID = Skill.GetID("Power_Drain")


class HolyInept(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Holy Inept",
            required_primary=Profession.Mesmer,
            required_secondary=Profession.Monk,
            template_code="OQNDAcsuRvAIg5ZkA4i7iwlLEA",
            required_skills=[
                Ineptitude_ID,
                Wandering_Eye_ID,
                Arcane_Conundrum_ID,
                Judges_Insight_ID,
            ],
            optional_skills=[
                Air_of_Superiority_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Signet_of_Clumsiness_ID,
                Power_Drain_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def build_rotation_tree(self) -> BehaviorTree:
        mesmer = lambda: self.skills.Mesmer
        return rotation_tree(
            "HolyInept",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "AirOfSuperiority",
                    lambda: self.IsSkillEquipped(Air_of_Superiority_ID) and (self.IsInAggro() or self.IsCloseToAggro()),
                    lambda: self.skills.Any.PvE.Air_of_Superiority(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        cast(
                            self,
                            "PowerDrainLowEnergy",
                            lambda: mesmer().InspirationMagic.Power_Drain(energy_threshold_pct=0.30),
                        ),
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            lambda: self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID),
                            lambda: self.skills.Any.PvE.Ebon_Vanguard_Assassin_Support(),
                        ),
                        cast(self, "PowerDrain", lambda: mesmer().InspirationMagic.Power_Drain()),
                        cast(self, "Ineptitude", lambda: mesmer().IllusionMagic.Ineptitude()),
                        cast(self, "WanderingEye", lambda: mesmer().IllusionMagic.Wandering_Eye()),
                        guarded_cast(
                            self,
                            "ArcaneConundrum",
                            lambda: self.IsSkillEquipped(Arcane_Conundrum_ID),
                            lambda: mesmer().IllusionMagic.Arcane_Conundrum(),
                        ),
                        cast(self, "JudgesInsight", lambda: self.skills.Monk.SmitingPrayers.Judges_Insight()),
                        guarded_cast(
                            self,
                            "SignetOfClumsiness",
                            lambda: self.IsSkillEquipped(Signet_of_Clumsiness_ID),
                            lambda: mesmer().IllusionMagic.Signet_of_Clumsiness(),
                        ),
                        guarded_cast(
                            self,
                            "EbonBattleStandardOfWisdom",
                            lambda: self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID),
                            lambda: self.CastSkillID(
                                skill_id=Ebon_Battle_Standard_of_Wisdom_ID,
                                log=False,
                                aftercast_delay=250,
                            ),
                        ),
                    ),
                ),
            ],
        )
