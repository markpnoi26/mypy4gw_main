from __future__ import annotations

from typing import TYPE_CHECKING

from Core.build_src.combat_services import BuildCoroutine
from Core import Range, Routines, Utils
from Core.Agent import Agent
from Core.Player import Player
from Core.Skill import Skill

if TYPE_CHECKING:
    from Core.build_src.combat_services import CombatServices

__all__ = ["ShadowArts"]


class ShadowArts:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build

    # region D
    def Deaths_Charge(self, *, min_distance: float = 500.0) -> BuildCoroutine:
        """Shadow Step to the most clustered foe, if it is far enough away.

        Targets the enemy with the most surrounding foes (within "nearby"
        range) among enemies in spellcast range, then only fires when that
        chosen enemy is farther than ``min_distance`` from the player. This
        keeps Death's Charge as a gap-closer into a pack rather than a wasted
        step onto an adjacent foe.
        """
        deaths_charge_id: int = Skill.GetID("Deaths_Charge")

        if not self.build.IsSkillEquipped(deaths_charge_id):
            return False

        # Most-clustered enemy within spellcast range. Cluster measured by
        # neighbors within "nearby" range of the candidate.
        target_agent_id = Routines.Targeting.PickClusteredTarget(
            Range.Nearby.value,
            filter_radius=Range.Spellcast.value,
        )
        if not target_agent_id:
            return False

        # Only step when that enemy is beyond the minimum distance.
        if Utils.Distance(Player.GetXY(), Agent.GetXY(target_agent_id)) <= min_distance:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                skill_id=deaths_charge_id,
                target_agent_id=target_agent_id,
                log=False,
                aftercast_delay=250,
            )
        )

    # endregion

    # region S
    def Shadow_Sanctuary(self) -> BuildCoroutine:
        """Self-cast Shadow Sanctuary while a foe stands within "nearby" range.

        Resolves whichever variant is actually on the bar — the skill is
        registered in the name table under both ``Shadow_Sanctuary_kurzick`` and
        ``Shadow_Sanctuary_luxon``. Skipped when the enchantment is already on
        the caster.
        """
        shadow_sanctuary_id: int = 0
        for variant_name in ("Shadow_Sanctuary_kurzick", "Shadow_Sanctuary_luxon"):
            variant_id = Skill.GetID(variant_name)
            if self.build.IsSkillEquipped(variant_id):
                shadow_sanctuary_id = variant_id
                break

        if not shadow_sanctuary_id:
            return False
        if not Routines.Agents.GetNearestEnemy(Range.Nearby.value):
            return False
        if Routines.Checks.Agents.HasEffect(Player.GetAgentID(), shadow_sanctuary_id):
            return False

        return (
            yield from self.build.CastSkillID(
                skill_id=shadow_sanctuary_id,
                log=False,
                aftercast_delay=250,
            )
        )

    # endregion
