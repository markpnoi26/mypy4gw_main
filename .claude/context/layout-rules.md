# Layout rules — always loaded

This tree is generated. Getting this wrong loses work silently.

## Branch discipline

- `vendor` — pristine `upstream/main`. **Fast-forward only, never edit.**
- `layout` — `apply.py(vendor)`. **Generated, disposable, never hand-edit.**
- `main` — `layout` + your commits. Edit freely here.

Never `git reset --hard layout` onto `main` once `main` carries work — it
discards your overlay history. **Rebase.** (This has already cost nine commits
once; they were recovered from reflog as `toolchain-history*`.)

## Where edits are safe

**Safe** — upstream never creates these, so no conflict is possible:
`Scripts/<pack>/`, `tools/`, `.claude/`, `CLAUDE.md`, `AGENTS.md`, `README.md`,
our own `docs/`.

**Careful** — `Core/`, `HeroAI/`, `Widgets/` came from upstream. An edit here
must be a commit on `main`, replayed onto every future `layout`, and it conflicts
if upstream touches the same file.

**Never** — hand-move a file. Motion is the manifest's job.

Preference order for structural change:
**manifest edit** → **wrap from a Tier 4 file you own** → **edit + upstream PR** →
carry a local patch forever.

## The only thing that conflicts

You and upstream editing the same upstream-owned file. Upstream adding, deleting,
moving or renaming whole trees is absorbed by the manifest. Keep work in paths
upstream has never created and the conflict surface stays near zero.

## Tiers are enforced, not advisory

A file may import only at or below its own tier. `tools/reforge/tiercheck.py`
exits non-zero on violation.

`0` stubs → `0.5` native_src/Scanner/Context → `1` Core domain →
`2` py4gwcorelib_src/GlobalCache/routines_src → `3` HeroAI/Builds/botting →
`4` Widgets/Scripts/dev

`tiercheck` **fails today by design**: the `Core` facade eagerly pulls 17 HeroAI
modules and `AutoInventoryHandler` reaches into `dev/reference`. Known, tracked
in `docs/tier_map_and_separation_plan.md`. Do not silence — fix, or waive with a
reason in `tier_map.toml`.

## Reading drift

`drift.py` must be clean before `apply.py`.

| Signal | Meaning |
|---|---|
| `UNCOVERED` | upstream added a path no rule covers → add a rule, prefer a glob |
| `STALE rules` | rule matches nothing → upstream moved or deleted its target |
| `STALE ids` | `legacy_id` points at a vanished path |
| `AMBIGUOUS` | two entries claim one path → add an `[[override]]` or narrow a glob |

**A stale rule plus an uncovered path in one run is a rename.** That pairing is
how upstream's 300-file restructures stay readable.

Widget id is the INI section key, so a widget move without a `legacy_id` alias
silently resets user config. `verify.py` checks this.

## Codemod hazard

The `Py4GWCoreLib` → `Core` codemod is a word-boundary token rename over
`**/*.py`. It is safe forward because `Py4GWCoreLib` is unique — and **unsafe in
reverse**, because `Core` is an ordinary English word. `tools/` is excluded from
it precisely because the toolchain describes the rename in prose and would
rewrite itself into nonsense. `backport.py` reverses only imports and quoted
paths, never bare tokens.
