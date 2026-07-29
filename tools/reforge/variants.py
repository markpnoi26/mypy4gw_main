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

import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VARIANTS = REPO / "dev" / "variants"


def git(*args: str, check: bool = True) -> str:
    # Explicit utf-8: bare text=True decodes with the Windows ANSI codepage, and
    # a smart quote in a source file then kills subprocess's reader thread. That
    # surfaces as stdout=None with returncode 0, so check= never fires and the
    # caller silently reads nothing. 76 .py files in this tree trigger it.
    out = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )
    return out.stdout


def conflicted_paths() -> list[str]:
    return [line for line in git("diff", "--name-only", "--diff-filter=U").splitlines() if line.strip()]


def stage(path: str, number: int) -> str | None:
    """1 = merge base, 2 = HEAD, 3 = the commit being applied.

    Read those two literally, because rebase inverts the usual reading of
    "ours"/"theirs". sync.py runs `rebase --onto layout old_layout staging`, so
    HEAD *is* layout: stage 2 is upstream's regenerated file and stage 3 is our
    own replayed commit. Returns None when the conflict has no such stage, which
    is normal for add/delete pairs.
    """
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


def differing_defs(ours: str, upstream: str) -> tuple[list[str], list[str], list[str]]:
    a, b = top_level_defs(ours), top_level_defs(upstream)
    changed = sorted(n for n in a.keys() & b.keys() if a[n] != b[n])
    only_ours = sorted(a.keys() - b.keys())
    only_upstream = sorted(b.keys() - a.keys())
    return changed, only_ours, only_upstream


def preserve(path: str) -> dict | None:
    upstream, ours = stage(path, 2), stage(path, 3)
    if upstream is None and ours is None:
        return None

    variant = ""
    if upstream is not None:
        target = VARIANTS / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(upstream, encoding="utf-8")
        variant = target.relative_to(REPO).as_posix()

    changed, only_ours, only_upstream = ([], [], [])
    if path.endswith(".py") and upstream is not None and ours is not None:
        changed, only_ours, only_upstream = differing_defs(ours, upstream)

    if upstream is None:
        note = "upstream deleted this file; we still modify it"
    elif ours is None:
        note = "we deleted this file; upstream still modifies it"
    else:
        note = ""

    return {
        "path": path,
        "variant": variant,
        "changed": changed,
        "only_ours": only_ours,
        "only_upstream": only_upstream,
        "note": note,
    }


def describe(records: list[dict]) -> str:
    lines = []
    for rec in records:
        lines.append("  %s" % rec["path"])
        if rec["variant"]:
            lines.append("      upstream's version kept at %s" % rec["variant"])
        if rec["note"]:
            lines.append("      %s" % rec["note"])
        if rec["changed"]:
            lines.append("      both changed: %s" % ", ".join(rec["changed"]))
        if rec["only_upstream"]:
            lines.append("      only upstream has: %s" % ", ".join(rec["only_upstream"]))
        if rec["only_ours"]:
            lines.append("      only we have: %s" % ", ".join(rec["only_ours"]))
    return "\n".join(lines)


def main() -> int:
    paths = conflicted_paths()
    if not paths:
        print("no conflicted files — nothing to preserve")
        return 0
    records = [rec for rec in (preserve(p) for p in paths) if rec]
    kept = len([rec for rec in records if rec["variant"]])
    print("%d conflicted file(s); upstream's side kept for %d under dev/variants/\n" % (len(paths), kept))
    print(describe(records))
    print("\nResolve the conflict as usual. Neither version is lost — upstream's")
    print("full file is on disk, and the functions listed above are the only ones")
    print("that actually differ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
