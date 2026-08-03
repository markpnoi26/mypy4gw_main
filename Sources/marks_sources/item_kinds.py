"""Item type taxonomy shared by InventoryLite and TeamInventoryViewer.

Lifted out of InventoryLite when TeamInventoryViewer needed the same grouping to cluster its view.
Neither widget owns it: it describes Guild Wars item types, not what either widget does with them.
"""

from Core.enums_src.Item_enums import ItemType

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


def group_of(item_type: int) -> str:
    """The ITEM_GROUPS name holding this type, or "" when no group does."""
    for name, members in ITEM_GROUPS.items():
        if item_type in members:
            return name
    return ""


def type_label(item_type: int, fallback: str = "") -> str:
    """What the rule editor calls this item.

    The precise checkbox label wins, so a report column and the Kind boxes agree letter for letter:
    a rule you cannot write from what the report shows is a rule you will get wrong.
    """
    return TYPE_NAMES.get(item_type) or group_of(item_type) or fallback or str(item_type)


def cluster_key(item_type: int, name: str, rarity: int = -1) -> tuple:
    """Sort key that puts like with like: kits before weapons before armour before bulk.

    Ranked by SORT_TYPE_ORDER so the view matches the order Organize lays the bags out in, with
    anything unlisted after everything else. Rarity bands inside a type -- greens, then golds,
    purples, blues -- the way InventoryLite's organize does it, because that is how you look for
    things. Leading stack counts are ignored so "10 Iron Ingots" files under I, not 1.
    """
    import re

    rank = TYPE_RANK.get(int(item_type or 0), len(SORT_TYPE_ORDER))
    return (rank, group_of(item_type), -int(rarity), re.sub(r"^\d+\s*", "", name or "").lower())
