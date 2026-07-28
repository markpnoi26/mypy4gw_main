"""Tier 1: declarative CastConditions evaluation, ported from the feature-count
half of CombatClass.AreCastConditionsMet (combat.py:1183-1540).

Returns None when a condition family is not covered natively yet — the caller
falls back to the legacy oracle. Families deferred: IsCasting (interrupt
feasibility + outcome queue), IsPartyWide, HasDervishEnchantment, HasChant,
and the Pet/PetAttack target special cases."""

from Core.Agent import Agent
from Core.Player import Player
from Core.Routines import Routines
from Core import Range

from HeroAI.types import SkillNature, Skilltarget, SkillType

FALLBACK_FAMILIES = ("IsCasting", "IsPartyWide", "HasDervishEnchantment", "HasChant")


def needs_fallback(skill) -> bool:
    conditions = skill.custom_skill_data.Conditions
    if any(getattr(conditions, family, False) for family in FALLBACK_FAMILIES):
        return True
    if skill.custom_skill_data.TargetAllegiance == Skilltarget.Pet.value:
        return True
    if skill.custom_skill_data.SkillType == SkillType.PetAttack.value:
        return True
    return False


def evaluate(handler, slot: int, target_id: int) -> bool | None:
    skill = handler.skills[slot]
    if needs_fallback(skill):
        return None

    conditions = skill.custom_skill_data.Conditions
    checks: list[bool] = []

    if conditions.IsAlive:
        checks.append(Routines.Checks.Agents.IsAlive(target_id))
    if conditions.HasCondition:
        checks.append(Routines.Checks.Agents.IsConditioned(target_id))
    if conditions.HasBleeding:
        checks.append(Agent.IsBleeding(target_id))
    if conditions.HasBlindness:
        checks.append(handler.HasEffect(target_id, handler.blind))
    if conditions.HasBurning:
        checks.append(handler.HasEffect(target_id, handler.burning))
    if conditions.HasCrackedArmor:
        checks.append(handler.HasEffect(target_id, handler.cracked_armor))
    if conditions.HasCrippled:
        checks.append(Agent.IsCrippled(target_id))
    if conditions.HasDazed:
        checks.append(handler.HasEffect(target_id, handler.dazed))
    if conditions.HasDeepWound:
        checks.append(handler.HasEffect(target_id, handler.deep_wound))
    if conditions.HasDisease:
        checks.append(handler.HasEffect(target_id, handler.disease))
    if conditions.HasPoison:
        checks.append(Agent.IsPoisoned(target_id))
    if conditions.HasWeakness:
        checks.append(handler.HasEffect(target_id, handler.weakness))

    if conditions.HasWeaponSpell:
        met = False
        if Routines.Checks.Agents.IsWeaponSpelled(target_id):
            if len(conditions.WeaponSpellList) == 0:
                met = True
            else:
                met = any(
                    handler.HasEffect(target_id, skill_id, exact_weapon_spell=True)
                    for skill_id in conditions.WeaponSpellList
                )
        checks.append(met)

    if conditions.HasEnchantment:
        met = False
        if Routines.Checks.Agents.IsEnchanted(target_id):
            if len(conditions.EnchantmentList) == 0:
                met = True
            else:
                met = any(handler.HasEffect(target_id, skill_id) for skill_id in conditions.EnchantmentList)
        checks.append(met)

    if conditions.HasHex:
        met = False
        if Routines.Checks.Agents.IsHexed(target_id):
            if len(conditions.HexList) == 0:
                met = True
            else:
                met = any(handler.HasEffect(target_id, skill_id) for skill_id in conditions.HexList)
        checks.append(met)

    if conditions.IsKnockedDown:
        checks.append(Routines.Checks.Agents.IsKnockedDown(target_id))
    if conditions.IsMoving:
        checks.append(Agent.IsMoving(target_id))
    if conditions.IsAttacking:
        checks.append(Routines.Checks.Agents.IsAttacking(target_id))
    if conditions.IsHoldingItem:
        checks.append(Agent.IsHoldingItem(target_id))

    if conditions.LessLife != 0:
        checks.append(Routines.Checks.Agents.GetHealth(target_id) < conditions.LessLife)
    if conditions.MoreLife != 0:
        checks.append(Routines.Checks.Agents.GetHealth(target_id) > conditions.MoreLife)

    if conditions.LessEnergy != 0:
        if handler.IsPartyMember(target_id):
            from HeroAI.utils import GetEnergyValues

            target_energy = GetEnergyValues(target_id)
            checks.append(target_energy >= 0.0 and target_energy < conditions.LessEnergy)
        else:
            checks.append(True)

    if conditions.LessSelfEnergyPercentage > 0:
        player_energy = handler.GetEnergyValues(Player.GetAgentID())
        checks.append(player_energy >= 0.0 and player_energy <= conditions.LessSelfEnergyPercentage)

    if conditions.Overcast != 0:
        checks.append(Player.GetAgentID() == target_id and Agent.GetOvercast(target_id) < conditions.Overcast)

    if conditions.RequiresSpiritInEarshot:
        checks.append(Routines.Agents.GetNearestSpirit(Range.Earshot.value) != 0)

    if conditions.EnemyCount != 0:
        player_x, player_y = Player.GetXY()
        enemy_array = Routines.Agents.GetFilteredEnemyArray(player_x, player_y, conditions.EnemiesInRange)
        checks.append(len(enemy_array) >= conditions.EnemyCount)

    if conditions.AlliesInRange != 0:
        player_x, player_y = Player.GetXY()
        ally_array = Routines.Agents.GetFilteredAllyArray(
            player_x, player_y, conditions.AlliesInRangeArea, other_ally=True
        )
        checks.append(len(ally_array) >= conditions.AlliesInRange)

    if conditions.SpiritsInRange != 0:
        player_x, player_y = Player.GetXY()
        spirit_array = Routines.Agents.GetFilteredSpiritArray(player_x, player_y, conditions.SpiritsInRangeArea)
        checks.append(len(spirit_array) >= conditions.SpiritsInRange)

    if conditions.MinionsInRange != 0:
        player_x, player_y = Player.GetXY()
        minion_array = Routines.Agents.GetFilteredMinionArray(player_x, player_y, conditions.MinionsInRangeArea)
        checks.append(len(minion_array) >= conditions.MinionsInRange)

    if conditions.CloseToAggro:
        checks.append(handler.in_aggro)

    if str(conditions.RequireWeapon or "").strip():
        checks.append(handler._matches_required_weapon(conditions.RequireWeapon))

    return all(checks)
