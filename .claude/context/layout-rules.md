# Layout rules — always loaded

This tree is generated. Getting this wrong loses work silently.

## Three repos

`mypy4gw_main` (here — freeform, primary source of changes) → `Py4GW_Reforged`
(the fork; the only one with a GitHub remote, staging for PRs) →
`apoguita/Py4GW_Reforged` (upstream; arrives here through `vendor`).

Commit freely here. Only what you deliberately publish goes through the fork.
Nothing has to flow upward — `backport.py` marks layout-only changes as such.

## Credentials are in this working tree

`accounts.json`, `Py4GW.ini`, `Settings/<account>/`, `json/<account>/` are real
account data, gitignored. **Never `git add -A` without checking.** Note that
`git check-ignore` exits 0 when any pattern matches, negations included — read
the printed rule, not the exit code.

## Branch discipline

- `vendor` — pristine `upstream/main`. **Fast-forward only, never edit.**
- `base` — `vendor` + toolchain and identity. **Commit tool/manifest edits HERE.**
- `layout` — `apply.py(base)`. **Generated, disposable, never hand-edit.**
- `main` — `layout` + your work. Commit everything else here.

Toolchain and work are separated because they rebase against different things. A
tool commit on `main` replays onto a `layout` that already contains its own final
state and conflicts with itself.

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
in `rules/TIER_MAP.md`. Do not silence — fix, or waive with a
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

**A token rename is the wrong tool whenever the move has carve-outs.** Use
`kind = "import_rename"` instead — it rewrites only `from x` / `import x` and
honours a `keep` list of dotted prefixes. `module_rename` cannot express the two
cases that actually come up: a kept subtree imported *from files that do get
rewritten* (per-import, not per-file, so `exclude` is useless), and a name that
is also a real directory in a string path, where dots break it. Both bit the
`Sources` → `dev.reference` rename. → RS-002.

## rules/ outranks docs/

`rules/` holds our standing decisions and the generated ledgers. `docs/` is
reference — upstream's material plus our own finished handovers — and is **never
corrected to match**; where the two disagree, `rules/` is current.

Read `rules/RESTRUCTURE.md` before proposing any structural change: every
deliberate divergence has an `RS-nnn` number that also appears in the
`layout.toml` note or gate code enforcing it. New rule → next number, written up
there, number referenced from the enforcing code. No enforcement means it is a
note, not a rule — mark it **OPEN**.

`BREAKAGE.md`, `DEPRECATED.md`, `DIVERGENCE.md` and `upstream-verdicts.tsv` are
**generated**. Never hand-edit them; change the rule and regenerate.

## Broken leaves are usually not bugs

Under RS-004, a widget or script that fails to load **outside**
`Scripts/py4gw-marks-corner/` is deprecated, not broken: listed in
`rules/DEPRECATED.md`, skipped by the gate, left in the tree. Do not
"helpfully" fix those — the 285 leaf files are mostly other people's bots.
Breakage *inside* a protected pack is a real bug.

Before touching any leaf, check `origin` in the ledger: `ours` means our
transform broke a file that worked upstream, `inherited` means it was already
broken before the fork. Those deserve opposite responses, and the difference is
not guessable from the traceback.
