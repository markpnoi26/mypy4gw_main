---
name: port-concept
description: Re-implementation discipline for moving a concept between the Reforged line (this tree) and the Unchained line, in either direction. Load before "port X from Unchained" or "contribute X to Unchained".
---

# Porting a concept across fork lines

Reforged and Unchained are sibling forks of the same pre-Reforged ancestor.
A file copied across does not even import: different module roots, different
native names, different DLL export surfaces, opposite persistence rules.
Concepts travel; files do not (RS-008).

## Process

1. **Consult `docs/repo-overlap.md` first.** Find where the concept already
   lives on each side, and whether it is a non-identity pairing (same idea,
   different shape — e.g. `BuildMgr` there vs `BldMgrBT`+`build_src` here).
2. **Read the source side for design**, not for text: what problem it solves,
   what data it keeps, what ordering matters.
3. **Re-implement in the target side's idioms** using the tables below.
4. **Verify on the target side**: import/compile it there, with that repo's
   interpreter and DLL stubs — never trust that it "looks right".
5. Outbound to Unchained ships via the `contribute-unchained` skill.

## Name translation

| Unchained (and old Py4GW) | here (Reforged) |
|---|---|
| `Py4GWCoreLib/` | `Core/` (RS-001) |
| `Py2DRenderer` | `PyDXOverlay` |
| `PyCombatEvents` | `PyAgentEvents` |
| `PyScanCodeKeystroke` | `PyKeyHandler` |
| `Point2D` / `Point3D` | `Vec2f` / `Vec3f` |
| `PyPointers` | retired — no equivalent |
| `Py4GW.Console.*` | `PySystem.Console.*` |

**The `Vec2f` trap:** Unchained *has* a `Vec2f` — a `ctypes.Structure` in
`Py4GWCoreLib/native_src/internals/types.py`, part of their direct-memory
layer. It is not the Reforged native binding. Matching names do not mean
matching things; check the overlap ledger's Table 6.

## Persistence — the shape of the data, not the way to write it

- **Into this tree:** Unchained code persists via `IniManager`, raw `json`,
  `configparser`, `open()`. All forbidden here. Take the data shape, write it
  through `Settings` (INI) or `JsonFactory` (JSON) — hard-rules.md.
- **Into Unchained:** do not export the jail. `Settings`/`JsonFactory` do not
  exist there; use their `IniManager`/raw-json idioms and match the
  surrounding code.

## UI frames

Unchained anchors frames on `frame_hash`, `frame_aliases.json` and
`PyUIManager` label lookups — the styles this repo rejected. When porting
frame logic in, re-anchor on `template_type` + `child_offset_id` tree
patterns (runtime-behaviour.md). When contributing frame logic out, their
anchoring is their convention — follow it there.

## Widgets

Treat every widget as line-specific: the taxonomies are disjoint (414 leaf
modules there, 17 here, 10 names shared). A widget "port" is a rewrite that
keeps only the idea.
