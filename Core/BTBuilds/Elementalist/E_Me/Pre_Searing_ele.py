"""BT port of Builds/Elementalist/E_Me/Pre_Searing_ele.py.

No SkillsTemplate — this build casts through CastSkillID directly, so the
guarded rungs keep their inline eligibility checks.
"""

from Core import Agent, BldMgrBT, Player, Profession, Range, Routines
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Aura_of_Restoration_ID = Skill.GetID("Aura_of_Restoration")
Fire_Storm_ID = Skill.GetID("Fire_Storm")
Flare_ID = Skill.GetID("Flare")
Ether_Feast_ID = Skill.GetID("Ether_Feast")
Resurrection_Signet_ID = Skill.GetID("Resurrection_Signet")


class Pre_Searing_ele(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Pre-Searing ele",
            required_primary=Profession.Elementalist,
            required_secondary=Profession.Mesmer,
            template_code="",
            required_skills=[
                Aura_of_Restoration_ID,
                Fire_Storm_ID,
                Flare_ID,
                Ether_Feast_ID,
                Resurrection_Signet_ID,
            ],
            optional_skills=[],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))

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

    def should_cast_ether_feast(self) -> bool:
        player_agent_id = Player.GetAgentID()
        target_id = Player.GetTargetID()
        _, target_allegiance = Agent.GetAllegiance(target_id)
        target_is_enemy = (
            target_id != 0 and Agent.IsValid(target_id) and not Agent.IsDead(target_id) and target_allegiance == "Enemy"
        )
        return Agent.GetHealth(player_agent_id) < 0.65 and target_is_enemy

    def build_rotation_tree(self) -> BehaviorTree:
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        simple_cast = lambda skill_id: (lambda: self.CastSkillID(skill_id=skill_id, log=False, aftercast_delay=250))
        return rotation_tree(
            "PreSearingEle",
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
                    simple_cast(Aura_of_Restoration_ID),
                ),
                guarded_cast(
                    self,
                    "EtherFeast",
                    lambda: self.IsSkillEquipped(Ether_Feast_ID) and self.should_cast_ether_feast(),
                    simple_cast(Ether_Feast_ID),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(self, "FireStorm", equipped(Fire_Storm_ID), simple_cast(Fire_Storm_ID)),
                        guarded_cast(self, "Flare", equipped(Flare_ID), simple_cast(Flare_ID)),
                        cast(self, "AutoAttack", lambda: self.AutoAttack()),
                    ),
                ),
            ],
        )
