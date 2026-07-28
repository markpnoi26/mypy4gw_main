"""BT port of Builds/Necromancer/N_Any/Necro_Prot.py.

`UpdatePartyHealthMonitor` ran unconditionally before the first rung — the
reactive prots read `GetPartyHealthDelta` off it — so it becomes an
always-SUCCESS gate node in that same position.
"""

from Core import BldMgrBT, Profession, Range, Routines
from Core.Agent import Agent
from Core.Builds.Skills import SkillsTemplate
from Core.Player import Player
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree

SIGNET_OF_LOST_SOULS_ID = Skill.GetID("Signet_of_Lost_Souls")
SPIRIT_BOND_ID = Skill.GetID("Spirit_Bond")
PROTECTIVE_SPIRIT_ID = Skill.GetID("Protective_Spirit")
REVERSE_HEX_ID = Skill.GetID("Reverse_Hex")

MARTYR_ID = Skill.GetID("Martyr")
REVERSAL_OF_FORTUNE_ID = Skill.GetID("Reversal_of_Fortune")
SHIELD_OF_ABSORPTION_ID = Skill.GetID("Shield_of_Absorption")
DRAW_CONDITIONS_ID = Skill.GetID("Draw_Conditions")
AURA_OF_FAITH_ID = Skill.GetID("Aura_of_Faith")
LIFE_SHEATH_ID = Skill.GetID("Life_Sheath")
DIVERT_HEXES_ID = Skill.GetID("Divert_Hexes")

SIGNET_ENERGY_CEILING = 0.60
MARTYR_CONDITION_ALLY_THRESHOLD = 3


class Necro_Prot(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Necro Prot",
            required_primary=Profession.Necromancer,
            required_secondary=Profession.Monk,
            template_code="OANCY5vjKpqKAmJQt7qWseA",
            required_skills=[
                SIGNET_OF_LOST_SOULS_ID,
                SPIRIT_BOND_ID,
                PROTECTIVE_SPIRIT_ID,
                REVERSE_HEX_ID,
            ],
            optional_skills=[
                MARTYR_ID,
                REVERSAL_OF_FORTUNE_ID,
                SHIELD_OF_ABSORPTION_ID,
                DRAW_CONDITIONS_ID,
                AURA_OF_FAITH_ID,
                LIFE_SHEATH_ID,
                DIVERT_HEXES_ID,
            ],
        )
        if match_only:
            return

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def sample_party_health(self) -> bool:
        self.UpdatePartyHealthMonitor(sample_interval_ms=150)
        return True

    def martyr(self):
        """Cleanse the party once at least three other allies carry a condition,
        targeting the lowest-HP conditioned ally."""
        if not self.IsInAggro():
            return False

        player_pos = Player.GetXY()
        allies = Routines.Agents.GetFilteredAllyArray(
            player_pos[0],
            player_pos[1],
            Range.Spellcast.value,
            other_ally=True,
        )
        conditioned = [ally_id for ally_id in (allies or []) if Agent.IsAlive(ally_id) and Agent.IsConditioned(ally_id)]
        if len(conditioned) < MARTYR_CONDITION_ALLY_THRESHOLD:
            return False

        target_agent_id = min(conditioned, key=lambda ally_id: Agent.GetHealth(ally_id))

        return (
            yield from self.CastSkillIDAndRestoreTarget(
                skill_id=MARTYR_ID,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    def build_rotation_tree(self) -> BehaviorTree:
        prot = lambda: self.skills.Monk.ProtectionPrayers
        equipped = lambda skill_id: (lambda: self.IsSkillEquipped(skill_id))
        return rotation_tree(
            "NecroProt",
            [
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
                cond("SamplePartyHealth", lambda: self.sample_party_health()),
            ],
            [
                guarded_cast(self, "Martyr", equipped(MARTYR_ID), lambda: self.martyr()),
                cast(
                    self,
                    "SignetOfLostSouls",
                    lambda: self.skills.Necromancer.SoulReaping.Signet_of_Lost_Souls(
                        max_self_energy_pct=SIGNET_ENERGY_CEILING
                    ),
                ),
                guarded_cast(
                    self,
                    "DrawConditions",
                    lambda: self.IsSkillEquipped(DRAW_CONDITIONS_ID) and self.IsInAggro(),
                    lambda: prot().Draw_Conditions(),
                ),
                cast(self, "SpiritBond", lambda: prot().Spirit_Bond()),
                cast(self, "ProtectiveSpirit", lambda: prot().Protective_Spirit(prebuff_melee_precombat=True)),
                guarded_cast(
                    self, "ShieldOfAbsorption", equipped(SHIELD_OF_ABSORPTION_ID), lambda: prot().Shield_of_Absorption()
                ),
                guarded_cast(self, "AuraOfFaith", equipped(AURA_OF_FAITH_ID), lambda: prot().Aura_of_Faith()),
                guarded_cast(
                    self, "LifeSheath", equipped(LIFE_SHEATH_ID), lambda: prot().Life_Sheath(min_conditions=2)
                ),
                guarded_cast(self, "DivertHexes", equipped(DIVERT_HEXES_ID), lambda: prot().Divert_Hexes(min_hexes=2)),
                cast(self, "ReverseHex", lambda: prot().Reverse_Hex()),
                guarded_cast(
                    self, "ReversalOfFortune", equipped(REVERSAL_OF_FORTUNE_ID), lambda: prot().Reversal_of_Fortune()
                ),
            ],
        )
