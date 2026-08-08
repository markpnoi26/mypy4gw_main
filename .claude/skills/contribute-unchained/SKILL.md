---
name: contribute-unchained
description: Branching, committing and filing PRs to the Unchained fork line (Py4GW-Unchained and Py4GW-unchained-cpp) — the active contribution target under RS-008. Load before any branch, commit or PR work in either sibling repo.
---

# Contributing to the Unchained line

The Reforged line takes nothing (RS-008). All outbound work goes here.

## Repos and remotes

| Repo | `origin` (push) | `upstream` (PR target) | Commit style |
|---|---|---|---|
| `../Py4GW-Unchained` | markpnoi26/Py4GW-Unchained | **Wick-Divinus**/Py4GW-Unchained | strict conventional: `fix(scope):`, `feat(scope):` |
| `../Py4GW-unchained-cpp` | markpnoi26/Py4GW-unchained-cpp | **sloppynacho**/Py4GW-unchained-cpp | loose prose — match the log |

Each repo has a local `CLAUDE.md` with its tree conventions. Read it when a
session starts there.

## Flow

```sh
cd ../Py4GW-Unchained          # or ../Py4GW-unchained-cpp
git fetch upstream
git log --oneline HEAD..upstream/main    # what moved since the fork was level
git checkout -b fix/<thing> upstream/main
# work, commit
git push -u origin fix/<thing>
gh pr create --repo Wick-Divinus/Py4GW-Unchained --base main \
  --title "..." --body-file <path>
```

Branch off `upstream/main`, not local `main` — a stale base is the #1 cause of
inflated PR diffs (below). Use `--body-file`, never `--body`: long bodies
overflow the command line. `gh` lives at `C:\Program Files\GitHub CLI\gh.exe`
if it is not on PATH. Run git through Bash, not PowerShell — PS 5.1 breaks on
git in a pipeline and has no `&&`.

## Never commit

- The local Claude layer: `CLAUDE.md`, `AGENTS.md`, `.claude/` additions.
  Both repos exclude them (their `.gitignore` / `.git/info/exclude`); the
  canonical copies live in `../claude-context/`.
- The Python repo's local runtime churn: `Py4GW.ini`, `Py4GW_Launcher.ini`,
  `Py4GW_injection_log.txt` — its README documents
  `git update-index --skip-worktree` for exactly these. Check `git status`
  before staging; they reappear constantly.

## Verification is on you

Neither repo has CI. For the Python side:

```sh
python -c "import compileall,sys; sys.exit(0 if compileall.compile_dir('<dir>', quiet=1) else 1)"
```

For the cpp side, build before filing (plain CMake, 32-bit — see that repo's
CLAUDE.md). Known-volatile area there: shared-memory / collision publishing —
upstream's history has six consecutive reverts in it.

## When a PR shows far more files than the branch changed

GitHub diffs against the base branch **as recorded when the PR was opened**,
not as it is now. If the base moved, upstream's work gets attributed to your
branch. Diagnose before touching anything:

```sh
gh pr view <N> --repo <upstream-owner>/<repo> --json baseRefOid,headRefOid
git diff --name-only <baseRefOid>...<branch> | wc -l   # reproduces GitHub's count
git diff --name-only upstream/main...<branch> | wc -l  # the honest count
```

If the second count is right, the branch is fine — reload the PR page, or
toggle the base branch away and back in the PR's Edit menu. **Never rebase or
force-push to fix this** — the content was never wrong. (Seen for real on the
old fork's PR #4: 195 shown vs 128 real.)

## Sequencing large changes

Every PR up to the last is a **provable runtime no-op** — new files nothing
imports, extractions that preserve behaviour, registries that exclude the new
thing by default. The single behavioural PR lands last, small enough to read
line by line, with rollback as a one-line flip.

## Boundary

Carrying code between this tree and the Unchained line is never a copy — that
is the `port-concept` skill's job. This skill is only the git/PR mechanics.
