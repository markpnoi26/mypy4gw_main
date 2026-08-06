---
name: rebuild-dll
description: Rebuild and deploy the patched Py4GW.dll when upstream ships a new DLL or native source changes. Guards the local native patches (game-thread deadlock fix) from being silently reverted by upstream DLL updates.
---

# Rebuilding Py4GW.dll on top of upstream

## Why this exists

`Py4GW.dll` in this repo is **our build** of `Py4GW_Reforged_Native` (fork of
apoguita's), not upstream's shipped binary. Upstream ships prebuilt DLLs
("updating dll" commits) that do not contain our patches. Any path that brings
upstream's DLL into this repo — vendor sync, manual copy, a merge taking theirs
— silently reverts the patches, and their bugs return with no code change
anywhere on our side. The reverse hazard is building from a stale fork and
losing fixes upstream's shipped DLL already had. The rule that resolves both:
**always take upstream's latest source, verify our patches on top, rebuild,
deploy our build.** Never run upstream's binary; never build without merging
upstream first.

## Standing local patches — verify present after every upstream merge

Keep this list short and current. A patch worth keeping is worth reporting
upstream so it can eventually come off this list.

1. **Game-thread GIL/mutex deadlock fix** (2026-08-06) —
   `src/GW/game_thread/game_thread.cpp` + `game_thread_methods.cpp`:
   - `CallFunctions` swaps the singleshot queue out (and copies `g_callbacks`)
     under `g_mutex`, then executes everything **outside** the lock.
   - `Enqueue`'s run-inline check and `IsInGameThread()` are lock-free:
     `GetCurrentThreadId() == g_game_thread_id && g_in_drain` (both atomics).
     **Both conditions are load-bearing.** Thread id alone shipped briefly and
     crashed visible clients on zone-out with GW's "Model closed while in
     render queue" assertion: GW's game thread IS its render thread, so
     draw-loop Python enqueueing mid-frame ran actions inline outside the
     LeaveGameThread sync point and mutated models still queued for render.
   - The invariant, if upstream rewrites these files and the patch must be
     re-derived: *no callable ever executes while `g_mutex` is held; no
     GIL-holding thread ever blocks on `g_mutex`; and nothing runs inline
     outside the LeaveGameThread drain window.*
   - Symptom when lost: minimised clients go "Not Responding" forever (ghost
     window showing the last frame), several per run, clustered at map loads.
     Details: memory `reference_minimized_client_loops`.

## Workflow

1. **Sync the fork.** In `Py4GW_Reforged_Native` (branch `personal`):
   `git fetch upstream && git merge upstream/main`. Read what changed —
   especially whether upstream touched any file in the patch list above.
2. **Verify the patches survived the merge.** If upstream rewrote a patched
   file, re-derive from the invariant, don't trust the merge resolution.
3. **Build.** No standalone cmake on this machine — use MSBuild on the
   already-generated solution:
   ```
   "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe" build\Py4GW.sln /p:Configuration=RelWithDebInfo /p:Platform=Win32 /m
   ```
   Output lands in the native repo root: `Py4GW.dll` + `Py4GW.pdb`.
   Win32/RelWithDebInfo only — the injected target is 32-bit.
4. **Deploy to `mypy4gw_main`.** Running clients hold the DLL file locked —
   rename, never overwrite:
   - `mv Py4GW.dll Py4GW.dll.upstream-<date>` (replace the previous backup)
   - copy the new `Py4GW.dll` **and** `Py4GW.pdb` from the native repo root
     (the PDB is what makes future crash dumps symbolize)
   - **sync `offsets/`**: `diff <(ls <native>/offsets) <(ls offsets)` and copy
     any file the native repo has that this repo lacks. The DLL loads resolver
     definitions from `offsets/*.json` at injection time; a new native module
     without its JSON aborts ALL of init with
     `Resolver definition not found ... policy=halt` →
     "Guild Wars hook initialization failed" in `<gw dir>\Py4GW_injection_log.txt`,
     which reads as "not injecting". This bit on 2026-08-06 with upstream's new
     `ctos` module (`offsets/ctos.json`) — it broke upstream's own stock DLL
     here too, not just our build.
5. **Restart every client.** Injected DLLs only change on relaunch; running
   clients keep the renamed old file.
6. **Confirm.** Loop Census's hang watchdog (`json/<account>/HANG.json`) stays
   quiet; a `stubs/` mismatch is the signal the DLL's surface moved further
   than expected.

## Guardrails

- `Py4GW.dll` is git-tracked in this repo. After a deploy, the tracked file IS
  our build; `git checkout -- Py4GW.dll` restores whatever the branch carries.
  If a vendor sync or upstream pull replaces it with their binary, the answer
  is this workflow — not accepting the file.
- Upstream's shipped DLL can be built from source they haven't pushed yet. If
  behavior differs from merged source in ways `stubs/` or the harness catch,
  prefer their published source + our patches and note the skew.
- The fork's `personal` branch carries the patches; upstream merges are
  commits, patch changes are commits — keep them separable so "what do we
  carry" stays answerable with `git log upstream/main..personal -- src/`.
