from __future__ import annotations

from typing import TYPE_CHECKING

from .NoAttribute import NoAttribute as NoAttributeClass
from .HealingPrayers import HealingPrayers as HealingPrayersClass
from .SmitingPrayers import SmitingPrayers as SmitingPrayersClass
from .ProtectionPrayers import ProtectionPrayers as ProtectionPrayersClass
from .DivineFavor import DivineFavor as DivineFavorClass

if TYPE_CHECKING:
    from Core.build_src.combat_services import CombatServices


class MonkSkills:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build
        self.NoAttribute: NoAttributeClass = NoAttributeClass(build)
        self.HealingPrayers: HealingPrayersClass = HealingPrayersClass(build)
        self.SmitingPrayers: SmitingPrayersClass = SmitingPrayersClass(build)
        self.ProtectionPrayers: ProtectionPrayersClass = ProtectionPrayersClass(build)
        self.DivineFavor: DivineFavorClass = DivineFavorClass(build)
