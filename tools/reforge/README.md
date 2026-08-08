# reforge

Deterministic mapping between upstream's layout and this repo's.

## Branches

| Branch | Contents | Rule |
|---|---|---|
| `vendor` | pristine mirror of `upstream/main` | ff-only, never edited |
| `base` | `vendor` + toolchain and identity | tool/manifest commits go here |
| `layout` | `apply.py(base)` | generated, disposable |
| `main` | layout + your overlay | free reign |

## Tools

| Tool | Job |
|---|---|
| `drift.py` | tracked paths no rule covers. Upstream additions land here |
| `apply.py` | the forward transform: moves, drops, codemods |
| `verify.py` | postconditions on a transformed tree |
| `tiercheck.py` | AST tier enforcement + eager-closure measurement. Exits 1 on violation |
| `compare.py` | splits divergence into transform / overlay / upstream |
| `backport.py` | maps a change here back onto upstream's layout. Dormant (RS-008) |

## Sync

```bash
git fetch upstream
git checkout vendor && git merge --ff-only upstream/main
git checkout -B layout vendor
python tools/reforge/drift.py          # must be clean
python tools/reforge/apply.py
python tools/reforge/verify.py
python tools/reforge/tiercheck.py --core Core
git checkout main && git rebase layout
python tools/reforge/compare.py
```

## Backport

Dormant since 2026-08-06 — no PRs go to the Reforged line (RS-008). Kept as the
manifest inverter.

```bash
python tools/reforge/backport.py layout..main
python tools/reforge/backport.py --path Core/Agent.py HEAD
```

Path mapping inverts the manifest exactly. Content mapping is deliberately narrow:
the forward codemod renames the unique token `Py4GWCoreLib` to `Core`, but `Core`
is an ordinary word, so only import statements and quoted path strings are
rewritten back. Files with no upstream counterpart are reported as layout-only
rather than guessed at.
