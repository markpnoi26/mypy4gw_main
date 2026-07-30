from __future__ import annotations

from typing import Any, Generator

from .build_src.combat_services import CombatServices
from .py4gwcorelib_src.BehaviorTree import BehaviorTree

BuildCoroutine = Generator[Any, Any, Any]


class BldMgrBT(CombatServices):
    """The base for every build. Sits on CombatServices; the generator
    execution model it replaced is gone.

    Provides build identity/matching (so BuildRegistry can discover and score
    BT builds), the fallback chain, and rotation-tree lifecycle. Subclasses
    override `build_rotation_tree()`; rotations that depend on live state also
    override `current_rotation_signature()` to trigger recompiles.

    CombatServices carries the shared cast/target surface, so the 69 modules
    under Builds/Skills bind through `self.build.<method>` unchanged.

    Fallback handlers are duck-typed, so anything exposing the tick protocol
    can serve as one.
    """

    is_build_type = True

    def __init__(
        self,
        name: str = "Generic BT Build",
        required_primary=None,
        required_secondary=None,
        template_code: str = "AAAAAAAAAAAAAAAA",
        required_skills: list[int] | None = None,
        optional_skills: list[int] | None = None,
        fallback_name: str | None = None,
        fallback_handler: Any = None,
        is_fallback_candidate: bool = False,
        IsFixedBuild: bool = False,
        is_combat_automator_compatible: bool = True,
        is_template_only: bool = False,
    ) -> None:
        from Core import Profession

        self.build_name = name
        self.required_primary = required_primary if required_primary is not None else Profession(0)
        self.required_secondary = required_secondary if required_secondary is not None else Profession(0)
        self.template_code = template_code
        self.required_skills = list(required_skills or [])
        self.optional_skills = list(optional_skills or [])
        self.skills = list(self.required_skills)
        self.minimum_required_match = len(self.required_skills)

        self.default_fallback_name = fallback_name
        self.current_fallback_name = fallback_name
        self.default_fallback_handler = fallback_handler
        self.current_fallback_handler = fallback_handler
        self.is_fallback_candidate = is_fallback_candidate
        self.blocked_skills: list[int] = []

        self.IsFixedBuild = IsFixedBuild
        self.is_combat_automator_compatible = is_combat_automator_compatible
        self.is_template_only = is_template_only

        self.tick_state: bool | None = None
        self.cached_data: Any = None
        self.init_combat_services()

        self.rotation_tree: BehaviorTree | None = None
        self.rotation_signature: Any = None
        self.service_tree: BehaviorTree | None = None
        self.active_generators: dict[str, Any] = {}

    # ---- identity / matching ----

    def set_cached_data(self, cached_data: Any) -> None:
        self.cached_data = cached_data
        self._cached_data = cached_data

    def ValidatePrimary(self, profession) -> bool:
        return self.required_primary == profession

    def ValidateSecondary(self, profession) -> bool:
        return self.required_secondary == profession

    def get_current_skills(self) -> list[int]:
        from Core.Skillbar import SkillBar

        skills: list[int] = []
        for slot in range(8):
            skill_id = SkillBar.GetSkillIDBySlot(slot + 1)
            if skill_id:
                skills.append(skill_id)
        return skills

    def ScoreMatch(
        self,
        current_primary=None,
        current_secondary=None,
        current_skills: list[int] | None = None,
    ) -> int:
        from Core import Player, Agent, Profession

        if current_primary is None or current_secondary is None:
            player_id = Player.GetAgentID()
            primary_value, secondary_value = Agent.GetProfessions(player_id)
            current_primary = current_primary if current_primary is not None else Profession(primary_value)
            current_secondary = current_secondary if current_secondary is not None else Profession(secondary_value)

        if current_skills is None:
            current_skills = self.get_current_skills()

        required_skills = [skill for skill in self.required_skills if skill]
        optional_skills = [skill for skill in self.optional_skills if skill and skill not in required_skills]
        current_skill_set = set(skill for skill in current_skills if skill)

        any_profession = Profession(0)
        primary_matches = self.required_primary in (any_profession, current_primary)
        secondary_matches = self.required_secondary in (any_profession, current_secondary)
        if not self.is_combat_automator_compatible or not primary_matches or not secondary_matches:
            return -1

        required_hits = sum(1 for skill in required_skills if skill in current_skill_set)
        minimum_required_hits = min(self.minimum_required_match, len(required_skills))
        if required_hits < minimum_required_hits:
            return -1

        optional_hits = sum(1 for skill in optional_skills if skill in current_skill_set)
        return required_hits + optional_hits

    def CanProcess(self) -> bool:
        from Core import Agent, Player, Routines

        return (
            Routines.Checks.Map.MapValid()
            and Routines.Checks.Map.IsExplorable()
            and Routines.Checks.Player.CanAct()
            and not Agent.IsDead(Player.GetAgentID())
        )

    # ---- fallback chain ----

    def SetFallback(self, fallback_name: str | None = None, fallback_handler: Any = None) -> None:
        self.current_fallback_name = fallback_name
        self.current_fallback_handler = fallback_handler

    def ResetFallback(self) -> None:
        self.current_fallback_name = self.default_fallback_name
        self.current_fallback_handler = self.default_fallback_handler

    def ResolveFallback(self) -> Any:
        if self.current_fallback_handler is not None:
            self.apply_fallback_skill_mask(self.current_fallback_handler)
            return self.current_fallback_handler
        return None

    def apply_fallback_skill_mask(self, fallback_handler: Any) -> None:
        if fallback_handler is None:
            return
        fallback_handler.ApplyBlockedSkillIDs(self.GetBlockedSkills())

    def SetBlockedSkills(self, skill_ids: list[int] | None = None) -> None:
        self.blocked_skills = [int(skill_id) for skill_id in (skill_ids or []) if int(skill_id) != 0]

    def GetSupportedSkills(self) -> list[int]:
        supported_skills: list[int] = []
        for skill_id in self.required_skills + self.optional_skills:
            skill_id = int(skill_id)
            if skill_id == 0 or skill_id in supported_skills:
                continue
            supported_skills.append(skill_id)
        return supported_skills

    def GetBlockedSkills(self) -> list[int]:
        blocked_skills: list[int] = []
        for skill_id in self.GetSupportedSkills() + self.blocked_skills:
            skill_id = int(skill_id)
            if skill_id == 0 or skill_id in blocked_skills:
                continue
            blocked_skills.append(skill_id)
        return blocked_skills

    def ApplyBlockedSkillIDs(self, blocked_skill_ids: list[int] | None = None) -> None:
        pass

    # ---- tick state (bridge for drivers that still read a bool) ----

    def ResetTickState(self) -> None:
        self.tick_state = None

    def SetTickSuccess(self) -> None:
        self.tick_state = True

    def SetTickFailure(self) -> None:
        self.tick_state = False

    def DidTickSucceed(self) -> bool:
        return self.tick_state is True

    # ---- generator bridge for the shared skill layer ----

    def drive(self, key: str, factory) -> BehaviorTree.NodeState:
        """Tick a Builds/Skills generator one step per frame.

        The skill layer's bodies are synchronous; only the spirit-cast branch of
        CastSkillID actually yields. So an ordinary cast raises StopIteration on
        the first step and resolves same-frame, while a spirit cast reports
        RUNNING until it completes — which is the semantics a tree wants, and it
        needs no change to the 69 shared skill modules.
        """
        generator = self.active_generators.get(key)
        if generator is None:
            generator = factory()
            if not hasattr(generator, "send"):
                return BehaviorTree.NodeState.SUCCESS if generator else BehaviorTree.NodeState.FAILURE
            self.active_generators[key] = generator
        try:
            next(generator)
            return BehaviorTree.NodeState.RUNNING
        except StopIteration as stop:
            self.active_generators.pop(key, None)
            return BehaviorTree.NodeState.SUCCESS if stop.value else BehaviorTree.NodeState.FAILURE
        except Exception:
            self.active_generators.pop(key, None)
            return BehaviorTree.NodeState.FAILURE

    def abandon_generators(self) -> None:
        self.active_generators.clear()

    # ---- rotation tree lifecycle ----

    def build_rotation_tree(self) -> BehaviorTree:
        raise NotImplementedError(f"{type(self).__name__} must override build_rotation_tree()")

    def current_rotation_signature(self) -> Any:
        return None

    def seed_blackboard(self, blackboard: dict) -> None:
        pass

    def current_tree(self) -> BehaviorTree:
        signature = self.current_rotation_signature()
        if self.rotation_tree is None or self.rotation_signature != signature:
            self.rotation_tree = self.build_rotation_tree()
            self.rotation_signature = signature
        return self.rotation_tree

    def reset_rotation_tree(self) -> None:
        if self.rotation_tree is not None:
            self.rotation_tree.reset()
        self.rotation_tree = None
        self.rotation_signature = None
        self.abandon_generators()

    def get_rotation_tree(self) -> BehaviorTree:
        if self.service_tree is None:
            self.service_tree = BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"{self.build_name}:Rotation",
                    action_fn=lambda node: self.tick_rotation(node.blackboard, ooc=None),
                )
            )
        return self.service_tree

    def tick_rotation(self, blackboard: dict, ooc: bool | None) -> BehaviorTree.NodeState:
        tree = self.current_tree()
        if isinstance(blackboard, dict):
            tree.blackboard = blackboard
        if ooc is None:
            ooc = not bool(tree.blackboard.get("in_aggro", False))
        tree.blackboard["ooc"] = ooc
        self.seed_blackboard(tree.blackboard)
        return tree.tick()

    # ---- execution ----

    def run_phase(self, ooc: bool | None) -> BuildCoroutine:
        self.ResetTickState()
        state = self.tick_rotation(self.current_tree().blackboard, ooc)
        if state in (BehaviorTree.NodeState.SUCCESS, BehaviorTree.NodeState.RUNNING):
            self.SetTickSuccess()
            yield
            return
        fallback = self.ResolveFallback()
        if fallback is not None:
            if ooc is True:
                yield from fallback.ProcessOOC()
            elif ooc is False:
                yield from fallback.ProcessCombat()
            else:
                yield from fallback.ProcessSkillCasting()
            return
        self.SetTickFailure()
        yield

    def ProcessSkillCasting(self) -> BuildCoroutine:
        yield from self.run_phase(None)

    def ProcessCombat(self) -> BuildCoroutine:
        yield from self.run_phase(False)

    def ProcessOOC(self) -> BuildCoroutine:
        yield from self.run_phase(True)


BTBuildMgr = BldMgrBT
