"""Backward-compatible quest data exports.

Use `Core.enums` or `Core.enums_src.Quest_enums` for new imports.
"""

from .enums_src.Quest_enums import QUEST_DATA, QUEST_NAMES, get_quest_id, get_quest_ids, get_quest_name

__all__ = [
    "QUEST_DATA",
    "QUEST_NAMES",
    "get_quest_id",
    "get_quest_ids",
    "get_quest_name",
]
