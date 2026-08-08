# Module reload boundaries — when a client restart is actually required

Handover for wiring up hot reload. Written 2026-08-08 after a session where
edits to `Sources/marks_sources/*` were invisible across multiple script
relaunches and cost three test runs.

## TL;DR

The dependency-aware reloader this repo needs **already exists and is tested**
(`Core/py4gwcorelib_src/script_manager/loader.py`, commit `fe8213d6`,
`test/Core/py4gwcorelib_src/script_manager/test_loader.py`). It is simply not
wired into the launch path: `Widgets/Panels/ScriptManagementSystem.py:65`
launches scripts via the native
`PySystem.script_control.defer_stop_load_and_run(...)`
(`stubs/PySystem.pyi:338`), which re-execs **only the script file** against the
interpreter's existing `sys.modules`. Every module the script imports stays
cached. The likely fix is small: call `ScriptLoader.purge()` in `launch()`
before handing off to the native runner.

## How code actually loads (three different mechanisms)

1. **The script slot** (native). "Python environment reset / Script compiled
   successfully / main() found" in the log. The script source is exec'd fresh
   from disk on every relaunch — **script-file edits always take effect on
   relaunch**. Its `import` statements resolve through `sys.modules` — cached
   modules are NOT re-read.
2. **The widget manager**. Widget `.py` files are compiled and re-run by the
   manager; a widget-file edit takes effect on widget reload. The modules a
   widget imports are cached exactly like a script's.
3. **`import` of project packages** (`Core/`, `Sources/`, `HeroAI/`, …). Loaded
   once per client process, cached in `sys.modules` until the process dies.
   The embedded interpreter is initialized once per injection
   (`Py_InitializeFromConfig`, see the injection log) and never torn down by a
   script relaunch — "Python environment reset" resets the script slot, not the
   interpreter.

## Restart matrix — what an edit needs TODAY

| You edited | Takes effect on | Restart all clients? |
|---|---|---|
| `Scripts/**` (bot scripts, e.g. SoOTeamBT.py) | script relaunch from the SMS panel | no |
| a widget file in `Widgets/**` | widget disable/enable (manager recompile) | no |
| `Sources/**` (marks_sources: team_bt, brazier_route, …) | **nothing short of client restart** | **yes** (fixable — see below) |
| `HeroAI/**` | client restart | yes, effectively forever — see "protected" below |
| `Core/**` (incl. `Core/py4gwcorelib_src`, `routines_src`, `botting_tree_src`) | client restart | yes, **by design** — do not try to make this reloadable |
| `Py4GW.dll` / native `Py*` modules | client relaunch (re-injection) | yes, always (see `rebuild-dll` skill) |
| `stubs/**` | type-checking only | no runtime effect |

## Why `Sources/` is stuck today, and the fix

`ScriptLoader` in `Core/py4gwcorelib_src/script_manager/loader.py`:

- `RELOAD_ROOTS = ("Sources", "HeroAI", "Bots", "bot_factory")` — eligible for
  dropping from `sys.modules` (`loader.py:19`).
- `PROTECTED_ROOTS = ("Core", "Py4GW_widget_manager", "Widgets", "Py4GW",
  "PySystem")` — never dropped (`loader.py:22`). `Core` is imported by ~620
  call sites and every widget; dropping it mid-session leaves live widgets
  holding a half-replaced library.
- `shared_with_widgets()` (`loader.py:47`) — AST-scans widget files and
  protects any reload-root module a widget imports, because the widget would
  keep the old module object while the script gets a fresh one. Currently that
  set is `Sources.marks_sources.item_kinds` and
  `Sources.marks_sources.item_naming` (imported by `Widgets/Items/
  InventoryLite.py` and `TeamInventoryViewer.py`), plus most of `HeroAI.*`
  (imported by the HeroAI widget — which is why HeroAI edits stay
  restart-only even after this fix).
- `purge()` (`loader.py:107`) drops everything eligible; `load()` does
  purge-then-import. **Nothing calls either in the launch path.**

`ScriptManagementSystem.launch()` (`Widgets/Panels/ScriptManagementSystem.py:57`)
goes straight to the native runner. The interpreter then re-execs the script,
whose `import Sources.marks_sources.team_bt` hits the stale cache.

### Work items

1. ~~**Wire the purge** into `launch()`~~ — **DONE 2026-08-08**: `launch()`
   calls `loader.purge()` immediately before `defer_stop_load_and_run` and
   logs the dropped module names (the log line is proof-of-reload when
   debugging). The native runner's re-exec then re-imports everything fresh.
   `purge()` is independent of `load()` — the native slot still owns
   execution. With this live, **the `Sources/**` row of the restart matrix
   becomes "script relaunch"**.
2. ~~**Refresh the protected set on Rescan**~~ — **DONE 2026-08-08**: `scan()`
   calls `loader.refresh_protected()` so a widget that starts importing a new
   reload-root module is noticed.
3. **Add a "Purge cached modules" button** to the SMS panel showing
   `purgeable()` as a preview — the manual escape hatch for anything the
   automation misses. (Open.)
4. **Leave alone:** `Core`, the native modules, and the widget-shared
   protection semantics. The protection is load-bearing — e.g.
   `Sources.marks_sources.item_naming` holds `NAME_CACHE` that live widgets
   depend on; dropping it hands the script a fresh empty cache while the
   widget keeps the old one.

### Hazards for whoever wires this

- Old objects survive a purge: removing a module from `sys.modules` does not
  touch instances the previous (stopped) script created. That is fine for the
  stop-then-launch flow, but a purge without the stop first would split module
  identity under a running bot.
- `isinstance` checks across the boundary: a class from the purged module is
  not the "same" class after re-import. Anything stashing class instances in
  cross-cycle homes (blackboards survive within a run, GLOBAL_CACHE,
  `HeroAI.globals`) and type-checking them later would break. Today's
  marks_sources modules keep no such state (team_turns/brazier_route are pure;
  team_bt/gadget_interact hold state only inside node closures).

## The bigger hammer: DLL eject + re-inject (the "relaunch the Python shell" concept)

Unchained's "relaunch the python shell" is a full DLL eject:
`Py4GW-unchained-cpp/src/dllmain.cpp:302` `Terminate(unload_module=true)` →
`py::finalize_interpreter()` → `FreeLibraryAndExitThread`, then re-injection
into the still-running client. A fresh DLL load means a fresh interpreter and
an empty `sys.modules` — **every row of the restart matrix above except "none
needed" collapses to eject + re-inject**, including `Core/` and a rebuilt DLL,
without killing the client or re-logging.

**Our native already has the eject half.** `../Py4GW_Reforged_Native/src/
Py4GW.cpp:490` `Py4GW_RequestShutdown()` → `Py4GW_Shutdown()` (stops the
script, flushes Settings/JsonFactory, tears down ImGui, listeners, GW hooks,
destroys the Python runtime and both shared-memory regions) → the runtime
thread exits via `FreeLibraryAndExitThread` (`Py4GW.cpp:511`). It is wired to
an ImGui shutdown callback (`imgui_manager.cpp:501`), so it is likely already
reachable from the native UI.

What is missing / unverified:

1. **The re-inject half** — an injector that attaches to a *running* GW
   process. The external launcher is the natural home (it is one of the
   sanctioned non-injected processes). If it only injects at client launch,
   that is the piece to build.
2. **A proven clean eject under the locally patched DLL** — the game-thread
   queue patch touches teardown-adjacent code. Test on one logged-in client in
   an outpost: eject, check the injection log, re-inject, confirm widgets
   return. Watch the multibox shared-memory creator handoff when the ejected
   client was the region creator.
3. **Repeat-cycle hygiene** — Unchained's comments show they fought dangling
   VEH filters from unloaded modules; our CrashHandler has
   `RtlDllShutdownInProgress` handling, but nobody has cycled it dozens of
   times in one session.

The two fixes are complementary, not competing: the `ScriptLoader.purge()`
wiring is seconds-fast and right for Sources-edit iteration; eject+re-inject
is the complete reset for `Core/`, `HeroAI/`, and DLL changes. Per RS-008 the
concept is re-implemented in our native idioms, never copied, and nothing
goes back upstream.

## Evidence (2026-08-08 session)

- `brazier_route.finished` was added on disk at ~00:20; at 00:33:20 the running
  client raised `AttributeError: module 'Sources.marks_sources.brazier_route'
  has no attribute 'finished'` from a freshly relaunched script whose line
  numbers matched the NEW script file — new script, stale import.
- `team_bt.walk` gained a retry wrapper in the same window; at 00:41:56, after
  two more relaunch cycles, a single Move timeout still killed the planner —
  the retry on disk had never loaded.
