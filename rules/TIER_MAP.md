# Tier map — RS-006

Where every part of this project sits, what it is built on, what it costs to
change, and which parts upstream will fight you over. **Read this before deciding
where a new file should live.**

`docs/Py4GW_Conceptual_Model.md` describes the *conceptual* layering. This is the
*operational* one: blast radius, reload cost, dependency direction, contested
ownership. Under RS-000 it outranks anything in `docs/` that disagrees.

## Standing of each part

Written in the fork before the four-branch machine existed, then ported here and
retargeted at this tree. Read it with that history in mind — in particular, its
"send it upstream as a PR" framing predates RS-008: nothing goes to the
Reforged line anymore. The tier analysis itself remains current:

| part | standing |
|---|---|
| 1 — the tier map | **current.** The reference. |
| 2 — reload cost | **current.** |
| 3 — where you stand | **current**, except the conflict-surface counts, which `DIVERGENCE.md` now measures every sync. |
| 4 — separation plan | **largely done.** Move 1 (pin and drift-test the Tier 0 boundary) and Move 3 (work into a Tier 4 overlay) are what `tools/reforge/` and the `Scripts/` packs implement. Move 2 (cut the Tier 3 cycle) is **not** done. |
| 6 — file-level tier assignment | **current**, and what `tiercheck.py` enforces. |

29 paths were translated from upstream's layout through
`tools/reforge/layout.toml`, so they name this tree — `Py4GWCoreLib` is `Core`
here under RS-001. Counts were measured against the fork at `dae4e4ca` and are
not re-measured per sync; treat them as an order of magnitude.

---

## Part 1 — The tier map

### Tier 0 — Injection substrate (upstream-owned, not forkable in practice)

| Thing | What it is | Where |
|---|---|---|
| `Gw.exe` | The game. Origin of all runtime data. | external |
| `Py4GW.dll` | 13.6 MB. Embeds CPython 3.13 32-bit, hooks D3D9, renders ImGui, exposes the `Py*` modules. | repo root, binary |
| `stubs/` | 43 top-level `.pyi` + `stubs/Py4GW/` (5 more). Type-only forward declarations of the DLL's binding surface. Zero runtime effect. | `stubs/` |
| Launcher / injector | `Py4GW_Reforged_Launcher.exe` + 29 `.py`. Runs out-of-process; cannot import the embedded modules. | `Py4GW_Reforged_Launcher/` |

`Py4GW.dll` is built by a **separate C++ repository**, `Py4GW_Reforged_Native`.
Nothing in this Python repo builds it. Runtime addresses come from that repo's
`offsets/*.json` pattern files.

**The single most important structural fact in this project:**

The DLL's entire Python-facing contract is **two pinned strings**. Scanning the
binary for referenced Python identifiers yields exactly:

```
Py4GW_widget_manager.py
autoexec_script
```

That is the whole handshake. The DLL loads `Py4GW_widget_manager.py` as the
always-on host and calls its `main()` every draw frame; `autoexec_script` in
`Py4GW.ini` optionally points at one more file. **Everything else — widget
discovery, the `.widget` marker convention, `Core`, `GLOBAL_CACHE`,
the launchpad — is Python-side convention layered on top of those two names.**

The practical consequence: the native boundary is narrow and stable. You are not
forking a platform when you restructure the Python side; you are rearranging code
behind a two-string interface. This is what makes independence cheap where it
matters and is the load-bearing assumption of Part 2.

**Change cost:** rebuild C++ → re-inject → restart the game client.

---

### Tier 0.5 — Native access path in Python (the mislabelled tier)

- `Core/native_src/` — ctypes structs read from process memory, plus
  pattern-scan function resolution (`native_src/context/`, `native_src/methods/`,
  `native_src/internals/`).
- `Core/Scanner.py`, `Core/Context.py`.

This is Python code, but it is pinned to game memory layout and to the native
repo's offset tables. It is Tier-0-fragile code living inside a Tier-1 directory —
**treat it as vendored, not as yours.** It is also high upstream churn
(`native_src/context` was touched in 51 of the last 200 upstream commits).

If a game patch or a native rebuild breaks you, this is where it breaks, and the
failure mode is a silent wrong read rather than an ImportError.

---

### Tier 1 — `Core` source-of-truth wrappers

The domain modules everything imports: `Agent`, `AgentArray`, `Player`, `Map`,
`Item`, `ItemArray`, `Inventory`, `Skill`, `Skillbar`, `Party`, `Quest`, `Effect`,
`Camera`, `Merchant`, `CombatEvents`, `UIManager`, `Overlay`, `DXOverlay`,
`ImGui`, `Pathing`, `enums`.

564 tracked `.py` files across the whole `Core` tree; 827 internal import
edges; 230 direct stub imports. This is the layer where Py4GW semantics begin.

**Fan-in — this is why it cannot be casually forked:**

| Importer | Import edges into `Core` |
|---|---|
| `Sources/` | 613 |
| `Widgets/` | 516 |
| repo root scripts | 292 |
| `HeroAI/` | 181 |
| `UI_RE/` | 93 |
| `Examples/` | 56 |
| `Bots/` | 54 |

---

### Tier 2 — Support infrastructure

`Core/py4gwcorelib_src/` — reusable machinery, not domain truth:

- **Orchestration:** `BehaviorTree`, `FSM`, `VectorFields`, `WidgetManager`
- **Dispatch/timing:** `ActionQueue`, `Timer`, `MultiThreading`, `Keystroke`, `HotkeyManager`
- **Persistence (the jail):** `Settings` (INI), `JsonFactory` (JSON)
- **UI infrastructure:** `launch_bar/`, `map_overlay/`, `Color`, `Profiling`
- **Automation support:** `AutoInventoryHandler`, `Lootconfig_src`
- **Mine:** `script_manager/` (5 files, uncontested)

Plus two peers that are conceptually Tier 2 despite living elsewhere:

- `Core/GlobalCache/` — cache runtime + `ShMem` multibox coordination
- `Core/routines_src/` — `Checks`, `Movement`, `Targeting`, `Transition`,
  and the three execution styles `Sequential` / `Yield` / `BehaviourTrees`

---

### Tier 3 — Combat & automation engines — **the tangle**

- `HeroAI/` (52 `.py`) — `combat`, `targeting`, `custom_skill`, `cache_data`,
  `interrupt`, `headless_tree`, `follow/`, `hex_removal_src/`, `bt/` (mine)
- `Core/SkillManager.py` (`Autocombat`), `BuildMgr.py`, `BldMgrBT.py`,
  `build_src/combat_services.py`
- `Core/Builds/` + `BTBuilds/` — per-profession build content
- `Core/Botting.py`, `botting_src/`, `BottingTree.py`, `botting_tree_src/`

**This tier contains a package-level dependency cycle:**

```
HeroAI  ──── 181 import edges ────▶  Core
   ◀──────── 55 import edges ─────────┘
```

`Core` imports `HeroAI` in 55 places. They are one component wearing two
directory names. You cannot take a `Core` update without implicitly
accepting the `HeroAI` surface it depends on, or vice versa.

Breaking down the 55 back-edges by what is actually needed:

| Imported from HeroAI | Used by | Nature |
|---|---|---|
| `custom_skill_src.skill_types` (`CustomSkill`, `CastConditions`) | 15 | pure type/vocabulary |
| `targeting` (~20 `Target*` / `GetEnemy*` fns) | 21 | pure functions over agent arrays |
| `types` (`SkillType`, `Skilltarget`, `SkillNature`) | 10 | pure enums |
| `utils`, `cache_data.CacheData`, `settings.Settings` | ~9 | state/handle |

The important detail: **the large majority of these edges come from
`Builds/` and `BTBuilds/` — content files, not engine files.** A build definition
imports `CustomSkill` and `GetAllAlliesArray`. That is not an architectural
dependency of the core library on the AI; it is build content reaching for shared
combat vocabulary that happens to be stored under `HeroAI/`.

There is a second, smaller cycle: `py4gwcorelib_src/AutoInventoryHandler.py`
imports `Sources.frenkeyLib.ItemHandling` in 5 places — Tier 2 reaching into a
Tier 4 contributor tree.

---

### Tier 4 — Leaf consumers (cheap, hot-loadable, mostly uncontested)

| Tree | Tracked `.py` | Notes |
|---|---|---|
| `Sources/` | 497 | per-contributor subtrees (`ApoSource`, `frenkeyLib`, `marks_sources`, …) |
| `Widgets/` | 182 | 57 `.widget` marker folders |
| `Examples/` | 61 | |
| `UI_RE/` | 41 | |
| `Bots/` | 38 | |
| repo root | 45 | test/scratch scripts, largely unmanaged |
| `Scripts/` | 1 tracked | flat drop zone — see Part 3 |

Out-of-process satellites (cannot import the embedded modules, exempt from the
persistence jail): `bridge_daemon.py`, `bridge_cli.py`, `py4gw_mcp_server.py`,
`BridgeRuntime/`.

---

## Part 2 — The axis that actually matters: reload cost

Tier tells you what depends on what. **Reload cost tells you what you can iterate
on.** These are different, and the second one is the lever.

| Tier | Load mechanism | To apply a change |
|---|---|---|
| 0 — DLL | injected into `Gw.exe` | rebuild C++, re-inject, **restart game** |
| 0.5–3 — any `Core` / `HeroAI` module | plain `import`, cached in `sys.modules`, **never purged** | **restart client** |
| 4 — widgets | `importlib.util.spec_from_file_location` under a unique `py4gw_widget_*` name | re-discover; module cached once loaded |
| 4 — scripts via `script_manager` | `loader.py` **purges the script's support modules from `sys.modules` by prefix** | **hot reload, no restart** |

Two details worth knowing precisely:

**Widget "reload" is weaker than it looks.** `WidgetHandler.reload_widgets()`
resets discovery flags and re-runs `discover()`, but `Widget.load()` returns early
when `self.module is not None`. An already-loaded widget is not re-executed. Only
the failure path (`del sys.modules[unique_name]`) actually evicts.

**`script_manager/loader.py` is the only true hot-reload path in the repo.** It
walks `sys.modules`, pops every module under the script's support-package prefix,
and re-imports. `discovery.py` reads the `__script__` metadata dict from the first
8 KB of each file without importing it — a rescan of 150 scripts costs ~18 ms
instead of ~3 s. Discovery is **flat** (`os.listdir`, not `os.walk`), so:

```
Scripts/Foo.py            → discovered as an entry point
Scripts/mylib/helper.py   → NOT discovered; importable support code
```

That flat-entry-point / nested-library split is a clean overlay contract, and it
already exists.

**Strategic consequence:** the further down the tier stack your code lives, the
more it costs you *per edit*, every day, forever. Code you own that sits in Tier 1
or 2 taxes your own iteration speed as much as it exposes you to upstream churn.
Both pressures push the same direction: **your work belongs in Tier 4, loaded
through `script_manager`.**

---

## Part 3 — Where you actually stand

### Ownership

| | |
|---|---|
| apoguita commits | 1769 |
| Mark commits | 303 |
| other contributors | Wick-Divinus 410, frenkey-derp 342, Icefox 231, sch0l0ka 213, … |
| `.py` files Mark has touched | 491 |
| `.py` files apoguita has touched | 3034 |
| **contested (both)** | **210** |
| **Mark-only** | **281** (151 of them under `Scripts/`) |

Current divergence from the last upstream sync (`55ec88a6`): **135 files,
+17,085 / −2,198**.

### Hottest contested files — your recurring merge pain

| File | apoguita | Mark |
|---|---|---|
| `Core/routines_src/Yield.py` | 62 | 11 |
| `HeroAI/windows.py` | 67 | 4 |
| `Widgets/HeroAI.py` | 52 | 15 |
| `Core/enums.py` | 53 | 4 |
| `Core/__init__.py` | 49 | 3 |
| `HeroAI/custom_skill.py` | 41 | 4 |
| `Core/BuildMgr.py` | 30 | 1 |

The shape is consistent: upstream owns these files by an order of magnitude, and
your edits are a thin minority. **Every one of these is a file to stop editing,
not a file to fight over.**

Inverted, your genuinely uncontested work is `Scripts/py4gw-marks-corner/scripts`
(20–27 Mark commits each vs 3–8), `HeroAI/bt/` (8 files), and
`py4gwcorelib_src/script_manager/` (5 files).

### The reorganization problem

Upstream restructures in large sweeps that defeat rename detection and turn
routine merges into manual reconciliation:

- `94f13114` "Imgui class restructure" — **308 files, +4,928 / −6,848**
- The widget tree was flattened and re-nested: `Widgets/HeroAI.py` →
  `Widgets/Multibox/HeroAI.py`, `Widgets/Messaging.py` →
  `Widgets/Panels/Messaging.py`, `Widgets/CombatPrep.py` → gone.

This is the concrete mechanism behind "the owner doesn't really know how to use
git," and it is not going to change. **Plan around it rather than negotiating it.**
The defense is not better merge tooling — it is owning file paths upstream has
never created and will therefore never move.

---

## Part 4 — Separation plan

### The principle

**Treat upstream as a vendored platform, not as a shared codebase.**

You cannot hard-fork: the DLL is a C++ project you would have to take over
wholesale, and it is the one component you genuinely cannot reproduce. You also
cannot keep editing hot upstream files and expect merges to stay cheap. The
resolution is to be a *consumer* of Tiers 0–2 and an *owner* of Tier 4, with one
targeted structural fix at Tier 3.

Ordered by leverage-per-unit-effort:

---

### Move 1 — Pin and drift-test the Tier 0 boundary *(small, do first)*

The DLL and `stubs/` are a release artifact. Never edit them.

- Record the DLL version/hash you are validated against.
- Add a test that imports each `Py*` module you actually call and asserts the
  symbols exist. `stubs/` is your declared contract; make violations of it fail
  loudly.

Today, an upstream native change breaks you at runtime with no signal, in
`native_src` reads that return plausible-but-wrong values. After this, a DLL bump
is a test failure with a name attached. This is the cheapest risk reduction
available and it is independent of everything else here.

---

### Move 2 — Cut the Tier 3 cycle *(highest structural value)*

Extract the shared combat vocabulary that `Core` currently reaches into
`HeroAI` for — the enums, the `CustomSkill`/`CastConditions` types, and the
`targeting` query functions — into a leaf package with **no upward dependencies**.
Point both `Core` and `HeroAI` at it.

This is not a rewrite. The imports are narrow and already enumerated in the Tier 3
table above, and most of them come from `Builds/`/`BTBuilds/` content files
reaching for vocabulary rather than from engine code with a real architectural
dependency.

Why it is worth doing properly rather than working around:

- It is the **only** thing standing between you and taking `Core` updates
  without also taking `HeroAI` updates.
- It is defensible on its own merits, so it is the one change worth pushing
  upstream as a PR. It helps every contributor, it is mechanical enough to review,
  and if it lands, your independence is maintained by upstream rather than by you.
- If it does not land, you carry it as a patch — but a small, semantically
  isolated one that survives file moves far better than scattered edits.

Same treatment for the smaller `AutoInventoryHandler → Sources.frenkeyLib` cycle.

---

### Move 3 — Move your work into a Tier 4 overlay *(the actual independence)*

**The mechanism already exists and you built it.** `script_manager` gives you flat
discovery, `__script__` metadata, resource-claim conflict detection, and real
hot-reload. `Scripts/` is already a near-untracked drop zone — 151 files of your
history, 1 file currently tracked, and two untracked subtrees
(`py4gw-marks-corner/`, `py4gw-community-bots/`) already sitting there.

Structure it deliberately:

```
Scripts/
  MyFarm.py               ← flat: discovered entry point, hot-reloadable
  MyOtherBot.py
  markslib/               ← nested: support code, importable, not discovered
    combat.py
    routing.py
```

Rules that keep this working:

1. **Own paths upstream has never created.** `Scripts/` flat files, `markslib/`,
   `HeroAI/bt/`, `script_manager/`. Upstream cannot move what it does not know
   about.
2. **Wrap, do not edit.** Where you need different Tier 1/2 behavior, wrap at load
   time rather than editing in place. The repo already sanctions this pattern —
   `CLAUDE.md` explicitly permits calling into existing underscore names on
   framework classes when wrapping.
3. **Anything you must edit upstream goes upstream**, as a small single-purpose
   PR. Not because upstream will be prompt, but because a merged change survives
   their next 308-file restructure and an unmerged local edit does not.
4. **Stop editing the hot-contested list** in Part 3 entirely. Reimplement the
   behavior you need in your overlay instead.

**Repo layout recommendation: separate git repo, cloned into `Scripts/`.**

Python resolves imports from the project root, so your tree must physically sit
inside the working tree — but it does not have to be *tracked* by the fork. A
plain clone (or submodule, if you want the fork to pin a revision) into `Scripts/`
gives you:

- your own history, branches, and release cadence, with no upstream commits in it
- a fork whose diff against upstream shrinks toward Moves 1 and 2 only
- the ability to run your work against upstream `main` directly instead of against
  a permanently diverged branch

Prefer the plain clone over a submodule initially — a submodule adds a pointer
commit in the fork, which is one more thing to explain to a collaborator who does
not use git well.

---

### What this leaves you

Your fork's permanent delta against upstream reduces to: the Move 2 cycle cut
(ideally upstreamed, worst case a small patch), the stub drift test, and a
`.gitignore` line for `Scripts/`. Everything else you build lives in a repo you
own outright, loads without a client restart, and is unaffected by upstream
reorganizing its widget tree for the third time.

You keep taking DLL and Tier 1 updates. You stop taking merge conflicts.

---

## Part 6 — File-level tier assignment

Part 1 bucketed code by **folder**. This part assigns tiers by **role**, then
lists every file whose folder disagrees with its role. The two give very
different pictures, and the role-based one is the actionable one.

### Assignment rule

A file's tier is the highest tier it may import from. A file at tier N may import
tiers ≤ N. Importing tier > N is a **violation**.

| Tier | May import | Meaning |
|---|---|---|
| 0 | — | native surface: `stubs/` |
| 0.5 | 0 | vendored memory access: `native_src/`, `Scanner.py`, `Context.py` |
| 1 | 0.5 | domain source-of-truth wrappers |
| 2 | 1 | reusable support infrastructure |
| 3 | 2 | combat / automation engines and content |
| 4 | 3 | leaf consumers |

### `Core/` — assignment of every top-level entry

**Tier 0.5 — vendored, treat as upstream**
`native_src/` · `Scanner.py` · `Context.py`

**Tier 1 — domain source-of-truth**
`Agent.py` · `AgentArray.py` · `AgentRecolor.py` · `Camera.py` · `ChatCommands.py`
· `CombatEvents.py` · `CombatEventQueue_src/` · `Dialog.py` · `DialogCatalog.py`
· `DXOverlay.py` · `Effect.py` · `GWUI.py` · `Inventory.py` · `Item.py`
· `ItemArray.py` · `Listeners.py` · `Map.py` · `Merchant.py` · `Overlay.py`
· `PacketSniffer.py` · `Party.py` · `Pathing.py` · `Player.py` · `Quest.py`
· `Skill.py` · `Skillbar.py` · `UIManager.py` · `EnemyBlacklist.py`
· `enums.py` · `enums_src/` · `ImGui.py` · `ImGui_src/` · `Database.py`
· `database_src/` · `model_data.py` · `model_id_converter.py` · `mods_*.py`
· `quest_data.py`

**Tier 2 — support infrastructure**
`py4gwcorelib_src/` · `GlobalCache/` · `routines_src/` · `Routines.py`
· `Py4GWcorelib.py` · `HotkeyManager.py` · `modular/` · `dNodes/` · `debug_hatch.py`

**Tier 3 — combat / automation. Misplaced: these are in a Tier 1 folder.**

| Files | Path |
|---:|---|
| 116 | `Core/Builds/` |
| 82 | `Core/BTBuilds/` |
| 39 | `Core/botting_src/` |
| 16 | `Core/botting_tree_src/` |
| 1 | `Core/build_src/` |
| 5 | `SkillManager.py` `BuildMgr.py` `BldMgrBT.py` `Botting.py` `BottingTree.py` |
| **259** | **of 565 `.py` in `Core/` — 46% of the "core library" is not core** |

Confirmed by two independent methods (filesystem walk and AST import analysis).

---

### The reframe

Bucketing by folder produced 55 `Core → HeroAI` edges. Most of those were
not violations — they were Tier 3 build content, correctly importing Tier 3
`HeroAI`, that merely happens to live inside the `Core/` directory.

Tiering by role, the whole tree has **22 violating import statements across 17
files**:

| Count | Violation | Verdict |
|---:|---|---|
| 9 | T1 domain → T2 support | definitional, not a bug |
| 5 | T2 support → T3 `HeroAI` | **real** |
| 4 | T2 support → T4 `Sources.frenkeyLib` | **real** |
| 3 | T0.5 native → T1 enums | real, trivial |
| 1 | T3 combat → T4 `Widgets` | **real** |

The 9 T1→T2 edges are `Agent`/`ItemArray`/`Merchant`/`UIManager` → `FrameCache`,
`Map` → `ActionQueue`, `Skillbar` → `Utils`, `Party` → `name_obfuscation`,
`EnemyBlacklist`/`ImGuisrc` → `Settings`. These say the T1/T2 line is drawn in the
wrong place — `FrameCache`, `Utils`, `ActionQueue`, and `Settings` are
primitives-of-convenience the domain layer legitimately sits on. Reclassify them
rather than "fix" them.

**That leaves 10 statements in 7 files as the genuine red.**

### The genuine red — every one, exact

```
T2 → T3  Core/GlobalCache/HexRemovalPriority.py
             → HeroAI.hex_removal_src.hex_removal_config
T2 → T3  Core/GlobalCache/SharedMemory.py
             → HeroAI.follow.leader_publish          (startup-sensitive; see hard rules)
T2 → T3  Core/GlobalCache/shared_memory_src/AccountStruct.py
             → HeroAI.settings
T2 → T3  Core/routines_src/Targeting.py
             → HeroAI.cache_data
T2 → T3  Core/routines_src/behaviourtrees_src/upkeepers.py
             → HeroAI.utils
T2 → T4  Core/py4gwcorelib_src/AutoInventoryHandler.py
             → Sources.frenkeyLib.ItemHandling.{Items.types, Items.item_snapshot,
                                                Rules.types, BTNodes}   (4 stmts)
T3 → T4  Core/botting_src/subclases_src/INTERACT_src.py
             → Widgets.Blessed
```

Seven files. That is the entire structural debt once placement is corrected.

---

### Root cause — the facade, not the edges

The reason the tiers feel fused in practice is not the 7 files. It is
`Core/__init__.py`, which re-exports the Tier 3 stack into the Tier 1
namespace:

```python
from .SkillManager import *        # line 120
from .BuildMgr import BuildMgr
from .BldMgrBT import BldMgrBT, BTBuildMgr
from .Botting import BottingClass as Botting
```

`SkillManager.py` lines 1–8 are **eager, module-scope** `from HeroAI... import`.
So the chain executes on every import of the facade.

Measured eager import closure of `import Core`:

```
244 project modules
  226  Core
   17  HeroAI          ← incl. all 11 custom_skill_src profession modules,
                          targeting, types, follow.leader_publish
    1  Py4GW_widget_manager
```

There are **876** `from Core import ...` statements across
`Widgets/`, `Sources/`, `Bots/`, and `HeroAI/`. Every one of them eagerly loads
all of HeroAI.

**This is why `HeroAI` behaves as a source rather than a consumer.** It is not a
design decision anywhere in the tree — it is four re-export lines plus eight eager
imports at the top of one file.

### Bonus: a genuine circular import

`Core/botting_src/subclases_src/MULTIBOX_src.py:5` has a module-scope
`from Py4GW_widget_manager import get_widget_handler`, and
`Py4GW_widget_manager.py` imports back into `Core`. The core library
imports the DLL's entry-point host, which imports the core library.

`get_widget_handler` is actually defined in
`py4gwcorelib_src/WidgetManager.py` — the three call sites reach it *through* the
entry-point host by accident of re-export. Two are lazy (function-scope); the
`MULTIBOX_src.py` one is eager.

---

### HeroAI's downward surface — the extraction target

Only these `HeroAI` modules are reached from tier ≤ 2:

| Module | Reached by |
|---|---|
| `HeroAI.types` | enums — `SkillType`, `Skilltarget`, `SkillNature` |
| `HeroAI.custom_skill_src.skill_types` | types — `CustomSkill`, `CastConditions` |
| `HeroAI.targeting` | ~20 pure `Target*` / `GetEnemy*` functions over agent arrays |
| `HeroAI.utils` | `GetEffectAndBuffIds`, `IsPartyMember`, `SameMapOrPartyAsAccount` |
| `HeroAI.cache_data` | `CacheData` — **stateful, the one real coupling** |
| `HeroAI.settings` | `Settings` — stateful |
| `HeroAI.follow.leader_publish` | `FollowFormationPublisher` |
| `HeroAI.hex_removal_src.hex_removal_config` | `load_active_overrides` |

The first four are pure vocabulary and pure functions — mechanical to extract into
a Tier 1/2 leaf package. The last four carry state and need a decision about
ownership rather than a move.

### Housekeeping noted in passing

- `Core/` — 278 orphaned `.pyc`, zero `.py`, untracked
- `Core/BTBuilds/__pycache__/` — 26 stray untracked `.py` files
  (108 on disk vs 82 tracked)
