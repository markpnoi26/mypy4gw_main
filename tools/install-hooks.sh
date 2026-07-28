#!/bin/sh
# Git hooks are not part of the repo, so a fresh clone has no guards. Run this once.
ROOT="$(git rev-parse --show-toplevel)"
for hook in pre-commit pre-push post-checkout post-merge post-rewrite; do
    cp "$ROOT/tools/githooks/$hook" "$ROOT/.git/hooks/$hook" || exit 1
    chmod +x "$ROOT/.git/hooks/$hook"
    echo "  installed $hook"
done
git config --local pull.ff only
echo "  set pull.ff=only (an accidental 'git pull' on main now fails instead of merging)"
