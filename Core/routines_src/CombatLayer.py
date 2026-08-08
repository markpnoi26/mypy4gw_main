"""Combat-layer (vertical) filtering for enemy pools.

Mechanism only — policy (settings, reference position) is registered by the
consumer via SetEnemyPoolFilter. See HeroAI/layer_targeting.py.
"""

import math
from typing import Callable
from typing import Iterable
from typing import List
from typing import Optional
from typing import TypeVar

T = TypeVar('T')

enemy_pool_filter: Optional[Callable[[List[int]], List[int]]] = None


def IsSameCombatLayer(
    reference_zplane: int,
    reference_z: float,
    target_zplane: int,
    target_z: float,
    tolerance: float,
) -> bool:
    """World-Z separation decides; a zplane match never bypasses the tolerance.

    Live maps place vertically separated surfaces on the same discrete pathing
    plane (zplane reads 0 on real multi-level maps), so the planes are kept
    only as diagnostics. Non-finite samples fail open.
    """
    _ = reference_zplane, target_zplane
    if not math.isfinite(float(reference_z)) or not math.isfinite(float(target_z)):
        return True
    return abs(float(reference_z) - float(target_z)) <= max(0.0, float(tolerance))


def FilterByCombatLayer(candidates: Iterable[T], is_eligible: Callable[[T], bool]) -> List[T]:
    """Filter before ranking so an ineligible nearest candidate cannot mask a valid farther one."""
    return [candidate for candidate in candidates if is_eligible(candidate)]


def SetEnemyPoolFilter(provider: Optional[Callable[[List[int]], List[int]]]) -> None:
    global enemy_pool_filter
    enemy_pool_filter = provider


def ApplyEnemyPoolFilter(agent_ids: List[int]) -> List[int]:
    if enemy_pool_filter is None:
        return agent_ids
    try:
        return enemy_pool_filter(list(agent_ids))
    except Exception:
        return agent_ids
