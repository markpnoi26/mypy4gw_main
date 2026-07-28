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
