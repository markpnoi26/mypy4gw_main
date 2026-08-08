"""A resolved, frozen view of one inventory item.

Economy nodes compare, sort and plan over items across several frames. Reading
each property off the live item every time re-enters the native layer per field
and can change under a plan mid-execution; a snapshot reads once and stays put.

Mods come from :mod:`Core.mods_core` — the data-table engine — so a snapshot
never needs the item's raw mod string.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Optional

from .. import mods_core
from ..enums_src.Item_enums import Bags
from ..enums_src.Item_enums import ItemType
from .item_catalog import storage
from .item_identifier import ItemIdentifier
from .item_identifier import ResolvedItemIdentifier
from .item_identifier import resolve


@dataclass(frozen=True)
class ItemSnapshot:
    item_id: int
    model_id: int = 0
    name: str = ''
    item_type: ItemType = ItemType.Unknown
    quantity: int = 0
    value: int = 0
    is_stackable: bool = False
    is_identified: bool = True
    is_customized: bool = False
    is_salvageable: bool = False
    is_material_salvageable: bool = False
    rarity: str = ''
    bag: Optional[Bags] = None
    slot: int = -1
    upgrades: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def is_material(self) -> bool:
        return storage.is_material(self.model_id)

    @property
    def is_rare_material(self) -> bool:
        return storage.is_rare_material(self.model_id)

    @property
    def storage_slot(self) -> Optional[int]:
        return storage.storage_slot(self.model_id)

    @property
    def has_upgrades(self) -> bool:
        return bool(self.upgrades)

    def upgrade_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.upgrades)

    def matches(self, identifier: ItemIdentifier) -> bool:
        """Whether this item answers to the given identifier.

        A (model id, type) pair must match both; a bare model id matches on id
        alone; a name matches case-insensitively. An unresolvable identifier
        never matches.
        """
        resolved: ResolvedItemIdentifier = resolve(identifier)
        if resolved.is_empty:
            return False
        if resolved.has_model_id and resolved.model_id != self.model_id:
            return False
        if resolved.has_item_type and resolved.item_type != self.item_type:
            return False
        if resolved.has_name and resolved.name.strip().lower() != self.name.strip().lower():
            return False
        return resolved.has_model_id or resolved.has_name

    def same_kind_as(self, other: 'ItemSnapshot') -> bool:
        return self.model_id == other.model_id and self.item_type == other.item_type


def read(item_id: int, bag: Optional[Bags] = None, slot: int = -1) -> Optional[ItemSnapshot]:
    """Snapshot one item. Returns None when the id is dead or the native read fails."""
    if not item_id:
        return None
    from ..Item import Item

    try:
        item_type_value, _ = Item.GetItemType(item_id)
        try:
            item_type = ItemType(item_type_value)
        except ValueError:
            item_type = ItemType.Unknown
        _, rarity_name = Item.Rarity.GetRarity(item_id)
        return ItemSnapshot(
            item_id=int(item_id),
            model_id=int(Item.GetModelID(item_id)),
            name=Item.GetName(item_id) or '',
            item_type=item_type,
            quantity=int(Item.Properties.GetQuantity(item_id)),
            value=int(Item.Properties.GetValue(item_id)),
            is_stackable=bool(Item.Properties.IsStackable(item_id)),
            is_identified=bool(Item.Usage.IsIdentified(item_id)),
            is_customized=bool(Item.Properties.IsCustomized(item_id)),
            is_salvageable=bool(Item.Usage.IsSalvageable(item_id)),
            is_material_salvageable=bool(Item.Usage.IsMaterialSalvageable(item_id)),
            rarity=rarity_name or '',
            bag=bag,
            slot=slot,
            upgrades=tuple(mods_core.upgrades_on(item_id)),
        )
    except Exception:
        return None


def read_bag(bag: Bags) -> list[ItemSnapshot]:
    """Snapshot one bag, in slot order. Dead slots are skipped, not padded."""
    from ..ItemArray import ItemArray

    snapshots: list[ItemSnapshot] = []
    for slot, item_id in enumerate(ItemArray.GetItemArray(ItemArray.CreateBagList(bag))):
        snapshot = read(item_id, bag=bag, slot=slot)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def read_bags(bags: list[Bags]) -> list[ItemSnapshot]:
    snapshots: list[ItemSnapshot] = []
    for bag in bags:
        snapshots.extend(read_bag(bag))
    return snapshots


def find(snapshots: list[ItemSnapshot], identifier: ItemIdentifier) -> list[ItemSnapshot]:
    return [snapshot for snapshot in snapshots if snapshot.matches(identifier)]


def total_quantity(snapshots: list[ItemSnapshot], identifier: ItemIdentifier) -> int:
    return sum(snapshot.quantity for snapshot in find(snapshots, identifier))
