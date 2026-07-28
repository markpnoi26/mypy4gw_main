# Porting builds to BldMgrBT

Supersedes `buildmgr_retirement_blueprint.md` (that doc assumed deleting
`BuildMgr`; the plan is now coexistence — `BuildMgr` keeps serving the legacy
engine and the skill-module base, while builds port to `BldMgrBT`).

Under the BT engine, contract resolution matches **only** `BldMgrBT` builds
(`HeroAI/bt/bt_engine.py: matchable_bt_builds`). A build that has not been
ported is invisible to it — no generator bridge exists by design. Porting is
therefore what makes a build reachable under the BT toggle.

---

## 1. The finding that makes this tractable

The generator layer is three levels of ceremony around straight-line code:

```
Martyr._run_local_skill_logic                         Builds/Monk/Mo_Any/Martyr.py:172
  └ yield from skills.Monk.HealingPrayers.Dwaynas_Kiss()   body 100% synchronous;
                                                            only the terminal cast yields
      └ yield from build.CastSkillIDAndRestoreTarget(...)   yields ONLY to delegate
          └ yield from build.CastSkillID(...)               synchronous EXCEPT the
                                                            spirit branch
```

Verified:

- `CanCastSkillID:1693` — zero yields.
- `CastSkillID:1785` — an `if False: yield` marker at `:1794` plus exactly two real
  yields, both inside the spirit branch (`_get_spirit_cast_wait_ms`,
  `_wait_for_spirit_spawn_and_step_away`).
- `CastSkillIDAndRestoreTarget:1859` — yields only to delegate to `CastSkillID`
  and `RestoreEnemyTarget`.
- `HealingPrayers.Dwaynas_Kiss:52` — target resolution and `IsSkillEquipped` are
  plain calls; the single `yield from` is the terminal cast.

**Consequence:** make the cast API synchronous at the bottom and the `yield from`
deletions cascade upward through all 69 skill modules and all 45 builds. One root
change, then mechanical edits. Spirit casting is the only genuine multi-frame
behavior and becomes a tier 3 subtree (`HeroAI/bt/skill_subtrees.py`).

## 2. Ported builds work under BOTH engines

`BldMgrBT` implements `ProcessCombat` / `ProcessOOC` / `ResetTickState` /
`tick_state`, which is exactly what `HeroAI_Build._run_contract:162` calls when
it delegates to a matched build. And `BuildRegistry._scan_build_types` now
discovers builds via the `is_build_type` marker rather than
`issubclass(BuildMgr)`, so BT builds appear in the registry for both engines.

So a ported build is reachable from:

| Engine | Path |
|---|---|
| legacy | `HeroAI_Build` → `yield from build.ProcessCombat()` → `run_phase` → `tick_rotation` |
| BT | `HeroAIBTEngine` → `build.tick_rotation(bb, ooc)` directly |

**Porting a build does not break it for accounts still on legacy.** That is what
makes incremental migration safe — port one, verify on both, move on.

## 2b. Where a ported build goes

```
Py4GWCoreLib/BTBuilds/                 general combat builds — matchable
  Monk/Mo_Any/  Mesmer/  Necromancer/  ...
  nodes.py                             authoring helpers

Py4GWCoreLib/BTBuilds/FarmBuilds/      purpose-specific — NEVER matchable
  Dervish/  Assassin/  ...
```

`BuildRegistry._iter_matchable_builds` drops anything whose module lives under
`Py4GWCoreLib.BTBuilds.FarmBuilds` (`is_purpose_specific_build`), so neither the
BT engine nor the legacy engine can auto-select it. Exclusion is **by location,
not by flag** — there is no `is_combat_automator_compatible=False` to forget.
`HeroAIBTEngine.matchable_bt_builds` re-checks it so the rule survives a change
to the registry's filters.

Farm builds are reached only by explicit instantiation from a script or by
`bot.AddBuild(...)`.

Decision rule: does HeroAI, seeing a matching skillbar on some account, want to
run this? If no — one farm route, one boss, one chest run — it goes in
`FarmBuilds`.

## 3. Port order (bottom-up, because of the cascade)

### Step 1 — synchronous cast API

`Py4GWCoreLib/build_src/casting.py`: `CanCastSkillID`, `CastSkillID`,
`CastSkillIDAndRestoreTarget`, `CastSkillSlot` — bodies lifted from `BuildMgr`
minus `yield from` and the `if False: yield` markers. Spirit casting is not
ported; it becomes a subtree factory.

`BuildMgr` keeps its generator versions untouched. The two coexist under
different module paths, so nothing legacy changes.

### Step 2 — skill layer (69 modules)

`SkillsTemplate` (`Builds/Skills/SkillsTemplate.py:18`) currently subclasses
`BuildMgr`. It needs a variant whose `self.build` is a `BldMgrBT`. The ~330
paradigm-agnostic call sites (`IsSkillEquipped` 130, `IsInAggro`/`IsCloseToAggro`
68, `GetCustomSkill` 36, `Resolve*AllyTarget` 34, `GetPartyHealthDelta` 12) need
those methods to exist on `BldMgrBT`.

This is the point at which `CombatServices` extraction becomes necessary — the
deferred work from the earlier analysis. Extract those ~1000 lines from
`BuildMgr` into `build_src/combat_services.py` as a mixin, then
`BuildMgr(CombatServices)` and `BldMgrBT(CombatServices)`. Pure move, zero
behavior change, verifiable by "nothing broke".

Only the ~148 cast call sites need editing, and mostly by deleting `yield from`.

### Step 3 — builds (45)

Ladder → Selector. See §4.

Order by risk: solo/farm builds first (fixed bars, no party targeting), party
support builds last.

## 4. The port pattern

`Martyr:172` is the representative case. A priority ladder of
`if cond and (yield from cast): return True` **is** a Selector of Sequences.

Before:

```python
class Martyr(BuildMgr):
    def __init__(self, match_only: bool = False):
        super().__init__(name="Martyr", required_primary=Profession.Monk, ...)
        if match_only:
            return
        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.SetSkillCastingFn(self._run_local_skill_logic)
        self.skills: SkillsTemplate = SkillsTemplate(self)

    def _run_local_skill_logic(self):
        if not Routines.Checks.Skills.CanCast():
            return False
        snapshot = self._get_required_support_snapshot()
        if not snapshot.any_required_support_needed:
            return False
        player_energy_pct = float(Agent.GetEnergy(Player.GetAgentID()))

        if snapshot.martyr_target_id and (yield from self.CastSkillIDAndRestoreTarget(
                Martyr_ID, snapshot.martyr_target_id, aftercast_delay=100)):
            return True
        if (yield from self.skills.Monk.NoAttribute.Remove_Hex(min_priority=HexRemovalPriority.HIGH)):
            return True
        if snapshot.dwaynas_kiss_needed and (yield from self.skills.Monk.HealingPrayers.Dwaynas_Kiss()):
            return True
        if player_energy_pct >= 0.50 and (yield from self.skills.Monk.NoAttribute.Remove_Hex(
                min_priority=HexRemovalPriority.MEDIUM)):
            return True
        ...
        return False
```

After:

```python
class Martyr(BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(name="Martyr", required_primary=Profession.Monk, ...)
        if match_only:
            return
        self.SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))
        self.skills = SkillsTemplate(self)

    def build_rotation_tree(self) -> BehaviorTree:
        return BehaviorTree(selector("Martyr",
            gate("CanCast",  lambda: Routines.Checks.Skills.CanCast()),
            rung("Martyr",   lambda n: n.blackboard["snapshot"].martyr_target_id,
                             lambda n: self.CastSkillIDAndRestoreTarget(
                                 Martyr_ID, n.blackboard["snapshot"].martyr_target_id,
                                 aftercast_delay=100)),
            step("RemoveHexHigh",
                             lambda: self.skills.Monk.NoAttribute.Remove_Hex(
                                 min_priority=HexRemovalPriority.HIGH)),
            rung("DwaynasKiss", lambda n: n.blackboard["snapshot"].dwaynas_kiss_needed,
                             lambda: self.skills.Monk.HealingPrayers.Dwaynas_Kiss()),
            rung("RemoveHexMed", lambda n: n.blackboard["player_energy"] >= 0.50,
                             lambda: self.skills.Monk.NoAttribute.Remove_Hex(
                                 min_priority=HexRemovalPriority.MEDIUM)),
            ...
        ))

    def seed_blackboard(self, blackboard: dict) -> None:
        blackboard["snapshot"] = self.get_required_support_snapshot()
        blackboard["player_energy"] = float(Agent.GetEnergy(Player.GetAgentID()))
```

Mapping:

| Before | After |
|---|---|
| `SetSkillCastingFn(fn)` | `build_rotation_tree()` |
| ladder of `if ...: return True` | `Selector` — first rung that succeeds wins |
| `if cond and (yield from cast)` | `Sequence(Condition(cond), Condition(cast))` |
| early `return False` guards | leading `Condition` in the root Sequence, or a gate rung |
| locals computed once at top (`snapshot`, `player_energy_pct`) | `seed_blackboard()` — computed once per tick, read by every rung |
| `yield from <skill call>` | plain call (after step 1/2) |

**Use `ConditionNode` for rungs, not `ActionNode`.** `ActionNode`
(`BehaviorTree.py:447-486`) latches: it returns RUNNING on the tick the action
runs and delivers the result on the *next* tick. That halves rotation cadence.
`ConditionNode` evaluates and returns same-frame. Same reasoning as
`HeroAI/bt/rotation.py`.

## 5. Traps

- **`match_only` early return.** Every build's `__init__` has
  `if match_only: return` before wiring. Preserve it — `BuildRegistry`
  instantiates builds in match-only mode for scoring, and `_call_build_ctor:2078`
  swallows `TypeError` and silently retries without kwargs, so a broken
  signature shows up as "build never matches" rather than an exception.
- **Locals hoisted to the blackboard must stay per-tick.** `snapshot` and
  `player_energy_pct` were computed once per ladder execution. Put them in
  `seed_blackboard()`, which runs once per `tick_rotation`. Recomputing them per
  rung is both slower and a behavior change (values could shift mid-ladder).
- **`self.skills` name collision.** `BldMgrBT.__init__` sets `self.skills` from
  `required_skills` (a list of ints). `Martyr` overwrites it with a
  `SkillsTemplate`. That is pre-existing behavior in `BuildMgr` too, but be
  aware `ScoreMatch` reads `self.required_skills`, not `self.skills`, so matching
  is unaffected.
- **Fallback still points at legacy.** `SetFallback("HeroAI",
  HeroAI_Build(standalone_fallback=True))` is duck-typed and keeps working —
  `run_phase` calls `fallback.ProcessOOC()/ProcessCombat()` on it. A ported build
  falling back to a legacy build is fine and intended during migration.
- **Spirit casts do not port directly.** Any build calling `CastSpiritSkillID`
  (6 sites) needs its spirit step registered as a tier 3 subtree instead.
- **`is_combat_automator_compatible=False` excludes a build from matching**
  entirely (`_iter_matchable_builds:2140`). `DervBoneFarmer` sets this, which is
  why it is `AddBuild`-only and not a contract candidate.

## 6. Verification per build

1. Port, keep the toggle OFF. The build must still match and run identically via
   the legacy path (`HeroAI_Build` → `ProcessCombat` → `run_phase`).
2. Toggle ON for one account. The build should now match via
   `matchable_bt_builds` and tick natively.
3. Compare cast order and cadence against the legacy account in the same party —
   cadence regressions almost always mean an `ActionNode` where a
   `ConditionNode` belonged.
