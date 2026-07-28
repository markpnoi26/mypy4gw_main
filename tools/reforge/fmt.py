"""Runs the pinned formatters. pyproject.toml is the single source of config."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# black is safe on any tree: it reformats, it never reorders statements.
#
# isort is NOT safe on upstream's code. Upstream has circular imports that only
# resolve because of import ORDER — Core/Py4GWcorelib.py imports Console before
# BehaviorTree precisely so BehaviorTree can import Console back out of the
# half-initialised module. Alphabetising that raises ImportError at load. isort
# has no "preserve order" mode, so it is scoped to trees we own outright.
# qa/test_imports.py is the gate that caught this and will catch the next one.
ISORT_ROOTS = ("tools", "qa", "Scripts/py4gw-marks-corner")


def formatter_python() -> str:
    venv = REPO / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def isort_targets(targets: list[str]) -> list[str]:
    if targets == ["."]:
        return [root for root in ISORT_ROOTS if (REPO / root).exists()]
    owned = []
    for target in targets:
        rel = target.replace("\\", "/")
        if any(rel == root or rel.startswith(root + "/") for root in ISORT_ROOTS):
            owned.append(target)
    return owned


def run_formatters(paths: list[str] | None = None) -> None:
    targets = paths if paths else ["."]
    py = formatter_python()

    owned = isort_targets(targets)
    if owned:
        isort = subprocess.run([py, "-m", "isort", *owned], cwd=REPO, capture_output=True, text=True)
        if isort.returncode != 0:
            sys.exit("isort failed:\n%s" % (isort.stderr or isort.stdout))

    black = subprocess.run([py, "-m", "black", *targets], cwd=REPO, capture_output=True, text=True)
    # 123: some files black cannot parse - they stay as upstream shipped them
    if black.returncode not in (0, 123):
        sys.exit("black failed:\n%s" % (black.stderr or black.stdout))

    err = black.stderr or ""
    for line in err.splitlines():
        if line.startswith("error: cannot format"):
            print("  left unformatted: %s" % line.removeprefix("error: cannot format "))
    summary = [ln for ln in err.splitlines() if "file" in ln and ("reformatted" in ln or "unchanged" in ln)]
    if summary:
        print("  %s" % summary[-1])


if __name__ == "__main__":
    run_formatters(sys.argv[1:] or None)
