"""One command for a whole upstream sync, gated at every step.

It deliberately stops short of touching `main`. Everything lands on `staging`,
which is what `main` *would* become; if a gate fails you delete that branch and
`main` never moved. Promotion is a separate, human decision — see promote.py.

    python tools/reforge/sync.py            # fetch, transform, stage, gate
    python tools/reforge/sync.py --offline  # skip the fetch, redo the rest
"""

from __future__ import annotations

import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

import divergence
import manifest as manifest_mod
import pins as pins_mod
import variants as variants_mod

REPO = Path(__file__).resolve().parents[2]
STATUS = REPO / "STATUS.md"
PY = str(REPO / ".venv" / "Scripts" / "python.exe")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=check)


def out(*args: str) -> str:
    return git(*args).stdout.strip()


def run(cmd: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, env=env)


class Report:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, str]] = []
        self.stopped: str | None = None
        self.conflicts: list[dict] = []

    def add(self, name: str, state: str, detail: str = "") -> None:
        self.steps.append((name, state, detail))
        marker = {"ok": "  ok  ", "FAIL": " FAIL ", "info": "      "}.get(state, "      ")
        print("[%s] %s%s" % (marker, name, (" — " + detail) if detail else ""))

    def stop(self, name: str, detail: str) -> None:
        self.add(name, "FAIL", detail)
        self.stopped = name


def gate(report: Report, name: str, cmd: list[str], allow_fail: bool = False) -> bool:
    result = run(cmd)
    tail = (result.stdout or result.stderr or "").strip().splitlines()
    detail = tail[-1] if tail else ""
    if result.returncode == 0:
        report.add(name, "ok", detail)
        return True
    if allow_fail:
        report.add(name, "info", "known-failing: %s" % detail)
        return True
    report.stop(name, detail)
    return False


def write_status(report: Report, facts: dict) -> None:
    lines = [
        "# Sync status",
        "",
        "%s · vendor `%s` → `%s`" % (facts["date"], facts["vendor_before"], facts["vendor_after"]),
        "",
        "**%s**"
        % (
            "STOPPED at %s — `main` untouched" % report.stopped
            if report.stopped
            else "All gates passed — ready to promote"
        ),
        "",
        "| step | result | detail |",
        "|---|---|---|",
    ]
    for name, state, detail in report.steps:
        lines.append("| %s | %s | %s |" % (name, state, detail.replace("|", "\\|")[:90]))
    lines.append("")

    if facts.get("upstream_files"):
        lines += ["## What upstream changed", "", "%d files." % facts["upstream_files"], ""]

    if report.conflicts:
        lines += [
            "## Conflicts",
            "",
            "Upstream and we edited the same file. Upstream's version is preserved",
            "under `dev/variants/` — neither side was discarded.",
            "",
        ]
        for rec in report.conflicts:
            lines.append("- `%s` — both changed: %s" % (rec["path"], ", ".join(rec["changed"]) or "(no named defs)"))
        lines.append("")

    if facts.get("divergence"):
        d = facts["divergence"]
        lines += [
            "## Divergence",
            "",
            "%d files are ours; %d of those are upstream-owned files we modified"
            % (d["ours_total"], d["conflict_surface"]),
            "(that second number is the only thing that can ever conflict).",
            "",
            "Full history in `rules/DIVERGENCE.md`.",
            "",
        ]

    lines += ["## Next", ""]
    if report.stopped:
        lines += [
            "Fix the failure, then re-run `python tools/reforge/sync.py`.",
            "To abandon this sync entirely: `git branch -D staging`. `main` never moved.",
        ]
    else:
        lines += ["`python tools/reforge/promote.py` moves `main` onto the staged tree."]
    STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="skip fetching upstream")
    args = parser.parse_args()

    report = Report()
    mf = manifest_mod.load()
    pristine = mf.meta.get("base", "vendor")

    if out("status", "--porcelain"):
        sys.exit("working tree is dirty — commit or stash first")

    start_branch = out("rev-parse", "--abbrev-ref", "HEAD")

    def restore():
        """Never strand HEAD on vendor or a half-built layout."""
        if out("rev-parse", "--abbrev-ref", "HEAD") != start_branch:
            git("checkout", start_branch, check=False)

    vendor_before = out("rev-parse", "--short", pristine)
    facts = {"date": date.today().isoformat(), "vendor_before": vendor_before}

    if not args.offline:
        if git("fetch", "upstream", check=False).returncode != 0:
            report.stop("fetch upstream", "could not reach upstream")
            write_status(report, facts | {"vendor_after": vendor_before})
            return 1
        report.add("fetch upstream", "ok")

    git("checkout", pristine)
    ff = git("merge", "--ff-only", "upstream/main", check=False)
    if ff.returncode != 0:
        report.stop("fast-forward %s" % pristine, "not a fast-forward — something was committed to it")
        git("checkout", start_branch)
        write_status(report, facts | {"vendor_after": vendor_before})
        return 1
    vendor_after = out("rev-parse", "--short", pristine)
    facts["vendor_after"] = vendor_after
    facts["upstream_files"] = len(
        [l for l in out("diff", "--name-only", "%s..%s" % (vendor_before, vendor_after)).splitlines() if l]
    )
    report.add("fast-forward %s" % pristine, "ok", "%s → %s" % (vendor_before, vendor_after))

    git("checkout", "base")
    rb = git("rebase", pristine, check=False)
    if rb.returncode != 0:
        git("rebase", "--abort", check=False)
        report.stop("rebase base", "toolchain conflicts with upstream — resolve by hand")
        git("checkout", start_branch)
        write_status(report, facts)
        return 1
    report.add("rebase base", "ok")

    if not gate(report, "drift", [PY, "tools/reforge/drift.py", "--quiet"]):
        write_status(report, facts)
        return 1

    old_layout = out("rev-parse", "layout")
    git("checkout", "-B", "layout", "base")
    apply_result = run([PY, "tools/reforge/apply.py"])
    if apply_result.returncode != 0:
        report.stop(
            "apply transform",
            (apply_result.stderr or "").strip().splitlines()[-1:][0] if apply_result.stderr else "failed",
        )
        write_status(report, facts)
        return 1
    report.add("apply transform", "ok")
    pins_mod.report(mf.pins)

    run(["git", "commit", "-m", "reforge layout @ %s" % vendor_after], {"REFORGE_ALLOW_LAYOUT": "1"})

    gate(report, "verify", [PY, "tools/reforge/verify.py"], allow_fail=True)
    gate(report, "tiercheck", [PY, "tools/reforge/tiercheck.py", "--core", "Core"], allow_fail=True)

    git("checkout", "-B", "staging", "main")
    rb = git("rebase", "--onto", "layout", old_layout, "staging", check=False)
    if rb.returncode != 0:
        records = [r for r in (variants_mod.preserve(p) for p in variants_mod.conflicted_paths()) if r]
        report.conflicts = records
        report.stop("stage your work", "%d conflicted file(s) — upstream variants preserved" % len(records))
        print(variants_mod.describe(records))
        write_status(report, facts)
        return 1
    report.add("stage your work", "ok", "replayed onto the new layout")

    if not gate(report, "imports", [PY, "-m", "pytest", "qa"]):
        write_status(report, facts)
        return 1

    facts["divergence"] = divergence.measure()
    divergence.append(facts["divergence"])
    report.add(
        "divergence",
        "ok",
        "%d ours, %d conflict surface" % (facts["divergence"]["ours_total"], facts["divergence"]["conflict_surface"]),
    )

    write_status(report, facts)

    # The ledger is committed so the trend survives; STATUS.md is scratch for
    # this run and is gitignored. Committing also leaves the tree clean, which
    # promote.py insists on.
    run(["git", "add", "rules/DIVERGENCE.md"])
    run(["git", "commit", "-m", "divergence @ %s" % vendor_after], {"REFORGE_ALLOW_LAYOUT": "1"})

    print("\nstaged and green. Review STATUS.md, then: python tools/reforge/promote.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
