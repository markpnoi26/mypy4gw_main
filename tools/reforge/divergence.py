"""How far our work has drifted from upstream, measured per sync.

Two different distances, and conflating them hides the interesting one:

  transform  vendor..layout  files the reorganisation moved or rewrote. Large
                             and boring — it is mechanical and reproducible.
  ours       layout..main    files we actually changed. This is the number that
                             matters, and the one to keep honest.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "docs" / "DIVERGENCE.md"


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True)
    return out.stdout


def name_status(rev_range: str) -> list[tuple[str, str]]:
    rows = []
    for line in git("diff", "--name-status", "-M", rev_range).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        rows.append((parts[0][0], parts[-1]))
    return rows


def area_of(path: str) -> str:
    head = path.split("/")[0]
    if head in ("Core", "HeroAI", "Widgets", "Scripts", "qa", "tools", "docs", "dev", "Runtime"):
        return head
    return "(root)"


def measure() -> dict:
    ours = name_status("layout..main")
    transform = name_status("vendor..layout")

    by_area: dict[str, int] = defaultdict(int)
    added = modified = deleted = 0
    for status, path in ours:
        by_area[area_of(path)] += 1
        if status == "A":
            added += 1
        elif status == "D":
            deleted += 1
        else:
            modified += 1

    upstream_owned = sum(1 for status, path in ours if status == "M" and area_of(path) in ("Core", "HeroAI", "Widgets"))

    return {
        "date": date.today().isoformat(),
        "vendor": git("rev-parse", "--short", "vendor").strip(),
        "ours_total": len(ours),
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "conflict_surface": upstream_owned,
        "transform_total": len(transform),
        "by_area": dict(by_area),
    }


def row(m: dict) -> str:
    areas = ", ".join("%s %d" % (a, n) for a, n in sorted(m["by_area"].items(), key=lambda kv: -kv[1])[:4])
    return "| %s | `%s` | %d | %d | %d | %d | %d | %s |" % (
        m["date"],
        m["vendor"],
        m["ours_total"],
        m["added"],
        m["modified"],
        m["deleted"],
        m["conflict_surface"],
        areas,
    )


HEADER = """# Divergence from upstream

How far `main` has drifted, recorded once per sync. Appended, never rewritten —
the trend is the point.

**conflict surface** is the number we care about: files upstream also owns that
we have *modified*. Those are the only ones that can ever conflict. Added files
in our own namespaces are free.

| date | vendor | ours | added | modified | deleted | conflict surface | where |
|---|---|---|---|---|---|---|---|
"""


def append(m: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if not LEDGER.exists():
        LEDGER.write_text(HEADER, encoding="utf-8")
    text = LEDGER.read_text(encoding="utf-8")
    line = row(m)
    if line.split("|")[1:3] == text.splitlines()[-1].split("|")[1:3] if text.splitlines() else False:
        return
    LEDGER.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")


def main() -> int:
    m = measure()
    append(m)
    print(
        "divergence: %d files ours (%d of them upstream-owned = conflict surface)"
        % (m["ours_total"], m["conflict_surface"])
    )
    print("            transform moved/rewrote %d" % m["transform_total"])
    print("recorded in docs/DIVERGENCE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
