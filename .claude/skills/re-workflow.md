---
name: re-workflow
description: Reverse-engineering the Guild Wars client to add or fix a native function — WASM-first procedure, Ghidra MCP usage, offset/pattern JSON, UI message dispatch. Load before investigating game internals, resolving an address, or when a needed binding does not exist anywhere in Reforged Native.
---

# RE workflow

Entry point for everything here: `docs/RE/reverse_engineering_reference.md`.
Translation procedure with worked examples: `docs/RE/CPP_WASM_MAPPING.md`.

## WASM first — this is the whole trick

Reverse-engineer on `/Gw.wasm`, then map the confirmed result to `/Gw.exe`.

The WASM build **retains full debug symbols** (`CCharAgent::GetConsiderColor`,
`FrameCreate`, `CtlTextMl::Markup`, …). Behaviour, control flow, struct fields
and call chains are dramatically faster and less error-prone to read there. The
EXE is stripped — everything is `FUN_xxxxxxxx`.

**Enter the EXE only at the end**, to resolve the concrete address the injector
needs. Reading architecture in the EXE first is slow and mistake-prone; this is
the single biggest time sink to avoid.

Genuine ABI differences to re-confirm on the EXE: WASM `call_indirect` table
indices are not x86 pointers, and structs may be repacked (watch `Color4b`
channel order). The *architecture* transfers; low-level calling convention does
not.

## Ghidra MCP — always pass `program` explicitly

The project contains multiple same-named `Gw.exe` images. **A call that omits
`program` silently hits the wrong one** and gives plausible, wrong answers.

| Image | Detail |
|---|---|
| `/Gw.exe(Symbols)` | 18,017 functions, x86:LE:32, base `0x00400000` |
| `/Gw.wasm` | 18,004 functions, Wasm:LE:32, base `ram:80000000` |

## Where things live

| Layer | Path |
|---|---|
| C++ Reforged Native (primary) | `../Py4GW_Reforged_Native/src/GW/<module>/` + `include/GW/<module>/` |
| Pattern/offset data | `../Py4GW_Reforged_Native/offsets/<module>.json` |
| C++ legacy GWCA (cross-ref only) | `../Py4GW/vendor/gwca` |
| Python native | `Core/native_src/` (`methods/`, `internals/native_function.py`) |
| Python scanner | `Core/Scanner.py` — `FindAssertion`, `FindInRange`, `ToFunctionStart` |

Addresses are resolved at runtime from byte patterns + step resolvers in the
offsets JSON. **Never hardcode an address** into shipped code. Each module's
`<module>_patterns.cpp` holds its `Resolve*` functions and declares named
ownership of every symbol it resolves.

Reforged Native's own docs: `docs/06-pattern-json-system.md`,
`docs/module-migration-guide.md`, `docs/gwca-manager-dependency-map.md`.

## Quick address reference

| GWCA name | WASM symbol | EXE address |
|---|---|---|
| `DoWorldActon_Func` | `CoreActionExecuteWorldAction` | `0x0050e5e0` |
| `CallTarget_Func` | `CharCliPlayerOrderAlertSimple` | `0x00917740` |
| `ChangeTarget_Func` | `IAgentView::SetSelections` | `0x007e0f60` |
| `MoveTo_Func` | `IUi::Game::Walk*` | `0x00534fa0` |
| `SendAgentDialog_Func` | (thunk) | `0x008105b0` |

Full catalog with sub-function breakdowns in the reference doc.

## UI message system

Dispatch is a **hash table** (`THashTable<IFrame::Msg::CHandler>` at
`DAT_ram_005a0338`), not a switch. Do not go looking for a jump table.

| Range | Meaning |
|---|---|
| `0x00`–`0x55` | base frame lifecycle |
| `0x100000xx` | server→client notifications (~90 mapped, ~15 unknown) |
| `0x300000xx` | client→server commands (~30 mapped, all send-to-server) |

Authoritative enum: `enum class UIMessage : uint32_t` in
`../Py4GW_Reforged_Native/include/GW/common/constants/ui.h` (aliased
`GW::ui::UIMessage`). The GWCA enum in `vendor/gwca/.../UIMgr.h` is
cross-reference only.

To find unmapped messages: hook the send path at runtime (Reforged registers UI
message callbacks) or run a Ghidra script over WASM callers of
`FrameMsgSendRegistered`. Procedure in reference doc §4.

## Frame identification

Never anchor on frame hash, alias name, or a fixed child-offset path — match on
`template_type` + offset pattern instead. (Learned the hard way; see memory
`feedback_ui_frame_identification`.)

## Other RE docs

- `docs/RE/rosetta_stone.txt` — GwA2 (AutoIt) → Py4GW mapping
- `docs/RE/gw_combat_ai_reverse_engineering.md` — combat AI analysis
- `docs/RE/native_gw_ui_function_catalog.json` — UI functions with addresses
- `docs/RE/native_gw_window_creation_investigation.md` — window proc creation
- `docs/RE/native_ui_title_and_encoded_string_reference.md` — title/encoding
- `docs/RE/name_tag_color_reverse_engineering.md` — worked end-to-end example:
  the `GetConsiderColor` resolver detour recipe and ABI. Hook the **resolver**
  `FUN_007f02e0`; the wrapper `FUN_007d9cf0` is only an anchor. Shipped feature
  guide: `docs/agent_name_tag_color.md`. Test harness:
  `tests/name_tag_color/name_tag_color_test.py`.
