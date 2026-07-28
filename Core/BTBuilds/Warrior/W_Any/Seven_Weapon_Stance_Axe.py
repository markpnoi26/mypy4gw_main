"""BT port of Builds/Warrior/W_Any/Seven_Weapon_Stance_Axe.py."""

from Core import BldMgrBT
from Core import GLOBAL_CACHE
from Core import Player
from Core import Profession
from Core import Routines
from Core.Builds.Any.HeroAI import HeroAI_Build
from Core.Builds.Skills import SkillsTemplate
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from ...nodes import cast, cond, guarded_cast, rotation_tree

Cyclone_Axe_ID = Skill.GetID("Cyclone_Axe")
Whirlwind_Attack_ID = Skill.GetID("Whirlwind_Attack")
Executioners_Strike_ID = Skill.GetID("Executioners_Strike")
Seven_Weapon_Stance_ID = Skill.GetID("Seven_Weapon_Stance")
Endure_Pain_ID = Skill.GetID("Endure_Pain")
Antidote_Signet_ID = Skill.GetID("Antidote_Signet")
Blind_ID = Skill.GetID("Blind")


class Seven_Weapon_Stance_Axe(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Seven Weapon Stance Axe",
            required_primary=Profession.Warrior,
            template_code="OQITEZJZVSpYHEqQsGAAAAAAAAA",
            required_skills=[
                Cyclone_Axe_ID,
                Whirlwind_Attack_ID,
                Seven_Weapon_Stance_ID,
            ],
            optional_skills=[
                Executioners_Strike_ID,
                Endure_Pain_ID,
                Antidote_Signet_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def build_rotation_tree(self) -> BehaviorTree:
        warrior = lambda: self.skills.Warrior
        return rotation_tree(
            "SevenWeaponStanceAxe",
            [
                cond("InAggro", lambda: self.IsInAggro()),
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
            ],
            [
                cast(self, "SevenWeaponStance", lambda: warrior().Strength.Seven_Weapon_Stance()),
                guarded_cast(
                    self,
                    "AntidoteSignet",
                    lambda: self.IsSkillEquipped(Antidote_Signet_ID),
                    lambda: self.CastSkillID(
                        Antidote_Signet_ID,
                        extra_condition=lambda: GLOBAL_CACHE.Effects.HasEffect(Player.GetAgentID(), Blind_ID),
                        aftercast_delay=100,
                    ),
                ),
                guarded_cast(
                    self,
                    "EndurePain",
                    lambda: self.IsSkillEquipped(Endure_Pain_ID),
                    lambda: warrior().Strength.Endure_Pain(),
                ),
                cast(self, "ExecutionersStrike", lambda: warrior().AxeMastery.Executioners_Strike()),
                cast(self, "CycloneAxe", lambda: warrior().AxeMastery.Cyclone_Axe()),
                cast(self, "WhirlwindAttack", lambda: warrior().NoAttribute.Whirlwind_Attack()),
            ],
        )
