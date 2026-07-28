"""Postconditions for a transformed tree. Run after apply.py."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import manifest as manifest_mod

REPO = Path(__file__).resolve().parents[2]

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
FORBIDDEN_FROM_CORE = ("Widgets", "Scripts", "Bots", "Sources")


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True)
    return out.stdout


def python_files(under: str) -> list[Path]:
    base = REPO / under
    if not base.is_dir():
        return []
    return [p for p in base.rglob("*.py") if not any(part in ("__pycache__", ".venv") for part in p.parts)]


def check_core_purity() -> list[str]:
    problems = []
    for path in python_files("Core"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for top in IMPORT_RE.findall(text):
            if top in FORBIDDEN_FROM_CORE:
                rel = path.relative_to(REPO).as_posix()
                problems.append("%s imports %s" % (rel, top))
    return problems


def check_no_stale_module_names(mf: manifest_mod.Manifest) -> list[str]:
    problems = []
    for mod in mf.codemods:
        if mod.get("kind") != "module_rename":
            continue
        needle = re.compile(r"\b%s\b" % re.escape(mod["old"]))
        exclude = tuple(mod.get("exclude", []))
        for path in REPO.rglob("*.py"):
            if any(part in (".git", "__pycache__", ".venv", ".claude") for part in path.parts):
                continue
            if any(path.relative_to(REPO).as_posix().startswith(prefix) for prefix in exclude):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if needle.search(text):
                problems.append("%s still references %s" % (path.relative_to(REPO).as_posix(), mod["old"]))
    return problems


def was_discovered_widget(src: str, tracked: set[str]) -> bool:
    folder = src.rsplit("/", 1)[0] if "/" in src else ""
    return bool(folder) and "%s/.widget" % folder in tracked


def check_legacy_ids(mf: manifest_mod.Manifest, tracked: set[str]) -> list[str]:
    problems = []
    for src in sorted(tracked):
        if not src.endswith(".py"):
            continue
        res = mf.resolve(src)
        if res is None or res.entry.tier != "widget" or not res.moves:
            continue
        if not was_discovered_widget(src, tracked):
            continue
        stem = src[len("Widgets/") :] if src.startswith("Widgets/") else src
        if stem not in mf.legacy_ids:
            problems.append("no legacy_id for moved widget %s" % stem)
    return problems


def check_idempotent(mf: manifest_mod.Manifest) -> list[str]:
    remaining = []
    for line in git("ls-files").splitlines():
        if not line.strip():
            continue
        res = mf.resolve(line)
        if res is not None and res.moves:
            remaining.append(line)
    if remaining:
        return ["%d files still resolve to a move (transform not idempotent)" % len(remaining)]
    return []


def report(title: str, problems: list[str], limit: int = 15) -> bool:
    if not problems:
        print("  PASS  %s" % title)
        return True
    print("  FAIL  %s — %d" % (title, len(problems)))
    for line in problems[:limit]:
        print("          %s" % line)
    if len(problems) > limit:
        print("          ... %d more" % (len(problems) - limit))
    return False


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
    mf = manifest_mod.load()
    tracked = {line for line in git("ls-files").splitlines() if line.strip()}
    transformed = is_transformed()

    print("verify (%s tree)" % ("transformed" if transformed else "pre-transform"))
    ok = True

    if transformed:
        ok &= report("Core imports no higher tier", check_core_purity())
        ok &= report("no stale module names", check_no_stale_module_names(mf))
        ok &= report("transform is idempotent", check_idempotent(mf))
    else:
        print("  SKIP  tree checks (Core/ absent — run apply.py first)")

    ok &= report("legacy_id coverage for moved widgets", check_legacy_ids(mf, tracked))
    print("\n%s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
