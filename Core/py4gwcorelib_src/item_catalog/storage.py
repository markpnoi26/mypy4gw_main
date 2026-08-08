"""Material storage pane layout — which slot each crafting material occupies.

The pane's own ordering is the authoritative common/rare split: everything at or
before Feather is common, everything after it is rare. That is more reliable
than :mod:`materials`' ``kind``, which records how a material is *obtained* by
salvaging and is blank for several materials that are nonetheless rare
(Damask, Ectoplasm, Deldrimor Steel).

Slots 7 and 28 are unused by any material — the map is deliberately not contiguous.
"""

from ...enums_src.Model_enums import ModelID

STORAGE_SLOT: dict[int, int] = {
    ModelID.Bone: 0,
    ModelID.Iron_Ingot: 1,
    ModelID.Tanned_Hide_Square: 2,
    ModelID.Scale: 3,
    ModelID.Chitin_Fragment: 4,
    ModelID.Bolt_Of_Cloth: 5,
    ModelID.Wood_Plank: 6,
    ModelID.Granite_Slab: 8,
    ModelID.Pile_Of_Glittering_Dust: 9,
    ModelID.Plant_Fiber: 10,
    ModelID.Feather: 11,
    ModelID.Fur_Square: 12,
    ModelID.Bolt_Of_Linen: 13,
    ModelID.Bolt_Of_Damask: 14,
    ModelID.Bolt_Of_Silk: 15,
    ModelID.Glob_Of_Ectoplasm: 16,
    ModelID.Steel_Ingot: 17,
    ModelID.Deldrimor_Steel_Ingot: 18,
    ModelID.Monstrous_Claw: 19,
    ModelID.Monstrous_Eye: 20,
    ModelID.Monstrous_Fang: 21,
    ModelID.Ruby: 22,
    ModelID.Sapphire: 23,
    ModelID.Diamond: 24,
    ModelID.Onyx_Gemstone: 25,
    ModelID.Lump_Of_Charcoal: 26,
    ModelID.Obsidian_Shard: 27,
    ModelID.Tempered_Glass_Vial: 29,
    ModelID.Leather_Square: 30,
    ModelID.Elonian_Leather_Square: 31,
    ModelID.Vial_Of_Ink: 32,
    ModelID.Roll_Of_Parchment: 33,
    ModelID.Roll_Of_Vellum: 34,
    ModelID.Spiritwood_Plank: 35,
    ModelID.Amber_Chunk: 36,
    ModelID.Jadeite_Shard: 37,
}

LAST_COMMON_SLOT = STORAGE_SLOT[ModelID.Feather]

COMMON_MATERIAL_IDS: tuple[int, ...] = tuple(
    model_id for model_id, slot in STORAGE_SLOT.items() if slot <= LAST_COMMON_SLOT
)

RARE_MATERIAL_IDS: tuple[int, ...] = tuple(
    model_id for model_id, slot in STORAGE_SLOT.items() if slot > LAST_COMMON_SLOT
)


def storage_slot(model_id: int) -> int | None:
    return STORAGE_SLOT.get(int(model_id))


def is_material(model_id: int) -> bool:
    return int(model_id) in STORAGE_SLOT


def is_rare_material(model_id: int) -> bool:
    slot = STORAGE_SLOT.get(int(model_id))
    return slot is not None and slot > LAST_COMMON_SLOT


def is_common_material(model_id: int) -> bool:
    slot = STORAGE_SLOT.get(int(model_id))
    return slot is not None and slot <= LAST_COMMON_SLOT
