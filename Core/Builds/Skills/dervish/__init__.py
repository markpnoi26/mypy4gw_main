from __future__ import annotations

from typing import TYPE_CHECKING

from .NoAttribute import NoAttribute as NoAttributeClass
from .ScytheMastery import ScytheMastery as ScytheMasteryClass
from .WindPrayers import WindPrayers as WindPrayersClass
from .EarthPrayers import EarthPrayers as EarthPrayersClass
from .Mysticism import Mysticism as MysticismClass

if TYPE_CHECKING:
    from Core.build_src.combat_services import CombatServices


class DervishSkills:
    def __init__(self, build: CombatServices) -> None:
        self.build: CombatServices = build
        self.NoAttribute: NoAttributeClass = NoAttributeClass(build)
        self.ScytheMastery: ScytheMasteryClass = ScytheMasteryClass(build)
        self.WindPrayers: WindPrayersClass = WindPrayersClass(build)
        self.EarthPrayers: EarthPrayersClass = EarthPrayersClass(build)
        self.Mysticism: MysticismClass = MysticismClass(build)
