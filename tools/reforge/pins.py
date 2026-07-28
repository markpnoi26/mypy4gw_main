"""Holds specific upstream paths at an older revision.

When upstream ships a broken file you do not want the whole release rejected —
you want that one file held back. A pin restores it from an older vendor commit
before the transform runs, so the rest of the release still lands.

A pin is a debt. Every run prints them, and they go stale as upstream moves on,
so `stale_pins` reports how far behind each one has fallen.
"""

from __future__ import annotations

import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True)
    return out.stdout


def commits_behind(sha: str, path: str) -> int:
    """How many commits have touched this path since the pinned revision."""
    try:
        log = git("rev-list", "--count", "%s..vendor" % sha, "--", path)
    except subprocess.CalledProcessError:
        return -1
    return int(log.strip() or 0)


def apply_pins(manifest, dry: bool) -> list[dict]:
    applied = []
    for pin in manifest.pins:
        path, at = pin["path"], pin["at"]
        if not dry:
            try:
                git("checkout", at, "--", path)
            except subprocess.CalledProcessError as exc:
                raise SystemExit("pin failed: %s at %s\n%s" % (path, at, exc.stderr or "")) from exc
        applied.append(pin)
    return applied


def report(pins: list[dict]) -> None:
    if not pins:
        return
    print("\nPINS ACTIVE: %d — upstream content deliberately held back" % len(pins))
    for pin in pins:
        behind = commits_behind(pin["at"], pin["path"])
        drift = "unknown" if behind < 0 else "%d commit(s) behind" % behind
        print("  %s @ %s (%s)" % (pin["path"], pin["at"], drift))
        if pin.get("reason"):
            print("      %s" % pin["reason"])


def stale_pins(manifest, limit: int = 25) -> list[str]:
    """Pins that upstream has moved a long way past — likely rotting."""
    problems = []
    for pin in manifest.pins:
        behind = commits_behind(pin["at"], pin["path"])
        if behind > limit:
            problems.append(
                "%s pinned at %s is %d commits behind — re-check whether the pin is still needed"
                % (pin["path"], pin["at"], behind)
            )
    return problems
