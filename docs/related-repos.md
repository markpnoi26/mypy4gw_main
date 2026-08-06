# Related repos on this machine

Reference material that is **not** upstream. Nothing here flows into this tree
automatically — no remote, no manifest rule, no sync. You read it, you don't
merge it.

All paths are siblings of this repo under `C:\cygwin64\home\Mark\code\`.

| Repo | `origin` | `upstream` | What it is |
|---|---|---|---|
| `Py4GW_Reforged` | markpnoi26 fork | apoguita | the fork. PR staging; `forwardport.py`'s `--source`. |
| `Py4GW_Reforged_Native` | — | — | the C++ CMake project that builds **this tree's** `Py4GW.dll`. |
| `GW_RE` | — | — | Ghidra 12 bench for the game itself (`Gw.exe` + `Gw.wasm`). |
| `Py4GW-Unchained` | markpnoi26 | Wick-Divinus | a **different fork line** of Py4GW. Python side. |
| `Py4GW-unchained-cpp` | markpnoi26 | sloppynacho | the C++ behind *Unchained's* `Py4GW.dll`. |

Both Unchained clones are Mark's forks with the true upstream wired as a second
remote, so `git fetch upstream && git log HEAD..upstream/main` shows what has
moved. Both forks were level with their upstreams when cloned (2026-08-06).

## Unchained is a sibling fork, not a downstream one

This is the thing to get right before reading a line of it.

Reforged and Unchained are **two independent forks of the same pre-Reforged
ancestor**. Unchained is not built on Reforged and never took its rename pass.
It still roots at `Py4GWCoreLib/` — the name this tree codemods to `Core/` — and
it still carries the legacy native modules that
`.claude/context/hard-rules.md` tells you not to write:

| Unchained (and old Py4GW) | here |
|---|---|
| `Py2DRenderer` | `PyDXOverlay` |
| `PyCombatEvents` | `PyAgentEvents` |
| `PyScanCodeKeystroke` | `PyKeyHandler` |
| `PyPointers` | retired |
| `Point2D` / `Point3D` | `Vec2f` / `Vec3f` |

Unchained *does* have a `Vec2f`, which is a trap: it is a `ctypes.Structure` in
`Py4GWCoreLib/native_src/internals/types.py`, part of their own direct-memory
layer. It is not the Reforged native binding of the same name.

The two DLLs are also different builds with different export surfaces —
13.6 MB here, 9.4 MB there. `Py4GW_Reforged_Native/src/` is organised by
subsystem (`base/`, `GW/`, `imgui/`, `json/`, `listeners/`, `overlay/`,
`settings/`, `system/`, `virtual_input/`); `Py4GW-unchained-cpp/src/` is a flat
sheet of `py_*.cpp`.

**So: no file from Unchained runs here, and no file from here runs there.** Read
it for design, for reverse-engineering knowledge, and for how they solved a
problem. Never copy a module across.

## It is also pre-persistence-jail

Unchained has `Py4GWCoreLib/IniManager.py` — the class this tree is in the middle
of removing — plus 33 `json.load`/`dump` sites, 16 `configparser` references and
49 bare `open(` calls inside its core library. That is exactly the surface
`.claude/context/hard-rules.md` forbids.

When Unchained shows you how to persist something, it is showing you the shape of
the data, not the way to write it. It goes through `Settings` or `JsonFactory`
here, always.

## Where it is genuinely worth reading

**Native UI frames.** Unchained exposes `PyUIManager`, a ~219-method binding over
the game's frame tree (`get_frame_id_by_label`, `get_child_frame_path_by_frame_id`,
`get_frame_clip_rect_by_frame_id`, frame layers, ancestry, UI message logs). This
tree has no such stub. Its `UI_RE/` directory holds ~20 standalone experiments in
cloning and grafting real GW windows — DevText clones, native button harnesses,
frame-callback grafts, encoded-text payload dumps.

That is the closest existing work to `Core/FrameTree/`, `docs/FrameTree_Design.md`
and the `docs/RE/ui_*.md` set. Read it before doing more frame RE from scratch.

Note the standing rule still applies (`.claude/context/runtime-behaviour.md`):
identify frames by `template_type` + `child_offset_id` tree pattern. Unchained
offers `get_frame_id_by_hash` and ships a `frame_aliases.json`; **both are the
anchoring styles we rejected.**

**The C++ side.** `Py4GW-unchained-cpp` is pybind11 over GWCA, same as ours, so
when a question is "how did anyone bind this game function at all", it is a second
answer next to `Py4GW_Reforged_Native`. Its `py_name_obfuscator.cpp`,
`py_inventory_overlay.cpp` and `py_agent_recolor.cpp` cover ground this tree has
RE notes on.

**Their bridge/MCP stack.** `bridge_daemon.py`, `bridge_cli.py`,
`py4gw_mcp_server.py`, `BridgeRuntime/` — compare against `docs/MCP_bridge.md`.

## Do not

- Do not add either as a git remote here. Different lineage; a fetch would only
  invite an accidental merge.
- Do not add them to `layout.toml`. The manifest describes upstream, and these
  are not upstream.
- Do not "port" an Unchained fix by copying the file. Re-implement it against
  `Core/` names and the persistence jail, or it will not even import.
