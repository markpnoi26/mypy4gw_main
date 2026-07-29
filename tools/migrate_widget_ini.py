"""Rewrite WidgetManager.ini section keys through the manifest's legacy_id map.

A widget's INI section key is its path, so a manifest move orphans the user's
saved enabled-state. verify.py enforces that every moved widget has a legacy_id,
but nothing consumes that map at runtime, so the aliases never reach the INI.
This applies them to the files that already exist.

Runs from a plain interpreter, never inside the client, so it reads and writes
these files directly rather than through Settings.
"""

import argparse
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tools" / "reforge" / "layout.toml"
SECTION_PREFIX = "[Widget:"
WIDGET_DEST_PREFIX = "widgets:"


def widget_legacy_map() -> dict[str, str]:
    """old widget id -> new widget id, forward-slash normalised, widgets only."""
    doc = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    mapping = {}
    for row in doc.get("legacy_id", []):
        new = row["new"]
        if not new.startswith(WIDGET_DEST_PREFIX):
            continue
        mapping[normalise(row["old"])] = new[len(WIDGET_DEST_PREFIX) :]
    return mapping


def normalise(widget_id: str) -> str:
    return widget_id.replace("\\", "/")


def section_id(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith(SECTION_PREFIX) or not stripped.endswith("]"):
        return None
    return stripped[len(SECTION_PREFIX) : -1]


def plan_for(path: Path, mapping: dict[str, str]) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    present = {normalise(sid) for sid in (section_id(line) for line in lines) if sid}

    out: list[str] = []
    renamed: list[tuple[str, str]] = []
    skipped: list[str] = []

    for line in lines:
        sid = section_id(line)
        if sid is None:
            out.append(line)
            continue
        new_id = mapping.get(normalise(sid))
        if new_id is None or normalise(sid) == new_id:
            out.append(line)
            continue
        if new_id in present:
            skipped.append(f"{sid} -> {new_id} (target section already present)")
            out.append(line)
            continue
        if not (ROOT / "Widgets" / new_id).is_file():
            skipped.append(f"{sid} -> {new_id} (no such widget in this tree; stale legacy_id)")
            out.append(line)
            continue
        ending = line[len(line.rstrip("\r\n")) :]
        out.append(f"{SECTION_PREFIX}{new_id}]{ending}")
        renamed.append((sid, new_id))

    return out, renamed, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    args = parser.parse_args()

    mapping = widget_legacy_map()
    targets = sorted(ROOT.glob("Settings/*/Widgets/WidgetManager/WidgetManager.ini"))
    if not targets:
        print("no WidgetManager.ini found under Settings/*/")
        return 1

    print(f"{len(mapping)} widget legacy_id entries, {len(targets)} account file(s)\n")
    total_renamed = 0
    total_skipped = 0

    for path in targets:
        out, renamed, skipped = plan_for(path, mapping)
        account = path.relative_to(ROOT).parts[1]
        if not renamed and not skipped:
            print(f"{account}: nothing to migrate")
            continue

        print(f"{account}: {len(renamed)} to rename")
        for old, new in renamed:
            print(f"    {old}\n      -> {new}")
        for note in skipped:
            print(f"    SKIP {note}")

        total_renamed += len(renamed)
        total_skipped += len(skipped)

        if args.apply and renamed:
            backup = path.with_suffix(".ini.premigrate")
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_text("".join(out), encoding="utf-8")
            print(f"    written (backup: {backup.name})")

    print(f"\ntotal: {total_renamed} rename(s), {total_skipped} skipped")
    if not args.apply:
        print("dry run - nothing written. re-run with --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
