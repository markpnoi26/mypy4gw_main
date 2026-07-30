"""BT port of Builds/Mesmer/Me_Any/Energy Surge.py.

The bar snapshot was computed once per ladder pass and read by nine rungs, so
it moves to seed_blackboard — one evaluation per tick, same as before.
"""

from dataclasses import dataclass

from Core import Agent, BldMgrBT, Player, Profession, Range, Routines
from Core.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Energy_Surge_ID = Skill.GetID("Energy_Surge")
Mistrust_ID = Skill.GetID("Mistrust")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Cry_of_Pain_ID = Skill.GetID("Cry_of_Pain")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Overload_ID = Skill.GetID("Overload")
Power_Drain_ID = Skill.GetID("Power_Drain")
Shatter_Hex_ID = Skill.GetID("Shatter_Hex")
Flesh_of_My_Flesh_ID = Skill.GetID("Flesh_of_My_Flesh")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")


@dataclass(slots=True)
class EnergySurgeBarSnapshot:
    in_aggro: bool = False
    enemy_in_spellcast: bool = False
    enemy_casting: bool = False
    enemy_casting_spell: bool = False
    enemy_casting_spell_or_chant: bool = False
    player_energy_pct: float = 1.0


class Energy_Surge(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Energy Surge",
            required_primary=Profession.Mesmer,
            template_code="OQBCAswEc5Jw0zuoNopTOggD",
            required_skills=[
                Air_of_Superiority_ID,
                Energy_Surge_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Cry_of_Frustration_ID,
            ],
            optional_skills=[
                Mistrust_ID,
                Cry_of_Pain_ID,
                Unnatural_Signet_ID,
                Power_Drain_ID,
                Shatter_Hex_ID,
                Overload_ID,
                Flesh_of_My_Flesh_ID,
                Breath_of_the_Great_Dwarf_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.SetBlockedSkills(
            [
                Air_of_Superiority_ID,
                Energy_Surge_ID,
                Mistrust_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Cry_of_Pain_ID,
                Unnatural_Signet_ID,
                Cry_of_Frustration_ID,
                Overload_ID,
                Power_Drain_ID,
                Shatter_Hex_ID,
            ]
        )
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def get_bar_snapshot(self) -> EnergySurgeBarSnapshot:
        snapshot = EnergySurgeBarSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())
        snapshot.player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))

        if not snapshot.in_aggro:
            return snapshot

        snapshot.enemy_in_spellcast = bool(Routines.Agents.GetNearestEnemy(Range.Spellcast.value))
        if snapshot.enemy_in_spellcast:
            snapshot.enemy_casting = bool(Routines.Targeting.GetEnemyCasting(Range.Spellcast.value))
            snapshot.enemy_casting_spell = bool(Routines.Targeting.GetEnemyCastingSpell(Range.Spellcast.value))
            snapshot.enemy_casting_spell_or_chant = bool(
                Routines.Targeting.GetEnemyCastingSpellOrChant(Range.Spellcast.value)
            )

        return snapshot

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["energy_surge_snapshot"] = self.get_bar_snapshot()

    def snapshot(self, node) -> EnergySurgeBarSnapshot:
        return node.blackboard.get("energy_surge_snapshot") or EnergySurgeBarSnapshot()

    def flesh_of_my_flesh(self):
        dead_ally_id = Routines.Agents.GetDeadAlly(Range.Spellcast.value) or 0
        if not dead_ally_id:
            return False
        return (
            yield from self.CastSkillIDAndRestoreTarget(
                skill_id=Flesh_of_My_Flesh_ID,
                target_agent_id=dead_ally_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def build_rotation_tree(self) -> BehaviorTree:
        domination = lambda: self.skills.Mesmer.DominationMagic
        inspiration = lambda: self.skills.Mesmer.InspirationMagic
        pve = lambda: self.skills.Any.PvE
        return rotation_tree(
            "EnergySurge",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "AirOfSuperiority",
                    lambda node: self.snapshot(node).in_aggro or self.IsCloseToAggro(),
                    lambda: pve().Air_of_Superiority(),
                ),
                cast(self, "BreathOfTheGreatDwarf", lambda: self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf()),
                guarded_cast(
                    self,
                    "FleshOfMyFlesh",
                    lambda: self.IsSkillEquipped(Flesh_of_My_Flesh_ID),
                    lambda: self.flesh_of_my_flesh(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda node: self.snapshot(node).in_aggro),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "PowerDrainLowEnergy",
                            lambda node: self.snapshot(node).enemy_casting_spell_or_chant,
                            lambda: inspiration().Power_Drain(energy_threshold_pct=0.30),
                        ),
                        cast(
                            self,
                            "ShatterHexHigh",
                            lambda: domination().Shatter_Hex(min_priority=HexRemovalPriority.HIGH),
                        ),
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            lambda node: self.snapshot(node).enemy_in_spellcast,
                            lambda: pve().Ebon_Vanguard_Assassin_Support(),
                        ),
                        guarded_cast(
                            self,
                            "EnergySurge",
                            lambda node: self.snapshot(node).enemy_in_spellcast,
                            lambda: domination().Energy_Surge(),
                        ),
                        guarded_cast(
                            self,
                            "CryOfFrustration",
                            lambda node: self.snapshot(node).enemy_casting,
                            lambda: domination().Cry_of_Frustration(),
                        ),
                        guarded_cast(
                            self,
                            "PowerDrain",
                            lambda node: self.snapshot(node).enemy_casting_spell_or_chant,
                            lambda: inspiration().Power_Drain(),
                        ),
                        guarded_cast(
                            self,
                            "ShatterHexMedium",
                            lambda node: self.snapshot(node).player_energy_pct >= 0.50,
                            lambda: domination().Shatter_Hex(min_priority=HexRemovalPriority.MEDIUM),
                        ),
                        guarded_cast(
                            self,
                            "Mistrust",
                            lambda node: self.snapshot(node).enemy_casting_spell,
                            lambda: domination().Mistrust(),
                        ),
                        guarded_cast(
                            self,
                            "Overload",
                            lambda node: self.snapshot(node).enemy_casting,
                            lambda: domination().Overload(),
                        ),
                        guarded_cast(
                            self,
                            "CryOfPainHexed",
                            lambda node: self.snapshot(node).enemy_casting,
                            lambda: pve().Cry_of_Pain(require_mesmer_hex=True),
                        ),
                        guarded_cast(
                            self,
                            "UnnaturalSignet",
                            lambda node: self.snapshot(node).enemy_in_spellcast,
                            lambda: domination().Unnatural_Signet(),
                        ),
                        guarded_cast(
                            self,
                            "CryOfPain",
                            lambda node: self.snapshot(node).enemy_in_spellcast,
                            lambda: pve().Cry_of_Pain(),
                        ),
                        guarded_cast(
                            self,
                            "ShatterHex",
                            lambda node: self.snapshot(node).player_energy_pct >= 0.70,
                            lambda: domination().Shatter_Hex(),
                        ),
                    ),
                ),
            ],
        )
