"""Reaper detection and shared-mode debouncing for the Dhuum utility build."""

import time

from Core import Agent
from Core import AgentArray
from Core import GLOBAL_CACHE
from Core import Party
from Core import Player
from Core import Skill
from Core import ThrottledTimer


class _DhuumModeTracker:
    """Tracks Reaper casts and exposes a lightweight shared mode for Dhuum skills.

    Aligned with the Underworld reaper mode behavior.
    """

    MODE_DREST = "drest"
    MODE_FURY = "fury"

    MODE_SWITCH_DEBOUNCE_MS = 6000.0

    REAPER_NAME_MATCHERS = (
        "reaper of the bone pits",
        "reaper of the chaos planes",
        "reaper of the forgotten vale",
        "reaper of the ice wastes",
        "reaper of the labyrinth",
        "reaper of the spawning pools",
        "reaper of the twin serpent mountains",
    )

    # Skill name candidates matching the CB reaper_mode_tracker
    _DHUUMS_REST_CANDIDATES = ("Dhuums_Rest_Reaper_skill",)
    _GHOSTLY_FURY_CANDIDATES = ("Ghostly_Fury_Reaper_skill",)

    _shared_mode: str | None = None
    _shared_mode_locked_until_ms: float = 0.0

    _reaper_refresh_timer: ThrottledTimer | None = None
    _event_refresh_timer: ThrottledTimer | None = None

    _cached_reaper_ids: set[int] = set()
    _learned_reaper_ids: set[int] = set()

    _dhuums_rest_skill_ids: set[int] = set()
    _ghostly_fury_skill_ids: set[int] = set()

    _cached_reaper_candidate_ids: set[int] = set()
    _reaper_candidate_timer: ThrottledTimer | None = None
    _cached_party_member_ids: set[int] = set()
    _party_member_timer: ThrottledTimer | None = None

    _skill_name_cache: dict[int, str] = {}

    @classmethod
    def _ensure_timers(cls) -> None:
        if cls._reaper_refresh_timer is None:
            cls._reaper_refresh_timer = ThrottledTimer(1200)
            cls._reaper_refresh_timer.Reset()

        if cls._event_refresh_timer is None:
            cls._event_refresh_timer = ThrottledTimer(250)
            cls._event_refresh_timer.Reset()

        if cls._reaper_candidate_timer is None:
            cls._reaper_candidate_timer = ThrottledTimer(1200)
            cls._reaper_candidate_timer.Reset()

        if cls._party_member_timer is None:
            cls._party_member_timer = ThrottledTimer(2000)
            cls._party_member_timer.Reset()

        # Resolve reaper skill IDs (matching CB fallback IDs)
        if not cls._dhuums_rest_skill_ids:
            for name in cls._DHUUMS_REST_CANDIDATES:
                try:
                    skill_id = int(Skill.GetID(name))
                except Exception:
                    skill_id = 0
                if skill_id > 0:
                    cls._dhuums_rest_skill_ids.add(skill_id)
            cls._dhuums_rest_skill_ids.add(3079)

        if not cls._ghostly_fury_skill_ids:
            for name in cls._GHOSTLY_FURY_CANDIDATES:
                try:
                    skill_id = int(Skill.GetID(name))
                except Exception:
                    skill_id = 0
                if skill_id > 0:
                    cls._ghostly_fury_skill_ids.add(skill_id)
            cls._ghostly_fury_skill_ids.add(3136)

    @classmethod
    def _refresh_reaper_ids(cls) -> None:
        cls._ensure_timers()
        if cls._reaper_refresh_timer is None:
            return
        if not cls._reaper_refresh_timer.IsExpired() and cls._cached_reaper_ids:
            return

        reaper_ids: set[int] = set()
        for agent_id in cls._get_reaper_candidate_agent_ids():
            name = str(Agent.GetNameByID(agent_id) or "").strip().lower()
            if any(matcher in name for matcher in cls.REAPER_NAME_MATCHERS):
                reaper_ids.add(int(agent_id))

        cls._cached_reaper_ids = cls._cached_reaper_ids.union(reaper_ids)
        cls._reaper_refresh_timer.Reset()

    @classmethod
    def _get_reaper_candidate_agent_ids(cls) -> set[int]:
        if (
            cls._reaper_candidate_timer is not None
            and not cls._reaper_candidate_timer.IsExpired()
            and cls._cached_reaper_candidate_ids
        ):
            return cls._cached_reaper_candidate_ids
        candidates = set(AgentArray.GetAllyArray())
        candidates.update(AgentArray.GetNeutralArray())
        candidates.update(AgentArray.GetNPCMinipetArray())
        candidates.update(AgentArray.GetSpiritPetArray())
        cls._cached_reaper_candidate_ids = {int(x) for x in candidates}
        if cls._reaper_candidate_timer is not None:
            cls._reaper_candidate_timer.Reset()
        return cls._cached_reaper_candidate_ids

    @classmethod
    def _get_party_member_agent_ids(cls) -> set[int]:
        if (
            cls._party_member_timer is not None
            and not cls._party_member_timer.IsExpired()
            and cls._cached_party_member_ids
        ):
            return cls._cached_party_member_ids
        party_ids: set[int] = set()
        for player in Party.GetPlayers():
            login_number = int(getattr(player, "login_number", 0) or 0)
            if login_number <= 0:
                continue
            agent_id = int(Party.Players.GetAgentIDByLoginNumber(login_number) or 0)
            if agent_id > 0:
                party_ids.add(agent_id)
        for hero in Party.GetHeroes():
            agent_id = int(getattr(hero, "agent_id", 0) or 0)
            if agent_id > 0:
                party_ids.add(agent_id)
        for henchman in Party.GetHenchmen():
            agent_id = int(getattr(henchman, "agent_id", 0) or 0)
            if agent_id > 0:
                party_ids.add(agent_id)
        cls._cached_party_member_ids = party_ids
        if cls._party_member_timer is not None:
            cls._party_member_timer.Reset()
        return party_ids

    @classmethod
    def _skill_matches(cls, skill_id: int, id_set: set[int], name_candidates: tuple[str, ...]) -> bool:
        if int(skill_id) in id_set:
            return True
        skill_name = cls._skill_name_cache.get(int(skill_id))
        if skill_name is None:
            skill_name = str(GLOBAL_CACHE.Skill.GetName(int(skill_id)) or "").strip().lower().replace("_", " ")
            cls._skill_name_cache[int(skill_id)] = skill_name
        if not skill_name:
            return False
        return any(c.lower().replace("_", " ") in skill_name for c in name_candidates)

    @classmethod
    def _set_mode(cls, mode: str, now_ms: float) -> None:
        if cls._shared_mode is None or cls._shared_mode == mode:
            cls._shared_mode = mode
            cls._shared_mode_locked_until_ms = now_ms + cls.MODE_SWITCH_DEBOUNCE_MS
            return
        if now_ms >= cls._shared_mode_locked_until_ms:
            cls._shared_mode = mode
            cls._shared_mode_locked_until_ms = now_ms + cls.MODE_SWITCH_DEBOUNCE_MS

    @classmethod
    def refresh(cls) -> None:
        cls._ensure_timers()
        cls._refresh_reaper_ids()

        if cls._event_refresh_timer is None or not cls._event_refresh_timer.IsExpired():
            return
        cls._event_refresh_timer.Reset()

        now_ms = time.monotonic() * 1000.0
        player_id = int(Player.GetAgentID())
        party_member_ids = cls._get_party_member_agent_ids()

        # Poll all non-party agents: if any is currently casting a Reaper
        # Dhuum's Rest or Ghostly Fury variant, mirror that mode.
        for agent_id in cls._get_reaper_candidate_agent_ids():
            agent_id_int = int(agent_id)
            if agent_id_int == player_id or agent_id_int in party_member_ids:
                continue
            casting_skill = int(Agent.GetCastingSkillID(agent_id_int) or 0)
            if casting_skill <= 0:
                continue
            is_drest = cls._skill_matches(casting_skill, cls._dhuums_rest_skill_ids, cls._DHUUMS_REST_CANDIDATES)
            is_fury = cls._skill_matches(casting_skill, cls._ghostly_fury_skill_ids, cls._GHOSTLY_FURY_CANDIDATES)
            if is_drest:
                cls._learned_reaper_ids.add(agent_id_int)
                cls._set_mode(cls.MODE_DREST, now_ms)
                return
            if is_fury:
                cls._learned_reaper_ids.add(agent_id_int)
                cls._set_mode(cls.MODE_FURY, now_ms)
                return

    @classmethod
    def is_dhuums_rest_mode(cls) -> bool:
        cls.refresh()
        return cls._shared_mode == cls.MODE_DREST

    @classmethod
    def is_ghostly_fury_mode(cls) -> bool:
        cls.refresh()
        return cls._shared_mode == cls.MODE_FURY
