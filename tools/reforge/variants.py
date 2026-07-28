"""Preserves both sides of a rebase conflict instead of forcing a choice.

A conflict here means we and upstream edited the same file. Resolving it by
picking a side silently discards working behaviour — theirs or ours. So on
conflict this keeps upstream's whole file under dev/variants/ and reports which
*functions* actually differ, so the decision can be made per function, later,
with the evidence still on disk.

Nothing is auto-merged. The conflict still has to be resolved by hand; this only
makes sure neither version is lost while you do it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VARIANTS = REPO / "dev" / "variants"


def git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=check)
    return out.stdout


def conflicted_paths() -> list[str]:
    return [line for line in git("diff", "--name-only", "--diff-filter=U").splitlines() if line.strip()]


def stage(path: str, number: int) -> str | None:
    """1 = common ancestor, 2 = ours (the branch being rebased onto), 3 = theirs."""
    try:
        return git("show", ":%d:%s" % (number, path))
    except subprocess.CalledProcessError:
        return None


def top_level_defs(source: str) -> dict[str, str]:
    """name -> source text, for functions and classes at any nesting depth."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    lines = source.splitlines(True)
    found: dict[str, str] = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                start = child.lineno - 1
                end = getattr(child, "end_lineno", child.lineno)
                found[name] = "".join(lines[start:end])
                if isinstance(child, ast.ClassDef):
                    walk(child, name + ".")

    walk(tree)
    return found


def differing_defs(ours: str, theirs: str) -> tuple[list[str], list[str], list[str]]:
    a, b = top_level_defs(ours), top_level_defs(theirs)
    changed = sorted(n for n in a.keys() & b.keys() if a[n] != b[n])
    only_ours = sorted(a.keys() - b.keys())
    only_theirs = sorted(b.keys() - a.keys())
    return changed, only_ours, only_theirs


def preserve(path: str) -> dict | None:
    ours, theirs = stage(path, 2), stage(path, 3)
    if ours is None or theirs is None:
        return None

    target = VARIANTS / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(theirs, encoding="utf-8")

    changed, only_ours, only_theirs = ([], [], [])
    if path.endswith(".py"):
        changed, only_ours, only_theirs = differing_defs(ours, theirs)

    return {
        "path": path,
        "variant": target.relative_to(REPO).as_posix(),
        "changed": changed,
        "only_ours": only_ours,
        "only_theirs": only_theirs,
    }


def describe(records: list[dict]) -> str:
    lines = []
    for rec in records:
        lines.append("  %s" % rec["path"])
        lines.append("      upstream's version kept at %s" % rec["variant"])
        if rec["changed"]:
            lines.append("      both changed: %s" % ", ".join(rec["changed"]))
        if rec["only_theirs"]:
            lines.append("      only upstream has: %s" % ", ".join(rec["only_theirs"]))
        if rec["only_ours"]:
            lines.append("      only we have: %s" % ", ".join(rec["only_ours"]))
    return "\n".join(lines)


def main() -> int:
    paths = conflicted_paths()
    if not paths:
        print("no conflicted files — nothing to preserve")
        return 0
    records = [rec for rec in (preserve(p) for p in paths) if rec]
    print("preserved %d upstream variant(s) under dev/variants/\n" % len(records))
    print(describe(records))
    print("\nResolve the conflict as usual. Neither version is lost — upstream's")
    print("full file is on disk, and the functions listed above are the only ones")
    print("that actually differ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
