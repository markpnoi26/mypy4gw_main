"""BT port of Builds/Necromancer/N_E/Pre_Searing_Necro.py.

`enemy_count` was computed once after the aggro gate and read by two rungs, so
it moves to the blackboard via a sampling node in that same position.
"""

from Core import BldMgrBT, GLOBAL_CACHE, Player, Profession, Range, Routines
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Aura_of_Restoration_ID = Skill.GetID("Aura_of_Restoration")
Fire_Storm_ID = Skill.GetID("Fire_Storm")
Flare_ID = Skill.GetID("Flare")
Deathly_Swarm_ID = Skill.GetID("Deathly_Swarm")
Animate_Bone_Horror_ID = Skill.GetID("Animate_Bone_Horror")
Resurrection_Signet_ID = Skill.GetID("Resurrection_Signet")


class Pre_Searing_Necro(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Pre-Searing Necro",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Elementalist,
            template_code="",
            required_skills=[
                Aura_of_Restoration_ID,
                Fire_Storm_ID,
                Flare_ID,
                Deathly_Swarm_ID,
                Animate_Bone_Horror_ID,
                Resurrection_Signet_ID,
            ],
            optional_skills=[],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))

    @staticmethod
    def get_nearest_exploitable_corpse(max_distance=Range.Spellcast.value):
        return Routines.Agents.GetNearestExploitableCorpse(
            max_distance,
            reserve=True,
            skill_id=Animate_Bone_Horror_ID,
        )

    def sample_enemy_count(self, node) -> bool:
        player_x, player_y = Player.GetXY()
        nearby_enemies = Routines.Agents.GetFilteredEnemyArray(player_x, player_y, max_distance=Range.Spellcast.value)
        node.blackboard["pre_searing_necro_enemy_count"] = len(nearby_enemies)
        return True

    def enemy_count(self, node) -> int:
        return int(node.blackboard.get("pre_searing_necro_enemy_count", 0))

    def resurrection_signet(self):
        dead_ally_id = Routines.Agents.GetResurrectionTarget(
            max_distance=Range.Spellcast.value,
            reserve=True,
            skill_id=Resurrection_Signet_ID,
        )
        if not dead_ally_id:
            return False
        return (
            yield from self.CastSkillID(
                skill_id=Resurrection_Signet_ID,
                target_agent_id=dead_ally_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def animate_bone_horror(self):
        corpse_id = self.get_nearest_exploitable_corpse(max_distance=Range.Spellcast.value)
        if not corpse_id:
            return False
        return (
            yield from self.CastSkillID(
                skill_id=Animate_Bone_Horror_ID,
                target_agent_id=corpse_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def fire_storm(self):
        clustered_enemy_id = Routines.Targeting.TargetClusteredEnemy(area=Range.Spellcast.value)
        if not clustered_enemy_id:
            return False
        return (
            yield from self.CastSkillID(
                skill_id=Fire_Storm_ID,
                target_agent_id=clustered_enemy_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def cast_at_nearest_enemy(self, skill_id: int):
        nearest_enemy_id = Routines.Agents.GetNearestEnemy(max_distance=Range.Spellcast.value)
        if not nearest_enemy_id:
            return False
        return (
            yield from self.CastSkillID(
                skill_id=skill_id,
                target_agent_id=nearest_enemy_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def build_rotation_tree(self) -> BehaviorTree:
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "PreSearingNecro",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self, "ResurrectionSignet", equipped(Resurrection_Signet_ID), lambda: self.resurrection_signet()
                ),
                guarded_cast(
                    self,
                    "AuraOfRestoration",
                    lambda: self.IsSkillEquipped(Aura_of_Restoration_ID)
                    and not Routines.Checks.Agents.HasEffect(Player.GetAgentID(), Aura_of_Restoration_ID),
                    lambda: self.CastSkillID(skill_id=Aura_of_Restoration_ID, log=False, aftercast_delay=250),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    cond("SampleEnemyCount", lambda node: self.sample_enemy_count(node)),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "AnimateBoneHorror",
                            equipped(Animate_Bone_Horror_ID),
                            lambda: self.animate_bone_horror(),
                        ),
                        guarded_cast(
                            self,
                            "FireStorm",
                            lambda node: self.IsSkillEquipped(Fire_Storm_ID) and self.enemy_count(node) >= 3,
                            lambda: self.fire_storm(),
                        ),
                        guarded_cast(
                            self,
                            "DeathlySwarm",
                            lambda node: self.IsSkillEquipped(Deathly_Swarm_ID) and self.enemy_count(node) >= 2,
                            lambda: self.cast_at_nearest_enemy(Deathly_Swarm_ID),
                        ),
                        guarded_cast(self, "Flare", equipped(Flare_ID), lambda: self.cast_at_nearest_enemy(Flare_ID)),
                        cast(self, "AutoAttack", lambda: self.AutoAttack()),
                    ),
                ),
            ],
        )
