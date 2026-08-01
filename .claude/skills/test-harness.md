---
description: How to test anything in this tree without a game client — the qa/ native-stub harness, where behavioural tests live, and the gate to run before calling a build stable. Invoke when adding or changing code under Core/, HeroAI/, Widgets/ or Scripts/, when a change could affect module reload, or when asked whether the tree is stable.
---

# Test harness

This tree **can** be tested offline. `Py4GW.dll` is absent, but `qa/nativestub.py`
serves `stubs/*.pyi` as importable stand-ins, so module bodies execute and
behaviour can be exercised without a client.

There is no CI. Nothing runs these unless you do.

> Older notes claim "no pytest config, verify with targeted scripts". That is
> stale. `pyproject.toml` configures pytest, `qa/conftest.py` installs the stub
> loader, and 570 import cases already pass.

---

## The gate

Run all three before saying a change is stable. Whole thing takes ~5s.

```bash
# 1. Everything still imports
./.venv/Scripts/python.exe -m pytest qa/

# 2. Behavioural suites
./.venv/Scripts/python.exe -m pytest HeroAI/ Core/

# 3. Formatting, on changed files only
./.venv/Scripts/python.exe -m black --line-length 120 --skip-string-normalization --check <files>
./.venv/Scripts/python.exe -m isort --force-single-line --check-only <files>
```

### Known-good baseline — memorise these two lines

```
qa/           570 passed, 287 deselected, 1 xfailed
HeroAI/ Core/   1 failed,  48 passed
```

**Both known reds are deliberate. Neither is a regression.**

- The xfail is `HeroAI.ui`, a circular import broken in pristine upstream too.
  Marked `strict=True`, so if upstream fixes it the run reports XPASS and the
  entry comes off the list.
- The failure is `test_every_script_parses`: 180 of 181 discovered scripts carry
  no `__script__` block. Left red on purpose — it is an accurate report of a
  metadata gap, and whether `__script__` becomes mandatory is a decision, not a
  cleanup.

Any *other* movement in those numbers is yours. Do not "fix" either known red
without checking whether the decision behind it has changed.

The 287 deselected are tier-4 widget and script leaf loads. `pyproject.toml` sets
`addopts = "-q -m 'not leaf'"`, so they are off by default; run them explicitly
with `-m leaf` before anything that touches `Widgets/` or `Scripts/`.

---

## Why the stub harness matters for module reload

`script_manager/loader.py` drops modules under `RELOAD_ROOTS` —
`Sources`, `HeroAI`, `Bots`, `bot_factory` — from `sys.modules` so a script
reload picks up edits to its supporting code. `PROTECTED_ROOTS`
(`Core`, `Widgets`, `Py4GW`, `PySystem`, `Py4GW_widget_manager`) are never
dropped, because a widget holding half-replaced library objects is worse than a
stale one.

The consequence: **anything under `HeroAI/` gets re-imported mid-session.** An
import-time error that a fresh start would surface immediately instead surfaces
on the first reload, in the middle of a run. `qa/test_imports.py` is the only
gate that executes module bodies, which makes it the reload gate too.

If you add a module under a reload root, it is covered automatically — the test
rglobs the package. If you add a *new top-level package*, add it to
`test_imports.py` alongside `test_heroai_module_imports`.

---

## Two test shapes, and which to use

### A. In-package `unittest`, loaded by file path

Pattern: `Core/py4gwcorelib_src/script_manager/test_discovery.py`.

For code that is **stdlib-only by contract** and must stay importable outside the
client. Loads its target by file path rather than package import, so it never
drags `Core/__init__.py` in:

```python
import importlib.util, os
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("discovery", os.path.join(HERE, "discovery.py"))
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)
```

Run with `-s` and `-t` both set to the module's own folder, or discovery walks up
into `Core/__init__.py`:

```bash
./.venv/Scripts/python.exe -m unittest discover \
    -s Core/py4gwcorelib_src/script_manager \
    -t Core/py4gwcorelib_src/script_manager
```

### B. pytest under `qa/`, or beside the code with the stub harness

For anything that needs real framework imports. `qa/conftest.py` calls
`nativestub.install()`; a test elsewhere must install it itself:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[N] / "qa"))
import nativestub
nativestub.install()
```

**Prefer A when the module allows it.** It is faster, has no import-order
hazards, and proves the stdlib-only contract still holds.

---

## Writing behavioural tests here

The framework is unavailable and the game is unobservable, so tests are built on
**explicit input structs, not mocks of the client**. Most subsystems in this tree
already take a config dataclass and an inputs dataclass for exactly this reason —
`ZoneConfig`/`ZoneInputs`, `PlacementConfig`, `EscapeConfig`. If the thing you
are testing does not, that is usually the bug: pull the readings out into a
struct the caller fills, and the test becomes possible.

Rules that keep these useful:

- **Assert the intent, not the constant.** `abs(floor + inp.midline_depth) < 0.5`
  survives a formation change; `abs(floor + 320) < 0.5` has to be edited every
  time and teaches nothing when it breaks.
- **Name the invariant in the assertion text.** When it fails months later the
  message is the whole explanation.
- **Test the ordering, not just the outcome.** Most real bugs here were
  precedence bugs — the escalation clamp, the dwell-vs-breach precedence, the
  forced-re-aim-before-geometry check. An outcome test passes on a correct answer
  reached by a wrong route.
- **Cover the degenerate shape.** The compressed-formation case is what caught the
  inverted escalation. Zero enemies, one enemy, and a formation half the default
  depth are where the sign errors live.
- **Fixtures need every field the code path reads.** A missing `retreat_distance`
  silently returns a zero ceiling and the test passes while asserting nothing.

## Prove the test can fail

A test that has never failed has not been shown to test anything. Before
committing a suite, mutate each invariant it claims to cover and confirm the
matching test raises. Config here is mutable dataclass instances, so this is
cheap — no source edits:

```python
def probe(label, field, bad, test):
    good = getattr(zone.ZONE_CFG, field)
    setattr(zone.ZONE_CFG, field, bad)
    try:
        test()
        print("NOT CAUGHT", label)
    except AssertionError:
        print("caught", label)
    finally:
        setattr(zone.ZONE_CFG, field, good)
```

This is not ceremony. Two real defects in `test_zone.py` were found this way and
would not have been found by reading it:

- A test asserting `hold == CFG.advance_hold_ms` reads the config *at assert
  time*, so it moves with any change and can never fail. The invariant was
  flatness across blob sizes, so the fix was to compare sizes against each other.
- A one-sided boundary test caught the ring being *widened* but not *narrowed* —
  and narrowing is the mistake that actually gets made. Both sides need a case.

Mutate in both directions. A "NOT CAUGHT" is either a weak test or a coverage
gap; decide which before moving on.

---

## What this harness does not cover

Say so plainly rather than implying stability you have not shown.

- **Client state lag.** The real failure mode of this codebase — the client
  reports the previous value for several frames after an action. No offline test
  reproduces it. See `.claude/context/runtime-behaviour.md`.
- **Native behaviour.** Stubs make imports work; every body is `...`. A test that
  appears to exercise a `Py*` call is exercising `Anything()`.
- **Frame tree matching.** `template_type` / `child_offset_id` values are read
  empirically from a live client.
- **Anything requiring a map, agents, or the ACTION queue.**

"All tests pass" means the tree loads and the pure logic is right. It does not
mean it works in game.

---

## When adding tests to existing work

Put them where the code is, not in a scratchpad. A suite in
`C:\cygwin64\tmp\...\scratchpad` is gone at the end of the session and cannot be
attached to a PR — which is exactly how the fight-zone suites ended up
unshippable.

Worked example: `HeroAI/fight/test_zone.py`.
