# CHEATSHEET — what not to screw up

## The whole repo in four lines

| | |
|---|---|
| `vendor` | The crate upstream ships you. You never open it. |
| `base` | Your machine tools — the manifest and scripts that reshape the crate's contents. |
| `layout` | What the machine spits out. Thrown away and rebuilt from scratch every sync. |
| `main` | That output, with your own work stacked on top. |

**You edit two of them: `base` and `main`.** The other two are not yours to touch.

## "Where do I commit?"

One question: **am I changing the stuff, or the machine?**

| Changing… | Commit on |
|---|---|
| code, scripts, widgets, builds, your docs | `main` |
| `tools/`, `layout.toml`, `pyproject.toml`, `AGENTS.md`, `README.md`, `.claude/` | `base` |

Guess wrong and the pre-commit hook stops you and names the right branch. You do
not need to memorise this table — you need to not fight the hook.

## The five ways to break it

Four are guarded. **One is not, and it's the only rule you have to actually hold
in your head.**

| # | Mistake | What happens | Guard |
|---|---|---|---|
| 1 | Commit on `layout` | Deleted at the next sync. Work silently gone. | blocked |
| 2 | Commit on `vendor` | Breaks fast-forward to upstream — the foundation everything rests on. | blocked |
| 3 | `git pull` on `main` | Merges a discarded build back in; resurrects ~785 files in upstream's old formatting. | fails loudly |
| 4 | Toolchain edits on `main` | Conflicts with itself at the next sync. | blocked |
| 5 | **Moving or renaming a file by hand** | **Undone at the next regeneration. The manifest owns file placement, not you.** | **none — remember it** |

For #5: to move a file, edit `tools/reforge/layout.toml` on `base` and regenerate.
That is the only way a move survives.

## Daily loop

```bash
git checkout main
# ...edit, run, break things...
git commit -am "what changed"
git push --force-with-lease origin main
```

`--force-with-lease` is **not** dangerous here and **is** required. `main` is
rebased onto a freshly generated `layout` every sync, so the remote can never
fast-forward. `git status` saying "diverged" after a sync is normal.

Never plain `--force`. The lease is what tells "I rebased" apart from "I'm about
to clobber something I haven't seen".

## When a hook blocks you

It printed the branch you actually want. Move the work there:

```bash
git stash
git checkout base     # or main, whichever it said
git stash pop
```

Escape hatch is `git commit --no-verify`. If you're reaching for it, you're
probably about to do mistake #1 or #4.

## Fresh clone

Hooks don't travel with a repo. First thing, every clone:

```bash
sh tools/install-hooks.sh
```

## Panic button

Nothing here is unrecoverable while the reflog exists.

```bash
git reflog                    # every commit you've had checked out, ~90 days
git reset --hard <sha>        # put a branch back where it was
```

`layout` never needs recovering — regenerate it. `vendor` never needs recovering —
re-fetch it. Only `base` and `main` hold anything irreplaceable, which is exactly
why those two are the ones to push.

---

Deeper detail lives in `AGENTS.md`: §3 for the sync cycle and conflict handling,
§9 for the traps that have already bitten.
