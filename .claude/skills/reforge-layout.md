---
name: reforge-layout
description: Regenerate the reorganized (Core / Widgets / Scripts) layout from upstream/main via the deterministic transform in `tools/reforge/`. Use when syncing upstream, adding a mapping rule, or resolving drift.
---

# Reforge layout

The reorg is a **pure transform**, not a merge. `vendor` mirrors
`upstream/main`; `base` adds the toolchain; `layout` is regenerated from
`base` on demand and never hand-edited; `main` is your overlay rebased on
top. The full four-branch cycle lives in **AGENTS.md §3** — this skill covers
the manifest.

## Invariants — violate these and the model collapses

1. **`vendor` never diverges from `upstream/main`.** Only `git merge
   --ff-only`. If it won't fast-forward, something was committed there by
   mistake — fix that first.
2. **`layout` is disposable.** Regenerated, never merged into anything, never
   hand-edited.
3. **Every source path matches exactly one rule.** Uncovered paths are drift,
   and drift fails the run rather than guessing.
4. **The transform does motion only** — moves plus import rewrites *derived
   from those moves*. Semantic changes live as overlay commits on `main`
   (RS-008: nothing goes upstream).
5. **Idempotent.** Running twice produces no diff.

## Regenerate

The sync cycle (fetch → vendor → base rebase → drift → apply → verify →
tiercheck → rebase main) is in AGENTS.md §3 — run it from there, with the
repo `.venv`.

`drift.py` failing is the normal signal that upstream added files. Add rules
to `tools/reforge/layout.toml`; don't pass `--allow-drift` except to inspect.

Run `clean_python_cache.bat` first. The transform moves tracked files, so
`__pycache__` dirs stay behind and keep their parent directories alive as
empty husks that `prune_empty_dirs` cannot remove.

**The transform only touches tracked files.** Anything untracked or excluded
keeps its old path and will not be discovered under the new layout — relocate
by hand after regenerating, or track it. This is deliberate: it is also what
stops the transform from relocating live user config.

## The manifest is the source of truth

`tools/reforge/layout.toml`. Rule order does not matter — specificity does.
Precedence is `(is_override, literal-prefix-length)`, highest wins: any
`[[override]]` beats any `[[rule]]`, and among peers the longest literal
prefix before the first wildcard wins. That is what lets
`Scripts/py4gw-community-bots/legacy`, `Scripts/py4gw-marks-corner/scripts`
and `Scripts/py4gw-marks-corner/lib` coexist as intended nesting. A genuine
tie between two entries with *different* destinations raises `Ambiguous` and
fails the run.

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

`{rel:PREFIX}` substitutes the source path with `PREFIX` stripped. `dest =
"keep"` means stays put; `dest = "drop"` means deleted by the transform.

## Adding a rule

Prefer a glob over per-file entries — a glob keeps covering new upstream
files in that subtree, where per-file entries turn every upstream addition
into drift. Reach for `[[override]]` only for genuine exceptions to a glob
you already have.

After editing the manifest, always re-run drift, apply and verify. `apply.py`
alone can produce a tree that imports nothing.

## What verify.py checks

- No `Core` file imports from `Widgets/**` or `Scripts/**` (tier-0 purity).
- Every intra-repo import resolves against the transformed tree.
- Every moved widget has a `legacy_ids` entry — widget id is the INI section
  key, so a move without an alias silently resets user config.
- Re-running `apply.py` yields an empty diff.

## Editing code that lives in the transformed tree

Don't, on `layout`. Either:

- **Layout-shaped change** — change `layout.toml`, regenerate.
- **Semantic change** — an overlay commit on `main`, replayed onto every
  future layout (conflict surface, tracked in `rules/DIVERGENCE.md`).

`apply.py --reverse <path>` maps a transformed path back to its upstream
path — the same inversion `backport.py` (dormant, RS-008) is built on. It
maps paths, not structure-dependent code.
