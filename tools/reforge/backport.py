"""Maps a change made in this repo's layout back onto upstream's layout, for PRing.

Path mapping is exact (it inverts the manifest). Content mapping is deliberately
narrow: the forward codemod renames the unique token ``Py4GWCoreLib`` to ``Core``,
which is safe, but ``Core`` is an ordinary English word so a blind reverse token
swap would corrupt prose and unrelated identifiers. Only import statements and
quoted path/module strings are rewritten back.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import manifest as manifest_mod

REPO = Path(__file__).resolve().parents[2]


def git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), out.stderr.strip()))
    return out.stdout


def vendor_files() -> list[str]:
    return [line for line in git("ls-tree", "-r", "--name-only", "vendor").splitlines() if line.strip()]


def reverse_map(mf: manifest_mod.Manifest) -> dict[str, str]:
    """transformed path -> upstream path, inverted from the forward resolution."""
    out: dict[str, str] = {}
    for src in vendor_files():
        try:
            res = mf.resolve(src)
        except manifest_mod.Ambiguous:
            continue
        if res is None or res.is_drop:
            continue
        out[src if res.is_keep else res.dest] = src
    return out


def reverse_content(text: str, old: str, new: str) -> str:
    """Undo a module_rename, scoped to imports and quoted paths."""
    text = re.sub(r"(?m)^(\s*(?:from|import)\s+)%s\b" % re.escape(new), r"\g<1>%s" % old, text)
    text = re.sub(r"(?m)^(\s*from\s+)%s(\.\S+\s+import\b)" % re.escape(new), r"\g<1>%s\g<2>" % old, text)
    text = re.sub(r"([\"'])%s/" % re.escape(new), r"\g<1>%s/" % old, text)
    text = re.sub(r"([\"'])%s\.([A-Za-z_])" % re.escape(new), r"\g<1>%s.\g<2>" % old, text)
    return text


def renames(mf: manifest_mod.Manifest) -> list[tuple[str, str]]:
    return [(mod["new"], mod["old"]) for mod in mf.codemods if mod.get("kind") == "module_rename"]


def changed_files(rev: str) -> list[tuple[str, str]]:
    raw = git("diff", "--name-status", "-M", rev)
    rows = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0][0], parts[-1]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rev", help="commit range to back-port, e.g. layout..main or a single sha")
    ap.add_argument("--out", default="backport.patch", help="patch file to write")
    ap.add_argument("--path", help="map one transformed path back and exit")
    args = ap.parse_args()

    mf = manifest_mod.load()
    rmap = reverse_map(mf)

    if args.path:
        hit = rmap.get(args.path)
        print(hit if hit else "NO UPSTREAM COUNTERPART (layout-only)")
        return 0 if hit else 1

    rows = changed_files(args.rev)
    if not rows:
        print("no changes in %s" % args.rev)
        return 0

    pairs = [(new, old) for new, old in renames(mf)]
    portable: list[tuple[str, str, str]] = []
    layout_only: list[str] = []

    for status, path in rows:
        upstream = rmap.get(path)
        if upstream is None:
            layout_only.append(path)
            continue
        blob = git("show", "%s:%s" % (args.rev.split("..")[-1] or "main", path), check=False)
        if not blob and status != "D":
            layout_only.append(path)
            continue
        for new, old in pairs:
            blob = reverse_content(blob, old, new)
        portable.append((path, upstream, blob))

    print("backportable : %d" % len(portable))
    print("layout-only  : %d" % len(layout_only))

    if layout_only:
        print("\nNOT backportable - these exist only in this layout:")
        for path in sorted(layout_only):
            print("  %s" % path)

    if not portable:
        print("\nnothing to write")
        return 0

    target = REPO / args.out
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        for path, upstream, blob in sorted(portable):
            before = git("show", "vendor:%s" % upstream, check=False)
            if before == blob:
                continue
            fh.write("--- a/%s\n+++ b/%s\n" % (upstream, upstream))
            fh.write("@@ CONTENT REPLACEMENT @@\n")
            fh.write(blob if blob.endswith("\n") else blob + "\n")

    print("\nwrote %s" % target)
    print("mapped paths:")
    for path, upstream, _ in sorted(portable)[:20]:
        print("  %s\n    -> %s" % (path, upstream))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
