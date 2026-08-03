"""Item grouping, labelling, and the cluster sort shared by the inventory widgets.

Shared code: a regression here shows up in InventoryLite and TeamInventoryViewer
at once, and as a mis-sorted bag rather than as an error.
"""

from Core.enums_src.Item_enums import ItemType
from Sources.marks_sources.item_kinds import ITEM_GROUPS
from Sources.marks_sources.item_kinds import ITEM_TYPES
from Sources.marks_sources.item_kinds import SORT_TYPE_ORDER
from Sources.marks_sources.item_kinds import TYPE_NAMES
from Sources.marks_sources.item_kinds import cluster_key
from Sources.marks_sources.item_kinds import group_of
from Sources.marks_sources.item_kinds import type_label

AXE = int(ItemType.Axe)
SALVAGE = int(ItemType.Salvage)
KIT = int(ItemType.Kit)
GOLD = int(ItemType.Gold_Coin)
BOOTS = int(ItemType.Boots)


def test_a_listed_type_reports_its_group():
    assert group_of(AXE) == "Weapons"


def test_an_ungrouped_type_matches_no_group_rather_than_a_catch_all():
    """Membership is explicit everywhere so a rule claims only what it names."""
    assert group_of(int(ItemType.Unknown)) == ""


def test_armor_does_not_silently_claim_salvage_items():
    """ITEM_TYPE_META_TYPES' ARMOR_TYPES includes Salvage. An 'Armor' rule that
    also claimed salvage drops would quietly destroy them."""
    assert SALVAGE not in ITEM_GROUPS["Armor"]
    assert group_of(SALVAGE) == "Salvage"


def test_no_item_type_belongs_to_two_groups():
    """group_of returns the first match, so an overlap makes the answer depend on
    dict order rather than on the taxonomy."""
    seen = {}
    duplicates = []
    for name, members in ITEM_GROUPS.items():
        for member in members:
            if member in seen:
                duplicates.append("%d in both %s and %s" % (member, seen[member], name))
            seen[member] = name
    assert duplicates == []


def test_the_precise_label_wins_over_the_group_name():
    """A report column and the Kind checkboxes have to agree letter for letter —
    a rule you cannot write from what the report shows is one you get wrong."""
    assert type_label(AXE) == "Axe"


def test_a_type_with_no_precise_label_falls_back_to_its_group():
    assert type_label(KIT) == "Kits"


def test_an_unknown_type_uses_the_caller_fallback():
    assert type_label(-999, fallback="Whatever") == "Whatever"


def test_an_unknown_type_with_no_fallback_is_still_printable():
    assert type_label(-999) == "-999"


def test_type_names_invert_item_types_without_collisions():
    assert len(TYPE_NAMES) == len(ITEM_TYPES)


def test_every_precisely_labelled_type_has_a_sort_rank():
    """A type in ITEM_TYPES but not in SORT_TYPE_ORDER sorts after everything,
    which puts equipment below the bulk materials."""
    assert [name for name, value in ITEM_TYPES.items() if value not in SORT_TYPE_ORDER] == []


def test_the_sort_order_lists_each_type_once():
    """A duplicate silently shadows the later rank."""
    assert len(SORT_TYPE_ORDER) == len(set(SORT_TYPE_ORDER))


def test_clustering_puts_tools_before_weapons_before_bulk():
    assert cluster_key(KIT, "Salvage Kit") < cluster_key(AXE, "Axe")
    assert cluster_key(AXE, "Axe") < cluster_key(GOLD, "Gold")


def test_an_unranked_type_sorts_after_everything_ranked():
    assert cluster_key(GOLD, "Gold") < cluster_key(int(ItemType.Unknown), "Mystery")


def test_rarity_bands_run_best_first_inside_a_type():
    assert cluster_key(AXE, "Axe", rarity=5) < cluster_key(AXE, "Axe", rarity=2)


def test_a_leading_stack_count_does_not_decide_the_letter():
    """'10 Iron Ingots' files under I, not 1."""
    ranked = sorted(["10 Iron Ingots", "Bone", "Zealous Axe"], key=lambda n: cluster_key(AXE, n))
    assert ranked == ["Bone", "10 Iron Ingots", "Zealous Axe"]


def test_clustering_is_case_insensitive():
    assert cluster_key(AXE, "iron ingot") == cluster_key(AXE, "Iron Ingot")


def test_clustering_survives_a_missing_name_and_type():
    assert cluster_key(0, "") == cluster_key(0, None)


def test_type_before_rarity_before_name():
    """Ordering, not just outcome: a gold axe still sorts before any boots, and a
    blue axe before a gold pair of boots."""
    assert cluster_key(AXE, "zzz", rarity=0) < cluster_key(BOOTS, "aaa", rarity=9)
