from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Core.BuildMgr import BuildMgr


class Swordsmanship:
    def __init__(self, build: BuildMgr) -> None:
        self.build: BuildMgr = build
