"""Decomposes this repo's divergence into the three diffs that mean different things."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

TRACKS = [
    ("transform", "vendor", "layout", "what the transform did - must be pure motion"),
    ("overlay", "layout", "main", "what YOU changed - your true divergence"),
]


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def have(ref: str) -> bool:
    return bool(git("rev-parse", "--verify", "--quiet", ref).strip())


def changed(base: str, head: str) -> list[tuple[str, str]]:
    raw = git("diff", "--name-status", "-M", "%s...%s" % (base, head))
    rows = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0][0], parts[-1]))
    return rows


def summarize(rows: list[tuple[str, str]]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for status, _ in rows:
        counts[status] += 1
    order = [("A", "added"), ("M", "modified"), ("D", "deleted"), ("R", "renamed")]
    bits = ["%d %s" % (counts[k], label) for k, label in order if counts.get(k)]
    return ", ".join(bits) or "no change"


def top_dirs(rows: list[tuple[str, str]], limit: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for _, path in rows:
        counts[path.rsplit("/", 1)[0] if "/" in path else "(root)"] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def upstream_delta() -> list[tuple[str, str]] | None:
    """What arrived from upstream since vendor last moved."""
    if not have("vendor@{1}"):
        return None
    return changed("vendor@{1}", "vendor")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", action="store_true", help="list every path, not just directories")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    missing = [ref for _, base, head, _ in TRACKS for ref in (base, head) if not have(ref)]
    if missing:
        print("missing refs: %s" % ", ".join(sorted(set(missing))), file=sys.stderr)
        print("run tools/reforge/apply.py to generate the layout branch first", file=sys.stderr)

    for name, base, head, meaning in TRACKS:
        print("\n=== %s  (%s...%s) ===" % (name.upper(), base, head))
        print(meaning)
        if not (have(base) and have(head)):
            print("  SKIPPED - branch missing")
            continue
        rows = changed(base, head)
        print("  %s" % summarize(rows))
        if args.files:
            for status, path in sorted(rows, key=lambda r: r[1]):
                print("    %s  %s" % (status, path))
        else:
            for directory, count in top_dirs(rows, args.limit):
                print("    %5d  %s" % (count, directory))

    print("\n=== UPSTREAM  (vendor@{1}...vendor) ===")
    print("what arrived from upstream at the last sync")
    delta = upstream_delta()
    if delta is None:
        print("  no prior vendor position recorded yet")
    else:
        print("  %s" % summarize(delta))
        for directory, count in top_dirs(delta, args.limit):
            print("    %5d  %s" % (count, directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
