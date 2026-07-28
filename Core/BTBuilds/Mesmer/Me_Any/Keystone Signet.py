"""BT port of Builds/Mesmer/Me_Any/Keystone Signet.py.

Note: the legacy snapshot never populates `enemy_casting` — it is declared on
the dataclass and defaults to False, so the leading Cry_of_Frustration rung can
never fire. Ported as-is (the rung is present but its guard reads the same
always-False field) rather than silently "fixing" it. Worth a decision on
review: either populate `enemy_casting` in the snapshot or drop the rung.
"""

from dataclasses import dataclass

from Core import BldMgrBT
from Core import Profession
from Core import Range
from Core import Routines
from Core.Agent import Agent
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Symbolic_Celerity_ID = Skill.GetID("Symbolic_Celerity")
Keystone_Signet_ID = Skill.GetID("Keystone_Signet")
Unnatural_Signet_ID = Skill.GetID("Unnatural_Signet")
Signet_of_Clumsiness_ID = Skill.GetID("Signet_of_Clumsiness")
Smite_Hex_ID = Skill.GetID("Smite_Hex")
Hex_Eater_Signet_ID = Skill.GetID("Hex_Eater_Signet")
Castigation_Signet_ID = Skill.GetID("Castigation_Signet")
Bane_Signet_ID = Skill.GetID("Bane_Signet")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Blood_Ritual_ID = Skill.GetID("Blood_Ritual")
Animate_Bone_Fiend_ID = Skill.GetID("Animate_Bone_Fiend")
Animate_Bone_Horror_ID = Skill.GetID("Animate_Bone_Horror")
Animate_Bone_Minions_ID = Skill.GetID("Animate_Bone_Minions")
Animate_Flesh_Golem_ID = Skill.GetID("Animate_Flesh_Golem")
Animate_Shambling_Horror_ID = Skill.GetID("Animate_Shambling_Horror")
Animate_Vampiric_Horror_ID = Skill.GetID("Animate_Vampiric_Horror")
Death_Nova_ID = Skill.GetID("Death_Nova")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Tryptophan_Signet_ID = Skill.GetID("Tryptophan_Signet")


@dataclass(slots=True)
class KeystoneBarSnapshot:
    has_symbolic_celerity: bool = False
    has_keystone_signet: bool = False
    enemy_casting: bool = False
    enemy_in_spellcast: bool = False
    attacking_enemy_in_spellcast: bool = False

    @property
    def symbolic_celerity_needed(self) -> bool:
        return not self.has_symbolic_celerity

    @property
    def keystone_signet_needed(self) -> bool:
        return self.has_symbolic_celerity and not self.has_keystone_signet


class KeystoneSignet(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Keystone Signet",
            required_primary=Profession.Mesmer,
            template_code="OQITEZJZVSpYHEqQsGAAAAAAAAA",
            required_skills=[
                Symbolic_Celerity_ID,
                Keystone_Signet_ID,
                Unnatural_Signet_ID,
                Signet_of_Clumsiness_ID,
            ],
            optional_skills=[
                Smite_Hex_ID,
                Hex_Eater_Signet_ID,
                Castigation_Signet_ID,
                Bane_Signet_ID,
                Breath_of_the_Great_Dwarf_ID,
                Blood_Ritual_ID,
                Animate_Bone_Fiend_ID,
                Animate_Bone_Horror_ID,
                Animate_Bone_Minions_ID,
                Animate_Flesh_Golem_ID,
                Animate_Shambling_Horror_ID,
                Animate_Vampiric_Horror_ID,
                Death_Nova_ID,
                Cry_of_Frustration_ID,
                Tryptophan_Signet_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def get_bar_snapshot(self) -> KeystoneBarSnapshot:
        player_id = Player.GetAgentID()
        snapshot = KeystoneBarSnapshot(
            has_symbolic_celerity=Routines.Checks.Effects.HasBuff(player_id, Symbolic_Celerity_ID),
            has_keystone_signet=Routines.Checks.Effects.HasBuff(player_id, Keystone_Signet_ID),
        )

        if not self.IsInAggro():
            return snapshot

        snapshot.enemy_in_spellcast = bool(Routines.Agents.GetNearestEnemy(Range.Spellcast.value))
        snapshot.attacking_enemy_in_spellcast = bool(Routines.Targeting.GetEnemyAttacking(Range.Spellcast.value))
        return snapshot

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["keystone_snapshot"] = self.get_bar_snapshot()
        blackboard["keystone_energy_pct"] = float(Agent.GetEnergy(Player.GetAgentID()))

    def snapshot(self, node) -> KeystoneBarSnapshot:
        return node.blackboard.get("keystone_snapshot") or KeystoneBarSnapshot()

    def energy_pct(self, node) -> float:
        return float(node.blackboard.get("keystone_energy_pct", 0.0))

    def build_rotation_tree(self) -> BehaviorTree:
        death = lambda: self.skills.Necromancer.DeathMagic
        smiting = lambda: self.skills.Monk.SmitingPrayers
        fast_casting = lambda: self.skills.Mesmer.FastCasting
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "KeystoneSignet",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "CryOfFrustration",
                    lambda node: self.snapshot(node).enemy_casting,
                    lambda: self.skills.Mesmer.DominationMagic.Cry_of_Frustration(),
                ),
                guarded_cast(
                    self, "AnimateFleshGolem", equipped(Animate_Flesh_Golem_ID), lambda: death().Animate_Flesh_Golem()
                ),
                guarded_cast(
                    self, "AnimateBoneFiend", equipped(Animate_Bone_Fiend_ID), lambda: death().Animate_Bone_Fiend()
                ),
                guarded_cast(
                    self, "AnimateBoneHorror", equipped(Animate_Bone_Horror_ID), lambda: death().Animate_Bone_Horror()
                ),
                guarded_cast(
                    self,
                    "AnimateBoneMinions",
                    equipped(Animate_Bone_Minions_ID),
                    lambda: death().Animate_Bone_Minions(),
                ),
                guarded_cast(
                    self,
                    "AnimateShamblingHorror",
                    equipped(Animate_Shambling_Horror_ID),
                    lambda: death().Animate_Shambling_Horror(),
                ),
                guarded_cast(
                    self,
                    "AnimateVampiricHorror",
                    equipped(Animate_Vampiric_Horror_ID),
                    lambda: death().Animate_Vampiric_Horror(),
                ),
                cast(self, "SmiteHexHigh", lambda: smiting().Smite_Hex(min_priority=HexRemovalPriority.HIGH)),
                guarded_cast(
                    self,
                    "SymbolicCelerity",
                    lambda node: self.snapshot(node).symbolic_celerity_needed,
                    lambda: fast_casting().Symbolic_Celerity(),
                ),
                guarded_cast(
                    self,
                    "HexEaterSignet",
                    equipped(Hex_Eater_Signet_ID),
                    lambda: self.skills.Mesmer.InspirationMagic.Hex_Eater_Signet(),
                ),
                guarded_cast(
                    self,
                    "BreathOfTheGreatDwarf",
                    equipped(Breath_of_the_Great_Dwarf_ID),
                    lambda: self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf(),
                ),
                guarded_cast(
                    self,
                    "BloodRitual",
                    equipped(Blood_Ritual_ID),
                    lambda: self.skills.Necromancer.BloodMagic.Blood_Ritual(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(self, "DeathNova", equipped(Death_Nova_ID), lambda: death().Death_Nova()),
                        guarded_cast(
                            self,
                            "KeystoneSignet",
                            lambda node: self.snapshot(node).keystone_signet_needed,
                            lambda: fast_casting().Keystone_Signet(),
                        ),
                        guarded_cast(
                            self,
                            "TryptophanSignet",
                            equipped(Tryptophan_Signet_ID),
                            lambda: self.skills.Any.PvE.Tryptophan_Signet(),
                        ),
                        guarded_cast(
                            self,
                            "SmiteHexMedium",
                            lambda node: self.energy_pct(node) >= 0.50,
                            lambda: smiting().Smite_Hex(min_priority=HexRemovalPriority.MEDIUM),
                        ),
                        guarded_cast(
                            self,
                            "UnnaturalSignet",
                            lambda node: self.snapshot(node).enemy_in_spellcast,
                            lambda: self.skills.Mesmer.DominationMagic.Unnatural_Signet(),
                        ),
                        guarded_cast(
                            self,
                            "SignetOfClumsiness",
                            lambda node: self.snapshot(node).attacking_enemy_in_spellcast,
                            lambda: self.skills.Mesmer.IllusionMagic.Signet_of_Clumsiness(),
                        ),
                        guarded_cast(
                            self,
                            "CastigationSignet",
                            lambda node: self.IsSkillEquipped(Castigation_Signet_ID)
                            and self.snapshot(node).attacking_enemy_in_spellcast,
                            lambda: smiting().Castigation_Signet(),
                        ),
                        guarded_cast(
                            self,
                            "BaneSignet",
                            lambda node: self.IsSkillEquipped(Bane_Signet_ID)
                            and self.snapshot(node).attacking_enemy_in_spellcast,
                            lambda: smiting().Bane_Signet(),
                        ),
                    ),
                ),
            ],
        )
