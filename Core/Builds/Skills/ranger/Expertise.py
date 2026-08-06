from __future__ import annotations

from typing import TYPE_CHECKING

from Core.BuildMgr import BuildCoroutine
from Core.Player import Player
from Core.Skill import Skill
from Core import Routines

if TYPE_CHECKING:
    from Core.BuildMgr import BuildMgr

__all__ = ["Expertise"]


class Expertise:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build

    # region T
    def Together_as_One(self) -> BuildCoroutine:
        together_as_one_id: int = Skill.GetID("Together_as_one")

        if not self.build.IsSkillEquipped(together_as_one_id):
            return False

        return (
            yield from self.build.CastSkillID(
                skill_id=together_as_one_id,
                log=False,
                aftercast_delay=250,
            )
        )

    # endregion
