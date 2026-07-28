---
name: inventory-actions
description: Reliable identify / salvage / loot from Python. Covers the working primitives, why `AutoInventoryHandler` is fragile, dedup pitfalls, anti-abuse throttling, and the fire-then-verify pattern.
---

# Inventory actions (identify / salvage / loot)

## Use these entry points

- `BT.Items.IdentifyInventoryItems(rarities=None, ...)` — BT-native.
- `BT.Items.SalvageInventoryItems(rarities=None, ...)` — BT-native.
- `Routines.Yield.Items.IdentifyItemsAndVerify(item_ids, ...)` — yield-based generator, if you need to drive it manually.
- `Routines.Yield.Items.SalvageItemsAndVerify(item_ids, ...)` — same.

Both `AndVerify` variants use fire-then-verify (see below). The BT wrappers auto-collect the inventory item IDs on first tick.

## Do NOT use for headless bots

- `AutoInventoryHandler().IDAndSalvageItems()` — routes through `frenkeyLib.BTNodes` which internally calls `IsSalvaging()` / `IsSalvageTransactionDone()` / `FinishSalvage()` — **none of which exist in Reforged** (see `stubs/PyInventory.pyi:3`). Frenkey wraps them in `try/except` so they silently no-op; the completion detection then breaks in subtle ways.
- `Routines.Yield.Items.SalvageItems` (no `AndVerify`) — legacy version that queues everything into `ActionQueueManager` and waits for the queue to drain. Doesn't detect actual salvage completion, uses blunt sleep between items.

## Rarity defaults

- Identify: `["Blue", "Purple", "Gold"]` — whites are already `is_identified=True` in-game, so scanning them is wasted work.
- Salvage: `["White", "Blue", "Purple", "Gold"]` — whites salvage into common materials.

## The fire-then-verify pattern (why we don't just sleep)

Each `PyInventory.Salvage(kit, item)` opens a server-side transaction. Firing another salvage before the first resolves either:
1. Trips anti-abuse detection → **disconnect**.
2. Corrupts state (second call targets wrong item).
3. Silently drops (rejected while modal is up).

Recipe per item:

```
1. AddAction("SALVAGE", Inventory.SalvageItem, item_id, kit_id)   # fire once
2. yield from _wait_for_empty_queue("SALVAGE", timeout_ms=5000)   # packet actually sent
3. fired_at = time.monotonic()
4. loop:
     yield from wait(50)                                          # poll interval
     if not item_still_present(item_id): break SUCCESS            # single item consumed
     if item.quantity < initial_qty:    break SUCCESS             # stack decremented
     if needs_confirm and materials_window_open and not clicked:
         AddAction("SALVAGE", Inventory.AcceptSalvageMaterialsWindow)
         clicked = now
     if (now - fired_at) * 1000 >= per_item_timeout_ms: break TIMEOUT
5. yield from wait(per_item_delay_ms)                             # small settle before next
```

Full implementation: `Core/routines_src/yield_src/items.py::SalvageItemsAndVerify`.

## Collection pitfall: bag iteration returns duplicates

`Routines.Items.GetSalvageableItems` iterates `range(Bags.Backpack, Bags.Bag2+1)` calling `PyInventory.Bag(id).GetItems()` per bag. In practice this returns the same `item_id` across multiple bag IDs — you get `[56, 56, 56, 56]` for what should be four distinct items. First salvage succeeds, next three iterations see `item_id=56` gone and skip.

**Fix**: use `GLOBAL_CACHE.Inventory.GetAllInventoryItemIds()` + a `seen: set[int]` dedupe. That's what `BT.Items.SalvageInventoryItems` / `IdentifyInventoryItems` do internally.

## The `is_valid` trap

`PyItem` in Reforged (see `stubs/PyItem.pyi`) does NOT expose an `is_valid` attribute — that's a frenkeyLib `ItemSnapshot` field. A guard like `item.is_valid` throws `AttributeError`. If it's wrapped in `try/except: return False`, every item gets silently classified as invalid and skipped.

**What PyItem actually exposes**: `is_inventory_item`, `is_identified`, `is_salvageable`, `is_salvage_kit`, `is_id_kit`, `quantity`, `model_id`, `rarity`, `slot`. Use `is_inventory_item` as the "still exists" gate.

## Anti-abuse throttling knobs

`ActionQueueManager` has per-queue throttle_time (packet spacing):
- `SALVAGE` queue = 125ms
- `IDENTIFY` queue = 150ms

Plus our own `per_item_delay_ms` between items in the `AndVerify` loops:
- Salvage default: 100ms (game usually completes salvage inside the polling loop first)
- Identify default: 50ms

Bump both if you see disconnects. Don't drop the `per_item_timeout_ms` guard (default 3000 salvage / 2000 identify) — it's a safety valve against hung UI windows.

## Materials confirmation dialog

Purple/Gold salvage triggers a "Salvage into materials?" confirmation. Detect and click:

```python
def is_materials_confirm_window_open() -> bool:
    from ...UIManager import UIManager
    parent_hash = 140452905
    yes_button_offsets = [6, 110, 6]
    frame_id = UIManager.GetChildFrameID(parent_hash, yes_button_offsets)
    return bool(frame_id) and UIManager.FrameExists(frame_id)

# In the polling loop, when needs_confirm and window open:
ActionQueueManager().AddAction("SALVAGE", Inventory.AcceptSalvageMaterialsWindow)
```

The BT wrappers handle this automatically.

## Kit availability

`GLOBAL_CACHE.Inventory.GetFirstSalvageKit()` and `GetFirstIDKit()` return `0` when the bag has none. Bail on that:

```python
kit_id = GLOBAL_CACHE.Inventory.GetFirstSalvageKit()
if kit_id == 0:
    ConsoleLog(..., "Out of salvage kits.")
    break
```

For a farming loop, purchase 5+ kits during a merchant step in the outpost cycle. Use `Routines.Yield.Merchant.BuySalvageKits(n)` / `BuyIDKits(n)`.

## Old FSM utility layer still works — via `RunGenerator`

`Scripts/py4gw-marks-corner/lib/loot_utils.py::identify_and_salvage_items()` is a yield generator that wraps the identify + salvage pass. If porting a bot that used it, keep the util working by driving it inside a `RunGenerator(...)` BT adapter (see `migrate-bot-to-bottingtree`). But new bots should prefer the BT-native `BT.Items.*` versions.

## `AutoInventoryHandler` singleton — if you must use it

Custom bots run with `module_active=False` (widget disabled), so nothing populates the handler's flags. If you route through it anyway, prime the singleton:

```python
handler = AutoInventoryHandler()
handler.salvage_whites = True
handler.salvage_blues = True
handler.salvage_purples = True
handler.salvage_golds = salvage_golds
handler.id_whites = True; handler.id_blues = True; ...
```

Otherwise `SalvageItems` skips every item (`respect_settings=True` gate + all flags False by default).
