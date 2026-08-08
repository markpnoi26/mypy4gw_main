# Material storage pane integrity. The slot map is shipped data, so the
# invariants that matter are agreement with the material catalog and the
# common/rare split the pane's own ordering defines.

from Core.py4gwcorelib_src.item_catalog import materials
from Core.py4gwcorelib_src.item_catalog import storage


def test_every_material_has_exactly_one_slot():
    assert len(storage.STORAGE_SLOT) == len(materials.MATERIALS)
    assert len(set(storage.STORAGE_SLOT.values())) == len(storage.STORAGE_SLOT)


def test_slot_map_and_material_catalog_name_the_same_items():
    assert set(storage.STORAGE_SLOT) == {m.model_id for m in materials.MATERIALS}


def test_split_partitions_the_whole_set():
    common = set(storage.COMMON_MATERIAL_IDS)
    rare = set(storage.RARE_MATERIAL_IDS)
    assert common | rare == set(storage.STORAGE_SLOT)
    assert not common & rare


def test_feather_is_the_last_common_slot():
    from Core.enums_src.Model_enums import ModelID

    assert storage.is_common_material(ModelID.Feather)
    assert storage.LAST_COMMON_SLOT == storage.STORAGE_SLOT[ModelID.Feather]


def test_pane_order_is_authoritative_over_salvage_kind():
    # materials.py records how a material is *obtained*; several rares have no
    # recorded kind, so the pane ordering is what the split must come from.
    from Core.enums_src.Model_enums import ModelID

    for model_id in (ModelID.Glob_Of_Ectoplasm, ModelID.Bolt_Of_Damask, ModelID.Deldrimor_Steel_Ingot):
        assert materials.material(model_id).kind == ''
        assert storage.is_rare_material(model_id)


def test_lookups_reject_unknown_model_ids():
    assert storage.storage_slot(1) is None
    assert storage.is_material(1) is False
    assert storage.is_rare_material(1) is False
    assert storage.is_common_material(1) is False
