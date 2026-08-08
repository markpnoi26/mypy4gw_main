"""Shipped item reference data, shared by every loot surface.

**Package data**: versioned with the code, never written at runtime, owned by no feature.
``JsonFactory`` and ``Settings`` are for user-generated data; the JSON store is gitignored, so a
catalog kept there is missing from a fresh clone.

**Vocabulary**: a **category** contains **groups**.

* :mod:`catalog` -- the item catalog (387 resolved entries) and the 16 whose id is unknown.
* :mod:`materials` -- the 36 crafting materials, and what items salvage into.
* :mod:`storage` -- where each material sits in the material storage pane.
* :mod:`dyes` -- dye colours, identified by item type, never by model id.
* :mod:`nicholas` -- resolving the scheduled trophy for a week.
"""

from .catalog import CATALOG
from .catalog import UNRESOLVED
from .catalog import CatalogEntry
from .catalog import UnresolvedEntry
from .catalog import alphabetical_bands
from .catalog import by_model_id
from .catalog import categories
from .catalog import groups
from .catalog import in_category
from .catalog import in_group
from .catalog import model_ids
from .dyes import DYE_ITEM_TYPE
from .dyes import DYE_MODEL_ID
from .dyes import DYES
from .dyes import Dye
from .dyes import channels_of
from .dyes import color_of
from .dyes import dye
from .dyes import dye_color_ids
from .dyes import is_dye
from .materials import COMMON
from .materials import MATERIALS
from .materials import RARE
from .materials import SALVAGE_MAP
from .materials import Material
from .materials import known_sources
from .materials import material
from .materials import material_model_ids
from .materials import salvages_into
from .materials import salvages_into_material
from .nicholas import NicholasCache
from .nicholas import NicholasSelection
from .nicholas import entry_for_day
from .nicholas import entry_for_week
from .nicholas import monday_of
from .storage import COMMON_MATERIAL_IDS
from .storage import RARE_MATERIAL_IDS
from .storage import STORAGE_SLOT
from .storage import is_common_material
from .storage import is_material
from .storage import is_rare_material
from .storage import storage_slot

__all__ = [
    "CATALOG",
    "COMMON",
    "COMMON_MATERIAL_IDS",
    "DYES",
    "DYE_ITEM_TYPE",
    "DYE_MODEL_ID",
    "MATERIALS",
    "RARE",
    "RARE_MATERIAL_IDS",
    "SALVAGE_MAP",
    "STORAGE_SLOT",
    "UNRESOLVED",
    "CatalogEntry",
    "Dye",
    "Material",
    "NicholasCache",
    "NicholasSelection",
    "UnresolvedEntry",
    "alphabetical_bands",
    "by_model_id",
    "categories",
    "channels_of",
    "color_of",
    "dye",
    "dye_color_ids",
    "entry_for_day",
    "entry_for_week",
    "groups",
    "in_category",
    "in_group",
    "is_common_material",
    "is_dye",
    "is_material",
    "is_rare_material",
    "known_sources",
    "material",
    "material_model_ids",
    "model_ids",
    "monday_of",
    "salvages_into",
    "salvages_into_material",
    "storage_slot",
]
