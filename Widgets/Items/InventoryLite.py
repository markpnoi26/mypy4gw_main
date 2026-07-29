"""Lightweight inventory helper: auto-identify, manual salvage, organize, Xunlai chest."""

import time

import PyImGui
import PyInventory

from Core import ImGui
from Core.enums_src.Item_enums import INVENTORY_BAGS
from Core.enums_src.Item_enums import ItemType
from Core.GlobalCache import GLOBAL_CACHE
from Core.Inventory import Inventory
from Core.Item import Item
from Core.Py4GWcorelib import Console
from Core.Py4GWcorelib import ConsoleLog
from Core.py4gwcorelib_src.Settings import Settings
from Core.Routines import Routines
from Core.UIManager import UIManager
from Core.UIManager import WindowFrame

MODULE_NAME = "Inventory Lite"
MODULE_CATEGORY = "Items"
MODULE_TAGS = ["Items", "Inventory"]

MAX_STACK = 250
MOVE_DELAY_MS = 150
AUTO_IDENTIFY_INTERVAL_MS = 2500
BUSY_TIMEOUT_MS = 300000
IDENTIFY_RARITIES = ("Blue", "Purple", "Gold")
SALVAGE_RARITIES = ("White", "Blue")

SORT_TYPE_ORDER = [
    int(ItemType.Kit),
    int(ItemType.Key),
    int(ItemType.Usable),
    int(ItemType.Trophy),
    int(ItemType.Quest_Item),
    int(ItemType.Materials_Zcoins),
]

SETTINGS_SECTION = "InventoryLite"


def item_type_rank(item_id: int) -> int:
    type_value = Item.GetItemType(item_id)[0]
    if type_value in SORT_TYPE_ORDER:
        return SORT_TYPE_ORDER.index(type_value)
    return len(SORT_TYPE_ORDER) + type_value


def rarity_name(item_id: int) -> str:
    return Item.Rarity.GetRarity(item_id)[1]


def inventory_item_ids() -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for item_id in GLOBAL_CACHE.Inventory.GetAllInventoryItemIds():
        if item_id and item_id not in seen:
            seen.add(item_id)
            unique.append(item_id)
    return unique


def unidentified_candidates() -> list[int]:
    return [
        item_id
        for item_id in inventory_item_ids()
        if not Item.Usage.IsIdentified(item_id) and rarity_name(item_id) in IDENTIFY_RARITIES
    ]


def salvage_candidates() -> list[int]:
    return [
        item_id
        for item_id in inventory_item_ids()
        if Item.Usage.IsSalvageable(item_id)
        and Item.Usage.IsIdentified(item_id)
        and rarity_name(item_id) in SALVAGE_RARITIES
    ]


def bag_layout() -> tuple[list[tuple[int, int]], list[int]]:
    """Flat slot positions across the four inventory bags, and the item id in each."""
    positions: list[tuple[int, int]] = []
    item_ids: list[int] = []
    for bag_enum in INVENTORY_BAGS:
        try:
            bag = PyInventory.Bag(bag_enum.value, bag_enum.name)
            size = int(bag.GetSize())
            occupied = {int(item.slot): int(item.item_id) for item in bag.GetItems()}
        except Exception:
            continue
        for slot in range(size):
            positions.append((bag_enum.value, slot))
            item_ids.append(occupied.get(slot, 0))
    return positions, item_ids


def condense_stacks():
    """Merge partial stacks of the same model into as few slots as possible."""
    positions, item_ids = bag_layout()

    partials: list[dict] = []
    for index, item_id in enumerate(item_ids):
        if item_id == 0 or not Item.Properties.IsStackable(item_id):
            continue
        quantity = Item.Properties.GetQuantity(item_id)
        if quantity <= 0 or quantity >= MAX_STACK:
            continue
        bag_id, slot = positions[index]
        partials.append(
            {
                "item_id": item_id,
                "bag_id": bag_id,
                "slot": slot,
                "model_id": Item.GetModelID(item_id),
                "dye": Item.GetDyeColor(item_id),
                "quantity": quantity,
            }
        )

    groups: dict[tuple[int, int], list[dict]] = {}
    for entry in partials:
        groups.setdefault((entry["model_id"], entry["dye"]), []).append(entry)

    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda entry: entry["quantity"], reverse=True)
        for source in reversed(group):
            if source["quantity"] <= 0:
                continue
            for target in group:
                if target is source or target["quantity"] <= 0 or target["quantity"] >= MAX_STACK:
                    continue
                move_amount = min(MAX_STACK - target["quantity"], source["quantity"])
                if move_amount <= 0:
                    continue
                Inventory.MoveItem(source["item_id"], target["bag_id"], target["slot"], move_amount)
                target["quantity"] += move_amount
                source["quantity"] -= move_amount
                yield from Routines.Yield.wait(MOVE_DELAY_MS)
                if source["quantity"] <= 0:
                    break


def sort_bags():
    """Reorder inventory into LootEx's ordering: type group, then model, quantity, rarity, value."""
    positions, item_ids = bag_layout()

    desired = sorted(
        item_ids,
        key=lambda item_id: (
            item_id == 0,
            item_type_rank(item_id) if item_id else 0,
            Item.GetModelID(item_id) if item_id else 0,
            -Item.Properties.GetQuantity(item_id) if item_id else 0,
            -Item.Rarity.GetRarity(item_id)[0] if item_id else 0,
            -Item.Properties.GetValue(item_id) if item_id else 0,
            Item.GetDyeColor(item_id) if item_id else 0,
            item_id,
        ),
    )

    for index, item_id in enumerate(desired):
        if item_id == 0 or item_ids[index] == item_id:
            continue
        bag_id, slot = positions[index]
        Inventory.MoveItem(item_id, bag_id, slot, Item.Properties.GetQuantity(item_id))
        yield from Routines.Yield.wait(MOVE_DELAY_MS)


class InventoryLite:
    def __init__(self):
        self.initialized = False
        self.active = None
        self.active_label = ""
        self.active_since = 0.0
        self.auto_identify = True
        self.show_config = False
        self.last_identify_check = 0.0

    @property
    def busy(self) -> bool:
        return self.active is not None

    def settings_handler(self):
        handler = Settings(MODULE_NAME, scope="account")
        return handler if handler.is_ready() else None

    def load_settings(self):
        handler = self.settings_handler()
        if handler is None:
            return False
        self.auto_identify = handler.get_bool(SETTINGS_SECTION, "AutoIdentify", True)
        return True

    def save_settings(self):
        handler = self.settings_handler()
        if handler is None:
            return
        handler.set_bool(SETTINGS_SECTION, "AutoIdentify", self.auto_identify)
        handler.save()

    def run(self, routine, label: str):
        if self.busy:
            return
        self.active = routine
        self.active_label = label
        self.active_since = time.monotonic()

    def release(self):
        if self.active is not None:
            self.active.close()
        self.active = None
        self.active_label = ""

    def pump(self):
        """Drive our own generator. GLOBAL_CACHE.Coroutines is only pumped by the
        Environment Upkeeper widget, so relying on it would make this widget silently
        inert whenever that one is disabled."""
        if self.active is None:
            return
        if (time.monotonic() - self.active_since) * 1000.0 >= BUSY_TIMEOUT_MS:
            ConsoleLog(
                MODULE_NAME,
                f"{self.active_label} exceeded {BUSY_TIMEOUT_MS} ms and was released.",
                Console.MessageType.Warning,
            )
            self.release()
            return
        try:
            next(self.active)
        except StopIteration:
            self.release()
        except Exception as exc:
            ConsoleLog(MODULE_NAME, f"{self.active_label} failed: {exc}", Console.MessageType.Error)
            self.release()

    def tick_auto_identify(self):
        if not self.auto_identify or self.busy:
            return
        now = time.monotonic() * 1000.0
        if now - self.last_identify_check < AUTO_IDENTIFY_INTERVAL_MS:
            return
        self.last_identify_check = now

        candidates = unidentified_candidates()
        if not candidates:
            return
        if GLOBAL_CACHE.Inventory.GetFirstIDKit() == 0:
            return
        self.run(Routines.Yield.Items.IdentifyItemsAndVerify(candidates), "Identify")

    def start_salvage(self):
        candidates = salvage_candidates()
        if not candidates:
            ConsoleLog(MODULE_NAME, "Nothing to salvage.", Console.MessageType.Info)
            return
        if GLOBAL_CACHE.Inventory.GetFirstSalvageKit() == 0:
            ConsoleLog(MODULE_NAME, "Out of salvage kits.", Console.MessageType.Warning)
            return
        self.run(Routines.Yield.Items.SalvageItemsAndVerify(candidates), "Salvage")

    def start_organize(self):
        self.run(self.organize(), "Organize")

    def organize(self):
        yield from condense_stacks()
        yield from sort_bags()

    def draw_buttons(self):
        frame = WindowFrame.InventoryBags
        if not frame.FrameExists():
            return
        frame_id = frame.GetFrameID()
        if not frame_id:
            return
        left, top, right, bottom = UIManager.GetFrameCoords(frame_id)
        if right <= left:
            return

        PyImGui.set_next_window_pos(left, bottom + 2)
        flags = (
            PyImGui.WindowFlags.AlwaysAutoResize
            | PyImGui.WindowFlags.NoTitleBar
            | PyImGui.WindowFlags.NoResize
            | PyImGui.WindowFlags.NoScrollbar
        )
        if PyImGui.begin(f"##{MODULE_NAME}Bar", flags):
            if self.busy:
                PyImGui.text_disabled("working...")
            else:
                if PyImGui.button("Salvage"):
                    self.start_salvage()
                PyImGui.same_line(0, 4)
                if PyImGui.button("Organize"):
                    self.start_organize()
                PyImGui.same_line(0, 4)
                if PyImGui.button("Xunlai"):
                    Inventory.OpenXunlaiWindow()
        PyImGui.end()

    def draw_config(self):
        if not self.show_config:
            return
        visible, still_open = PyImGui.begin(
            f"{MODULE_NAME} Config", self.show_config, PyImGui.WindowFlags.AlwaysAutoResize
        )
        if visible:
            auto_identify = ImGui.checkbox("Auto-Identify (Blue/Purple/Gold)", self.auto_identify)
            if auto_identify != self.auto_identify:
                self.auto_identify = auto_identify
                self.save_settings()
            PyImGui.text_disabled(f"Salvage button: {', '.join(SALVAGE_RARITIES)} only.")
            PyImGui.text_disabled("Organize condenses stacks, then sorts bags.")
            PyImGui.separator()
            PyImGui.text_disabled("Identify and Salvage need the Environment Upkeeper")
            PyImGui.text_disabled("widget enabled - it is the only queue processor.")
            PyImGui.text_disabled("Organize works standalone.")
        PyImGui.end()
        if not still_open:
            self.show_config = False


widget = InventoryLite()


def configure():
    widget.show_config = True


def main():
    if not widget.initialized:
        if not widget.load_settings():
            return
        widget.initialized = True

    widget.pump()
    widget.tick_auto_identify()
    widget.draw_buttons()
    widget.draw_config()


if __name__ == "__main__":
    main()
