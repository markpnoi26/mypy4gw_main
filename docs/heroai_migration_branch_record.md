> **Historical.** The Reforged line is inbound-only since 2026-08-06 (RS-008);
> the branch is now part of the fork's archive, reachable via `forwardport.py`.

# `HEROAI_MIGRATION` — branch record

What this branch is, what is actually in it, and the order it comes apart into
upstreamable PRs.

The branch is **not** one feature. It accumulated five independent workstreams
over the course of getting a BT-driven farm loop running end to end. Each one is
separately stable and separately landable; they share a branch only because they
were written in the same stretch of work.

This document is the index. The detailed design records live in the per-workstream
docs linked below — this file does not restate them.

Base: `upstream/main` at merge-base `1221cfd1`.
Total: 128 files, ~16.5k insertions, ~2.2k deletions.

---

## Why the branch exists

The starting goal was narrow: get HeroAI's combat rotation off the hand-rolled
`BuildMgr` generator engine and onto behaviour trees, so a build reads as a tree
instead of a pile of interleaved `yield` statements.

Pulling that thread exposed four other things that had to be fixed or built before
the BT rotation could run unattended for more than a few minutes:

- Builds needed a shared combat vocabulary that neither base owned → `CombatServices`.
- A farm loop that salvages its own drops kept crashing the client → the salvage /
  merchant fixes.
- Scripts buried in `Widgets/` could not be discovered or reloaded independently →
  `script_manager`.
- Debugging a 40-minute bot run with no step trace was not viable → `StepLogger`.

So the branch is best read as *"BT rotation, plus everything that had to be true
for it to work."*

---

## The five workstreams

### 1. HeroAI BT combat engine — ~9.7k ins / ~1.6k del, 94 files

The main event. `BuildMgr`'s paradigm-agnostic combat utilities extracted into
`build_src/combat_services.py` (~1700 ln, 64 methods) so `BuildMgr` and the new
`BldMgrBT` can share them; `BuildMgr.py` drops 2221 → 671 lines. Adds the BT engine
(`HeroAI/bt/**`), the router (`HeroAI/engine.py`), and 33 builds ported to
`Py4GWCoreLib/BTBuilds/**`. Gated per-account behind an off-by-default
`RotationEngine.UseBT` toggle in the HeroAI config UI.

- Design + rationale: `docs/heroai_bt_migration_complete.md`
- **PR sequencing: `docs/heroai_bt_pr_plan.md`** — 12 PRs, ordered so every one
  before the last is a provable runtime no-op
- Porting guide: `docs/build_port_to_bldmgrbt.md`
- Retirement path for the legacy base: `docs/buildmgr_retirement_blueprint.md`

Do not try to land this as one PR. Follow the plan; the ordering exists to defend a
specific hazard (a build-resolution tie that legacy currently wins by `rglob`
ordering luck, documented in the plan).

### 2. Salvage / identify / merchant — 5 files ✅ **LANDED**

Merchant restock waits on the `"MERCHANT"` action queue, which nothing ever enqueues
onto, so the waits never waited. Materials-confirm dialog located by frame hash plus
a fixed child offset that drifts between sessions. Salvage fired on a fixed delay and
crashed the client when it hit an already-open confirm dialog.

→ **[apoguita/Py4GW_Reforged#32](https://github.com/apoguita/Py4GW_Reforged/pull/32)**
(branch `FIX_SALVAGE_IDENTIFY_MERCHANT`, rebased clean onto `main`)

Fully self-contained: touches only `Inventory.py`, `routines_src/Sequential.py`,
`routines_src/yield_src/{items,merchant}.py`, `routines_src/behaviourtrees_src/items.py`.
No dependency on any other workstream here. This is why it went first.

### 3. Script manager — 8 files, ~1.5k ins 📝 **DRAFT PR OPEN**

Flattens runnable scripts out of `Widgets/` into `Scripts/` with declarative
metadata. Adds `py4gwcorelib_src/script_manager/` (discovery registry + dependency-aware
loader, with tests) and the `ScriptManagementSystem` widget. `Scripts/DervCOFFarmBT.py`
is the first script under the new scheme.

→ **[apoguita/Py4GW_Reforged#33](https://github.com/apoguita/Py4GW_Reforged/pull/33)**
(branch `SCRIPT_MANAGER_SYSTEM`, **draft** — the widget and runner wiring are still WIP)

- Migration inventory: `docs/SCRIPT_MIGRATION_LIST.md`

Independent of workstream 1. Two things stay behind on this branch and are
deliberately **not** in #33:

- `Scripts/DervCOFFarmBT.py` — imports `Bots.marks_coding_corner.utils.*`, the
  branch-modified `DervBoneFarmer`, and the BT item nodes from #32. It cannot stand
  on `upstream/main`, so `Scripts/` lands empty in the PR. `ScriptRegistry.scan_mtimes`
  handles a missing root, so the widget renders an empty list rather than erroring.
- `test_discovery.py` / `test_loader.py` — 35 cases, held here while the API moves.
  The repo has no test runner configured, so landing them is a maintainer call.

Active development stays on this branch; `SCRIPT_MANAGER_SYSTEM` is re-cut from it
and force-pushed rather than built up incrementally.

Run the suite with `-t` pointed at the module's own folder, or unittest discovery
walks up into `Py4GWCoreLib/__init__.py` and pulls in `PySystem`, which only exists
in-process:

```
.venv/Scripts/python.exe -m unittest discover \
    -s Py4GWCoreLib/py4gwcorelib_src/script_manager \
    -t Py4GWCoreLib/py4gwcorelib_src/script_manager
```

### 4. Botting step logger — 3 files, ~240 ins

`botting_src/step_logger.py` attaches to `BottingClass` and writes a timestamped
per-step trace so a long unattended run can be reconstructed after the fact.

**Not upstreamable as-is.** Two rule violations, both easy:
- `step_logger.py:110` uses raw `open(path, "a")` and `os.makedirs` — the
  persistence jail routes all disk writes through `Settings` / `JsonFactory`.
  A rolling append-log is neither INI nor structured JSON, so this needs a
  decision from the maintainer about whether the native side should grow a log
  sink, rather than a workaround here.
- `_sanitize`, `_project_root`, `_now_ms`, `_fmt_ts` are underscore-prefixed
  module functions; house style forbids introducing new ones.

Also in this group: `botting_tree_src/ticks.py` has a **commented-out** upkeep log
block that must be either deleted or restored before this goes near a PR — leaving
commented-out code in the diff will (correctly) get it bounced.

### 5. Enemy tracker data — 2 files, ~1.9k ins

`EnemyData/EnemyTrackerData.json` + `EnemyTrackerNames.en.json` regenerated with
substantially more coverage. Pure data. Zero code coupling — this can go up on its
own at any time and is probably the cheapest remaining win after #2.

---

## Not for upstream

| Path | Why |
|---|---|
| `Bots/marks_coding_corner/**` | Personal bot directory. `DervCOFFarm.py` was deleted here because it was superseded by `Scripts/DervCOFFarmBT.py`; the `utils/` changes are local cleanups. |
| `Py4GW_Reforged_Launcher.exe` | Rebuilt binary (18.5 MB → 15.9 MB). Belongs to the launcher's own release flow, not a feature branch. |
| `Py4GWCoreLib/Builds/Dervish/D_A/DervBoneFarmer.py` | Local farm build edits, entangled with workstream 1's `BTBuildMgr` alias. Re-derive against whatever lands. |

---

## Landing order

```
  #32  salvage / identify / merchant     ✅ open       independent
  #33  script_manager                    📝 draft      independent of BT work
  #34  BT 1/12 — CombatServices          ✅ open       first slice of the long haul
   3   EnemyTracker data                 ready        independent, pure data
   4   HeroAI BT engine (11 PRs left)    see plan     the long haul
   5   step_logger                       blocked      persistence-jail decision
```

BT slices are named `HEROAI_BT_NN_SHORT_NAME` / `[BT n/12] ...`; the running
table lives in `docs/heroai_bt_pr_plan.md`.

3 does not depend on 4 and should not wait on it. 5 needs a maintainer answer
before any code is written.

## Keeping the branch alive

This branch stays as the integration point while the pieces land. As each PR merges
upstream, merge `upstream/main` back in rather than rebasing — the branch has merge
commits already (`10f1f780`, `e12f8db7`, `0377fdab`) and rebasing 23 commits across
128 files would be worse than the duplicate-diff noise merging produces.

Slices are cut by branching off `upstream/main` and `git checkout HEROAI_MIGRATION --`
only the files in scope, then verifying independently. That is how #32 was produced,
and it is the reason it applies cleanly despite the branch being 23 commits deep.

Verification available: byte-compile via `.venv/Scripts/python.exe`, and `pyflakes`
diffed against the `main` baseline for the touched files. There is no CI, no pytest
config and no global test command — do not assume one.

Do **not** run `black` across touched files when cutting a slice. These files are not
black-clean on `main` either, so formatting them buries the real change in unrelated
reflow.
