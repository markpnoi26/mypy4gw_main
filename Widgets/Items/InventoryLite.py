"""Lightweight inventory helper: auto-identify, salvage, organize, and its own item filtering.

Facts about an item come from its modifier words matched against the LootEx mod tables, and rules
match those facts in pure Python. Naming -- the mod tables, the shared model_id cache and the
resolution ladder -- lives in `Sources.marks_sources.item_naming`, shared with TeamInventoryViewer.
"""

import re
import time
import traceback
from dataclasses import dataclass
from dataclasses import field

import PyImGui
import PyInventory

from Core import ImGui
from Core.enums_src.Item_enums import INVENTORY_BAGS
from Core.enums_src.Item_enums import STORAGE_BAGS
from Core.enums_src.Item_enums import ItemType
from Core.GlobalCache import GLOBAL_CACHE
from Core.Inventory import Inventory
from Core.Py4GWcorelib import ActionQueueManager
from Core.Py4GWcorelib import Console
from Core.Py4GWcorelib import ConsoleLog
from Core.py4gwcorelib_src.Settings import Settings
from Core.Routines import Routines
from Core.UIManager import UIManager
from Core.UIManager import WindowFrame
from Sources.marks_sources.item_naming import NAME_CACHE
from Sources.marks_sources.item_naming import fetch_base_name
from Sources.marks_sources.item_naming import known_base_name
from Sources.marks_sources.item_naming import mod_database
from Sources.marks_sources.item_naming import mod_display_name
from Sources.marks_sources.item_naming import parse_item_mods
from Sources.marks_sources.item_naming import request_names

MODULE_NAME = "Inventory Lite"
MODULE_CATEGORY = "Items"
MODULE_TAGS = ["Items", "Inventory"]

MAX_STACK = 250
MOVE_DELAY_MS = 150
AUTO_IDENTIFY_INTERVAL_MS = 2500
BUSY_TIMEOUT_MS = 300000
IDENTIFY_RARITIES = ("Blue", "Purple", "Gold")
SALVAGE_RARITIES = ("White", "Blue")

POLL_MS = 50
STORAGE_OPEN_TIMEOUT_MS = 4000
DEPOSIT_CONFIRM_TIMEOUT_MS = 2500
MAX_MOVES_PER_ITEM = 8
DISPLAY_LINE_CAP = 220
REPORT_LINES = 40
REPORT_HEIGHT = 320.0
RARITY_NAMES = ("White", "Blue", "Purple", "Gold", "Green")

SCAN_COLUMNS = ("Name", "Prefix", "Suffix", "Inherent", "Req", "Rarity", "Max", "Qty")
PREVIEW_COLUMNS = ("Do",) + SCAN_COLUMNS + ("Rule", "Why")

DEPOSIT_MOVED = "moved"
DEPOSIT_FULL = "full"
DEPOSIT_UNCONFIRMED = "unconfirmed"

# The organize order, deliberately explicit. Anything not listed sorts after everything here, by its
# raw ItemType value -- the enum's own order is arbitrary, which is why it is not relied on.
#
# The five meta types (Weapon, MartialWeapon, OffhandOrShield, EquippableItem, SpellcastingWeapon) are
# filter groupings and never appear on a real item, so they are absent on purpose.
SORT_TYPE_ORDER = [
    # tools and keys first -- the things reached for most
    int(ItemType.Kit),
    int(ItemType.Key),
    # consumables together
    int(ItemType.Usable),
    int(ItemType.Scroll),
    int(ItemType.Storybook),
    # dyes together, ordered by colour further down the key
    int(ItemType.Dye),
    # weapons: martial, then spellcasting, then off-hand
    int(ItemType.Axe),
    int(ItemType.Sword),
    int(ItemType.Hammer),
    int(ItemType.Daggers),
    int(ItemType.Scythe),
    int(ItemType.Spear),
    int(ItemType.Bow),
    int(ItemType.Staff),
    int(ItemType.Wand),
    int(ItemType.Offhand),
    int(ItemType.Shield),
    # armour, head to foot
    int(ItemType.Headpiece),
    int(ItemType.Chestpiece),
    int(ItemType.Gloves),
    int(ItemType.Leggings),
    int(ItemType.Boots),
    int(ItemType.Salvage),
    # upgrades
    int(ItemType.Rune_Mod),
    # story and collectables
    int(ItemType.Trophy),
    int(ItemType.Quest_Item),
    int(ItemType.Minipet),
    int(ItemType.Present),
    int(ItemType.Costume),
    int(ItemType.Costume_Headpiece),
    # containers
    int(ItemType.Bag),
    int(ItemType.Bundle),
    # bulk last
    int(ItemType.Materials_Zcoins),
    int(ItemType.Gold_Coin),
    int(ItemType.CC_Shards),
]
TYPE_RANK = {type_value: index for index, type_value in enumerate(SORT_TYPE_ORDER)}
DYE_ITEM_TYPE = int(ItemType.Dye)

SETTINGS_SECTION = "InventoryLite"
RULES_DOC = "Widgets/Items/InventoryLiteRules.json"
TRACE_INI = "Widgets/Config/Inventory Lite Trace.ini"
TRACE_SECTION = "LastRun"

GRAY = (0.66, 0.67, 0.70, 1.0)
WARN = (0.79, 0.63, 0.29, 1.0)

LAST_TRACE: dict[str, str] = {}


def trace(stage: str):
    """Force a step marker to disk.

    A native crash takes the console with it and console output is memory-only, so this file is the
    only thing that can report which step was reached.
    """
    if LAST_TRACE.get("stage") == stage:
        return
    LAST_TRACE["stage"] = stage
    try:
        handler = Settings(TRACE_INI, scope="global")
        handler.set_str(TRACE_SECTION, "LastStage", stage)
        handler.save()
    except Exception:
        pass


def display_safe(text: str) -> str:
    """Strip GW markup, keep printable ASCII, escape %, cap length."""
    cleaned = re.sub(r"<[^>]*>", "", text or "")
    cleaned = re.sub(r"\{s c?\}|\{s\}|\{sc\}", "", cleaned)
    cleaned = "".join(ch for ch in cleaned if 32 <= ord(ch) < 127)
    return cleaned.replace("%", "%%")[:DISPLAY_LINE_CAP]


# ---------------------------------------------------------------- item facts


def normalize_match(text: str) -> str:
    """Lowercase, letters and digits only -- the form both sides of a text criterion compare in."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def item_facts(item_id: int, model_id: int, quantity: int) -> dict:
    """Everything a rule may test, from cached reads and the mod tables only."""
    item_type_value, item_type_name = GLOBAL_CACHE.Item.GetItemType(item_id)
    facts = {
        "item_id": item_id,
        "model_id": model_id,
        "quantity": quantity,
        "name": known_base_name(model_id),
        "item_type": int(item_type_value),
        "item_type_name": str(item_type_name or ""),
        "rarity": str(GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)[1] or ""),
        "value": int(GLOBAL_CACHE.Item.Properties.GetValue(item_id) or 0),
        "identified": bool(GLOBAL_CACHE.Item.Usage.IsIdentified(item_id)),
        "prefix": "",
        "suffix": "",
        "inherent": "",
        "requirement": 0,
        "prefix_max": False,
        "suffix_max": False,
        "inherent_max": False,
        "prefix_value": 0,
        "suffix_value": 0,
        "inherent_value": 0,
        "mod_count": 0,
        "max_mod_count": 0,
        "all_mods_max": False,
    }

    parsed = parse_item_mods(item_id, model_id, facts["item_type"])
    if parsed is None:
        return facts

    facts["requirement"] = int(getattr(parsed, "requirements", 0) or 0)

    # Maxed is the parser's own verdict: it compares the ROLLED value against the mod's declared max
    # from the LootEx tables (`mods_parser.py:204`), so a fixed-value mod counts as maxed because it
    # has no roll range. Per slot, because "perfect suffix" and "perfect item" are different questions.
    for slot in ("prefix", "suffix", "inherent"):
        matched = getattr(parsed, slot, None)
        facts[slot] = mod_display_name(matched)
        facts["%s_max" % slot] = bool(getattr(matched, "is_maxed", False))
        facts["%s_value" % slot] = int(getattr(matched, "value", 0) or 0)

    every_mod = list(getattr(parsed, "runes", None) or []) + list(getattr(parsed, "weapon_mods", None) or [])
    facts["mod_count"] = len(every_mod)
    facts["max_mod_count"] = sum(1 for m in every_mod if getattr(m, "is_maxed", False))
    facts["all_mods_max"] = bool(every_mod) and facts["max_mod_count"] == facts["mod_count"]
    return facts


def mod_cell(facts: dict, slot: str) -> str:
    """One mod slot as a cell: the name, its roll, and whether that roll is the top of the range."""
    name = facts[slot]
    if not name:
        return ""
    value = facts["%s_value" % slot]
    cell = "%s (%d)" % (name, value) if value else name
    return cell + (" MAX" if facts["%s_max" % slot] else "")


def facts_row(facts: dict) -> list[str]:
    return [
        display_safe(cell)
        for cell in (
            facts["name"] or "model %d" % facts["model_id"],
            mod_cell(facts, "prefix"),
            mod_cell(facts, "suffix"),
            mod_cell(facts, "inherent"),
            str(facts["requirement"] or ""),
            facts["rarity"],
            "%d/%d" % (facts["max_mod_count"], facts["mod_count"]) if facts["mod_count"] else "",
            str(facts["quantity"]) if facts["quantity"] > 1 else "",
        )
    ]


def resolve_unknown_names(facts_by_id: dict):
    """Name the items nothing could name, ONCE PER MODEL, and write the answer down.

    This is the only thing here that asks the game anything, so it is last, and it is skipped entirely
    for models already known. The answer is stored globally, so a model costs one resolution ever
    rather than one per scan.
    """
    unknown: dict[int, int] = {}
    for facts in facts_by_id.values():
        if not facts["name"]:
            unknown.setdefault(facts["model_id"], facts["item_id"])
    if not unknown:
        return 0

    # Every request goes out before any is collected, so the server resolves them in parallel instead
    # of the routine stalling on each in turn.
    request_names(unknown.values())

    learned = 0
    unresolved: list[int] = []
    for model_id, item_id in unknown.items():
        trace("name model %d" % model_id)
        facts = facts_by_id[item_id]
        name = yield from fetch_base_name(item_id, model_id, facts["prefix"], facts["suffix"])
        if name:
            learned += 1
        else:
            # Left unresolved on purpose and NOT written down: the model has to stay retryable.
            unresolved.append(model_id)

    for facts in facts_by_id.values():
        if not facts["name"]:
            facts["name"] = known_base_name(facts["model_id"])

    if unresolved:
        ConsoleLog(
            MODULE_NAME,
            "Could not name model(s) %s - they will be retried on the next scan."
            % ", ".join(str(m) for m in unresolved),
            Console.MessageType.Warning,
        )
    trace("names learned: %d" % learned)
    return learned


def gather_facts(learn_names: bool = True):
    """Facts for every bag item, ONE ITEM PER FRAME. Returns {item_id: facts}."""
    NAME_CACHE.load(force=True)

    out: dict[int, dict] = {}
    for item_id, (model_id, quantity) in list(live_bag_items().items()):
        trace("facts %d" % item_id)
        out[item_id] = item_facts(item_id, model_id, quantity)
        yield
    trace("facts done: %d" % len(out))

    if learn_names:
        learned = yield from resolve_unknown_names(out)
        if learned:
            ConsoleLog(
                MODULE_NAME,
                f"Learned {learned} new item name(s); future scans will not need to ask.",
                Console.MessageType.Info,
            )
    return out


# ---------------------------------------------------------------- rules


@dataclass
class Rule:
    """One rule, matched against an item's facts. Pure values: no client reads happen in here."""

    name: str = "New rule"
    enabled: bool = True
    keep: bool = False  # a keep rule vetoes deposit
    match_all: bool = True
    name_contains: tuple[str, ...] = field(default_factory=tuple)
    prefix_contains: tuple[str, ...] = field(default_factory=tuple)
    suffix_contains: tuple[str, ...] = field(default_factory=tuple)
    inherent_contains: tuple[str, ...] = field(default_factory=tuple)
    rarities: tuple[str, ...] = field(default_factory=tuple)
    max_requirement: int | None = None
    #: Per slot: the mod in that slot must be at the top of its roll range.
    prefix_max_only: bool = False
    suffix_max_only: bool = False
    inherent_max_only: bool = False
    #: Every mod on the item is maxed -- a perfect item, not just a perfect slot.
    all_mods_max: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "keep": self.keep,
            "match_all": self.match_all,
            "name_contains": list(self.name_contains),
            "prefix_contains": list(self.prefix_contains),
            "suffix_contains": list(self.suffix_contains),
            "inherent_contains": list(self.inherent_contains),
            "rarities": list(self.rarities),
            "max_requirement": self.max_requirement,
            "prefix_max_only": self.prefix_max_only,
            "suffix_max_only": self.suffix_max_only,
            "inherent_max_only": self.inherent_max_only,
            "all_mods_max": self.all_mods_max,
        }

    @staticmethod
    def from_dict(raw: dict) -> "Rule":
        def strs(key):
            return tuple(str(v) for v in (raw.get(key) or ()) if str(v).strip())

        req = raw.get("max_requirement")
        return Rule(
            name=str(raw.get("name", "New rule")),
            enabled=bool(raw.get("enabled", True)),
            keep=bool(raw.get("keep", False)),
            match_all=bool(raw.get("match_all", True)),
            name_contains=strs("name_contains"),
            prefix_contains=strs("prefix_contains"),
            suffix_contains=strs("suffix_contains"),
            inherent_contains=strs("inherent_contains"),
            rarities=strs("rarities"),
            max_requirement=None if req is None else int(req),
            prefix_max_only=bool(raw.get("prefix_max_only", False)),
            suffix_max_only=bool(raw.get("suffix_max_only", False)),
            inherent_max_only=bool(raw.get("inherent_max_only", False)),
            # Deliberately NOT migrated from the older `perfect_only`. That flag meant "the mod this
            # rule names is max", which is the per-slot check above; mapping it here silently turned
            # rules into "every mod on the item is max" and stopped them matching anything imperfect.
            all_mods_max=bool(raw.get("all_mods_max", False)),
        )

    def criteria_count(self) -> int:
        filled = (
            self.name_contains,
            self.prefix_contains,
            self.suffix_contains,
            self.inherent_contains,
            self.rarities,
            (self.max_requirement,) if self.max_requirement is not None else (),
            ("prefix max",) if self.prefix_max_only else (),
            ("suffix max",) if self.suffix_max_only else (),
            ("inherent max",) if self.inherent_max_only else (),
            ("all max",) if self.all_mods_max else (),
        )
        return sum(1 for c in filled if c)

    def evaluate(self, facts: dict) -> tuple[bool, list[tuple[str, bool]]]:
        """(verdict, per-criterion breakdown). A rule with no criteria matches nothing."""
        results: list[tuple[str, bool]] = []

        def contains(haystack: str, needles) -> bool:
            # Loose on purpose: case is ignored, and so is anything that is not a letter or digit.
            # The mod tables read "of Enchanting" while LootEx-style identifiers read "OfEnchanting",
            # and both should hit -- as should "Superior Vigor" against "Rune of Superior Vigor".
            low = normalize_match(haystack)
            return bool(low) and any(normalize_match(n) in low for n in needles if normalize_match(n))

        for label, value, needles in (
            ("name", facts["name"], self.name_contains),
            ("prefix", facts["prefix"], self.prefix_contains),
            ("suffix", facts["suffix"], self.suffix_contains),
            ("inherent", facts["inherent"], self.inherent_contains),
        ):
            if needles:
                results.append(("%s~%s" % (label, "/".join(needles)), contains(value, needles)))

        if self.rarities:
            results.append(("rarity", facts["rarity"] in self.rarities))
        if self.max_requirement is not None:
            got = facts["requirement"]
            results.append(("req<=%d" % self.max_requirement, bool(got) and got <= self.max_requirement))

        # A slot's max check needs a mod IN that slot: an empty slot is not a maxed one.
        for slot, wanted in (
            ("prefix", self.prefix_max_only),
            ("suffix", self.suffix_max_only),
            ("inherent", self.inherent_max_only),
        ):
            if wanted:
                results.append(("%s max" % slot, bool(facts[slot]) and facts["%s_max" % slot]))
        if self.all_mods_max:
            results.append(("all %d mods max" % facts["mod_count"], facts["all_mods_max"]))

        if not results:
            return False, results
        passed = [ok for _label, ok in results]
        return (all(passed) if self.match_all else any(passed)), results


def load_rules() -> list[Rule]:
    try:
        from Core.py4gwcorelib_src.JsonFactory import JsonFactory

        raw = JsonFactory(RULES_DOC, "global").get_json("rules", [])
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: list[Rule] = []
    for entry in raw:
        if isinstance(entry, dict):
            try:
                out.append(Rule.from_dict(entry))
            except Exception:
                continue
    return out


def save_rules(rules) -> None:
    try:
        from Core.py4gwcorelib_src.JsonFactory import JsonFactory

        JsonFactory(RULES_DOC, "global").set_json("rules", [r.to_dict() for r in rules])
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Could not save rules: {exc}", Console.MessageType.Error)


def describe_verdict(rule, results) -> str:
    return "%s: %s" % (
        "ALL" if rule.match_all else "ANY",
        ", ".join("%s=%s" % (label, "y" if ok else "n") for label, ok in results),
    )


def deposit_matches(rules, facts_by_id: dict) -> tuple[list[int], list[list[str]]]:
    """Ids a deposit rule claims and no keep rule vetoes, plus one row for EVERY item.

    Items that did NOT match are listed too, each with the breakdown of whichever deposit rule came
    closest. An item you expected to be taken and was not is the case that needs explaining, and it
    cannot explain itself if it has no row.
    """
    keepers = [r for r in rules if r.enabled and r.keep]
    takers = [r for r in rules if r.enabled and not r.keep]
    matched: list[int] = []
    rows: list[list[str]] = []

    for item_id, facts in facts_by_id.items():
        action = "-"
        rule_name = ""
        why = "no enabled deposit rule has criteria"

        kept = next((r for r in keepers if r.evaluate(facts)[0]), None)
        if kept is not None:
            action, rule_name, why = "KEEP", kept.name, describe_verdict(kept, kept.evaluate(facts)[1])
        else:
            best_score = -1
            for rule in takers:
                verdict, results = rule.evaluate(facts)
                if verdict:
                    action, rule_name, why = "TAKE", rule.name, describe_verdict(rule, results)
                    matched.append(item_id)
                    break
                score = sum(1 for _label, ok in results if ok)
                if results and score > best_score:
                    best_score = score
                    rule_name, why = rule.name, describe_verdict(rule, results)

        rows.append([action] + facts_row(facts) + [display_safe(rule_name), display_safe(why)])

    order = {"TAKE": 0, "KEEP": 1, "-": 2}
    rows.sort(key=lambda row: order.get(row[0], 3))
    return matched, rows


# ---------------------------------------------------------------- bags


def bag_sizes(bag_enums) -> list[tuple[int, str, int]]:
    """(bag_id, bag_name, size) for the bags that exist on this account."""
    existing: list[tuple[int, str, int]] = []
    for bag_enum in bag_enums:
        try:
            size = int(PyInventory.Bag(bag_enum.value, bag_enum.name).GetSize())
        except Exception:
            continue
        if size > 0:
            existing.append((bag_enum.value, bag_enum.name, size))
    return existing


def inventory_bags() -> list[tuple[int, str, int]]:
    return bag_sizes(INVENTORY_BAGS)


def storage_bags() -> list[tuple[int, str, int]]:
    return bag_sizes(STORAGE_BAGS)


def live_items(bags) -> dict[int, tuple[int, int]]:
    """``item_id -> (model_id, quantity)``, read out of the bags this instant."""
    live: dict[int, tuple[int, int]] = {}
    for bag_id, bag_name, _size in bags:
        try:
            entries = PyInventory.Bag(bag_id, bag_name).GetItems()
        except Exception:
            continue
        for entry in entries:
            try:
                item_id = int(entry.item_id)
                if item_id:
                    live[item_id] = (int(entry.model_id), int(entry.quantity))
            except Exception:
                continue
    return live


def live_bag_items() -> dict[int, tuple[int, int]]:
    return live_items(inventory_bags())


def slot_layout(bags) -> tuple[list[tuple[int, int]], list[int]]:
    """Flat slot positions across the given bags, and the item id in each."""
    positions: list[tuple[int, int]] = []
    item_ids: list[int] = []
    for bag_id, bag_name, size in bags:
        try:
            occupied = {int(item.slot): int(item.item_id) for item in PyInventory.Bag(bag_id, bag_name).GetItems()}
        except Exception:
            continue
        for slot in range(size):
            positions.append((bag_id, slot))
            item_ids.append(occupied.get(slot, 0))
    return positions, item_ids


def item_type_rank(item_id: int) -> int:
    type_value = int(GLOBAL_CACHE.Item.GetItemType(item_id)[0])
    return TYPE_RANK.get(type_value, len(TYPE_RANK) + type_value)


def sort_dye(item_id: int) -> int:
    """Dye colour, but only for dyes.

    `dye_color` returns the first non-zero modifier arg on ANY item, so using it as a sort key
    everywhere would scatter weapons by an unrelated number.
    """
    if int(GLOBAL_CACHE.Item.GetItemType(item_id)[0]) != DYE_ITEM_TYPE:
        return 0
    return dye_color(item_id)


def rarity_name(item_id: int) -> str:
    return GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)[1]


def dye_color(item_id: int) -> int:
    """Dye channel from the cache-held item's modifiers -- the same first-non-zero-arg1 rule the
    framework uses, without building a raw PyItem."""
    for modifier in GLOBAL_CACHE.Item.Mods.GetModifiers(item_id) or []:
        try:
            color = modifier.GetArg1()
        except Exception:
            continue
        if color:
            return color
    return 0


def unidentified_candidates() -> list[int]:
    return [
        item_id
        for item_id in live_bag_items()
        if not GLOBAL_CACHE.Item.Usage.IsIdentified(item_id) and rarity_name(item_id) in IDENTIFY_RARITIES
    ]


def salvage_candidates() -> list[int]:
    return [
        item_id
        for item_id in live_bag_items()
        if GLOBAL_CACHE.Item.Usage.IsSalvageable(item_id)
        and GLOBAL_CACHE.Item.Usage.IsIdentified(item_id)
        and rarity_name(item_id) in SALVAGE_RARITIES
    ]


# ---------------------------------------------------------------- organize


def condense_stacks(layout):
    """Merge partial stacks of the same model into as few slots as possible."""
    positions, item_ids = layout

    partials: list[dict] = []
    for index, item_id in enumerate(item_ids):
        if item_id == 0 or not GLOBAL_CACHE.Item.Properties.IsStackable(item_id):
            continue
        quantity = GLOBAL_CACHE.Item.Properties.GetQuantity(item_id)
        if quantity <= 0 or quantity >= MAX_STACK:
            continue
        bag_id, slot = positions[index]
        partials.append(
            {
                "item_id": item_id,
                "bag_id": bag_id,
                "slot": slot,
                "model_id": GLOBAL_CACHE.Item.GetModelID(item_id),
                "dye": dye_color(item_id),
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


def sort_key(item_id: int) -> tuple:
    """Type group, then rarity band inside it, then colour, model, size, worth.

    Rarity sits ABOVE model id on purpose: within a weapon or armour group that bands greens, then
    golds, purples and blues together, which is how you actually look for things.
    """
    if not item_id:
        return (1,) + (0,) * 6
    return (
        0,
        item_type_rank(item_id),
        -int(GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)[0] or 0),
        sort_dye(item_id),
        int(GLOBAL_CACHE.Item.GetModelID(item_id) or 0),
        -int(GLOBAL_CACHE.Item.Properties.GetQuantity(item_id) or 0),
        -int(GLOBAL_CACHE.Item.Properties.GetValue(item_id) or 0),
    )


def sort_slots(layout, bags):
    """Reorder slots into the hardcoded organize order (see SORT_TYPE_ORDER and sort_key)."""
    positions, item_ids = layout

    desired = sorted(item_ids, key=lambda item_id: sort_key(item_id) + (item_id,))

    for index, item_id in enumerate(desired):
        if item_id == 0 or item_ids[index] == item_id:
            continue
        # The snapshot ages while this runs, and a stack that merges into another takes its id with
        # it. Re-walk before every move rather than trusting the snapshot's quantity.
        live = live_items(bags)
        if item_id not in live:
            continue
        bag_id, slot = positions[index]
        Inventory.MoveItem(item_id, bag_id, slot, live[item_id][1])
        yield from Routines.Yield.wait(MOVE_DELAY_MS)


def organize(bags_reader):
    bags = bags_reader()
    yield from condense_stacks(slot_layout(bags))
    yield from sort_slots(slot_layout(bags), bags)


# ---------------------------------------------------------------- storage


def ensure_storage_open():
    if Inventory.IsStorageOpen():
        return True
    Inventory.OpenXunlaiWindow()
    waited = 0
    while waited < STORAGE_OPEN_TIMEOUT_MS:
        yield from Routines.Yield.wait(POLL_MS)
        waited += POLL_MS
        if Inventory.IsStorageOpen():
            return True
    ConsoleLog(
        MODULE_NAME,
        "Xunlai storage did not open - the chest is only reachable in an outpost.",
        Console.MessageType.Warning,
    )
    return False


def storage_deposit_target(model_id: int, quantity: int, stackable: bool, dye: int):
    """(bag_id, slot, amount) for this item's next move into storage, or None when storage is full."""
    first_empty = None

    for bag_id, bag_name, size in storage_bags():
        try:
            entries = PyInventory.Bag(bag_id, bag_name).GetItems()
        except Exception:
            continue
        occupied: dict[int, tuple[int, int, int]] = {}
        for entry in entries:
            try:
                occupied[int(entry.slot)] = (int(entry.item_id), int(entry.model_id), int(entry.quantity))
            except Exception:
                continue
        if stackable:
            for slot, (existing_id, existing_model, existing_quantity) in occupied.items():
                room = MAX_STACK - existing_quantity
                if existing_model != model_id or room <= 0:
                    continue
                if dye_color(existing_id) != dye:
                    continue
                return bag_id, slot, min(room, quantity)
        if first_empty is None:
            for slot in range(size):
                if slot not in occupied:
                    first_empty = (bag_id, slot, min(quantity, MAX_STACK) if stackable else quantity)
                    break

    return first_empty


def wait_for_deposit(item_id: int, quantity_before: int):
    """One live walk answers both outcomes: the stack left the bags, or its quantity dropped."""
    waited = 0
    while waited < DEPOSIT_CONFIRM_TIMEOUT_MS:
        yield from Routines.Yield.wait(POLL_MS)
        waited += POLL_MS
        live = live_bag_items()
        if item_id not in live:
            return True
        if live[item_id][1] < quantity_before:
            return True
    return False


def deposit_item(item_id: int):
    """One of the DEPOSIT_* statuses.

    Expiry of the confirm window is UNCONFIRMED, never failure: a guessed deadline must not decide
    whether the move counted. Only FULL is a real observation, so only FULL stops a run.
    """
    for _ in range(MAX_MOVES_PER_ITEM):
        live = live_bag_items()
        if item_id not in live:
            return DEPOSIT_MOVED
        model_id, quantity = live[item_id]
        if quantity <= 0:
            return DEPOSIT_MOVED
        stackable = GLOBAL_CACHE.Item.Properties.IsStackable(item_id)
        dye = dye_color(item_id)
        target = storage_deposit_target(model_id, quantity, stackable, dye)
        if target is None:
            return DEPOSIT_FULL
        bag_id, slot, amount = target
        Inventory.MoveItem(item_id, bag_id, slot, amount)
        moved = yield from wait_for_deposit(item_id, quantity)
        if not moved:
            still = live_bag_items().get(item_id)
            ConsoleLog(
                MODULE_NAME,
                f"Could not observe item {item_id} leaving the bags within {DEPOSIT_CONFIRM_TIMEOUT_MS} ms: "
                f"quantity={quantity} target={bag_id}/{slot} "
                f"still_in_bags={'no' if still is None else still[1]} - moving on.",
                Console.MessageType.Warning,
            )
            return DEPOSIT_UNCONFIRMED
    return DEPOSIT_UNCONFIRMED


# ---------------------------------------------------------------- widget


class InventoryLite:
    def __init__(self):
        self.initialized = False
        self.active = None
        self.active_label = ""
        self.active_since = 0.0
        self.auto_identify = True
        self.show_config = False
        self.last_identify_check = 0.0
        self.rules: list[Rule] = []
        self.report_rows: list[list[str]] = []
        self.report_columns: tuple[str, ...] = SCAN_COLUMNS
        self.report_title = ""
        self.editing: dict[str, str] = {}
        self.logged: set[str] = set()

    @property
    def busy(self) -> bool:
        return self.active is not None

    def log_once(self, key: str, message: str):
        if key in self.logged:
            return
        self.logged.add(key)
        ConsoleLog(MODULE_NAME, message, Console.MessageType.Error)

    def settings_handler(self):
        handler = Settings(MODULE_NAME, scope="account")
        return handler if handler.is_ready() else None

    def load_settings(self):
        handler = self.settings_handler()
        if handler is None:
            return False
        self.auto_identify = handler.get_bool(SETTINGS_SECTION, "AutoIdentify", True)
        self.rules = load_rules()
        return True

    def save_settings(self):
        handler = self.settings_handler()
        if handler is None:
            return
        handler.set_bool(SETTINGS_SECTION, "AutoIdentify", self.auto_identify)

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

    def cancel(self):
        """Closing the generator only stops our loop. Identify and Salvage hand per-item work to the
        framework's queues, which keep draining unless cleared. Our own moves are direct calls, so the
        shared ACTION queue is left alone."""
        label = self.active_label or "Routine"
        self.release()
        for queue_name in ("IDENTIFY", "SALVAGE"):
            try:
                ActionQueueManager().ResetQueue(queue_name)
            except Exception as exc:
                ConsoleLog(MODULE_NAME, f"Could not clear the {queue_name} queue: {exc}", Console.MessageType.Warning)
        ConsoleLog(MODULE_NAME, f"{label} cancelled.", Console.MessageType.Info)

    def pump(self):
        """Drive our own generator. GLOBAL_CACHE.Coroutines is only pumped by the Environment Upkeeper
        widget, so relying on it would make this widget inert whenever that one is disabled."""
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

    def start_scan(self):
        self.run(self.scan(), "Scan")

    def scan(self):
        """Read every bag item's facts and show them -- exactly what the rules will see."""
        trace("scan begin")
        facts_by_id = yield from gather_facts()
        self.report_title = "%d item(s) read" % len(facts_by_id)
        self.report_columns = SCAN_COLUMNS
        self.report_rows = [facts_row(f) for f in facts_by_id.values()]
        trace("scan done")

    def start_preview(self):
        self.run(self.preview(), "Preview")

    def preview(self):
        trace("preview begin")
        facts_by_id = yield from gather_facts()
        matched, rows = deposit_matches(self.rules, facts_by_id)
        self.report_title = "%d of %d item(s) would be deposited" % (len(matched), len(facts_by_id))
        self.report_columns = PREVIEW_COLUMNS
        self.report_rows = rows
        trace("preview done")

    def start_deposit(self):
        if not [r for r in self.rules if r.enabled and not r.keep]:
            ConsoleLog(MODULE_NAME, "No deposit rule is enabled.", Console.MessageType.Info)
            return
        self.run(self.deposit(), "Deposit")

    def deposit(self):
        trace("deposit begin")
        facts_by_id = yield from gather_facts()
        matched, rows = deposit_matches(self.rules, facts_by_id)
        self.report_title = "%d of %d item(s) matched" % (len(matched), len(facts_by_id))
        self.report_columns = PREVIEW_COLUMNS
        self.report_rows = rows
        if not matched:
            ConsoleLog(MODULE_NAME, "Nothing matches the deposit rules.", Console.MessageType.Info)
            return
        if not (yield from ensure_storage_open()):
            return

        deposited = 0
        unconfirmed = 0
        for item_id in matched:
            trace("deposit %d" % item_id)
            status = yield from deposit_item(item_id)
            if status == DEPOSIT_FULL:
                ConsoleLog(MODULE_NAME, "Storage is full; stopping.", Console.MessageType.Warning)
                break
            if status == DEPOSIT_UNCONFIRMED:
                unconfirmed += 1
            else:
                deposited += 1
            yield from Routines.Yield.wait(MOVE_DELAY_MS)

        tail = f" ({unconfirmed} unconfirmed)" if unconfirmed else ""
        ConsoleLog(
            MODULE_NAME,
            f"Deposited {deposited} of {len(matched)} item(s){tail}.",
            Console.MessageType.Success if deposited == len(matched) else Console.MessageType.Warning,
        )
        trace("deposit done")

    def start_organize(self):
        self.run(organize(inventory_bags), "Organize")

    def start_organize_storage(self):
        self.run(self.organize_storage(), "Organize storage")

    def organize_storage(self):
        if not (yield from ensure_storage_open()):
            return
        yield from organize(storage_bags)

    # -- drawing. Reads NOTHING off an item: every value shown was gathered by a routine. --

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
        # begin() sits outside the guard: end() must run once per successful begin() and never for one
        # that raised. An unbalanced ImGui window stack takes the client down.
        opened = PyImGui.begin(f"##{MODULE_NAME}Bar", flags)
        try:
            if opened:
                self.draw_bar_body()
        except Exception as exc:
            self.log_once("bar", f"Button bar draw failed: {exc}\n{traceback.format_exc()}")
        finally:
            PyImGui.end()

    def draw_bar_body(self):
        if self.busy:
            PyImGui.text_disabled(f"{self.active_label.lower()}...")
            PyImGui.same_line(0, 6)
            if PyImGui.button("Cancel"):
                self.cancel()
            return
        if PyImGui.button("Salvage"):
            self.start_salvage()
        PyImGui.same_line(0, 4)
        if PyImGui.button("Deposit"):
            self.start_deposit()
        PyImGui.same_line(0, 4)
        if PyImGui.button("Xunlai"):
            Inventory.OpenXunlaiWindow()
        if PyImGui.button("Organize bags"):
            self.start_organize()
        PyImGui.same_line(0, 4)
        if PyImGui.button("Organize storage"):
            self.start_organize_storage()
        PyImGui.same_line(0, 4)
        if PyImGui.button("Config"):
            self.show_config = not self.show_config

    def draw_config(self):
        if not self.show_config:
            return
        visible, still_open = PyImGui.begin(
            f"{MODULE_NAME} Config", self.show_config, PyImGui.WindowFlags.AlwaysAutoResize
        )
        try:
            if visible:
                self.draw_config_body()
        except Exception as exc:
            self.log_once("config", f"Config draw failed: {exc}\n{traceback.format_exc()}")
        finally:
            PyImGui.end()
        if not still_open:
            self.show_config = False

    def draw_config_body(self):
        auto_identify = ImGui.checkbox("Auto-Identify (Blue/Purple/Gold)", self.auto_identify)
        if auto_identify != self.auto_identify:
            self.auto_identify = auto_identify
            self.save_settings()
        PyImGui.text_disabled(f"Salvage button: {', '.join(SALVAGE_RARITIES)} only.")
        PyImGui.separator()
        self.draw_rules()
        PyImGui.separator()
        self.draw_report()

    def draw_rules(self):
        PyImGui.text("Deposit rules")
        if mod_database() is None:
            PyImGui.text_colored("Mod database failed to load - prefix/suffix criteria cannot match.", WARN)
        else:
            PyImGui.text_colored(
                "Mods are read from the item's modifier words against the LootEx tables. "
                "Comma-separate several values; matching is case-insensitive 'contains'.",
                GRAY,
            )
        if self.busy:
            PyImGui.text_colored(f"{self.active_label.lower()} running", GRAY)
        else:
            if PyImGui.small_button("Scan items"):
                self.start_scan()
            PyImGui.same_line(0, 6)
            if PyImGui.small_button("Preview matches"):
                self.start_preview()
            PyImGui.same_line(0, 6)
            if PyImGui.small_button("New rule"):
                self.rules.append(Rule(name="Rule %d" % (len(self.rules) + 1)))
                save_rules(self.rules)
        PyImGui.separator()

        if not self.rules:
            PyImGui.text_colored("No rules yet.", GRAY)
            return

        for index, rule in enumerate(list(self.rules)):
            self.draw_rule(index, rule)

    def draw_rule(self, index: int, rule: Rule):
        tag = str(index)
        enabled = PyImGui.checkbox("##en_%s" % tag, rule.enabled)
        if enabled != rule.enabled:
            rule.enabled = enabled
            save_rules(self.rules)
        PyImGui.same_line(0, 6)
        header = "%s%s  (%d criteria)###hdr_%s" % (
            "KEEP " if rule.keep else "",
            rule.name,
            rule.criteria_count(),
            tag,
        )
        if not PyImGui.collapsing_header(header):
            return

        typed = PyImGui.input_text("Name##nm_%s" % tag, rule.name)
        if typed != rule.name and typed.strip():
            rule.name = typed.strip()
            save_rules(self.rules)

        keep = PyImGui.checkbox("Keep (never deposit)##keep_%s" % tag, rule.keep)
        if keep != rule.keep:
            rule.keep = keep
            save_rules(self.rules)
        PyImGui.same_line(0, 10)
        match_all = PyImGui.checkbox("Match ALL##all_%s" % tag, rule.match_all)
        if match_all != rule.match_all:
            rule.match_all = match_all
            save_rules(self.rules)
        if not rule.match_all:
            PyImGui.text_colored("ANY: one criterion passing is the whole verdict.", WARN)

        for label, attribute, max_attribute in (
            ("Item name contains", "name_contains", ""),
            ("Prefix contains", "prefix_contains", "prefix_max_only"),
            ("Suffix contains", "suffix_contains", "suffix_max_only"),
            ("Inherent contains", "inherent_contains", "inherent_max_only"),
        ):
            key = "%s_%s" % (attribute, tag)
            current = ", ".join(getattr(rule, attribute))
            shown = self.editing.get(key, current)
            typed = PyImGui.input_text("%s##%s" % (label, key), shown)
            if typed != shown:
                self.editing[key] = typed
            PyImGui.same_line(0, 4)
            if PyImGui.small_button("set##%s" % key):
                values = tuple(v.strip() for v in self.editing.get(key, current).split(",") if v.strip())
                setattr(rule, attribute, values)
                self.editing.pop(key, None)
                save_rules(self.rules)
            if max_attribute:
                PyImGui.same_line(0, 8)
                on = getattr(rule, max_attribute)
                if PyImGui.checkbox("max##%s" % key, on) != on:
                    setattr(rule, max_attribute, not on)
                    save_rules(self.rules)
            # What the rule ACTUALLY holds, which is not the same as what is typed in the box: the
            # value only reaches the rule when `set` is pressed, and an uncommitted edit is otherwise
            # indistinguishable from a saved one.
            stored = getattr(rule, attribute)
            if stored:
                PyImGui.text_colored("    saved: %s" % display_safe(", ".join(stored)), GRAY)
            elif self.editing.get(key, "").strip():
                PyImGui.text_colored("    not saved yet - press set", WARN)

        all_max = PyImGui.checkbox("Every mod on the item is max##allmax_%s" % tag, rule.all_mods_max)
        if all_max != rule.all_mods_max:
            rule.all_mods_max = all_max
            save_rules(self.rules)

        PyImGui.text_colored("Rarity", GRAY)
        for name in RARITY_NAMES:
            on = name in rule.rarities
            if PyImGui.checkbox("%s##rar_%s_%s" % (name, tag, name), on) != on:
                rule.rarities = tuple(r for r in rule.rarities if r != name) if on else rule.rarities + (name,)
                save_rules(self.rules)
            PyImGui.same_line(0, 6)
        PyImGui.new_line()

        has_req = rule.max_requirement is not None
        want_req = PyImGui.checkbox("Requirement at most##rq_%s" % tag, has_req)
        if want_req != has_req:
            rule.max_requirement = 9 if want_req else None
            save_rules(self.rules)
        if rule.max_requirement is not None:
            PyImGui.same_line(0, 6)
            PyImGui.push_item_width(110)
            typed_req = PyImGui.slider_int("##rqv_%s" % tag, int(rule.max_requirement), 0, 13)
            PyImGui.pop_item_width()
            if typed_req != rule.max_requirement:
                rule.max_requirement = typed_req
                save_rules(self.rules)

        if PyImGui.small_button("Delete##del_%s" % tag):
            self.rules.pop(index)
            save_rules(self.rules)

    def draw_report(self):
        if not self.report_title:
            PyImGui.text_colored("Press Scan items to see what the rules will read.", GRAY)
            return
        PyImGui.text_colored(self.report_title, GRAY)
        if not self.report_rows:
            return

        columns = self.report_columns
        flags = (
            PyImGui.TableFlags.Borders
            | PyImGui.TableFlags.RowBg
            | PyImGui.TableFlags.Resizable
            | PyImGui.TableFlags.SizingStretchProp
            | PyImGui.TableFlags.ScrollY
        )
        # end_table() belongs INSIDE the success branch: unlike begin/end for windows, EndTable is
        # only valid when BeginTable returned true.
        if not PyImGui.begin_table("il_report", len(columns), flags, 0.0, REPORT_HEIGHT):
            return
        try:
            for label in columns:
                PyImGui.table_setup_column(label)
            PyImGui.table_headers_row()
            for row in self.report_rows[:REPORT_LINES]:
                PyImGui.table_next_row()
                for index in range(len(columns)):
                    PyImGui.table_next_column()
                    PyImGui.text_unformatted(row[index] if index < len(row) else "")
        finally:
            PyImGui.end_table()

        if len(self.report_rows) > REPORT_LINES:
            PyImGui.text_colored("... %d more" % (len(self.report_rows) - REPORT_LINES), GRAY)


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
