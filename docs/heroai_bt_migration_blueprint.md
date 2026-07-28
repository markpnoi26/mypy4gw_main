# HeroAI BT Migration Blueprint

Blueprint for the new BT-native HeroAI combat engine, built side by side with the
legacy path. The legacy path stays byte-identical and selectable at runtime.

## The invariant

Nothing in this migration edits legacy decision code. `CombatClass.HandleCombat`,
`FindCastableSkill`, `AreCastConditionsMet`, and `HeroAI_Build`'s generators keep
working exactly as they do today, selected per account. The new engine is a second
vertical stack behind one seam.

---

## 1. What we are migrating away from

| Thing | Where | Why it goes |
|---|---|---|
| `next(ProcessCombat(), None)` bridge | `headless_tree.py:158, :173`, widget `:110, :126` | Builds a fresh generator each frame, advances once, discards it. `BuildMgr`'s coroutine protocol is inert: `yield from wait(250)` never waits, delegated multi-yield rotations restart every frame |
| bool-returning `_handle_combat` / `_handle_out_of_combat` | `headless_tree.py:142-174` | Two states only. Cannot say RUNNING, so casting state was displaced into a sibling guard |
| `IsCasting` sibling guard | `headless_tree.py:325`, inline check `:154` | Holds the RUNNING state that belongs to the cast node itself. Double-gates once the rotation can return RUNNING |
| Side-channel result flag | `tick_state` + `DidTickSucceed()` round-trip | BT results flow through return values, not mutable fields interrogated after the fact |
| `FindCastableSkill` flat loop | `combat.py:1840` | Replaced by a Selector over slot branches (same first-castable-wins semantics, but observable) |
| 61-branch `if skill_id ==` ladder | `combat.py:1025` inside `AreCastConditionsMet` | Replaced by a registry keyed by skill_id (legacy copy untouched) |
| Duplicate outer tree | widget `HeroAI.py:355` vs `headless_tree.py:271` | Collapsed into one factory both entries import |

## 2. What stays untouched (both engines depend on it)

| Component | Where | Role |
|---|---|---|
| Executor: `UseSkill` + `aftercast_timer` + spike lock + skill lock | `combat.py:2017` tail, `:606-664`, `:1934-2010` | Multibox spike coordination rides on this. Neither engine replaces it |
| `GetAppropiateTarget` | `combat.py:738` (235 ln) | Target resolution by allegiance/strictness. Reused by the new engine as a called function |
| `IsReadyToCast` gate prefix | `combat.py:1552-1620` | Recharge, adrenaline, energy + expertise, Vow of Silence, shout suppression. Pure function of skillbar + player |
| `IsSkillReady` | `combat.py:483` | recharge / enabled / blocked |
| `PrioritizeSkills` | `combat.py:338` | SkillNature ordering (CustomA → Interrupt → ... → Resurrection) |
| `targeting.py` | 535 ln | Lowest-ally selectors, already perf-tuned |
| `CastConditions` schema + all skill definition values | `custom_skill_src/skill_types.py:7`, 12 profession files | Already the declarative data model |
| `BuildRegistry` contract scoring | `Builds/Any/HeroAI.py:100` | Which build matches the bar — engine-agnostic |
| `CacheData` | `cache_data.py:89` | 6000+ lines of UI read it directly. Survives as config/identity store |
| ShMem mirror + Whiteboard locks | `GlobalCache/SharedMemory.py`, `combat.py:606` | Cross-account substrate for both engines |

---

## 3. The seam

`headless_tree.py:30` already takes `heroai_build` as an injectable constructor
parameter. Nobody uses it. That is the plug-in point.

```python
# HeroAI/engine.py  (new)
def create_heroai_engine(cached_data, standalone_fallback: bool = False):
    if Settings().get_account_rotation_engine() == "bt":
        return HeroAIBTEngine(cached_data, standalone_fallback=standalone_fallback)
    return HeroAI_Build(cached_data, standalone_fallback=standalone_fallback)
```

Three edits wire it:

1. `headless_tree.py:32` → `self.heroai_build = heroai_build or create_heroai_engine(self.cached_data)`
2. Widget `HeroAI.py:39` → `heroai_build = create_heroai_engine(cached_data)`
3. New per-account setting following the `settings.py:429/435` pattern:
   `get_account_rotation_engine()` / `set_account_rotation_engine()`, values `"legacy" | "bt"`.

Both drivers touch exactly this surface (verified against every call site):

```python
class HeroAIEngine(Protocol):
    def set_cached_data(self, cached_data) -> None: ...
    def ProcessOOC(self): ...                 # generator, legacy compat
    def ProcessCombat(self): ...              # generator, legacy compat
    def DidTickSucceed(self) -> bool: ...     # ticks.py:173 reads this
    def EnsureBuildContract(self, cached_data=None): ...
    def GetBuildContract(self): ...
    def ClearBuildContract(self) -> None: ...
    def ApplyBlockedSkillIDs(self, ids: list[int] | None = None) -> None: ...
```

`HeroAI_Build` satisfies all eight today with zero changes. `HeroAIBTEngine`
implements the generator methods as two-line shims (`tick tree, yield`) so the
drivers do not change while legacy exists.

Fallback construction sites (~10 builds hardcode
`SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))` — DervBoneFarmer:105,
Any_Dhuum:261, ShadowTheftDaggerSpammer:59, BuildTemplate:98/221, others): leave
hardcoded in phase 1-4. Fallback is always legacy until the new engine is trusted,
then route through the factory in one pass.

---

## 4. BldMgrBT base class

`Py4GWCoreLib/BldMgrBT.py`, replacing `BTBuildMgr.py` (alias kept:
`BTBuildMgr = BldMgrBT`; only 3 references exist — `__init__.py:124`,
`upkeep.py:8`, `DervBoneFarmer.py:7/81`).

Fixes two live defects:

- **Casing bug**: `BTBuildMgr.py:39` defines `process_skill_casting` (snake_case);
  the real hook is `ProcessSkillCasting` (`BuildMgr.py:1969`). Nothing calls it.
  Any BT build driven through the pipeline today hits `NotImplementedError`
  (`BuildMgr.py:1973`), which is why DervBoneFarmer is `AddBuild()`-only.
- **Service freeze**: `planner.py:198` calls the registered builder once and stores
  the tree forever. HeroAI needs recompile-on-skillbar-change, so the service
  handle must be a stable wrapper whose inner tree is free to swap.

```python
class BldMgrBT(BuildMgr):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rotation_tree: BehaviorTree | None = None
        self.rotation_signature = None
        self.service_tree: BehaviorTree | None = None

    def build_rotation_tree(self) -> BehaviorTree:
        raise NotImplementedError(f"{type(self).__name__} must override build_rotation_tree()")

    def current_rotation_signature(self):
        return None            # constant -> compiled once (custom bots)

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
        tree.blackboard = blackboard
        blackboard["ooc"] = ooc
        self.seed_blackboard(blackboard)
        return tree.tick()

    def ProcessSkillCasting(self) -> BuildCoroutine:
        yield from self.run_phase(None)

    def ProcessCombat(self) -> BuildCoroutine:
        yield from self.run_phase(False)

    def ProcessOOC(self) -> BuildCoroutine:
        yield from self.run_phase(True)

    def run_phase(self, ooc: bool | None) -> BuildCoroutine:
        self.ResetTickState()
        board = self.current_tree().blackboard
        state = self.tick_rotation(board, ooc)
        if state == BehaviorTree.NodeState.SUCCESS:
            self.SetTickSuccess()
            yield
            return
        if state == BehaviorTree.NodeState.RUNNING:
            self.SetTickSuccess()
            yield
            return
        fallback = self.ResolveFallback()
        if fallback is not None:
            if ooc is True:     yield from fallback.ProcessOOC()
            elif ooc is False:  yield from fallback.ProcessCombat()
            else:               yield from fallback.ProcessSkillCasting()
            return
        self.SetTickFailure()
        yield
```

Inherited for free from `BuildMgr`: `ResolveFallback`, `ApplyBlockedSkillIDs`,
whiteboard methods (`_is_whiteboard_skill:1584`, `_whiteboard_is_claimed:1605`,
`_whiteboard_post_intent:1635`, `_whiteboard_owner_self_clear:1673`).

Contract resolution stays OUT of this base. It is HeroAI's resolver role, not
something every custom bot should inherit.

Custom bots (DervBoneFarmer et al.) change only their parent class name. Their
hand-written `build_rotation_tree` bodies are untouched, and the casing fix makes
them drivable through the normal pipeline for the first time.

---

## 5. The new outer tree

One factory, `HeroAI/bt/outer_tree.py`, imported by both the headless tree and the
widget. Kills the `:355` duplicate.

```
Sequence Main
  ├ Guard: IsAlive / DistanceSafe / NotKnockedDown        (unchanged shape)
  ├ Action SeedFrame                                       (new — see §7)
  └ Selector
       ├ Looting
       ├ Sequence OutOfCombat
       │    ├ Condition CombatEnabled        (account_options.Combat)
       │    ├ Condition NotInAggro           (bb["in_aggro"] is False)
       │    ├ Condition NotFollowRecovery
       │    └ Action    Rotation ooc=True    → engine subtree → NodeState
       ├ Follow
       └ Sequence Combat
            ├ Condition CombatEnabled
            ├ Condition InAggro
            ├ Condition NotFollowRecovery
            └ Action    Rotation ooc=False   → engine subtree → NodeState
```

Deltas from today:

- The `IsCasting` guard (`:325`) and the inline `InCastingRoutine` check (`:154`)
  are deleted. RUNNING now lives in the Rotation node: while a cast + aftercast is
  in flight it returns RUNNING and the Selector naturally holds.
- `IsHeadlessCombatPauseActive` (`cache_data.py:219`) — which is just
  `in_aggro or local_in_aggro`, not a pause — becomes the single `bb["in_aggro"]`
  key read by both Sequences instead of being tested in opposite directions in two
  private methods.
- Guard conditions that lived inline in `_handle_combat`/`_handle_out_of_combat`
  (`options.Combat`, follow recovery) are hoisted into named ConditionNodes.

`ticks.py:173` keeps working: `HEROAI_SUCCESS` maps from the Rotation node's last
state (SUCCESS or RUNNING → True).

---

## 6. Rotation: build detection, then generic engine

The Rotation node dispatches on the contract resolved once per frame in SeedFrame:

```
Rotation (ooc flag from parent Sequence)
  └ Switch on bb["contract_build"]
       ├ matched BldMgrBT build → build.tick_rotation(bb, ooc)   (its own tree)
       ├ matched legacy build   → not reachable here: engine="bt" resolves
       │                          against BldMgrBT builds only (see §6a)
       └ no match               → generic rotation tree (below)
```

### 6a. Contract resolution

`HeroAIBTEngine` holds a `BuildContractResolver` — the `BuildRegistry` scoring
logic extracted from `HeroAI_Build:68-120` into a plain object both engines can
hold rather than be. Signature (map + region + district + language + professions
+ skill ids) is the invalidation key, identical to `HeroAI_Build:48`. Resolved
once per map/bar change, cached, parked on the blackboard each frame.

In the "bt" engine the resolver scores only builds that subclass `BldMgrBT`.
Legacy generator builds are invisible to it by design: the BT stack contains only
BT. An account that needs a legacy-only build runs `engine="legacy"`.

### 6b. Generic rotation tree — the recommendation on per-skill BTs

**Do not build a BT per skill in the game.** `CustomSkillClass.MaxSkillData` is
3433. A per-skill tree means 3433 factories whose nodes are almost all pure
booleans evaluated inside one frame — no RUNNING, no sequencing, no reset
semantics. That is predicate work wearing node costumes, and it pays node
allocation plus `_propagate_blackboard` (`BehaviorTree.py:2045`) walking every
node every tick, in the path `docs/heroai_combat_handover.md` flags as
perf-critical. The gain is zero: boolean algebra does not need a scheduler.

Instead, three tiers. Structure in the tree, conditions as data, escape hatches
in code:

**Tier 1 — declarative conditions (covers nearly every skill).**
The ~50 `CastConditions` fields become a predicate lookup table. One entry per
field, not per skill:

```python
# HeroAI/bt/condition_table.py
CONDITION_TABLE = {
    "LessLife":     lambda c, bb: bb["target_health"] < c.LessLife,
    "MoreLife":     lambda c, bb: bb["target_health"] > c.MoreLife,
    "HasHex":       lambda c, bb: bb["target_hexed"],
    "EnemyCount":   lambda c, bb: bb["enemies_in_range"] >= c.EnemyCount,
    "RequireWeapon": lambda c, bb: bb["weapon_name"] == c.RequireWeapon,
    # ... one entry per CastConditions field
}

def evaluate_conditions(skill, bb) -> tuple[bool, str]:
    conditions = skill.custom_skill_data.Conditions
    for field_name, predicate in active_entries(conditions):
        if not predicate(conditions, bb):
            return False, field_name          # name of the first failing check
    return True, ""
```

The skill definitions in `custom_skill_src/*.py` are the data feeding this table,
unchanged. The returned failing-field name is what the debug panel shows.

**Tier 2 — the 61 UniqueProperty skills.**
A registry keyed by skill_id, replacing the `if skill_id ==` ladder. The logic
inside each branch is correct today; only the dispatch changes. Each entry is a
plain predicate, individually testable:

```python
# HeroAI/bt/unique_skills.py
UNIQUE_SKILLS = {
    SkillID.Ether_Feast:   lambda c, bb: bb["player_health"] < c.LessLife,
    SkillID.Energy_Drain:  lambda c, bb: bb["player_energy_valid"] and bb["player_energy"] < c.LessEnergy,
    SkillID.Essence_Strike: lambda c, bb: bb["player_energy"] < c.LessEnergy and bb["nearest_spirit_id"] != 0,
    # ... 61 entries, ported branch by branch from combat.py:1038-1543
}
```

**Tier 3 — per-skill BT subtrees, opt-in, rare.**
The only legitimate per-skill BT: skills whose execution genuinely spans frames —
weapon-swap-then-cast, wait-for-adrenaline-confirm, multi-step combos. This is
DervBoneFarmer's territory generalized. A registry of subtree factories, empty at
launch, populated only when a skill proves it needs RUNNING:

```python
# HeroAI/bt/skill_subtrees.py
SKILL_SUBTREES: dict[int, Callable[[], BehaviorTree]] = {}
```

The generic rotation tree, compiled per skillbar signature:

```
Selector "HeroAI_Rotation"                    (priority order from PrioritizeSkills)
  ├ Sequence "Slot0:<skill name>"
  │    ├ Condition SkillReady                 → combat.py:483 (reused, called)
  │    ├ Condition PhaseAllows                → bb["ooc"] vs IsOOCSkill
  │    ├ Action    ResolveTarget              → GetAppropiateTarget → bb["target"]
  │    ├ Condition NotClaimed                 → only if _is_whiteboard_skill
  │    ├ Condition Conditions                 → tier 1 + tier 2 evaluation
  │    └ Action    Cast                       → tier 3 subtree if registered,
  │                                             else UseSkill + locks + PostIntent;
  │                                             returns RUNNING until aftercast done
  ├ ... slots 1-7
  └ Action AutoAttack                          (combat only, FAILURE when ooc)
```

Eight slot branches of six nodes each, not 3433 trees. First-castable-wins is
preserved by Selector semantics. The Cast node owns RUNNING: it returns RUNNING
from cast start until aftercast expiry, which is what replaces
`in_casting_routine` + the deleted `IsCasting` guard.

### 6c. Ready-gate reuse

`IsSkillReady` and the `IsReadyToCast` prefix are called as functions from the
SkillReady/Conditions nodes — not reimplemented, not extracted yet. If the shared
`CombatClass` mutable state bothers us later (it sets `in_casting_routine` as a
side effect), extraction into pure functions is a follow-up, not a blocker: the
"bt" engine simply never reads that flag.

---

## 7. Channels: BB / ShMem / WB

| Channel | Scope | Rule |
|---|---|---|
| Blackboard | this account, this frame | Read/write freely. Never carries anything another account must see |
| ShMem mirror | all accounts | Read once per frame in SeedFrame → parked on BB. Nodes read the mirror, never ShMem directly |
| WB registry (`GlobalCache/Whiteboard.py`) | this process | `_is_whiteboard_skill` — import-time lookup, free to call at tree-compile time |
| WB locks (`ShMem.PostLock`) | all accounts | Claim-check after target resolution, before cast. Post intent on cast. Cannot be faked on BB |

SeedFrame writes, once per tick:

```
bb["in_aggro"]           ← data.in_aggro or data.local_in_aggro
bb["party_position"]     ← cache
bb["is_leader"]          ← cache
bb["player_health"]      ← Agent.GetHealth (once, not per slot)
bb["player_energy"]      ← GetEnergyValues (once)
bb["weapon_name"]        ← cache
bb["enemies_in_range"]   ← one scan
bb["nearest_spirit_id"]  ← one scan
bb["lowest_ally"]        ← targeting.py selector (once)
bb["contract_build"]     ← resolver, cached on signature
bb["claims"]             ← WB lock snapshot for this frame's candidate targets
```

Per-target facts (`target_health`, `target_hexed`, `target_effects`) are written
by ResolveTarget for the slot's resolved target, keyed simply (`bb["target"]`,
overwritten per slot — slots run sequentially inside one tick).

This is the frame-cache the combat handover doc asks for: the same fact is never
computed twice in one frame, and eight slots produce at most one ShMem read per
fact instead of eight.

CacheData boundary (unchanged from the analysis): config/identity/UI state stays
on CacheData; per-frame derived facts live on the BB; `in_casting_routine` and
`aftercast` gating are absorbed by RUNNING; `stay_alert_timer` stays on CacheData
(game-feel, not control flow).

---

## 8. File map

New files — everything BT-native lives under `HeroAI/bt/`:

```
Py4GWCoreLib/BldMgrBT.py            base class (§4); BTBuildMgr aliased to it
HeroAI/engine.py                    create_heroai_engine seam (§3)
HeroAI/bt/__init__.py
HeroAI/bt/outer_tree.py             shared outer tree factory (§5)
HeroAI/bt/bt_engine.py              HeroAIBTEngine(BldMgrBT) + generator shims
HeroAI/bt/contract_resolver.py      BuildContractResolver extracted scoring (§6a)
HeroAI/bt/rotation.py               generic rotation tree compiler (§6b)
HeroAI/bt/condition_table.py        tier 1 predicate table
HeroAI/bt/unique_skills.py          tier 2 registry, 61 entries
HeroAI/bt/skill_subtrees.py         tier 3 registry, empty at launch
HeroAI/bt/frame_seed.py             SeedFrame node (§7)
```

Edited files (minimal, reversible):

```
HeroAI/headless_tree.py:32          factory call at the injection point
Widgets/Automation/Multiboxing/HeroAI.py:39   factory call; later, outer-tree import
HeroAI/settings.py                  get/set_account_rotation_engine
Py4GWCoreLib/__init__.py:124        export BldMgrBT
Py4GWCoreLib/Builds/Dervish/D_A/DervBoneFarmer.py   parent class name only
HeroAI/windows.py                   debug panel: engine column (phase 5)
```

Untouched: `combat.py`, `Builds/Any/HeroAI.py`, `targeting.py`, `cache_data.py`
(one additive key at most), all 12 `custom_skill_src` files, every legacy build.

---

## 9. Phases

Each phase ships independently; the toggle stays on "legacy" until phase 6.

**P1 — BldMgrBT base.** New file, casing fix, alias. DervBoneFarmer reparented.
Verify: DervBoneFarmer runs identically via `AddBuild`; additionally drivable
through `ProcessSkillCasting` without `NotImplementedError`.

**P2 — Seam.** `engine.py`, settings toggle, two driver edits. `"bt"` returns a
stub engine that always fails over. Verify: legacy accounts byte-identical;
toggle round-trips in the UI.

**P3 — Outer tree unification + SeedFrame.** Both drivers import the factory;
BB seeding live for both engines (legacy engine ignores it). Verify: widget and
headless behave identically to their old duplicated trees; BB keys visible in a
debug draw.

**P4 — Generic rotation.** `rotation.py` + `condition_table.py` +
`unique_skills.py` + Cast node with RUNNING. This is the bulk of the work and the
bulk of the risk; land tier 1 first, then tier 2 in reviewable batches (the 61
ports, checked against `combat.py:1038-1543` branch by branch). Verify: one
account on `"bt"` in a live party against 7 on legacy; the windows.py panel shows
per-slot verdicts from both engines' perspectives for the same frame.

**P5 — Contract dispatch + debug surface.** `contract_resolver.py`, the Switch
node, windows.py engine column, per-condition failure names surfaced.

**P6 — Trust flip.** Accounts opt in per-box. Fallback construction sites
(§3 tail) routed through the factory once the bt engine has survived real
content. Legacy remains selectable indefinitely; deletion is a separate decision
with its own doc.

---

## 10. Risks

- **Tier 2 ports.** 61 hand-ported branches feeding real builds; regressions here
  are party-wide. Mitigation: port in batches, side-by-side verdict panel in P4,
  one account live at a time.
- **Blackboard propagation cost.** `BehaviorTree.py:2045` re-walks the whole tree
  each tick. The rotation adds ~50 nodes. Measure in P4; the fix (propagate at
  build/reset only — the dict is never swapped) touches every BT consumer, so it
  is its own change, made only if measurement demands it.
- **Shared CombatClass state.** The bt engine calls `IsReadyToCast`, which sets
  `in_casting_routine` as a side effect. The bt engine never reads it, but the
  legacy debug panel does. Acceptable while engines are exclusive per account;
  extract pure variants if it ever bites.
- **RUNNING vs auto-attack timing.** The Cast node's RUNNING replaces
  `aftercast_timer` gating; `auto_attack_timer` interplay (`headless_tree.py:295`)
  must be re-verified in combat, not assumed.
- **`ooc=None` in service mode.** `AddBuild()` carries no phase signal. Decision:
  SeedFrame's `bb["in_aggro"]` is the single source; the base derives the phase
  from it when `ooc is None`.
