"""BT port of Builds/Ranger/R_W/Pre_Searing_Ignite.py.

The preparation rung is an if/elif in the legacy ladder — preparations do not
stack, so Read the Wind only runs when Ignite Arrows is absent from the bar.
That exclusivity is preserved in the Read the Wind guard.
"""

from Core import Agent, BldMgrBT, Party, Player, Profession, Range, Routines
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Troll_Unguent_ID = Skill.GetID("Troll_Unguent")
Ignite_Arrows_ID = Skill.GetID("Ignite_Arrows")
Frenzy_ID = Skill.GetID("Frenzy")
Comfort_Animal_ID = Skill.GetID("Comfort_Animal")
Charm_animal_ID = Skill.GetID("Charm_Animal")
Resurrection_Signet_ID = Skill.GetID("Resurrection_Signet")
Read_the_Wind_ID = Skill.GetID("Read_the_Wind")


class Pre_Searing_Ignite(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Pre-Searing ignite",
            required_primary=Profession.Ranger,
            required_secondary=Profession.Warrior,
            template_code="OgEUYlrh5cG++1aFAAAA0WAA",
            required_skills=[
                Troll_Unguent_ID,
                Resurrection_Signet_ID,
                Comfort_Animal_ID,
                Charm_animal_ID,
            ],
            optional_skills=[Frenzy_ID, Read_the_Wind_ID, Ignite_Arrows_ID],
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

    def pet_needs_comfort(self) -> bool:
        pet_id = Party.Pets.GetPetID(Player.GetAgentID())
        if not pet_id:
            return False
        return not Agent.IsAlive(pet_id) or Agent.GetHealth(pet_id) < 0.01

    def should_cast_troll_unguent(self) -> bool:
        player_agent_id = Player.GetAgentID()
        return (
            Agent.GetHealth(player_agent_id) < 0.99
            and not Routines.Checks.Agents.HasEffect(player_agent_id, Troll_Unguent_ID)
            and not Routines.Checks.Agents.HasEffect(player_agent_id, Frenzy_ID)
        )

    def should_cast_frenzy(self) -> bool:
        player_agent_id = Player.GetAgentID()
        return Agent.GetHealth(player_agent_id) > 0.95 and not Routines.Checks.Agents.HasEffect(
            player_agent_id, Frenzy_ID
        )

    def build_rotation_tree(self) -> BehaviorTree:
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        simple_cast = lambda skill_id: (lambda: self.CastSkillID(skill_id=skill_id, log=False, aftercast_delay=250))
        return rotation_tree(
            "PreSearingIgnite",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self, "ResurrectionSignet", equipped(Resurrection_Signet_ID), lambda: self.resurrection_signet()
                ),
                guarded_cast(
                    self,
                    "ComfortAnimal",
                    lambda: self.IsSkillEquipped(Comfort_Animal_ID) and self.pet_needs_comfort(),
                    simple_cast(Comfort_Animal_ID),
                ),
                guarded_cast(
                    self,
                    "TrollUnguent",
                    lambda: self.IsSkillEquipped(Troll_Unguent_ID) and self.should_cast_troll_unguent(),
                    simple_cast(Troll_Unguent_ID),
                ),
                sequence(
                    "InAggroRotation",
                    cond("InAggro", lambda: self.IsInAggro()),
                    selector(
                        "AggroRungs",
                        guarded_cast(
                            self,
                            "IgniteArrows",
                            lambda: self.IsSkillEquipped(Ignite_Arrows_ID)
                            and not Routines.Checks.Agents.HasEffect(Player.GetAgentID(), Ignite_Arrows_ID),
                            simple_cast(Ignite_Arrows_ID),
                        ),
                        guarded_cast(
                            self,
                            "ReadTheWind",
                            lambda: not self.IsSkillEquipped(Ignite_Arrows_ID)
                            and self.IsSkillEquipped(Read_the_Wind_ID)
                            and not Routines.Checks.Agents.HasEffect(Player.GetAgentID(), Read_the_Wind_ID),
                            simple_cast(Read_the_Wind_ID),
                        ),
                        guarded_cast(
                            self,
                            "Frenzy",
                            lambda: self.IsSkillEquipped(Frenzy_ID) and self.should_cast_frenzy(),
                            simple_cast(Frenzy_ID),
                        ),
                        cast(self, "AutoAttack", lambda: self.AutoAttack(target_type="EnemyClustered")),
                    ),
                ),
            ],
        )
