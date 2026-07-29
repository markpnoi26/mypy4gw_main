# How this repo works — the long version

`README.md` has the four-branch table. `CHEATSHEET.md` has the one-page "what
not to screw up". This is the version that explains *why*, with pictures.

## The whole thing in one sentence

**This repo is not a copy of upstream. It is a recipe for rebuilding upstream's
code in a different shape, plus your own changes re-applied on top, re-run from
scratch every time upstream ships.**

Every rule below follows from that. Most files here were not written, they were
generated.

## The four branches

```
   apoguita/Py4GW_Reforged        <- someone else's repo. Read-only. Never push here.
              |
              |  git fetch upstream
              v
      +---------------+
      |    vendor     |   The sealed box, exactly as delivered.
      +-------+-------+   NEVER EDIT. Fast-forward only.
              |
              |  apply.py  --reads-->  +------------+
              |                        |    base    |  The instruction sheet:
              v                        +------------+  tools/, layout.toml,
      +---------------+                                .claude/, CLAUDE.md,
      |    layout     |   Box unpacked and rearranged  README, CHEATSHEET
      +-------+-------+   per the instructions.
              |           DELETED + REBUILT every sync.
              |  + your commits replayed on top
              v
      +---------------+
      |     main      |   <- YOU LIVE HERE
      +---------------+   layout + your work
```

## ELI5

Upstream ships you **flat-pack furniture**.

- **`vendor`** is the box, sealed, exactly as it arrived. You never open it.
- **`base`** is *your own assembly instructions* — where each part goes, which
  parts to throw out, what to rename.
- **`layout`** is the assembled furniture. A machine builds it from the box,
  following your instructions. **You never sand or paint it directly** — at the
  next delivery the machine builds a fresh one and yours goes in the skip.
- **`main`** is that furniture *plus the shelf you bolted on yourself*.

The crucial bit: **your shelf is not stored as a finished object.** It is stored
as *"drill here, attach this"* — instructions (commits) re-applied to every
newly assembled unit.

That is why:

- Editing `layout` achieves nothing lasting. You painted a unit that is about to
  be thrown away.
- Hand-moving a file achieves nothing lasting. Placement is the instruction
  sheet's job, so the machine puts it back.
- `main` is **rebased, never merged**. Your instructions get re-run against the
  new unit; they do not get fused into it.

## The pancake — you are on both sides

```
   main     <-  YOUR changes, on top           replayed every sync,  CAN conflict
   ---------------------------------------
   layout   <-  upstream's content, reshaped
   ---------------------------------------
   base     <-  YOUR instructions, underneath  runs before you see it, NEVER conflicts
   ---------------------------------------
   vendor   <-  upstream, untouched
```

Upstream is the filling. You are both slices of bread. `base` reshapes their
code on the way in; `main` patches the result on the way out.

This is not a metaphor, it is the operational choice on every change: **express
it from below, or from above?** From below is free — you are not editing their
file, you are instructing where it goes, so there is nothing to collide with.
From above conflicts if they touch the same file. That is why the preference
order under "Where to put changes" starts with the manifest.

### The bottom slice: `tools/reforge/layout.toml`

The reshaping rules all live in one file on `base`:

| entry | job |
|---|---|
| `[[rule]]` | match upstream paths, send them somewhere (globs preferred) |
| `[[override]]` | one specific path, when a glob would be wrong or ambiguous |
| `[[legacy_id]]` | keep a widget's INI section key stable across a move |
| `[[codemod]]` | derived rewrites applied after the moves |
| `[[pin]]` | freeze one file at an older vendor sha when upstream breaks it |

`legacy_id` matters more than it looks: a widget's id **is** its INI section
key, so moving a widget without an alias silently resets user config.
`verify.py` checks for that.

The rest of `base` is the machine that runs the manifest (`tools/reforge/`) plus
this project's identity (`.claude/`, `CLAUDE.md`, `AGENTS.md`, `README.md`,
`CHEATSHEET.md`, `pyproject.toml`, `.gitignore`).

## What is actually yours

Your commits are yours, but they are **not** independent of upstream. The
overlay splits into two very different halves:

```bash
git diff --name-status layout..main    # everything that is yours
git rev-list --count layout..main      # how many commits you are carrying
```

Snapshot at vendor `55ec88a6` (2026-07-28) — recompute with the commands above,
these numbers move:

| | |
|---|---|
| commits in the overlay | 13 |
| files they touch | 139 |
| — **added** into namespaces upstream does not populate | 117 — can *never* conflict |
| — **modified** files upstream also owns | 21 — shared custody |
| — deleted | 1 |

`Core/BTBuilds/` — the whole BT port — sits *inside* an upstream directory and
is still completely conflict-free, because upstream has no such tree. **Adding
is free. Modifying is the only thing that costs.**

Of those 21 modified files, 2 are actually ours
(`.claude/skills/heroai-bt-engine.md`, one under `Scripts/py4gw-devtools/`),
leaving **19** — exactly the "conflict surface: 19" in `rules/DIVERGENCE.md`.
That ledger is appended once per sync and never rewritten, because the trend is
the point. If the surface climbs from 19 toward 60, the repo is drifting into a
fork that has to be maintained forever; push some of it upstream, or move it
into a namespace of our own, before it gets there.

## What each command does

### `git save "msg"` — park your work

```
   working tree (dirty)
        |
        +-- code files --------------> commit on  main
        |   Core/ HeroAI/ Widgets/
        |   Scripts/ rules/ docs/
        |
        +-- instruction files -------> commit on  base
            tools/ .claude/ *.md          (via a temp worktree, so main
            .gitignore .vscode/            stays checked out)
```

Instruction files must reach `apply.py`, and `apply.py` reads `base`. A
toolchain edit committed on `main` would be replayed onto a `layout` that
already contains it — a patch fighting itself. That is what the pre-commit hook
blocks, and what `git save` routes around.

See "The `git save` helper" below.

### `git pushmain` — back up to GitHub

```
   main --force-with-lease--> origin/main
```

The force is mandatory, not dangerous: `main` is rebased onto a fresh `layout`
every sync, so its history is rewritten by design and the remote can never
fast-forward. `--force-with-lease` means "I rebased". Plain `--force` means "I
do not care what is there" — never that one.

### `python tools/reforge/sync.py` — take upstream's new work

This command cannot hurt you. It never touches `main`.

```
  BEFORE                          AFTER sync.py
  ------                          -------------
  vendor  @ old upstream          vendor  @ NEW upstream    (fast-forwarded)
  layout  = apply(old)            layout  = apply(NEW)      (rebuilt from scratch)
  main    = layout + your work    main    = UNCHANGED  <-- still exactly where it was
                                  staging = NEW layout + your work replayed
                                            `- scratch branch. The trial run.
                                  STATUS.md - what happened, what broke
```

Gates run on the way: `drift` -> `apply` -> `verify` -> `tiercheck`.

### `cat STATUS.md` — read it before accepting anything

### `python tools/reforge/promote.py` — accept

```
   main --reset --hard--> staging      then staging is deleted
```

The only command that moves `main`. The deliberate act of saying yes.

### `git branch -D staging` — reject

The entire rollback. `main` never moved, so there is nothing to undo.

## Where to put changes

The dividing line is **not which directory** — it is **add versus modify**.

```
  ADDING a file, anywhere upstream does not already have one:
       Scripts/<pack>/   tools/   rules/   our docs/   Core/BTBuilds/
       ZERO CONFLICT, FOREVER.  Even inside Core/, because upstream has
       no such tree. This is 117 of our 139 overlay files.

  MODIFYING a file upstream owns:
       Core/Inventory.py   HeroAI/headless_tree.py   Widgets/Multibox/HeroAI.py
       Replayed every sync. Conflicts if upstream edits the same file.
       This is the entire risk surface, and it is only 19 files.
```

So the question when adding a feature is not "am I allowed to touch `Core/`" —
it is "can I do this by *adding* rather than *editing*?" A new BT build in
`Core/BTBuilds/` costs nothing forever. A three-line edit to `Core/Inventory.py`
joins the 19 and gets replayed for as long as the file exists.

When you must change upstream behaviour, prefer in this order:

1. **Change the instruction sheet** (`layout.toml` on `base`) — the machine does
   it forever, no conflict
2. **Wrap it** from a Tier 4 file you own
3. **Edit it and send it upstream as a PR** — then it stops being your patch
4. **Edit and carry the patch forever** — worst, but allowed

## The five ways to break it

| Do not | Why | Guarded |
|---|---|---|
| Commit on `layout` | Deleted at the next sync. Work silently gone. | blocked |
| Commit on `vendor` | Breaks the fast-forward everything rests on. | blocked |
| `git pull` on `main` | Merges a discarded build back in; resurrects ~785 files in upstream's old formatting. | fails loudly |
| Toolchain edits on `main` | The patch-fighting-itself problem above. | blocked |
| **Move or rename a file by hand** | The instruction sheet owns placement. Undone at the next rebuild. | **nothing catches this** |

The last one is the only rule you have to actually hold in your head. To move a
file, edit `tools/reforge/layout.toml` on `base` and regenerate.

## The daily loop

```bash
git save "added feature"
git pushmain

# when you want upstream's new work:
python tools/reforge/sync.py        # safe - main does not move
cat STATUS.md
python tools/reforge/promote.py     # yes
#   ...or...
git branch -D staging               # no
```

Nothing is obligated to flow upstream. A change that only makes sense in this
layout just stays here; `backport.py` exists for the rare thing you *choose* to
publish, and reports layout-only changes as such rather than guessing.

## The `git save` helper

`tools/git-save.sh`, wired up as a git alias:

```bash
git config alias.save '!bash "$(git rev-parse --show-toplevel)/tools/git-save.sh"'
```

It stages with `git add -u` — **tracked modifications only** — so it structurally
cannot sweep up a file git has never seen. For genuinely new files, `git add
<path>` by name first, then `git save`.

It never passes `--no-verify`, so the pre-commit credential check, the branch
check and the `LINE_INDEX.md` regeneration all still run.

Verified behaviour: mixed change sets split correctly (including filenames with
spaces); code-only creates no worktree; nothing-staged exits without an empty
commit; deletions work on both halves; a force-staged `accounts.json` is still
blocked by the hook.

**Maintenance:** the script carries its own copy of the toolchain path list. If
you add a path to the regex in `.git/hooks/pre-commit`, add it in both places, or
`git save` will route that file to `main` and the hook will block you again.

### The one wart

After `git save` sends a file to `base`, that file still shows as modified on
`main` — `main`'s HEAD carries the older blob until the next sync replays
everything onto a `layout` rebuilt from `base`. Expected. Do not commit it on
`main` to make the dirty marker go away; that creates the duplicate the hook was
trying to prevent.

If you do end up with the same change on both branches, it is survivable while
the two versions are byte-identical: `git rebase` detects the replay as empty and
drops it (`dropping <sha> -- patch contents already upstream`). It becomes a real
conflict the moment the two copies drift apart.
