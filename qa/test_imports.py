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


def loadable_files(package: str) -> list[Path]:
    root = REPO / package
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.rglob("*.py"))
        if not (SKIP_PARTS & set(p.relative_to(REPO).parts))
    ]


def test_core_facade_imports():
    """`import Core` is what every widget and script does first."""
    importlib.import_module("Core")


@pytest.mark.parametrize("name", module_names("Core"))
def test_core_module_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("name", module_names("HeroAI"))
def test_heroai_module_imports(name):
    importlib.import_module(name)


@pytest.mark.leaf
@pytest.mark.parametrize(
    "path", loadable_files("Widgets"), ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_widget_loads(path):
    load_from_path(path)


@pytest.mark.leaf
@pytest.mark.parametrize(
    "path", loadable_files("Scripts"), ids=lambda p: p.relative_to(REPO).as_posix()
)
def test_script_loads(path):
    load_from_path(path)


def load_from_path(path: Path) -> None:
    """Import a file whose name can't be a module identifier (spaces, dashes)."""
    name = "leafmod_%s" % abs(hash(str(path)))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
