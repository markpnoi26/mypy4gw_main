---
name: reforged-vs-frenkey-primitives
description: What actually exists in Reforged native bindings vs what `frenkeyLib` pretends still exists. Attribute gotchas (`is_valid` vs `is_inventory_item`), async name decoding, missing salvage state primitives.
---

# Reforged native bindings vs frenkeyLib assumptions

Ground truth lives in `stubs/*.pyi`. `frenkeyLib` was written against an older API and papers over missing calls with `try/except`, which produces silent-nothing behavior in Reforged.

## PyItem attributes — what you actually have

From `stubs/PyItem.pyi:49-98`:

**Exists:** `item_id`, `agent_id`, `agent_item_id`, `name`, `modifiers`, `is_customized`, `item_type`, `dye_info`, `value`, `interaction`, `model_id`, `model_file_id`, `item_formula`, `is_material_salvageable`, `quantity`, `equipped`, `profession`, `slot`, `is_stackable`, `is_inscribable`, `is_material`, `is_zcoin`, `rarity`, `uses`, `is_id_kit`, `is_salvage_kit`, `is_tome`, `is_lesser_kit`, `is_expert_salvage_kit`, `is_perfect_salvage_kit`, `is_weapon`, `is_armor`, `is_salvageable`, `is_inventory_item`, `is_storage_item`, `is_rare_material`, `is_offered_in_trade`, `is_sparkly`, `is_identified`, `is_prefix_upgradable`, `is_suffix_upgradable`, `is_usable`, `is_tradable`, `is_inscription`, `is_rarity_blue/purple/green/gold`.

**Does NOT exist (frenkeyLib uses these):** `is_valid`. That attribute belongs to `frenkeyLib.ItemHandling.Items.item_snapshot.ItemSnapshot`, not `PyItem.PyItem`.

**The trap:** guarding with `if not item.is_valid` throws `AttributeError`; wrap it in `try/except: return False` and every item silently classifies as "invalid" and gets skipped.

**Use `is_inventory_item` instead** as the "does this item still exist in one of my inventory bags" gate.

## PyInventory — what actually exists

From `stubs/PyInventory.pyi:3` (their own note):

> NOTE: `IsSalvaging()`, `IsSalvageTransactionDone()`, `FinishSalvage()` NOT in Reforged.

Also missing: `StartSalvage`, `ContinueSalvage` (see `Scripts/py4gw-examples/salvage.py` which still references them).

**What Reforged does expose:**
- `PyInventory.IdentifyItem(id_kit_id, item_id)` — fire-and-forget
- `PyInventory.Salvage(salv_kit_id, item_id)` — fire-and-forget
- `PyInventory.AcceptSalvageWindow()` — click the materials-confirm dialog

**Consequence:** you cannot ask the game "am I currently salvaging?" or "did the transaction close?" You have to detect completion by polling the item's state (`is_inventory_item`, `quantity`) and the UI window state. See `inventory-actions` for the fire-then-verify recipe.

**Frenkey pretends** (`dev/reference/frenkeyLib/ItemHandling/BTNodes.py::SalvageItem`):

```python
try:
    is_salvaging = bool(inventory_instance.IsSalvaging())
except Exception:
    is_salvaging = False       # ← always False in Reforged
```

Their state machine's completion branch based on `IsSalvaging()` / `IsSalvageTransactionDone()` therefore silently never triggers. Downstream logic falls back to qty-diff / item-gone / windows-closed checks, but the full flow is fragile.

**Rule:** don't route through frenkeyLib for salvage/identify. Use `Routines.Yield.Items.*AndVerify` or `BT.Items.SalvageInventoryItems` / `IdentifyInventoryItems` which are pure Reforged.

## Async name decoding

`Agent.GetNameByID(agent_id)` → `decode_raw(PyAgent.get_agent_enc_name(agent_id))`. Under the hood (`Core/native_src/internals/string_table.py::decode`):

```python
if raw in _decode_cache: return _decode_cache[raw]     # cache hit → fast
if raw in _pending: return ""                          # already decoding
_pending.add(raw)
_decode_pool.submit(_decode_and_cache, raw)            # kick off worker
return ""                                              # first call gets nothing
```

- First call for a given encoded string: returns `""`, dispatches decode to a background thread.
- Next call (usually < 20 ms later): cache hit, returns real string.
- Cache is process-lifetime; same enemy across runs decodes once.

**Impact on the blacklist:** for the first tick after an enemy becomes visible, name-substring matching fails. Prefer encoded-string matching for anything time-sensitive.

## Encoded strings are always available

`Agent.GetEncNameByID(agent_id)` and `Agent.GetEncNameStrByID(agent_id, literal=False)` return synchronously from the agent's own raw bytes — no string-table lookup required. Use these when you need instant, locale-safe identity.

- `literal=True` — returns the exact runtime string, e.g. `"\x171C\x8FE8"` (contains actual bytes 0x17, 0x8F).
- `literal=False` — returns backslash-escaped form, e.g. `"\\x171C\\x8FE8"` — safe to print, safe to copy into Python source.

Use `literal=False` on both the diagnostic-log side and the blacklist-comparison side so string equality works.

## String table backing store

`gw.dat` (the game client's data archive). Read via `read_dat_file_by_hash(file_hash)` (native), parsed by `_parse_string_file`, kept in `_string_table: dict[int, bytes]`. ~100K entries per language. Loaded once per language per process.

## Model ID drift

Model IDs (`SpiritModelID.BLOODSONG` etc.) sometimes drift or don't match what's actually present in a given map/instance. Prefer name-substring OR encoded-string identity. If you must use a model ID, log the actual model IDs from your target enemies once and hardcode the observed values — don't trust the enum labels.

## Frame cache

`@frame_cache` decorators in `py4gwcorelib_src/FrameCache.py` cache results for the current frame. Many Player/Agent/Item helpers use it. Safe assumption: consecutive reads within a tick return the same value; the cache invalidates between frames.

## Rule of thumb

- **New code**: use `PyInventory` / `PyAgent` / `PyItem` primitives directly, or their `Inventory` / `Agent` / `Item` Python wrappers in `Core/`.
- **Never rely** on `IsSalvaging`, `IsSalvageTransactionDone`, `FinishSalvage`, `StartSalvage`, `ContinueSalvage`, or `PyItem.is_valid`.
- **Match ground truth** against `stubs/*.pyi` when in doubt — those track the actual Reforged binding surface.
