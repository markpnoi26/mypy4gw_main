"""Applies the layout transform: moves, drops, derived markers, codemods."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import fmt
import manifest as manifest_mod
import pins as pins_mod

REPO = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True)
    return out.stdout


def tracked_files() -> list[str]:
    return [line for line in git("ls-files").splitlines() if line.strip()]


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").strip()


def working_tree_dirty() -> bool:
    return bool(git("status", "--porcelain").strip())


def guard(args, mf: manifest_mod.Manifest) -> None:
    pristine = mf.meta.get("base", "vendor")
    branch = current_branch()
    if branch == pristine:
        sys.exit("refusing to transform %r — it must stay identical to upstream/main" % pristine)
    if working_tree_dirty() and not args.dry_run:
        sys.exit("working tree is dirty — commit or stash first")


def plan(mf: manifest_mod.Manifest, files: list[str]):
    moves, drops, uncovered = [], [], []
    for src in files:
        res = mf.resolve(src)
        if res is None:
            uncovered.append(src)
        elif res.is_drop:
            drops.append(res)
        elif res.moves:
            moves.append(res)
    return moves, drops, uncovered


def widget_marker_dirs(moves) -> set[str]:
    return {res.dest.rsplit("/", 1)[0] for res in moves if res.entry.tier == "widget" and "/" in res.dest}


def pack_roots(moves) -> dict[str, str]:
    roots: dict[str, str] = {}
    for res in moves:
        if not res.entry.pack:
            continue
        parts = res.dest.split("/")
        if len(parts) >= 2 and parts[0] == "Scripts":
            roots[res.entry.pack] = "/".join(parts[:2])
    return roots


def run_moves(moves, dry: bool) -> None:
    for res in moves:
        src, dest = REPO / res.src, REPO / res.dest
        if dry:
            continue
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))


def run_drops(drops, dry: bool) -> None:
    for res in drops:
        target = REPO / res.src
        if dry or not target.exists():
            continue
        target.unlink()


def write_markers(dirs: set[str], dry: bool) -> None:
    for folder in sorted(dirs):
        if dry:
            continue
        path = REPO / folder / ".widget"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def write_pack_manifests(roots: dict[str, str], dry: bool) -> None:
    for pack, root in sorted(roots.items()):
        if dry:
            continue
        target = REPO / root / "pack.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "id": pack,
                    "name": pack,
                    "version": "0.1.0",
                    "requires_core": ">=2.0.0",
                    "script_roots": ["scripts"],
                    "lib_root": "lib",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def write_legacy_ids(mf: manifest_mod.Manifest, dry: bool) -> None:
    if dry:
        return
    target = REPO / "Runtime" / "config" / "legacy_widget_ids.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(mf.legacy_ids, indent=2) + "\n", encoding="utf-8")


def write_module_aliases(mf: manifest_mod.Manifest, dry: bool) -> list[str]:
    """Bake each moved widget's pre-move id into the widget as MODULE_ALIASES.

    Runtime cannot read Runtime/config/legacy_widget_ids.json: JsonFactory scopes
    live strictly under json/ and there is no root scope, so the map has to travel
    inside the module. WidgetManager.adopt_legacy_enabled consumes it to carry the
    user's enabled-state across the move.
    """
    unanchored = []
    for old_id, new_dest in mf.legacy_ids.items():
        if not new_dest.startswith("widgets:"):
            continue
        target = REPO / "Widgets" / new_dest[len("widgets:") :]
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8")
        if "MODULE_ALIASES" in text:
            continue
        lines = text.splitlines(keepends=True)
        anchor = next((i for i, line in enumerate(lines) if line.startswith("MODULE_NAME")), None)
        if anchor is None:
            unanchored.append(new_dest[len("widgets:") :])
            continue
        lines.insert(anchor, f"MODULE_ALIASES = [{old_id!r}]\n")
        if not dry:
            target.write_text("".join(lines), encoding="utf-8")
    return unanchored


def prune_empty_dirs(dry: bool) -> int:
    candidates = [p for p in REPO.rglob("*") if p.is_dir()]
    candidates.sort(key=lambda p: len(p.parts), reverse=True)
    removed = 0
    for path in candidates:
        if any(part in (".git", ".venv", ".claude") for part in path.parts):
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            if not dry:
                path.rmdir()
            removed += 1
        except OSError:
            continue
    return removed


def rename_imports(old: str, new: str, keep: tuple[str, ...], text: str) -> str:
    """Rewrite `from old.x` / `import old.x` only, leaving all other text alone.

    A blunt token rename is wrong whenever the transform leaves carve-outs at the
    original path: Sources moved to dev/reference but marks_sources and InvPlus
    stayed put, and they are imported from files that do get rewritten, so the
    exemption has to be per-import, not per-file. It is also wrong for string
    paths — TeamInventoryViewer.py:35 joins a real directory called "Sources/",
    and turning that into dots would break it.
    """
    pattern = re.compile(r"^(\s*)(from|import)(\s+)(%s(?:\.[\w.]*)?)" % re.escape(old), re.MULTILINE)

    def swap(match: re.Match) -> str:
        dotted = match.group(4)
        if any(dotted == k or dotted.startswith(k + ".") for k in keep):
            return match.group(0)
        return "".join(match.group(1, 2, 3)) + new + dotted[len(old) :]

    return pattern.sub(swap, text)


def run_codemods(mf: manifest_mod.Manifest, dry: bool) -> int:
    touched = 0
    for mod in mf.codemods:
        kind = mod.get("kind")
        if kind not in ("module_rename", "import_rename"):
            continue
        old, new = mod["old"], mod["new"]
        exclude = tuple(mod.get("exclude", []))
        keep = tuple(mod.get("keep", []))
        pattern = re.compile(r"\b%s\b" % re.escape(old))
        for path in REPO.rglob("*.py"):
            if any(part in (".git", "__pycache__", ".venv") for part in path.parts):
                continue
            rel = path.relative_to(REPO).as_posix()
            if any(rel.startswith(prefix) for prefix in exclude):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if old not in text:
                continue
            replaced = rename_imports(old, new, keep, text) if kind == "import_rename" else pattern.sub(new, text)
            if replaced != text:
                touched += 1
                if not dry:
                    path.write_text(replaced, encoding="utf-8")
    return touched


def summarize(moves, drops, uncovered, markers, packs) -> None:
    by_dest_root: dict[str, int] = defaultdict(int)
    for res in moves:
        by_dest_root[res.dest.split("/")[0]] += 1
    print("plan")
    print("  moves            %d" % len(moves))
    print("  drops            %d" % len(drops))
    print("  uncovered (stay) %d" % len(uncovered))
    print("  .widget markers  %d" % len(markers))
    print("  pack manifests   %d" % len(packs))
    print("\ndestination roots:")
    for root, count in sorted(by_dest_root.items(), key=lambda kv: -kv[1]):
        print("  %-14s %d" % (root, count))
    if drops:
        print("\ndrops:")
        for res in drops[:20]:
            print("  %s" % res.src)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-format", action="store_true")
    parser.add_argument("--reverse", metavar="PATH")
    args = parser.parse_args()

    mf = manifest_mod.load()

    if args.reverse:
        for src in tracked_files():
            res = mf.resolve(src)
            if res and res.dest == args.reverse:
                print(src)
                return 0
        print("no upstream path maps to %s" % args.reverse, file=sys.stderr)
        return 1

    guard(args, mf)
    applied_pins = pins_mod.apply_pins(mf, args.dry_run)
    files = tracked_files()
    moves, drops, uncovered = plan(mf, files)
    markers = widget_marker_dirs(moves)
    packs = pack_roots(moves)

    summarize(moves, drops, uncovered, markers, packs)

    run_moves(moves, args.dry_run)
    run_drops(drops, args.dry_run)
    write_markers(markers, args.dry_run)
    write_pack_manifests(packs, args.dry_run)
    write_legacy_ids(mf, args.dry_run)
    for path in write_module_aliases(mf, args.dry_run):
        print(f"  no MODULE_NAME anchor, alias not injected: {path}")
    touched = run_codemods(mf, args.dry_run)
    pruned = prune_empty_dirs(args.dry_run)
    print("pruned %d empty directories" % pruned)
    pins_mod.report(applied_pins)

    print("\ncodemod: %d files %s" % (touched, "would change" if args.dry_run else "rewritten"))
    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    if not args.no_format:
        print("\nformat stage (isort + black, config from pyproject.toml):")
        fmt.run_formatters()

    git("add", "-A")
    print("staged — index now reflects the transformed tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
