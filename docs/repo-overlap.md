# Repo overlap ledger

Snapshot taken **2026-08-06**, by hand (directory listings and `git` queries —
deliberately no generator; to refresh, re-list and replace this file, and
re-date it). Name-level comparison: a shared name means shared ancestry, not
identical content.

Purpose: under RS-008 a concept crosses fork lines only by re-implementation.
Before re-implementing, this ledger says where the concept already lives on
each side. The capability-level companion — what each side can *do* that the
other cannot, from a deep read — is [repo-gaps.md](repo-gaps.md).

## The six repos at a glance

| Repo | Line | Language | Role |
|---|---|---|---|
| `mypy4gw_main` (here) | Reforged | Python | Mark's sandbox: `layout` regenerated from upstream + his overlay. The working bot lives here. |
| `Py4GW_Reforged` | Reforged | Python | the fork clone: archive of unported work (13 branches), `forwardport.py` source. Inbound only. |
| `Py4GW_Reforged_Native` | Reforged | C++ | builds **this** tree's `Py4GW.dll` (13.6 MB). Subsystem-tree `src/`, CMake presets, local game-thread patch (`rebuild-dll` skill). |
| `Py4GW-Unchained` | Unchained | Python | sibling fork of the pre-Reforged ancestor. **Active contribution target** (PR to Wick-Divinus). |
| `Py4GW-unchained-cpp` | Unchained | C++ | builds *Unchained's* `Py4GW.dll` (9.4 MB). Flat `src/`, plain CMake. **Active contribution target** (PR to sloppynacho). |
| `GW_RE` | — | Ghidra | RE bench for the game itself (`Gw.exe` + `Gw.wasm`). Plain directory, not a repo. |

The two *lines* forked from the same pre-Reforged ancestor and never re-merged.
Reforged renamed the native modules and (here) the library root; Unchained kept
the legacy names and accumulated widgets. The two DLLs export different
surfaces — nothing binary-compatible crosses between them.

## Method

Native binding areas are compared after name normalization: `items`↔`item`,
`trading`↔`trade`, `pathing_maps`↔`pathing`, `ctos`↔`stoc`,
`combat_events`↔`events`+listeners, `2d_renderer`+`overlay`↔`overlay/`,
`imgui*`↔`imgui/`, `ui`↔`native_ui`+`ui`.

## Table 1 — core library

`Py4GW-Unchained/Py4GWCoreLib/` has 75 top-level entries; `Core/` here has 66.
**55 names are shared** — the whole classic surface: Agent, AgentArray,
Botting + botting_src/botting_tree_src, Camera, CombatEvents, Context,
Database, Dialog(+Catalog), DXOverlay, Effect, enums(+src), GlobalCache, GWUI,
HotkeyManager, ImGui(+src), Inventory, Item(+Array), Map, Merchant, Overlay,
PacketSniffer, Party, Pathing, Player, Py4GWcorelib(+src), Quest, Routines
(+src), Scanner, Skill, Skillbar, SkillManager, UIManager, model/quest data…

| Unchained-only (20) | mypy4gw-only (11) |
|---|---|
| `IniManager.py`, `Settings.py` (their persistence — forbidden style here) | `FrameTree/` (frame-tree work) |
| `BuildMgr.py` (retired here under RS-007) | `BldMgrBT.py`, `BTBuilds/`, `build_src/` |
| `item_mods_src/`, `item_data/` | `mods_core.py`, `mods_types.py`, `mods_upgrades.py` |
| `frame_aliases.json` (anchoring style we rejected) | `AgentRecolor.py`, `ChatCommands.py`, `Listeners.py` |
| `Mission.py`, `SC.py`, `speedclear_src/`, `CrystalDesertTeleporter.py` | `debug_hatch.py` |
| `Blacklist.py`, `BottingTreeSteps.py`, `dialog_history.py`, `EnemyCapabilities.py` | |
| `generics/`, `global_configs/`, `data_collectors/`, `Packet.py`, `tests/`, `Settings.py.bk` | |

## Table 2 — non-identity pairings

Same concept, different shape. Never map these 1:1.

| Unchained | here | note |
|---|---|---|
| `BuildMgr.py` | `BldMgrBT.py` + `build_src/` | RS-007 retired the generator ladder; only the BT engine remains |
| `item_mods_src/` | `mods_core.py` / `mods_types.py` / `mods_upgrades.py` | same domain, re-cut |
| `frame_aliases.json` + `get_frame_id_by_hash` | `FrameTree/` + template-pattern matching | opposite anchoring philosophies — see runtime-behaviour.md |
| `combat_layer.py` + `hero_skills.py` | `HeroAI/bt/` + `HeroAI/engine.py` | their layer system vs our behaviour trees |

## Table 3 — HeroAI

The highest-overlap surface: **24 of ~27 names shared** (~86%) — cache_data,
call_target, combat, commands, constants, custom_skill(+src), enemy_party,
follow, globals, headless_tree, hex_removal_src, interrupt, party_cache,
resurrection_scroll, settings, targeting, team_viewer_broadcast, types, ui,
ui_base, utils, windows…

| Unchained-only | mypy4gw-only |
|---|---|
| `combat_layer.py`, `hero_skills.py`, `hex_tracker_src/` | `bt/`, `fight/`, `engine.py`, `command_api.py` |

## Table 4 — Widgets

Essentially disjoint. Unchained carries **414** leaf widget modules in its own
folder taxonomy (Automation, Coding, Config, Guild Wars, Legacy, System, Tom,
WidgetCatalog); this tree keeps **17** in a re-cut taxonomy (Diagnostics,
Items, Multibox, Overlays, Panels, Upkeep, WidgetManager, World, lib). Only 10
leaf names appear in both: Enemy Tracker, Environment Upkeeper, Frame Limiter,
HeroAI, Messaging, SkillInfo, Style Manager, Switch Character, Titles, Travel.
Treat any widget as line-specific.

## Table 5 — native binding areas

`Py4GW-unchained-cpp/src/` is a flat sheet (52 files, 32 `py_*.cpp`);
`Py4GW_Reforged_Native/src/` is a subsystem tree (186 `.cpp`, 42
`*_bindings.cpp`). After normalization, **~14 areas exist on both sides**:
agent, agent_recolor, camera, chat_commands, dialog, effects, merchant,
name_obfuscator, packet_sniffer, party, player, quest, skillbar, ui — plus the
normalized pairs (item/items, trade/trading, pathing, stoc/ctos, overlay,
imgui, events).

| unchained-cpp only | Reforged_Native only |
|---|---|
| `character_select`, `dialog_catalog`, `inventory_overlay`, `skillbar_effects_overlay`, `mission` | whole subsystems: `json/` (JsonFactory), `settings/`, `profiler/`, `virtual_input/`, `callback/`, `base/` (scanner, hooker, memory_patcher, python_runtime) |
| monolithic data blobs in `src/` (`SpecialSkilldata.cpp` 325 KB, `SkillArray.cpp` 136 KB) and own texture stack (`GwDat*`, `AtexAsm`, `ArenaNetFileParser`) | 15 listeners (auto_cancel_ua, auto_open_chest, cinematic_skip, skill_filter…), `multibox`, `shared_memory`, `game_thread`, `ping`, `friend_list`, `guild`, `world_render`, `context`, `textures`, `map` |

The DLLs are different builds with different export surfaces — a Python file
written against one will not import under the other.

## Table 6 — where does concept X live

| Concept | Unchained line | Reforged line (here) |
|---|---|---|
| persistence | `IniManager.py`, raw `json`/`configparser`/`open()` — normal there | `Settings` + `JsonFactory` only (the jail, hard-rules.md) |
| UI frame identity | `PyUIManager` (~219 methods), `frame_aliases.json`, hash lookup | `Core/FrameTree/`, template_type + child_offset_id pattern (runtime-behaviour.md) |
| 2D overlay | `Py2DRenderer` | `PyDXOverlay` |
| combat events | `PyCombatEvents` | `PyAgentEvents` |
| keyboard input | `PyScanCodeKeystroke` | `PyKeyHandler` |
| vectors | `Point2D`/`Point3D`; a ctypes `Vec2f` in `native_src/internals/types.py` (a trap — not a binding) | `Vec2f`/`Vec3f` native bindings |
| raw pointers | `PyPointers` | retired |
| build management | `BuildMgr.py` | `BldMgrBT` + `build_src/` (RS-007) |
| combat engine | `combat_layer.py`, `hero_skills.py` | `HeroAI/bt/`, `engine.py`, `fight/` |
| bridge / MCP | `bridge_daemon.py`, `bridge_cli.py`, `py4gw_mcp_server.py`, `BridgeRuntime/` | `docs/MCP_bridge.md` (design), no shipped stack |
| launcher | `Py4GW_Launcher.exe` + `.py` in-tree | `Launcher/`, `Py4GW_Reforged_Launcher.exe` |
| frame RE experiments | `UI_RE/` (~20 standalone experiments) | `dev/harness/frame_viewer.py`, `docs/RE/ui_*.md` |
