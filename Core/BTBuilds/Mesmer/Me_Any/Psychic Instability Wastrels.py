"""BT port of Builds/Mesmer/Me_Any/Psychic Instability Wastrel's.py.

Each Wastrel's rung picks a target, casts, then records a per-target cooldown
only on success. That pick/cast/track triple has to stay atomic, so it lives in
one generator per rung rather than being split across guard and cast nodes —
splitting would let the target change between the guard and the cast.
"""

import time
from dataclasses import dataclass

from Core import Agent, BldMgrBT, Player, Profession, Range, Routines
from Core.AgentArray import AgentArray
from Core.Builds.Skills import SkillsTemplate
from Core.GlobalCache import GLOBAL_CACHE
from Core.Py4GWcorelib import Utils
from Core.Skill import Skill
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, cond, guarded_cast, rotation_tree

WASTRELS_DEMISE_COOLDOWN_S: float = 5.0
WASTRELS_WORRY_COOLDOWN_S: float = 3.0

Psychic_Instability_ID = Skill.GetID("Psychic_Instability")
Wastrels_Demise_ID = Skill.GetID("Wastrels_Demise")
Wastrels_Worry_ID = Skill.GetID("Wastrels_Worry")
Power_Spike_ID = Skill.GetID("Power_Spike")
Cry_of_Frustration_ID = Skill.GetID("Cry_of_Frustration")
Power_Drain_ID = Skill.GetID("Power_Drain")
Mistrust_ID = Skill.GetID("Mistrust")
Cry_of_Pain_ID = Skill.GetID("Cry_of_Pain")


def agent_is_knocked_down(agent_id: int) -> bool:
    model_state = Agent.GetModelState(agent_id)
    if model_state == 1104 or (model_state & 0x400):
        return True
    return bool(Agent.IsKnockedDown(agent_id))


def pick_wastrels_target(
    skill_id: int,
    last_cast: dict[int, float],
    cooldown_s: float,
    *,
    require_knockdown: bool = False,
    exclude_knockdown: bool = False,
    min_energy_abs: int = 0,
) -> int:
    if require_knockdown and exclude_knockdown:
        return 0

    if min_energy_abs > 0:
        player_id = Player.GetAgentID()
        current_energy = Agent.GetEnergy(player_id) * Agent.GetMaxEnergy(player_id)
        if current_energy < min_energy_abs:
            return 0

    aoe_range = GLOBAL_CACHE.Skill.Data.GetAoERange(skill_id) or Range.Adjacent.value
    now = time.monotonic()

    def not_on_cooldown(agent_id: int) -> bool:
        last = last_cast.get(agent_id)
        return last is None or now - last >= cooldown_s

    player_pos = Player.GetXY()
    enemy_array = AgentArray.GetEnemyArray()
    enemy_array = AgentArray.Filter.ByDistance(enemy_array, player_pos, Range.Spellcast.value)
    enemy_array = AgentArray.Filter.ByCondition(
        enemy_array,
        lambda agent_id: Agent.IsValid(agent_id) and Agent.IsAlive(agent_id) and not_on_cooldown(agent_id),
    )
    if not enemy_array:
        return 0

    def cluster_sort_key(agent_id: int) -> tuple[int, float]:
        return (
            -Routines.Targeting.CountNearbyEnemies(agent_id, aoe_range),
            Utils.Distance(player_pos, Agent.GetXY(agent_id)),
        )

    if require_knockdown:
        kd_enemies = [a for a in enemy_array if agent_is_knocked_down(a)]
        if not kd_enemies:
            return 0
        return sorted(kd_enemies, key=cluster_sort_key)[0]

    if exclude_knockdown:
        enemy_array = [a for a in enemy_array if not agent_is_knocked_down(a)]
        if not enemy_array:
            return 0

    non_casting = [a for a in enemy_array if not Agent.IsCasting(a)]
    if non_casting:
        return sorted(non_casting, key=cluster_sort_key)[0]

    return sorted(enemy_array, key=cluster_sort_key)[0]


@dataclass(slots=True)
class PsychicInstabilityWastrelsBarSnapshot:
    in_aggro: bool = False
    enemy_in_spellcast: bool = False
    enemy_casting: bool = False
    enemy_casting_spell: bool = False
    enemy_casting_spell_or_chant: bool = False
    player_energy_pct: float = 1.0


class Psychic_Instability_Wastrels(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="Psychic Instability Wastrel's",
            required_primary=Profession.Mesmer,
            template_code="OQBTAUBPwJEeTlBXgcQGAAAAA",
            required_skills=[
                Psychic_Instability_ID,
                Wastrels_Demise_ID,
                Wastrels_Worry_ID,
            ],
            optional_skills=[
                Power_Spike_ID,
                Cry_of_Frustration_ID,
                Power_Drain_ID,
                Mistrust_ID,
                Cry_of_Pain_ID,
            ],
        )
        if match_only:
            return

        self.wastrels_demise_last_cast: dict[int, float] = {}
        self.wastrels_worry_last_cast: dict[int, float] = {}

        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))
        self.SetBlockedSkills(
            [
                Psychic_Instability_ID,
                Wastrels_Demise_ID,
                Wastrels_Worry_ID,
                Power_Spike_ID,
                Cry_of_Frustration_ID,
                Power_Drain_ID,
                Mistrust_ID,
                Cry_of_Pain_ID,
            ]
        )
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def get_bar_snapshot(self) -> PsychicInstabilityWastrelsBarSnapshot:
        snapshot = PsychicInstabilityWastrelsBarSnapshot()
        snapshot.in_aggro = bool(self.IsInAggro())
        snapshot.player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))

        if not snapshot.in_aggro:
            return snapshot

        snapshot.enemy_in_spellcast = bool(Routines.Agents.GetNearestEnemy(Range.Spellcast.value))
        if snapshot.enemy_in_spellcast:
            snapshot.enemy_casting = bool(Routines.Targeting.GetEnemyCasting(Range.Spellcast.value))
            snapshot.enemy_casting_spell = bool(Routines.Targeting.GetEnemyCastingSpell(Range.Spellcast.value))
            snapshot.enemy_casting_spell_or_chant = bool(
                Routines.Targeting.GetEnemyCastingSpellOrChant(Range.Spellcast.value)
            )

        return snapshot

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["pi_wastrels_snapshot"] = self.get_bar_snapshot()

    def snapshot(self, node) -> PsychicInstabilityWastrelsBarSnapshot:
        return node.blackboard.get("pi_wastrels_snapshot") or PsychicInstabilityWastrelsBarSnapshot()

    def pick_demise(self, **kwargs) -> int:
        now = time.monotonic()
        self.wastrels_demise_last_cast = {
            a: t for a, t in self.wastrels_demise_last_cast.items() if now - t < WASTRELS_DEMISE_COOLDOWN_S
        }
        return pick_wastrels_target(
            Wastrels_Demise_ID, self.wastrels_demise_last_cast, WASTRELS_DEMISE_COOLDOWN_S, **kwargs
        )

    def pick_worry(self, **kwargs) -> int:
        now = time.monotonic()
        self.wastrels_worry_last_cast = {
            a: t for a, t in self.wastrels_worry_last_cast.items() if now - t < WASTRELS_WORRY_COOLDOWN_S
        }
        return pick_wastrels_target(
            Wastrels_Worry_ID, self.wastrels_worry_last_cast, WASTRELS_WORRY_COOLDOWN_S, **kwargs
        )

    def cast_demise(self, **kwargs):
        target = self.pick_demise(**kwargs)
        if not target:
            return False
        fired = yield from self.skills.Mesmer.DominationMagic.Wastrels_Demise(target_agent_id=target)
        if fired:
            self.wastrels_demise_last_cast[target] = time.monotonic()
        return fired

    def cast_worry(self, **kwargs):
        target = self.pick_worry(**kwargs)
        if not target:
            return False
        fired = yield from self.skills.Mesmer.DominationMagic.Wastrels_Worry(target_agent_id=target)
        if fired:
            self.wastrels_worry_last_cast[target] = time.monotonic()
        return fired

    def build_rotation_tree(self) -> BehaviorTree:
        domination = lambda: self.skills.Mesmer.DominationMagic
        inspiration = lambda: self.skills.Mesmer.InspirationMagic
        pve = lambda: self.skills.Any.PvE
        return rotation_tree(
            "PsychicInstabilityWastrels",
            [
                cond("CanCast", lambda: Routines.Checks.Skills.CanCast()),
                cond("InAggro", lambda node: self.snapshot(node).in_aggro),
            ],
            [
                cast(self, "WastrelsDemiseKnockedDown", lambda: self.cast_demise(require_knockdown=True)),
                cast(self, "WastrelsWorryKnockedDown", lambda: self.cast_worry(require_knockdown=True)),
                guarded_cast(
                    self,
                    "PowerDrainLowEnergy",
                    lambda node: self.snapshot(node).enemy_casting_spell_or_chant,
                    lambda: inspiration().Power_Drain(energy_threshold_pct=0.30),
                ),
                guarded_cast(
                    self,
                    "PsychicInstability",
                    lambda node: self.snapshot(node).enemy_casting,
                    lambda: domination().Psychic_Instability(),
                ),
                guarded_cast(
                    self,
                    "CryOfFrustration",
                    lambda node: self.snapshot(node).enemy_casting,
                    lambda: domination().Cry_of_Frustration(),
                ),
                guarded_cast(
                    self,
                    "CryOfPainHexed",
                    lambda node: self.snapshot(node).enemy_casting,
                    lambda: pve().Cry_of_Pain(require_mesmer_hex=True),
                ),
                guarded_cast(
                    self,
                    "CryOfPain",
                    lambda node: self.snapshot(node).enemy_in_spellcast,
                    lambda: pve().Cry_of_Pain(),
                ),
                guarded_cast(
                    self,
                    "Mistrust",
                    lambda node: self.snapshot(node).enemy_casting_spell,
                    lambda: domination().Mistrust(),
                ),
                guarded_cast(
                    self,
                    "PowerSpike",
                    lambda node: self.snapshot(node).enemy_casting_spell_or_chant,
                    lambda: inspiration().Power_Spike(),
                ),
                guarded_cast(
                    self,
                    "PowerDrain",
                    lambda node: self.snapshot(node).enemy_casting_spell_or_chant,
                    lambda: inspiration().Power_Drain(),
                ),
                cast(
                    self,
                    "WastrelsDemiseLowPriority",
                    lambda: self.cast_demise(min_energy_abs=10, exclude_knockdown=True),
                ),
                cast(
                    self, "WastrelsWorryLowPriority", lambda: self.cast_worry(min_energy_abs=10, exclude_knockdown=True)
                ),
            ],
        )
