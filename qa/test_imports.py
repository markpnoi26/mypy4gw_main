"""Does the tree actually load? The only gate that executes module bodies.

Everything else checks the tree's shape. This one catches a bad move, a bad
codemod rewrite, and — the reason it exists — upstream shipping a module that
does not import.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

SKIP_PARTS = {"__pycache__", ".venv", ".git", "dev", "tools", "tests", "qa", "stubs"}

# Broken in pristine upstream too — verified by importing them in a `vendor`
# worktree with this same harness. Not ours, and strict=True so they cannot
# quietly mask a real failure: if upstream fixes one, pytest reports XPASS and
# the entry comes off this list.
UPSTREAM_BROKEN = {
    "HeroAI.ui": "circular import — ui.py imports its own draw_* names back out of itself",
}


def cases(names: list[str]):
    return [
        (
            pytest.param(name, marks=pytest.mark.xfail(reason=UPSTREAM_BROKEN[name], strict=True))
            if name in UPSTREAM_BROKEN
            else name
        )
        for name in names
    ]


def module_names(package: str) -> list[str]:
    root = REPO / package
    if not root.is_dir():
        return []
    names = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(REPO)
        if SKIP_PARTS & set(rel.parts):
            continue
        # a filename with a space is unreachable by import; covered by path-load below
        if any(" " in part for part in rel.parts):
            continue
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        if parts:
            names.append(".".join(parts))
    return names


# Busy-wait or block at module scope, so importing them never returns. pytest's
# thread timeout method — the only one Windows supports — aborts the whole run
# rather than the one test, so these have to be excluded rather than timed out.
BLOCKING = {
    "Scripts/py4gw-examples/TestGenerator.py",
}


def deprecated() -> dict[str, str]:
    """RS-004: leaves we have decided not to keep. Skipped, not deleted.

    Read rather than hardcoded so the rule lives in one place — qa/breakage.py
    derives the list, this only honours it. Skipped rather than xfailed because
    several of these do real work at import before they fail.
    """
    ledger = REPO / "rules" / "DEPRECATED.md"
    if not ledger.is_file():
        return {}
    rows = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells[2]
    return rows


DEPRECATED = deprecated()


def loadable_files(package: str) -> list[Path]:
    root = REPO / package
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.rglob("*.py"))
        if not (SKIP_PARTS & set(p.relative_to(REPO).parts))
        and p.relative_to(REPO).as_posix() not in BLOCKING
    ]


def leaf_cases(package: str):
    out = []
    for path in loadable_files(package):
        reason = DEPRECATED.get(path.relative_to(REPO).as_posix())
        marks = [pytest.mark.skip(reason="deprecated (RS-004): %s" % reason)] if reason else []
        out.append(pytest.param(path, marks=marks))
    return out


def test_core_facade_imports():
    """`import Core` is what every widget and script does first."""
    importlib.import_module("Core")


@pytest.mark.parametrize("name", cases(module_names("Core")))
def test_core_module_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("name", cases(module_names("HeroAI")))
def test_heroai_module_imports(name):
    importlib.import_module(name)


@pytest.mark.leaf
@pytest.mark.parametrize("path", leaf_cases("Widgets"), ids=lambda p: p.relative_to(REPO).as_posix())
def test_widget_loads(path):
    load_from_path(path)


@pytest.mark.leaf
@pytest.mark.parametrize("path", leaf_cases("Scripts"), ids=lambda p: p.relative_to(REPO).as_posix())
def test_script_loads(path):
    load_from_path(path)


def load_from_path(path: Path) -> None:
    """Import a file whose name can't be a module identifier (spaces, dashes)."""
    name = "leafmod_%s" % abs(hash(str(path)))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
