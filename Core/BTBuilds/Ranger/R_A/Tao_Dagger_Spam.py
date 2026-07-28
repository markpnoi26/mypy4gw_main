"""BT port of Builds/Ranger/R_A/Tao_Dagger_Spam.py.

Four rungs in the legacy ladder end in a bare `return` (None, falsy) instead of
`return True` — I_Am_the_Strongest, Comfort_Animal, Air_of_Superiority and
Lightning_Reflexes. That made the build report a failed tick after successfully
casting, falling through to the HeroAI fallback in the same frame. Ported as
normal rungs (SUCCESS). Flagged for review: if that fall-through was
deliberate, these four need `optional(...)` wrappers instead.
"""

from Core import Agent, Party, Player
from Core import BldMgrBT
from Core import Profession
from Core import Routines
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree, selector, sequence

Jagged_Strike_ID = Skill.GetID("Jagged_Strike")
Fox_Fangs_ID = Skill.GetID("Fox_Fangs")
Death_Blossom_ID = Skill.GetID("Death_Blossom")
Together_as_one_ID = Skill.GetID("Together_as_one")
Breath_of_the_Great_Dwarf_ID = Skill.GetID("Breath_of_the_Great_Dwarf")
Air_of_Superiority_ID = Skill.GetID("Air_of_Superiority")
Comfort_Animal_ID = Skill.GetID("Comfort_Animal")
I_Am_the_Strongest_ID = Skill.GetID("I_Am_the_Strongest")
Lightning_Reflexes_ID = Skill.GetID("Lightning_Reflexes")


def should_cast_comfort_animal() -> bool:
    pet_id = Party.Pets.GetPetID(Player.GetAgentID())
    if not pet_id:
        return False
    if not Agent.IsAlive(pet_id):
        return True
    return Agent.GetHealth(pet_id) < 0.30


class Tao_Dagger_Spam(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="TaO Dagger Spam",
            required_primary=Profession.Ranger,
            required_secondary=Profession.Assassin,
            template_code="OgcTYr72Xyhhh5gZsGAAAAAAAAA",
            required_skills=[
                Jagged_Strike_ID,
                Fox_Fangs_ID,
                Death_Blossom_ID,
                Together_as_one_ID,
            ],
            optional_skills=[
                Breath_of_the_Great_Dwarf_ID,
                Comfort_Animal_ID,
                I_Am_the_Strongest_ID,
                Lightning_Reflexes_ID,
                Air_of_Superiority_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)
        self.dagger_target_type = "EnemyNearest"

    def build_rotation_tree(self) -> BehaviorTree:
        daggers = lambda: self.skills.Assassin.DaggerMastery
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "TaoDaggerSpam",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                guarded_cast(
                    self,
                    "IAmTheStrongest",
                    equipped(I_Am_the_Strongest_ID),
                    lambda: self.CastSkillID(skill_id=I_Am_the_Strongest_ID, log=False, aftercast_delay=250),
                ),
                guarded_cast(
                    self,
                    "ComfortAnimal",
                    lambda: self.IsSkillEquipped(Comfort_Animal_ID) and should_cast_comfort_animal(),
                    lambda: self.CastSkillID(
                        skill_id=Comfort_Animal_ID,
                        extra_condition=should_cast_comfort_animal,
                        log=False,
                        aftercast_delay=250,
                    ),
                ),
                guarded_cast(
                    self,
                    "BreathOfTheGreatDwarf",
                    equipped(Breath_of_the_Great_Dwarf_ID),
                    lambda: self.skills.Any.NoAttribute.Breath_of_the_Great_Dwarf(),
                ),
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
                        guarded_cast(
                            self,
                            "LightningReflexes",
                            equipped(Lightning_Reflexes_ID),
                            lambda: self.CastSkillID(skill_id=Lightning_Reflexes_ID, log=False, aftercast_delay=250),
                        ),
                        cast(self, "TogetherAsOne", lambda: self.skills.Ranger.Expertise.Together_as_One()),
                        cast(self, "DeathBlossom", lambda: daggers().Death_Blossom()),
                        cast(self, "FoxFangs", lambda: daggers().Fox_Fangs()),
                        cast(self, "JaggedStrike", lambda: daggers().Jagged_Strike()),
                    ),
                ),
            ],
        )
