"""BT port of Builds/Monk/Mo_Any/Ray of Judgment.py.

`arcane_echo_active` gates twelve rungs and was evaluated once per pass, so it
moves to seed_blackboard. `_last_ray_of_judgment_target_id` and
`_last_ray_of_judgment_cast_ts_ms` keep their underscore names — the shared
skill layer writes them onto the build (Builds/Skills/Monk/SmitingPrayers.py).
The YMLAD chain rung records its own timestamp only on a successful cast,
matching the legacy side effect.
"""

import time

from Core import BldMgrBT
from Core import GLOBAL_CACHE
from Core import Profession
from Core import Routines
from Core.Agent import Agent
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Builds.Skills import HexRemovalPriority, SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Ray_of_Judgment_ID = Skill.GetID("Ray_of_Judgment")
Smite_Hex_ID = Skill.GetID("Smite_Hex")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Castigation_Signet_ID = Skill.GetID("Castigation_Signet")
Arcane_Echo_ID = Skill.GetID("Arcane_Echo")
You_Move_Like_a_Dwarf_ID = Skill.GetID("You_Move_Like_a_Dwarf")
Smite_Condition_ID = Skill.GetID("Smite_Condition")
Ebon_Battle_Standard_of_Wisdom_ID = Skill.GetID("Ebon_Battle_Standard_of_Wisdom")
Ebon_Vanguard_Assassin_Support_ID = Skill.GetID("Ebon_Vanguard_Assassin_Support")
Smiters_Boon_ID = Skill.GetID("Smiters_Boon")
Reversal_of_Damage_ID = Skill.GetID("Reversal_of_Damage")
Technobabble_ID = Skill.GetID("Technobabble")


class Ray_of_Judgment(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Ray of Judgment",
            required_primary=Profession.Monk,
            template_code="OwAS4YIT+MuEWfAAAAAAAAwl",
            required_skills=[
                Ray_of_Judgment_ID,
                Smite_Hex_ID,
                Air_of_Superiority_ID,
                Castigation_Signet_ID,
            ],
            optional_skills=[
                Arcane_Echo_ID,
                You_Move_Like_a_Dwarf_ID,
                Smite_Condition_ID,
                Ebon_Battle_Standard_of_Wisdom_ID,
                Ebon_Vanguard_Assassin_Support_ID,
                Smiters_Boon_ID,
                Reversal_of_Damage_ID,
                Technobabble_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self.last_ymlad_chain_ts_ms = 0.0

    def seed_blackboard(self, blackboard: dict) -> None:
        player_id = Player.GetAgentID()
        blackboard["roj_arcane_echo_active"] = bool(Routines.Checks.Agents.HasEffect(player_id, Arcane_Echo_ID))
        blackboard["roj_player_energy_pct"] = float(Agent.GetEnergy(player_id))

    def echo_active(self, node) -> bool:
        return bool(node.blackboard.get("roj_arcane_echo_active", False))

    def energy_pct(self, node) -> float:
        return float(node.blackboard.get("roj_player_energy_pct", 0.0))

    def should_seed_arcane_echo(self) -> bool:
        roj_slot = GLOBAL_CACHE.SkillBar.GetSlotBySkillID(Ray_of_Judgment_ID)
        roj_is_ready = roj_slot != 0 and Routines.Checks.Skills.IsSkillSlotReady(roj_slot)
        player_id = Player.GetAgentID()
        player_energy_abs = Agent.GetEnergy(player_id) * Agent.GetMaxEnergy(player_id)
        return self.IsSkillEquipped(Arcane_Echo_ID) and roj_is_ready and player_energy_abs > 23

    def roj_chain_ready(self) -> bool:
        roj_cast_ts_ms = getattr(self, "_last_ray_of_judgment_cast_ts_ms", 0.0)
        now_ms = time.monotonic() * 1000.0
        return roj_cast_ts_ms > self.last_ymlad_chain_ts_ms and (now_ms - roj_cast_ts_ms) <= 2000.0

    def ymlad_chain(self):
        now_ms = time.monotonic() * 1000.0
        fired = yield from self.skills.Any.NoAttribute.You_Move_Like_a_Dwarf()
        if fired:
            self.last_ymlad_chain_ts_ms = now_ms
        return fired

    def ray_of_judgment_echo_copy(self):
        last_target_id = getattr(self, "_last_ray_of_judgment_target_id", 0)
        return (yield from self.skills.Monk.SmitingPrayers.Ray_of_Judgment(exclude_target_id=last_target_id))

    def build_rotation_tree(self) -> BehaviorTree:
        smiting = lambda: self.skills.Monk.SmitingPrayers
        anyskills = lambda: self.skills.Any
        no_echo = lambda node: not self.echo_active(node)
        return rotation_tree(
            "RayOfJudgment",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "AirOfSuperiority",
                    lambda node: no_echo(node)
                    and self.IsSkillEquipped(Air_of_Superiority_ID)
                    and (self.IsInAggro() or self.IsCloseToAggro()),
                    lambda: anyskills().PvE.Air_of_Superiority(),
                ),
                guarded_cast(
                    self,
                    "SeedArcaneEcho",
                    lambda: self.should_seed_arcane_echo(),
                    lambda: self.skills.Mesmer.NoAttribute.Arcane_Echo(),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "RayOfJudgmentSeedEcho",
                            lambda node: self.echo_active(node),
                            lambda: smiting().Ray_of_Judgment(),
                        ),
                        guarded_cast(
                            self,
                            "SmiteHexHigh",
                            no_echo,
                            lambda: smiting().Smite_Hex(min_priority=HexRemovalPriority.HIGH),
                        ),
                        guarded_cast(
                            self,
                            "RayOfJudgmentFallback",
                            lambda: not self.IsSkillEquipped(Arcane_Echo_ID),
                            lambda: smiting().Ray_of_Judgment(),
                        ),
                        guarded_cast(
                            self,
                            "SmitersBoon",
                            lambda node: no_echo(node) and self.IsSkillEquipped(Smiters_Boon_ID),
                            lambda: smiting().Smiters_Boon(),
                        ),
                        guarded_cast(
                            self,
                            "RayOfJudgmentEchoCopy",
                            lambda: self.IsSkillEquipped(Arcane_Echo_ID),
                            lambda: self.ray_of_judgment_echo_copy(),
                        ),
                        guarded_cast(
                            self,
                            "YouMoveLikeADwarfChain",
                            lambda node: no_echo(node)
                            and self.roj_chain_ready()
                            and self.IsSkillEquipped(You_Move_Like_a_Dwarf_ID),
                            lambda: self.ymlad_chain(),
                        ),
                        guarded_cast(
                            self,
                            "SmiteHexMedium",
                            lambda node: no_echo(node) and self.energy_pct(node) >= 0.50,
                            lambda: smiting().Smite_Hex(min_priority=HexRemovalPriority.MEDIUM),
                        ),
                        guarded_cast(
                            self,
                            "CastigationSignet",
                            no_echo,
                            lambda: smiting().Castigation_Signet(),
                        ),
                        guarded_cast(
                            self,
                            "ReversalOfDamage",
                            lambda node: no_echo(node) and self.IsSkillEquipped(Reversal_of_Damage_ID),
                            lambda: smiting().Reversal_of_Damage(),
                        ),
                        guarded_cast(
                            self,
                            "YouMoveLikeADwarf",
                            lambda node: no_echo(node) and self.IsSkillEquipped(You_Move_Like_a_Dwarf_ID),
                            lambda: anyskills().NoAttribute.You_Move_Like_a_Dwarf(),
                        ),
                        guarded_cast(
                            self,
                            "SmiteCondition",
                            lambda node: no_echo(node) and self.IsSkillEquipped(Smite_Condition_ID),
                            lambda: smiting().Smite_Condition(),
                        ),
                        guarded_cast(
                            self,
                            "EbonVanguardAssassinSupport",
                            lambda node: no_echo(node) and self.IsSkillEquipped(Ebon_Vanguard_Assassin_Support_ID),
                            lambda: anyskills().PvE.Ebon_Vanguard_Assassin_Support(),
                        ),
                        guarded_cast(
                            self,
                            "Technobabble",
                            lambda node: no_echo(node) and self.IsSkillEquipped(Technobabble_ID),
                            lambda: anyskills().PvE.Technobabble(),
                        ),
                        guarded_cast(
                            self,
                            "EbonBattleStandardOfWisdom",
                            lambda node: no_echo(node) and self.IsSkillEquipped(Ebon_Battle_Standard_of_Wisdom_ID),
                            lambda: anyskills().NoAttribute.Ebon_Battle_Standard_of_Wisdom(),
                        ),
                        guarded_cast(
                            self,
                            "SmiteHexLow",
                            lambda node: no_echo(node) and self.energy_pct(node) >= 0.70,
                            lambda: smiting().Smite_Hex(),
                        ),
                    ),
                ),
            ],
        )
