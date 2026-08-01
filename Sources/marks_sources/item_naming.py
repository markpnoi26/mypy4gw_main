"""Item naming shared by InventoryLite and TeamInventoryViewer: one mod database, one cache, one ladder."""

import os
import re

import PyItem
import PySystem

from Core.enums_src.GameData_enums import DyeColor
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
#: carries that name -- the entries are {encoded singular name: base_name} and belong to neither widget.
NAME_CACHE_DOC = "TeamInventoryViewer/item_names.json"

#: Leading rarity-colour control wchar, stripped from the key. Documented in
#: docs/RE/name_tag_color_reverse_engineering.md:157.
COLOUR_CONTROL_CODEPOINTS = frozenset({0xA3F})


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


def name_key(item_id: int) -> str:
    """This item's identity for the name cache: its ENCODED singular name, as hex.

    Replaces the model id. The game renumbers model ids across builds, and a renumbered id makes a
    model-keyed cache hand back the previous item's name -- a confident mislabel rather than a miss.
    An encoded name that no longer matches anything simply stops being looked up.

    Singular because that form omits the stack count, so five feathers and six key the same entry.
    Many keys per name is expected: every prefix/suffix combination of a skin encodes differently,
    so "Fiery Voltaic Spear" and "Furious Voltaic Spear" are two keys both holding "Voltaic Spear".
    """
    if not item_id:
        return ""
    try:
        raw = PyItem.PyItem(item_id).GetSingleItemName() or ()
    except Exception:
        return ""
    try:
        enc = bytes(raw)
    except Exception:
        # Not a byte vector after all. The width does not matter to a key, only that it is stable.
        try:
            enc = b"".join(int(v).to_bytes(2, "little") for v in raw)
        except Exception:
            return ""
    # Leading rarity-colour control wchar, or the same item at four rarities becomes four entries.
    if len(enc) >= 2 and int.from_bytes(enc[:2], "little") in COLOUR_CONTROL_CODEPOINTS:
        enc = enc[2:]
    return enc.hex()


class ItemNameCache:
    """Shared {encoded singular name: base_name} map over one global-scope document.

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
        return self.names

    def get(self, key: str, default: str = "") -> str:
        if not self.loaded:
            self.load()
        return self.names.get(key, default)

    def remember(self, key: str, name: str):
        """Write a RESOLVED name down so no later scan has to ask for it again.

        A raw encoding is not a name: storing one marks the item resolved and it is never retried,
        which is how `enc:...` once ended up displayed as an item's name.
        """
        if not key or not name or name.startswith("enc:"):
            return
        if self.names.get(key) == name:
            return
        self.names[key] = name
        try:
            self.doc().set(key, name)
        except Exception as exc:
            ConsoleLog(MODULE_NAME, f"Could not store the name for {key[:16]}: {exc}", Console.MessageType.Warning)


NAME_CACHE = ItemNameCache()


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


def known_base_name(item_id: int) -> str:
    """The name without asking the server: the shared cache, keyed by this item's encoded name.

    "" when we have not learned it yet. The ModelID enum used to sit in front of this; it went
    because it is a hand-maintained snapshot of ids the game renumbers, so it answers confidently
    and sometimes wrongly. The cache is now the only source, and it cannot mistake one item for
    another because the key IS the item's own name encoding.
    """
    return NAME_CACHE.get(name_key(item_id))


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


def store_base_name(full: str, key: str, prefix: str = "", suffix: str = "") -> str:
    """The one place a server-returned name becomes a cache entry.

    Both callers go through it so they cannot disagree about how a name is stripped: they write to the
    same document under the same key, so two spellings would overwrite each other on every scan.
    """
    name = base_name_from_full(full or "", prefix, suffix)
    if name and key:
        NAME_CACHE.remember(key, name)
    return name


def fetch_base_name(item_id: int, model_id: int, prefix: str = "", suffix: str = ""):
    """Ask the game for this item's name, waiting for it, and write it to the shared cache.

    Blocks for up to ~2s per item, so it belongs behind an explicit "scan now" press and nowhere near
    a loop that runs on a timer -- use learn_base_name there. Runs once per ENCODED NAME rather than
    per item, which is finer than per model: two prefixes on one skin are two lookups, both landing on
    the same base name.
    """
    try:
        full = yield from Routines.Yield.Items.GetItemNameByItemID(item_id)
    except Exception as exc:
        ConsoleLog(MODULE_NAME, f"Name lookup failed for model {model_id}: {exc}", Console.MessageType.Warning)
        return ""
    key = name_key(item_id)
    name = store_base_name(full, key, prefix, suffix)
    if name and not key:
        # Named but unkeyable, so it cannot be written down and every later scan asks again.
        # Silent until now, which looked exactly like "this item never gets cached".
        ConsoleLog(
            MODULE_NAME,
            f"'{name}' (model {model_id}) has no encoded name to key on; not cached.",
            Console.MessageType.Warning,
        )
    return name


# ---------------------------------------------------------------- composed display names


def armor_display_name(item_id: int, model_id: int, key: str | None = None) -> str:
    """Base name plus its rune and insignia, as TeamInventoryViewer shows armor."""
    base = NAME_CACHE.get(key) if key is not None else known_base_name(item_id)
    if not base:
        return ""
    prefix, suffix, _inherent = mod_names(item_id, model_id)
    parts = [base]
    if prefix:
        parts.append(f"| {prefix}")
    if suffix:
        parts.append(f"| {suffix}")
    return " ".join(parts)


def weapon_display_name(item_id: int, model_id: int, key: str | None = None) -> str:
    """Base name wrapped in its mods, as TeamInventoryViewer shows weapons."""
    base = NAME_CACHE.get(key) if key is not None else known_base_name(item_id)
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


def dye_label(item_id: int) -> str:
    """A dye's colour as " [Blue]", empty for everything else.

    Gated on the native item type, not on a model id. GetDyeColor returns the first non-zero modifier
    arg on ANY item, so without a gate every modded weapon picks up a colour.
    """
    try:
        type_value, _name = GLOBAL_CACHE.Item.GetItemType(item_id)
        if int(type_value) != int(ItemType.Dye):
            return ""
        return f" [{DyeColor(GLOBAL_CACHE.Item.GetDyeColor(item_id)).name}]"
    except Exception:
        return ""


def display_name(item_id: int, model_id: int, key: str | None = None) -> str:
    """The item as a viewer shows it: the cached base name with its mods composed back on.

    "" until the base name has been learned, and deliberately nothing behind it. A model-id rung used
    to sit here, and when the enum is wrong it does not miss -- it names a DIFFERENT item confidently,
    which a viewer then writes down and serves back forever.

    Pass `key` when the caller already has it. name_key builds a PyItem per call, so a scan that walks
    every bag every few seconds must not pay for it three times per item.
    """
    try:
        if GLOBAL_CACHE.Item.Type.IsWeapon(item_id) and not GLOBAL_CACHE.Item.Rarity.IsGreen(item_id):
            return weapon_display_name(item_id, model_id, key)
        if GLOBAL_CACHE.Item.Type.IsArmor(item_id):
            return armor_display_name(item_id, model_id, key)
    except Exception:
        pass
    base = NAME_CACHE.get(key) if key is not None else known_base_name(item_id)
    return base + dye_label(item_id) if base else ""


def learn_base_name(item_id: int, model_id: int, key: str = "") -> str:
    """Non-blocking twin of fetch_base_name: reads a name the client ALREADY holds, or queues it.

    Returns "" when the answer is not in yet -- the caller is expected to leave the item alone and
    look again on a later pass, not to wait. GetName queues its own request when it comes up empty,
    so an item asked about once keeps making progress without anything polling it.
    """
    if not key:
        key = name_key(item_id)
    if not key:
        return ""
    try:
        if not GLOBAL_CACHE.Item.IsNameReady(item_id):
            GLOBAL_CACHE.Item.RequestName(item_id)
            return ""
        full = GLOBAL_CACHE.Item.GetName(item_id)
    except Exception:
        return ""
    if not full:
        return ""
    prefix, suffix, _inherent = mod_names(item_id, model_id)
    return store_base_name(full, key, prefix, suffix)
