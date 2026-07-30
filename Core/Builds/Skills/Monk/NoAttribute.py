from __future__ import annotations

from typing import TYPE_CHECKING

from Core.build_src.combat_services import BuildCoroutine
from Core import AgentArray, Range, Routines, Utils
from Core.Agent import Agent
from Core.Player import Player
from Core.Skill import Skill
from Core.GlobalCache.HexRemovalPriority import (
    HexRemovalPriority,
    cast_hex_removal_and_track,
    get_hexed_ally_for_removal,
)
from HeroAI.targeting import GetAllAlliesArray
from HeroAI.types import Skilltarget

if TYPE_CHECKING:
    from HeroAI.custom_skill_src.skill_types import CustomSkill
    from Core.build_src.combat_services import CombatServices

__all__ = ["NoAttribute"]


class NoAttribute:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build

    # region R
    def Remove_Hex(self, min_priority: int = HexRemovalPriority.LOW) -> BuildCoroutine:
        remove_hex_id: int = Skill.GetID("Remove_Hex")

        if not self.build.IsSkillEquipped(remove_hex_id):
            return False

        target_agent_id = get_hexed_ally_for_removal(
            Range.Spellcast.value,
            reserve=True,
            skill_id=remove_hex_id,
            min_priority=min_priority,
        )
        if not target_agent_id:
            return False

        return (
            yield from cast_hex_removal_and_track(
                self.build,
                skill_id=remove_hex_id,
                target_agent_id=target_agent_id,
                aftercast_delay=250,
            )
        )

    # endregion

    # region S
    def Seed_of_Life(self, *, rank_by_relative_spike: bool = False, drop_threshold: float = 0.08) -> BuildCoroutine:
        seed_of_life_id: int = Skill.GetID("Seed_of_Life")
        seed_of_life: CustomSkill = self.build.GetCustomSkill(seed_of_life_id)
        health_threshold: float = max(0.0, min(1.0, float(seed_of_life.Conditions.LessLife or 0.80)))
        spike_ratio_precision = 1

        def _is_valid_seed_target(agent_id: int) -> bool:
            return (
                Agent.IsAlive(agent_id)
                and agent_id != Player.GetAgentID()
                and Agent.GetHealth(agent_id) <= health_threshold
            )

        def resolve_relative_spike_target() -> int:
            """Seed whoever stands out most against the party's own damage intake.

            The absolute floor still gates — a party eating even AoE has no
            standout and must stay seedable — but among everyone taking real
            damage the pick is the largest multiple of the party average, so a
            focused target beats a merely low-HP one. Ratios are rounded so
            near-equal spikes fall through to the melee preference.
            """
            self.build.UpdatePartyHealthMonitor(sample_interval_ms=150, window_ms=1000)
            baseline = max(self.build.GetPartyHealthDeltaAverage(), drop_threshold)

            def class_rank(agent_id: int) -> int:
                if Routines.Checks.Agents.IsMelee(agent_id):
                    return 0
                return 1 if Routines.Checks.Agents.IsMartial(agent_id) else 2

            return self.build.ResolveRankedPartyAllyTarget(
                seed_of_life_id,
                seed_of_life,
                validator=lambda agent_id: (
                    _is_valid_seed_target(agent_id) and self.build.GetPartyHealthDelta(agent_id) >= drop_threshold
                ),
                rank_key=lambda agent_id: (
                    -round(self.build.GetPartyHealthDelta(agent_id) / baseline, spike_ratio_precision),
                    class_rank(agent_id),
                    Agent.GetHealth(agent_id),
                ),
                sample_interval_ms=150,
                window_ms=1000,
            )

        def _resolve_seed_of_life_target() -> int:
            return self.build.ResolvePreferredPartySpikeAllyTarget(
                seed_of_life_id,
                seed_of_life,
                variants=[
                    lambda custom_skill: setattr(custom_skill, "TargetAllegiance", Skilltarget.AllyMartialMelee.value),
                    lambda custom_skill: setattr(custom_skill, "TargetAllegiance", Skilltarget.AllyMartial.value),
                    None,
                ],
                validator=_is_valid_seed_target,
                drop_threshold=drop_threshold,
                sample_interval_ms=150,
                window_ms=1000,
            )

        if not self.build.IsSkillEquipped(seed_of_life_id):
            return False

        if rank_by_relative_spike:
            target_agent_id = resolve_relative_spike_target()
        else:
            target_agent_id = _resolve_seed_of_life_target()
        if not target_agent_id:
            return False

        return (
            yield from self.build.CastSkillIDAndRestoreTarget(
                seed_of_life_id,
                target_agent_id,
            )
        )
