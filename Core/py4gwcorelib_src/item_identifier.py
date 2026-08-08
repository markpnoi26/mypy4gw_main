"""Resolve the several ways callers name an item into one shape.

Bot scripts identify an item by model id, by encoded name, by display name, or
by a (model id, item type) pair when a model id alone is ambiguous. Nodes that
accept any of those resolve once here instead of re-testing types inline.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import ClassVar
from typing import Union

from ..enums_src.Item_enums import ItemType

ItemModelAndType = tuple[int, ItemType]
ItemIdentifier = Union[int, list, bytes, str, ItemModelAndType]

KIND_MODEL_ID = 'model_id'
KIND_MODEL_ID_AND_TYPE = 'model_id_and_type'
KIND_ENCODED_NAME = 'encoded_name'
KIND_NAME = 'name'
KIND_EMPTY = 'empty'


@dataclass(frozen=True)
class ResolvedItemIdentifier:
    EMPTY: ClassVar['ResolvedItemIdentifier']

    raw: object = None
    kind: str = KIND_EMPTY
    model_id: int = 0
    item_type: ItemType = ItemType.Unknown
    encoded_name: bytes = b''
    name: str = ''

    @property
    def has_model_id(self) -> bool:
        return self.model_id != 0

    @property
    def has_item_type(self) -> bool:
        return self.item_type != ItemType.Unknown

    @property
    def has_name(self) -> bool:
        return self.name != ''

    @property
    def has_encoded_name(self) -> bool:
        return self.encoded_name != b''

    @property
    def is_empty(self) -> bool:
        return self.kind == KIND_EMPTY


ResolvedItemIdentifier.EMPTY = ResolvedItemIdentifier()


def resolve(identifier: ItemIdentifier) -> ResolvedItemIdentifier:
    """Never raises — an unrecognised identifier resolves to EMPTY so callers can branch on is_empty."""
    if isinstance(identifier, bool):
        return ResolvedItemIdentifier.EMPTY

    if isinstance(identifier, tuple):
        if len(identifier) != 2:
            return ResolvedItemIdentifier.EMPTY
        model_id, item_type = identifier
        if isinstance(model_id, bool) or not isinstance(model_id, int):
            return ResolvedItemIdentifier.EMPTY
        try:
            item_type = ItemType(item_type)
        except ValueError:
            return ResolvedItemIdentifier.EMPTY
        return ResolvedItemIdentifier(
            raw=identifier, kind=KIND_MODEL_ID_AND_TYPE, model_id=int(model_id), item_type=item_type
        )

    if isinstance(identifier, int):
        return ResolvedItemIdentifier(raw=identifier, kind=KIND_MODEL_ID, model_id=int(identifier))

    if isinstance(identifier, (bytes, bytearray)):
        return ResolvedItemIdentifier(raw=identifier, kind=KIND_ENCODED_NAME, encoded_name=bytes(identifier))

    if isinstance(identifier, list):
        try:
            return ResolvedItemIdentifier(raw=identifier, kind=KIND_ENCODED_NAME, encoded_name=bytes(identifier))
        except (TypeError, ValueError):
            return ResolvedItemIdentifier.EMPTY

    if isinstance(identifier, str):
        return ResolvedItemIdentifier(raw=identifier, kind=KIND_NAME, name=identifier)

    return ResolvedItemIdentifier.EMPTY


def model_id_of(identifier: ItemIdentifier) -> int:
    return resolve(identifier).model_id


def item_type_of(identifier: ItemIdentifier) -> ItemType:
    return resolve(identifier).item_type


def name_of(identifier: ItemIdentifier) -> str:
    return resolve(identifier).name


def encoded_name_of(identifier: ItemIdentifier) -> bytes:
    return resolve(identifier).encoded_name
