"""HeroAI policy for the combat-layer enemy filter (Core/routines_src/CombatLayer)."""

from Core import Agent, Player
from Core.routines_src import CombatLayer

from .settings import Settings


def enemy_pool_filter(agent_ids: list[int]) -> list[int]:
    settings = Settings()
    if not settings.LayerAwareTargeting:
        return agent_ids
    player_id = Player.GetAgentID()
    if not player_id or not Agent.IsValid(player_id):
        return agent_ids
    reference_zplane = Agent.GetZPlane(player_id)
    reference_z = Agent.GetXYZ(player_id)[2]
    tolerance = settings.CombatLayerZTolerance

    def same_layer(agent_id: int) -> bool:
        return CombatLayer.IsSameCombatLayer(
            reference_zplane, reference_z, Agent.GetZPlane(agent_id), Agent.GetXYZ(agent_id)[2], tolerance
        )

    return CombatLayer.FilterByCombatLayer(agent_ids, same_layer)


def InstallLayerFilter() -> None:
    CombatLayer.SetEnemyPoolFilter(enemy_pool_filter)
