"""Model-id danger tables and body-block detection for the Shadow Form runner builds."""

import time
from typing import Iterable
from typing import Sequence

from Core import Agent
from Core import ChatChannel
from Core import Player
from Core import Range
from Core import Routines
from Core import Utils

DangerEntry = tuple[Iterable[int], str]
DangerTable = Sequence[DangerEntry]


class BuildDangerHelper:
    """Answers "is something about to cripple/KD me" from caller-supplied model-id
    tables, and tracks whether the player has stopped moving while touching an enemy.

    Every check carries its own throttle because the callers poll them once per
    rotation pass, which is far faster than the agent scans are worth running.
    """

    def __init__(
        self,
        cripple_kd_table: DangerTable = (),
        spellcast_table: DangerTable = (),
        extreme_kd_categories: list[str] | None = None,
        scan_throttle: float = 0.1,
        danger_check_cooldown: float = 0.1,
        spell_caster_check_cooldown: float = 1.0,
        log_enabled: bool = False,
    ):
        self.name = "BuildDangerHelper"
        self.log_enabled = log_enabled

        self.cripple_kd_table = cripple_kd_table
        self.spellcast_table = spellcast_table
        self.extreme_kd_categories = list(extreme_kd_categories or [])

        self.scan_throttle = scan_throttle
        self.danger_check_cooldown = danger_check_cooldown
        self.spell_caster_check_cooldown = spell_caster_check_cooldown

        self.last_cripple_kd_check = 0.0
        self.last_cripple_kd_scan = 0.0
        self.last_spellcaster_check = 0.0
        self.last_spellcaster_scan = 0.0

        self.prev_pos: tuple[float, float] | None = None
        self.last_move_time = time.time()

        self.rebuild_caches()

    def rebuild_caches(self) -> None:
        self.cripple_kd_models: set[int] = set()
        self.extreme_kd_range_models: set[int] = set()
        terms = [term.lower() for term in self.extreme_kd_categories]

        for model_ids, category in self.cripple_kd_table:
            self.cripple_kd_models.update(model_ids)
            if terms and any(term in category.lower() for term in terms):
                self.extreme_kd_range_models.update(model_ids)

        self.spellcaster_models: set[int] = set()
        for model_ids, _ in self.spellcast_table:
            self.spellcaster_models.update(model_ids)

    def enemy_category_from_model_id(self, model_id: int) -> str:
        for model_ids, category in self.cripple_kd_table:
            if model_id in model_ids:
                return category
        for model_ids, category in self.spellcast_table:
            if model_id in model_ids:
                return category
        return "Unknown"

    def warn_danger(self, model_id: int) -> None:
        category = self.enemy_category_from_model_id(model_id)
        Player.SendFakeChat(ChatChannel.CHANNEL_WARNING, f"Cripple/KD danger - {category} spotted!")

    def check_cripple_kd(self, x: float, y: float) -> bool:
        if not self.cripple_kd_models and not self.extreme_kd_range_models:
            return False

        now = time.time()
        if now - self.last_cripple_kd_check < self.danger_check_cooldown:
            return False
        if now - self.last_cripple_kd_scan < self.scan_throttle:
            return False
        self.last_cripple_kd_scan = now

        for enemy_id in Routines.Agents.GetFilteredEnemyArray(x, y, max_distance=500.0):
            model_id = Agent.GetModelID(enemy_id)
            if model_id in self.cripple_kd_models:
                self.warn_danger(model_id)
                self.last_cripple_kd_check = now
                return True

        if not self.extreme_kd_range_models:
            return False

        for enemy_id in Routines.Agents.GetFilteredEnemyArray(x, y, max_distance=2000.0):
            model_id = Agent.GetModelID(enemy_id)
            if model_id in self.extreme_kd_range_models:
                self.warn_danger(model_id)
                self.last_cripple_kd_check = now
                return True

        return False

    def check_spellcaster(self, custom_distance: float = 2000.0, include_non_specified: bool = True) -> bool:
        if not include_non_specified and not self.spellcaster_models:
            return False

        now = time.time()
        if now - self.last_spellcaster_check < self.spell_caster_check_cooldown:
            return False
        if now - self.last_spellcaster_scan < self.scan_throttle:
            return False
        self.last_spellcaster_scan = now

        x, y = Player.GetXY()
        if not (x and y):
            return False

        nearby_enemies = Routines.Agents.GetFilteredEnemyArray(x, y, max_distance=custom_distance)
        special_caster_found = any(Agent.GetModelID(enemy_id) in self.spellcaster_models for enemy_id in nearby_enemies)

        nearby_spellcaster = (
            Routines.Agents.GetNearestEnemyCaster(custom_distance, aggressive_only=False)
            if include_non_specified
            else 0
        )

        if special_caster_found or nearby_spellcaster:
            Player.SendFakeChat(ChatChannel.CHANNEL_WARNING, "Spellcaster - spotted!")
            self.last_spellcaster_check = now
            return True

        return False

    def body_block_detection(self, seconds: float = 2.0) -> bool:
        if not Routines.Agents.GetNearestEnemy(Range.Touch.value):
            return False

        pos = Player.GetXY()
        if not pos:
            return False

        if not self.prev_pos:
            self.prev_pos = pos
            self.last_move_time = time.time()
            return False

        if Utils.Distance(pos, self.prev_pos) > Range.Touch.value:
            self.prev_pos = pos
            self.last_move_time = time.time()
            return False

        return time.time() - self.last_move_time >= seconds

    def update_tables(self, cripple_kd_table: DangerTable = (), spellcast_table: DangerTable = ()) -> None:
        if cripple_kd_table:
            self.cripple_kd_table = cripple_kd_table
        if spellcast_table:
            self.spellcast_table = spellcast_table
        self.rebuild_caches()
