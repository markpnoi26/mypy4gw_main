---
name: reforged-native-bindings
description: Calling into the game from Python — the Py* embedded modules, ctypes context structs, and the legacy GWCA to Reforged Native rename migration. Load before touching Core/native_src/, stubs/*.pyi, anything named Py<Something>, or when a binding that "should exist" is missing.
---

# Reforged native bindings

The rename table is in `.claude/context/hard-rules.md` (always loaded). This is
the architecture around it. Session log: `docs/migration_to_reforged/`.

## Where the DLL comes from

`Py4GW.dll` is built by a **separate sibling C++ project**,
`Py4GW_Reforged_Native` (`../Py4GW_Reforged_Native`) — a 32-bit injected DLL
embedding CPython via pybind11, hooking D3D9, rendering ImGui. It is a
ground-up replacement for the legacy GWCA backend, itself still under parity
migration (GWCA managers → `GW/<module>/`).

Its build is CMake (`cmake -S . -B build -A Win32`, preset `vs2022-win32`).
**No build command in this Python repo touches it.** If a binding is missing,
the fix lives in that repo, not here.

## Two data paths

| Path | Mechanism | Lives in |
|---|---|---|
| **bindings** | `Py*` embedded modules, pybind11 | type stubs in `stubs/*.pyi` |
| **context** | ctypes structs read from shared memory | `Core/native_src/context` |

They are not interchangeable. The context path reads game memory layout
directly, so it breaks on client patches; the bindings path goes through
resolved functions. When something reads suspiciously fast and has no
corresponding `Py*` call, it is the context path.

## Reforged style differs from legacy

Reforged `Py*` classes favor **getter methods and module-level functions** over
the legacy data-field style. Code written against GWCA-era bindings will look
like attribute access where Reforged wants a call. When porting, check
`stubs/*.pyi` for the actual surface rather than assuming the old shape.

## frenkeyLib is not a binding layer

`dev/reference/frenkeyLib/` (85 files) is a compatibility shim that **pretends legacy
primitives still exist**. Attribute gotchas (`is_valid` vs `is_inventory_item`),
async name decoding, missing salvage-state primitives. Covered in detail by the
`reforged-vs-frenkey-primitives` skill — load that one when working in
`Sources/`.

## When a binding is missing

Order of investigation:

1. `stubs/*.pyi` — is it declared? If yes, it exists; your call shape is wrong.
2. `Core/native_src/` — is there already a Python-side wrapper?
3. `../Py4GW_Reforged_Native/src/GW/<module>/` — is the C++ side migrated?
   Each module declares named ownership of every resolved symbol;
   `<module>_patterns.cpp` holds the `Resolve*` functions.
4. `../Py4GW_Reforged_Native/offsets/<module>.json` — runtime addresses come
   from byte patterns and step resolvers here, **never hardcoded**.

If it genuinely does not exist, that is an RE task → skill: `re-workflow`.

## Cross-reference, not source of truth

The legacy GWCA tree at `../Py4GW/vendor/gwca` still exists and is useful for
understanding how a subsystem worked pre-Reforged. It is **not** authoritative
anymore. Read it to learn intent; implement against Reforged.
