from __future__ import annotations

from typing import TYPE_CHECKING

from .NoAttribute import NoAttribute as NoAttributeClass
from .PvE import PvE as PvEClass

if TYPE_CHECKING:
    from Core.build_src.combat_services import CombatServices


class AnySkills:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build
        self.NoAttribute: NoAttributeClass = NoAttributeClass(build)
        self.PvE: PvEClass = PvEClass(build)
