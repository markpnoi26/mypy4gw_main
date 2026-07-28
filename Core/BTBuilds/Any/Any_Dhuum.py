"""BT port of Builds/Any/Any_Dhuum.py.

`_DhuumModeTracker` (~180 lines of Reaper detection and shared-mode debouncing)
is imported from the legacy module rather than duplicated. It holds class-level
shared state, so a second copy would split the mode between engines and could
desync Dhuum's Rest / Ghostly Fury across a party running both.

The mode flags are read once per tick into the blackboard, matching the legacy
ladder which computed them once per pass.
"""

from Core import BldMgrBT, Profession, Routines, Skill
from Core.Builds.Any.Any_Dhuum import _DhuumModeTracker
from Core.Builds.Skills.any.PvE import PvE
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ..nodes import cast, cond, guarded_cast, rotation_tree


def resolve_skill_id(names: tuple[str, ...], fallback: int = 0) -> int:
    for name in names:
        try:
            skill_id = int(Skill.GetID(name))
        except Exception:
            skill_id = 0
        if skill_id > 0:
            return skill_id
    return int(fallback)


class Any_Dhuum(BldMgrBT):
    """BT adaptation of the Dhuum utility build."""

    TEMPLATE_CODE = "OQBDAqwDSPwQwRwSwTwAAAAAAA"

    def __init__(self, match_only: bool = False):
        self.dhuums_rest_id = resolve_skill_id(("Dhuum's_Rest",), fallback=3087)
        self.spiritual_healing_id = resolve_skill_id(("Spiritual_Healing",), fallback=3088)
        self.encase_skeletal_id = resolve_skill_id(("Encase_Skeletal",), fallback=3089)
        self.reversal_of_death_id = resolve_skill_id(("Reversal_of_Death",), fallback=3090)
        self.ghostly_fury_id = resolve_skill_id(("Ghostly_Fury",), fallback=3136)

        required_candidates = [
            self.dhuums_rest_id,
            self.spiritual_healing_id,
            self.reversal_of_death_id,
            self.ghostly_fury_id,
        ]
        required_skills = [sid for sid in required_candidates if sid > 0]

        optional_candidates = [self.encase_skeletal_id]
        optional_skills = [sid for sid in optional_candidates if sid > 0 and sid not in required_skills]

        super().__init__(
            name="Any Dhuum",
            required_primary=Profession(0),
            required_secondary=Profession(0),
            template_code=self.TEMPLATE_CODE,
            required_skills=required_skills,
            optional_skills=optional_skills,
        )

        self.minimum_required_match = 1

        if match_only:
            return

        if self.dhuums_rest_id > 0:
            _DhuumModeTracker._dhuums_rest_skill_ids.add(self.dhuums_rest_id)
        if self.ghostly_fury_id > 0:
            _DhuumModeTracker._ghostly_fury_skill_ids.add(self.ghostly_fury_id)
        _DhuumModeTracker._ensure_timers()

        self.pve = PvE(self)
        self.SetFallback("HeroAI", HeroAIBTEngine(standalone_fallback=True))

    def seed_blackboard(self, blackboard: dict) -> None:
        drest_mode = _DhuumModeTracker.is_dhuums_rest_mode()
        fury_mode = _DhuumModeTracker.is_ghostly_fury_mode()
        # No Reaper activity detected (e.g. the Dhuum fight itself, where no
        # Reapers are present) defaults Dhuum's Rest to active.
        no_mode = _DhuumModeTracker._shared_mode is None
        blackboard["dhuum_rest_active"] = bool(drest_mode or no_mode)
        blackboard["dhuum_fury_active"] = bool(fury_mode)

    def rest_active(self, node) -> bool:
        return bool(node.blackboard.get("dhuum_rest_active", False))

    def fury_active(self, node) -> bool:
        return bool(node.blackboard.get("dhuum_fury_active", False))

    def build_rotation_tree(self) -> BehaviorTree:
        return rotation_tree(
            "AnyDhuum",
            [cond("CanCast", lambda: Routines.Checks.Skills.CanCast())],
            [
                cast(
                    self,
                    "DhuumsRest",
                    lambda: self.pve.Dhuums_Rest(
                        is_active=bool(self.current_tree().blackboard.get("dhuum_rest_active", False))
                    ),
                ),
                cast(
                    self,
                    "GhostlyFury",
                    lambda: self.pve.Ghostly_Fury(
                        is_active=bool(self.current_tree().blackboard.get("dhuum_fury_active", False))
                    ),
                ),
                cast(self, "ReversalOfDeath", lambda: self.pve.Reversal_of_Death()),
                cast(self, "SpiritualHealing", lambda: self.pve.Spiritual_Healing()),
            ],
        )
