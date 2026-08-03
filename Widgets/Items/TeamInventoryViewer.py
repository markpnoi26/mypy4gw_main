import json
import re
import traceback
from collections import OrderedDict

import PyImGui
import PySystem

from Core import GLOBAL_CACHE
from Core import Color
from Core import ConsoleLog
from Core import ImGui
from Core import JsonFactory
from Core import Map
from Core import Player
from Core import Routines
from Core import ThrottledTimer
from Core import get_texture_for_model
from Core.enums import Bags
from Core.enums_src.Item_enums import Rarity
from Core.Item import Item
from Sources.marks_sources.item_kinds import cluster_key
from Sources.marks_sources.item_kinds import type_label
from Sources.marks_sources.item_naming import NAME_CACHE
from Sources.marks_sources.item_naming import display_name
from Sources.marks_sources.item_naming import learn_base_name
from Sources.marks_sources.item_naming import mod_names
from Sources.marks_sources.item_naming import name_key
from Sources.marks_sources.item_naming import request_names

MODULE_ALIASES = ['Guild Wars/Items & Loot/TeamInventoryViewer.py']
MODULE_NAME = "TeamInventoryViewer"

inventory_write_timer = ThrottledTimer(3000)
inventory_read_timer = ThrottledTimer(5000)

on_first_load = True
all_accounts_search_query = ''
search_query = ''
current_character_name = ''

# Read-side cache the widget iterates when drawing every frame. Populated from the
# shared global JsonFactory doc: every account (current and peers) is just an
# email-keyed subtree of that one document, so no cross-account file access is needed.
TEAM_INVENTORY_CACHE = {}

INVENTORY_BAGS = {
    "Backpack": Bags.Backpack.value,
    "BeltPouch": Bags.BeltPouch.value,
    "Bag1": Bags.Bag1.value,
    "Bag2": Bags.Bag2.value,
    "EquipmentPack": Bags.EquipmentPack.value,
    "EquippedItems": Bags.EquippedItems.value,
}

STORAGE_BAGS = {
    "Storage1": Bags.Storage1.value,
    "Storage2": Bags.Storage2.value,
    "Storage3": Bags.Storage3.value,
    "Storage4": Bags.Storage4.value,
    "Storage5": Bags.Storage5.value,
    "Storage6": Bags.Storage6.value,
    "Storage7": Bags.Storage7.value,
    "Storage8": Bags.Storage8.value,
    "Storage9": Bags.Storage9.value,
    "Storage10": Bags.Storage10.value,
    "Storage11": Bags.Storage11.value,
    "Storage12": Bags.Storage12.value,
    "Storage13": Bags.Storage13.value,
    "Storage14": Bags.Storage14.value,
    "MaterialStorage": Bags.MaterialStorage.value,
}

# endregion


# region JSONStore


# Not 0 and not ItemType.Unknown: 0 is ItemType.Salvage, so a record written before types were
# recorded would file itself under Salvage and sort there, which is worse than admitting ignorance.
UNKNOWN_ITEM_TYPE = -1


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ItemFileIDJSONStore:
    """Shared {encoded singular name: file_id} lookup so any character can render icons straight
    from GW.dat, even for items they've never held themselves.

    Keyed the same way as the name cache. The old model-id-keyed model_file_ids.json is left where
    it is and simply stops being read; it refills under the new key on the next scan.

    Global scope; same multibox-safe merge semantics as the shared name cache.
    """

    FILE = "TeamInventoryViewer/item_file_ids.json"

    def __init__(self):
        self.file = JsonFactory(self.FILE, "global")

    def save_file_id(self, key, file_id):
        if not key or not file_id or file_id <= 0:
            return
        self.file.set(str(key), int(file_id))

    def get(self, key, default=0):
        if not key:
            return default
        return self.file.get_int(str(key), default)


class ItemTypeJSONStore:
    """Shared {encoded singular name: ItemType} lookup so the Type column and the clustered sort work
    on records written by a character who is not logged in.

    Item type is a property of the skin, not of the instance, so one character seeing an item types
    that item for every account. Without this the whole view reads "Unknown" until each character has
    been visited in turn, which is exactly the situation a team viewer exists to avoid.

    Global scope; same multibox-safe merge semantics as the shared name cache.
    """

    FILE = "TeamInventoryViewer/item_types.json"

    def __init__(self):
        self.file = JsonFactory(self.FILE, "global")

    def save_item_type(self, key, item_type):
        if not key or item_type is None or item_type < 0:
            return
        self.file.set(str(key), int(item_type))

    def get(self, key, default=UNKNOWN_ITEM_TYPE):
        if not key:
            return default
        return self.file.get_int(str(key), default)


class ModHashJSONStore:
    """Shared {mod_hash: [prefix, suffix]} map, backed by a global-scope JsonFactory doc."""

    FILE = "TeamInventoryViewer/mod_hash.json"

    def __init__(self):
        self.file = JsonFactory(self.FILE, "global")

    @staticmethod
    def hash_mods(modifiers):
        """Stable 64-bit hash of a list of modifier objects (identifier + args + modbits)."""

        def safe_int(v):
            try:
                return int(v)
            except Exception:
                return 0

        data = []
        for mod in modifiers:
            data.append(
                [
                    safe_int(mod.GetIdentifier()),
                    safe_int(mod.GetArg1()),
                    safe_int(mod.GetArg2()),
                    safe_int(mod.GetArg()),
                    safe_int(mod.GetModBits()),
                ]
            )

        h = 0
        for lst in data:
            for num in lst:
                h = (h * 1315423911) ^ num ^ (h >> 5)
                h &= 0xFFFFFFFFFFFFFFFF

        return hex(h)[2:]

    def save_mod_hash(self, mod_hash, prefix=None, suffix=None):
        if not mod_hash or not (prefix or suffix):
            return
        self.file.set_json(str(mod_hash), [prefix, suffix])

    def get(self, mod_hash, default=None):
        return self.file.get_json(str(mod_hash), default)


class AccountJSONStore:
    """Per-account inventory (Characters + Storage) stored as an email-keyed subtree of
    ONE global-scope JsonFactory document shared by every client.

    Global scope is multibox-safe: each account only ever writes under its OWN email key,
    and the native journal-merge folds every client's subtree into the same file without
    clobbering. Reading a peer is then a normal read of that shared document (its email
    subtree) — no cross-account file access. Write/clear methods still no-op for any email
    other than the current account, so a client never mutates another account's subtree.
    """

    FILE = "TeamInventoryViewer/team_inventory.json"

    def __init__(self, email):
        self.email = email
        current = Player.GetAccountEmail()
        self._is_current = bool(current) and email == current
        self.file = JsonFactory(self.FILE, "global")

    def _fresh_snapshot(self):
        return self.file.get_json(self.email, None) or {
            "Characters": OrderedDict(),
            "Storage": OrderedDict(),
        }

    # ----- Reads -----

    def load(self):
        data = self.file.get_json(self.email, None)
        if not data:
            data = {"Characters": OrderedDict(), "Storage": OrderedDict()}
        TEAM_INVENTORY_CACHE[self.email] = data
        return data

    # ----- Writes (own account subtree only) -----

    def save_bag(self, char_name=None, storage_name=None, bag_name=None, bag_items=None):
        if not self._is_current:
            return
        if bag_items is None:
            bag_items = {}
        if char_name and bag_name:
            path = f"{self.email}/Characters/{char_name}/Inventory/{bag_name}"
        elif storage_name:
            path = f"{self.email}/Storage/{storage_name}"
        else:
            return
        # set_json dedups against the current subtree, so an unchanged assignment is free.
        self.file.set_json(path, dict(bag_items))
        TEAM_INVENTORY_CACHE[self.email] = self._fresh_snapshot()

    def clear_character(self, char_name):
        if not self._is_current:
            return
        if self.file.delete(f"{self.email}/Characters/{char_name}"):
            ConsoleLog("AccountJSONStore", f"Removed character {char_name} from {self.email}.")
        else:
            ConsoleLog("AccountJSONStore", f"[WARN] Character {char_name} not found for {self.email}.")
        TEAM_INVENTORY_CACHE[self.email] = self._fresh_snapshot()

    def clear_account(self):
        if self._is_current:
            # Drop this account's whole subtree from the shared doc.
            self.file.delete(self.email)
        TEAM_INVENTORY_CACHE[self.email] = None
        ConsoleLog("AccountJSONStore", f"Cleared all data for {self.email}.")


class MultiAccountInventoryStore:
    def __init__(self):
        self.file = JsonFactory(AccountJSONStore.FILE, "global")

    def account_store(self, email):
        return AccountJSONStore(email)

    def load_all(self):
        """Populate TEAM_INVENTORY_CACHE from the shared global doc.

        Every account is an email-keyed subtree of the one document, so both the current
        account (freshest in-memory state, including writes made this tick that haven't
        autosaved yet) and every peer come from the same read — no disk scan, no
        cross-account file access.
        """
        current_email = Player.GetAccountEmail()
        if current_email:
            AccountJSONStore(current_email).load()

        for email in self.file.keys(""):
            if not email or email == current_email:
                continue
            AccountJSONStore(email).load()

        return TEAM_INVENTORY_CACHE

    def clear_all_data(self):
        """Wipe every account's subtree from the shared global doc and clear the cache."""
        self.file.set_json("", {})
        TEAM_INVENTORY_CACHE.clear()


multi_store = MultiAccountInventoryStore()
inventory_mod_hash_store = ModHashJSONStore()
inventory_file_ids_store = ItemFileIDJSONStore()
inventory_item_types_store = ItemTypeJSONStore()


ROW_ICON_SIZE = 36.0
ROW_HEIGHT = 40.0
# Sized for "Consumables", the longest label type_label returns.
TYPE_COLUMN_WIDTH = 95.0

# ImGuiTableBgTarget values (RowBg targets paint the whole row's background slot;
# TableFlags.RowBg alternates between RowBg0 and RowBg1 for zebra striping, so we
# override BOTH to keep the rarity tint visible on every row regardless of parity).
_TABLE_BG_TARGET_ROW_BG0 = 1
_TABLE_BG_TARGET_ROW_BG1 = 2

# Alpha kept low so item text stays readable over the tint. Missing/unknown rarity
# yields None from the dict, which short-circuits set_bg_color calls in the helper.
RARITY_ROW_BG = {
    Rarity.White.value: Color(64, 64, 72, 55).to_color(),
    Rarity.Blue.value: Color(60, 130, 220, 55).to_color(),
    Rarity.Purple.value: Color(160, 100, 220, 55).to_color(),
    Rarity.Gold.value: Color(220, 180, 60, 55).to_color(),
    Rarity.Green.value: Color(60, 180, 100, 55).to_color(),
}


def _center_cell_y(num_lines=1):
    """Advance the current cell's cursor so a text block of ``num_lines`` lines
    sits vertically centered in a ROW_HEIGHT row. Call immediately after
    ``table_next_column`` and before the ``PyImGui.text(...)`` call."""
    line_h = PyImGui.get_text_line_height()
    spacing = PyImGui.get_text_line_height_with_spacing() - line_h
    block_h = line_h * num_lines + spacing * max(0, num_lines - 1)
    offset = (ROW_HEIGHT - block_h) / 2.0
    if offset > 0:
        PyImGui.set_cursor_pos_y(PyImGui.get_cursor_pos_y() + offset)


def _apply_rarity_row_bg(info):
    """Tint the current row's background based on the item's stored rarity.

    Must be called immediately after ``PyImGui.table_next_row(...)`` and before any
    per-cell ``table_set_bg_color`` call in the same row. No-ops on missing/unknown
    rarity or when the item is White (no tint).
    """
    color = RARITY_ROW_BG.get(info.get("rarity"))
    if not color:
        return
    PyImGui.table_set_bg_color(_TABLE_BG_TARGET_ROW_BG0, color, -1)
    PyImGui.table_set_bg_color(_TABLE_BG_TARGET_ROW_BG1, color, -1)


def _icon_texture_for(info):
    """Always render from game memory (gwdat://<file_id>) when possible.

    Lookup order:
      1. The item's own stored model_file_id (freshest, gender-correct).
      2. The shared encoded-name → file_id cache (populated by any scan on any
         character, so we get real icons even for items we haven't held).
      3. The not-found placeholder. The PNG atlas rung that used to sit here needed a model id to
         index by, and only ever fired for items neither of the rungs above had seen.
    """
    file_id = int(info.get("model_file_id") or 0)
    if file_id <= 0:
        file_id = inventory_file_ids_store.get(info.get("name_key", ""), 0)
    if file_id > 0:
        return f"gwdat://{file_id}"
    return get_texture_for_model(0)


def item_type_of(info):
    """The same ladder as ``_icon_texture_for``: the item's own recorded type first, then the shared
    encoded-name cache, then unknown."""
    stored = to_int(info.get("item_type"), UNKNOWN_ITEM_TYPE)
    if stored >= 0:
        return stored
    return inventory_item_types_store.get(info.get("name_key", ""), UNKNOWN_ITEM_TYPE)


def clustered_rows(items, item_names):
    """``(name, info, item_type)`` in organize order: type, then rarity band, then name."""
    rows = [(name, items[name], item_type_of(items[name])) for name in item_names if name in items]
    rows.sort(key=lambda row: cluster_key(row[2], row[0], to_int(row[1].get("rarity"), -1)))
    return rows


def draw_type_cell(item_type):
    _center_cell_y(1)
    PyImGui.text(type_label(item_type, "Unknown"))


# region Generators
def get_character_bag_items_coroutine(bag, bag_id, email, char_name, bag_name):
    """Updates recorded_data[email]["Characters"][char_name]["Inventory"][bag_name]"""

    store = AccountJSONStore(email)
    if not email or not char_name:
        return

    bag_items = yield from _collect_bag_items(bag)
    store.save_bag(char_name=char_name, bag_name=bag_name, bag_items=bag_items)


def get_storage_bag_items_coroutine(bag, bag_id, email, storage_name):
    """Updates recorded_data[email]["Storage"][bag_name]"""

    store = AccountJSONStore(email)
    if not email:
        return

    bag_items = yield from _collect_bag_items(bag)
    store.save_bag(storage_name=storage_name, bag_items=bag_items)


def _collect_bag_items(bag):
    """Shared coroutine to fetch all items from a bag with modifier and frenkey DB name support.

    Takes only the bag now: the account, character and bag id were there to look a name up out of the
    PREVIOUS snapshot by model id, which is exactly the rung that made a mislabel permanent.
    """

    def _generate_unique_key(bag_items: dict, base_name: str) -> str:
        if base_name not in bag_items:
            return base_name

        i = 1
        while f"{base_name} #{i}" in bag_items:
            i += 1

        return f"{base_name} #{i}"

    bag_items = OrderedDict()

    # Queue every name up front, in two passes: read the ids out of the native array first, then ask.
    # Nothing here waits for an answer -- an item whose name has not arrived is simply skipped below
    # and picked up on a later pass.
    request_names([item.item_id for item in bag.GetItems() if item and item.model_id and item.item_id])

    # This function must stay a generator. Drop the last yield and `yield from` iterates the returned
    # OrderedDict instead, which is how the viewer silently stopped loading once before.
    yield

    for item in bag.GetItems():
        if not item or item.model_id == 0:
            continue

        model_id = item.model_id
        item_id = item.item_id
        quantity = item.quantity
        slot = item.slot

        # Once per item: name_key builds a PyItem natively and this loop runs over every bag every
        # few seconds. "" until the client has this item's name at all.
        key = name_key(item_id)
        final_name = display_name(item_id, model_id, key)

        # Never seen this encoding, but the name may already be sitting in the cache unread.
        if not final_name:
            prefix, suffix, _inherent = mod_names(item_id, model_id)
            if learn_base_name(item_id, model_id, key):
                key = key or name_key(item_id)
                final_name = display_name(item_id, model_id, key)
                raw_modifiers = GLOBAL_CACHE.Item.Mods.GetModifiers(item_id) or []
                if raw_modifiers:
                    mod_hash = ModHashJSONStore.hash_mods(raw_modifiers)
                    inventory_mod_hash_store.save_mod_hash(mod_hash, prefix, suffix)

        # Still nameless: it stays queued and shows up on a later pass. Never guessed at.
        if not final_name:
            continue

        unique_name = final_name
        if final_name in bag_items:
            unique_name = _generate_unique_key(bag_items, final_name)

        try:
            raw_file_id = int(GLOBAL_CACHE.Item.GetModelFileID(item_id) or 0)
            # Composite items (armor, most weapons) point at a manifest — resolve
            # to the actual texture sub-file for the current character's gender.
            model_file_id = int(Item.GetTrueModelFileID(raw_file_id) or raw_file_id) if raw_file_id > 0 else 0
        except Exception:
            model_file_id = 0

        # Feed the shared encoded-name → file_id cache so other accounts/characters
        # can render this item's icon straight from GW.dat without needing to
        # have held it themselves.
        if model_file_id > 0:
            inventory_file_ids_store.save_file_id(key, model_file_id)

        # Rarity drives the row background tint. Fetched per-instance because it's
        # a live-item property (drop-time value), not a property of the skin.
        try:
            rarity_val = int(GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)[0])
        except Exception:
            rarity_val = -1

        # Type drives both the Type column and the clustered sort. A property of the skin, so it also
        # feeds the shared name-keyed cache for the characters that are not logged in to record it.
        try:
            item_type_val = int(GLOBAL_CACHE.Item.GetItemType(item_id)[0])
        except Exception:
            item_type_val = UNKNOWN_ITEM_TYPE
        if item_type_val >= 0:
            inventory_item_types_store.save_item_type(key, item_type_val)

        # Always insert or update using the unique name
        if unique_name not in bag_items:
            bag_items[unique_name] = OrderedDict(
                {
                    "name_key": key,
                    "model_file_id": model_file_id,
                    "item_type": item_type_val,
                    "rarity": rarity_val,
                    "slot": OrderedDict(),
                }
            )
        else:
            if model_file_id and not bag_items[unique_name].get("model_file_id"):
                bag_items[unique_name]["model_file_id"] = model_file_id
            if rarity_val >= 0 and bag_items[unique_name].get("rarity", -1) < 0:
                bag_items[unique_name]["rarity"] = rarity_val
            if item_type_val >= 0 and to_int(bag_items[unique_name].get("item_type"), -1) < 0:
                bag_items[unique_name]["item_type"] = item_type_val

        bag_items[unique_name]["slot"][str(slot)] = quantity

    return bag_items


def record_account_data():
    global current_character_name

    current_email = Player.GetAccountEmail()
    login_number = GLOBAL_CACHE.Party.Players.GetLoginNumberByAgentID(Player.GetAgentID())
    char_name = GLOBAL_CACHE.Party.Players.GetPlayerNameByLoginNumber(login_number)

    if not current_email or not char_name:
        yield
        return

    current_character_name = char_name
    # Re-read once per pass rather than per item, so names another client discovered still show up.
    NAME_CACHE.load(force=True)
    raw_item_cache = GLOBAL_CACHE.Inventory._raw_item_cache

    for bag_name, bag_id in INVENTORY_BAGS.items():
        bag = raw_item_cache.get_bags([bag_id])[0]
        yield from (
            get_character_bag_items_coroutine(
                bag,
                bag_id,
                current_email,
                char_name=char_name,
                bag_name=bag_name,
            )
        )

    for storage_name, bag_id in STORAGE_BAGS.items():
        if bag_id is None:
            continue
        bag = raw_item_cache.get_bags([bag_id])[0]
        yield from (
            get_storage_bag_items_coroutine(
                bag,
                bag_id,
                current_email,
                storage_name=storage_name,
            )
        )


# region Helper functions
def search(query: str, items: list[str]) -> list[str]:
    """Return items matching partially or with fuzzy similarity."""
    if not query:
        return items

    query = query.lower()

    # --- Partial match first (fast) ---
    partial_matches = [item for item in items if query in item.lower()]

    return sorted(partial_matches)


# region Widget
def draw_widget():
    global TEAM_INVENTORY_CACHE
    global all_accounts_search_query
    global search_query
    global on_first_load

    if on_first_load:
        PyImGui.set_next_window_size(1000, 1250)
        # Window geometry delegated to ImGui native persistence
        on_first_load = False

        TEAM_INVENTORY_CACHE = multi_store.load_all()

    # This triggers a reload of and save of bag data
    if inventory_write_timer.IsExpired() and Routines.Checks.Map.IsOutpost():
        GLOBAL_CACHE.Coroutines.append(record_account_data())
        inventory_write_timer.Reset()

    if inventory_read_timer.IsExpired() and Routines.Checks.Map.IsOutpost():
        TEAM_INVENTORY_CACHE = multi_store.load_all()
        inventory_read_timer.Reset()

    if PyImGui.begin("Team Inventory Viewer"):
        PyImGui.text("Inventory + Storage Viewer")
        PyImGui.separator()

        # === SCROLLABLE AREA START ===
        # Compute space for footer
        available_height = PyImGui.get_window_height() - 190  # leave room for buttons + footer
        PyImGui.begin_child("ScrollableContent", (0.0, float(available_height)), True, 1)

        # === TABS BY ACCOUNT ===
        if TEAM_INVENTORY_CACHE:
            if PyImGui.begin_tab_bar("AccountTabs"):
                # === GLOBAL SEARCH TAB ===
                if PyImGui.begin_tab_item("Search View"):
                    PyImGui.text("Search for items across all accounts")
                    PyImGui.separator()

                    all_accounts_search_query = PyImGui.input_text("##GlobalSearchBar", all_accounts_search_query, 128)
                    PyImGui.separator()

                    if all_accounts_search_query:
                        # === Gather all matching results across accounts ===
                        search_results = []
                        for email, account_data in TEAM_INVENTORY_CACHE.items():
                            # Build a neat identifier like: email â€” [Char1, Char2]
                            character_names = list(account_data.get("Characters", {}).keys())
                            if character_names:
                                character_block = "\n".join(f"   - {name}" for name in character_names)
                                account_label = f"{character_block}"
                            else:
                                account_label = "[No Characters]"

                            # --- Characters ---
                            if "Characters" in account_data:
                                for char_name, char_info in account_data["Characters"].items():
                                    inv_data = char_info.get("Inventory", {})
                                    for bag_name, items in inv_data.items():
                                        for item_name, info in items.items():
                                            if all_accounts_search_query.lower() in item_name.lower():
                                                count = 0
                                                for slot_count in info.get("slot", {}).values():
                                                    count += slot_count
                                                search_results.append(
                                                    {
                                                        "account_label": account_label,
                                                        "email": email,
                                                        "character": char_name,
                                                        "bag": bag_name,
                                                        "item_name": item_name,
                                                        "name_key": info.get("name_key", ""),
                                                        "model_file_id": info.get("model_file_id", 0),
                                                        "rarity": info.get("rarity", -1),
                                                        "item_type": item_type_of(info),
                                                        "count": count or str(info.get('count', 0)),
                                                        "location_type": "Character",
                                                    }
                                                )

                            # --- Storage ---
                            if "Storage" in account_data:
                                for storage_name, items in account_data["Storage"].items():
                                    for item_name, info in items.items():
                                        if all_accounts_search_query.lower() in item_name.lower():
                                            count = 0
                                            for slot_count in info.get("slot", {}).values():
                                                count += slot_count
                                            search_results.append(
                                                {
                                                    "account_label": account_label,
                                                    "email": email,
                                                    "character": None,
                                                    "bag": storage_name,
                                                    "item_name": item_name,
                                                    "name_key": info.get("name_key", ""),
                                                    "model_file_id": info.get("model_file_id", 0),
                                                    "rarity": info.get("rarity", -1),
                                                    "item_type": item_type_of(info),
                                                    "count": count or str(info.get('count', 0)),
                                                    "location_type": "Storage",
                                                }
                                            )

                        # === Display results ===
                        if search_results:
                            search_results.sort(
                                key=lambda entry: cluster_key(
                                    entry["item_type"], entry["item_name"], to_int(entry.get("rarity"), -1)
                                )
                            )
                            if PyImGui.begin_table(
                                "SearchResultsTable",
                                6,
                                PyImGui.TableFlags.Borders | PyImGui.TableFlags.RowBg | PyImGui.TableFlags.ScrollY,
                            ):
                                PyImGui.table_setup_column("Icon", PyImGui.TableColumnFlags.WidthFixed, 40.0)
                                PyImGui.table_setup_column("Item Name", PyImGui.TableColumnFlags.WidthStretch, 1.0)
                                PyImGui.table_setup_column(
                                    "Type", PyImGui.TableColumnFlags.WidthFixed, TYPE_COLUMN_WIDTH
                                )
                                PyImGui.table_setup_column("Count", PyImGui.TableColumnFlags.WidthFixed, 40.0)
                                PyImGui.table_setup_column("Location", PyImGui.TableColumnFlags.WidthFixed, 150.0)
                                PyImGui.table_setup_column("Account", PyImGui.TableColumnFlags.WidthFixed, 150.0)
                                PyImGui.table_headers_row()

                                for index, entry in enumerate(search_results):
                                    texture = _icon_texture_for(entry)

                                    PyImGui.table_next_row(0, ROW_HEIGHT)
                                    _apply_rarity_row_bg(entry)

                                    # === ICON ===
                                    PyImGui.table_next_column()
                                    if texture:
                                        ImGui.DrawTexture(texture, ROW_ICON_SIZE, ROW_ICON_SIZE)
                                    else:
                                        _center_cell_y(1)
                                        PyImGui.text("N/A")

                                    # === ITEM NAME ===
                                    PyImGui.table_next_column()
                                    _center_cell_y(1)
                                    PyImGui.text(re.sub(r'^\d+\s*', '', entry["item_name"]))

                                    # === TYPE ===
                                    PyImGui.table_next_column()
                                    draw_type_cell(entry["item_type"])

                                    # === COUNT ===
                                    PyImGui.table_next_column()
                                    _center_cell_y(1)
                                    PyImGui.text(str(entry.get("count", 0)))

                                    # === LOCATION ===
                                    PyImGui.table_next_column()
                                    _center_cell_y(2)
                                    if entry["location_type"] == "Character":
                                        PyImGui.text(f"{entry['character']}\n  - {entry['bag']}")
                                    else:
                                        PyImGui.text(f"Storage\n  - {entry['bag']}")

                                    # === ACCOUNT IDENTIFIER ===
                                    PyImGui.table_next_column()
                                    _center_cell_y(1)
                                    if PyImGui.collapsing_header(f'{entry["email"]}##{index}'):
                                        PyImGui.text(entry["account_label"])

                                PyImGui.end_table()
                        else:
                            PyImGui.text("No matching items found.")
                    else:
                        PyImGui.text("Type above to search across all accounts.")
                    PyImGui.end_tab_item()
                for email, account_data in TEAM_INVENTORY_CACHE.items():
                    if PyImGui.begin_tab_item(email):
                        PyImGui.text(f"Account: {email}")
                        PyImGui.separator()

                        # === SEARCH BAR ===
                        PyImGui.text("Search Items:")
                        search_query = PyImGui.input_text("##SearchBar", search_query, 128)
                        PyImGui.separator()

                        PyImGui.begin_child(f"Child_{email}")

                        # === CHARACTER INVENTORIES ===
                        if "Characters" in account_data:
                            for char_name, char_info in account_data["Characters"].items():
                                if char_name == "Invalid ID":
                                    continue

                                if PyImGui.collapsing_header(char_name, True):
                                    inv_data = char_info.get("Inventory", {})
                                    ordered_inv_data = {
                                        bag_name: inv_data.get(bag_name, [])
                                        for bag_name in INVENTORY_BAGS.keys()
                                        if bag_name in inv_data
                                    }
                                    for bag_name, items in ordered_inv_data.items():
                                        if not items:
                                            continue

                                        # Filter visible items
                                        item_names = list(items.keys())
                                        filtered_items = item_names
                                        if search_query:
                                            filtered_items = search(search_query, item_names)
                                        if not filtered_items:
                                            continue

                                        PyImGui.text(bag_name)
                                        if PyImGui.begin_table(
                                            f"InvTable_{email}_{char_name}_{bag_name}",
                                            4,
                                            PyImGui.TableFlags.Borders | PyImGui.TableFlags.RowBg,
                                        ):
                                            PyImGui.table_setup_column(
                                                "Icon", PyImGui.TableColumnFlags.WidthFixed, 40.0
                                            )
                                            PyImGui.table_setup_column(
                                                "Item Name", PyImGui.TableColumnFlags.WidthStretch, 1.0
                                            )
                                            PyImGui.table_setup_column(
                                                "Type", PyImGui.TableColumnFlags.WidthFixed, TYPE_COLUMN_WIDTH
                                            )
                                            PyImGui.table_setup_column(
                                                "Count", PyImGui.TableColumnFlags.WidthFixed, 40.0
                                            )
                                            PyImGui.table_headers_row()

                                            for item_name, info, item_type in clustered_rows(items, filtered_items):
                                                texture = _icon_texture_for(info)

                                                PyImGui.table_next_row(0, ROW_HEIGHT)
                                                _apply_rarity_row_bg(info)

                                                # === ICON COLUMN ===
                                                PyImGui.table_next_column()
                                                if texture:
                                                    ImGui.DrawTexture(texture, ROW_ICON_SIZE, ROW_ICON_SIZE)
                                                else:
                                                    _center_cell_y(1)
                                                    PyImGui.text("N/A")

                                                # === ITEM NAME COLUMN ===
                                                PyImGui.table_next_column()
                                                _center_cell_y(1)
                                                PyImGui.text(re.sub(r'^\d+\s*', '', item_name))

                                                # === TYPE COLUMN ===
                                                PyImGui.table_next_column()
                                                draw_type_cell(item_type)

                                                # === COUNT COLUMN ===
                                                PyImGui.table_next_column()
                                                _center_cell_y(1)
                                                count = 0
                                                for slot_count in info.get("slot", {}).values():
                                                    count += slot_count
                                                PyImGui.text(str(count) if count else str(info.get('count', 0)))
                                            PyImGui.end_table()
                                        PyImGui.separator()

                        # === STORAGE SECTION ===
                        if "Storage" in account_data:
                            if PyImGui.collapsing_header("Shared Storage", True):
                                account_storage = account_data.get("Storage", {})
                                ordered_storage_data = {
                                    storage_name: account_storage.get(storage_name, [])
                                    for storage_name in STORAGE_BAGS.keys()
                                    if storage_name in account_storage
                                }
                                for storage_name, items in ordered_storage_data.items():
                                    if not items:
                                        continue

                                    item_names = list(items.keys())
                                    filtered_items = item_names
                                    if search_query:
                                        filtered_items = search(search_query, item_names)
                                    if not filtered_items:
                                        continue

                                    PyImGui.text(storage_name)
                                    if PyImGui.begin_table(
                                        f"StorageTable_{email}_{storage_name}",
                                        4,
                                        PyImGui.TableFlags.Borders | PyImGui.TableFlags.RowBg,
                                    ):
                                        PyImGui.table_setup_column("Icon", PyImGui.TableColumnFlags.WidthFixed, 40.0)
                                        PyImGui.table_setup_column(
                                            "Item Name", PyImGui.TableColumnFlags.WidthStretch, 1.0
                                        )
                                        PyImGui.table_setup_column(
                                            "Type", PyImGui.TableColumnFlags.WidthFixed, TYPE_COLUMN_WIDTH
                                        )
                                        PyImGui.table_setup_column("Count", PyImGui.TableColumnFlags.WidthFixed, 40.0)
                                        PyImGui.table_headers_row()

                                        for item_name, info, item_type in clustered_rows(items, filtered_items):
                                            texture = _icon_texture_for(info)

                                            PyImGui.table_next_row(0, ROW_HEIGHT)
                                            _apply_rarity_row_bg(info)

                                            # === ICON COLUMN ===
                                            PyImGui.table_next_column()
                                            if texture:
                                                ImGui.DrawTexture(texture, ROW_ICON_SIZE, ROW_ICON_SIZE)
                                            else:
                                                _center_cell_y(1)
                                                PyImGui.text("N/A")

                                            # === ITEM NAME COLUMN ===
                                            PyImGui.table_next_column()
                                            _center_cell_y(1)
                                            PyImGui.text(re.sub(r'^\d+\s*', '', item_name))

                                            # === TYPE COLUMN ===
                                            PyImGui.table_next_column()
                                            draw_type_cell(item_type)

                                            # === COUNT COLUMN ===
                                            PyImGui.table_next_column()
                                            _center_cell_y(1)
                                            count = 0
                                            for slot_count in info.get("slot", {}).values():
                                                count += slot_count
                                            PyImGui.text(str(count) if count else str(info.get('count', 0)))
                                        PyImGui.end_table()
                                    PyImGui.separator()

                        PyImGui.end_child()
                        PyImGui.end_tab_item()
                PyImGui.end_tab_bar()
        else:
            PyImGui.text("No recorded accounts found yet.")
        PyImGui.end_child()  # End scrollable section

        PyImGui.separator()
        current_character = f'Current Character: {current_character_name}'
        PyImGui.text(f"{"Waiting for ..." if not current_character_name else current_character}")
        if PyImGui.collapsing_header("Advanced Clearing", True):
            PyImGui.text(
                f'Save timer: {(inventory_write_timer.GetTimeRemaining() / 1000):.1f}(s), Read timer: {(inventory_read_timer.GetTimeRemaining() / 1000):.1f}(s)'
            )
            if PyImGui.begin_table("clear_buttons_table", 3, PyImGui.TableFlags.BordersInnerV):
                # Define colors
                orange_color = Color(255, 165, 0, 255).to_tuple_normalized()  # orange
                orange_hover = Color(255, 200, 50, 255).to_tuple_normalized()
                orange_active = Color(255, 140, 0, 255).to_tuple_normalized()

                red_color = Color(220, 20, 60, 255).to_tuple_normalized()  # crimson red
                red_hover = Color(255, 50, 80, 255).to_tuple_normalized()
                red_active = Color(180, 0, 40, 255).to_tuple_normalized()

                green_color = Color(50, 205, 50, 255).to_tuple_normalized()  # lime green
                green_hover = Color(80, 230, 80, 255).to_tuple_normalized()
                green_active = Color(0, 180, 0, 255).to_tuple_normalized()

                PyImGui.table_next_row()
                # === CLEAR CHARACTER ===
                PyImGui.table_set_column_index(0)
                col_width = PyImGui.get_content_region_avail()[0]
                PyImGui.push_style_color(PyImGui.ImGuiCol.Button, green_color)
                PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, green_hover)
                PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, green_active)
                if PyImGui.button("Clear Character", width=col_width):
                    current_email = Player.GetAccountEmail()
                    login_number = GLOBAL_CACHE.Party.Players.GetLoginNumberByAgentID(Player.GetAgentID())
                    char_name = GLOBAL_CACHE.Party.Players.GetPlayerNameByLoginNumber(login_number)
                    if current_email and char_name:
                        store = AccountJSONStore(current_email)
                        store.clear_character(char_name)
                    else:
                        ConsoleLog("Inventory Recorder", "No data found for this character.")
                PyImGui.pop_style_color(3)

                # === CLEAR CURRENT ACCOUNT ===
                PyImGui.table_set_column_index(1)
                col_width = PyImGui.get_content_region_avail()[0]
                PyImGui.push_style_color(PyImGui.ImGuiCol.Button, orange_color)
                PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, orange_hover)
                PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, orange_active)
                if PyImGui.button("Clear Current account", width=col_width):
                    current_email = Player.GetAccountEmail()
                    if current_email:
                        store = AccountJSONStore(current_email)
                        store.clear_account()
                        TEAM_INVENTORY_CACHE = multi_store.load_all()
                    else:
                        ConsoleLog("Inventory Recorder", "No data found for this account.")
                PyImGui.pop_style_color(3)

                # === CLEAR ALL ACCOUNTS ===
                PyImGui.table_set_column_index(2)
                col_width = PyImGui.get_content_region_avail()[0]
                PyImGui.push_style_color(PyImGui.ImGuiCol.Button, red_color)
                PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, red_hover)
                PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, red_active)
                if PyImGui.button("Clear all accounts", width=col_width):
                    multi_store.clear_all_data()
                    TEAM_INVENTORY_CACHE = multi_store.load_all()
                PyImGui.pop_style_color(3)
                PyImGui.end_table()
    PyImGui.end()

    # Window geometry delegated to ImGui native persistence


def tooltip():
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Team Inventory Viewer", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()

    # Description
    PyImGui.text("This widget allows you to view and search the inventories and storages of all")
    PyImGui.text("your characters across different accounts. It records item data when you are in")
    PyImGui.text("outposts and provides a convenient interface to browse through collected items.")

    PyImGui.spacing()

    # Features
    PyImGui.text_colored("Features:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Records inventories and storages of all characters across accounts.")
    PyImGui.bullet_text("Provides a searchable interface to quickly find items.")
    PyImGui.bullet_text("Displays item icons using model IDs and LootEx textures.")

    PyImGui.spacing()

    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Mark")

    PyImGui.end_tooltip()


def json_tree_view(data):
    # Convert JSON to pretty string
    json_str = json.dumps(data, indent=2)

    # --- In your PyImGui render loop ---
    PyImGui.begin("JSON Viewer", True)

    # Display the JSON string
    PyImGui.text_unformatted(json_str)

    PyImGui.end_child()
    PyImGui.end()


def main():
    try:
        if not Routines.Checks.Map.MapValid() or Map.Pregame.InCharacterSelectScreen():
            # When swapping characters, reset everything
            return

        if Routines.Checks.Map.IsMapReady():
            draw_widget()

    except ImportError as e:
        PySystem.Console.Log(MODULE_NAME, f"ImportError encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    except ValueError as e:
        PySystem.Console.Log(MODULE_NAME, f"ValueError encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    except TypeError as e:
        PySystem.Console.Log(MODULE_NAME, f"TypeError encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    except Exception as e:
        # Catch-all for any other unexpected exceptions
        PySystem.Console.Log(MODULE_NAME, f"Unexpected error encountered: {str(e)}", PySystem.Console.MessageType.Error)
        PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)
    finally:
        pass


if __name__ == "__main__":
    main()
