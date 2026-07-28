"""What is broken in the leaf tier, why, and whether we care.

The pytest gate answers pass/fail. This answers "is it worth fixing" — for each
break it names the missing module and, if the reorganisation moved it, where it
lives now. It then applies RS-004: a break outside a protected pack is not a bug
report, it is a deprecation. Those land in rules/DEPRECATED.md and stop counting
against the gate.

    python qa/breakage.py
    python qa/breakage.py --vs-upstream    # also ask whether upstream's copy loads
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import traceback
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

import nativestub
import test_imports

REPO = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = ("Core", "HeroAI", "Widgets", "Scripts", "dev", "Sources", "Runtime")

# RS-004. Breakage inside these is worth fixing; breakage outside them is worth
# deleting. Everything else in the leaf tier is inherited community code we do
# not maintain, so a file that stops loading there is deprecated, not broken.
PROTECTED = ("Scripts/py4gw-marks-corner/",)

BREAKAGE = REPO / "rules" / "BREAKAGE.md"
DEPRECATED = REPO / "rules" / "DEPRECATED.md"
VERDICTS = REPO / "rules" / "upstream-verdicts.tsv"


def protected(rel: str) -> bool:
    return rel.startswith(PROTECTED)


def load_verdicts() -> dict[str, str]:
    """Cached answer to 'does upstream's copy of this file load?', keyed by our path."""
    if not VERDICTS.is_file():
        return {}
    rows = {}
    for line in VERDICTS.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rel, _, verdict = line.partition("\t")
            rows[rel] = verdict
    return rows


def suffix_score(a: Path, b: Path) -> int:
    n = 0
    for x, y in zip(list(a.parts)[::-1], list(b.parts)[::-1]):
        if x != y:
            break
        n += 1
    return n


def compute_verdicts(rels: list[str]) -> dict[str, str]:
    """Run this same harness against a pristine vendor tree.

    The only way to tell a break we caused from one we inherited, and the two
    deserve opposite responses. Matching is by longest trailing-path match, not
    basename — three different files in this tree are called FSM.py.
    """
    with tempfile.TemporaryDirectory(prefix="reforge-vendor-") as tmp:
        work = Path(tmp) / "tree"
        add = subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach", str(work), "vendor"],
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            os.write(2, b"could not create vendor worktree; skipping --vs-upstream\n")
            return {}
        try:
            index: dict[str, list[Path]] = defaultdict(list)
            for path in work.rglob("*.py"):
                if not ({".git", "__pycache__"} & set(path.parts)):
                    index[path.name].append(path)

            saved_stubs, saved_repo = nativestub.STUBS, nativestub.REPO
            nativestub.STUBS, nativestub.REPO = work / "stubs", work
            sys.path.insert(0, str(work))
            try:
                out = {}
                for rel in rels:
                    ours = Path(rel)
                    hits = index.get(ours.name)
                    if not hits:
                        out[rel] = "ABSENT"
                        continue
                    match = max(hits, key=lambda h: suffix_score(ours, h.relative_to(work)))
                    out[rel] = "LOADS" if loads(match) else "FAILS"
                return out
            finally:
                sys.path.remove(str(work))
                nativestub.STUBS, nativestub.REPO = saved_stubs, saved_repo
        finally:
            subprocess.run(
                ["git", "-C", str(REPO), "worktree", "remove", str(work), "--force"],
                capture_output=True,
            )


def loads(path: Path) -> bool:
    name = "vendorcheck_%s" % abs(hash(str(path)))
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        return False
    module = importlib.util.module_from_spec(spec)
    try:
        with redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
        return True
    except BaseException:
        return False


def build_index() -> dict[str, list[str]]:
    """name -> where anything by that name now lives."""
    index: dict[str, list[str]] = defaultdict(list)
    for root in SEARCH_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            if path.is_dir():
                index[path.name].append(rel)
            elif path.suffix == ".py":
                index[path.stem].append(rel)
    return index


def load_manifest():
    sys.path.insert(0, str(REPO / "tools" / "reforge"))
    try:
        import manifest as manifest_mod

        return manifest_mod.load()
    except Exception:
        return None


MANIFEST = load_manifest()


def manifest_verdict(module: str) -> str | None:
    """What the manifest did to this path — authoritative, unlike guessing."""
    if MANIFEST is None:
        return None
    prefix = module.replace(".", "/")
    hits = []
    for entry in MANIFEST.entries:
        literal = entry.match.split("*")[0].split("{")[0].rstrip("/")
        if not literal:
            continue
        if literal == prefix or literal.startswith(prefix + "/"):
            hits.append(entry)
    if not hits:
        return None
    entry = min(hits, key=lambda e: len(e.match))
    if entry.dest == "drop":
        return "DROPPED by rule `%s`" % entry.match
    return "`%s` → `%s`" % (entry.match, entry.dest)


def locate(module: str, index: dict[str, list[str]]) -> str:
    verdict = manifest_verdict(module)
    if verdict:
        return verdict
    tail = module.split(".")[-1]
    hits = index.get(tail, [])
    if hits:
        extra = "" if len(hits) == 1 else " (+%d more)" % (len(hits) - 1)
        return "`%s`%s" % (hits[0], extra)
    return "not in the tree — dropped, or never existed"


def failure_of(path: Path):
    try:
        test_imports.load_from_path(path)
    except BaseException as exc:
        return exc
    return None


def missing_module(exc: BaseException) -> str | None:
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        return exc.name
    return None


def collect():
    results = []
    for package in ("Widgets", "Scripts"):
        for path in test_imports.loadable_files(package):
            exc = failure_of(path)
            if exc is not None:
                results.append((path.relative_to(REPO).as_posix(), exc))
    return results


def origin(rel: str, verdicts: dict[str, str]) -> str:
    return {"LOADS": "ours", "FAILS": "inherited", "ABSENT": "ours (no upstream copy)"}.get(
        verdicts.get(rel, ""), "unknown"
    )


def render(results, index, verdicts) -> str:
    lines: list[str] = []
    total = len(test_imports.loadable_files("Widgets")) + len(test_imports.loadable_files("Scripts"))
    keep = [(rel, exc) for rel, exc in results if protected(rel)]
    drop = [(rel, exc) for rel, exc in results if not protected(rel)]

    lines.append("# Leaf breakage")
    lines.append("")
    lines.append("%d of %d widget/script files fail to load." % (len(results), total))
    lines.append("")
    lines.append("Generated by `python qa/breakage.py`. Nothing here is fixed automatically.")
    lines.append("")
    lines.append("Under **RS-004** these split into two piles that get opposite treatment:")
    lines.append("")
    lines.append("| pile | count | what happens |")
    lines.append("|---|---|---|")
    lines.append("| **worth fixing** — inside a protected pack | %d | stays in the gate, fix it |" % len(keep))
    lines.append("| **deprecated** — everywhere else | %d | tracked in `DEPRECATED.md`, off the gate |" % len(drop))
    lines.append("")
    lines.append("Protected packs: %s" % ", ".join("`%s`" % p for p in PROTECTED))
    lines.append("")

    if keep:
        lines.append("## Worth fixing")
        lines.append("")
        lines.append("| file | origin | error |")
        lines.append("|---|---|---|")
        for rel, exc in sorted(keep):
            detail = str(exc).splitlines()[0][:70] if str(exc) else ""
            lines.append("| `%s` | %s | %s: %s |" % (rel, origin(rel, verdicts), type(exc).__name__, detail))
        lines.append("")

    by_missing: dict[str, list[str]] = defaultdict(list)
    other: list[tuple[str, BaseException]] = []
    for rel, exc in results:
        name = missing_module(exc)
        if name:
            by_missing[name].append(rel)
        else:
            other.append((rel, exc))

    lines.append("## Missing modules — the reorganisation moved these")
    lines.append("")
    lines.append("| wanted | by | now lives at |")
    lines.append("|---|---|---|")
    for name in sorted(by_missing, key=lambda n: -len(by_missing[n])):
        lines.append("| `%s` | %d | %s |" % (name, len(by_missing[name]), locate(name, index)))
    lines.append("")

    lines.append("## Everything else")
    lines.append("")
    if other:
        lines.append("| file | error |")
        lines.append("|---|---|")
        for rel, exc in sorted(other):
            detail = str(exc).splitlines()[0][:90] if str(exc) else ""
            lines.append("| `%s` | %s: %s |" % (rel, type(exc).__name__, detail))
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Which files, per missing module")
    lines.append("")
    for name in sorted(by_missing, key=lambda n: -len(by_missing[n])):
        lines.append("**`%s`** → %s" % (name, locate(name, index)))
        lines.append("")
        for rel in sorted(by_missing[name]):
            lines.append("- `%s`" % rel)
        lines.append("")
    return "\n".join(lines)


def render_deprecated(results, verdicts) -> str:
    drop = sorted((rel, exc) for rel, exc in results if not protected(rel))
    lines = [
        "# Deprecated leaves",
        "",
        "%d files. **This list is generated — edit the rule, not the list.**" % len(drop),
        "",
        "Under RS-004 a leaf that fails to load outside a protected pack is treated",
        "as unwanted rather than broken. Nothing here is deleted: the files are still",
        "in the tree and still in git. They are simply off the gate, so they cannot",
        "hold up a sync, and listed here so they cannot disappear quietly either.",
        "",
        "To rescue one, fix it and it leaves this list on the next run. To protect a",
        "whole pack from the rule, add it to `PROTECTED` in `qa/breakage.py`.",
        "",
        "`origin` is whether upstream's own copy loads: **ours** means our transform",
        "broke a file that worked upstream, **inherited** means it was already broken.",
        "Refresh it with `python qa/breakage.py --vs-upstream`.",
        "",
        "| file | origin | error |",
        "|---|---|---|",
    ]
    for rel, exc in drop:
        detail = str(exc).splitlines()[0][:70] if str(exc) else ""
        lines.append("| `%s` | %s | %s: %s |" % (rel, origin(rel, verdicts), type(exc).__name__, detail))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vs-upstream",
        action="store_true",
        help="also import each failing file from a pristine vendor worktree, to tell ours from inherited",
    )
    args = parser.parse_args()

    nativestub.install()
    results = collect()
    index = build_index()

    verdicts = load_verdicts()
    if args.vs_upstream:
        verdicts = compute_verdicts([rel for rel, _ in results])
        VERDICTS.parent.mkdir(parents=True, exist_ok=True)
        VERDICTS.write_text(
            "# does upstream's copy of this file load? LOADS = we broke it.\n"
            + "".join("%s\t%s\n" % (rel, verdicts[rel]) for rel in sorted(verdicts)),
            encoding="utf-8",
        )

    # Importing the tree redirects sys.stdout into the Py4GW console, so the
    # report always goes to a file — printing it would vanish.
    BREAKAGE.parent.mkdir(parents=True, exist_ok=True)
    BREAKAGE.write_text(render(results, index, verdicts) + "\n", encoding="utf-8")
    DEPRECATED.write_text(render_deprecated(results, verdicts) + "\n", encoding="utf-8")
    kept = sum(1 for rel, _ in results if protected(rel))
    os.write(1, b"wrote rules/BREAKAGE.md and rules/DEPRECATED.md\n")
    os.write(1, b"  %d broken: %d worth fixing, %d deprecated\n" % (len(results), kept, len(results) - kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
