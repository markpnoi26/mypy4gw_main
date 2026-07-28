"""Engine seam: routes the HeroAI drivers to legacy HeroAI_Build or the BT
engine based on the per-account toggle. Both engines stay constructed once and
the switch takes effect on the next frame without a restart."""

from HeroAI.settings import Settings


class HeroAIEngineRouter:
    def __init__(self, cached_data=None, standalone_fallback: bool = False):
        self.cached_data = cached_data
        self.standalone_fallback = standalone_fallback
        self.legacy_engine = None
        self.bt_engine = None

    def active(self):
        if Settings().get_account_bt_rotation_enabled():
            if self.bt_engine is None:
                from HeroAI.bt import HeroAIBTEngine

                self.bt_engine = HeroAIBTEngine(self.cached_data, standalone_fallback=self.standalone_fallback)
            return self.bt_engine
        if self.legacy_engine is None:
            from Core.Builds.Any.HeroAI import HeroAI_Build

            self.legacy_engine = HeroAI_Build(self.cached_data, standalone_fallback=self.standalone_fallback)
        return self.legacy_engine

    def is_bt_active(self) -> bool:
        return bool(Settings().get_account_bt_rotation_enabled())

    def set_cached_data(self, cached_data) -> None:
        self.cached_data = cached_data
        for engine in (self.legacy_engine, self.bt_engine):
            if engine is not None:
                engine.set_cached_data(cached_data)

    def ProcessOOC(self):
        return self.active().ProcessOOC()

    def ProcessCombat(self):
        return self.active().ProcessCombat()

    def ProcessSkillCasting(self):
        return self.active().ProcessSkillCasting()

    def DidTickSucceed(self) -> bool:
        return self.active().DidTickSucceed()

    def EnsureBuildContract(self, cached_data=None):
        return self.active().EnsureBuildContract(cached_data)

    def GetBuildContract(self):
        return self.active().GetBuildContract()

    def ClearBuildContract(self) -> None:
        for engine in (self.legacy_engine, self.bt_engine):
            if engine is not None:
                engine.ClearBuildContract()

    def ApplyBlockedSkillIDs(self, blocked_skill_ids: list[int] | None = None) -> None:
        self.active().ApplyBlockedSkillIDs(blocked_skill_ids)


def create_heroai_engine(cached_data=None, standalone_fallback: bool = False) -> HeroAIEngineRouter:
    return HeroAIEngineRouter(cached_data, standalone_fallback=standalone_fallback)
