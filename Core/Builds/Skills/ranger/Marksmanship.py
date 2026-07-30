from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Core.build_src.combat_services import CombatServices


class Marksmanship:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build
