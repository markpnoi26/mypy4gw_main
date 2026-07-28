# HeroAI BT Migration — PR Sequencing Plan

How to land the migration as a readable, reviewable series instead of one
unreviewable drop. Built backwards from the plug-in point: foundations that
are provably no-ops go first, the decider goes last.

Reference: `docs/heroai_bt_migration_complete.md` (full record and rationale).

## Naming

Branch `HEROAI_BT_NN_SHORT_NAME`, title `[BT n/12] <short imperative>`. The
count goes in both so a reviewer can see the position in the series without
opening anything.

| PR | Branch | Upstream |
|---|---|---|
| 1 | `HEROAI_BT_01_COMBAT_SERVICES` | [#34](https://github.com/apoguita/Py4GW_Reforged/pull/34) — open |

---

## The invariant that drives the ordering

Every PR up to the last one must be a **no-op at runtime**. Not "low risk" —
provably nothing changes. That gives you:

- a reviewable diff per PR, judged on its own
- a bisectable history if something surfaces later
- the ability to stop at any point without a half-migrated repo
- one small, high-scrutiny PR that actually changes behaviour

### The hazard this must survive

`_scan_build_types` walks `Builds` then `BTBuilds`. The legacy resolver
(`Builds/Any/HeroAI.py`) picks with strict `score > best_score`, so **the
first build achieving the max score wins ties**. A ported build and its legacy
original have identical `required_skills`, `optional_skills` and professions —
identical `ScoreMatch` — a tie. Legacy currently wins only because `Builds` is
scanned first.

That is ordering luck. `Path.rglob` order is not contractually stable, and
reordering `package_names` would flip it silently.

**Therefore PR 3 must establish isolation before any ported build lands.**

### Recommended isolation mechanism (keeps `Builds/` untouched)

Add a `native` filter to `BuildRegistry._iter_matchable_builds`, defaulting to
**legacy-only**:

```python
def _iter_matchable_builds(self, match_only=False, native=False):
    # native=False -> BuildMgr builds only   (legacy resolver, unchanged callers)
    # native=True  -> BldMgrBT builds only   (HeroAIBTEngine)
    # native=None  -> both
```

`Builds/Any/HeroAI.py:100` calls it with no arguments, so it keeps getting
legacy builds only — **zero edits to any legacy file**. `matchable_bt_builds`
asks explicitly for `native=True`. One place owns the rule, both callers are
explicit, and the tie can never occur.

---

## The sequence

```
 1  CombatServices extraction            no-op   mechanical, huge diff
 2  BldMgrBT base + alias + marker        no-op   new file + 1-line filter swap
 3  BTBuilds scaffold + scan + isolation  no-op   ← establishes the invariant
 4  Ported builds: Monk / Paragon         inert   6 builds
 5  Ported builds: Mesmer                 inert   6 builds
 6  Ported builds: Necromancer            inert   8 builds
 7  Ported builds: remaining professions  inert   8 builds
 8  FarmBuilds (4)                        inert   heavy docs, no decomposition
 9  BT engine internals                   inert   nothing imports HeroAI/bt yet
10  HeroAIBTEngine + contract resolution  inert   nothing constructs it yet
11  THE DECIDER — router, toggle, UI      LIVE    the only behavioural PR
12  Docs + skill                          no-op
```

"inert" = the code exists and compiles but is unreachable: legacy can't match
it (PR 3 isolation) and the BT engine doesn't exist yet.

---

## PR 1 — Extract `CombatServices`

**Goal** Split `BuildMgr`'s paradigm-agnostic combat utilities into a mixin
both build bases can share.

| | |
|---|---|
| Adds | `Py4GWCoreLib/build_src/combat_services.py` (~1700 ln, 64 methods) |
| Edits | `Py4GWCoreLib/BuildMgr.py` → 2221→671 ln, `class BuildMgr(CombatServices)` |
| Behaviour | none — pure relocation |

**What moves**: targeting (`ResolveAllyTarget`, `ResolvePreferredAllyTarget`,
`ResolveRankedPartyAllyTarget`, `AcquireTarget`, `_resolve_target`), aggro
(`IsInAggro`, `IsCloseToAggro`, `GetActiveScanRange`), party health/spike
monitor, spirit placement, custom-skill inspection (`GetCustomSkill`,
`IsSkillEquipped`), auto-attack, blacklist, whiteboard (4 methods), and the
cast API.

**What stays**: identity/matching (`ScoreMatch`, `ValidateSkills`,
`LoadSkillBar`), fallback chain, tick state, and the generator execution
engine (`_process_phase`, `_yield_from_handler`, `Process*`, `Tick`,
`Set*Fn`), plus `BuildRegistry`.

**Do it with a script, not by hand.** An AST pass that splits by method name
and rewrites both files is verifiable; 64 manual edits are not.

**Review guidance** Do NOT read 1700 relocated lines. Verify instead:
- method sets: moved ∪ kept == original, intersection empty
- every `self.*` a moved method touches resolves on `BuildMgr`
- `init_combat_services()` covers all 11 service state fields
- full `compileall` over `Py4GWCoreLib` + `HeroAI`

**Rollback** Revert; nothing depends on it yet.

---

## PR 2 — `BldMgrBT` base, `BTBuildMgr` alias, registry marker

**Goal** A standalone BT build base that the registry can discover, without
either base importing the other.

| | |
|---|---|
| Adds | `Py4GWCoreLib/BldMgrBT.py` — `class BldMgrBT(CombatServices)`: identity + `ScoreMatch`, fallback chain, tick-state bridge, `drive()`, tree lifecycle, `Process*` via `run_phase`. `BTBuildMgr = BldMgrBT` at the bottom |
| Deletes | `Py4GWCoreLib/BTBuildMgr.py` |
| Edits | `BuildMgr.py`: `is_build_type = True`; `_scan_build_types` filters on the marker instead of `issubclass` (**still `Builds`-only**). `__init__.py` exports. `botting_tree_src/upkeep.py:8` typing import |
| Behaviour | none — the marker finds exactly the same classes |

**Bug fixed here** The old `BTBuildMgr.process_skill_casting` was snake_case
and never overrode `BuildMgr.ProcessSkillCasting` (`BuildMgr.py:1969`), so BT
builds hit `raise NotImplementedError` outside `AddBuild()`. `BldMgrBT`
overrides correctly. `DervBoneFarmer` is `is_combat_automator_compatible=False`
and only ever reached via `AddBuild`, so this is strictly a capability gain
with no live path change.

**Keep the scan single-package in this PR.** Discovery of `BTBuilds` belongs
with the isolation guard, not here.

**Review guidance** Confirm `BldMgrBT` has no `BuildMgr` import; confirm the
marker is on `BuildMgr`; confirm `DervBoneFarmer` still resolves
`BTBuildMgr`; check `get_rotation_tree()` returns a **stable wrapper** (see
`planner.py:198` — it calls the builder once and freezes the result).

---

## PR 3 — `BTBuilds` scaffold, two-package scan, isolation ⚠️ KEYSTONE

**Goal** Create the destination package and make it safe for builds to land in
it. This is the PR that establishes the invariant everything after depends on.

| | |
|---|---|
| Adds | `BTBuilds/` (package `__init__`, `nodes.py`) and `BTBuilds/FarmBuilds/` — **no builds yet** |
| Edits | `BuildMgr.py`: `_scan_build_types` walks both packages; `is_purpose_specific_build()` + FarmBuilds check in `_iter_matchable_builds`; **`native` filter defaulting to legacy-only** |
| Behaviour | none — `BTBuilds` is empty, and the filter default means the legacy resolver's call site is unchanged |

**`nodes.py`** ships the authoring vocabulary: `rotation_tree(name, gates,
rungs)`, `cast`, `guarded_cast`, `cond`, `step`, `rung`, `gate`, `selector`,
`sequence`, `optional`, `act`.

Encode the two hard-won rules **in the helper and its docstring** so no
reviewer has to remember them:
- gates go in a **Sequence**, rungs in a **Selector** (a passing gate placed
  under a Selector returns SUCCESS and skips the entire rotation)
- rungs are `ConditionNode`, not `ActionNode` (`BehaviorTree.py:447-486`
  latches — result arrives the NEXT tick, halving cast cadence)

**FarmBuilds exclusion is by location, not flag** — a flag can be forgotten, a
package path cannot:

```python
FARM_BUILD_PACKAGE = "Py4GWCoreLib.BTBuilds.FarmBuilds"
def is_purpose_specific_build(build):
    m = type(build).__module__ or ""
    return m == FARM_BUILD_PACKAGE or m.startswith(FARM_BUILD_PACKAGE + ".")
```

**Review guidance** This PR earns the most scrutiny of the first three.
Verify with tests, not reading:
- exclusion rule against the prefix trap: `BTBuilds.FarmBuildsExtra.X` must
  NOT match; `Builds.FarmBuilds.X` must NOT match
- `_iter_matchable_builds()` with no args returns **only** `BuildMgr` builds
- an AST check that every relative import under `BTBuilds` resolves (this is
  what catches modules whose filenames contain spaces — they cannot be
  imported by normal syntax)

---

## PRs 4–8 — Ported builds

Five PRs, each a set of self-contained new files under `BTBuilds/`. **Zero
edits to `Builds/`.** Every build is unreachable: the legacy resolver filters
them out (PR 3) and the BT engine does not exist yet.

| PR | Scope | Builds |
|---|---|---|
| 4 | Monk + Paragon | Martyr, Healing Burst, Ray of Judgment, Defensive Refrain |
| 5 | Mesmer | Energy Surge, Ineptitude, Panic, Keystone Signet, Psychic Instability Wastrels, Holy Inept |
| 6 | Necromancer | Dark Aura Support, Assassins Promise Death Magic, Blood is Power Healer, Necro_Prot, Pre_Searing_Necro, Soul Taker Scythe, Xinraes Weapon Healer, Contagion |
| 7 | Ele / Ranger / Warrior / Ritualist / Dervish / Any | Ether Renewal Prot Infuser, Pre_Searing_ele, Tao_Dagger_Spam, Pre_Searing_Ignite, Seven_Weapon_Stance_Axe, SoS Spirit Spammer ×2 (+ `sos_rotation.py`), Soul Twisting, VoS_Grenths_Aura_Farmer, Any_Dhuum |
| 8 | FarmBuilds | SF_Ass_vaettir, SF_Mes_vaettir, ShadowTheftDaggerSpammer, KeiranThackerayEOTN |

**Review protocol per build**: open the legacy original side by side and check
four things.

1. **Rung order identical**, including duplicate rungs at different thresholds
   (Blood is Power Healer has Recuperation three times).
2. **Guards identical**, including mid-ladder aggro gates (which become a
   nested `sequence(cond("InAggro"), selector(...))`).
3. **Once-per-pass locals** (snapshots, energy) moved to `seed_blackboard()`,
   not recomputed per rung — recomputation is both slower and a behaviour
   change mid-ladder.
4. **Atomic rungs stay atomic** — a pick-target → cast → record-cooldown
   triple must remain one generator, or the target can change between the
   guard node and the cast node.

Each ported file's docstring records deviations. Several flag **legacy bugs
preserved deliberately** — bare `return` rungs in VoS_Grenths_Aura_Farmer (all
9), Tao_Dagger_Spam (4) and Holy Inept (1); a permanently dead
`Cry_of_Frustration` rung in Keystone Signet. Those are decisions to make, not
silent fixes.

**PR 8 is different in kind.** The four farm builds are hosted verbatim under
a single `drive()` node rather than decomposed — they are script-driven,
map-gated, FSM-coupled multi-frame routines, not rotations. Review the
**headers** (which argue why decomposition would be wrong) rather than looking
for a tree. It also carries the `ShadowTheftDaggerSpammer` missing-import fix
(`Keystroke`/`Key`/`ActionQueueManager` are used but never imported in legacy,
raising `NameError` on every weapon swap) — a deliberate behaviour difference,
documented.

**Suggested**: land 4 first, verify the review protocol works on it, then run
5–8 in parallel review.

---

## PR 9 — BT engine internals (unwired)

**Goal** The decision machinery, with nothing importing it.

| | |
|---|---|
| Adds | `HeroAI/bt/__init__.py`, `frame_seed.py`, `condition_table.py`, `unique_skills.py`, `skill_subtrees.py`, `conditions.py`, `rotation.py` |
| Edits | none |
| Behaviour | none — no import path reaches `HeroAI/bt` |

The reviewable claims:

- **`conditions.decide_slot`** ships with `NATIVE_DECIDE = False`, delegating
  to `CombatClass.IsReadyToCast`. The native port exists but is off.
- **`decide_slot_native`** must replicate `IsReadyToCast` (`combat.py:1552`)
  in order — it is the FULL oracle, not a gate: gate prefix, sacrifice floors,
  `GetAppropiateTarget`, blacklist, hex-on-spirit, dagger combos,
  `SpiritBuffExists`, `HasEffect` nuances, BiP/BR special case, conditions —
  plus `Agent.IsLiving(target)` from `FindCastableSkill:1858`.
- **`rotation.cast_slot`** is a verbatim port of the `HandleCombat` tail
  (`combat.py:2017-2068`). Order is load-bearing; whiteboard claim posting
  happens inside `_apply_spike_lock`/`_skill_lock_post`. It also writes
  `in_casting_routine` exactly like legacy because that flag has **external
  readers** (`headless_tree.py:153/:330`, widget `:230/:313`,
  `windows.py:325`).
- **Tier 2** carries all 61 `UniqueProperty` branches, keyed off the handler's
  own attributes so skill ids cannot diverge.
- **Tier 1** returns `None` for deliberately deferred families (IsCasting —
  which has `_queue_outcome` interrupt side effects — plus IsPartyWide,
  HasDervishEnchantment, HasChant, Pet/PetAttack), falling back to legacy.

**Review guidance** Diff `unique_skills.py` against `combat.py:1038-1180`
branch by branch; diff `cast_slot` against `combat.py:2017-2068` line by line.
Those two are where a regression would hide.

---

## PR 10 — `HeroAIBTEngine` + contract resolution (unwired)

| | |
|---|---|
| Adds | `HeroAI/bt/bt_engine.py` |
| Edits | none |
| Behaviour | none — nothing constructs it |

Implements the 7-method driver protocol (`set_cached_data`, `ProcessOOC`,
`ProcessCombat`, `ProcessSkillCasting`, `DidTickSucceed`,
`Ensure/Get/ClearBuildContract`, `ApplyBlockedSkillIDs`), guards mirroring
`HeroAI_Build._prepare_combat`, a rotation signature mirroring
`_get_contract_signature`, and `matchable_bt_builds` (asks the registry for
`native=True`, re-checks the FarmBuilds exclusion so the rule survives future
filter changes).

**Design point worth stating in the PR body**: legacy generator builds are
invisible to this resolver **by decision** — new builds are `BldMgrBT` and get
ported, rather than adding a `next(gen)` compatibility bridge into the BT path.

---

## PR 11 — The decider ⚠️ THE ONLY BEHAVIOURAL PR

**Goal** Wire it up behind a per-account toggle that defaults to off.

| | |
|---|---|
| Adds | `HeroAI/engine.py` — `create_heroai_engine()` + `HeroAIEngineRouter` |
| Edits | `HeroAI/settings.py` (toggle getter/setter), `HeroAI/ui.py` ("Combat Engine" tab), `HeroAI/headless_tree.py:31`, `Widgets/Automation/Multiboxing/HeroAI.py:39` |
| Behaviour | **live** — but only for accounts that opt in |

Small diff, maximum scrutiny. The four things to verify:

1. **Default is off.** `[RotationEngine] UseBT` defaults `False`
   (`settings.py`). An untouched account is byte-identical.
2. **The seam already existed.** `headless_tree.py:30` has always accepted an
   injectable `heroai_build`; nobody used it. The edit is a factory call at
   the existing injection point, not new plumbing.
3. **The router re-checks the toggle every frame** and lazily constructs
   whichever engine is selected, so flipping takes effect next frame with no
   restart.
4. **`ticks.py:173` needs no change** — `DidTickSucceed` is in the protocol.

**Rollback** is a one-line default flip, or revert the PR; everything below it
stays inert and harmless.

---

## PR 12 — Docs + skill

`docs/heroai_bt_migration_complete.md`, `docs/build_port_to_bldmgrbt.md`, the
SUPERSEDED banner on `buildmgr_retirement_blueprint.md`, and
`.claude/skills/heroai-bt-engine.md`.

Land last so it describes what actually shipped. (If you prefer docs-first for
reviewer context, PR 1 can carry a stub that PR 12 completes.)

---

## Verification harness — build it once, run it every PR

None of these need a game client. Wire them into CI or a `make check` and they
protect every PR in the series:

| Check | Catches |
|---|---|
| `compileall` on `Py4GWCoreLib` + `HeroAI` with **3.13** | syntax; note the dev shell's `python` is 3.11 and `ui.py` uses 3.12+ nested-quote f-strings |
| AST: relative imports under `BTBuilds` all resolve | modules whose filenames contain spaces (unimportable) |
| AST: `CombatServices` method-set split, no overlap, all `self.*` resolve on both bases | a botched extraction |
| Exclusion-rule table incl. prefix traps | `FarmBuildsExtra` false-matching |
| `_iter_matchable_builds()` default returns legacy-only | the tie hazard silently returning |
| `drive()` simulation: sync hit/miss same-frame, yielding → RUNNING→SUCCESS, exception → FAILURE + no leak | generator-bridge regressions |
| Rung-order extraction: legacy ladder vs ported Selector | reordered priorities |
| `git status --porcelain Py4GWCoreLib/Builds` is empty (PRs 3–10) | accidental legacy edits |

---

## Live rollout, after PR 11 merges

1. Toggle ON for **one** account, `NATIVE_DECIDE=False`. Decisions come from
   the legacy oracle; only dispatch is a tree. Compare cast order and cadence
   against a legacy box in the same party. *Cadence regression almost always
   means an `ActionNode` where a `ConditionNode` belonged.*
2. `NATIVE_DECIDE=True` on that account. Compare per-slot verdicts via
   `windows.py:322 DrawPrioritizedSkills`.
3. Shrink `condition_table.FALLBACK_FAMILIES` one family at a time, same
   protocol. IsCasting last — it has the `_queue_outcome` side effect.
4. Ported builds: verify each on the **legacy** engine first (toggle off — it
   must match and run identically), then on BT.
5. The ~10 hardcoded `SetFallback("HeroAI", HeroAI_Build(...))` sites stay
   legacy until the BT engine has earned trust; converting them is one
   mechanical pass afterwards.
