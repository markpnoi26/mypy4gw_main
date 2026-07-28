# AGENTS.md

Operating rules for this repo. If you read one thing before changing a file,
read **§2 — where you may edit**.

## 0. The three repos

This repo is one of three. Know which you are in before touching anything.

```
mypy4gw_main            ← YOU ARE HERE. Freeform. Commit anything.
  |                       The primary source of changes.
  |  backport.py maps a change onto upstream's layout
  v
Py4GW_Reforged          ← the fork. Staging ground for anything going public.
  |                       Also holds work not yet ported here (HEROAI_MIGRATION).
  |  normal PR
  v
apoguita/Py4GW_Reforged ← upstream. Read-only to us; arrives via `vendor`.
```

The point of the split: **here you are free** — commit whatever, restructure
whatever, no collaboration overhead — while still taking upstream's work through
`vendor`. Only what you deliberately choose to publish goes through the fork.

Nothing is obligated to flow upward. A change that only makes sense in this
layout can simply stay here; `backport.py` reports those as "layout-only" rather
than guessing.

Other repos on this machine, for reference: `Py4GW` is the retired pre-Reforged
project. `MyPy4GW` is a working symlink-overlay runtime that ran in-client on
2026-07-22 — it is the proof that owning `Py4GW_widget_manager.py` plus
`sys.path` precedence is enough to control the runtime without touching
upstream's tree. `Py4GW_Reforged_Native`, the C++ project that builds
`Py4GW.dll`, is **not present** — Tier 0 is a binary you consume.

## 1. The one thing to understand

**This tree is generated.** `layout` is produced by running
`tools/reforge/apply.py` against `vendor` (a pristine mirror of `upstream/main`).
Most files here arrived by transform, not by authorship.

Consequence: an edit to a transformed file is not automatically durable. It
survives only if it lives as a commit on `main` that gets rebased onto each new
`layout`. Edit `layout` directly and your change is gone at the next sync.

## 2. Where you may edit — the conflict map

### SAFE — upstream never touches these, no conflict possible

| Path | Why |
|---|---|
| `Scripts/<pack>/` | packs are ours; upstream has no such tree |
| `tools/` | the transform itself, excluded from codemods |
| `.claude/`, `CLAUDE.md`, `AGENTS.md`, `README.md` | our identity; upstream's originals live in `docs/reference/` |
| `docs/` (our own files) | `docs/reference/` is upstream's, leave it verbatim |
| `tools/reforge/layout.toml`, `tier_map.toml` | **the manifest is the preferred place to make structural change** |

### CAREFUL — you may edit, but understand the cost

| Path | Cost |
|---|---|
| `Core/`, `HeroAI/`, `Widgets/` | these came from upstream. Your edit must be a commit on `main`, and it will be replayed onto every future `layout`. If upstream edits the same file, that replay conflicts. |
| anything with a `note =` in the manifest | the note explains a pin or a hazard. Read it first. |

Prefer, in order: **change the manifest** → **wrap the behaviour from a Tier 4
file you own** → **edit the upstream file and send it up as a PR** → edit and
carry it locally forever (worst).

### NEVER

- **Never edit `vendor`.** It must stay byte-identical to `upstream/main`.
  Fast-forward only. If it won't fast-forward, something was committed by
  mistake — fix that first.
- **Never hand-edit `layout`.** It is regenerated and disposable.
- **Never `git reset --hard layout` onto `main`** once `main` carries real work —
  that discards your overlay history. Rebase instead.
- **Never move a file by hand.** Motion is the manifest's job, so that it stays
  reproducible and reversible.

### What actually conflicts

Only one thing: **you and upstream editing the same upstream-owned file.**
Everything else — upstream adding files, deleting files, moving files, renaming
whole trees — is absorbed by the manifest. That is the point of the design.

So: keep your work in paths upstream has never created, and the conflict surface
stays near zero.

## 3. Updating from upstream

### Four branches, and which one you commit to

| Branch | Holds | You commit here when |
|---|---|---|
| `vendor` | pristine `upstream/main` | never |
| `base` | `vendor` + toolchain and identity | you change `tools/`, the manifest, `.claude/`, `AGENTS.md`, `README.md` |
| `layout` | `apply.py(base)` | never — generated |
| `main` | `layout` + your work | you change anything else |

**Toolchain edits go on `base`, work goes on `main`.** They are separated
because each rebases against a different thing. Put a tool commit on `main` and
it will replay onto a `layout` that already contains its own final state, and
conflict with itself.

### The cycle

```bash
git fetch upstream
git checkout vendor && git merge --ff-only upstream/main

git checkout base && git rebase vendor          # toolchain forward, history intact
python tools/reforge/drift.py                   # must be clean — fix the manifest here

git checkout -B layout base
python tools/reforge/apply.py
python tools/reforge/verify.py
python tools/reforge/tiercheck.py --core Core
git commit -am "reforge layout @ $(git rev-parse --short vendor)"

git checkout main && git rebase --onto layout <previous-layout-sha> main
python tools/reforge/compare.py
```

Record the previous `layout` sha before regenerating — it is the rebase anchor.

**`drift.py` failing is the normal signal that upstream changed something.** It
reports four things, and each means something different:

| Signal | Meaning | Action |
|---|---|---|
| `UNCOVERED` | upstream added a path no rule covers | add a rule — prefer a glob |
| `STALE rules` | a rule matches nothing — upstream moved or deleted its target | retarget or remove it |
| `STALE ids` | a `legacy_id` points at a path that no longer exists | same |
| `AMBIGUOUS` | two entries claim one path at equal specificity | add an `[[override]]` or narrow a glob |

A **stale rule plus an uncovered path in the same run is a rename.** That pairing
is how upstream's periodic mass-restructures become readable instead of fatal.
Retarget the rule's `match` at the new path and the file lands where it always
did.

Prefer globs over per-file entries: a glob keeps covering new upstream files in
that subtree, where per-file entries turn every upstream addition into drift.

### What a conflict actually looks like

Only one thing conflicts: **you and upstream editing the same upstream-owned
file.** It surfaces during the final `git rebase`, as an ordinary 3-way merge:

```
UU Core/Agent.py
<<<<<<< HEAD
# upstream's change
=======
# your change
>>>>>>> (your overlay commit)
```

Resolve it like any merge, `git add`, `git rebase --continue`. Everything else —
upstream adding files, deleting them, moving them, renaming whole trees — is
absorbed by the manifest and never reaches the rebase.

## 4. Sending changes upstream

```bash
python tools/reforge/backport.py layout..main
python tools/reforge/backport.py --path Core/Agent.py HEAD   # map one path back
```

Path mapping inverts the manifest exactly. Content mapping is deliberately
narrow: the forward codemod renames the unique token `Py4GWCoreLib` to `Core`,
but `Core` is an ordinary English word, so only import statements and quoted path
strings are rewritten back. Files with no upstream counterpart are reported as
layout-only rather than guessed at.

Semantic fixes are worth upstreaming individually — they stand on their own merit
and, once merged, survive upstream's next restructure. Local-only edits do not.


### Getting it into the fork

`backport.py` gives you upstream-shaped content. Landing it publicly:

```bash
python tools/reforge/backport.py layout..main     # what is portable, what is not
cd ../Py4GW_Reforged
git checkout -b fix/<thing> main
# apply the mapped content, commit, push, PR to upstream
```

This repo has an `origin` (`markpnoi26/mypy4gw_main`, private backup) and a
`fork` remote — but PRs to upstream go through `../Py4GW_Reforged` only. Before
any push here, confirm no credential-shaped file is tracked — a pre-push hook
(`.git/hooks/pre-push`) enforces it: `git ls-files` must match none of
`accounts.json`, `Py4GW.ini`, `Settings/` outside `Defaults/`, or `json/`.

Send semantic fixes individually. They stand on their own merit, and once merged
they survive upstream's next restructure — a local-only edit does not.

## 5. Gates

| Tool | Checks | Fails on |
|---|---|---|
| `drift.py` | manifest covers the tree; no stale or ambiguous entries | uncovered paths |
| `verify.py` | core imports no higher tier; no stale module names; idempotent; `legacy_id` coverage | any |
| `tiercheck.py` | AST tier enforcement + eager import closure of the facade | any violation |

`tiercheck.py` currently **fails by design** — the facade eagerly pulls 17
`HeroAI` modules, and `Core/py4gwcorelib_src/AutoInventoryHandler.py` still
reaches into `dev/reference`. Those are known and tracked in
`docs/tier_map_and_separation_plan.md`. Do not silence them; fix or waive with a
reason in `tier_map.toml`.

## 6. Navigation

`.claude/LINE_INDEX.md` is an auto-generated symbol index of every `.py` in the
tree (`L<line> <signature>`, grouped by file), regenerated by git hooks on
commit, checkout, merge and rebase. **Grep it first** — one search against one
file beats ripgrep across ~1,700 files.

```bash
python tools/generate_line_index.py     # regenerate by hand
```

## 7. Code rules

The rules that make code wrong even when it runs — persistence, native binding
names, import constraints, toolchain, formatting — are in
`.claude/context/hard-rules.md`. Naming and comment style is in
`.claude/context/code-style.md`. Both apply to everything written here.

Upstream's `AGENTS.md` is preserved at `docs/reference/upstream-AGENTS.md`. Its
code rules still hold; its layout claims describe upstream's tree, not this one.

## 8. Known state — read before assuming something is broken

**Gates that fail by design.** `tiercheck.py` exits non-zero: the `Core` facade
eagerly imports 244 modules including 17 from `HeroAI`, and
`Core/py4gwcorelib_src/AutoInventoryHandler.py` reaches into `dev/reference`.
`verify.py` reports the same 6 tier violations. These are measured, tracked in
`docs/tier_map_and_separation_plan.md`, and **not** to be silenced.

**Never run in the game client.** Nothing in this tree has been loaded by
`Py4GW.dll`. 2,098 file moves and ~1,020 codemod rewrites are statically checked
only. Treat runtime behaviour as unverified. `MyPy4GW` is the ready-made harness:
repoint its `vendor/py4gw` here and run its `setup.sh`.

**Four widgets are tabled** in `dev/tabled/` — `MerchantRules`, `MultiBoxing`,
`PartyQuestLog`, `TitleHelper`. They import `Sources` eagerly and cannot load.
Un-tabling is a one-line `dest` change once rewritten against `Core`.

**Credentials live here and must never be committed.** `accounts.json`,
`Py4GW.ini`, `Settings/<account>/`, `json/<account>/` are real account data,
gitignored. Verify with `git check-ignore` before any `git add -A`.

**Tooling runs from `.venv/Scripts/python.exe`** (3.13.0, 32-bit, gitignored).
It holds the pinned formatters (`tools/reforge/requirements.lock`) plus pytest.
The `apply.py` format stage and `forwardport.py` both use it; recreate with
`python -m venv .venv` from any 32-bit 3.13 and `pip install -r tools/reforge/requirements.lock`.

**The generated tree is formatted.** `apply.py` ends with isort+black driven by
the root `pyproject.toml` (ours — upstream's is in `docs/reference/`). Because
`layout` is regenerated, this costs zero overlay commits. Import order is
semantic in a few files (`Core/__init__.py`, `Core/GlobalCache/`,
`HeroAI/follow/`, `Py4GW_widget_manager.py`) — they are in isort's
`extend_skip_glob` and must stay there.

**Importing fork/upstream-shaped work:** `tools/reforge/forwardport.py
upstream/main..BRANCH [--filter GLOB]` — backport's inverse: manifest path map,
forward codemod, same formatters. Unmapped paths are reported, never guessed.

## 9. Gotchas that have already bitten

Each of these cost real time; none is obvious from the code.

- **The codemod rewrote its own toolchain.** `Py4GWCoreLib -> Core` over
  `**/*.py` turned `backport.py`'s prose into "renames the unique token `Core` to
  `Core`". `tools/` is excluded from codemods for this reason, in both `apply.py`
  and `verify.py`.
- **`.claude/`, `AGENTS.md` and `CLAUDE.md` are gitignored** — the first two by
  the global ignore, the latter two also by upstream's `.gitignore`. They need
  explicit negations or the project identity silently fails to track. A commit
  can look successful while dropping them.
- **`Settings/` and `Widgets/Data/` are native-read paths.** The persistence jail
  roots at `<projects_path>/settings` and `<projects_path>/json`; template seeding
  reads `settings/Defaults/*.cfg`. Both are pinned `keep`. Moving them breaks
  seeding silently.
- **A tool commit on `main` conflicts with itself** on the next sync. That is why
  `base` exists. See §3.
- **`drift.py` against a transformed tree** reported 2,113 false uncovered paths
  before it learned to refuse. It only means anything on `base`/`vendor`.
- **`apply.py` prunes empty directories.** Empty account dirs under `json/`
  disappear during a transform. Harmless — the native side recreates them — but
  do not read it as data loss.
- **`git check-ignore` exits 0 when *any* pattern matches, including a negation.**
  Read the printed rule, not the exit code.
