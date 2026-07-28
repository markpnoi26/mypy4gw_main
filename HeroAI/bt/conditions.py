"""Per-slot cast decision for the BT rotation.

Default path delegates to the proven legacy oracle (CombatClass.IsReadyToCast,
combat.py:1552) so behavior is byte-identical. NATIVE_DECIDE flips to the
native port below, which reuses the same primitives but dispatches conditions
through the tier 1/2 tables instead of the AreCastConditionsMet ladder."""

from Core.Agent import Agent
from Core.GlobalCache import GLOBAL_CACHE
from Core.Player import Player
from Core.Routines import Routines
from Core import Allegiance

from HeroAI.types import SkillNature, Skilltarget, SkillType

from . import condition_table
from . import unique_skills

NATIVE_DECIDE = False


def decide_slot(handler, slot: int) -> tuple[bool, int]:
    if not NATIVE_DECIDE:
        return handler.IsReadyToCast(slot)
    return decide_slot_native(handler, slot)


def cast_conditions_met(handler, slot: int, target_id: int) -> bool:
    skill = handler.skills[slot]
    conditions = skill.custom_skill_data.Conditions

    if skill.custom_skill_data.Nature == SkillNature.Resurrection.value:
        from HeroAI.targeting import IsResurrectablePartyMember

        return bool(IsResurrectablePartyMember(target_id) and Routines.Checks.Agents.IsDead(target_id))

    if conditions.UniqueProperty:
        verdict = unique_skills.evaluate(handler, skill.skill_id, conditions, target_id)
        # legacy ladder falls through to `return True` for unconfigured UniqueProperty skills
        return True if verdict is None else verdict

    verdict = condition_table.evaluate(handler, slot, target_id)
    if verdict is None:
        return handler.AreCastConditionsMet(slot, target_id)
    return verdict


def decide_slot_native(handler, slot: int) -> tuple[bool, int]:
    from HeroAI.combat import VOW_SPELL_TYPES

    skill = handler.skills[slot]
    skillbar_data = skill.skillbar_data
    skill_id = skill.skill_id
    conditions = skill.custom_skill_data.Conditions
    target_allegiance = skill.custom_skill_data.TargetAllegiance

    def reject(target: int = 0) -> tuple[bool, int]:
        handler.in_casting_routine = False
        return False, target

    if skill_id == 0:
        return reject()
    if skillbar_data.recharge != 0:
        return reject()

    player_id = Player.GetAgentID()
    if Agent.IsCasting(player_id):
        return reject()
    if (GLOBAL_CACHE.SkillBar.GetCasting() or 0) != 0:
        return reject()

    adrenaline_required = GLOBAL_CACHE.Skill.Data.GetAdrenaline(skill_id)
    if adrenaline_required > 0 and skillbar_data.adrenaline_a < adrenaline_required:
        return reject()

    skill_type, _ = GLOBAL_CACHE.Skill.GetType(skill_id)
    if skill_type in VOW_SPELL_TYPES and Routines.Checks.Effects.HasBuff(player_id, 1517):
        return reject()
    if skill_type == SkillType.Shout.value:
        if handler.HasEffect(player_id, handler.vocal_minority) or handler.HasEffect(
            player_id, handler.well_of_silence
        ):
            return reject()

    current_hp = Agent.GetHealth(player_id)
    current_energy_fraction = handler.GetEnergyValues(player_id)
    if current_energy_fraction < 0.0:
        return reject()
    current_energy = current_energy_fraction * Agent.GetMaxEnergy(player_id)
    energy_cost = Routines.Checks.Skills.GetEnergyCostWithEffects(skill_id, player_id)
    if handler.expertise_exists:
        energy_cost = Routines.Checks.Skills.apply_expertise_reduction(energy_cost, handler.expertise_level, skill_id)
    if current_energy < energy_cost:
        return reject()

    health_cost = GLOBAL_CACHE.Skill.Data.GetHealthCost(skill_id)
    if (current_hp < conditions.SacrificeHealth) and health_cost > 0:
        return reject()

    min_after_pct = getattr(conditions, "MinHealthAfterSacrificePercent", 0.0)
    min_after_abs = getattr(conditions, "MinHealthAfterSacrificeAbsolute", 0)
    sacrifice_pct = getattr(conditions, "SacrificePercent", 0.0)
    if sacrifice_pct > 0 and (min_after_pct > 0 or min_after_abs > 0):
        max_hp = Agent.GetMaxHealth(player_id)
        sacrifice_amount = max_hp * sacrifice_pct
        hp_after_sacrifice = (current_hp * max_hp) - sacrifice_amount
        if min_after_abs > 0 and hp_after_sacrifice <= min_after_abs:
            return reject()
        if min_after_pct > 0 and max_hp > 0 and (hp_after_sacrifice / max_hp) <= min_after_pct:
            return reject()

    v_target = handler.GetAppropiateTarget(slot)
    if v_target is None or v_target == 0:
        return reject()

    v_target_allegiance, _ = Agent.GetAllegiance(v_target)
    if v_target_allegiance == Allegiance.Enemy.value and handler._is_blacklisted_enemy_target(v_target):
        return reject()

    if skill_type == SkillType.Hex.value:
        if Agent.IsSpirit(v_target) or (v_target_allegiance == Allegiance.Enemy.value and Agent.IsSpawned(v_target)):
            return reject()

    combo_type = GLOBAL_CACHE.Skill.Data.GetCombo(skill_id)
    dagger_status = Agent.GetDaggerStatus(v_target)
    if (
        (combo_type == 1 and dagger_status not in (0, 3))
        or (combo_type == 2 and dagger_status != 1)
        or (combo_type == 3 and dagger_status != 2)
    ):
        return reject(v_target)

    if handler.SpiritBuffExists(skill_id):
        return reject()

    exact_weapon_spell = skill_type == SkillType.WeaponSpell.value and conditions.AllowOverlapWeaponSpell
    if target_allegiance != Skilltarget.NonWeaponSpelledAlly.value and handler.HasEffect(
        v_target, skill_id, exact_weapon_spell=exact_weapon_spell
    ):
        return reject(v_target)

    if skill_id in (handler.blood_is_power, handler.blood_ritual):
        if handler.HasEffect(v_target, handler.blood_is_power) or handler.HasEffect(v_target, handler.blood_ritual):
            return reject(v_target)

    if not cast_conditions_met(handler, slot, v_target):
        return reject(v_target)

    return True, v_target
