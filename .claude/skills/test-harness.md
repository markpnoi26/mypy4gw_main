---
description: How to test anything in this tree without a game client — the test/ mirror tree, its native-stub harness, and the gate to run before calling a build stable. Invoke when adding or changing code under Core/, HeroAI/, Widgets/ or Scripts/, when a change could affect module reload, or when asked whether the tree is stable.
---

# Test harness

This tree **can** be tested offline. `Py4GW.dll` is absent, but `test/nativestub.py`
serves `stubs/*.pyi` as importable stand-ins, so module bodies execute and
behaviour can be exercised without a client.

There is no CI. Nothing runs these unless you do.

> Older notes claim "no pytest config, verify with targeted scripts". That is
> stale. `pyproject.toml` configures pytest, `test/conftest.py` installs the stub
> loader, and one bare `pytest` runs everything.

---

## Where a test goes

`test/` **mirrors the source tree**. A suite for `HeroAI/fight/zone.py` lives at
`test/HeroAI/fight/test_zone.py`; one for `Core/FrameTree/frame.py` at
`test/Core/FrameTree/test_frame.py`. Add directories as you need them — no
`__init__.py`, ever, and nothing to register.

Singular `test/`, not `tests/`: upstream ships a plural `tests/` that the manifest
moves to `dev/tests/imgui/`.

Three consequences worth knowing:

- **`conftest.py` installs the stubs before collection**, so a suite just writes
  `from HeroAI.fight import zone`. No `sys.path` bootstrap, no
  `nativestub.install()` — those lines are now wrong, not merely redundant.
- **`--import-mode=importlib`** is set in `addopts`. It is what lets the same
  basename appear in two packages, and it keeps each test's folder out of
  `sys.path`, where a `test/Core/` would shadow the real `Core/`.
- **`test/` is deliberately off `pythonpath`**, since a top-level `test` package
  would shadow the stdlib's. `conftest.py` adds itself, which is what keeps
  `nativestub`, `pathload` and `test_imports` importable.

Repo-wide gates stay at the root — `test_imports.py`, `breakage.py`,
`nativestub.py`, `pathload.py`. They belong to no single source file.

For a module that is **stdlib-only by contract**, load it by path so the test
keeps proving that contract rather than quietly relying on the package import:

```python
import pathload

loader = pathload.load("Core/py4gwcorelib_src/script_manager/loader.py")
```

`Launcher/` is the deliberate exception: a separate app rooted at `Launcher/`,
whose suites import `launcher_core.*` directly and stay where they are.

---

## The gate

Two commands before saying a change is stable. Whole thing takes ~3s.

```bash
# 1. Everything — imports and behaviour, one run
./.venv/Scripts/python.exe -m pytest

# 2. Formatting, on changed files only
./.venv/Scripts/python.exe -m black --line-length 120 --skip-string-normalization --check <files>
./.venv/Scripts/python.exe -m isort --force-single-line --check-only <files>
```

### Known-good baseline — memorise this line

```
1 failed, 778 passed, 282 deselected, 1 xfailed
```

**Both known reds are deliberate. Neither is a regression.**

- The xfail is `HeroAI.ui`, a circular import broken in pristine upstream too.
  Marked `strict=True`, so if upstream fixes it the run reports XPASS and the
  entry comes off the list.
- The failure is `test_every_script_parses`: most discovered scripts carry no
  `__script__` block. Left red on purpose — it is an accurate report of a
  metadata gap, and whether `__script__` becomes mandatory is a decision, not a
  cleanup. The count in the diff moves as scripts are added; the failure itself
  staying single is what matters.

Any *other* movement in those numbers is yours. Do not "fix" either known red
without checking whether the decision behind it has changed.

The 282 deselected are tier-4 widget and script leaf loads. `pyproject.toml` sets
`addopts = "-q -m 'not leaf'"`, so they are off by default; run them explicitly
with `-m leaf` before anything that touches `Widgets/` or `Scripts/`. That run has
its own baseline — **7 failed, 244 passed, 31 skipped** — where the 7 are the
protected-pack breaks listed in `rules/BREAKAGE.md` and the 31 are RS-004
deprecations. `test/breakage.py` derives the same 38 without pytest, so the two
corroborate each other.

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
on the first reload, in the middle of a run. `test/test_imports.py` is the only
gate that executes module bodies, which makes it the reload gate too.

If you add a module under a reload root, it is covered automatically — the test
rglobs the package. If you add a *new top-level package*, add it to
`test_imports.py` alongside `test_heroai_module_imports`.

---

## Two ways to reach the code under test

Both live in the mirror; the choice is only how the target is imported.

### A. `pathload`, for a stdlib-only contract

Pattern: `test/Core/py4gwcorelib_src/script_manager/test_discovery.py`. The
target is loaded by file path, so `Core/__init__.py` never runs and the test
fails the day someone gives that module a framework import.

```python
import pathload

discovery = pathload.load("Core/py4gwcorelib_src/script_manager/discovery.py")
```

`unittest.TestCase` files work unchanged — pytest collects them, so there is no
`unittest discover` invocation to remember any more.

### B. A plain package import

Pattern: `test/HeroAI/fight/test_zone.py`. For anything that legitimately needs
the framework. The stubs are already installed, so this is just:

```python
from HeroAI.fight import zone
```

**Prefer A when the module allows it** — it proves something B cannot.

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

Where the invariant is in a **function** rather than a config field, swap the
function instead — reimplement it the wrong way and confirm the test raises.
Patch it on whichever object the test actually reads: the source module when the
test calls `module.fn()`, the test module itself when it did `from x import fn`.
The strongest form is to reimplement the *original bug*, so the test is shown to
catch the thing it was written for. All ten mutations of the fight and
`marks_sources` suites were checked this way.

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

Put them in the mirror, not in a scratchpad. A suite in
`C:\cygwin64\tmp\...\scratchpad` is gone at the end of the session and cannot be
attached to a PR — which is exactly how the fight-zone suites ended up
unshippable once already.

The mutation probe is the one thing that belongs in the scratchpad: it is
scaffolding for writing the suite, not part of it.

Worked examples, in rough order of how much they teach:

| Suite | Shape it demonstrates |
|---|---|
| `test/HeroAI/fight/test_zone.py` | config/inputs dataclasses, precedence, degenerate shapes |
| `test/HeroAI/fight/test_breadcrumbs.py` | pure geometry, explicit config instead of the module default |
| `test/Core/FrameTree/test_registry.py` | consistency across generated tables — the bad-regeneration class |
| `test/Sources/marks_sources/test_item_naming.py` | pure string ladders, and asserting a table's own shape |
