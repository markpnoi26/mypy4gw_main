---
name: pr-workflow
description: Branching, committing and filing PRs in this fork — the origin/upstream split, branch_status scratchpads, files that must never be committed, and the PowerShell git trap. Load before creating a branch, committing, or opening a PR.
---

# PR and branch workflow

## Remotes — this is a fork

| Remote | URL | Role |
|---|---|---|
| `origin` | `git@github.com:markpnoi26/Py4GW_Reforged.git` (ssh) | the user's fork — normal push target |
| `upstream` | `https://github.com/apoguita/Py4GW_Reforged` | the shared project |

PRs land on **`origin` (markpnoi26)** unless the change is explicitly being
proposed upstream. `gh` needs the repo spelled out either way:

```sh
gh pr create --repo markpnoi26/Py4GW_Reforged --base main --head <BRANCH> \
  --title "..." --body-file <path>
```

Use `--body-file`, not `--body` — long bodies overflow the command line.
`gh` lives at `C:\Program Files\GitHub CLI\gh.exe` if it is not on PATH.

## Run git through Bash, not PowerShell

PowerShell 5.1 fails with *"Cannot run a document in the middle of a pipeline"*
when `git` appears in a pipeline, and has no `&&`. The Bash tool is Git Bash,
already rooted at the repo — use it for all git work.

## Branch naming

Existing branches are `SCREAMING_SNAKE_CASE` describing the change
(`SCRIPTS_FLAT_MIGRATION`, `BTBUILDMGR_AND_BT_INVENTORY_PRIMITIVES`,
`FIX_LAUNCHER_CODE`), with occasional `chore/kebab-case` for housekeeping.
Match the dominant style.

## Branch scratchpads — `.claude/branch_status/`

One file per branch, `<branch-name>.md`, git-ignored and private. Structure:

- **Scope** — one sentence, why the branch exists
- **Changes on this branch** — grouped core-lib-worthy vs local/bot-specific
- **PR split plan** — which subset ships upstream, from which base, suggested
  branch name
- **Followups** — anything to pick up later

Check for the current branch's file before planning work; update it as the
branch grows. On merge or abandon, either delete it or append a
`**Merged/Abandoned:**` footer. Index lives in
`.claude/branch_status/README.md`.

This is the mechanism for the user's preferred way of working: build on a wide
local branch, then split readable, sequenced PRs out of it.

## Never commit these

Local runtime/config churn, unless the task is specifically about them:

- `Py4GW.ini`
- `Py4GW_Launcher.ini`
- `Py4GW_injection_log.txt`

README documents `git update-index --skip-worktree` for exactly this. Check
`git status` before staging — these reappear constantly.

`.claude/` as a whole is ignored via `.git/info/exclude:10`, so skills, branch
notes and `LINE_INDEX.md` never reach the shared repo. `CLAUDE.md` is ignored
via `.gitignore:24`.

## The commit hooks

`pre-commit`, `post-checkout`, `post-merge` and `post-rewrite` all run
`.git/hooks/_run_line_index.sh`, regenerating `.claude/LINE_INDEX.md` with
`.venv/Scripts/python.exe .vscode/generate_line_index.py`. It is `|| true`, so
it never blocks a commit, and its output is git-ignored. Nothing to work around
— just know why that file changes on every branch operation.

## No CI

No `.github/workflows`, no pytest/tox config, no Makefile, empty
`requirements.txt`. **Nothing verifies a PR but you.** Verify with targeted
scripts before filing:

```sh
.venv/Scripts/python.exe -c "import compileall,sys; sys.exit(0 if compileall.compile_dir('<dir>', quiet=1) else 1)"
```

Formatting to preserve: Black `line-length = 120`,
`skip-string-normalization = true`, isort `force_single_line = true`.
`pyright` only if actually installed (`pyrightconfig.json` sets
`stubPath = ./stubs`).

## When a PR shows far more files than the branch changed

GitHub diffs a PR against the base branch **as it was when the PR's base ref was
last recorded**, not against wherever `main` is now. Fast-forward `main` after
opening the PR and the file count inflates by everything `main` gained — those
files look like your changes, including deletions you never made.

Seen for real on PR #4 (`HEROAI_MIGRATION`, 2026-07-27):

```
195   what the PR showed         base = b5d6cd37 (stale)
-70   what main gained since     b5d6cd37 -> 1221cfd1, from upstream
────
128   the branch's real contribution
```

`errors.txt`, `navmesh_debug.py`, `Core/Overlay.py` and all of
`Widgets/Config/**` sat in that 70-file gap — upstream's work, attributed to
the branch.

Diagnose by comparing GitHub's recorded base against reality:

```sh
gh pr view <N> --repo markpnoi26/Py4GW_Reforged --json baseRefOid,headRefOid
git rev-parse --short main origin/main upstream/main
git diff --name-only <baseRefOid>...<branch> | wc -l   # reproduces GitHub's count
git diff --name-only upstream/main...<branch> | wc -l  # the honest count
```

If the second count is right, the branch is fine — only the comparison is
stale. Fix in order of least effort: reload the PR page; toggle the base branch
away and back in the PR's Edit menu to force recomputation; confirm `main` is
actually pushed (`git push origin main`, a no-op if GitHub has it). **Never
rebase or force-push to fix this** — the branch content was never wrong.

A branch that merges upstream mid-flight is the setup for this. It is one more
reason to sequence PRs off a fresh base rather than growing one long-lived
branch, per the next section.

## Sequencing large changes

The pattern that worked for the HeroAI BT migration
(`docs/heroai_bt_pr_plan.md` is the worked example): every PR up to the last is
a **provable runtime no-op** — new files nothing imports, extractions that
preserve method resolution, registries that exclude the new thing by default.
The single behavioural PR lands last and small enough to read line by line,
with rollback as a one-line default flip.
