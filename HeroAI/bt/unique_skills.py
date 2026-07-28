"""Tier 2: UniqueProperty skill predicates, ported branch-for-branch from
CombatClass.AreCastConditionsMet (combat.py:1038-1180). Registry replaces the
`if skill_id ==` ladder; the logic inside each entry is unchanged."""

from typing import Callable

from Core.Agent import Agent
from Core.GlobalCache import GLOBAL_CACHE
from Core.Player import Player
from Core.Routines import Routines
from Core import Range

Predicate = Callable[[object, object, int], bool]

registry_cache: tuple[int, dict[int, Predicate]] | None = None


def player_energy_below(handler, threshold: float) -> bool:
    energy = handler.GetEnergyValues(Player.GetAgentID())
    return energy >= 0.0 and energy < threshold


def player_life_below(threshold: float) -> bool:
    return Agent.GetHealth(Player.GetAgentID()) < threshold


def pet_low_or_dead(conditions) -> bool:
    from Core.Party import Party

    pet_data = Party.Pets.GetPetInfo(Player.GetAgentID())
    if not pet_data or pet_data.agent_id == 0:
        return False
    low = Routines.Checks.Agents.GetHealth(pet_data.agent_id) < conditions.LessLife
    return low or Routines.Checks.Agents.IsDead(pet_data.agent_id)


def pet_alive() -> bool:
    pet_id = GLOBAL_CACHE.Party.Pets.GetPetID(Player.GetAgentID())
    return pet_id != 0 and Routines.Checks.Agents.IsAlive(pet_id)


def natures_blessing_check(conditions) -> bool:
    player_low = player_life_below(conditions.LessLife)
    nearest_npc = Routines.Agents.GetNearestNPC(Range.Spirit.value)
    if nearest_npc == 0:
        return player_low
    return player_low or Routines.Checks.Agents.GetHealth(nearest_npc) < conditions.LessLife


def junundu_wail_check(handler, conditions) -> bool:
    if Routines.Agents.GetDeadAlly(Range.Earshot.value) != 0:
        return True
    life = player_life_below(conditions.LessLife)
    if Routines.Agents.GetNearestEnemy(handler.get_combat_distance()) == 0:
        return life
    return False


def junundu_siege_check() -> bool:
    return (
        Routines.Agents.GetNearestEnemy(Range.Nearby.value) != 0
        and Routines.Agents.GetNearestEnemyOutsideRange(Range.Nearby.value, Range.Earshot.value) != 0
    )


def build_registry(handler) -> dict[int, Predicate]:
    global registry_cache
    if registry_cache is not None and registry_cache[0] == id(handler):
        return registry_cache[1]

    energy_gate: Predicate = lambda h, c, t: player_energy_below(h, c.LessEnergy)
    hex_or_ench: Predicate = lambda h, c, t: (
        Routines.Checks.Agents.IsHexed(t) or Routines.Checks.Agents.IsEnchanted(t)
    )
    hex_or_condi: Predicate = lambda h, c, t: (
        Routines.Checks.Agents.IsHexed(t) or Routines.Checks.Agents.IsConditioned(t)
    )
    player_conditioned: Predicate = lambda h, c, t: Routines.Checks.Agents.IsConditioned(Player.GetAgentID())
    player_enchanted: Predicate = lambda h, c, t: Routines.Checks.Agents.IsEnchanted(Player.GetAgentID())
    spirit_in_spellcast: Predicate = lambda h, c, t: Routines.Agents.GetNearestSpirit(Range.Spellcast.value) != 0

    registry: dict[int, Predicate] = {
        handler.energy_drain: energy_gate,
        handler.energy_tap: energy_gate,
        handler.ether_lord: energy_gate,
        handler.ether_feast: lambda h, c, t: player_life_below(c.LessLife),
        handler.essence_strike: lambda h, c, t: (
            player_energy_below(h, c.LessEnergy) and Routines.Agents.GetNearestSpirit(Range.Spellcast.value) != 0
        ),
        handler.glowing_signet: lambda h, c, t: (player_energy_below(h, c.LessEnergy) and h.HasEffect(t, h.burning)),
        handler.clamor_of_souls: lambda h, c, t: (
            player_energy_below(h, c.LessEnergy) and Agent.IsHoldingItem(Player.GetAgentID())
        ),
        handler.waste_not_want_not: lambda h, c, t: (
            player_energy_below(h, c.LessEnergy)
            and not Agent.IsCasting(t)
            and not Routines.Checks.Agents.IsAttacking(t)
        ),
        handler.mend_body_and_soul: lambda h, c, t: (
            player_life_below(c.LessLife)
            or (Routines.Agents.GetNearestSpirit(Range.Earshot.value) != 0 and Routines.Checks.Agents.IsConditioned(t))
        ),
        handler.grenths_balance: lambda h, c, t: (
            player_life_below(c.LessLife) and Agent.GetHealth(Player.GetAgentID()) < Routines.Checks.Agents.GetHealth(t)
        ),
        handler.deaths_retreat: lambda h, c, t: (
            Agent.GetHealth(Player.GetAgentID()) < Routines.Checks.Agents.GetHealth(t)
        ),
        handler.plague_sending: player_conditioned,
        handler.plague_signet: player_conditioned,
        handler.plague_touch: player_conditioned,
        handler.golden_fang_strike: player_enchanted,
        handler.golden_fox_strike: player_enchanted,
        handler.golden_lotus_strike: player_enchanted,
        handler.golden_phoenix_strike: player_enchanted,
        handler.golden_skull_strike: player_enchanted,
        handler.brutal_weapon: lambda h, c, t: not Routines.Checks.Agents.IsEnchanted(Player.GetAgentID()),
        handler.signet_of_removal: lambda h, c, t: (
            not Routines.Checks.Agents.IsEnchanted(t) and Routines.Checks.Agents.IsConditioned(t)
        ),
        handler.dwaynas_kiss: hex_or_ench,
        handler.unnatural_signet: hex_or_ench,
        handler.toxic_chill: hex_or_ench,
        handler.discord: lambda h, c, t: (
            (Routines.Checks.Agents.IsHexed(t) and Routines.Checks.Agents.IsConditioned(t))
            or Routines.Checks.Agents.IsEnchanted(t)
        ),
        handler.empathic_removal: hex_or_condi,
        handler.iron_palm: hex_or_condi,
        handler.melandrus_resilience: hex_or_condi,
        handler.necrosis: hex_or_condi,
        handler.peace_and_harmony: hex_or_condi,
        handler.purge_signet: hex_or_condi,
        handler.resilient_weapon: hex_or_condi,
        handler.gaze_from_beyond: spirit_in_spellcast,
        handler.spirit_burn: spirit_in_spellcast,
        handler.signet_of_ghostly_might: spirit_in_spellcast,
        handler.comfort_animal: lambda h, c, t: pet_low_or_dead(c),
        handler.heal_as_one: lambda h, c, t: pet_low_or_dead(c),
        handler.never_rampage_alone: lambda h, c, t: pet_alive(),
        handler.whirlwind_attack: lambda h, c, t: Agent.GetWeaponType(Player.GetAgentID())[0] not in (1, 6),
        handler.natures_blessing: lambda h, c, t: natures_blessing_check(c),
        handler.relentless_assault: lambda h, c, t: (
            Routines.Checks.Agents.IsHexed(Player.GetAgentID())
            or Routines.Checks.Agents.IsConditioned(Player.GetAgentID())
        ),
        handler.junundu_wail: lambda h, c, t: junundu_wail_check(h, c),
        handler.junundu_tunnel: lambda h, c, t: (Routines.Agents.GetNearestEnemy(h.get_combat_distance()) == 0),
        handler.junundu_siege: lambda h, c, t: junundu_siege_check(),
        handler.unknown_junundu_ability: lambda h, c, t: False,
        handler.leave_junundu: lambda h, c, t: False,
    }
    registry.pop(0, None)
    registry_cache = (id(handler), registry)
    return registry


def evaluate(handler, skill_id: int, conditions, target_id: int) -> bool | None:
    """True/False = ported verdict. None = no entry, caller falls back to legacy."""
    predicate = build_registry(handler).get(skill_id)
    if predicate is None:
        return None
    return predicate(handler, conditions, target_id)
