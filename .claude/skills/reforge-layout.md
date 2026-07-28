---
name: reforge-layout
description: Regenerate the reorganized (core / Widgets / Scripts) layout from upstream/main by running the deterministic transform in `.claude/reforge/`. Use when syncing upstream, adding a mapping rule, resolving drift, or preparing a layout change to propose upstream.
---

# Reforge layout

The reorg is a **pure transform**, not a merge. `main` stays byte-identical to `upstream/main`; the layout branch is regenerated from it on demand and never hand-merged.

## Invariants — violate these and the model collapses

1. **`main` never diverges from `upstream/main`.** Only `git merge --ff-only`. If it won't fast-forward, something was committed to `main` by mistake — fix that first.
2. **The layout branch is disposable.** It is regenerated, never merged into anything. Never commit a hand edit to it.
3. **Every source path matches exactly one rule.** Uncovered paths are drift, and drift fails the run rather than guessing.
4. **The transform does motion only** — `git mv` plus import rewrites *derived from those moves*. Semantic changes go upstream as PRs.
5. **Idempotent.** Running twice produces no diff.

## Regenerate

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main
git checkout -B reforged-layout main
python .claude/reforge/drift.py        # must be clean before applying
python .claude/reforge/apply.py
python .claude/reforge/verify.py
git add -A && git commit -m "reforge layout @ $(git rev-parse --short main)"
```

`drift.py` failing is the normal signal that upstream added files. Add rules to `layout.toml`; don't pass `--allow-drift` except to inspect.

Run `clean_python_cache.bat` first. The transform moves tracked files, so `__pycache__` dirs stay behind and keep their parent directories alive as empty husks that `prune_empty_dirs` cannot remove.

**The transform only touches tracked files.** Anything in `.git/info/exclude` keeps its old path and will not be discovered under the new layout — currently `Core/debug_hatch.py`, `Widgets/System/Debug Terminal.py`, `Py4GW_Launcher_Lite.py`, `.claude/`. Relocate those by hand after regenerating, or track them. This is deliberate: it is also what stops the transform from relocating live user config under `Widgets/Config/`.

To undo a run: `git reset --hard main && git clean -fd`.

## The manifest is the source of truth

`.claude/reforge/layout.toml`. Rule order does not matter — specificity does. Precedence is `(is_override, literal-prefix-length)`, highest wins: any `[[override]]` beats any `[[rule]]`, and among peers the longest literal prefix before the first wildcard wins. That is what lets `Scripts/py4gw-community-bots/legacy`, `Scripts/py4gw-marks-corner/scripts` and `Scripts/py4gw-marks-corner/lib` coexist as intended nesting. A genuine tie between two entries with *different* destinations raises `Ambiguous` and fails the run.

```toml
[[rule]]
match = "Scripts/py4gw-community-bots/scripts"
dest  = "Scripts/py4gw-community-bots/scripts/{rel:Scripts/py4gw-community-bots/scripts}"
tier  = "script"

[[override]]
match = "Widgets/Panels/Messaging.py"
dest  = "Widgets/Panels/Messaging.py"
tier  = "widget"
```

`{rel:PREFIX}` substitutes the source path with `PREFIX` stripped. `dest = "keep"` means stays put; `dest = "drop"` means deleted by the transform.

## Adding a rule

Prefer a glob over per-file entries — a glob keeps covering new upstream files in that subtree, where per-file entries turn every upstream addition into drift. Reach for `[[override]]` only for genuine exceptions to a glob you already have.

After editing the manifest, always re-run all three tools. `apply.py` alone can produce a tree that imports nothing.

## What verify.py checks

- No `Core` file imports from `Widgets/**` or `Scripts/**` (tier-0 purity).
- Every intra-repo import resolves against the transformed tree.
- Every moved widget has a `legacy_ids` entry — widget id is the INI section key (`WidgetManager.py:896`, `:925`), so a move without an alias silently resets user config.
- Re-running `apply.py` yields an empty diff.

## Editing code that lives in the transformed tree

Don't, on the layout branch. Either:

- **Upstream-shaped fix** — make it on a branch off `main`, PR it to `upstream`, regenerate afterward.
- **Layout-shaped fix** — change `layout.toml`, regenerate.

`apply.py --reverse <path>` maps a transformed path back to its upstream path, for turning a layout-branch experiment into an upstream PR. It maps paths, not structure-dependent code.

## Proposing the layout upstream

The transform doubles as the proposal. Sequence that has a chance of landing:

1. Upstream the semantic fixes first, individually, as ordinary bug-fix PRs (dead `Widgets.Blessed` import in `INTERACT_src.py:156`; `enabled_widgets` frame-loop fix; extracting shared helpers out of `Widgets/Panels/Messaging.py`). These stand on their own merit and need no buy-in on the reorg.
2. Only then pitch the motion, with the transform as evidence it is mechanical and reversible.
3. If accepted, promote `.claude/reforge/` to a tracked `tools/reforge/` in the same PR so upstream can re-run and audit it.
