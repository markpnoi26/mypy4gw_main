# Item identifier resolution — the several ways a caller can name an item.

from Core.enums_src.Item_enums import ItemType
from Core.py4gwcorelib_src import item_identifier


def test_int_resolves_as_model_id():
    resolved = item_identifier.resolve(925)
    assert resolved.kind == item_identifier.KIND_MODEL_ID
    assert resolved.model_id == 925
    assert resolved.has_model_id and not resolved.has_item_type


def test_model_id_and_type_pair():
    resolved = item_identifier.resolve((925, ItemType.Salvage))
    assert resolved.kind == item_identifier.KIND_MODEL_ID_AND_TYPE
    assert resolved.model_id == 925
    assert resolved.item_type == ItemType.Salvage


def test_string_resolves_as_name():
    resolved = item_identifier.resolve('Bolt of Cloth')
    assert resolved.kind == item_identifier.KIND_NAME
    assert resolved.name == 'Bolt of Cloth'
    assert not resolved.has_model_id


def test_bytes_and_list_resolve_as_encoded_name():
    assert item_identifier.resolve(b'\x01\x02').encoded_name == b'\x01\x02'
    assert item_identifier.resolve([1, 2]).encoded_name == b'\x01\x02'


def test_bool_is_not_a_model_id():
    # bool is an int subclass; True must not silently become model id 1
    assert item_identifier.resolve(True).is_empty
    assert item_identifier.resolve(False).is_empty


def test_unrecognised_shapes_resolve_empty_instead_of_raising():
    assert item_identifier.resolve(None).is_empty
    assert item_identifier.resolve(3.5).is_empty
    assert item_identifier.resolve((1, 2, 3)).is_empty
    assert item_identifier.resolve(('nope', ItemType.Salvage)).is_empty
    assert item_identifier.resolve((925, 'not-a-type')).is_empty


def test_accessor_helpers_agree_with_resolve():
    assert item_identifier.model_id_of((925, ItemType.Salvage)) == 925
    assert item_identifier.item_type_of((925, ItemType.Salvage)) == ItemType.Salvage
    assert item_identifier.name_of('Feather') == 'Feather'
    assert item_identifier.encoded_name_of(b'\x09') == b'\x09'
    assert item_identifier.model_id_of(None) == 0
