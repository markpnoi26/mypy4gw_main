# ItemSnapshot value semantics and the read() guard. No live client involved.

from Core.enums_src.Item_enums import Bags
from Core.enums_src.Item_enums import ItemType
from Core.py4gwcorelib_src import item_snapshot


def snapshot(**overrides):
    base = dict(item_id=1, model_id=925, name='Bolt of Cloth', item_type=ItemType.Salvage, quantity=3)
    base.update(overrides)
    return item_snapshot.ItemSnapshot(**base)


def test_material_properties_come_from_the_storage_map():
    cloth = snapshot()
    assert cloth.is_material is True
    assert cloth.is_rare_material is False
    assert cloth.storage_slot == 5

    ecto = snapshot(model_id=930, name='Glob of Ectoplasm')
    assert ecto.is_rare_material is True

    sword = snapshot(model_id=1, name='Sword', item_type=ItemType.Sword)
    assert sword.is_material is False
    assert sword.storage_slot is None


def test_matches_on_model_id_and_on_name():
    cloth = snapshot()
    assert cloth.matches(925)
    assert cloth.matches('bolt of cloth')
    assert not cloth.matches(926)
    assert not cloth.matches('Feather')


def test_matches_requires_both_halves_of_a_typed_pair():
    cloth = snapshot()
    assert cloth.matches((925, ItemType.Salvage))
    assert not cloth.matches((925, ItemType.Axe))


def test_unresolvable_identifier_never_matches():
    assert not snapshot().matches(None)
    assert not snapshot().matches(True)


def test_same_kind_compares_model_and_type():
    assert snapshot().same_kind_as(snapshot(item_id=2, quantity=1))
    assert not snapshot().same_kind_as(snapshot(item_id=2, model_id=933))
    assert not snapshot().same_kind_as(snapshot(item_id=2, item_type=ItemType.Axe))


def test_upgrade_helpers():
    plain = snapshot()
    assert plain.has_upgrades is False
    assert plain.upgrade_names() == ()

    upgraded = snapshot(upgrades=(('Fortitude', 30), ('Sundering', 20)))
    assert upgraded.has_upgrades is True
    assert upgraded.upgrade_names() == ('Fortitude', 'Sundering')


def test_snapshots_are_frozen():
    import dataclasses
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot().quantity = 99


def test_read_returns_none_for_a_dead_id():
    assert item_snapshot.read(0) is None


def test_read_returns_none_when_the_native_read_fails(monkeypatch):
    from Core.Item import Item

    def boom(item_id):
        raise RuntimeError('item vanished mid-read')

    monkeypatch.setattr(Item, 'GetItemType', staticmethod(boom))
    assert item_snapshot.read(1234) is None


def test_find_and_total_quantity_aggregate_stacks():
    stacks = [snapshot(item_id=1, quantity=3), snapshot(item_id=2, quantity=7), snapshot(item_id=3, model_id=933)]
    assert [s.item_id for s in item_snapshot.find(stacks, 925)] == [1, 2]
    assert item_snapshot.total_quantity(stacks, 925) == 10
    assert item_snapshot.total_quantity(stacks, 999) == 0


def test_bag_and_slot_ride_along():
    placed = snapshot(bag=Bags.Backpack, slot=4)
    assert placed.bag == Bags.Backpack
    assert placed.slot == 4
