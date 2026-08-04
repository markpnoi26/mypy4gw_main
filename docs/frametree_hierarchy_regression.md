# FrameTree — `get_frame_hierarchy()` row layout is unconfirmed

**Status: OPEN. Reverted, working, slower. Needs one live reading to close.**

Self-contained handover. Nothing here depends on the conversation that produced it.
Unrelated to the fight-zone / health-retreat work in `FIGHT_ZONE_BEHAVIOR.md`.

---

## 1. What happened

Commit `3fa2c5bb` replaced `FrameTree.rebuild()`'s per-frame `UIFrame` read with a
single `get_frame_hierarchy()` call, unpacking each row **by position** as
`(frame_id, parent_id, code, hash)`.

Live result: the HeroAI party overlay and the Party Search accounts tab both went
blank — reported as "accounts are not showing up". Reverting `rebuild()` to the
by-name read brought them straight back, so the one-call path was definitely
producing a wrong tree.

That layout is asserted **nowhere**. It was introduced by the commit that started
depending on it.

## 2. Why it broke exactly those two things

`FrameId` paths are an **anchor hash** plus a **tail of child-offset codes**.
`Frame._resolve()` (`Core/FrameTree/frame.py:920`) looks the anchor up, then walks
the tail. At the time of the outage the two steps were not equally guarded:

- `FrameTree.anchor_ids()` (`frame.py:469`) — misses in `_by_hash` fall through to
  `get_frame_id_by_hash()`. **Guarded.**
- `FrameTree.child_of()` (`frame.py:490`) — read `_children` and nothing else.
  A miss returned `None`, and `None` means "no such child". **Unguarded — since
  fixed, see §4.**

So a mis-folded tree could not break the anchor, and killed every tail walk at the
first code. Anything reaching the engine directly — `child_native`, `by_hash`,
`child_with_hash`, `child_path_native` — kept working, which is why it presented
as an accounts bug rather than a UI one.

It was **silent** because `.exists == False` is also the normal reading for "that
window is closed". Both consumers treat it as such and simply decline to draw:

- `HeroAI/ui.py:2663` — `if not Frame.party_list().exists: return`
- `HeroAI/ui.py:2877` — `if not party_search_id.exists: party_search = None`

## 3. Proven vs assumed

**Proven.** The folded tree was wrong; reverting fixed it. The failing step reads
only `_children`, which `fold_hierarchy` builds from columns 1 and 2. Therefore
**either column 1 is not `parent_id`, or column 2 is not `child_offset_id`**, or
the row *set* differs from `get_frame_array()` so a parent in the chain is absent.

**Not known.** Which of those. And the **hash column is completely untested** — the
anchor fallback masked it, so it may have been wrong all along without symptom.

## 4. Current code state

- `rebuild()` (`frame.py:355`) is back on the per-frame `UIFrame` read. Body
  verified identical to pre-`3fa2c5bb`, comments aside — including
  `order.append(fid)` **before** the read, so a frame present in the array but
  unreadable this tick still counts as `known()`.
- `fold_hierarchy()` (`frame.py:174`) is kept and still tested. The fold is
  correct; only its input was in doubt. Its docstring says it is not currently
  wired, so nobody deletes it as dead or re-enables it without doing §6.
- `FrameTree.child_of()` (`frame.py:490`) now carries the guard it lacked, falling
  back to `get_child_frame_by_frame_id()` (`stubs/PyUIManager.pyi:212`) on a
  snapshot miss — the same shape as `anchor_ids()`. **This does not answer the
  column question**; it removes the silence. A wrong tree now degrades to slow
  and correct rather than blank, so re-enabling the fast path can no longer fail
  the way it did. Cost is one native call per unresolved path per tick, since a
  genuinely closed window also misses. `children_at()` is deliberately left
  unguarded — the native call answers one child, not colliding siblings.
- `FrameTree.hierarchy()` (`frame.py:612`) still exposes the raw call.
- Cost of the revert: ~370 object constructions and ~1100 getattrs per tick,
  measured at 4.4ms of a 5.8ms frame, billed to whichever widget touches the tree
  first that tick. That is the prize for closing this.

## 5. Offline sources already exhausted — do not redo

None of these give the column meanings:

| Source | What it has |
|---|---|
| `stubs/PyUIManager.pyi` | `get_frame_hierarchy() -> List[Tuple[int, int, int, int]]` — arity only |
| `docs/demo_replacement/reengineer/R2_binding_method_inventory.md:2222` | the same signature again |
| `docs/demo_replacement/06_cpp_bindings_gameplay.md` | names it in a list of ~20 calls |
| `docs/RE/*` | frame material there is FrameProc / control creation, unrelated |
| `docs/py_ui_function_catalog.txt:168` | maps it to an RE test not present in this tree |
| `docs/FrameTree_Design.md:156` | lists it as a Tier-2 binding, no layout |

`Py4GW.dll` is built by a separate C++ project **not in this workspace**, so there
is nothing to disassemble. This is only answerable from a live client.

## 6. How to get the answer

**Existing tool, no code needed.**
`Scripts/py4gw-devtools/scripts/Debug/Guild Wars/Frame_Showcase.py` → UIManager API
tester tab → *Tree Walkers* section → **GetFrameHierarchy** button (line 783). It
calls `LiveTree.hierarchy` and renders the raw rows.

Caveat: `_exec` does `str(result)` and draws it as one long line, so a ~370-row
list runs off screen. The first tuple or two is all that is needed.

**The magnitude heuristic is decisive on its own** — the three quantities look
nothing alike:

| Quantity | Shape |
|---|---|
| frame id / parent id | ordinary integers; recur as ids in other rows |
| child offset code | tiny — 0, 1, 2, 8 |
| frame hash | huge 32-bit — `PARTY_WINDOW_HASH` is `3332025202` (`HeroAI/constants.py:34`) |

Whichever slot holds the enormous numbers is the hash. Last slot → the assumed
layout had hash right and the break is parent/code. Third slot → hash and code are
swapped, which is exactly the shape that produces §2.

**Tidier alternative**, pasted into the console:

```python
import PyUIManager
rows = PyUIManager.UIManager.get_frame_hierarchy()
print("rows", len(rows), "frame_array", len(PyUIManager.UIManager.get_frame_array()))
for fid, a, b, c in rows[:5]:
    f = PyUIManager.UIFrame(fid)
    print(fid, (a, b, c), "| parent", f.parent_id, "offset", f.child_offset_id, "hash", f.frame_hash)
```

Differing counts, or `a`/`b` not matching `parent_id`/`child_offset_id`, is the
answer. **Capture this output verbatim** — it is also the test fixture (§8).

## 7. The fix, once the layout is known

1. Point `rebuild()` back at `fold_hierarchy(PyUIManager.UIManager.get_frame_hierarchy())`.
2. Fix the unpack order inside `fold_hierarchy` to match the real columns.
3. Handle the row-set difference if there is one. The old loop put **every** id from
   `get_frame_array()` into `order`, even when the `UIFrame` read failed, and
   `known()` is `frame_id in self._parent`. If the hierarchy dump omits frames,
   that semantic has to be preserved some other way or `known()` silently narrows.
4. Re-verify in game: party overlay **and** Party Search accounts tab.

## 8. Test requirements — the part that let this ship

`test/Core/FrameTree/test_frame.py` fixtures are **hand-written in the assumed
layout**. The suite passed for the entire outage. A fixture that encodes the same
assumption as the code can only ever confirm it.

Before re-enabling, replace `ROOT`/`CHILD_A`/`CHILD_B`/`TWIN` with rows captured
from the §6 dump, so the layout is evidence rather than a premise. The file's
docstring already says this.

## 9. Environment gotchas for whoever picks this up

- **`import Core` redirects `sys.stdout`/`sys.stderr`** into the Py4GW console.
  Any offline script that imports `Core.FrameTree` (or anything pulling `Core`)
  goes silent — prints vanish, exit code 0, no traceback. Restore with
  `sys.stdout = sys.__stdout__` after the imports.
- Test baselines to hold: `1 failed, 778 passed, 282 deselected, 1 xfailed` at the
  time of `3fa2c5bb`, and the leaf run `-m leaf` at `7 failed, 244 passed,
  31 skipped`. Both known reds are deliberate — see `.claude/skills/test-harness.md`.
  (Counts drift upward as suites are added; the shape is what matters.)
- `isort --check-only` already fails on `Core/FrameTree/frame.py` in the committed
  tree, before any of this. Pre-existing, unrelated, and not worth fixing inside a
  behaviour change to a startup-sensitive file.
- Run with `./.venv/Scripts/python.exe`. Bare `python` is 3.11 in PowerShell and
  absent from Bash.
