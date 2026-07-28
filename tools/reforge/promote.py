"""Moves `main` onto the staged tree. The one step a machine does not decide.

sync.py stops at `staging` on purpose. This is the deliberate act of accepting
upstream's release, and it refuses unless the gates actually passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATUS = REPO / "STATUS.md"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=check)


def out(*args: str) -> str:
    return git(*args).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="promote even though STATUS.md records a failure")
    args = parser.parse_args()

    if git("rev-parse", "--verify", "staging", check=False).returncode != 0:
        sys.exit("no staging branch — run sync.py first")

    if out("status", "--porcelain"):
        sys.exit("working tree is dirty — commit or stash first")

    if STATUS.exists() and "STOPPED at" in STATUS.read_text(encoding="utf-8") and not args.force:
        sys.exit("STATUS.md records a failed sync. Fix it and re-run sync.py, or pass --force if you mean it.")

    previous = out("rev-parse", "--short", "main")
    git("checkout", "main")
    git("reset", "--hard", "staging")
    git("branch", "-D", "staging")

    print("main %s → %s" % (previous, out("rev-parse", "--short", "main")))
    print("\nmain was rebased, so the remote cannot fast-forward:")
    print("  git push --force-with-lease origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
