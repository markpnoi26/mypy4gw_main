# Retire BuildMgr — staged execution plan

Audience: the implementing agent (Opus 5). Goal owner: Mark.
Objective: remove the legacy generator-build base class `BuildMgr` and the
duplicated generator rotations under `Core/Builds/`, keeping everything the BT
stack (`BldMgrBT` + `Core/BTBuilds` + `Core/Builds/Skills`) actually uses.

## Process rules — read before any edit

1. **Never run `git add`, `git commit`, or any staging command.** Mark is the
   only one who touches git state. You edit files and report.
2. **Execute ONE stage, then stop and report.** Mark reviews, tests in the
   client, and commits before the next stage begins. Do not start stage N+1 in
   the same run as stage N, even if everything went smoothly.
3. **Decision gates** are marked `GATE:`. At a gate, ask Mark and wait. Do not
   pick a default and proceed.
4. Code style (from `.claude/context/code-style.md`): no new `_underscore`
   names, near-zero comments, minimal docstrings. When *moving* existing code,
   keep its current names — do not rename underscore names you relocate.
5. `Core/` and `HeroAI/` are upstream-owned paths ("Careful" tier in
   `.claude/context/layout-rules.md`). Every edit there is overlay divergence
   carried on `main`. That is accepted for this task, but keep edits minimal —
   no drive-by cleanups in files you touch.
6. Do NOT edit anything under `Scripts/py4gw-community-bots/`,
   `Textures/Module_Icons/`, or other RS-004 deprecated leaves. They are
   allowed to break; the ledgers record it (Mark regenerates those — never
   hand-edit `rules/DEPRECATED.md`, `rules/BREAKAGE.md`).
7. Verification after each stage (no test suite exists; `import Core` only
   works inside the injected client, so imports cannot be smoke-tested here):
   - `../Py4GW_Reforged/.venv/Scripts/python.exe -m compileall -q Core HeroAI`
     (Bash tool, repo root; bare `python` is the wrong interpreter).
   - `../Py4GW_Reforged/.venv/Scripts/python.exe tools/reforge/tiercheck.py`
     — it fails by design today; capture output BEFORE your edits and diff:
     no NEW violations allowed.
   - Grep sweeps listed per stage.
   - Runtime verification is Mark's, in the client. Say so in your report.

## STATUS — stages 1 and 2 are DONE (uncommitted, awaiting Mark's testing)

Stage 3 is the only remaining stage. Read the corrections below before running
it: three "Ground facts" in the original plan were wrong and are fixed in place.

Corrections found during execution:

- **`LoadSkillBar` had no BT equivalent.** It lived only on `BuildMgr`, while
  four protected-pack scripts call `bot.config.build_handler.LoadSkillBar()`.
  It now lives on `CombatServices` (next to `IsSkillEquipped`), so both engines
  inherit it. Stage 3 must NOT reintroduce it.
- **Only `DervBoneFarmer` was BT-native.** `DervDustFarmer` and
  `DervFeatherFarmer` are `BuildMgr` subclasses. Per Mark: do NOT port them —
  they are demoted to `dev/reference/buildmgr_builds/` as rewrite material.
- **`HeroAI_Build` is reachable, but only from doomed code.** 22 generator
  builds call `SetFallback("HeroAI", HeroAI_Build(...))`, all inside
  `Core/Builds/**`, plus one deprecated community-bots leaf. The BT world's
  equivalent is `HeroAIBTEngine` (`HeroAI/bt/bt_engine.py:19`). So stage 0's
  verdict is **A-by-consequence**: it dies with its only consumers. The one BT
  build that referenced it (`DervBoneFarmer`) was switched to `HeroAIBTEngine`.

Done in stage 1: `BuildRegistry` + `is_purpose_specific_build` +
`FARM_BUILD_PACKAGE` now live in `Core/build_src/build_registry.py`;
`BuildHandler` added to `combat_services.py`; `Core/BuildMgr.py` is a
re-export shim (383 lines, class body only); `bt_engine.py` and `ui_base.py`
import from the new home.

Done in stage 2: botting stack uses `None` as the no-build sentinel (plus a
`None` guard in `UI_src.py`, whose debug panel walked `build_handler.__dict__`);
`VaettirMarksMods.py` AND `VoltaicSpearTeamFarm.py` repointed at
`BTBuilds/FarmBuilds` twins; the three Derv farmers relocated with manifest
overrides already written in `layout.toml` (base-branch material).

Known-broken pending Mark's rewrite: `DervDustFarm.py` and `DervFeatherFarm.py`
in the protected pack have dangling imports.

## Ground facts (verified 2026-07-30 — trust these, don't re-derive)

`Core/BuildMgr.py` (upstream-owned, arrived via layout commit) contains four
separable things:

| Lines | What | Fate |
|---|---|---|
| 21–24 | aliases `BuildCoroutine`, `BuildHandler`, `TargetPredicate`, `CustomSkillMutator` | `BuildCoroutine`/`TargetPredicate`/`CustomSkillMutator` already exist in `Core/build_src/combat_services.py:16-18`; only `BuildHandler` is unique — move it there |
| 28–383 | `class BuildMgr(CombatServices)` | delete (stage 3) |
| 387–395 | `FARM_BUILD_PACKAGE`, `is_purpose_specific_build` | relocate (stage 1) |
| 398–618 | `class BuildRegistry` | relocate (stage 1) — still load-bearing for the BT path |

Other verified facts:

- `BldMgrBT` (`Core/BldMgrBT.py`, ours) does NOT inherit `BuildMgr`; both sit
  on `CombatServices`. `BldMgrBT` defines its own `BuildCoroutine` (line 8) and
  provides `ProcessSkillCasting`/`ProcessCombat`/`ProcessOOC` (lines 297–305),
  so the Botting build ticker drives BT builds unchanged.
- The `Core/Builds/Skills/**` layer (69 modules, upstream-owned) calls only
  `CombatServices` methods at runtime (`IsSkillEquipped`, `CastSkillID`,
  `CastSkillIDAndRestoreTarget`, `SpiritBuffExists` — all defined in
  `combat_services.py`). Its `BuildMgr` dependency is imports/type hints only —
  EXCEPT `SkillsTemplate.py:18`, which really subclasses `BuildMgr`.
- `BuildRegistry` discovery uses the `is_build_type` class marker
  (`BuildMgr.py:447`), not `issubclass`, precisely so `BldMgrBT` builds are
  discoverable. The `value is BuildMgr` guard at line 441 is redundant with the
  `value.__module__ != module.__name__` check at 443 (the base classes live at
  `Core/` root, never inside the scanned `Core.Builds`/`Core.BTBuilds`
  packages).
- `HeroAI` runtime does not tick `BuildMgr` builds:
  `HeroAI/bt/bt_engine.py:123-138` filters to BT-native builds. HeroAI's only
  imports are `BuildRegistry` (+ `is_purpose_specific_build`) at
  `bt_engine.py:130` and `ui_base.py:868`.
- `Core/Builds/Any/HeroAI.py` (`HeroAI_Build`) is the ONLY
  `is_fallback_candidate=True` build in the whole tree. Deleting it makes
  `BuildRegistry.ResolveFallback` always return `None`. Its reachability is
  stage 0's question.
- Botting stack runtime deps: `Core/botting_src/config.py:116` defaults
  `self.build_handler = BuildMgr()`; `Upkeepers.py:121` uses
  `type(build) is BuildMgr` as the "no real build" sentinel;
  `Core/Botting.py:6,45,361` type hints; `Core/botting_src/event.py:245`
  imports `BuildMgr` and never uses it (dead import).
- Protected pack (`Scripts/py4gw-marks-corner/`, must keep working):
  `VaettirMarksMods.py:23-25` imports `BuildMgr` plus the GENERATOR
  `SF_Ass_vaettir` / `SF_Mes_vaettir` from `Core/Builds/...`. BT twins exist at
  `Core/BTBuilds/FarmBuilds/Assassin/A_Me/SF_Ass_vaettir.py` and
  `.../Mesmer/Me_A/SF_Mes_vaettir.py` with the same class names and
  `SetStuckSignal` API (the Mes twin intentionally trips at `>3` not `>0` —
  documented in its header, do not "fix").
- `Core/Builds/CombatAutomatorExcluded/` (7 generator builds + BuildDangerHelper)
  is consumed ONLY by deprecated-tier community-bots leaves (OutpostRunnerV2,
  three chestruns, legacy YAVB). No protected-pack consumer.
- ~~The three Derv farmers are all BT-native.~~ **WRONG — see corrections.**
  Only `DervBoneFarmer` was; it now lives at
  `Core/BTBuilds/Dervish/D_A/DervBoneFarmer.py`. `DervDustFarmer` and
  `DervFeatherFarmer` were `BuildMgr` builds and now live at
  `dev/reference/buildmgr_builds/`. All three have manifest overrides.
  `VoS_Grenths_Aura_Farmer` already has a BT twin in `Core/BTBuilds/Dervish/D_A/`,
  so `Core/Builds/Dervish/` is now safe to delete wholesale.
- Every other rotation under `Core/Builds/<Profession>/` has a 1:1 BT twin
  under `Core/BTBuilds/` (verified by tree diff; only naming drift is
  `Assassin's Promise Death Magic.py` vs `Assassins Promise Death Magic.py`).
- The reforge manifest supports `dest = "drop"` rules
  (`tools/reforge/layout.toml`, e.g. lines 214–217; executed by
  `tools/reforge/apply.py run_drops`).
- Next free restructure id: **RS-007** (`rules/RESTRUCTURE.md` currently ends
  at RS-006).

---

## Stage 0 — reachability check on `HeroAI_Build` (read-only, no edits)

Question to answer: **can `Core/Builds/Any/HeroAI.py` (`HeroAI_Build`) still be
instantiated and TICKED at runtime**, or is it referenced only as an inert
fallback name?

1. Read `HeroAI/bt/bt_engine.py` fully. Trace every use of
   `self.build_registry`: `GetBestBuild`, `ResolveFallback`,
   `_iter_matchable_builds`, and what `EnsureBuildContract` does when no BT
   build matches (`matchable_bt_builds` yields nothing). Determine whether the
   registry's fallback resolution (`default_fallback_name=self.build_name`)
   can ever hand back `HeroAI_Build` and whether anything then calls
   `ProcessCombat`/`ProcessOOC`/`Tick` on it.
2. Read `HeroAI/ui_base.py` around line 868: find every use of
   `_get_build_registry()` — is it display-only (matched-build labels) or does
   anything call into the resolved build?
3. Read the rest of `Core/Builds/Any/HeroAI.py` (beyond line 60): what do its
   `ProcessCombat`/`ProcessOOC` do — does it delegate to the registry's best
   build (i.e., is it the old pre-BT auto-combat driver)?
4. Grep for other constructions: `HeroAI_Build(`, `fallback_name="HeroAI"`,
   `default_fallback_name="HeroAI"` across the tree.

Report to Mark, one of:
- **A: unreachable** — nothing can tick it → stage 3 drops it, and
  `ResolveFallback` returning `None` is the already-current behavior. State the
  evidence.
- **B: reachable** — some path still ticks it → GATE: Mark decides whether that
  path gets a BT replacement, gets removed, or `HeroAI_Build` survives as a
  ported `BldMgrBT` fallback. Do not proceed to stage 2/3 without this verdict.

STOP after reporting.

---

## Stage 1 — relocate the survivors out of `BuildMgr.py` (zero behavior change)

Everything that must outlive `BuildMgr` moves into files we own. `BuildMgr.py`
itself becomes a re-export shim (it dies in stage 3; upstream-owned files that
still import from it keep working untouched until then).

1. **Add `BuildHandler` to `Core/build_src/combat_services.py`** next to the
   existing aliases (lines 16–18): `BuildHandler = Callable[[], Any]`.
2. **Create `Core/build_src/build_registry.py`** (new file, ours). Move,
   verbatim except as noted: `FARM_BUILD_PACKAGE`, `is_purpose_specific_build`,
   `BuildRegistry` (all of `Core/BuildMgr.py:386-618`).
   - Type hints referencing `BuildMgr` inside the moved code: retarget to
     `CombatServices` (import from `.combat_services`). The registry already
     handles both build families duck-typed via the `is_build_type` marker, so
     `CombatServices` is the honest type.
   - Drop the `if value is BuildMgr: continue` guard
     (old `BuildMgr.py:441-442`) — redundant, see Ground facts. Keep the
     `__module__` check and the `is_build_type` marker check exactly as-is.
   - No other logic changes. Module-level imports stay what the moved code
     needs (`importlib`, `inspect`, `Path`, `typing`); anything from `Core`
     stays a local import exactly as in the original.
3. **Turn the moved region of `Core/BuildMgr.py` into re-exports**: delete
   lines 386–618 and the four alias definitions (21–24); replace with
   `from .build_src.build_registry import FARM_BUILD_PACKAGE, BuildRegistry, is_purpose_specific_build`
   and
   `from .build_src.combat_services import BuildCoroutine, BuildHandler, CustomSkillMutator, TargetPredicate`.
   The `BuildMgr` class body itself stays untouched.
4. **Retarget the BT-path consumers** to the new home:
   - `HeroAI/bt/bt_engine.py:130` → `from Core.build_src.build_registry import BuildRegistry, is_purpose_specific_build`
   - `HeroAI/ui_base.py:868` → same module for `BuildRegistry`.
   - `Core/BldMgrBT.py:15` docstring mentions BuildRegistry — leave it, still true.
   - Do NOT touch `Core/Builds/Any/HeroAI.py` or any `Core/Builds/**` file;
     the shim covers them until stage 3.
5. **Verify**: compileall + tiercheck diff (per Process rules), plus
   `grep -rn "from Core.BuildMgr import" HeroAI/` must return nothing.
   Confirm `Core/build_src/build_registry.py` has no module-level `Core`
   facade import (would risk import cycles at startup).

Report and STOP. (Mark commits; this stage is safe to ship alone — nothing
observable changes.)

---

## Stage 2 — remove runtime dependencies and resolve stragglers

### 2a. Botting stack: `None` replaces the bare-`BuildMgr` sentinel

Invariant to preserve: "no custom build" must behave exactly as the default
`BuildMgr()` instance behaves today — the build ticker skips it
(`Upkeepers.py:121`), and nothing else may crash on it.

1. Grep every read of `build_handler` across `Core/` and `Scripts/py4gw-marks-corner/`
   (`grep -rn "build_handler" Core Scripts/py4gw-marks-corner`). For EACH site,
   read enough context to know what a `None` value would do. Fix each site to
   be None-safe. Known sites: `config.py:113-118` (reads
   `is_combat_automator_compatible` — guard it), `Upkeepers.py:120-128`,
   `Botting.py:361` (`OverrideBuild`), `VaettirMarksMods.py:560`.
2. `Core/botting_src/config.py`: `custom_build: Optional[BuildMgr]` →
   `Optional[CombatServices]` (import from `..build_src.combat_services`);
   default `self.build_handler` to `None` instead of `BuildMgr()`.
3. `Core/botting_src/helpers_src/Upkeepers.py:111-123`: drop the `BuildMgr`
   import; sentinel becomes `if build is None:`.
4. `Core/Botting.py:6,45,361`: retarget hints to `CombatServices`; remove the
   `BuildMgr` import.
5. `Core/botting_src/event.py:245`: delete the dead `BuildMgr` import (keep
   `ThrottledTimer`).

### 2b. Repoint the protected pack at the BT twins

`Scripts/py4gw-marks-corner/scripts/VaettirMarksMods.py`:
- Line 24 → `from Core.BTBuilds.FarmBuilds.Assassin.A_Me.SF_Ass_vaettir import SF_Ass_vaettir`
- Line 25 → `from Core.BTBuilds.FarmBuilds.Mesmer.Me_A.SF_Mes_vaettir import SF_Mes_vaettir`
- Line 23 (`from Core.BuildMgr import BuildMgr`, used only for the annotation
  at line 560) → import `CombatServices` and annotate with that (or drop the
  annotation import entirely if that's the only use — check).
- Class names and `SetStuckSignal` survive; `isinstance` checks keep working.
- **Flag loudly in your report: Mark must run the vaettir farm end-to-end
  before committing this.** The BT twins are ports; only a live run proves
  behavior parity.

### 2c. Relocate the three BT-native Derv farmers out of `Core/Builds/`

`DervBoneFarmer.py`, `DervDustFarmer.py`, `DervFeatherFarmer.py` in
`Core/Builds/Dervish/D_A/` must survive the stage-3 drop of `Core/Builds/`.
Check origin first: `git log --oneline -- "Core/Builds/Dervish/D_A/DervBoneFarmer.py"`.
- If the layout commit is their only history → they are upstream-owned:
  relocation is the MANIFEST's job (a move rule in `tools/reforge/layout.toml`
  targeting `Core/BTBuilds/FarmBuilds/Dervish/D_A/`), NOT a hand-move. Write
  the rule; flag that it lands with the stage-3 manifest batch.
- If our commits created them → hand-move to
  `Core/BTBuilds/FarmBuilds/Dervish/D_A/` now and fix any importers
  (grep for `DervBoneFarmer|DervDustFarmer|DervFeatherFarmer`).

### 2d. GATE: fate of `CombatAutomatorExcluded`

Consumers are exclusively deprecated-tier community-bots leaves. Options for
Mark: **(1)** let them drop in stage 3 — the runner/chestrun leaves break and
get recorded as deprecated, origin ours (recommended: least work, consistent
with RS-004); **(2)** port the 7 builds + BuildDangerHelper to `BldMgrBT`
first (substantial: they're FSM-driven with custom signal APIs). Ask, wait.

### 2e. GATE: fate of the fallback chain (needs stage 0's verdict)

If stage 0 said **A (unreachable)**: confirm with Mark that "no fallback build
exists" is acceptable end-state — `ResolveFallback` returns `None`, and
`BldMgrBT`'s duck-typed fallback chain simply never engages. If **B**: execute
whatever Mark decided at the stage-0 gate before stage 3.

Verify (compileall + tiercheck diff + `grep -rn "BuildMgr" Core/Botting.py
Core/botting_src Scripts/py4gw-marks-corner` returns nothing), report, STOP.

---

## Stage 3 — the axe

Preconditions (all must hold — verify, don't assume): stages 1–2 committed by
Mark; vaettir runtime test passed; both gates resolved.

### 3a. Retarget the Skills layer (the only surviving `Core/Builds` subtree)

Across `Core/Builds/Skills/**` (~69 files):
1. `from Core.BuildMgr import BuildCoroutine` →
   `from Core.build_src.combat_services import BuildCoroutine` (same for any
   other alias imports).
2. TYPE_CHECKING `from Core.BuildMgr import BuildMgr` + `BuildMgr` annotations
   (`self.build: BuildMgr`, `build: BuildMgr` params) →
   `CombatServices` from `Core.build_src.combat_services`. Also
   `_whiteboard.py` and `_targeting.py` if they carry the hint.
3. `SkillsTemplate.py` is special — it SUBCLASSES `BuildMgr` and passes
   metadata kwargs to `super().__init__`. Rebase it onto `BldMgrBT`
   (`from Core.BldMgrBT import BldMgrBT`), keeping the same constructor
   metadata (`name="Skills"`, `is_template_only=True`, empty skills). Then set
   `is_build_type = False` on the class so `BuildRegistry` stops
   instantiating a container that was never a real build (today it's
   instantiated and filtered by `is_template_only`; not scanning it at all is
   the equivalent end state with less work done). Confirm `BldMgrBT.__init__`
   accepts the kwargs used — read its signature first and adapt minimally.
4. Sweep: `grep -rn "BuildMgr" Core/Builds/Skills` must return nothing.

### 3b. Delete the class and the generator twins (working tree + manifest)

Working-tree deletions on `main` (use `rm` via shell; deletions become no-ops
at the next reforge rebase once the manifest drops land):
- `Core/BuildMgr.py`
- All of `Core/Builds/` EXCEPT `Core/Builds/Skills/` and `__init__.py`.
  That means: `Any/`, `Assassin/`, `BuildTemplate.py`,
  `CombatAutomatorExcluded/`, `Dervish/`, `Elementalist/`, `Mesmer/`, `Monk/`,
  `Necromancer/`, `Paragon/`, `Ranger/`, `Ritualist/`, `Warrior/`.
  The three Derv farmers are already relocated and must NOT be re-deleted;
  `Core/Builds/Dervish/` now holds only `VoS_Grenths_Aura_Farmer.py`, which has
  a BT twin, so the directory goes wholesale.
  Do NOT add manifest drops for the three files that already carry move
  overrides — a drop and a move on one path is the AMBIGUOUS case.
  Check `Core/Builds/__init__.py` contents first — if it imports the deleted
  subpackages, prune it to whatever keeps `Core.Builds.Skills` importable.
- `Core/__init__.py:132`: remove `from .BuildMgr import BuildMgr`.
- `Core/BldMgrBT.py` docstring lines 12–24: strike the sentences that
  reference BuildMgr as extant (lines 20–24 about the 69 modules stay true —
  reword "shared with BuildMgr" to reflect it's the sole base now).
- `HeroAI/interrupt.py:9` docstring points at `Core/BuildMgr.py CastSkillID` —
  repoint to `Core/build_src/combat_services.py`.
- `HeroAI/custom_skill_src/skill_types.py:75` comment mentions
  `BuildMgr.SpiritBuffExists` — reword to `CombatServices.SpiritBuffExists`.

Manifest (`tools/reforge/layout.toml`) — these edits belong on the `base`
branch per branch discipline; make them in the working tree and tell Mark
they're base-branch material in your report:
1. First learn the upstream-path convention: find the existing rules that map
   upstream's build files into `Core/` (grep the manifest for `Builds` and for
   the rule that produces `Core/BuildMgr.py`) — `match` keys are UPSTREAM
   paths, and the examples at lines 214–217 / 434–436 show drop syntax.
2. Add `dest = "drop"` overrides/rules covering upstream's sources of:
   `Core/BuildMgr.py` and each dropped `Core/Builds/` subtree (NOT Skills).
   Prefer globs per subtree over per-file entries. Every entry gets a
   `note = "RS-007: ..."`.
3. If 2c chose the manifest route, add the move rule for the Derv farmers here.

### 3c. Record the decision

Append **RS-007** to `rules/RESTRUCTURE.md` following the format of RS-001..006
(read two of them first): what was removed, why (BT stack is the only
execution model; generator twins were duplicates), what survived and where
(`BuildRegistry` → `build_src/build_registry.py`, aliases → `combat_services`,
Skills layer retargeted, protected pack repointed to FarmBuilds twins), which
leaves knowingly broke (community-bots runners/chestruns/YAVB if gate 2d chose
drop). Reference RS-007 from every manifest note added in 3b. Do not touch the
generated ledgers.

### 3d. Final verification

1. `../Py4GW_Reforged/.venv/Scripts/python.exe -m compileall -q Core HeroAI Widgets` — clean.
2. tiercheck diff vs. pre-stage baseline — no new violations (expect some to
   DISAPPEAR: the `Core` facade's HeroAI closure may shrink).
3. `grep -rn "BuildMgr" Core HeroAI Widgets Scripts/py4gw-marks-corner` —
   remaining hits must be only: `BldMgrBT`/`BTBuildMgr` names, and (if kept)
   historical mentions in `rules/` or `docs/`. Anything else is a miss.
4. `grep -rn "from Core.Builds" Core HeroAI Widgets Scripts/py4gw-marks-corner`
   — every hit must resolve to `Core.Builds.Skills`.
5. List for Mark: exact runtime checks he should do in the client (HeroAI
   auto-combat with a BT-matched bar; HeroAI with an unmatched bar — exercises
   the now-empty fallback path; VaettirMarksMods full run; one Botting script
   that uses no custom build — exercises `build_handler = None`).

Report everything, including the base-branch/manifest handoff items and the
expectation that community-bots leaves now fail to load. STOP.
