"""The HeroAI combat engine.

Exposes the driver protocol headless_tree.py and the widget call
(set_cached_data / ProcessOOC / ProcessCombat / ProcessSkillCasting /
DidTickSucceed / contract methods). The generator-shaped Process* signatures
and the DidTickSucceed side channel are kept because both drivers still
advance a fresh generator once per frame."""

from Core.Agent import Agent
from Core.BldMgrBT import BldMgrBT
from Core.Map import Map
from Core.Player import Player
from Core.Routines import Routines
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree

from .frame_seed import seed_frame


class HeroAIBTEngine(BldMgrBT):
    def __init__(self, cached_data=None, standalone_fallback: bool = False):
        super().__init__(
            name="HeroAI BT",
            template_code="HEROAI_BT",
            IsFixedBuild=True,
        )
        self.cached_data = cached_data
        self.standalone_fallback = standalone_fallback
        self.build_registry = None
        self.contract_signature = None
        self.contract_build = None

    def set_cached_data(self, cached_data) -> None:
        self.cached_data = cached_data

    def get_cached_data(self):
        if self.cached_data is None:
            from HeroAI.cache_data import CacheData

            self.cached_data = CacheData()
        return self.cached_data

    def current_rotation_signature(self):
        primary, secondary = Agent.GetProfessions(Player.GetAgentID())
        current_skills = tuple(int(skill_id) for skill_id in self.get_current_skills())
        return (
            int(Map.GetMapID()),
            int(Map.GetRegion()[0]),
            int(Map.GetDistrict()),
            int(Map.GetLanguage()[0]),
            int(primary),
            int(secondary),
            *current_skills,
        )

    def build_rotation_tree(self) -> BehaviorTree:
        from .rotation import build_rotation_tree

        return build_rotation_tree()

    def seed_blackboard(self, blackboard: dict) -> None:
        seed_frame(blackboard, self.get_cached_data())

    def prepare_tick(self):
        cached_data = self.get_cached_data()
        if not Routines.Checks.Map.MapValid():
            return None
        if not Map.IsExplorable() or Map.IsInCinematic():
            return None
        player_id = Player.GetAgentID()
        if not Agent.IsAlive(player_id) or Agent.IsKnockedDown(player_id):
            return None
        cached_data.Update()
        cached_data.UpdateCombat()
        return cached_data

    def run_engine_phase(self, ooc: bool):
        blackboard = self.current_tree().blackboard
        contract_build = self.EnsureBuildContract()
        if contract_build is not None and contract_build is not self:
            state = contract_build.tick_rotation(blackboard, ooc=ooc)
            if state == BehaviorTree.NodeState.FAILURE:
                state = self.tick_rotation(blackboard, ooc=ooc)
        else:
            state = self.tick_rotation(blackboard, ooc=ooc)

        if state in (BehaviorTree.NodeState.SUCCESS, BehaviorTree.NodeState.RUNNING):
            self.SetTickSuccess()
        else:
            self.SetTickFailure()

    def ProcessOOC(self):
        self.ResetTickState()
        cached_data = self.prepare_tick()
        if cached_data is None or cached_data.data.in_aggro:
            self.SetTickFailure()
            yield
            return
        self.run_engine_phase(ooc=True)
        yield

    def ProcessCombat(self):
        self.ResetTickState()
        cached_data = self.prepare_tick()
        if cached_data is None or not cached_data.data.in_aggro:
            self.SetTickFailure()
            yield
            return
        self.run_engine_phase(ooc=False)
        yield

    def ProcessSkillCasting(self):
        self.ResetTickState()
        cached_data = self.prepare_tick()
        if cached_data is None:
            self.SetTickFailure()
            yield
            return
        if cached_data.data.in_aggro:
            yield from self.ProcessCombat()
        else:
            yield from self.ProcessOOC()

    def matchable_bt_builds(self):
        """Registry builds that are BldMgrBT-native. Legacy BuildMgr builds are
        deliberately invisible here: the BT path takes no generator dependency,
        so a build must be ported to become reachable under this engine.

        BTBuilds/FarmBuilds is excluded by _iter_matchable_builds; re-checked
        here so the rule survives a change to the registry's filters."""
        from Core.BuildMgr import BuildRegistry, is_purpose_specific_build

        if self.build_registry is None:
            self.build_registry = BuildRegistry(default_fallback_name=self.build_name)
        for build in self.build_registry._iter_matchable_builds():
            if build is self or is_purpose_specific_build(build):
                continue
            if hasattr(build, "tick_rotation") and hasattr(build, "build_rotation_tree"):
                yield build

    def EnsureBuildContract(self, cached_data=None):
        if cached_data is not None:
            self.set_cached_data(cached_data)

        if not Map.IsExplorable():
            self.contract_signature = None
            self.contract_build = None
            return None

        signature = self.current_rotation_signature()
        if self.contract_build is not None and self.contract_signature == signature:
            return self.contract_build

        if self.standalone_fallback:
            self.contract_signature = signature
            self.contract_build = self
            return self

        from Core import Profession

        primary_value, secondary_value = Agent.GetProfessions(Player.GetAgentID())
        current_primary = Profession(primary_value)
        current_secondary = Profession(secondary_value)
        current_skills = self.get_current_skills()

        resolved = self
        best_score = 0
        for build in self.matchable_bt_builds():
            score = build.ScoreMatch(
                current_primary=current_primary,
                current_secondary=current_secondary,
                current_skills=current_skills,
            )
            if score > best_score:
                best_score = score
                resolved = build

        if resolved is not self:
            resolved.set_cached_data(self.get_cached_data())

        self.contract_signature = signature
        self.contract_build = resolved
        return resolved

    def GetBuildContract(self):
        return self.contract_build

    def ClearBuildContract(self) -> None:
        self.contract_signature = None
        self.contract_build = None
        self.reset_rotation_tree()

    def ApplyBlockedSkillIDs(self, blocked_skill_ids: list[int] | None = None) -> None:
        combat_handler = getattr(self.get_cached_data(), "combat_handler", None)
        if combat_handler is not None and hasattr(combat_handler, "ApplyBlockedSkillIDs"):
            combat_handler.ApplyBlockedSkillIDs(blocked_skill_ids)
