"""Item naming shared by InventoryLite and TeamInventoryViewer: one mod database, one cache, one ladder."""

import os
import re

import PySystem

from Core.enums import ModelID
from Core.enums_src.Item_enums import ItemType
from Core.GlobalCache import GLOBAL_CACHE
from Core.Py4GWcorelib import Console
from Core.Py4GWcorelib import ConsoleLog
from Core.py4gwcorelib_src.JsonFactory import JsonFactory
from Core.Routines import Routines
from Sources.marks_sources.mods_parser import MatchedRuneInfo
from Sources.marks_sources.mods_parser import MatchedWeaponModInfo
from Sources.marks_sources.mods_parser import ModDatabase
from Sources.marks_sources.mods_parser import parse_modifiers

MODULE_NAME = "ItemNaming"

MODS_DATA_DIR = "Sources/marks_sources/mods_data"

#: Shared by every widget and every account. TeamInventoryViewer built it first, which is why it still
#: carries that name -- the entries are plain {model_id: base_name} and belong to neither widget.
NAME_CACHE_DOC = "TeamInventoryViewer/model_ids.json"

#: InventoryLite's own map before the two were merged. Read once to seed, never written.
LEGACY_NAME_DOC = "Widgets/Items/InventoryLiteNames.json"


ATTRIBUTES = {
    "Axe Mastery",
    "Hammer Mastery",
    "Swordsmanship",
    "Tactics",
    "Strength",
    "Marksmanship",
    "Beast Mastery",
    "Wilderness Survival",
    "Expertise",
    "Divine Favor",
    "Healing Prayers",
    "Protection Prayers",
    "Smiting Prayers",
    "Blood Magic",
    "Curses",
    "Death Magic",
    "Soul Reaping",
    "Domination Magic",
    "Fast Casting",
    "Illusion Magic",
    "Inspiration Magic",
    "Energy Storage",
    "Air Magic",
    "Earth Magic",
    "Fire Magic",
    "Water Magic",
    "Critical Strikes",
    "Dagger Mastery",
    "Deadly Arts",
    "Shadow Arts",
    "Channeling Magic",
    "Communing",
    "Restoration Magic",
    "Spawning Power",
    "Command",
    "Leadership",
    "Motivation",
    "Spear Mastery",
    "Earth Prayers",
    "Mysticism",
    "Scythe Mastery",
    "Wind Prayers",
}

NON_ATTRIBUTE_RUNES = {"Vitae", "Vigor", "Attunement", "Clarity", "Purity", "Recovery", "Restoration", "Absorption"}

ARMOR_RUNE_SUFFIXES = {
    f"of {mod}{rune}" for rune in ATTRIBUTES | NON_ATTRIBUTE_RUNES for mod in ["", "Minor ", "Major ", "Superior "]
}

WEAPON_ATTRIBUTE_SUFFIXES = {f"of {attr}" for attr in ATTRIBUTES}

OTHER_WEAPON_SUFFIXES = {
    "of Defense",
    "of Shelter",
    "of Warding",
    "of Enchanting",
    "of Swiftness",
    "of Aptitude",
    "of Fortitude",
    "of Devotion",
    "of Endurance",
    "of Valor",
    "of Mastery",
    "of Quickening",
    "of Memory",
    "of the Warrior",
    "of the Ranger",
    "of the Necromancer",
    "of the Elementalist",
    "of the Mesmer",
    "of the Monk",
    "of the Ritualist",
    "of the Assassin",
    "of the Paragon",
    "of the Dervish",
    "of Charrslaying",
    "of Demonslaying",
    "of Dragonslaying",
    "of Dwarfslaying",
    "of Giantslaying",
    "of Ogreslaying",
    "of Pruning",
    "of Tenguslaying",
    "of Trollslaying",
    "of Undeadbane",
    "of Skeletonslaying",
    "of Deathbane",
}

ALL_SUFFIXES = ARMOR_RUNE_SUFFIXES | WEAPON_ATTRIBUTE_SUFFIXES | OTHER_WEAPON_SUFFIXES

WEAPON_PREFIXES = {
    "Barbed",
    "Crippling",
    "Cruel",
    "Heavy",
    "Poisonous",
    "Silencing",
    "Ebon",
    "Fiery",
    "Icy",
    "Shocking",
    "Furious",
    "Sundering",
    "Vampiric",
    "Zealous",
    "Adept",
    "Defensive",
    "Hale",
    "Insightful",
    "Swift",
}

INSIGNIAS = {
    "Survivor",
    "Radiant",
    "Stalwart",
    "Brawler's",
    "Blessed",
    "Herald's",
    "Sentry's",
    "Knight's",
    "Stonefist",
    "Dreadnought",
    "Sentinel's",
    "Lieutenant's",
    "Frostbound",
    "Pyrebound",
    "Stormbound",
    "Scout's",
    "Earthbound",
    "Beastmaster's",
    "Wanderer's",
    "Disciple's",
    "Anchorite's",
    "Bloodstained",
    "Tormentor's",
    "Bonelace",
    "Minion Master's",
    "Blighter's",
    "Undertaker's",
    "Virtuoso's",
    "Artificer's",
    "Prodigy's",
    "Hydromancer",
    "Geomancer",
    "Pyromancer",
    "Aeromancer",
    "Prismatic",
    "Vanguard's",
    "Infiltrator's",
    "Saboteur's",
    "Nightstalker's",
    "Shaman's",
    "Ghostforge",
    "Mystic's",
    "Centurion's",
    "Windwalker",
    "Forsaken",
}

NORMALIZED_PREFIXES = {p.lower().rstrip("'s") for p in WEAPON_PREFIXES | INSIGNIAS}


# ---------------------------------------------------------------- mod database


MOD_DB = None
MOD_DB_LOADED = False


def mod_database():
    """The LootEx rune / weapon-mod tables, loaded once per session. None when they could not be read."""
    global MOD_DB, MOD_DB_LOADED
    if MOD_DB_LOADED:
        return MOD_DB
    MOD_DB_LOADED = True
    try:
        # A real directory join, not a dotted module path: layout.toml pins Sources/marks_sources
        # partly because this string is a literal path (RS-002).
        MOD_DB = ModDatabase.load(os.path.join(PySystem.Console.get_projects_path(), MODS_DATA_DIR))
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Could not load the mod database: {exc}", Console.MessageType.Error)
        MOD_DB = None
    return MOD_DB


# ---------------------------------------------------------------- name cache


class ModelNameCache:
    """Shared {model_id: base_name} map over one global-scope document.

    Global scope is multibox-safe: saves merge every client's discoveries through a cross-process
    lock, so accounts scanning different items all contribute to the same map without clobbering.
    """

    def __init__(self):
        self.names: dict[str, str] = {}
        self.loaded = False

    def doc(self):
        return JsonFactory(NAME_CACHE_DOC, "global")

    def load(self, force: bool = False) -> dict:
        if self.loaded and not force:
            return self.names
        try:
            raw = self.doc().items("")
        except Exception:
            raw = {}
        self.names = {str(k): str(v) for k, v in raw.items() if v and not str(v).startswith("enc:")}
        self.loaded = True
        self.seed_from_legacy()
        return self.names

    def seed_from_legacy(self):
        """Fold InventoryLite's old private map in once. Its entries are mostly models this map lacks."""
        try:
            legacy = JsonFactory(LEGACY_NAME_DOC, "global").get_json("names", {})
        except Exception:
            return
        if not isinstance(legacy, dict):
            return
        added = 0
        for model_id, name in legacy.items():
            key = str(model_id)
            if key in self.names or not name or str(name).startswith("enc:"):
                continue
            cleaned = clean_gw_item_name(str(name))[0] or str(name)
            self.names[key] = cleaned
            try:
                self.doc().set(key, cleaned)
            except Exception:
                continue
            added += 1
        if added:
            ConsoleLog(MODULE_NAME, f"Merged {added} name(s) from the older InventoryLite map.")

    def get(self, model_id, default: str = "") -> str:
        if not self.loaded:
            self.load()
        return self.names.get(str(model_id), default)

    def remember(self, model_id, name: str):
        """Write a RESOLVED name down so no later scan has to ask for it again.

        A raw encoding is not a name: storing one marks the model resolved and it is never retried,
        which is how `enc:...` once ended up displayed as an item's name.
        """
        if not model_id or not name or name.startswith("enc:"):
            return
        key = str(model_id)
        if self.names.get(key) == name:
            return
        self.names[key] = name
        try:
            self.doc().set(key, name)
        except Exception as exc:
            ConsoleLog(
                MODULE_NAME, f"Could not store the name for model {model_id}: {exc}", Console.MessageType.Warning
            )


NAME_CACHE = ModelNameCache()


# ---------------------------------------------------------------- strippers


def strip_markup(text: str) -> str:
    """GW names arrive wrapped in colour and style codes. Storage keeps the words only."""
    cleaned = re.sub(r"<c=[^>]+>(.*?)</c>", r"\1", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]*>", "", cleaned)
    cleaned = re.sub(r"\{[^}]*\}", "", cleaned)
    return " ".join(cleaned.split())


def strip_mod_words(full_name: str, prefix: str = "", suffix: str = "") -> str:
    """The base name with the mod words the parser matched on THIS item taken off.

    A weapon name reads "Zealous Scythe of Fortitude" -- storing that against the MODEL id would label
    every scythe of that skin with one roll's mods. The parser already told us which mod words are on
    this item, so they can be removed by name rather than guessed at.
    """
    base = full_name or ""
    for word in (prefix, suffix):
        if not word:
            continue
        base = re.sub(r"\b%s\b" % re.escape(word), " ", base, flags=re.IGNORECASE)
        head = word.split()[0] if word.split() else ""
        if head and head.lower() not in ("of", "the"):
            base = re.sub(r"\b%s\b" % re.escape(head), " ", base, flags=re.IGNORECASE)
    base = re.sub(r"\bof\s*$", "", " ".join(base.split()), flags=re.IGNORECASE)
    return " ".join(base.split())


def clean_gw_item_name(item_name: str):
    """Base name with at most one leading prefix/insignia and one trailing suffix/rune removed.

    Returns (base_name, removed_prefix, removed_suffix). Works off fixed word tables, so it catches
    mods the parser did not match -- and misses any the tables do not list.
    """
    if not item_name:
        return "", None, None

    words = item_name.strip().split()
    if not words:
        return "", None, None

    result = []
    removed_prefix = None
    removed_suffix = None
    index = 0
    count = len(words)

    if index < count:
        original = words[index].rstrip(".,!?")
        if original.lower().rstrip("'s") in NORMALIZED_PREFIXES:
            removed_prefix = original
            index += 1

    while index < count:
        remaining = words[index:]
        matched = False
        for length in range(min(5, len(remaining)), 0, -1):
            candidate = " ".join(remaining[:length]).rstrip(".,!?")
            if candidate in ALL_SUFFIXES:
                removed_suffix = candidate
                matched = True
                break
        if matched:
            break
        result.append(words[index])
        index += 1

    # Digits are stack counts leaking in from a display string ("2 Stalker's Rations"), never part of
    # a GW1 skin name.
    base_name = "".join(c for c in " ".join(result).strip() if not c.isdigit())
    return " ".join(base_name.split()), removed_prefix, removed_suffix


def base_name_from_full(full_name: str, prefix: str = "", suffix: str = "") -> str:
    """The one way a server-returned name becomes something safe to key by model id.

    Both passes run because they fail in opposite places: the parser pass handles mods no word table
    lists but does nothing when nothing was matched, and the table pass catches those plus stack
    counts but only knows the mods it lists.
    """
    return clean_gw_item_name(strip_mod_words(strip_markup(full_name), prefix, suffix))[0]


# ---------------------------------------------------------------- mods


def modifier_triples(item_id: int) -> list[tuple[int, int, int]]:
    """(identifier, arg1, arg2) per modifier, off the item the cache already holds."""
    out: list[tuple[int, int, int]] = []
    for modifier in GLOBAL_CACHE.Item.Mods.GetModifiers(item_id) or []:
        try:
            out.append((modifier.GetIdentifier(), modifier.GetArg1(), modifier.GetArg2()))
        except Exception:
            continue
    return out


def parse_item_mods(item_id: int, model_id: int, item_type_value=None):
    """The parsed mod result for an item, or None when it has no mods or an unmapped type."""
    database = mod_database()
    if database is None:
        return None
    triples = modifier_triples(item_id)
    if not triples:
        return None
    if item_type_value is None:
        item_type_value, _name = GLOBAL_CACHE.Item.GetItemType(item_id)
    try:
        item_type = ItemType(int(item_type_value))
    except ValueError:
        return None
    try:
        return parse_modifiers(modifiers=triples, item_type=item_type, model_id=model_id, db=database)
    except Exception:
        return None


def mod_display_name(matched) -> str:
    """A matched mod's name. Runes and weapon mods carry it on different attributes."""
    if matched is None:
        return ""
    if isinstance(matched, MatchedWeaponModInfo):
        return str(getattr(matched.weapon_mod, "name", "") or "")
    if isinstance(matched, MatchedRuneInfo):
        return str(getattr(matched.rune, "name", "") or "")
    holder = getattr(matched, "rune", None) or getattr(matched, "weapon_mod", None)
    return str(getattr(holder, "name", "") or "")


def mod_names(item_id: int, model_id: int) -> tuple[str, str, str]:
    """(prefix, suffix, inherent) as display names, empty where the slot is unmatched."""
    parsed = parse_item_mods(item_id, model_id)
    if parsed is None:
        return "", "", ""
    return (
        mod_display_name(getattr(parsed, "prefix", None)),
        mod_display_name(getattr(parsed, "suffix", None)),
        mod_display_name(getattr(parsed, "inherent", None)),
    )


# ---------------------------------------------------------------- the ladder


def known_base_name(model_id: int, prefer_cache: bool = False) -> str:
    """The name without asking the server: the ModelID enum and the shared cache. "" when neither knows.

    `prefer_cache` flips the order for weapons and armor, where a learned name is the specific skin
    ("Crenellated Scythe") and the enum, when it has the model at all, is the generic one.
    """
    cached = NAME_CACHE.get(model_id)
    if prefer_cache and cached:
        return cached
    try:
        return ModelID(model_id).name.replace("_", " ")
    except Exception:
        pass
    return cached


def request_names(item_ids):
    """Ask for every unresolved name up front so the server answers them in parallel.

    Without this each unknown model stalls the caller for up to 2s in turn, which is most of what made
    naming feel slow.
    """
    for item_id in item_ids:
        if not item_id:
            continue
        try:
            if not GLOBAL_CACHE.Item.IsNameReady(item_id):
                GLOBAL_CACHE.Item.RequestName(item_id)
        except Exception:
            continue


def fetch_base_name(item_id: int, model_id: int, prefix: str = "", suffix: str = ""):
    """Ask the game for this item's name, reduce it to a base name, and write it to the shared cache.

    The only rung that talks to the server, so it is last and runs once per MODEL rather than per item.
    """
    try:
        full = yield from Routines.Yield.Items.GetItemNameByItemID(item_id)
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Name lookup failed for model {model_id}: {exc}", Console.MessageType.Warning)
        return ""
    name = base_name_from_full(full or "", prefix, suffix)
    if name:
        NAME_CACHE.remember(model_id, name)
    return name


# ---------------------------------------------------------------- composed display names


def armor_display_name(item_id: int, model_id: int) -> str:
    """Base name plus its rune and insignia, as TeamInventoryViewer shows armor."""
    base = known_base_name(model_id, prefer_cache=True)
    if not base:
        return ""
    prefix, suffix, _inherent = mod_names(item_id, model_id)
    parts = [base]
    if prefix:
        parts.append(f"| {prefix}")
    if suffix:
        parts.append(f"| {suffix}")
    return " ".join(parts)


def weapon_display_name(item_id: int, model_id: int) -> str:
    """Base name wrapped in its mods, as TeamInventoryViewer shows weapons."""
    base = known_base_name(model_id, prefer_cache=True)
    if not base:
        return ""
    prefix, suffix, inherent = mod_names(item_id, model_id)
    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(base)
    if suffix:
        parts.append(suffix)
    if inherent:
        parts.append(f"({inherent})")
    return " ".join(parts)
