---
name: heroai-bt-engine
description: Working on the HeroAI BT combat engine (HeroAI/bt/), BldMgrBT builds (BTBuilds/, FarmBuilds/), CombatServices, the legacy/bt engine toggle, or porting/authoring builds. Load before touching HeroAI/bt/, HeroAI/engine.py, Core/BldMgrBT.py, Core/build_src/, or Core/BTBuilds/.
---

# HeroAI BT engine + BldMgrBT builds — working knowledge

Full record with rationale, diagrams and history:
`docs/heroai_bt_migration_complete.md`. Port patterns:
`docs/build_port_to_bldmgrbt.md`. This skill is the condensed version.

## State: COMPLETE and working

All 32 matchable builds ported (28 combat → `BTBuilds/`, 4 farm →
`BTBuilds/FarmBuilds/`). Legacy (`Builds/**`, `combat.py`,
`Builds/Any/HeroAI.py`) untouched — git-clean — and still the default engine.

## Architecture (as built)

```
drivers: headless_tree.py:31 | widget HeroAI.py:39
  └ create_heroai_engine() → HeroAIEngineRouter        HeroAI/engine.py
      toggle OFF (default) → HeroAI_Build (legacy, untouched)
      toggle ON  → HeroAIBTEngine(BldMgrBT)            HeroAI/bt/bt_engine.py
          EnsureBuildContract → matchable_bt_builds (BldMgrBT-native ONLY,
             FarmBuilds excluded; legacy generator builds invisible BY DESIGN)
          matched build.tick_rotation(bb,ooc) → FAILURE → generic rotation
          generic rotation (HeroAI/bt/rotation.py):
             Slot0..7: Ready → Decide → Cast, then AutoAttack
             Decide: NATIVE_DECIDE(False) ? native+tiers : IsReadyToCast oracle
             Cast: verbatim HandleCombat tail (locks/alcohol/aftercast order)

class hierarchy:
  CombatServices (build_src/combat_services.py, 64 methods, extracted from BuildMgr)
    ↙ BuildMgr (671 ln: identity/fallback/tick-state/GENERATOR exec/registry)
    ↘ BldMgrBT (standalone: identity/fallback/drive()/tree lifecycle/BT exec)
  BTBuildMgr = BldMgrBT (alias at bottom of BldMgrBT.py; old module deleted)
```

Toggle: `Settings().get_account_bt_rotation_enabled()` — ini
`[RotationEngine] UseBT`, per-account, HeroAI config → "Combat Engine" tab,
takes effect next frame. `Settings` is a singleton (settings.py:91) — per-frame
reads are cheap.

Registry: `_scan_build_types` walks `Builds` AND `BTBuilds`, filters on the
`is_build_type = True` class marker (NOT issubclass — neither base imports the
other). `is_purpose_specific_build` structurally excludes anything under
`Core.BTBuilds.FarmBuilds` from matching, for BOTH engines; checked in
`_iter_matchable_builds` and re-checked in `matchable_bt_builds`.

## The progressive-migration flags

1. `HeroAI/bt/conditions.py: NATIVE_DECIDE = False` — False: per-slot decision
   = `CombatClass.IsReadyToCast` (byte-identical). True: `decide_slot_native`
   (full faithful port) dispatching conditions through the tiers.
2. `condition_table.py` (tier 1): `None` = family not covered → fallback to
   `AreCastConditionsMet`. Deferred families: IsCasting (has `_queue_outcome`
   side effect), IsPartyWide, HasDervishEnchantment, HasChant, Pet/PetAttack.
3. `unique_skills.py` (tier 2): ALL 61 UniqueProperty branches ported; keys
   from handler attributes so ids can't diverge; None→True matches legacy
   fall-through.
4. `skill_subtrees.py` (tier 3): per-skill BT registry, empty; for genuinely
   multi-frame skills only.

## drive() — the generator bridge (BldMgrBT.drive)

Ticks a `Builds/Skills` generator one step per frame, keyed in
`active_generators`. Skill-layer bodies are synchronous (only `CastSkillID`'s
SPIRIT branch really yields; `CanCastSkillID` has zero yields), so ordinary
casts resolve StopIteration on the first step → same-frame SUCCESS/FAILURE =
legacy cadence; spirit casts report RUNNING across frames. Exceptions pop the
slot; `reset_rotation_tree()` abandons all in-flight generators. This is why
all 69 skill modules needed ZERO changes — they bind to the owner
(`SkillsTemplate.py:39`) and are plain classes (BuildMgr only in
TYPE_CHECKING annotations).

## Authoring/porting builds (BTBuilds/nodes.py)

`rotation_tree(name, gates, rungs)` = Sequence(gates) wrapping
Selector(rungs). **Gates must NOT go under the Selector** — a passing gate
would return SUCCESS and skip every rung (bug caught in the first Martyr
draft). Mapping from legacy ladders:

| legacy | BT |
|---|---|
| `SetSkillCastingFn(fn)` | `build_rotation_tree()` |
| leading `if not X: return False` | gate |
| `if g and (yield from cast()): return True` | `guarded_cast(self, name, g, factory)` |
| unguarded rung | `cast(self, name, factory)` |
| mid-ladder aggro gate | nested `sequence(cond("InAggro"), selector(rungs))` |
| once-per-pass locals (snapshots/energy) | `seed_blackboard()` → rungs read bb |
| bare side effect (UpdatePartyHealthMonitor) | always-SUCCESS cond, same position |
| pick→cast→track-cooldown | ONE generator per rung (atomic; splitting lets the target change) |

Rung ORDER is preserved verbatim, including duplicates at different
thresholds. `match_only` early-return preserved (registry swallows ctor
TypeError and retries — broken signature = "never matches", not an error).
Fallback stays `SetFallback("HeroAI", HeroAI_Build(standalone_fallback=True))`
— duck-typed, intended. Ported builds work under BOTH engines (legacy reaches
them via ProcessCombat→run_phase→tick_rotation).

FarmBuilds decision rule: "would HeroAI, seeing a matching bar, want to run
this?" No (one route/boss/escort) → FarmBuilds; reached only by script or
AddBuild. The 4 there (SF_Ass_vaettir, SF_Mes_vaettir,
ShadowTheftDaggerSpammer, KeiranThackerayEOTN) are verbatim generators hosted
under one drive() node — script-driven, map-gated, multi-frame, FSM-coupled;
their headers explain why decomposition would be wrong and what a real tree
rewrite would need.

## Traps (do not relearn)

- **`next(gen, None)` bridge**: drivers advance a fresh generator once per
  frame — engines must do all work before the first `yield`.
- **`ActionNode` latches** (BehaviorTree.py:447-486): result arrives the NEXT
  tick → halves cadence. Rotation leaves are `ConditionNode`s; `act()` only
  when the extra frame is intended.
- **`planner.py:198` freezes service trees** → `get_rotation_tree()` returns a
  stable one-node wrapper; the inner tree recompiles on
  `current_rotation_signature()` change.
- **`IsReadyToCast` is the FULL oracle** (gate prefix + sacrifice + target +
  blacklist + hex-on-spirit + combos + SpiritBuffExists + HasEffect nuances +
  BiP/BR + AreCastConditionsMet). `FindCastableSkill:1858` also requires
  `Agent.IsLiving(target)`.
- **`in_casting_routine` has external readers** (headless :153/:330, widget
  :230/:313, windows.py:325) — the BT cast path writes it exactly like legacy.
- **Cast tail order matters**: SetSkillPointer → in_casting_routine/aftercast
  → `_skill_lock_is_blocked` → timer reset → `_apply_spike_lock` →
  `_skill_lock_post` → alcohol → UseSkill(skill_order[slot]+1). Whiteboard
  claim posting lives inside those calls.
- **Disabled skills**: handled solely by `IsSkillReady` (is_skill_enabled +
  blocked_skill_ids) — no extra handling anywhere.
- **Space-in-filename modules can't be imported** — shared code goes in an
  importable sibling (`sos_rotation.py`); an AST checker verifies all BTBuilds
  relative imports resolve.
- **Two "whiteboards"**: process-local registry (GlobalCache/Whiteboard.py)
  vs cross-account ShMem.PostLock. Blackboard writes coordinate nothing.
- **`IsHeadlessCombatPauseActive`** = just `in_aggro or local_in_aggro`.
- **`_propagate_blackboard` re-walks the tree every tick** — measure before
  adding hundreds of nodes.
- **Shared class-level state is imported, not copied** (`_DhuumModeTracker`).
- **Python**: shell `python` is 3.11; project is 3.13 (ui.py uses 3.12+
  f-strings). Verify with `py -3.13-32 -m py_compile` / `compileall`.
- **AreCastConditionsMet declarative half** = feature-count matching with
  early-return families; IsCasting family has interrupt-queue side effects.

## Known legacy bugs (flagged in port docstrings, not silently fixed)

- ShadowTheftDaggerSpammer: Keystroke/Key/ActionQueueManager never imported
  (NameError on every weapon swap) — imports ADDED in the port (documented
  behaviour difference); Shadow Theft guard precedence `(A and B and not C)
  or (B and D)` preserved verbatim.
- KeiranThackerayEOTN: `los_recent=[]` (CombatEvents commented out) →
  damage_dealt/received always False → Lazy-Miku never fires, LoS gap-close
  unconditional. Re-enabling CombatEvents changes all three.
- Bare-`return` rungs (falsy → failed tick after successful cast → HeroAI
  fallback same frame): VoS_Grenths_Aura_Farmer (all 9), Tao_Dagger_Spam (4),
  Holy Inept (1). Ported as SUCCESS.
- Keystone Signet: snapshot never populates `enemy_casting` → leading
  Cry_of_Frustration rung permanently dead. Preserved.
- `Builds/Skills/any/NoAttribute.py:661` calls
  `_get_can_cast_skill_failure_reason` which exists nowhere.
- VoS_Grenths_Aura_Farmer is named a farmer but carries no exclusion flag →
  kept matchable in the port to preserve behaviour (flagged).

## Verification path before flipping anything

1. Toggle ON, `NATIVE_DECIDE=False`, one account, live party — decisions
   byte-identical, only dispatch is a tree. Cadence regression ⇒ ActionNode
   where a ConditionNode belonged.
2. `NATIVE_DECIDE=True` same account; compare via windows.py:322
   DrawPrioritizedSkills.
3. Shrink `FALLBACK_FAMILIES` one at a time.
4. Ported builds: verify on legacy engine first (must be identical), then BT.
