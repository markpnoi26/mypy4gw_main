# HeroAI BT Migration — Complete Record

The full record of migrating HeroAI combat and the build system to BehaviorTree
execution: architecture before and after, every design decision and why it was
made, every pitfall hit, every bug found, every file touched, and how it was
all verified. This document exists so none of that knowledge is lost.

Status at time of writing: **working**. All 32 builds ported, full compile
clean, legacy path untouched and still the default.

Companion documents:
- `docs/heroai_bt_migration_blueprint.md` — the original phased plan
- `docs/build_port_to_bldmgrbt.md` — the build port guide + patterns
- `docs/buildmgr_retirement_blueprint.md` — SUPERSEDED (plan changed to coexistence)
- `.claude/skills/heroai-bt-engine.md` — condensed working knowledge for AI sessions

---

## Table of contents

1. [The starting point](#1-the-starting-point)
2. [Discovery: what was actually wrong](#2-discovery-what-was-actually-wrong)
3. [Design decisions and their rationale](#3-design-decisions-and-their-rationale)
4. [The new architecture](#4-the-new-architecture)
5. [File-by-file: what was built and why](#5-file-by-file-what-was-built-and-why)
6. [The build migration campaign](#6-the-build-migration-campaign)
7. [Pitfalls catalog — do not relearn these](#7-pitfalls-catalog)
8. [Bugs found in legacy code](#8-bugs-found-in-legacy-code)
9. [Verification methodology](#9-verification-methodology)
10. [Rollout path](#10-rollout-path)
11. [Future work](#11-future-work)

---

## 1. The starting point

### 1.1 Legacy execution path

```
           ┌─────────────────────────┐   ┌─────────────────────────┐
 ENTRY     │ Widget HeroAI.py :355   │   │ headless_tree.py :271   │
           │   inline BT             │   │   _build_tree           │
           └───────────┬─────────────┘   └───────────┬─────────────┘
                       │      DUPLICATED (~250 ln,   │
                       │      drifting independently)│
                       └──────────────┬──────────────┘
                                      ▼
 ORCH               Sequence "Main_BT"
                     ├ Guard (IsAlive / DistanceSafe / NotKnockedDown)
                     ├ Condition IsCasting  ← RUNNING smuggled into a guard
                     └ Selector: Loot / OOC / Follow / Combat
                                      │
                        next(ProcessCombat(), None)   ← THE BRIDGE
                                      ▼
 BUILD              HeroAI_Build (BuildMgr)   Builds/Any/HeroAI.py
                     ProcessCombat/ProcessOOC (generators)
                     _run_contract :147 → EnsureBuildContract :68
                     BuildRegistry scoring → matched build OR self
                                      ▼
 ROTATION           combat.py :1840 FindCastableSkill
                     for slot in 0..7: flat imperative loop
                       IsSkillReady :483 / IsOOCSkill :1710
                       IsReadyToCast :1552 → target
                                      ▼
 CONDITION          combat.py :1025 AreCastConditionsMet (518 ln)
                     UniqueProperty? → 61× `if skill_id == self.X` ladder
                     else → ~50 declarative feature-count checks
                                      ▼
 EXEC               SkillBar.UseSkill(slot+1, target, aftercast)
                     + aftercast_timer + spike lock + skill lock

 STATE      CacheData singleton (cache_data.py:89) — combat_handler, timers,
            account_options, shmem, in_aggro.
            BehaviorTree.blackboard EXISTED (:2022) but HeroAI never used it.
```

### 1.2 The two rails

Builds reached the game through **two disconnected paths**:

- **Rail 1 — BuildMgr pipeline**: HeroAI's contract resolution scored registry
  builds and delegated via `yield from build.ProcessCombat()`.
- **Rail 2 — BottingTree service rail**: `bot.AddBuild(build)` →
  `upkeep.py:52 AddServiceTree(name, build.get_rotation_tree)` → tree ticked
  directly. `DervBoneFarmer` lived only here.

The old `BTBuildMgr` (42 lines) was supposed to bridge them but could not
(see §2.3).

### 1.3 Key numbers established during discovery

| Fact | Number |
|---|---|
| Build classes under `Builds/` (excl. skill layer) | 44 |
| Contract-matchable (rest excluded by flags) | 32 |
| Skill-layer modules (`Builds/Skills/**`) | 69 |
| Public skill methods in the skill layer | ~154 |
| `self.build.*` call sites from skill layer | ~481 |
| … of which generator-shaped (cast calls) | ~148 |
| … of which paradigm-agnostic utilities | ~330 |
| `UniqueProperty` skills in the 61-branch ladder | 61 |
| Whiteboard-registered skills | 18 |
| `CastConditions` declarative fields | ~50 |

---

## 2. Discovery: what was actually wrong

### 2.1 The `next(gen, None)` bridge kills coroutine semantics

`headless_tree.py:158/:173` (and the widget equivalents) did:

```python
next(self.heroai_build.ProcessCombat(), None)
return self.heroai_build.DidTickSucceed()
```

A **fresh generator every frame, advanced exactly once, then discarded**.
Consequences:

- Everything after the first `yield` in `Process*` never ran.
- `yield from Routines.Yield.wait(250)` was structurally dead code.
- A matched build's multi-yield rotation **restarted from the top every
  frame** — any build relying on sequential yields silently executed only its
  first segment.
- Results flowed through a side channel (`tick_state` + `DidTickSucceed()`),
  not return values.

`BuildMgr`'s entire coroutine protocol was inert in this path. The BT and the
BuildMgr were not cooperating; the bridge resolved the paradigm conflict by
discarding the BuildMgr's half.

### 2.2 RUNNING was expressed backwards

`HandleCombat` returned `bool` — two states. "Still casting" could not be said
by the node doing the casting, so it was displaced into a **sibling guard**
(`ConditionNode("IsCasting")` at `headless_tree.py:325` returning RUNNING) plus
an inline check at `:154`. The guard held the state the work node should own.
That displacement is the signature of a BT retrofit rather than BT-native code.

### 2.3 `BTBuildMgr.process_skill_casting` was dead code

`BTBuildMgr.py:39` defined `process_skill_casting` (snake_case). The real
`BuildMgr` hook is `ProcessSkillCasting` (`BuildMgr.py:1969`). Nothing ever
called the lowercase one. Driving any BT build through the normal pipeline hit
`raise NotImplementedError` at `BuildMgr.py:1973` — which is why
`DervBoneFarmer` was `AddBuild()`-only.

### 2.4 `planner.py:198` freezes service trees

`_coerce_runtime_tree` calls the registered builder **once** and stores the
resulting tree forever. Fine for a fixed build; fatal for anything whose tree
must recompile when the skillbar changes. This forced the "stable wrapper"
design in `BldMgrBT.get_rotation_tree()` (§3.6).

### 2.5 `IsReadyToCast` is the FULL oracle, not a gate

`combat.py:1552-1710` is: gate prefix (recharge, casting, adrenaline,
energy + expertise, Vow of Silence, shout suppression) **plus** sacrifice
floors, `GetAppropiateTarget` (235 ln), enemy blacklist, hex-on-spirit
rejection, dagger combo checks, `SpiritBuffExists`, `HasEffect` (with the
weapon-spell-overlap and NonWeaponSpelledAlly nuances), the BiP/BR special
case, **and** `AreCastConditionsMet`. Any native replacement must replicate
all of it in order — `decide_slot_native` does.

Also easy to miss: `FindCastableSkill:1858` additionally requires
`Agent.IsLiving(target)`.

### 2.6 `AreCastConditionsMet` declarative half is feature-count matching

Lines 1183-1540: every declared `CastConditions` field must be satisfied
(`feature_count == number_of_features`), with early `return False` for the
count/weapon/aggro families, plus Pet/PetAttack special cases, plus the
IsCasting family which calls `is_interrupt_feasible` and has a
`_queue_outcome` **side effect** (interrupt bookkeeping). That side effect is
why the IsCasting family was deliberately deferred from native porting.

### 2.7 The generator ceremony is three levels deep — and mostly hollow

```
Martyr._run_local_skill_logic                       (ladder, yields)
  └ yield from skills.Monk.HealingPrayers.Dwaynas_Kiss()   body 100% sync
      └ yield from build.CastSkillIDAndRestoreTarget(...)  yields only to delegate
          └ yield from build.CastSkillID(...)              sync EXCEPT spirit branch
```

Verified by inspection: `CanCastSkillID` has **zero** yields; `CastSkillID`
has an `if False: yield` generator-marker plus exactly two real yields, both in
the **spirit-cast branch** (wait-for-spawn, step-away). ~145 of ~148 cast call
sites are straight-line code wearing generator costume. This single finding is
what made the whole migration tractable: the spirit branch is the only genuine
multi-frame cast, and it is exactly what `drive()` handles (§3.7).

### 2.8 The skill layer was never actually coupled to BuildMgr

`SkillsTemplate.py:39` binds every profession module to the **owner**:

```python
self.owner = owner if owner is not None else self
self.Monk = MonkSkills(self.owner)        # → HealingPrayers.build = owner
```

And the 69 modules themselves are **plain classes** — their `BuildMgr`
references are type annotations under `TYPE_CHECKING`. So once `BldMgrBT`
carried the `self.build.*` API surface (via CombatServices), the entire skill
layer worked for BT builds with **zero changes to any of the 69 files**.

### 2.9 BuildRegistry is duck-typed except in one place

- `_call_build_ctor:2078` — `cast(Any, ...)` + `except TypeError` retry chain
- `_instantiate_build:2107` — `hasattr(build, "set_cached_data")`
- `_iter_matchable_builds` — reads plain attributes
- **Zero** `isinstance(BuildMgr)` / `issubclass` gates repo-wide…
- …except `_scan_build_types`, which filtered `issubclass(value, BuildMgr)`.
  One line. Replaced with the `is_build_type = True` class marker.

### 2.10 Channel taxonomy: Blackboard vs ShMem vs the two "whiteboards"

| Channel | Scope | Purpose |
|---|---|---|
| BT blackboard (`BehaviorTree.py:2022`, plain dict) | one account, one process | stop re-deriving facts within a frame |
| ShMem (`"HeroAI_Mem"`, `multiprocessing.shared_memory`) | all accounts | party state mirror + message queue |
| WB registry (`GlobalCache/Whiteboard.py`) | this process, import time | "does this skill coordinate?" lookup |
| WB locks (`ShMem.PostLock`) | all accounts | claims: don't both Shatter the same target |

Rule established: the blackboard **mirrors** ShMem reads once per frame; it is
never the transport. Writing `bb["claim"]` coordinates nothing — other boxes
have their own blackboards.

### 2.11 What belongs in a BT node and what doesn't

A node earns its cost only with **cross-tick state** (RUNNING, sequencing
memory, reset semantics). A `LessLife < 0.75` check is a within-frame boolean.
Hence: **no per-skill BTs for 3433 skills.** Structure lives in the tree
(slots, phases, orchestration); conditions are plain predicates; the only
per-skill trees are the tier-3 registry for skills that genuinely span frames.

### 2.12 CacheData survives — three buckets

| Contents | Home |
|---|---|
| config/identity/UI state (`account_email`, ini keys, options) | CacheData (6000+ lines of UI read it directly) |
| per-frame derived facts (`in_aggro`, energy, targets, scans) | blackboard |
| casting/aftercast timers | absorbed by RUNNING where possible; `stay_alert_timer` stays (game feel, not control flow) |

---

## 3. Design decisions and their rationale

### 3.1 Side-by-side engines behind a seam — never a rewrite in place

The injection point **already existed**: `headless_tree.py:30` took
`heroai_build` as a constructor parameter that nobody used. The seam is a
factory + router:

```python
# HeroAI/engine.py
def create_heroai_engine(cached_data, standalone_fallback=False):
    return HeroAIEngineRouter(...)      # re-checks the toggle every frame
```

The router implements the 7-method driver protocol extracted from the real
call sites (`set_cached_data`, `ProcessOOC`, `ProcessCombat`,
`ProcessSkillCasting`, `DidTickSucceed`, `Ensure/Get/ClearBuildContract`,
`ApplyBlockedSkillIDs`) and lazily constructs whichever engine the per-account
toggle selects. Flip takes effect next frame, no restart.

**Why:** the legacy path stays byte-identical by construction; blast radius of
the toggle is one account; `ticks.py:173` and every other consumer keeps
working unchanged.

### 3.2 Per-account toggle, in the existing settings pattern

`Settings().get/set_account_bt_rotation_enabled()` — ini section
`[RotationEngine] UseBT`, default False, following the resurrection-scroll
getter pattern (`settings.py:429`). `Settings` is a singleton (`:91-96`) and
`NativeSettings("HeroAI.ini", "account")` is process-wide per (name, scope),
so per-frame reads are cheap. UI: a "Combat Engine" tab in the HeroAI config
window (`HeroAI/ui.py`, between Resurrection Scroll and Debug). No fancy UI —
one checkbox and two lines of explanation.

### 3.3 `BldMgrBT` standalone — coexistence, not retirement

The design went through three stages driven by explicit user decisions:

1. `BldMgrBT(BuildMgr)` — inherits everything, carries dead weight.
2. User: "make it its own, without depending on BuildMgr" → standalone class
   with copied identity/fallback/matching (~250 ln), `is_build_type` marker so
   the registry sees both kinds, duck-typed fallback handlers so BT and legacy
   builds can fall back to each other.
3. User: "keep BuildMgr for the other service, don't gut it" → **coexistence**:
   `BuildMgr` keeps serving legacy; both bases share `CombatServices`.

`BuildMgr` itself was left as-is apart from: the marker, the two-package
registry scan, and the FarmBuilds exclusion. The retirement blueprint was
superseded.

### 3.4 `CombatServices` extraction — the mixin both bases share

`BuildMgr` was five concerns fused (2221 ln):

| Concern | ~ln | Fate |
|---|---|---|
| Combat services (targeting, aggro, party health/spike, spirit placement, skill inspection, blacklist, whiteboard, cast API) | ~1700 | **moved** to `build_src/combat_services.py` |
| Identity/matching (`ScoreMatch`, `ValidateSkills`, …) | ~100 | stayed in BuildMgr; copied into BldMgrBT |
| Fallback chain | ~40 | same |
| Tick state | ~20 | same |
| Generator execution (`_process_phase`, `Tick`, `Set*Fn`) | ~150 | stayed in BuildMgr only |

The extraction was done **by AST script**, not hand edits: 64 methods moved,
34 kept, zero overlap, verified. `BuildMgr(CombatServices)` and
`BldMgrBT(CombatServices)`; service state (11 fields) initialised by
`init_combat_services()`.

**Why mixin, not composition:** ~330 skill-layer call sites read
`self.build.<method>` directly. Composition would need 30+ delegation shims
for zero gain.

**Why this unlocked the skill layer for free:** see §2.8 — modules bind to the
owner and only annotate `BuildMgr` under `TYPE_CHECKING`.

### 3.5 The three-tier condition system (HeroAI generic engine)

- **Tier 1** — `HeroAI/bt/condition_table.py`: one predicate per
  `CastConditions` **field** (~50 entries, not per skill). Returns `None` for
  deliberately deferred families → caller falls back to legacy
  `AreCastConditionsMet`. Deferred: `IsCasting` (interrupt feasibility +
  `_queue_outcome` side effects), `IsPartyWide`, `HasDervishEnchantment`,
  `HasChant`, Pet/PetAttack target special cases.
- **Tier 2** — `HeroAI/bt/unique_skills.py`: registry keyed by skill id,
  **all 61** UniqueProperty branches ported branch-for-branch from
  `combat.py:1038-1180`. Keys come from the handler's own attributes
  (`handler.energy_drain` etc.) so ids can never diverge from legacy.
  `evaluate()` returning `None` = no branch = legacy's fall-through
  `return True`.
- **Tier 3** — `HeroAI/bt/skill_subtrees.py`: per-skill BT subtree registry,
  **empty at launch**, opt-in for skills that genuinely span frames.

### 3.6 `BldMgrBT` tree lifecycle

- `build_rotation_tree()` — required override.
- `current_rotation_signature()` — invalidation hook; `None` = compile once
  (custom bots); HeroAI overrides with map+region+district+language+professions+skills
  (mirrors `HeroAI_Build._get_contract_signature`).
- `get_rotation_tree()` — returns a **stable wrapper** (one ActionNode ticking
  `tick_rotation`) because `planner.py:198` freezes whatever it's given. The
  wrapper's identity never changes; the inner tree recompiles freely.
- `tick_rotation(bb, ooc)` — bridges the caller's blackboard onto the inner
  tree, derives `ooc` from `bb["in_aggro"]` when None (service mode has no
  phase signal), calls `seed_blackboard`, ticks.
- `run_phase` — SUCCESS/RUNNING → tick success; FAILURE → `ResolveFallback()`
  → fallback's `Process*` (so BT builds keep falling back to legacy HeroAI).

### 3.7 `drive()` — the generator bridge that saved 69 files

```python
def drive(self, key, factory) -> NodeState:
    gen = self.active_generators.get(key)
    if gen is None:
        gen = factory()
        if not hasattr(gen, "send"):            # plain function returned a bool
            return SUCCESS if gen else FAILURE
        self.active_generators[key] = gen
    try:
        next(gen);  return RUNNING
    except StopIteration as stop:
        self.active_generators.pop(key, None)
        return SUCCESS if stop.value else FAILURE
    except Exception:
        self.active_generators.pop(key, None);  return FAILURE
```

Because skill-layer bodies are synchronous (§2.7), an ordinary cast raises
StopIteration on the very first step → **same-frame SUCCESS/FAILURE**, exactly
matching legacy cadence. A spirit cast yields → RUNNING across frames, which is
what the tree wants. Exceptions clean up the slot. `reset_rotation_tree()`
abandons all in-flight generators.

This is the mechanism that let all 32 builds keep calling the shared skill
layer **unchanged**.

### 3.8 ConditionNode everywhere; ActionNode is the exception

`BehaviorTree.ActionNode` (`:447-486`) **latches**: run → return RUNNING that
tick → deliver result NEXT tick → self-reset. One extra frame per completion.
In a rotation ticked per frame that silently halves cast cadence. So every
rotation leaf is a `ConditionNode` (evaluates and returns same-frame, supports
bool or NodeState, node-arg detected by signature). `act()` exists in the
helpers for when the extra frame is intended.

### 3.9 `rotation_tree(name, gates, rungs)` — the gate/Selector fix

First Martyr draft put `cond("CanCast")` as a **Selector** child — a passing
gate returns SUCCESS and short-circuits the whole rotation; nothing would ever
cast. The fix became a helper encoding the correct shape:

```
BehaviorTree( Sequence(name, *gates, Selector(name+"Rotation", *rungs)) )
```

Gates (early `return False` in ladders) go in the Sequence; priority rungs go
in the Selector (first hit wins = ladder semantics). Any hand-written BT that
puts conds directly under a Selector has this bug.

### 3.10 Contract resolution under the BT engine — BT-only, no bridge

`HeroAIBTEngine.EnsureBuildContract` scores registry builds cached on the
rotation signature, but `matchable_bt_builds` yields **only** builds exposing
`tick_rotation` + `build_rotation_tree` (i.e., BldMgrBT-native). Legacy
generator builds are invisible **by explicit decision**: the user chose
"new builds must be BldMgrBT; port them" over a `next(gen)` compatibility
bridge. On a matched build's FAILURE the engine falls through to the generic
rotation. Ported builds are still reachable from the **legacy** engine too,
because `BldMgrBT` implements `ProcessCombat`/`ProcessOOC`/`tick_state` —
which is exactly what `HeroAI_Build._run_contract:162` calls. So porting never
breaks an account still on legacy.

### 3.11 FarmBuilds — exclusion by location, not by flag

`BTBuilds/FarmBuilds/**` is structurally unmatchable:

```python
# BuildMgr.py
FARM_BUILD_PACKAGE = "Py4GWCoreLib.BTBuilds.FarmBuilds"
def is_purpose_specific_build(build):
    m = type(build).__module__ or ""
    return m == FARM_BUILD_PACKAGE or m.startswith(FARM_BUILD_PACKAGE + ".")
```

Checked in `_iter_matchable_builds` (so the **legacy** engine can't match a
farm build either) and re-checked in `matchable_bt_builds` (so the rule
survives registry filter changes). A flag can be forgotten; a package path
cannot. The four farm ports additionally carry
`is_combat_automator_compatible=False` as belt-and-braces.

Decision rule: *"Would HeroAI, seeing a matching skillbar on some account,
want to run this?"* If no — one farm route, one boss, one escort — it goes in
FarmBuilds and is reached only by explicit instantiation or `AddBuild()`.

### 3.12 The generic HeroAI rotation (v1: proven oracle, native behind a flag)

`HeroAI/bt/rotation.py` compiles per skillbar signature:

```
Sequence "HeroAI_BT_Rotation"
  ├ Cond CallLeaderTarget            (side effect, always SUCCESS; combat only)
  └ Selector "Skills"
       ├ Seq "Slot0":  Ready → Decide → Cast
       ├ … slots 1-7
       └ Cond AutoAttack             (FAILURE when ooc)
```

- **Ready** = `IsSkillReady` (honours per-skill enable toggles +
  `blocked_skill_ids` — this is why disabled skills need no extra handling
  anywhere) + phase check (`IsOOCSkill` when ooc).
- **Decide** = `conditions.decide_slot`: with `NATIVE_DECIDE = False`
  (shipped default) it delegates to `IsReadyToCast` — decisions byte-identical
  to legacy, only the dispatch is a tree. With True it runs
  `decide_slot_native`, the full faithful port whose condition step goes
  through tiers 1/2 with legacy fallback. Adds the `Agent.IsLiving(target)`
  check from `FindCastableSkill:1858`.
- **Cast** = verbatim port of the `HandleCombat` tail (`combat.py:2017-2068`):
  `SetSkillPointer` → `in_casting_routine=True` + aftercast 250/500 →
  `_skill_lock_is_blocked` → `aftercast_timer.Reset()` → `_apply_spike_lock` →
  `_skill_lock_post` → alcohol block (incl. the debug log) →
  `UseSkill(skill_order[slot]+1, …)` → `ResetSkillPointer`. Order matters:
  whiteboard claim posting lives inside those calls. Tier-3 subtrees are
  consulted first if registered for the skill.

`in_casting_routine` is written exactly like legacy because it has **external
readers**: `headless_tree.py:153/:330`, widget `:230/:313`, `windows.py:325`.
The outer IsCasting guard and the debug panel keep working for both engines.

### 3.13 Frame seeding

`HeroAI/bt/frame_seed.py`: eager per tick (frame id, cache handle, `in_aggro`
= `data.in_aggro or local_in_aggro` — note `IsHeadlessCombatPauseActive` is
misnamed, it IS the aggro discriminator — party position, leader flag, weapon
type, player id/health/energy) and **lazy memoized** scans (nearest spirit
spellcast/earshot, nearest enemy) popped at seed time. Engine guards
(`prepare_tick`) mirror `HeroAI_Build._prepare_combat`: map valid, explorable,
not cinematic, alive, not KD, `Update()` + `UpdateCombat()` (both internally
throttled, safe to call).

---

## 4. The new architecture

### 4.1 Execution paths

```
 drivers (unchanged shape)
   headless_tree.py:31   Widgets/Automation/Multiboxing/HeroAI.py:39
        └──────────────┬──────────────┘
                       ▼
        create_heroai_engine() → HeroAIEngineRouter        HeroAI/engine.py
              │  Settings [RotationEngine] UseBT, per-account, per-frame check
   ┌──────────┴───────────┐
   ▼ OFF (default)        ▼ ON
 HeroAI_Build           HeroAIBTEngine(BldMgrBT)           HeroAI/bt/bt_engine.py
 (legacy, UNTOUCHED)      │ prepare_tick guards
   │                      │ EnsureBuildContract → matchable_bt_builds
   │                      │   (BldMgrBT-native only; FarmBuilds excluded)
   │                      ├─ matched build → build.tick_rotation(bb, ooc)
   │                      │     └ FAILURE → generic rotation
   │                      └─ else → generic rotation      HeroAI/bt/rotation.py
   │                            Slot0..7: Ready → Decide → Cast, AutoAttack
   │                            Decide: NATIVE_DECIDE ? native+tiers : IsReadyToCast
   ▼                            Cast: verbatim HandleCombat tail
 combat.py monolith                   │
 (FindCastableSkill →                 ▼
  AreCastConditionsMet)      SkillBar.UseSkill + spike/skill locks  (SHARED, untouched)
```

### 4.2 Class hierarchy

```
                    CombatServices                build_src/combat_services.py
                    (~1700 ln, 64 methods: targeting, aggro, party health,
                     spirit placement, skill inspection, whiteboard, cast API)
                     ↙                    ↘
        BuildMgr (671 ln)              BldMgrBT              Py4GWCoreLib/BldMgrBT.py
        identity, fallback,            identity, fallback, tick-state bridge,
        tick state, GENERATOR          drive(), tree lifecycle, BT execution
        execution, BuildRegistry       (ProcessCombat/OOC/SkillCasting via run_phase)
             ↑                              ↑
        legacy Builds/** (44)          BTBuilds/** (28 combat, matchable)
        SkillsTemplate (69 modules     BTBuilds/FarmBuilds/** (4, excluded)
        bind via owner → work for      HeroAIBTEngine
        BOTH bases unchanged)          DervBoneFarmer (via BTBuildMgr alias)
```

`BTBuildMgr` is now `BTBuildMgr = BldMgrBT` (alias at the bottom of
BldMgrBT.py; the separate module was deleted).

### 4.3 Both engines can run one ported build

| Engine | Path into a ported build |
|---|---|
| legacy | `HeroAI_Build._run_contract` → `yield from build.ProcessCombat()` → `run_phase` → `tick_rotation` |
| BT | `HeroAIBTEngine.run_engine_phase` → `build.tick_rotation(bb, ooc)` |

This is what makes incremental migration safe: port one, verify on both,
move on. No flag day.

---

## 5. File-by-file: what was built and why

### New files

| File | Purpose |
|---|---|
| `Py4GWCoreLib/BldMgrBT.py` | Standalone BT build base (§3.3, §3.6, §3.7). `BTBuildMgr` alias at bottom |
| `Py4GWCoreLib/build_src/combat_services.py` | The extracted 64-method mixin (§3.4) |
| `Py4GWCoreLib/BTBuilds/__init__.py` | Package doc + helper re-exports |
| `Py4GWCoreLib/BTBuilds/nodes.py` | Authoring helpers: `rotation_tree`, `cast`, `guarded_cast`, `cond`, `step`, `rung`, `gate`, `selector`, `sequence`, `optional`, `act` |
| `Py4GWCoreLib/BTBuilds/<28 builds>` | Ported combat builds (§6) |
| `Py4GWCoreLib/BTBuilds/Ritualist/Rt_Any/sos_rotation.py` | Shared SoS mixin (two variants, legacy duplicated the ladder) |
| `Py4GWCoreLib/BTBuilds/FarmBuilds/**` | 4 farm ports + package docstring stating the exclusion contract |
| `HeroAI/engine.py` | `create_heroai_engine` + `HeroAIEngineRouter` (§3.1) |
| `HeroAI/bt/bt_engine.py` | `HeroAIBTEngine` — driver protocol, guards, contract resolution |
| `HeroAI/bt/rotation.py` | Generic 8-slot rotation compiler + cast-tail port |
| `HeroAI/bt/conditions.py` | `decide_slot` (`NATIVE_DECIDE` flag) + `decide_slot_native` (full IsReadyToCast port) + `cast_conditions_met` tier dispatch |
| `HeroAI/bt/condition_table.py` | Tier 1 declarative evaluation (feature-count port; `None` = fallback) |
| `HeroAI/bt/unique_skills.py` | Tier 2 — all 61 UniqueProperty predicates |
| `HeroAI/bt/skill_subtrees.py` | Tier 3 registry (empty) |
| `HeroAI/bt/frame_seed.py` | Blackboard seeding (§3.13) |
| `.claude/skills/heroai-bt-engine.md` | Working-knowledge skill for AI sessions |
| `docs/heroai_bt_migration_blueprint.md`, `docs/build_port_to_bldmgrbt.md`, this file | The paper trail |

### Edited files (all minimal and reversible)

| File | Change | Why |
|---|---|---|
| `Py4GWCoreLib/BuildMgr.py` | CombatServices extraction (AST-scripted move); `is_build_type = True`; `_scan_build_types` walks `Builds` **and** `BTBuilds` (marker instead of `issubclass`); `is_purpose_specific_build` + FarmBuilds check in `_iter_matchable_builds` | §2.3, §2.9, §3.4, §3.11 |
| `Py4GWCoreLib/__init__.py` | exports `BldMgrBT`, `BTBuildMgr` from BldMgrBT | alias collapse |
| `Py4GWCoreLib/botting_tree_src/upkeep.py:8` | typing import path → BldMgrBT | alias collapse |
| `HeroAI/headless_tree.py:31` | factory call at the pre-existing injection point; dropped the now-unused HeroAI_Build import | §3.1 |
| `Widgets/Automation/Multiboxing/HeroAI.py` | import + `:39` factory call | §3.1 |
| `HeroAI/settings.py` | `get/set_account_bt_rotation_enabled` | §3.2 |
| `HeroAI/ui.py` | "Combat Engine" tab | §3.2 |

### Deleted

- `Py4GWCoreLib/BTBuildMgr.py` (alias moved into BldMgrBT.py; only 3
  references existed and were updated)

### Explicitly untouched

`Py4GWCoreLib/Builds/**` (git-clean throughout), `HeroAI/combat.py`,
`Builds/Any/HeroAI.py`, all 12 `custom_skill_src` profession files, the
legacy outer trees, `ticks.py`.

---

## 6. The build migration campaign

### 6.1 Survey and classification

An AST survey classified the 32 matchable builds: **28 LADDER** (a
`SetSkillCastingFn` generator of `if cond and (yield from cast): return True`
rungs) and **4 OVERRIDE** (`ProcessSkillCasting` overridden directly — which
turned out to mean "stateful farm routine", not "rotation").

The 12 excluded-from-matching builds (`HeroAI_Build` itself, `BuildTemplate`,
6 in `CombatAutomatorExcluded/`, 3 Dervish farmers) were not ported — they are
reached directly or via `AddBuild`, never by matching.

### 6.2 The ladder port pattern

| Legacy construct | BT construct |
|---|---|
| `SetSkillCastingFn(fn)` | `build_rotation_tree()` |
| leading `if not X: return False` | gate in `rotation_tree`'s Sequence |
| `if cond and (yield from cast()): return True` | `guarded_cast(self, name, guard, factory)` |
| unguarded rung | `cast(self, name, factory)` |
| mid-ladder `if not self.IsInAggro(): return False` | nested `sequence("InAggroRotation", cond("InAggro"), selector("AggroRungs", …))` |
| locals computed once per pass (snapshots, energy) | `seed_blackboard()` → rungs read `node.blackboard` |
| bare side-effect statements (`UpdatePartyHealthMonitor`) | always-SUCCESS cond node **in the same position** |
| pick-target → cast → record-cooldown triples | kept in ONE generator per rung (atomicity — target must not change between guard and cast) |
| `class X(BuildMgr)` | `class X(BldMgrBT)`; `match_only` early-return preserved |

Non-negotiables: **rung order preserved verbatim** (including duplicate rungs
at different thresholds — Blood is Power Healer has Recuperation three times);
snapshots evaluated once per tick exactly as the ladder did once per pass;
`SetBlockedSkills` lists carried over; fallback stays
`SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))` — duck-typed,
intended, works during migration.

### 6.3 The 28 ported combat builds

Monk: Martyr (rung order machine-verified), Healing Burst, Ray of Judgment
(echo-gated 12-rung, YMLAD chain timestamp side effect preserved).
Mesmer: Energy Surge, Ineptitude, Panic, Keystone Signet, Psychic Instability
Wastrels (per-target cooldown atomicity), Holy Inept.
Necromancer: Dark Aura Support, Assassins Promise Death Magic, Blood is Power
Healer (25 rungs), Necro_Prot, Pre_Searing_Necro, Soul Taker Scythe
(**partially decomposed** — the scythe spike block reassigns its target
mid-flow and recomputes enchant counts inside the attack loop, so it stays one
generator behind one node), Xinraes Weapon Healer, Contagion (helpers carried
verbatim).
Elementalist: Ether Renewal Prot Infuser, Pre_Searing_ele.
Ranger: Tao_Dagger_Spam, Pre_Searing_Ignite (if/elif preparation exclusivity
preserved in the Read the Wind guard).
Ritualist: SoS Spirit Spammer (+ Any_Rt variant via shared mixin), Soul
Twisting (two gate tiers).
Warrior: Seven_Weapon_Stance_Axe.  Paragon: Defensive Refrain (double
AutoAttack positions preserved).  Any: Any_Dhuum (imports the legacy
`_DhuumModeTracker` rather than duplicating it — it holds class-level shared
mode state; a second copy would desync Dhuum's Rest/Ghostly Fury across a
party running both engines).  Dervish: VoS_Grenths_Aura_Farmer (kept
matchable — despite the name it carries no exclusion flag, so moving it to
FarmBuilds would be a behaviour change, not a translation; flagged).

### 6.4 The 4 farm builds — hosted, not decomposed

`SF_Ass_vaettir`, `SF_Mes_vaettir`, `ShadowTheftDaggerSpammer`,
`KeiranThackerayEOTN` → `BTBuilds/FarmBuilds/**`. Each is the verbatim legacy
routine hosted under a single node via `drive()`, with a long header
explaining exactly why a Selector decomposition would be wrong:

- **script-driven**: external control surfaces (`SetKillingRoutine`,
  `SetStuckSignal`, `self.status` written by the farm script) that don't exist
  in a party context;
- **map-gated with early returns** (Jaga Moraine anchors hardcoded);
- **multi-frame by design**: 29-75 yield points, `wait(1000)` idles,
  keybind handshakes, blocking `while` loops, tuned 200/200/250 ms combo
  spacing;
- **shared-state mutation mid-routine**: `ActionQueueManager().ResetQueue`
  between casts, FSM `pause()`/`resume()` via a `pause_reasons` set (Keiran),
  a two-leg PathHandler retrace sub-machine (Keiran).

Also documented per-file: the diff list between the two Vaettir variants
(Mantra of Earth vs Channeling, stuck threshold >3 vs >0, kill-spot-aware HoS
bail, different spike gates), and what a real tree rewrite would require
(phase field, map gate as top branch, WaitUntil nodes) with the instruction to
do it against a live run, never blind.

**Latent mis-match bug fixed for the BT engine:** none of the four carried
`is_combat_automator_compatible=False`, so legacy matching *can* select e.g.
`SF_Ass_vaettir` for any account holding a Shadow Form bar and start running a
Vaettir farm mid-party. FarmBuilds placement fixes this for the BT engine; the
legacy files still carry the exposure for the legacy engine (untouched by
policy).

---

## 7. Pitfalls catalog

Every trap hit during this migration. Do not relearn these.

1. **`next(gen, None)` bridge** — drivers advance a fresh generator once per
   frame. Anything after the first `yield` in an engine's `Process*` never
   runs. The BT engine does all work before a single `yield`.
2. **`ActionNode` latches** (`BehaviorTree.py:447-486`) — result delivered the
   tick AFTER the action runs. Halves rotation cadence if used for rungs. Use
   `ConditionNode` for same-frame leaves.
3. **Gates under a Selector short-circuit backwards** — a passing gate returns
   SUCCESS and skips every rung. Use `rotation_tree(gates, rungs)`:
   Sequence(gates) wrapping Selector(rungs).
4. **`planner.py:198` freezes service trees** — never hand `AddBuild` a tree
   that must recompile; hand it a stable wrapper.
5. **`IsReadyToCast` is the whole oracle** (§2.5) — a "gate-only" reuse would
   silently skip target resolution, combos, HasEffect, conditions.
6. **`in_casting_routine` has external readers** — the BT cast path must write
   it like legacy or the outer guard and debug panel break.
7. **`_propagate_blackboard` (`BehaviorTree.py:2045`) re-walks the whole tree
   every tick** — known cost, measure before adding hundreds of nodes; the fix
   (propagate on build/reset only) touches every BT consumer, so it's its own
   change.
8. **Python: dev-shell `python` is 3.11; the project is 3.13** and `ui.py`
   uses 3.12+ nested-quote f-strings. Compile-check with
   `py -3.13-32 -m py_compile` / `compileall`.
9. **Space-in-filename modules can't be imported** (`SoS Spirit Spammer.py`)
   — shared code goes in an importable sibling (`sos_rotation.py`). An AST
   checker now verifies every relative import in BTBuilds resolves.
10. **Two "whiteboards"** — the process-local registry
    (`GlobalCache/Whiteboard.py`) vs the cross-account ShMem locks. Blackboard
    writes coordinate nothing across accounts.
11. **`IsHeadlessCombatPauseActive` is misnamed** — it's just
    `in_aggro or local_in_aggro`, the combat/OOC discriminator.
12. **`match_only` ctor discipline** — BuildRegistry instantiates with
    `match_only=True` first and *swallows TypeError*, silently retrying
    without kwargs. A broken signature shows up as "build never matches", not
    an exception. Keiran got a trailing `match_only` param for this reason.
13. **Pick/cast/track must stay atomic** — splitting a target-picking rung
    into guard-node + cast-node lets the target change between them
    (Psychic Instability Wastrels, Contagion helpers).
14. **Snapshot semantics** — ladder locals computed once per pass must become
    once-per-tick blackboard values, not per-rung recomputation (both slower
    and a behaviour change mid-ladder).
15. **Shared class-level state must not be duplicated** —
    `_DhuumModeTracker` is imported from the legacy module, not copied.
16. **`_ensure_blackboard_data`/tick plumbing** — `BehaviorTree.tick()`
    normalizes states and propagates the board; SelectorNode/SequenceNode
    self-reset on completion (the legacy outer tree depends on this).
17. **CLAUDE.md style rules** — no new `_underscore` names (calling existing
    ones is fine; two Ray-of-Judgment attrs written by the shared skill layer
    keep their underscore names deliberately), near-zero comments, minimal
    docstrings — farm-build headers are the sanctioned exception because the
    *why* is genuinely non-obvious.

---

## 8. Bugs found in legacy code

All flagged in the ported files' docstrings; none silently fixed except where
the port would otherwise crash.

| Location | Bug | Handling |
|---|---|---|
| `BTBuildMgr.py:39` (old) | `process_skill_casting` casing — never overrode the real hook; BT builds crashed outside `AddBuild` | **Fixed** in BldMgrBT |
| `ShadowTheftDaggerSpammer` | `Keystroke`/`Key`/`ActionQueueManager` used but **never imported** — every weapon swap raised NameError | **Imports added** in port (else it cannot run); documented as a deliberate difference |
| `ShadowTheftDaggerSpammer` | Shadow Theft guard precedence: `(A and B and not C) or (B and D)` | Preserved verbatim + note |
| `KeiranThackerayEOTN` | `los_recent = []` (CombatEvents import commented out) → `damage_dealt`/`damage_received` always False → Lazy-Miku trigger never fires; both LoS gap-close branches unconditional | Preserved verbatim + note; re-enabling CombatEvents changes all three at once |
| `VoS_Grenths_Aura_Farmer` | all 9 rungs end in bare `return` (None) → failed tick after every successful cast → HeroAI fallback fires same frame | Ported as SUCCESS rungs + flag |
| `Tao_Dagger_Spam` | 4 rungs, same bare-`return` bug | Same |
| `Holy Inept` | `Signet_of_Clumsiness` rung, same | Same |
| `Keystone Signet` | snapshot never populates `enemy_casting` → leading Cry_of_Frustration rung permanently dead | Preserved + note (populate or drop — user's call) |
| `Builds/Skills/any/NoAttribute.py:661` | calls `self.build._get_can_cast_skill_failure_reason` — defined **nowhere**, never existed in BuildMgr (verified against git history) | Left alone; live AttributeError when reached |
| Legacy 4 farm builds | contract-matchable despite being map-specific farm routines (no exclusion flag) | Fixed for BT engine via FarmBuilds; legacy untouched |

---

## 9. Verification methodology

No game client in the dev loop, so every claim was machine-checked:

- **Compile**: `py -3.13-32` + `compileall` over `Py4GWCoreLib` and `HeroAI`
  after every batch (final: FULL_COMPILE_OK across ~200 files).
- **AST extraction check**: methods moved vs kept, zero overlap; every
  `self.*` read by a moved method resolves on BOTH bases; all 11 service
  fields covered by `init_combat_services`; skill-layer API (26 members)
  reachable from both bases.
- **AST import resolver**: every relative import under BTBuilds maps to a real
  module/package (this is what catches the space-filename class of bug).
- **`drive()` semantics simulation**: ordinary hit/miss same-frame,
  spirit-style RUNNING→RUNNING→SUCCESS, exception → FAILURE with no generator
  leak.
- **Exclusion rule test**: 6 cases including the prefix trap
  (`FarmBuildsExtra` must NOT match; `Builds.FarmBuilds` must NOT match).
- **Rung-order check**: legacy ladder token order vs ported Selector child
  order (Martyr, machine-verified identical across 11 rungs).
- **Consumer-protocol check**: every method each known consumer calls
  (drivers, registry, upkeep, DervBoneFarmer) present on the standalone base.
- **`git status` on `Builds/`**: empty throughout — the legacy tree was never
  modified.

---

## 10. Rollout path

1. Everything ships with the toggle **OFF** — legacy byte-identical.
2. Flip `[RotationEngine] UseBT` (HeroAI config → Combat Engine tab) on ONE
   account in a live party. `NATIVE_DECIDE = False` means decisions still come
   from `IsReadyToCast`; only the dispatch is a tree. Compare cast order and
   cadence against a legacy account — cadence regressions almost always mean
   an ActionNode where a ConditionNode belonged.
3. Flip `HeroAI/bt/conditions.py: NATIVE_DECIDE = True` on that account.
   Verify per-slot verdicts via `windows.py:322 DrawPrioritizedSkills`.
4. Shrink `condition_table.FALLBACK_FAMILIES` one family at a time, same
   protocol.
5. Ported builds: verify each on legacy first (toggle OFF — must match and run
   identically), then on BT.
6. The ~10 hardcoded `SetFallback("HeroAI", HeroAI_Build(...))` sites stay
   legacy until the BT engine has earned trust; converting them later is one
   mechanical pass.

---

## 11. Future work

- **Flip-and-verify NATIVE_DECIDE**, then absorb the deferred tier-1 families
  (IsCasting last — it has the `_queue_outcome` side effect).
- **Tier-3 subtrees** for genuinely multi-frame skills (spirit casting is the
  canonical first tenant).
- **`_propagate_blackboard` cost** — measure under the full rotation; if it
  bites, move propagation to build/reset (touches every BT consumer, own
  change).
- **Blackboard as frame cache for conditions** — tier 1/2 currently call the
  same primitives legacy does; wiring them through seeded facts is the perf
  payoff the combat handover doc wanted.
- **Farm-build tree rewrites** (optional) — per the notes in each header,
  against live runs only.
- **Legacy bug decisions** — bare-`return` rungs (intended fall-through or
  bug?), Keystone dead rung, CombatEvents re-enable for Keiran,
  `_get_can_cast_skill_failure_reason`.
- **Eventually**: outer-tree unification (delete the widget's duplicated
  inline tree in favour of one factory), RUNNING ownership moved fully into
  the cast node, and retiring the `DidTickSucceed` bridge once nothing reads
  it.
