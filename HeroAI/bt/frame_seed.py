"""Per-frame blackboard seeding: cheap facts eagerly, scans lazily."""

from Core.Agent import Agent
from Core.Player import Player
from Core.Routines import Routines
from Core import Range


def seed_frame(blackboard: dict, cached_data) -> None:
    blackboard["frame_id"] = blackboard.get("frame_id", 0) + 1
    blackboard["cache"] = cached_data
    blackboard["in_aggro"] = bool(cached_data.data.in_aggro or cached_data.data.local_in_aggro)
    blackboard["party_position"] = cached_data.data.party_position
    blackboard["is_leader"] = cached_data.data.is_leader
    blackboard["weapon_type"] = cached_data.data.weapon_type
    player_id = Player.GetAgentID()
    blackboard["player_id"] = player_id
    blackboard["player_health"] = Agent.GetHealth(player_id)
    blackboard["player_energy"] = cached_data.combat_handler.GetEnergyValues(player_id)
    for key in ("nearest_spirit_spellcast", "nearest_spirit_earshot", "nearest_enemy"):
        blackboard.pop(key, None)


def nearest_spirit_spellcast(blackboard: dict) -> int:
    if "nearest_spirit_spellcast" not in blackboard:
        blackboard["nearest_spirit_spellcast"] = Routines.Agents.GetNearestSpirit(Range.Spellcast.value)
    return blackboard["nearest_spirit_spellcast"]


def nearest_spirit_earshot(blackboard: dict) -> int:
    if "nearest_spirit_earshot" not in blackboard:
        blackboard["nearest_spirit_earshot"] = Routines.Agents.GetNearestSpirit(Range.Earshot.value)
    return blackboard["nearest_spirit_earshot"]


def nearest_enemy(blackboard: dict, distance: float) -> int:
    if "nearest_enemy" not in blackboard:
        blackboard["nearest_enemy"] = Routines.Agents.GetNearestEnemy(distance)
    return blackboard["nearest_enemy"]
