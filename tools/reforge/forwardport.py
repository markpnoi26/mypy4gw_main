"""Maps upstream-shaped changes (e.g. from the fork) into this layout.

The inverse direction of backport.py: path via the manifest, content via the
forward codemods, then the pinned formatters — so a ported file is
byte-identical to what apply.py would have produced from the same input.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

import fmt
import manifest as manifest_mod

REPO = Path(__file__).resolve().parents[2]


def git_bytes(repo: str, *args: str) -> bytes:
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True)
    return out.stdout


def range_tip(rev_range: str) -> str:
    if ".." in rev_range:
        return rev_range.split("..")[-1] or "HEAD"
    return rev_range


def changed_files(source: str, rev_range: str) -> list[tuple[str, str]]:
    """[(status, upstream_path)] with renames expanded to delete + add."""
    raw = git_bytes(source, "diff", "--name-status", "-M", rev_range).decode("utf-8")
    changes: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R"):
            changes.append(("D", parts[1]))
            changes.append(("A", parts[2]))
        else:
            changes.append((status[0], parts[1]))
    return changes


def map_path(mf: manifest_mod.Manifest, src: str) -> str | None:
    res = mf.resolve(src)
    if res is None:
        return None
    if res.is_drop:
        return "drop"
    return src if res.is_keep else res.dest


def apply_codemods(mf: manifest_mod.Manifest, dest: str, data: bytes) -> bytes:
    if not dest.endswith(".py"):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for mod in mf.codemods:
        if mod.get("kind") != "module_rename":
            continue
        if any(dest.startswith(prefix) for prefix in mod.get("exclude", [])):
            continue
        text = re.sub(r"\b%s\b" % re.escape(mod["old"]), mod["new"], text)
    return text.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rev_range", help="range in the source repo, e.g. upstream/main..HEROAI_MIGRATION")
    parser.add_argument("--source", default=str(REPO.parent / "Py4GW_Reforged"))
    parser.add_argument("--filter", action="append", default=[], metavar="GLOB",
                        help="only port upstream paths matching (repeatable)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-format", action="store_true")
    args = parser.parse_args()

    mf = manifest_mod.load()
    tip = range_tip(args.rev_range)
    changes = changed_files(args.source, args.rev_range)
    if args.filter:
        changes = [
            (st, p) for st, p in changes
            if any(fnmatch.fnmatch(p, g) for g in args.filter)
        ]

    written: list[str] = []
    deleted: list[str] = []
    unmapped: list[str] = []
    dropped: list[str] = []

    for status, src in changes:
        dest = map_path(mf, src)
        if dest is None:
            unmapped.append(src)
            continue
        if dest == "drop":
            dropped.append(src)
            continue
        target = REPO / dest
        if status == "D":
            if target.exists() and not args.dry_run:
                target.unlink()
            deleted.append(dest)
            continue
        data = git_bytes(args.source, "show", "%s:%s" % (tip, src))
        data = apply_codemods(mf, dest, data)
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        written.append(dest)
        if src != dest:
            print("  %s -> %s" % (src, dest))
        else:
            print("  %s" % dest)

    py_written = [d for d in written if d.endswith(".py")]
    if py_written and not args.dry_run and not args.no_format:
        fmt.run_formatters(py_written)

    print("\nported %d, deleted %d, dropped-by-manifest %d, UNMAPPED %d"
          % (len(written), len(deleted), len(dropped), len(unmapped)))
    for src in unmapped:
        print("  UNMAPPED (place by hand): %s" % src)
    for src in dropped:
        print("  dropped: %s" % src)
    if args.dry_run:
        print("(dry run — nothing written)")
    return 1 if unmapped else 0


if __name__ == "__main__":
    sys.exit(main())
