#!/bin/bash
# One command to commit from main. Files the hook would reject are committed on
# base instead, via a throwaway worktree, so main is never checked out.
set -euo pipefail

msg="${1:-}"
if [ -z "$msg" ]; then
    echo "usage: git save \"message\"" >&2
    exit 1
fi

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" != "main" ]; then
    echo "git save runs on main; you are on '$branch'." >&2
    exit 1
fi

if ! git rev-parse --verify --quiet base >/dev/null; then
    echo "no 'base' branch in this repo." >&2
    exit 1
fi

# Must stay in step with the pre-commit hook's own list.
TOOLCHAIN='^(qa|tools|\.claude|\.vscode)/|^(pyproject\.toml|AGENTS\.md|README\.md|CLAUDE\.md|CHEATSHEET\.md|\.gitignore)$'

git add -u

staged=()
while IFS= read -r -d '' f; do staged+=("$f"); done < <(git diff --cached --name-only -z)

if [ ${#staged[@]} -eq 0 ]; then
    echo "nothing to commit."
    exit 0
fi

base_files=()
main_files=()
for f in "${staged[@]}"; do
    if printf '%s' "$f" | grep -qE "$TOOLCHAIN"; then
        base_files+=("$f")
    else
        main_files+=("$f")
    fi
done

if [ ${#main_files[@]} -gt 0 ]; then
    if [ ${#base_files[@]} -gt 0 ]; then
        git restore --staged -- "${base_files[@]}"
    fi
    git commit -q -m "$msg"
    echo "main  <- ${#main_files[@]} file(s)  $(git rev-parse --short HEAD)"
fi

if [ ${#base_files[@]} -gt 0 ]; then
    WT="$(mktemp -d)/base-wt"
    trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
    git worktree add --quiet "$WT" base
    add_files=()
    for f in "${base_files[@]}"; do
        if [ -e "$f" ]; then
            mkdir -p "$WT/$(dirname "$f")"
            cp "$f" "$WT/$f"
            add_files+=("$f")
        else
            git -C "$WT" rm -q --ignore-unmatch -- "$f"
        fi
    done
    if [ ${#add_files[@]} -gt 0 ]; then
        git -C "$WT" add -- "${add_files[@]}"
    fi
    if git -C "$WT" diff --cached --quiet; then
        echo "base  <- already up to date"
    else
        git -C "$WT" commit -q -m "$msg"
        echo "base  <- ${#base_files[@]} file(s)  $(git -C "$WT" rev-parse --short HEAD)"
    fi
    git worktree remove --force "$WT"
    trap - EXIT
    if [ ${#main_files[@]} -eq 0 ]; then
        git restore --staged -- "${base_files[@]}"
    fi
    printf '%s\n' "${base_files[@]}" | sed 's/^/  still dirty on main until the next sync: /'
fi
