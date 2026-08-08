# Capability gaps between the fork lines

Deep-read snapshot **2026-08-06** — the companion to [repo-overlap.md](repo-overlap.md)
(which is name-level; this is what each side can *do* that the other cannot).
Sources: module-level reads of both Python libraries, both C++ trees, and a
stub-tree diff. "A" = this tree / Reforged line; "B" = Py4GW-Unchained line.

## The shape of it

The two libraries grew in **opposite directions** from the shared ancestor.
B deepened *bot and game automation*: economy/logistics BT nodes, party
assembly, a speedclear framework, hex tracking, hero smart-cast, and ~250 more
route bots. A deepened *infrastructure*: FrameTree, a native widget toolkit,
listeners, persistence, the BT combat engine, the fight/ positional layer, and
a large RE corpus. Sizes tell it: B's `routines_src` 36.9k LOC vs A's 23.9k;
A's `py4gwcorelib_src` 29.8k vs B's 11.0k.

The DLLs encode **opposite philosophies**. A exposes typed, game-thread-safe
high-level *verbs* — `travel()`, `add_hero()`, `enable("auto_open_locked_chest")` —
plus an infra layer (settings, JSON, profiler, script control). B exposes raw
*primitives* — ~192 readable agent struct fields, raw context pointers
(`PyPointers`), raw packet send (`PyCtoS`), the UI message bus — and pushes
logic up into Python via ctypes. Net modules: A 43 vs B 33, 25 shared, three
pairs are renames (`PyDXOverlay`↔`Py2DRenderer`, `PyTrade`↔`PyTrading`,
`PyAgentEvents`↔`PyCombatEvents`).

## Corrections to "Unchained is ahead"

- A already has **all 37** of B's `UI_RE/` experiments (at `dev/ui-re/`, plus 4
  more and captured logs), the **bridge daemon + CLI + client widget**
  (`dev/bridge/`, `Scripts/py4gw-devtools/`), and a **superset of
  `bot_factory/`** (B ships only the scanner; A also has `Bot_Factory.py`).
- Many "B-only" widgets exist in A parked under `Scripts/orphaned-widgets/`
  and `Scripts/py4gw-devtools/` — the gap is packaging, not capability.
- **Stubs lie in places.** A's `PyUIManager.pyi` is stale — the DLL has the
  window-creation natives `GWUI.py` calls; B ships `PyMouse` and
  `PyNameObfuscator` with no stub at all. Trust `PYBIND11_EMBEDDED_MODULE`
  greps over stub counts.
- Shared ancestry still holds where it matters: `PyCallback`, `Py4GW.UI`,
  `Py4GW.Console` are byte-identical; `Skillbar.py` is identical; the
  `Py4GWcorelib` grab-bag (Timer, FSM, ActionQueue, BehaviorTree,
  MultiThreading) has near-zero public divergence.

## B has, A lacks — Python library (ranked)

1. **BT economy/logistics nodes** — `BT.Items` +5.1k LOC (buy/sell/restock/
   deposit/withdraw/craft/exchange/equip/weapon-set/bag-sort), plus
   `BT.AutoInventory` (2.8k), `BT.MultiboxInventory` (1.1k),
   `BT.ConsetCrafting` (0.8k). A's `BT.Items` covers loot+salvage+identify only.
2. **BT party assembly** — hero/henchman invite/setup/kick, template load,
   wipe recovery, resign-and-return (`BT.Party` 2,145 vs 780).
3. **Speedclear framework** (`speedclear_src/`, 8.5k) — role auto-detection by
   skill-bar signature, multi-account barriers, parallel service trees,
   respawn/retry state machine, and a 4.2k-LOC predictive **body-block
   avoidance sentinel** (velocity prediction, hazard polygons).
4. **Cross-client hex-identity tracking** (`hex_tracker_src/`, 1.9k) — each
   client records its own casts into a dedicated shared-memory block; durations
   and AoE derived from the skill DB. Reconstructs data the game never gives.
5. **Layer-aware targeting** (`combat_layer.py`, 36 lines + ~24 call sites) —
   Z-tolerance filter so the party stops attacking through bridges. zplane is
   deliberately ignored (it reads 0 on real multi-level maps).
6. **Hero (NPC) smart-cast** (`hero_skills.py`, 1.9k) — ST/SoS/BiP/Splinter/
   Honor/hex+condition cleanse/interrupt/Panic for leader-owned heroes.
7. **Pet smart combat** — Fight/Guard/Heel sync inside `combat.py` + native
   pet-commander frame outline.
8. **BottingTree Steps + stats** — restart a planner at any named checkpoint
   without replaying (AST-extracted catalog, 1.3k), run statistics mixin.
9. **3D / z-plane pathing + terrain altitude** — `get_path_3d`,
   `get_position_with_zplane`, path densify/smooth, `Overlay.QueryAltitude`,
   `Map.GetBlockingProps`.
10. **MCP server + scenario harness + ui_capture** — `py4gw_mcp_server.py`
    (16 tools: read/assert/poll_until/script control), `BridgeRuntime/harness.py`
    (scenarios with `expect_log`, exit 0/1 — CI-shaped), bounded game-thread-safe
    `ui.*` inspection. A has the daemon/CLI but none of these three.
11. **Global config / item-data stack** — `global_configs/` (5.5k: Condition
    predicates, Buy/Loot/Crafting/Sorting configs, profile manager with
    multibox broadcast), `item_data/` (ItemData/ItemSnapshot/Recipe),
    `generics/data_dict.py` (multi-process-safe versioned JSON store).
12. **Raw packet send** (`Packet.py` fluent builder over `PyCtoS`),
    **data_collectors/** sweep framework (3.8k), **EnemyCapabilities** lookup,
    **ally + temporary blacklists**, **dialog_history model**, **offline
    contract test suite** (14 files, 5.6k — AST-based, no runtime needed).

## A has, B lacks — Python library (ranked)

1. **FrameTree** (7.5k) — the native-UI object model: ~130-member `Frame`
   handle, generated type-checked `FrameId` constants, per-tick cached
   snapshot, hash↔name provenance table. B works in raw ids + a hand-kept
   hash catalog (its own Friend Notes widget hardcodes hashes — the exact
   problem FrameTree solves).
2. **The fight/ positional layer** (3.2k + 7 test files) — zone state machine,
   engagement-vs-aggro distinction, front/mid/back formations with reach
   budgets, non-ratcheting budget-based health retreat, breadcrumb + radial
   navmesh escape, leader publisher riding the follow channel. B has zero.
3. **The BT combat engine** (`HeroAI/bt/` + `BldMgrBT` + `build_src`, ~3.2k +
   88 BTBuilds) — build-contract scoring by map/profession/skill signature,
   three-tier condition dispatch with per-family fallback to the legacy
   oracle, `NATIVE_DECIDE` kill switch. B still grows the monolithic ladder
   (its `combat.py` is 2,425 lines and rising).
4. **GWUI toolkit** (877 vs 305) — buttons, checkboxes, edit boxes, sliders,
   tabs, hyperlinks, panels drawn as *native game frames*, with read-back.
5. **Native listener control** (`Listeners.py` + `PyListeners`) — 14
   runtime-toggleable automations (auto-open chest, cinematic skip, auto
   return on wipe, skill-list filter, gold-confirm suppression…), zero
   overhead when off. B would need Python polling loops for each.
6. **Recolor + marking stack** (~3.7k) — rule-driven agent/gadget/item
   recolor, beacons, item-frame tint natives (23 methods B's DLL lacks).
7. **Feature packages with no B counterpart** (~20k) — map_overlay (3.0k),
   launch_bar (3.3k), item_catalog (3.3k shipped reference data), loot filter
   factory (3.5k), script_manager, name_obfuscation, window_renamer,
   skillbar_plus, system_settings, camera_smoothing.
8. **`command_api.py`** — typed multibox command façade + launch-bar
   registration; B has only stringly-typed commands.
9. **`debug_hatch.py`** — live HTTP REPL into the injected process.
10. **mods_core engine** — same job as B's 12.3k-LOC item-mods class
    hierarchy in 2.4k of data tables.
11. **`docs/RE/` corpus** (~1.5 MB: control master catalog, creation recipes,
    button pipeline, rosetta stone) — B has no `docs/RE/` at all.
12. **Deep git history** — 145 commits on `combat.py` back to original Py4GW;
    B is a June-2026 squash and cannot bisect its own regressions.

## DLL: B-only capabilities (ranked)

1. **~192 readable agent struct fields** — energy/regen/overcast, every
   condition/hex/enchant boolean, weapon type + attack speed, casting skill
   id, animation/model state, velocity. A exposes 19 opaque getters. **The
   single biggest gap in either direction.**
2. **`PyPointers`** — 12 raw context pointers; Python ctypes-reads any game
   struct. B's whole `native_src/context/` layer rides on it.
3. **`PyCtoS.SendPacket`** — arbitrary client→server packets.
4. **Native character select** — `queue_native_character_select(name)` with
   status polling + pregame-thread enqueue + startup email. A has only a bool.
5. **UI message bus tap** — `get_ui_message_logs()` structured records.
6. **Native window creation/layout** — titled/clone/scrollable windows, title
   hooks, frame subclassing, anchor margins, rect persistence (16 methods).
7. **Salvage preflight + upgrade engine + weapon sets** in `PyInventory` —
   `CanSalvage*` family, `ValidateUpgrade`/`ApplyUpgrade`,
   `ChangeWeaponSet`/`GetActiveWeaponSet`.
8. **Dialog catalog enumeration** with async decode — A can send but not list.
9. **Rule-set agent recolor** (priority/allegiance/map/name-scoped) vs A's
   flat setters; **z-plane pathing natives**; **quest objective event queue**;
   **mission progress bars**; **native skillbar-effects overlay**;
   **`draw_on_compass`**; ImGui **multi-viewport + multi-select + ImHotKey**
   (policy-audited, upstream-pinned).

## DLL: A-only capabilities (ranked)

1. **Travel/map control** — `PyMap.travel`, districts, challenge entry, and
   map state. B has *zero* travel bindings (does it via ctypes UIMessage).
2. **Terrain raycasting** — `RayCast`/`RayCastTerrain`/`RayCastInteractive`,
   prop geometry, ground-Z batch helpers.
3. **The 15 native listeners** (see above).
4. **Persistence** — `PySettings` (per-account INI, cross-account copy) +
   `PyJson` (tree docs, atomic, cross-process-locked global scope = the
   sanctioned multibox IPC). B has nothing; its Python side hand-rolls it.
5. **`PyTexture`** — GW.dat file-id textures + dye-colored model textures.
   B's texture stack is C++-internal, invisible to Python.
6. **`PyChat`** — send/whisper/local/fake chat, channel colors. B: none.
7. **`PySystem.window` + `script_control` + `widget_manager`** — 25 window
   fns (borderless, opacity, click-through, z-order), script load/pause/
   resume, widget manager control. B: none.
8. **`PyWorldRender`** — draw callbacks inside GW's world render pass (true
   depth occlusion) with a script-death watchdog.
9. **Party mutation** — 42 exclusive fns (heroes, henchmen, invites, flags,
   hard mode, tick, party search). B has 4 readers.
10. **ImPlot + DockBuilder + file browser + memory editor** in ImGui;
    `PyProfiler` percentiles; `PyParticles`; `PyGuild.travel_gh`;
    `PyFriendList`; `PyPing`; Xunlai/gold ops in `PyItem`; camera
    unlock/FOV/fog; `PyEffects` buff ops; `UseSkill` returns bool (B: None —
    B scripts cannot detect a rejected cast).

## Ecosystem

- **Route content**: B's `Widgets/Automation/Bots/` has 344 bots vs A's 94 —
  the delta is Vanquish (+135) and Runners (+103), i.e. *content*, not
  machinery. Widgets are line-specific; routes port as data, not files.
- **Overlay widget family** (B): WorldMap+/Mission Map+/Compass+/Skillbar+
  Effect/Inventory Overlay/Friend Notes/PartyQuestLog/Hex Tracker view.
- **Widget-manager UX** (B): the WidgetCatalog query layer + 147 KB explorer.
- **Tests**: B pins inventory/salvage/Steps via offline AST-contract suites
  (one even reaches into its C++ sibling to pin a binding); A's `test/` covers
  fight/, FrameTree, and marks_sources.

## Mining shortlist — into this tree, by value ÷ cost

1. **Layer-aware targeting** — tiny, pure logic, immediate party-quality win.
2. **BT economy nodes** — the biggest bot-capability hole; re-implement
   against A's `BT.Items` idioms (persistence jail applies to configs).
3. **BT party assembly / wipe recovery** — second-biggest hole.
4. **Steps checkpoint-restart + run stats** — planner ergonomics.
5. **Hex tracker** — concept port; its shared-memory block must become a
   `GLOBAL_CACHE.ShMem` design here.
6. **Hero smart-cast** — big but self-contained; pairs with hex tracker.
7. **MCP server + harness + ui_capture** — completes A's half-built bridge;
   the ui_capture safety rules (game-thread, no blind decode) carry over.
8. **Pet smart combat** — moderate.
9. **z-plane pathing + QueryAltitude** — needs native work in
   `Py4GW_Reforged_Native` (B's DLL has the natives, A's does not).
10. **Agent struct fields / salvage preflight / character select** — all
    **DLL gaps**: closing them means C++ in `Py4GW_Reforged_Native`, using
    B's cpp as the reference implementation.

## Contribution material — what this tree has that Unchained would want

The `fight/` positional layer, the BT combat engine pattern, FrameTree's
generated-registry approach (fixes their hardcoded-hash widgets), the listener
registry concept, GWUI's widget toolkit, the RE corpus, mods_core's
data-table approach, per-widget profiling hooks. All travel as concepts via
`port-concept` + `contribute-unchained`.
