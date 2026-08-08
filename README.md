# mypy4gw

**A generated, tier-enforced reorganization of [Py4GW_Reforged](https://github.com/apoguita/Py4GW_Reforged).**

This is not a fork you merge into. Upstream is *vendored* and never edited. The
tree you see is *generated* from upstream by a deterministic transform, and your
own work sits on top as an overlay.

That distinction is the whole project. Everything below follows from it.

---

## The four branches

| Branch | What it is | Rule |
|---|---|---|
| `vendor` | byte-identical mirror of `upstream/main` | **fast-forward only. Never edit.** |
| `base` | `vendor` + the toolchain, manifest, and this project's identity | **yours. Commit the *machine* here.** |
| `layout` | `apply.py(base)` — the reorganized, formatted tree | **generated. Disposable. Never hand-edit.** |
| `main` | `layout` + your work | **yours. Commit the *code* here.** |

Commit hooks enforce that split. `CHEATSHEET.md` is the one-page version.

Because `layout` is regenerated rather than merged, upstream restructuring — and
they restructure in 300-file sweeps — costs you a manifest edit instead of a
merge conflict.

## Why it exists

Upstream is a capable system with poor hygiene: 46% of the "core library" is not
core, the package facade eagerly imports 241 modules including the combat AI, and
the widget tree gets re-nested periodically. Those are structural problems you
cannot fix by editing files, because your edits are what get destroyed.

So instead: leave upstream alone, and express the reorganization as **data** —
one manifest that maps every upstream path to where it belongs here. The manifest
is the product. The tree is a build artifact.

## The tiers

Every file has a tier, and a file may only import at or below its own. This is
enforced by `tools/reforge/tiercheck.py`, which exits non-zero on violation.

| Tier | What | Where |
|---|---|---|
| 0 | native surface — the injected DLL's bindings | `stubs/`, `offsets/`, `Py4GW.dll` |
| 0.5 | vendored memory access, pinned to game layout | `Core/native_src/`, `Scanner.py`, `Context.py` |
| 1 | domain source-of-truth wrappers | `Core/` — Agent, Player, Map, Item, … |
| 2 | support infrastructure | `Core/py4gwcorelib_src/`, `GlobalCache/`, `routines_src/` |
| 3 | combat & automation | `HeroAI/`, `Core/Builds/`, `Core/botting_src/`, … |
| 4 | leaf consumers | `Widgets/`, `Scripts/`, `dev/` |

`Py4GW.dll` is built by a separate C++ project and is **consumed, not built**
here. Tier 0 is a binary you vendor. `stubs/` is your contract with it.

## Layout

```
Core/        the library (upstream Py4GWCoreLib, renamed by codemod)
HeroAI/      combat AI — tier 3, despite what upstream's layout implies
Widgets/     shipped in-game UI, 12 flat categories
Scripts/     packs — community-bots, devtools, examples, marks-corner, …
dev/         not shipped: reference/, legacy/, tabled/, tests/, harness/
Runtime/     settings, db seeds, account data
tools/       the transform itself — tracked, because it IS the project
docs/        architecture notes; docs/reference/ holds upstream's originals
```

## Getting started

```bash
git clone <this> && cd mypy4gw
python tools/reforge/drift.py       # manifest covers every upstream path?
python tools/reforge/verify.py      # postconditions on the tree
python tools/reforge/tiercheck.py --core Core
```

Read next: **`CHEATSHEET.md`** first if you just want to not break anything —
it is one page and covers the five real mistakes. Then **`AGENTS.md`** (what you may change and what will conflict),
then `.claude/context/hard-rules.md` (rules that make code wrong even when it
runs), then `rules/TIER_MAP.md` (the measured analysis this is all built on).

## Relationship to upstream

The Reforged line is a supplier, nothing more (RS-008).

```
apoguita/Py4GW_Reforged  →  mypy4gw_main         Py4GW_Reforged (sibling fork)
(upstream, supplier)        (freeform, here)     (archive of unported work)
```

You work here without collaboration overhead and take upstream's work through
`vendor`; `origin` is a private backup, guarded by a pre-push credential check.

- **Down** — `git fetch upstream && sync`. Upstream's work arrives through
  `vendor`, is re-transformed, and your overlay rebases on top.
- **In** — `tools/reforge/forwardport.py` pulls not-yet-ported work from the
  sibling fork's branches into this layout.
- **Up** — nothing. No PRs go to the Reforged line (RS-008, decided
  2026-08-06). Contributions go to the separate `Py4GW-Unchained` fork line as
  re-implemented concepts — mapped in `docs/related-repos.md`. `backport.py`
  is kept dormant as the manifest inverter.

---

Upstream's own `README.md` and `AGENTS.md` are preserved verbatim under
`docs/reference/`.
