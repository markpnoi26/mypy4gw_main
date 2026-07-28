# BuildMgr Retirement Blueprint

> **SUPERSEDED.** The plan changed to coexistence: `BuildMgr` stays for the
> legacy engine and generator builds; `BldMgrBT` is standalone and both share
> `CombatServices`. See `docs/heroai_bt_migration_complete.md` (the full
> record) and `docs/build_port_to_bldmgrbt.md` (port guide). Kept for the
> analysis in §1-2, which is still accurate.

Target: `BldMgrBT` becomes a standalone build base with no `BuildMgr` dependency,
and `BuildMgr` is deleted once every build and skill module has migrated.

This is a structural refactor, not a behavior change. Each phase leaves the repo
working and is independently revertable.

---

## 1. What BuildMgr actually is

2221 lines fusing five concerns with very different fates:

| Concern | ~lines | Fate |
|---|---|---|
| Combat services — targeting, aggro, party health/spike, spirit placement, skill inspection, blacklist, whiteboard | ~1000 | **Extract and keep.** Paradigm-agnostic |
| Cast API — `CanCastSkillID`, `CastSkillID`, `CastSkillIDAndRestoreTarget`, `CastSpiritSkillID`, `CastSkillSlot` | ~250 | **Port to synchronous.** Generator-ness is vestigial (see §2) |
| Identity / matching — `ScoreMatch`, `ValidateSkills`, `LoadSkillBar`, profession + skill fields | ~100 | **Move into BldMgrBT** |
| Fallback chain — `SetFallback`, `ResolveFallback`, blocked-skill mask | ~40 | **Move into BldMgrBT** |
| Generator execution — `_yield_from_handler`, `_process_phase`, `_process_skill_casting_phase`, `Process*`, `Tick`, `SetOOCFn`/`SetCombatFn`/`SetSkillCastingFn`, `tick_state`/`DidTickSucceed` | ~150 | **Delete.** This is the part that is not the future |

### State inventory (`BuildMgr.__init__:44-80`)

Cleanly separable, which is what makes extraction mechanical:

```
identity   build_name, required_primary, required_secondary, template_code,
           required_skills, optional_skills, skills, minimum_required_match,
           IsFixedBuild, is_template_only, is_combat_automator_compatible
fallback   default/current_fallback_name, default/current_fallback_handler,
           is_fallback_candidate, blocked_skills
services   priority_target, current_target_id, _was_in_aggro,
           _local_cast_timer, _auto_attack_timer, _auto_attack_time,
           _party_health_monitor, _party_health_monitor_timer,
           _party_health_monitor_window_ms, _custom_skill_data_handler,
           _cached_data
DELETE     _local_skill_casting_handler, _local_ooc_handler,
           _local_combat_handler, tick_state
```

Combat services needs 11 fields — a self-contained context, not a tangle.

---

## 2. The cast API is already synchronous

Verified by inspection:

- `CanCastSkillID:1693` — **zero yields**. Pure predicate wearing no costume at all.
- `CastSkillID:1785` — one `if False: yield` marker (`:1794`, forces generator-hood)
  plus exactly two real yields, both inside the **spirit branch**:
  `yield from Routines.Yield.wait(self._get_spirit_cast_wait_ms(...))` and
  `yield from self._wait_for_spirit_spawn_and_step_away(skill_id)`.
- `CastSkillIDAndRestoreTarget:1859` — yields only to delegate to `CastSkillID`
  and `RestoreEnemyTarget`.

Consequence: of ~148 generator cast call sites across `Builds/Skills/**`, roughly
145 port by deleting `yield from`. The bodies are already straight-line code.

The genuine multi-frame case — spirit casting (wait for spawn, step away from
overlap) — becomes a **tier 3 BT subtree** returning RUNNING. That is a better
expression of it than a generator that only works when someone drives it
correctly, and it reuses the registry already built at
`HeroAI/bt/skill_subtrees.py`.

---

## 3. Target architecture

```
Py4GWCoreLib/
  build_src/
    combat_services.py   CombatServices  — the ~1000 lines, moved verbatim
    casting.py           BTCasting       — synchronous cast API
    registry.py          BuildRegistry   — moved out of BuildMgr.py
  BldMgrBT.py            BldMgrBT(CombatServices, BTCasting)
                           + identity, fallback, tree lifecycle
```

**Mixins, not composition.** 330 skill-module call sites read `self.build.<method>`
directly (`IsSkillEquipped` 130, `IsInAggro`/`IsCloseToAggro` 68, `GetCustomSkill`
36, `Resolve*AllyTarget` 34, `GetPartyHealthDelta` 12, ...). Mixin inheritance keeps
every one of those working untouched. Composition would require 30+ delegation
shims for zero gain during a migration whose whole point is minimizing churn.

**Two pieces, not five.** The size distribution is ~1000 lines of services against
~200 of everything else. Splitting identity and fallback into their own bases would
be ceremony.

### What dies with BuildMgr

- `tick_state` / `SetTickSuccess` / `SetTickFailure` / `DidTickSucceed` —
  `NodeState` becomes the only result channel
- `_local_ooc_handler` / `_local_combat_handler` / `_local_skill_casting_handler`
  and their `Set*Fn` registrars
- `_yield_from_handler`, `_process_phase`, `_process_skill_casting_phase`, `Tick`
- the generator cast API

---

## 4. Phases

Every phase ends with a working repo.

### A. Extract CombatServices — pure move, zero behavior change

Move the ~1000 lines and their 11 state fields into
`build_src/combat_services.py`. Make `BuildMgr(CombatServices)`. Nothing else
changes; every existing build and skill module keeps working because method
resolution is identical.

Verify: repo-wide import + compile; DervBoneFarmer and one generator party build
(`Martyr`, `Necro_Prot`) run unchanged.

This is the phase that makes deletion possible at all — the services cannot be
trapped inside the class being deleted.

### B. Synchronous cast API

Add `build_src/casting.py` with non-generator `CanCastSkillID` / `CastSkillID` /
`CastSkillIDAndRestoreTarget` / `CastSkillSlot`, bodies lifted from the existing
ones minus `yield from`. Spirit casting goes to a tier 3 subtree factory rather
than being ported.

`BuildMgr` keeps its generator versions during transition — the two APIs coexist
under different names until phase E retires the generator ones.

### C. BldMgrBT standalone

Re-declare as `BldMgrBT(CombatServices, BTCasting)`. Absorb identity + fallback +
`ScoreMatch` + `ValidateSkills` + `LoadSkillBar`. Drop the `BuildMgr` import
entirely. Move `BuildRegistry` to `build_src/registry.py` and type it against a
protocol both bases satisfy, so it can discover builds of either kind during
migration.

At this point `BldMgrBT` is standalone and `DervBoneFarmer` is a build that no
longer touches `BuildMgr` anywhere in its MRO.

### D. Re-parent SkillsTemplate

`Builds/Skills/SkillsTemplate.py:18` is currently `SkillsTemplate(BuildMgr)`, so
all 69 skill modules are `BuildMgr` subclasses. Re-parent to
`SkillsTemplate(CombatServices, BTCasting)`.

The ~330 paradigm-agnostic call sites need no edit. The ~148 cast sites switch to
the synchronous API — mechanical, and mostly deleting `yield from`.

Largest single phase; batch it by profession directory so each batch is reviewable.

### E. Migrate builds

45 build classes, one at a time or by directory. For each: change the base to
`BldMgrBT`, replace the `Set*Fn` handler registration with `build_rotation_tree()`,
and convert `ProcessCombat`/`ProcessOOC` bodies into tree branches.

This is the only phase involving real rewriting rather than moving. Builds that
are already thin wrappers around skill modules convert nearly mechanically; builds
with hand-written generator rotations need genuine translation.

Order by risk: farm/solo builds first (fixed bars, no party targeting), party
support builds last.

### F. Delete BuildMgr

Once no class subclasses it and no module imports it, delete `BuildMgr.py`.
Remove the `BTBuildMgr = BldMgrBT` alias at the same time — by then it is the
only name.

---

## 5. Interaction with the HeroAI BT migration

`HeroAIBTEngine` currently satisfies a driver protocol that includes
`DidTickSucceed()` (read by `headless_tree.py`, the widget, and `ticks.py:173`).
That method dies in phase F, so the drivers move to reading `NodeState` directly.

Sequencing: **finish validating the HeroAI BT engine before starting phase A.**
The current `BldMgrBT(BuildMgr)` inheritance costs nothing but unused methods in
the meantime, and stacking an unproven rotation engine on top of a large
structural refactor makes any regression ambiguous between the two.

Phase A can begin the moment the toggle has run a live party successfully.

---

## 6. Cost and honest risk

| | |
|---|---|
| Mechanical (move/rename, verifiable by "nothing broke") | Phases A, B, C, D — the bulk |
| Genuine rewriting | Phase E only, 45 builds, risk-ordered |
| Total consumers touched | 45 builds + 69 skill modules |
| Call sites needing edits | ~148 cast sites; the other ~330 are untouched |

Main risk is phase E: hand-written generator rotations do not always have a
one-to-one tree expression, particularly where a generator's implicit
frame-to-frame position was doing work nobody wrote down. Expect a handful of
builds to need behavior decisions rather than translation. Those are the ones to
do last, individually, with the build's owner watching it run.
