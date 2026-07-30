# Restructure register

Every deliberate divergence from upstream, numbered. The number is the handle:
it appears in `layout.toml` notes, in gate code, and in commit messages, so any
piece of machinery can be traced back to the decision that put it there.

This is not a changelog. It records **standing decisions that stay true** — if a
rule is reversed, its entry says so rather than being deleted.

| id | decision | status |
|---|---|---|
| [RS-000](#rs-000) | `rules/` outranks `docs/` | active |
| [RS-001](#rs-001) | `Py4GWCoreLib` → `Core` | active |
| [RS-002](#rs-002) | `Sources/` demoted to `dev/reference/` | active |
| [RS-003](#rs-003) | widgets that are tasks become scripts | active, 1 of ~146 done |
| [RS-004](#rs-004) | breakage outside a protected pack is a deprecation | active |
| [RS-005](#rs-005) | packs cannot reach their own `lib/` | **OPEN — blocks 6 scripts** |
| [RS-006](#rs-006) | the tier map is instruction, not reference | active |
| [RS-007](#rs-007) | `BuildMgr` is retired; `BldMgrBT` is the only build base | active |

---

## RS-000

**`rules/` outranks `docs/`.**

Where the two disagree, `rules/` is current and `docs/` is history. `docs/` is
never corrected to match — a handover that was accurate when written stays as
written.

*Why.* Upstream owns most of `docs/` and rewrites it freely. Standing decisions
need somewhere upstream has no vote.

*Enforced by.* `[[rule]] match = "rules/**"` → `keep` at the top of
`tools/reforge/layout.toml`.

## RS-001

**The core library is imported as `Core`, not `Py4GWCoreLib`.**

*Why.* Predates this register; it is the change the whole layout transform was
built around. Upstream's name describes upstream's project.

*Cost.* 3678 import statements across 1009 files, rewritten mechanically every
sync. Also rewrites `'Py4GWCoreLib/...'` string paths, which is correct here
because the directory really was renamed.

*Enforced by.* `[[codemod]] kind = "module_rename"`. `verify.py` greps for
survivors.

## RS-002

**`Sources/` is reference material and lives at `dev/reference/`.**

Two carve-outs stay at the root path because live code depends on their location:
`Sources/marks_sources/` (a hardcoded string path at
`Widgets/Items/TeamInventoryViewer.py:34`) and `Sources/ApoSource/InvPlus/`.

*Why.* It was a dumping ground of vendored third-party bot libraries sitting in
the import root as though it were first-party code.

*Cost.* 21 leaf files still said `Sources.` after the move and stopped loading.
Fixed by the codemod below; that is what took leaf breakage from 52 to 32.

*Enforced by.* `[[codemod]] kind = "import_rename"`, with `keep` entries for the
two carve-outs.

> **Why `import_rename` and not `module_rename`.** A blunt token rename was
> written first and was wrong twice over. The carve-outs are imported *from files
> that do get rewritten*, so the exemption has to be per-import, not per-file —
> a file-level `exclude` cannot express it. And `Sources/` also appears as a real
> directory in a string path, where dots would break it. `import_rename` only
> touches `from x` / `import x` statements and honours a `keep` list of dotted
> prefixes. If you add a codemod, this is the one to copy.

## RS-003

**A widget whose only entry point is `main()` is a script, not a widget.**

Widgets are autoloaded and get `draw()` / `update()` called every frame. A module
with no `draw()` and no `configure()` gains nothing from that and pays the import
cost every session. Those move to a script pack and run on demand.

*Done.* `EliteSkillsCapture.py` — 12617 lines, `main()` only. Was autoloaded from
`Widgets/World/`, now `Scripts/py4gw-tasks/scripts/`.

*Not done.* [SCRIPT_MIGRATION_LIST.md](SCRIPT_MIGRATION_LIST.md) holds ~146 more
candidates with per-file risk notes.

*Deliberately not converted.* `Map Overlay.py` and `Style Manager.py` have real
`draw()` bodies. They are broken for other reasons — see DEPRECATED.md — but they
are genuinely widgets.

*Enforced by.* `tier = "script"` in the manifest. `apply.py` synthesizes `.widget`
markers from widget-tier destinations only, so changing the tier is the whole
conversion. The matching `[[legacy_id]]` moves from `widgets:` to `scripts:` so
the widget manager stops trying to restore enabled-state for it.

## RS-004

**A leaf that fails to load outside a protected pack is unwanted, not broken.**

Protected today: `Scripts/py4gw-marks-corner/`. Breakage there is a bug and stays
on the gate. Breakage anywhere else in `Widgets/` or `Scripts/` is inherited
community code we do not maintain, and gets deprecated instead of fixed.

Deprecated means: listed in [DEPRECATED.md](DEPRECATED.md), skipped by the gate,
**still in the tree and still in git**. Nothing is deleted. Fix one and it leaves
the list on the next run.

*Why.* 285 leaf files, most of them other people's bots. Without this rule every
sync is hostage to whether a stranger's 2024 farming script still imports.

*Cost.* 25 files currently deprecated. The ledger records for each whether
upstream's own copy loads, so "we broke it" never gets quietly filed under
"community code was already rotten".

*Enforced by.* `PROTECTED` in `qa/breakage.py` derives the ledger;
`qa/test_imports.py` reads it and skips. To protect a pack, add its prefix to
`PROTECTED` — one place, not two.

## RS-005 — OPEN

**Script packs cannot import their own `lib/`. There is no fix in place.**

The manifest routes shared code to `<pack>/lib/` (`tier = "lib"`), but nothing
puts that directory on `sys.path`, and pack directories are named with hyphens
(`py4gw-marks-corner`) so they are not importable as packages either.
`Core/py4gwcorelib_src/script_manager/loader.py` loads scripts with
`spec_from_file_location` and never touches `sys.path`.

*Who this blocks.* Six of the seven remaining protected-pack failures. They want
`from Bots.marks_coding_corner.utils.loot_utils import ...`; the file is sitting
at `Scripts/py4gw-marks-corner/lib/loot_utils.py`, reachable by no import
statement that exists.

*Why it is still open.* Every fix touches `Core/`, which is upstream-owned and
therefore conflict surface. That is a decision about how much divergence to buy,
not a detail to settle silently.

*Options, cheapest first.*

1. Teach the loader to prepend `<pack>/lib` to `sys.path` before `exec_module`.
   Smallest change; adds one upstream-owned file to the conflict surface.
2. Put shared pack code in an importable top-level package (`packlib/`) and
   rewrite the imports with a codemod. No `Core/` change at all, so no new
   conflict surface — but it abandons the per-pack `lib/` idea the manifest
   already encodes.
3. Leave it. The six scripts stay broken and the `lib` tier stays decorative.

Option 2 is the one that costs upstream nothing.

## RS-006

**The tier map is part of the instruction set, and lives here — not in `docs/`.**

[TIER_MAP.md](TIER_MAP.md) says which tier a file belongs to, what changing it
costs, and which parts upstream contests. That is a rule about where new code
goes, not background reading, so it sits in `rules/` and outranks `docs/`.

*Why it moved.* It only ever existed as an untracked file in the fork's working
copy, while `AGENTS.md` §5 and a `layout.toml` override both cited it by its old
`docs/` path. Two references pointed at a file this repo did not have — and
because it was untracked, one `git clean` in the fork would have destroyed the
only copy.

*What changed on the way.* 29 paths translated through the manifest, a standing
table marking which parts are current versus superseded, and the stale
`docs/tier_map_and_separation_plan.md` override deleted — `rules/**` already
covers it.

*Enforced by.* `tiercheck.py` implements Part 6's assignment rule. Its known
failures — the facade's eager 17-module `HeroAI` closure, and
`Core/py4gwcorelib_src/AutoInventoryHandler.py` reaching into `dev/reference` —
are Part 4's unfinished Move 2. Do not silence them; fix, or waive with a reason
in `tier_map.toml`.

## RS-007

**`BuildMgr` is retired. `BldMgrBT` is the only build base class, and
`Core/Builds/` keeps only the `Skills/` layer.**

Upstream ships two execution models for the same rotations: `BuildMgr`, a
generator ladder, and the behaviour-tree stack we built on top of
`CombatServices`. Every rotation under `Core/Builds/<Profession>/` had a 1:1
twin under `Core/BTBuilds/`, so the generator half was pure duplication — two
files to keep in step for every skill-bar change, with only one of them ever
ticked by HeroAI.

*What went.* `Core/BuildMgr.py`, `Core/Builds/BuildTemplate.py`, and the
profession subtrees `Any/ Assassin/ CombatAutomatorExcluded/ Dervish/
Elementalist/ Mesmer/ Monk/ Necromancer/ Paragon/ Ranger/ Ritualist/ Warrior/`
— 156 upstream files.

*What survived, and where.*

- `BuildRegistry`, `is_purpose_specific_build`, `FARM_BUILD_PACKAGE` →
  `Core/build_src/build_registry.py`. Discovery keys on the `is_build_type`
  class marker, never `issubclass`, so it never depended on `BuildMgr` in the
  first place.
- The `Callable` aliases `BuildCoroutine`, `BuildHandler`, `TargetPredicate`,
  `CustomSkillMutator` → `Core/build_src/combat_services.py`, next to the
  `CombatServices` base both engines already shared.
- `LoadSkillBar` → `CombatServices`. It existed only on `BuildMgr` while four
  protected-pack scripts called it through `bot.config.build_handler`.
- `Core/Builds/Skills/**` — 69 modules, retargeted from `BuildMgr` hints to
  `CombatServices`. `SkillsTemplate` subclassed `BuildMgr`; it now subclasses
  `BldMgrBT` with `is_build_type = False`, so the registry stops instantiating
  a container that was never a build.
- `DervBoneFarmer` → `Core/BTBuilds/Dervish/D_A/` (already BT-native).
  `DervDustFarmer` and `DervFeatherFarmer` → `dev/reference/buildmgr_builds/`,
  kept as reading material for a rewrite rather than ported.

*The fallback chain changed shape.* `Core/Builds/Any/HeroAI.py` held the tree's
only `is_fallback_candidate=True` build, so `BuildRegistry.ResolveFallback` now
always returns `None`. Nothing regressed: BT builds wire their fallback
explicitly with `SetFallback("HeroAI", HeroAIBTEngine(...))`, and the seven that
still named the generator build were switched over with the rest.

*What knowingly broke.* Six deprecated-tier community-bots leaves —
`OutpostRunnerV2`, the Barbarous Shore / Hells Precipice / Pongmei chestruns,
and the legacy `YAVB` pair — were the sole consumers of
`CombatAutomatorExcluded/`. Under [RS-004](#rs-004) they are deprecated rather
than ported; the ledgers record them with origin `ours`.
`Scripts/py4gw-marks-corner/DervDustFarm.py` and `DervFeatherFarm.py` carry
dangling imports until their builds are rewritten — those are protected-pack
files and therefore real bugs, deliberately accepted for the length of that
rewrite.

*Enforced by.* Five `dest = "drop"` rules in `tools/reforge/layout.toml`, each
noting RS-007, plus the three Dervish `[[override]]` moves that outrank them.
`Core/Builds/__init__.py` is emptied by overlay on `main` — the manifest cannot
edit file contents, and the package must stay importable for
`Core.Builds.Skills`.
