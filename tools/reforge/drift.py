"""Reports tracked files that no layout rule covers, plus ambiguous matches."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import manifest as manifest_mod

REPO = Path(__file__).resolve().parents[2]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def is_transformed() -> bool:
    """Ask git, not the filesystem: leftover __pycache__ makes Core/ exist as a
    directory long after a checkout moved away from the transformed tree."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "Core/__init__.py"],
        capture_output=True,
        text=True,
    )
    return bool(out.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    mf = manifest_mod.load()

    if is_transformed():
        print(
            "this tree is already transformed - drift only means anything against the\n"
            "pristine tree. Run it on %r." % mf.meta.get("base", "vendor"),
            file=sys.stderr,
        )
        return 2

    files = tracked_files()

    uncovered: list[str] = []
    ambiguous: list[str] = []
    by_tier: dict[str, int] = defaultdict(int)
    moves = keeps = drops = 0

    matched_entries: set[int] = set()

    for src in files:
        try:
            res = mf.resolve(src)
        except manifest_mod.Ambiguous as exc:
            ambiguous.append(str(exc))
            continue
        if res is None:
            uncovered.append(src)
            continue
        matched_entries.add(id(res.entry))
        by_tier[res.entry.tier or "(untiered)"] += 1
        if res.is_drop:
            drops += 1
        elif res.is_keep or not res.moves:
            keeps += 1
        else:
            moves += 1

    print("tracked files    %d" % len(files))
    print("  move           %d" % moves)
    print("  keep           %d" % keeps)
    print("  drop           %d" % drops)
    stale = [e for e in mf.entries if id(e) not in matched_entries]
    stale_ids = [old for old in mf.legacy_ids if not any(f.endswith(old) for f in files)]

    print("  UNCOVERED      %d" % len(uncovered))
    print("  AMBIGUOUS      %d" % len(ambiguous))
    print("  STALE rules    %d" % len(stale))
    print("  STALE ids      %d" % len(stale_ids))
    print("\nby tier:")
    for tier, count in sorted(by_tier.items(), key=lambda kv: -kv[1]):
        print("  %-12s %d" % (tier, count))

    if stale:
        print("\nSTALE RULES — match nothing. Upstream moved or deleted the target:")
        for entry in stale:
            print("  %s%s" % ("[override] " if entry.is_override else "", entry.match))

    if stale_ids:
        print("\nSTALE legacy_ids — the old widget path no longer exists upstream:")
        for old in stale_ids:
            print("  %s" % old)

    if ambiguous:
        print("\nAMBIGUOUS (fix by adding an override or narrowing a glob):")
        for line in ambiguous[:40]:
            print("  %s" % line)

    if uncovered and not args.quiet:
        print("\nUNCOVERED — group these into rules:")
        folders: dict[str, list[str]] = defaultdict(list)
        for src in uncovered:
            head = src.rsplit("/", 1)[0] if "/" in src else "(root)"
            folders[head].append(src)
        for folder in sorted(folders, key=lambda f: -len(folders[f])):
            names = folders[folder]
            print("  %-56s %d" % (folder, len(names)))
            if len(names) <= 4:
                for n in names:
                    print("       %s" % n.rsplit("/", 1)[-1])

    strict = bool(mf.meta.get("strict", False))
    if ambiguous:
        return 1
    if uncovered and strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
