"""Enforces tier_map.toml with AST analysis: no file may import above its own tier.

Also reports the eager import closure of the core facade, which is the metric that
actually decides whether the tiers hold at runtime rather than only on paper.
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SKIP_DIRS = {"__pycache__", ".git", ".venv", ".vs", ".idea", "node_modules"}


def load_map(path: Path | None = None):
    with open(path or HERE / "tier_map.toml", "rb") as fh:
        doc = tomllib.load(fh)
    rows = []
    for entry in doc.get("tier", []):
        for prefix in entry["paths"]:
            rows.append((prefix, float(entry["level"]), entry["name"]))
    rows.sort(key=lambda r: -len(r[0]))
    waivers = {row["path"] for row in doc.get("waiver", [])}
    return rows, waivers, doc.get("meta", {})


def tier_of(rel: str, rows) -> tuple[float, str] | tuple[None, None]:
    for prefix, level, name in rows:
        if rel == prefix or rel.startswith(prefix):
            return level, name
    return None, None


def stub_names() -> set[str]:
    base = REPO / "stubs"
    if not base.is_dir():
        return set()
    return {p.stem for p in base.glob("*.pyi")}


def module_to_rel(mod: str) -> list[str]:
    parts = mod.split(".")
    return ["/".join(parts) + ".py", "/".join(parts) + "/__init__.py"]


def imports_of(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.add(node.module)
    return out


def package_of(path: Path, modname: str) -> str:
    if path.name == "__init__.py":
        return modname
    return modname.rsplit(".", 1)[0] if "." in modname else ""


def absolute_name(pkg: str, level: int, module: str | None) -> str:
    base = pkg.split(".") if pkg else []
    if level > 1:
        base = base[: len(base) - (level - 1)]
    parts = base + (module.split(".") if module else [])
    return ".".join(p for p in parts if p)


def eager_imports(path: Path, modname: str) -> set[str]:
    """Module-scope imports only - what actually executes on import."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    pkg = package_of(path, modname)
    out = set()
    body = list(tree.body)
    for node in body:
        if isinstance(node, ast.Try):
            body.extend(node.body)
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    out.add(node.module)
            else:
                resolved = absolute_name(pkg, node.level, node.module)
                if resolved:
                    out.add(resolved)
    return out


def resolve(mod: str) -> Path | None:
    for rel in module_to_rel(mod):
        candidate = REPO / rel
        if candidate.is_file():
            return candidate
    return None


def closure(root: str) -> set[str]:
    seen: set[str] = set()
    stack = [root]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        path = resolve(mod)
        if path is None:
            continue
        seen.add(mod)
        stack.extend(dep for dep in eager_imports(path, mod) if dep not in seen)
    return seen


def walk_py():
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", default="", help="facade module to measure, e.g. Core or Py4GWCoreLib")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rows, waivers, meta = load_map()
    stubs = stub_names()

    violations: list[tuple[str, float, str, float]] = []
    waived = 0
    tally: dict[str, int] = defaultdict(int)

    for path in walk_py():
        rel = path.relative_to(REPO).as_posix()
        level, name = tier_of(rel, rows)
        if level is None:
            continue
        tally[name] += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for mod in imports_of(tree):
            top = mod.split(".")[0]
            if top in stubs:
                continue
            target = resolve(mod)
            if target is None:
                continue
            trel = target.relative_to(REPO).as_posix()
            tlevel, _ = tier_of(trel, rows)
            if tlevel is None or tlevel <= level:
                continue
            if rel in waivers:
                waived += 1
                continue
            violations.append((rel, level, mod, tlevel))

    print("files by tier:")
    for name, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %6d  %s" % (count, name))

    print("\nviolations: %d   (waived: %d)" % (len(violations), waived))
    grouped: dict[tuple[float, float], list[tuple[str, str]]] = defaultdict(list)
    for rel, level, mod, tlevel in violations:
        grouped[(level, tlevel)].append((rel, mod))
    for (level, tlevel), items in sorted(grouped.items()):
        print("\n  T%s -> T%s   (%d)" % (level, tlevel, len(items)))
        if not args.quiet:
            for rel, mod in sorted(items):
                print("    %s\n        imports %s" % (rel, mod))

    if args.core:
        pulled = closure(args.core)
        by_root: dict[str, int] = defaultdict(int)
        for mod in pulled:
            by_root[mod.split(".")[0]] += 1
        print("\neager import closure of `import %s`: %d modules" % (args.core, len(pulled)))
        for root, count in sorted(by_root.items(), key=lambda kv: -kv[1]):
            print("  %6d  %s" % (count, root))
        leaked = sorted(m for m in pulled if m.split(".")[0] == "HeroAI")
        if leaked:
            print("\n  TIER 3 LEAKED INTO THE FACADE (%d):" % len(leaked))
            for mod in leaked:
                print("    %s" % mod)

    if violations and meta.get("enforce"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
