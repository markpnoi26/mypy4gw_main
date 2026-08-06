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
import PySystem

from Core import ImGui
from Core.Agent import Agent
from Core.AgentArray import AgentArray
from Core.enums_src.Item_enums import INVENTORY_BAGS
from Core.enums_src.Item_enums import STORAGE_BAGS
from Core.enums_src.Item_enums import ItemType
from Core.enums_src.Model_enums import ModelID
from Core.enums_src.Multiboxing_enums import SharedCommandType
from Core.FrameTree import Frame
from Core.FrameTree import FrameId
from Core.GlobalCache import GLOBAL_CACHE
from Core.ImGui_src.IconsFontAwesome5 import IconsFontAwesome5
from Core.Inventory import Inventory
from Core.Player import Player
from Core.Py4GWcorelib import ActionQueueManager
from Core.Py4GWcorelib import Color
from Core.Py4GWcorelib import Console
from Core.Py4GWcorelib import ConsoleLog
from Core.Py4GWcorelib import Utils
from Core.py4gwcorelib_src.Settings import Settings
from Core.Routines import Routines
from Core.UIManager import XunlaiStorageWindow
from Sources.marks_sources.item_naming import NAME_CACHE
from Sources.marks_sources.item_naming import fetch_base_name
from Sources.marks_sources.item_naming import known_base_name
from Sources.marks_sources.item_naming import mod_database
from Sources.marks_sources.item_naming import mod_display_name
from Sources.marks_sources.item_naming import name_key
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
#: How long an unclaimed cross-account deposit request may hold an inbox slot. Generous on purpose:
#: it only has to outlast a peer being mid-load, and expiry here means "nobody is listening".
DEPOSIT_REQUEST_TTL_MS = 300000
STORAGE_OPEN_TIMEOUT_MS = 4000
DEPOSIT_CONFIRM_TIMEOUT_MS = 2500
MATERIAL_DEPOSIT_TIMEOUT_MS = 3000
MAX_MOVES_PER_ITEM = 8

#: Matched case-insensitively against agent names. Traders ("Rune Trader", "Material Trader") do not
#: contain it, and an exact hit outranks a partial one -- see nearest_merchant.
MERCHANT_NAME = "merchant"
MERCHANT_SEARCH_RANGE = 5000.0
#: How many nearby NPCs to walk to before giving up. Small: when you are standing at a merchant the
#: first candidate is it, and a wrong guess costs a round trip across the outpost.
MERCHANT_TRY_LIMIT = 3
MERCHANT_WALK_TIMEOUT_MS = 30000
MERCHANT_WINDOW_TIMEOUT_MS = 5000
KIT_MODELS = (
    int(ModelID.Salvage_Kit.value),
    int(ModelID.Identification_Kit.value),
    int(ModelID.Superior_Identification_Kit.value),
)
DISPLAY_LINE_CAP = 220
REPORT_LINES = 40
REPORT_HEIGHT = 320.0
RARITY_NAMES = ("White", "Blue", "Purple", "Gold", "Green")

SCAN_COLUMNS = ("Name", "Type", "Prefix", "Suffix", "Inherent", "Req", "Rarity", "Max", "Qty")
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

# What a rule can filter on by kind. Spelled out rather than taken from ITEM_TYPE_META_TYPES because
# that table has no armour or consumable grouping, and its ARMOR_TYPES includes ItemType.Salvage --
# an "Armor" rule that silently also claimed salvage drops would be a trap. Membership is explicit
# everywhere, so an item type in no group is matched by no group rather than by a catch-all.
ITEM_GROUPS: dict[str, tuple[int, ...]] = {
    "Weapons": (
        int(ItemType.Axe),
        int(ItemType.Sword),
        int(ItemType.Hammer),
        int(ItemType.Daggers),
        int(ItemType.Scythe),
        int(ItemType.Spear),
        int(ItemType.Bow),
        int(ItemType.Staff),
        int(ItemType.Wand),
    ),
    "Off-hand": (int(ItemType.Offhand), int(ItemType.Shield)),
    "Armor": (
        int(ItemType.Headpiece),
        int(ItemType.Chestpiece),
        int(ItemType.Gloves),
        int(ItemType.Leggings),
        int(ItemType.Boots),
    ),
    "Consumables": (int(ItemType.Usable), int(ItemType.Scroll), int(ItemType.Storybook)),
    "Upgrades": (int(ItemType.Rune_Mod),),
    "Materials": (int(ItemType.Materials_Zcoins), int(ItemType.CC_Shards), int(ItemType.Gold_Coin)),
    "Salvage": (int(ItemType.Salvage),),
    "Trophies": (int(ItemType.Trophy),),
    "Kits": (int(ItemType.Kit),),
    "Dyes": (int(ItemType.Dye),),
    "Keys": (int(ItemType.Key),),
    "Quest": (int(ItemType.Quest_Item),),
    "Misc": (
        int(ItemType.Bag),
        int(ItemType.Bundle),
        int(ItemType.Minipet),
        int(ItemType.Present),
        int(ItemType.Costume),
        int(ItemType.Costume_Headpiece),
    ),
}
GROUPS_PER_ROW = 4

# The same criterion at single-type resolution, for the rules a group cannot express -- "of Enchanting
# on a spear but not on a bow". Only the equipment types: that is where a group is too coarse to be
# useful, and the groups above already say everything worth saying about the rest.
ITEM_TYPES: dict[str, int] = {
    "Axe": int(ItemType.Axe),
    "Sword": int(ItemType.Sword),
    "Hammer": int(ItemType.Hammer),
    "Daggers": int(ItemType.Daggers),
    "Scythe": int(ItemType.Scythe),
    "Spear": int(ItemType.Spear),
    "Bow": int(ItemType.Bow),
    "Staff": int(ItemType.Staff),
    "Wand": int(ItemType.Wand),
    "Offhand": int(ItemType.Offhand),
    "Shield": int(ItemType.Shield),
    "Headpiece": int(ItemType.Headpiece),
    "Chestpiece": int(ItemType.Chestpiece),
    "Gloves": int(ItemType.Gloves),
    "Leggings": int(ItemType.Leggings),
    "Boots": int(ItemType.Boots),
}
TYPE_NAMES = {value: name for name, value in ITEM_TYPES.items()}
TYPES_PER_ROW = 4

SETTINGS_SECTION = "InventoryLite"
RULES_DOC = "Widgets/Items/InventoryLiteRules.json"
SEEDED_UNNAMED_KEY = "seeded_unnamed"
UNNAMED_RULE_NAME = "Unnamed - park in storage"
SEEDED_SALVAGE_KEY = "seeded_salvage"
SALVAGE_RULE_NAME = "Salvage - white and blue"

# In precedence order: an item claimed by an earlier action is invisible to the later ones. One item,
# one fate, decided by the strongest claim on it rather than by which button you happen to press.
ACTIONS = ("keep", "sell", "salvage")
ACTION_TABS = {"keep": "Keep", "sell": "Sell", "salvage": "Salvage"}
ACTION_VERBS = {"keep": "DEPOSIT", "sell": "SELL", "salvage": "SALVAGE"}
ACTION_BLURBS = {
    "keep": "Matches are deposited into storage, if there is room.",
    "sell": "Matches are sold at the merchant.",
    "salvage": "Matches are salvaged. Irreversible - the item is consumed.",
}
#: Kinds no merchant takes. Applied on the sell side whatever the rules say, so a rule that claims
#: them stalls the run on items that cannot leave the bag instead of quietly doing nothing.
NON_SELLABLE_GROUPS = ("Consumables", "Quest", "Kits")

GRAY = (0.66, 0.67, 0.70, 1.0)
WARN = (0.79, 0.63, 0.29, 1.0)

BAR_GAP = 2.0
BAR_ICON_SIZE = 46.0
#: One of ImGui.push_font's exact atlas sizes (14/22/30/46/62/124). An in-between number renders
#: through push_font_scaled, which is both softer and the branch that makes nesting unsafe. 46 is the
#: next size up and is far too big for a bar this tall, so BAR_ICON_SIZE carries the growth instead,
#: keeping the glyph-to-padding proportion the 34/22 pair had.
BAR_GLYPH_SIZE = 30
#: Vertical break between button groups, so the chest reads as its own thing rather than as the first
#: item of the list below it.
BAR_GROUP_GAP = 12.0

# Icon-only, so each one has to read as its target at a glance rather than as its verb: a hammer
# breaks things down, a shop sells, an archive box swallows, a crate opens, a grid tidies.
ICON_SALVAGE = IconsFontAwesome5.ICON_HAMMER
ICON_DEPOSIT = IconsFontAwesome5.ICON_ARCHIVE
ICON_MERCHANT = IconsFontAwesome5.ICON_STORE
ICON_XUNLAI = IconsFontAwesome5.ICON_BOX_OPEN
ICON_ORGANIZE = IconsFontAwesome5.ICON_TH
ICON_BROADCAST = IconsFontAwesome5.ICON_USERS
ICON_CONFIG = IconsFontAwesome5.ICON_COG
ICON_CANCEL = IconsFontAwesome5.ICON_TIMES

# (base, hovered, active) per action. Grouped by what the button does to your items: amber consumes,
# gold sells, blue moves, green rearranges, red stops.
BUTTON_SALVAGE = (Color(150, 90, 40), Color(184, 114, 52), Color(118, 70, 30))
BUTTON_MERCHANT = (Color(146, 128, 36), Color(180, 158, 50), Color(114, 100, 26))
BUTTON_DEPOSIT = (Color(44, 94, 150), Color(58, 120, 186), Color(34, 74, 118))
BUTTON_XUNLAI = (Color(70, 80, 100), Color(90, 104, 130), Color(54, 62, 78))
BUTTON_ORGANIZE = (Color(50, 114, 76), Color(64, 144, 96), Color(38, 88, 58))
BUTTON_CONFIG = (Color(80, 80, 88), Color(104, 104, 114), Color(60, 60, 66))
BUTTON_CANCEL = (Color(150, 54, 54), Color(184, 70, 70), Color(118, 42, 42))
# Violet stands alone: the only button here that acts on accounts you are not looking at.
BUTTON_BROADCAST = (Color(104, 66, 150), Color(130, 84, 184), Color(82, 50, 118))


def bar_button(icon: str, palette, tooltip: str) -> bool:
    """One square icon button in the bar.

    The glyph is drawn as an ordinary button label under a pushed font rather than through
    ImGui.icon_button, which pins the glyph to `get_text_line_height() * 0.8` and cannot be told to
    draw it bigger. Its own internal push_font calls also flip ImGui's global `_last_font_scaled`,
    so wrapping it in an outer push would make our pop take the wrong branch.

    The tooltip is the only label there is, so it is required rather than optional: an icon nobody
    can name is a button nobody will press.
    """
    base, hovered, active = palette
    PyImGui.push_style_color(PyImGui.ImGuiCol.Button, base.to_tuple_normalized())
    PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonHovered, hovered.to_tuple_normalized())
    PyImGui.push_style_color(PyImGui.ImGuiCol.ButtonActive, active.to_tuple_normalized())
    ImGui.push_font("Regular", BAR_GLYPH_SIZE)
    try:
        clicked = PyImGui.button(icon, BAR_ICON_SIZE, BAR_ICON_SIZE)
    finally:
        ImGui.pop_font()
        PyImGui.pop_style_color(3)
    if PyImGui.is_item_hovered():
        PyImGui.set_tooltip(tooltip)
    return clicked


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
        "name": known_base_name(item_id),
        "item_type": int(item_type_value),
        "item_type_name": str(item_type_name or ""),
        "rarity": str(GLOBAL_CACHE.Item.Rarity.GetRarity(item_id)[1] or ""),
        "value": int(GLOBAL_CACHE.Item.Properties.GetValue(item_id) or 0),
        "identified": bool(GLOBAL_CACHE.Item.Usage.IsIdentified(item_id)),
        "salvageable": bool(GLOBAL_CACHE.Item.Usage.IsSalvageable(item_id)),
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


def group_of(item_type: int) -> str:
    """The ITEM_GROUPS name holding this type, or "" when no group does."""
    for name, members in ITEM_GROUPS.items():
        if item_type in members:
            return name
    return ""


def type_label(facts: dict) -> str:
    """What the rule editor calls this item.

    The precise checkbox label wins, so the column and the Kind boxes agree letter for letter: a rule
    you cannot write from what the report shows is a rule you will get wrong.
    """
    item_type = facts["item_type"]
    return TYPE_NAMES.get(item_type) or group_of(item_type) or facts["item_type_name"] or str(item_type)


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
            type_label(facts),
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
    """Name the items nothing could name, ONCE PER ENCODED NAME, and write the answer down.

    This is the only thing here that asks the game anything, so it is last, and it is skipped entirely
    for items already known. The answer is stored globally, so an encoded name costs one resolution
    ever rather than one per scan. Deduped by that key rather than by model id, so two prefixes on
    one skin are two lookups -- both landing on the same base name.
    """
    unnamed = [facts["item_id"] for facts in facts_by_id.values() if not facts["name"]]
    if not unnamed:
        return 0

    # Every request goes out before any is collected, so the server resolves them in parallel instead
    # of the routine stalling on each in turn. It also has to happen BEFORE any key is read: an item
    # the client has never named has no encoded name yet either, and keying first silently dropped
    # exactly the unfamiliar drops this function exists to name.
    request_names(unnamed)

    # Deduped by key where there is one, by item otherwise. Falling back rather than skipping matters:
    # the key may still be empty here, and fetch_base_name reads it again after the name arrives.
    unknown: dict[str, int] = {}
    for item_id in unnamed:
        unknown.setdefault(name_key(item_id) or "item:%d" % item_id, item_id)

    learned = 0
    unresolved: list[int] = []
    for item_id in unknown.values():
        facts = facts_by_id[item_id]
        name = yield from fetch_base_name(item_id, facts["model_id"], facts["prefix"], facts["suffix"])
        if name:
            learned += 1
        else:
            # Left unresolved on purpose and NOT written down: the item has to stay retryable.
            unresolved.append(facts["model_id"])

    for facts in facts_by_id.values():
        if not facts["name"]:
            facts["name"] = known_base_name(facts["item_id"])

    if unresolved:
        ConsoleLog(
            MODULE_NAME,
            "Could not name model(s) %s - they will be retried on the next scan."
            % ", ".join(str(m) for m in unresolved),
            Console.MessageType.Warning,
        )
    return learned


def gather_facts(learn_names: bool = True):
    """Facts for every bag item, ONE ITEM PER FRAME. Returns {item_id: facts}."""
    NAME_CACHE.load(force=True)

    out: dict[int, dict] = {}
    for item_id, (model_id, quantity) in list(live_bag_items().items()):
        out[item_id] = item_facts(item_id, model_id, quantity)
        yield

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


def action_from_raw(raw: dict) -> str:
    """The action a stored rule asks for, including the two older spellings.

    Both of those became "keep": the old `keep` flag meant "worth having, do not deposit" and a plain
    rule meant "deposit", and the Keep tab is now the one place either intent lives. Neither reading
    can destroy an item if this guesses wrong, which is why the migration is silent.
    """
    action = str(raw.get("action", "") or "").lower()
    if action in ACTIONS:
        return action
    if raw.get("sell"):
        return "sell"
    return "keep"


@dataclass
class Rule:
    """One rule, matched against an item's facts. Pure values: no client reads happen in here."""

    name: str = "New rule"
    enabled: bool = True
    #: One of ACTIONS. Not a criterion -- it picks what happens to a match, not what matches.
    action: str = "keep"
    match_all: bool = True
    name_contains: tuple[str, ...] = field(default_factory=tuple)
    prefix_contains: tuple[str, ...] = field(default_factory=tuple)
    suffix_contains: tuple[str, ...] = field(default_factory=tuple)
    inherent_contains: tuple[str, ...] = field(default_factory=tuple)
    rarities: tuple[str, ...] = field(default_factory=tuple)
    #: Keys into ITEM_GROUPS. Stored by name so a hand-edited rules file stays readable and so a
    #: renumbered ItemType cannot silently repoint a rule at a different kind of item.
    item_groups: tuple[str, ...] = field(default_factory=tuple)
    #: Keys into ITEM_TYPES. Unions with item_groups into ONE criterion, so ticking a group and a type
    #: widens the same test rather than adding a second one that Match ALL would then require.
    item_types: tuple[str, ...] = field(default_factory=tuple)
    max_requirement: int | None = None
    #: Per slot: the mod in that slot must be at the top of its roll range.
    prefix_max_only: bool = False
    suffix_max_only: bool = False
    inherent_max_only: bool = False
    #: Every mod on the item is maxed -- a perfect item, not just a perfect slot.
    all_mods_max: bool = False
    #: Nothing could name the model. Parks the item in storage until naming catches up.
    unnamed_only: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "action": self.action,
            "match_all": self.match_all,
            "name_contains": list(self.name_contains),
            "prefix_contains": list(self.prefix_contains),
            "suffix_contains": list(self.suffix_contains),
            "inherent_contains": list(self.inherent_contains),
            "rarities": list(self.rarities),
            "item_groups": list(self.item_groups),
            "item_types": list(self.item_types),
            "max_requirement": self.max_requirement,
            "prefix_max_only": self.prefix_max_only,
            "suffix_max_only": self.suffix_max_only,
            "inherent_max_only": self.inherent_max_only,
            "all_mods_max": self.all_mods_max,
            "unnamed_only": self.unnamed_only,
        }

    @staticmethod
    def from_dict(raw: dict) -> "Rule":
        def strs(key):
            return tuple(str(v) for v in (raw.get(key) or ()) if str(v).strip())

        req = raw.get("max_requirement")
        return Rule(
            name=str(raw.get("name", "New rule")),
            enabled=bool(raw.get("enabled", True)),
            action=action_from_raw(raw),
            match_all=bool(raw.get("match_all", True)),
            name_contains=strs("name_contains"),
            prefix_contains=strs("prefix_contains"),
            suffix_contains=strs("suffix_contains"),
            inherent_contains=strs("inherent_contains"),
            rarities=strs("rarities"),
            # Filtered against the live table, so a group we no longer ship drops out instead of
            # sitting in the rule as a criterion nothing can ever satisfy.
            item_groups=tuple(g for g in strs("item_groups") if g in ITEM_GROUPS),
            item_types=tuple(t for t in strs("item_types") if t in ITEM_TYPES),
            max_requirement=None if req is None else int(req),
            prefix_max_only=bool(raw.get("prefix_max_only", False)),
            suffix_max_only=bool(raw.get("suffix_max_only", False)),
            inherent_max_only=bool(raw.get("inherent_max_only", False)),
            # Deliberately NOT migrated from the older `perfect_only`. That flag meant "the mod this
            # rule names is max", which is the per-slot check above; mapping it here silently turned
            # rules into "every mod on the item is max" and stopped them matching anything imperfect.
            all_mods_max=bool(raw.get("all_mods_max", False)),
            unnamed_only=bool(raw.get("unnamed_only", False)),
        )

    def criteria_count(self) -> int:
        filled = (
            self.name_contains,
            self.prefix_contains,
            self.suffix_contains,
            self.inherent_contains,
            self.rarities,
            # One criterion, however it was spelled -- see Rule.item_types.
            self.item_groups + self.item_types,
            (self.max_requirement,) if self.max_requirement is not None else (),
            ("prefix max",) if self.prefix_max_only else (),
            ("suffix max",) if self.suffix_max_only else (),
            ("inherent max",) if self.inherent_max_only else (),
            ("all max",) if self.all_mods_max else (),
            ("unnamed",) if self.unnamed_only else (),
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
        if self.item_groups or self.item_types:
            allowed: set[int] = set()
            for group in self.item_groups:
                allowed.update(ITEM_GROUPS.get(group, ()))
            allowed.update(ITEM_TYPES[t] for t in self.item_types if t in ITEM_TYPES)
            wanted = "/".join(self.item_groups + self.item_types)
            results.append(("kind~%s" % wanted, facts["item_type"] in allowed))
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

        # Read AFTER resolve_unknown_names has run, so an empty name means every rung of the ladder
        # failed for this model -- not that the scan simply has not asked yet.
        if self.unnamed_only:
            results.append(("unnamed (model %d)" % facts["model_id"], not facts["name"]))

        if not results:
            return False, results
        passed = [ok for _label, ok in results]
        return (all(passed) if self.match_all else any(passed)), results


def read_rules() -> list[Rule] | None:
    """Parsed rules from the shared document, or None when it could not be read at all.

    None and [] are deliberately different answers: "the file says there are no rules" and "we could
    not ask" lead to opposite decisions once a session is already running.
    """
    try:
        from Core.py4gwcorelib_src.JsonFactory import JsonFactory

        raw = JsonFactory(RULES_DOC, "global").get_json("rules", [])
    except Exception:
        return None
    if not isinstance(raw, list):
        return None
    out: list[Rule] = []
    for entry in raw:
        if isinstance(entry, dict):
            try:
                out.append(Rule.from_dict(entry))
            except Exception:
                continue
    return out


def load_rules() -> list[Rule]:
    return read_rules() or []


def seed_once(key: str, rules, rule: Rule) -> bool:
    """Append a starter rule, once ever. True when it was appended.

    Guarded by a flag in the document rather than by "does such a rule exist", so deleting the rule is
    a decision that sticks instead of one the next load undoes.
    """
    try:
        from Core.py4gwcorelib_src.JsonFactory import JsonFactory

        doc = JsonFactory(RULES_DOC, "global")
        if doc.get_bool(key, False):
            return False
        doc.set_bool(key, True)
    except Exception:
        return False
    rules.append(rule)
    return True


def seed_unnamed_rule(rules) -> bool:
    return seed_once(SEEDED_UNNAMED_KEY, rules, Rule(name=UNNAMED_RULE_NAME, unnamed_only=True))


def seed_salvage_rule(rules) -> bool:
    """The Salvage button used to be a hardcoded white/blue filter. Same behaviour, now a visible
    rule you can edit, so nothing changes silently for anyone who already had the button."""
    return seed_once(
        SEEDED_SALVAGE_KEY,
        rules,
        Rule(name=SALVAGE_RULE_NAME, action="salvage", rarities=SALVAGE_RARITIES),
    )


def save_rules(rules) -> None:
    try:
        from Core.py4gwcorelib_src.JsonFactory import JsonFactory

        doc = JsonFactory(RULES_DOC, "global")
        doc.set_json("rules", [r.to_dict() for r in rules])
        # Forced rather than left to the autosave debounce: another account can read this file at any
        # moment, and it must never read a version older than what this client is already showing.
        doc.save()
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Could not save rules: {exc}", Console.MessageType.Error)


def reload_rules(current: list[Rule]) -> list[Rule]:
    """Re-read the shared rules from disk, picking up edits made on another account.

    JsonFactory caches one instance per (name, scope) PER PROCESS and nothing polls the file, so a
    peer's write lands on disk and never reaches this client's copy on its own. Losing an unsaved
    local edit to the reload is not possible: save_rules flushes on every change.

    Returns `current` untouched when the document cannot be read. A transient read failure must not
    silently disarm every rule -- that turns a button press into a no-op with nothing to see.
    """
    try:
        from Core.py4gwcorelib_src.JsonFactory import JsonFactory

        JsonFactory(RULES_DOC, "global").reload()
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Could not re-read the shared rules: {exc}", Console.MessageType.Warning)
        return current
    fresh = read_rules()
    if fresh is None:
        ConsoleLog(
            MODULE_NAME, "Shared rules were unreadable; keeping the ones already loaded.", Console.MessageType.Warning
        )
        return current
    return fresh


def describe_verdict(rule, results) -> str:
    return "%s: %s" % (
        "ALL" if rule.match_all else "ANY",
        ", ".join("%s=%s" % (label, "y" if ok else "n") for label, ok in results),
    )


def action_block_reason(facts: dict, action: str) -> str:
    """Why the game will not let this item take this action, or "" when it will.

    Checked ahead of the rules and separately from them, because these are facts about the item rather
    than preferences about it. A rule cannot argue a merchant into buying a scroll.
    """
    if action == "sell":
        if group_of(facts["item_type"]) in NON_SELLABLE_GROUPS:
            return "no merchant buys this kind"
        if facts["value"] <= 0:
            return "worth nothing to a merchant"
    elif action == "salvage":
        if not facts["salvageable"]:
            return "not salvageable"
        if not facts["identified"]:
            return "unidentified"
    return ""


def match_rules(rules, facts_by_id: dict, action: str, residual: bool = False):
    """Ids this action claims, plus one row for EVERY item. (matched, rows).

    Actions run in ACTIONS order and each one only sees what the earlier ones left: an item a Keep
    rule claims is never offered to Sell or Salvage. That is what makes one item have one fate no
    matter which button is pressed, instead of the buttons racing each other for it.

    Items that did NOT match are listed too, each with the breakdown of whichever rule came closest.
    An item you expected to be taken and was not is the case that needs explaining, and it cannot
    explain itself if it has no row.
    """
    verb = ACTION_VERBS[action]
    rank = ACTIONS.index(action)
    stronger = [r for r in rules if r.enabled and ACTIONS.index(r.action) < rank]
    mine = [r for r in rules if r.enabled and r.action == action]
    matched: list[int] = []
    rows: list[list[str]] = []

    for item_id, facts in facts_by_id.items():
        verdict_label = "-"
        rule_name = ""
        why = "no enabled %s rule has criteria" % action

        claimed = next((r for r in stronger if r.evaluate(facts)[0]), None)
        blocked = action_block_reason(facts, action)
        if claimed is not None:
            verdict_label = ACTION_VERBS[claimed.action]
            rule_name = claimed.name
            why = describe_verdict(claimed, claimed.evaluate(facts)[1])
        elif blocked:
            verdict_label, why = "SKIP", blocked
        else:
            best_score = -1
            for rule in mine:
                passes, results = rule.evaluate(facts)
                if passes:
                    verdict_label, rule_name, why = verb, rule.name, describe_verdict(rule, results)
                    matched.append(item_id)
                    break
                score = sum(1 for _label, ok in results if ok)
                if results and score > best_score:
                    best_score = score
                    rule_name, why = rule.name, describe_verdict(rule, results)
            else:
                if residual:
                    verdict_label, rule_name, why = verb, "", "unclaimed by any rule"
                    matched.append(item_id)

        rows.append([verdict_label] + facts_row(facts) + [display_safe(rule_name), display_safe(why)])

    order = {verb: 0, "SKIP": 2, "-": 3}
    rows.sort(key=lambda row: order.get(row[0], 1))
    return matched, rows


def deposit_matches(rules, facts_by_id: dict):
    return match_rules(rules, facts_by_id, "keep")


def sell_matches(rules, facts_by_id: dict):
    return match_rules(rules, facts_by_id, "sell")


def salvage_matches(rules, facts_by_id: dict, residual: bool = False):
    return match_rules(rules, facts_by_id, "salvage", residual=residual)


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


def material_count_in_bags() -> int:
    return sum(1 for item_id in live_bag_items() if GLOBAL_CACHE.Item.Type.IsMaterial(item_id))


def deposit_all_materials():
    """The client's own button, which sweeps every material out of the bags and ignores the Keep rules.

    Nothing else here can reach the material panes: both MoveItem and DepositItemToStorage target
    Storage1..14 only, so materials would otherwise land in ordinary storage slots.
    """
    before = material_count_in_bags()
    if not before:
        return
    if not XunlaiStorageWindow.ClickDepositAllMaterials():
        ConsoleLog(MODULE_NAME, "Could not reach the deposit-all-materials button.", Console.MessageType.Warning)
        return

    waited = 0
    remaining = before
    moving = False
    while waited < MATERIAL_DEPOSIT_TIMEOUT_MS:
        yield from Routines.Yield.wait(POLL_MS)
        waited += POLL_MS
        current = material_count_in_bags()
        if current != remaining:
            remaining = current
            moving = True
            continue
        if moving:
            break

    if remaining < before:
        ConsoleLog(
            MODULE_NAME,
            f"{before - remaining} of {before} material stack(s) left the bags.",
            Console.MessageType.Success,
        )
        return
    ConsoleLog(
        MODULE_NAME,
        f"Clicked deposit all materials, but none of the {before} stack(s) moved within "
        f"{MATERIAL_DEPOSIT_TIMEOUT_MS} ms - the material panes may be full.",
        Console.MessageType.Warning,
    )


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


def wait_for_item_to_leave(item_id: int, quantity_before: int):
    """One live walk answers both outcomes: the stack left the bags, or its quantity dropped.

    The same question for a deposit and for a sale -- both end with the item no longer in the bags.
    """
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
        moved = yield from wait_for_item_to_leave(item_id, quantity)
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


# ---------------------------------------------------------------- merchant


def agent_name(agent_id: int) -> str:
    """An agent's lowercase name, or "" when the client cannot supply one yet.

    Empty is the NORMAL case, not an error. Agent.GetNameByID decodes through
    `Core.native_src.internals.string_table`, whose table is loaded lazily from gw.dat -- the same
    ~100K-entry load the item naming path stopped waiting on. Until it is populated every NPC is
    nameless, so nothing here may treat a name as required.
    """
    try:
        return Agent.GetNameByID(agent_id).strip().lower()
    except Exception:
        return ""


def merchant_candidates() -> list[int]:
    """NPCs worth trying, best first.

    Distance does the real work; a name only refines the order when one happens to be available. The
    NPC array is the framework's own pre-filtered one, so players, pets and gadgets never appear.
    """
    px, py = Player.GetXY()
    ranked: list[tuple[int, float, int]] = []
    for agent_id in AgentArray.GetNPCMinipetArray():
        try:
            x, y = Agent.GetXY(agent_id)
        except Exception:
            continue
        distance = Utils.Distance((px, py), (x, y))
        if distance > MERCHANT_SEARCH_RANGE:
            continue
        name = agent_name(agent_id)
        rank = 0 if name == MERCHANT_NAME else 1 if MERCHANT_NAME in name else 2
        ranked.append((rank, distance, agent_id))
    ranked.sort()
    return [agent_id for _rank, _distance, agent_id in ranked]


def merchant_stocks_kits(offered) -> bool:
    """Whether the open window is a general merchant rather than a trader.

    Asked of the window rather than of the NPC, because what the window sells is the only thing that
    actually decides whether the trip was worth taking.
    """
    for item_id in offered:
        try:
            if int(GLOBAL_CACHE.Item.GetModelID(item_id)) in KIT_MODELS:
                return True
        except Exception:
            continue
    return False


def wait_for_merchant_window():
    """The merchant's stock, or [] when it never arrived."""
    waited = 0
    while waited < MERCHANT_WINDOW_TIMEOUT_MS:
        yield from Routines.Yield.wait(POLL_MS)
        waited += POLL_MS
        try:
            offered = list(GLOBAL_CACHE.Trading.Merchant.GetOfferedItems())
        except Exception:
            offered = []
        if offered:
            return offered
    return []


def open_merchant():
    """Get a merchant window open. Its stock on success, [] otherwise.

    Identity is decided by what the window turns out to sell, never by the NPC we walked to -- names
    are usually unavailable (see agent_name) so a candidate is a guess until its stock proves it.
    A guess that turns out to be a trader costs a walk and is retried, not treated as failure.

    Expiry IS load-bearing here, unlike the item confirms: with no stock list there is nothing to sell
    into, so giving up is the correct reading of a timeout rather than a guess about one.
    """
    already_open = list(GLOBAL_CACHE.Trading.Merchant.GetOfferedItems())
    if already_open and merchant_stocks_kits(already_open):
        return already_open

    candidates = merchant_candidates()
    if not candidates:
        ConsoleLog(MODULE_NAME, "No NPC within range to try.", Console.MessageType.Warning)
        return []

    tried = 0
    for agent_id in candidates[:MERCHANT_TRY_LIMIT]:
        tried += 1
        x, y = Agent.GetXY(agent_id)
        yield from Routines.Yield.Movement.FollowPath([(x, y)], timeout=MERCHANT_WALK_TIMEOUT_MS)
        # Interact does not set the target on its own, so the target has to be changed first or the
        # framework's interact resolves against whatever was selected before.
        yield from Routines.Yield.Agents.ChangeTarget(agent_id)
        yield from Routines.Yield.Agents.InteractAgent(agent_id)

        offered = yield from wait_for_merchant_window()
        if offered and merchant_stocks_kits(offered):
            return offered

    ConsoleLog(
        MODULE_NAME,
        f"Tried {tried} of {len(candidates)} nearby NPC(s); none opened a merchant window that " "stocks kits.",
        Console.MessageType.Warning,
    )
    return []


def sell_item(item_id: int, quantity: int):
    """True when the item was observed leaving the bags."""
    value = int(GLOBAL_CACHE.Item.Properties.GetValue(item_id) or 0)
    GLOBAL_CACHE.Trading.Merchant.SellItem(item_id, value * max(1, quantity))
    return (yield from wait_for_item_to_leave(item_id, quantity))


# ---------------------------------------------------------------- widget


class InventoryLite:
    def __init__(self):
        self.initialized = False
        self.active = None
        self.active_label = ""
        self.active_since = 0.0
        self.auto_identify = True
        self.id_kit_target = 1
        self.salvage_kit_target = 1
        self.salvage_residual = False
        self.rules_tab = ACTIONS[0]
        self.config_was_open = False
        self.show_config = False
        self.bar_size: tuple[float, float] = (0.0, 0.0)
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
        """Global, matching the rules document.

        Every setting here changes what a rule DOES -- whether the residual salvages, how many kits a
        merchant run buys back. Splitting those per account would mean the same shared rule produced
        different outcomes on different characters, which is the one thing shared rules exist to stop.
        """
        handler = Settings(MODULE_NAME, scope="global")
        return handler if handler.is_ready() else None

    def load_settings(self):
        handler = self.settings_handler()
        if handler is None:
            return False
        self.auto_identify = handler.get_bool(SETTINGS_SECTION, "AutoIdentify", True)
        self.id_kit_target = max(0, handler.get_int(SETTINGS_SECTION, "IDKitTarget", 1))
        self.salvage_kit_target = max(0, handler.get_int(SETTINGS_SECTION, "SalvageKitTarget", 1))
        # Off by default and staying that way: salvaging is the one action that destroys the item, so
        # the residual has to be something you turned on rather than something you inherited.
        self.salvage_residual = handler.get_bool(SETTINGS_SECTION, "SalvageResidual", False)
        self.rules = load_rules()
        seeded = [
            name
            for name, added in (
                (UNNAMED_RULE_NAME, seed_unnamed_rule(self.rules)),
                (SALVAGE_RULE_NAME, seed_salvage_rule(self.rules)),
            )
            if added
        ]
        if seeded:
            save_rules(self.rules)
            ConsoleLog(
                MODULE_NAME,
                "Added the %s rule(s). Nothing moves until you press a button; each tab has its own "
                "preview." % ", ".join("'%s'" % n for n in seeded),
                Console.MessageType.Info,
            )
        return True

    def save_settings(self):
        handler = self.settings_handler()
        if handler is None:
            return
        handler.set_bool(SETTINGS_SECTION, "AutoIdentify", self.auto_identify)
        handler.set_int(SETTINGS_SECTION, "IDKitTarget", self.id_kit_target)
        handler.set_int(SETTINGS_SECTION, "SalvageKitTarget", self.salvage_kit_target)
        handler.set_bool(SETTINGS_SECTION, "SalvageResidual", self.salvage_residual)

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
        self.run(self.salvage(), "Salvage")

    def salvage(self):
        """Salvage what the salvage rules claim. Irreversible, so the report is written first."""
        facts_by_id = yield from self.gather()
        matched, rows = salvage_matches(self.rules, facts_by_id, residual=self.salvage_residual)
        self.report_title = "%d of %d item(s) matched a salvage rule" % (len(matched), len(facts_by_id))
        self.report_columns = PREVIEW_COLUMNS
        self.report_rows = rows

        if not matched:
            ConsoleLog(MODULE_NAME, "Nothing matches the salvage rules.", Console.MessageType.Info)
            return
        if GLOBAL_CACHE.Inventory.GetFirstSalvageKit() == 0:
            ConsoleLog(MODULE_NAME, "Out of salvage kits.", Console.MessageType.Warning)
            return
        yield from Routines.Yield.Items.SalvageItemsAndVerify(matched)

    def gather(self):
        """Bag facts, with the shared rules re-read first.

        Every action goes through here, so no run can act on a rule set another account has already
        replaced -- including a run a peer asked for over ShMem, where the rules almost certainly
        changed on the sender rather than here.
        """
        self.rules = reload_rules(self.rules)
        return (yield from gather_facts())

    def start_scan(self):
        self.run(self.scan(), "Scan")

    def scan(self):
        """Read every bag item's facts and show them -- exactly what the rules will see."""
        facts_by_id = yield from self.gather()
        self.report_title = "%d item(s) read" % len(facts_by_id)
        self.report_columns = SCAN_COLUMNS
        self.report_rows = [facts_row(f) for f in facts_by_id.values()]

    def start_preview(self, action: str):
        self.run(self.preview(action), "Preview %s" % ACTION_TABS[action])

    def preview(self, action: str):
        facts_by_id = yield from self.gather()
        matched, rows = match_rules(
            self.rules,
            facts_by_id,
            action,
            residual=action == "salvage" and self.salvage_residual,
        )
        self.report_title = "%d of %d item(s) would be %sed" % (
            len(matched),
            len(facts_by_id),
            ACTION_VERBS[action].lower(),
        )
        self.report_columns = PREVIEW_COLUMNS
        self.report_rows = rows

    def start_deposit(self):
        if not [r for r in self.rules if r.enabled and r.action == "keep"] and not material_count_in_bags():
            ConsoleLog(MODULE_NAME, "No Keep rule is enabled and there are no materials.", Console.MessageType.Info)
            return
        self.run(self.deposit(), "Deposit")

    def deposit(self):
        facts_by_id = yield from self.gather()
        matched, rows = deposit_matches(self.rules, facts_by_id)
        self.report_title = "%d of %d item(s) matched" % (len(matched), len(facts_by_id))
        self.report_columns = PREVIEW_COLUMNS
        self.report_rows = rows
        if not (yield from ensure_storage_open()):
            return
        yield from deposit_all_materials()
        if not matched:
            ConsoleLog(MODULE_NAME, "Nothing matches the deposit rules.", Console.MessageType.Info)
            return

        deposited = 0
        unconfirmed = 0
        for item_id in matched:
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

    def start_merchant(self):
        self.run(self.merchant_run(), "Merchant")

    def merchant_run(self):
        """Walk to the nearest merchant, sell what the sell rules claim, then top the kits back up.

        The facts are gathered BEFORE the walk so the report exists even when the trip fails, and so a
        rule that cannot be satisfied never sends you across an outpost for nothing.
        """
        facts_by_id = yield from self.gather()
        matched, rows = sell_matches(self.rules, facts_by_id)
        self.report_title = "%d of %d item(s) matched a sell rule" % (len(matched), len(facts_by_id))
        self.report_columns = PREVIEW_COLUMNS
        self.report_rows = rows

        restocking = self.id_kit_target > 0 or self.salvage_kit_target > 0
        if not matched and not restocking:
            ConsoleLog(
                MODULE_NAME,
                "Nothing matches the sell rules and both kit targets are 0 - staying put.",
                Console.MessageType.Info,
            )
            return

        offered = yield from open_merchant()
        if not offered:
            return

        sold = 0
        unconfirmed = 0
        for item_id in matched:
            live = live_bag_items()
            if item_id not in live:
                continue
            quantity = live[item_id][1]
            if (yield from sell_item(item_id, quantity)):
                sold += 1
            else:
                unconfirmed += 1
                ConsoleLog(
                    MODULE_NAME,
                    f"Could not observe item {item_id} leaving the bags within {DEPOSIT_CONFIRM_TIMEOUT_MS} ms "
                    f"after selling it - moving on.",
                    Console.MessageType.Warning,
                )
            yield from Routines.Yield.wait(MOVE_DELAY_MS)

        if matched:
            tail = f" ({unconfirmed} unconfirmed)" if unconfirmed else ""
            ConsoleLog(
                MODULE_NAME,
                f"Sold {sold} of {len(matched)} item(s){tail}.",
                Console.MessageType.Success if sold == len(matched) else Console.MessageType.Warning,
            )

        if restocking:
            yield from Routines.Yield.Merchant.RestockKitsToTarget(self.id_kit_target, self.salvage_kit_target)
            ConsoleLog(
                MODULE_NAME,
                f"Kits topped up to {self.id_kit_target} ID / {self.salvage_kit_target} salvage.",
                Console.MessageType.Info,
            )

    def full_pass(self):
        """Deposit, sell, then tidy both ends. Identical work whether asked for here or by a peer.

        Deposit runs first so the Keep rules have claimed everything they want before anything is
        offered to a merchant, and organising runs last so it sorts what is actually left. Note that
        the materials sweep inside deposit is the client's own button: it takes every material,
        including any a Sell rule would have offered to the merchant.
        """
        yield from self.deposit()
        yield from self.merchant_run()
        yield from self.organize_all()

    def start_full_pass(self):
        """The multi-account button: ask the peers, then do the same work here.

        The messages go out BEFORE the local run so every account works at once. Running locally
        first would leave the peers idle for the length of a merchant trip.
        """
        send_full_pass()
        self.run(self.full_pass(), "Full pass")

    def remote_full_pass(self, index: int, account_email: str):
        """Run the chain for a peer's request, then release the message however it ends."""
        try:
            if not Routines.Checks.Map.IsOutpost():
                ConsoleLog(
                    MODULE_NAME,
                    "Ignoring the request: storage and merchants are only reachable from an outpost.",
                    Console.MessageType.Warning,
                )
                return
            yield from self.full_pass()
        finally:
            GLOBAL_CACHE.ShMem.MarkMessageAsFinished(account_email, index)

    def poll_full_pass_requests(self):
        """Claim a peer's request. One at a time: a second would fight the first for storage."""
        if self.busy:
            return
        account_email = Player.GetAccountEmail()
        if not account_email:
            return
        try:
            index, message = GLOBAL_CACHE.ShMem.GetNextMessage(account_email)
        except Exception:
            return
        if index == -1 or message is None:
            return
        if int(getattr(message, "Command", SharedCommandType.NoCommand)) != int(SharedCommandType.DepositAndOrganize):
            return
        # Claim it before running: GetNextMessage skips Running messages, so this is what stops both
        # the Messaging panel and the next frame of this poll from picking it up again.
        GLOBAL_CACHE.ShMem.MarkMessageAsRunning(account_email, index)
        ConsoleLog(MODULE_NAME, f"Full pass requested by {message.SenderEmail}.", Console.MessageType.Info)
        self.run(self.remote_full_pass(index, account_email), "Remote full pass")

    def start_organize(self):
        self.run(self.organize_all(), "Organize")

    def organize_all(self):
        """Both ends where both exist.

        Outside an outpost the chest is simply not there, so the carry bags are the whole job rather
        than a failed reach for storage. Storage goes first so the chest window opens once, up front,
        instead of landing on top of a bag sort already in progress.
        """
        if Routines.Checks.Map.IsOutpost():
            yield from self.organize_storage()
        yield from organize(inventory_bags)

    def organize_storage(self):
        if not (yield from ensure_storage_open()):
            return
        yield from organize(storage_bags)

    # -- drawing. Reads NOTHING off an item: every value shown was gathered by a routine. --

    def draw_buttons(self):
        frame = Frame(FrameId.InventoryBagsWindow)
        if not frame.exists:
            return
        left, top, right, bottom = frame.coords()
        if right <= left:
            return

        # An auto-sized window only knows its extent after it has drawn, so the left edge is placed
        # from last frame's measurement. Frame one lands flush against the bags and every frame after
        # sits beside them.
        width, _height = self.bar_size
        x = left - width - BAR_GAP
        if x < 0.0:
            x = right + BAR_GAP
        PyImGui.set_next_window_pos(x, top)
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
                self.bar_size = PyImGui.get_window_size()
        except Exception as exc:
            self.log_once("bar", f"Button bar draw failed: {exc}\n{traceback.format_exc()}")
        finally:
            PyImGui.end()

    def bar_actions(self):
        """Groups of (icon, palette, tooltip, callable), drawn top to bottom with a gap between groups.

        Outpost-only buttons are absent outside one rather than greyed: the chest is unreachable, so
        the column shrinks to the two things that still work anywhere plus the gear.

        Opening the chest is its own group because it is the only button that does nothing to your
        items -- it just puts a window on screen. Everything below it acts, in escalating order.
        """
        salvage = (ICON_SALVAGE, BUTTON_SALVAGE, "Salvage everything the Salvage rules claim", self.start_salvage)
        if not Routines.Checks.Map.IsOutpost():
            return (
                (
                    (ICON_ORGANIZE, BUTTON_ORGANIZE, "Sort and condense the carry bags", self.start_organize),
                    salvage,
                ),
            )

        return (
            ((ICON_XUNLAI, BUTTON_XUNLAI, "Open storage", Inventory.OpenXunlaiWindow),),
            (
                (
                    ICON_ORGANIZE,
                    BUTTON_ORGANIZE,
                    "Sort and condense storage and the carry bags",
                    self.start_organize,
                ),
                (
                    ICON_DEPOSIT,
                    BUTTON_DEPOSIT,
                    "Deposit all materials, then everything the Keep rules claim",
                    self.start_deposit,
                ),
                (
                    ICON_MERCHANT,
                    BUTTON_MERCHANT,
                    "Walk to the nearest merchant, sell what the Sell rules claim, restock kits",
                    self.start_merchant,
                ),
                salvage,
                (
                    ICON_BROADCAST,
                    BUTTON_BROADCAST,
                    "EVERY account, this one included: deposit, sell at the merchant, organize both ends",
                    self.start_full_pass,
                ),
            ),
        )

    def draw_bar_body(self):
        """A vertical column. Nothing calls same_line, so every button lands on its own row."""
        if self.busy:
            if bar_button(ICON_CANCEL, BUTTON_CANCEL, f"Stop {self.active_label.lower()}"):
                self.cancel()
            return

        for index, group in enumerate(self.bar_actions()):
            if index:
                PyImGui.dummy((1.0, BAR_GROUP_GAP))
            for icon, palette, tooltip, action in group:
                if bar_button(icon, palette, tooltip):
                    action()

        if bar_button(ICON_CONFIG, BUTTON_CONFIG, "Rules, report and settings"):
            self.show_config = not self.show_config

    def draw_config(self):
        if not self.show_config:
            self.config_was_open = False
            return
        # On opening, not every frame: a per-frame disk read to catch an edit nobody is making.
        if not self.config_was_open:
            self.config_was_open = True
            self.rules = reload_rules(self.rules)
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
        PyImGui.text_disabled("Every button runs its own tab's rules. Keep wins over Sell, Sell over Salvage.")

        PyImGui.push_item_width(110)
        id_target = PyImGui.slider_int("ID kits to keep##idkit", int(self.id_kit_target), 0, 5)
        salvage_target = PyImGui.slider_int("Salvage kits to keep##salvkit", int(self.salvage_kit_target), 0, 5)
        PyImGui.pop_item_width()
        if (id_target, salvage_target) != (self.id_kit_target, self.salvage_kit_target):
            self.id_kit_target, self.salvage_kit_target = id_target, salvage_target
            self.save_settings()
        PyImGui.text_disabled("The Merchant button buys only the shortfall. 0 disables restocking.")

        PyImGui.separator()
        self.draw_rules()
        PyImGui.separator()
        self.draw_report()

    def draw_rules(self):
        PyImGui.text("Rules")
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
        elif PyImGui.small_button("Scan items"):
            self.start_scan()
        PyImGui.separator()

        # begin_tab_bar sits outside the guard for the same reason begin() does: end_tab_bar must run
        # once per successful begin and never for one that failed.
        if not PyImGui.begin_tab_bar("##%sRuleTabs" % MODULE_NAME):
            return
        try:
            for action in ACTIONS:
                if not PyImGui.begin_tab_item(ACTION_TABS[action]):
                    continue
                try:
                    self.rules_tab = action
                    self.draw_rules_tab(action)
                finally:
                    PyImGui.end_tab_item()
        finally:
            PyImGui.end_tab_bar()

    def draw_rules_tab(self, action: str):
        PyImGui.text_colored(ACTION_BLURBS[action], WARN if action == "salvage" else GRAY)

        if action == "salvage":
            residual = PyImGui.checkbox("Salvage anything no rule claimed##residual", self.salvage_residual)
            if residual != self.salvage_residual:
                self.salvage_residual = residual
                self.save_settings()
            if self.salvage_residual:
                PyImGui.text_colored(
                    "ON: every identified, salvageable item not claimed by a Keep, Sell or Salvage "
                    "rule WILL be salvaged. Preview before pressing Salvage.",
                    WARN,
                )

        if not self.busy:
            if PyImGui.small_button("Preview##prev_%s" % action):
                self.start_preview(action)
            PyImGui.same_line(0, 6)
            if PyImGui.small_button("New rule##new_%s" % action):
                self.rules.append(Rule(name="Rule %d" % (len(self.rules) + 1), action=action))
                save_rules(self.rules)
        PyImGui.separator()

        # Enumerated over the whole list so the index stays the real position: draw_rule pops by it.
        shown = 0
        for index, rule in enumerate(list(self.rules)):
            if rule.action == action:
                self.draw_rule(index, rule)
                shown += 1
        if not shown:
            PyImGui.text_colored("No %s rules yet." % ACTION_TABS[action].lower(), GRAY)

    def draw_rule(self, index: int, rule: Rule):
        tag = str(index)
        enabled = PyImGui.checkbox("##en_%s" % tag, rule.enabled)
        if enabled != rule.enabled:
            rule.enabled = enabled
            save_rules(self.rules)
        PyImGui.same_line(0, 6)
        header = "%s  (%d criteria)###hdr_%s" % (rule.name, rule.criteria_count(), tag)
        if not PyImGui.collapsing_header(header):
            return

        typed = PyImGui.input_text("Name##nm_%s" % tag, rule.name)
        if typed != rule.name and typed.strip():
            rule.name = typed.strip()
            save_rules(self.rules)

        match_all = PyImGui.checkbox("Match ALL##all_%s" % tag, rule.match_all)
        if match_all != rule.match_all:
            rule.match_all = match_all
            save_rules(self.rules)
        for other in ACTIONS:
            if other == rule.action:
                continue
            PyImGui.same_line(0, 6)
            if PyImGui.small_button("move to %s##mv_%s_%s" % (ACTION_TABS[other], tag, other)):
                rule.action = other
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

        unnamed = PyImGui.checkbox("Nothing could name it##unnamed_%s" % tag, rule.unnamed_only)
        if unnamed != rule.unnamed_only:
            rule.unnamed_only = unnamed
            save_rules(self.rules)
        if rule.unnamed_only:
            PyImGui.text_colored("    matches models the name ladder gave up on - shown as 'model <id>'", GRAY)

        PyImGui.text_colored("Rarity", GRAY)
        for name in RARITY_NAMES:
            on = name in rule.rarities
            if PyImGui.checkbox("%s##rar_%s_%s" % (name, tag, name), on) != on:
                rule.rarities = tuple(r for r in rule.rarities if r != name) if on else rule.rarities + (name,)
                save_rules(self.rules)
            PyImGui.same_line(0, 6)
        PyImGui.new_line()

        PyImGui.text_colored("Kind", GRAY)
        for position, name in enumerate(ITEM_GROUPS):
            on = name in rule.item_groups
            if PyImGui.checkbox("%s##grp_%s_%s" % (name, tag, name), on) != on:
                rule.item_groups = tuple(g for g in rule.item_groups if g != name) if on else rule.item_groups + (name,)
                save_rules(self.rules)
            # No same_line at a row boundary or on the last one: the next widget then starts its own
            # line by itself, so nothing here has to close the row.
            if (position + 1) % GROUPS_PER_ROW and position + 1 < len(ITEM_GROUPS):
                PyImGui.same_line(0, 6)

        if PyImGui.collapsing_header("Specific types##types_%s" % tag):
            PyImGui.text_colored("Widens Kind rather than narrowing it: groups and types are ORed.", GRAY)
            for position, name in enumerate(ITEM_TYPES):
                on = name in rule.item_types
                if PyImGui.checkbox("%s##typ_%s_%s" % (name, tag, name), on) != on:
                    rule.item_types = (
                        tuple(t for t in rule.item_types if t != name) if on else rule.item_types + (name,)
                    )
                    save_rules(self.rules)
                if (position + 1) % TYPES_PER_ROW and position + 1 < len(ITEM_TYPES):
                    PyImGui.same_line(0, 6)

        picked = rule.item_groups + rule.item_types
        if picked:
            PyImGui.text_colored("    kind is any of: %s" % display_safe(", ".join(picked)), GRAY)

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


def reap_stale_deposit_requests():
    """Release requests that were never claimed.

    Inbox slots are a small pool shared by every account and nothing in the framework expires them, so
    a request sent to a client with this widget switched off would hold a slot for everyone, forever.
    Running requests are left alone: those have an owner working through them.
    """
    now = PySystem.get_tick_count64()
    for index, message in GLOBAL_CACHE.ShMem.GetAllMessages():
        if message is None or not getattr(message, "Active", False) or getattr(message, "Running", False):
            continue
        if int(getattr(message, "Command", SharedCommandType.NoCommand)) != int(SharedCommandType.DepositAndOrganize):
            continue
        if now - int(getattr(message, "Timestamp", 0) or 0) < DEPOSIT_REQUEST_TTL_MS:
            continue
        receiver = str(getattr(message, "ReceiverEmail", "") or "")
        if not receiver:
            continue
        GLOBAL_CACHE.ShMem.MarkMessageAsFinished(receiver, index)
        ConsoleLog(
            MODULE_NAME,
            f"Dropped an unanswered deposit request for {receiver} - is Inventory Lite enabled there?",
            Console.MessageType.Warning,
        )


def send_full_pass():
    """Ask every OTHER account to run the full pass. This account is not messaged.

    A client cannot claim its own ShMem message -- the sender is skipped here and start_full_pass
    runs the same chain locally instead, so "every account" really does mean every account.

    Fire and forget: each client runs the chain against its own bags, so nothing here waits on them.
    Peers not in an outpost decline the request rather than queueing it.
    """
    try:
        reap_stale_deposit_requests()
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Could not check for stale requests: {exc}", Console.MessageType.Warning)
    sender = Player.GetAccountEmail()
    if not sender:
        ConsoleLog(MODULE_NAME, "No account email yet; not sending.", Console.MessageType.Warning)
        return
    sent = 0
    for account in GLOBAL_CACHE.ShMem.GetAllAccountData():
        email = str(getattr(account, "AccountEmail", "") or "")
        if not email or email == sender:
            continue
        if GLOBAL_CACHE.ShMem.SendMessage(sender, email, SharedCommandType.DepositAndOrganize) >= 0:
            sent += 1
        else:
            ConsoleLog(MODULE_NAME, f"Could not queue a request for {email}.", Console.MessageType.Warning)
    ConsoleLog(
        MODULE_NAME,
        (
            f"Asked {sent} other account(s) to deposit, sell and organize; running here too."
            if sent
            else "No other accounts to ask; running here only."
        ),
        Console.MessageType.Success if sent else Console.MessageType.Info,
    )


def configure():
    widget.show_config = True


def main():
    if not widget.initialized:
        if not widget.load_settings():
            return
        widget.initialized = True

    widget.pump()
    widget.poll_full_pass_requests()
    widget.tick_auto_identify()
    widget.draw_buttons()
    widget.draw_config()


if __name__ == "__main__":
    main()
