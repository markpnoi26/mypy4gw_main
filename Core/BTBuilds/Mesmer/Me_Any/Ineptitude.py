"""BT port of Builds/Mesmer/Me_Any/Ineptitude.py.

The two commented-out rungs in the legacy ladder (a duplicate Cry_of_Pain and
a duplicate Signet_of_Clumsiness) are not carried over.
"""

from dataclasses import dataclass

from Core import BldMgrBT, Profession, Range, Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Ineptitude_ID = Skill.GetID("Ineptitude")
Wandering_Eye_ID = Skill.GetID("Wandering_Eye")
Signet_of_Clumsiness_ID = Skill.GetID("Signet_of_Clumsiness")
Arcane_Conundrum_ID = Skill.GetID("Arcane_Conundrum")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Power_Drain_ID = Skill.GetID("Power_Drain")
Drain_Enchantment_ID = Skill.GetID("Drain_Enchantment")
Cry_of_Pain_ID = Skill.GetID("Cry_of_Pain")


@dataclass(slots=True)
class IneptitudeBarSnapshot:
    in_aggro: bool = False
    enemy_in_spellcast: bool = False
    enemy_casting: bool = False


class Ineptitude(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Ineptitude",
            required_primary=Profession.Mesmer,
            template_code="OQBDAawDSvAIg5ZkAAAAAAAAAA",
            required_skills=[
                Ineptitude_ID,
                Wandering_Eye_ID,
                Signet_of_Clumsiness_ID,
            ],
            optional_skills=[
                Arcane_Conundrum_ID,
                Air_of_Superiority_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                Power_Drain_ID,
                Drain_Enchantment_ID,
                Cry_of_Pain_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def get_bar_snapshot(self) -> IneptitudeBarSnapshot:
        snapshot = IneptitudeBarSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())

        if not snapshot.in_aggro:
            return snapshot

        snapshot.enemy_in_spellcast = bool(Routines.Agents.GetNearestEnemy(Range.Spellcast.value))
        if snapshot.enemy_in_spellcast:
            snapshot.enemy_casting = bool(Routines.Targeting.GetEnemyCasting(Range.Spellcast.value))

        return snapshot

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["ineptitude_snapshot"] = self.get_bar_snapshot()

    def snapshot(self, node) -> IneptitudeBarSnapshot:
        return node.blackboard.get("ineptitude_snapshot") or IneptitudeBarSnapshot()

    def build_rotation_tree(self) -> BehaviorTree:
        illusion = lambda: self.skills.Mesmer.IllusionMagic
        inspiration = lambda: self.skills.Mesmer.InspirationMagic
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "Ineptitude",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "AirOfSuperiority",
                    lambda node: self.IsSkillEquipped(Air_of_Superiority_ID)
                    and (self.snapshot(node).in_aggro or self.IsCloseToAggro()),
                    lambda: self.skills.Any.PvE.Air_of_Superiority(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda node: self.snapshot(node).in_aggro),
                    selector(
                        "AggroRungs",
                        cast(self, "PowerDrain30", lambda: inspiration().Power_Drain(energy_threshold_pct=0.30)),
                        cast(
                            self,
                            "DrainEnchantment30",
                            lambda: inspiration().Drain_Enchantment(energy_threshold_pct=0.30),
                        ),
                        cast(self, "SignetOfClumsiness", lambda: illusion().Signet_of_Clumsiness()),
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            equipped(Ebon_Vanguard_Assassin_Support_ID),
                            lambda: self.skills.Any.PvE.Ebon_Vanguard_Assassin_Support(),
                        ),
                        cast(self, "Ineptitude", lambda: illusion().Ineptitude()),
                        cast(self, "PowerDrain50", lambda: inspiration().Power_Drain(energy_threshold_pct=0.50)),
                        cast(
                            self,
                            "DrainEnchantment50",
                            lambda: inspiration().Drain_Enchantment(energy_threshold_pct=0.50),
                        ),
                        cast(self, "WanderingEye", lambda: illusion().Wandering_Eye()),
                        guarded_cast(
                            self,
                            "CryOfPain",
                            lambda node: self.snapshot(node).enemy_casting,
                            lambda: self.skills.Any.PvE.Cry_of_Pain(),
                        ),
                        guarded_cast(
                            self,
                            "ArcaneConundrum",
                            equipped(Arcane_Conundrum_ID),
                            lambda: illusion().Arcane_Conundrum(),
                        ),
                        cast(self, "PowerDrain", lambda: inspiration().Power_Drain()),
                        cast(self, "DrainEnchantment", lambda: inspiration().Drain_Enchantment()),
                        guarded_cast(
                            self,
                            "EbonBattleStandardOfWisdom",
                            equipped(Ebon_Battle_Standard_of_Wisdom_ID),
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
