"""BT port of Builds/Any/KeiranThackerayEOTN.py — Hearts of the North escort AI.

=============================================================================
FarmBuild. Callers:
  Widgets/Automation/Bots/Farmers/Trophies/War Supply/HeartsOfTheNorth.py
  Widgets/Automation/Bots/Levelers/Factions/Factions Character Leveler.py
  Legacy code and tests/Deprecated but working/AuspiciousBeginnings_3.0.py
  Legacy code and tests/Deprecated but working/keiran_vengeance of blades_farm.py
=============================================================================

WHY THIS IS THE LEAST TREE-SHAPED BUILD IN THE REPO

This is not a rotation with some state attached — it is an *escort AI* that
happens to end with a skill ladder. Its ProcessSkillCasting is explicitly two
systems stacked in one coroutine (the legacy docstring says so):

  TOP:    movement / combat-AI — Miku tracking, spirit avoidance, AoE
          sidestep, kiting, LoS gap-close, path retrace
  BOTTOM: the skill priority ladder

Four things make a Selector decomposition wrong rather than merely awkward:

1. IT OWNS AN EXTERNAL FSM.
   `_set_pause(reason)` / `_clear_pause(reason)` maintain a `pause_reasons`
   set and call `fsm.pause()` / `fsm.resume()` on the *bot's* FSM. Reasons
   are added and cleared from different branches across frames ("miku_dead",
   "miku_reset", "spirit", "miku_lazy"). Splitting the body reorders those
   pause/resume edges, which stalls or un-stalls the whole bot.

2. IT RUNS A TWO-LEG PATH-FOLLOWING STATE MACHINE.
   When Miku falls through the world, it builds `_retrace_ph` (backward) and
   `_return_ph` (forward) PathHandlers plus a FollowXY, then drives them one
   frame at a time through `_retrace_phase` ('retrace' -> 'return' -> ''). That
   is a multi-frame sub-machine with its own completion detection.

3. MOST BRANCHES END IN `Player.Move(...)` + wait(500) + `return`.
   The movement section is a chain of mutually-exclusive escapes, each of which
   ends the frame. Selector semantics (first SUCCESS wins) would express that,
   but the branches also write shared timers (`last_movement_run`,
   `combat_approach_at`, `los_fail_since`) that later branches read in the same
   frame. Order and mutation are load-bearing.

4. IT DELEGATES TO HeroAI DIRECTLY, NOT VIA ResolveFallback.
   The tail is `yield from self.hero_ai_handler.ProcessSkillCasting()` after
   `_sync_hero_ai_fallback_skill_blocks()` masks slots 2-7. That is an explicit
   hand-off, not the BldMgrBT fallback chain, and it is preserved as such.

So: the routine is hosted verbatim under one BT node via BldMgrBT.drive(),
which advances it one step per frame and reports RUNNING until it completes.
Contrast DervBoneFarmer, which WAS rewritten as a real tree because its phases
are discrete and it owns a simple `status` field.

-----------------------------------------------------------------------------
DEAD CODE FOUND — TWO TRIGGERS CAN NEVER FIRE
-----------------------------------------------------------------------------
`_los_recent = []` at the hit-detection block, with the CombatEvents import
commented out at the top of the legacy module. Therefore:

    damage_dealt    = any(... for ... in [])   ->  ALWAYS False
    damage_received = any(... for ... in [])   ->  ALWAYS False

Consequences in the current behaviour, all preserved here verbatim:

  * The "Lazy Miku" trigger requires `damage_dealt` -> it NEVER fires, and the
    `else` branch resets `miku_lazy_at = 0.0` every frame.
  * LoS gap-close WITH a priority target always takes the `else` branch, so it
    closes the gap unconditionally once the 3 s grace expires.
  * LoS gap-close WITHOUT a priority target likewise always moves toward the
    group.

Re-enabling CombatEvents would change all three at once. Flagged, not fixed.

-----------------------------------------------------------------------------
CONSTRUCTOR AND MATCHING NOTES
-----------------------------------------------------------------------------
Legacy signature is `__init__(self, fsm=None, debug_fn=None)` with NO
`match_only`. BuildRegistry._call_build_ctor tries `match_only=True` first,
catches the TypeError, and retries with no args — so it still instantiates,
just via the fallback path. A trailing `match_only` parameter is added here so
the first attempt succeeds cleanly; existing keyword callers are unaffected.

`super().__init__(name=...)` passes no professions and no required_skills, so
ScoreMatch returns 0 for everyone, and HeroAIBTEngine requires `score > 0`, so
it is not selectable even before the FarmBuilds exclusion. Belt, braces, and
location.
"""

import ctypes
import math
import time
from typing import Callable, Optional

import PySystem

from Core import (
    GLOBAL_CACHE,
    ActionQueueManager,
    Agent,
    AgentArray,
    AutoPathing,
    BldMgrBT,
    ConsoleLog,
    Map,
    Player,
    Range,
    Routines,
)
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from HeroAI.bt.bt_engine import HeroAIBTEngine

from ...nodes import cast, rotation_tree

MIKU_MODEL_ID = 8513

SHADOWSONG_ID = 4264
SOS_SPIRIT_IDS = frozenset({4280, 4281, 4282})  # Anger, Hate, Suffering
AOE_SKILLS = {1380: 2000, 1372: 2000, 1083: 2000, 830: 2000, 192: 5000}
SPIRIT_FLEE_DIST = 1900
AOE_SIDESTEP_DIST = 600.0

MIKU_PATH = [
    (10165.07, -6181.43),
    (8270.00, -9010.00),
    (4245.00, -7412.00),
    (2025.00, -10726.00),
    (-1822.00, -11230.00),
    (-2292.00, -9034.00),
    (-4190.00, -10460.00),
    (-5640.00, -10371.00),
    (-8748.00, -8329.00),
    (-12122.00, -7530.00),
    (-15170.00, -8951.00),
]

# White Mantle priority kill order, highest first.
PRIORITY_TARGET_MODELS = [
    8369,  # Ritualist: Preservation, strong heal, hex-remove, spirits
    8373,  # Ritualist: Weapon of Remedy rit (hard-rez)
    8343,  # Abbot: Prot Boon Signet, Spiritbond
    8344,  # Abbot: Mantra of Recall
    8345,  # Abbot: Restore Condition
    8322,  # Sycophant: Word of Healing
    8368,  # Ritualist: spear caster
    8372,  # Ritualist: Minion-summoning rit
    8324,  # Ritualist (additional)
    8359,  # Seeker 1
    8361,  # Seeker 2 Conjure Flames
]
TARGET_SWITCH_INTERVAL = 1.0
PRIORITY_TARGET_RANGE = 1500
WEAPON_RANGE = Range.Longbow


def distance_between(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def escape_point(
    me_x: float, me_y: float, threat_x: float, threat_y: float, dist: float, rotation: int = 0, debug_fn=None
):
    """Point `dist` away from the threat, navmesh-aware.

    Sweeps +/-180 degrees in 10 degree steps looking for a reachable direction
    with line of sight; falls back to the straight-line escape when the navmesh
    has not loaded yet."""
    navmesh = AutoPathing().get_navmesh()

    dx = me_x - threat_x
    dy = me_y - threat_y
    escape_radians = math.atan2(dy, dx)

    if rotation != 0:
        escape_radians = escape_radians + math.radians(rotation)

    escape_x = me_x + dist * math.cos(escape_radians)
    escape_y = me_y + dist * math.sin(escape_radians)
    escape_x_far = me_x + 1000 * math.cos(escape_radians)
    escape_y_far = me_y + 1000 * math.sin(escape_radians)
    escape_pos = (escape_x, escape_y)

    if navmesh:
        base_deg = math.degrees(escape_radians) % 360 - 180
        found = False

        if navmesh.find_trapezoid_id_by_coord((escape_x_far, escape_y_far)) is not None:
            if navmesh.has_line_of_sight((me_x, me_y), (escape_x_far, escape_y_far)):
                found = True
        if not found:
            for step in range(1, 19):
                for sign in (1, -1):
                    candidate_deg = (base_deg + sign * step * 10) % 360 - 180
                    candidate_rads = math.radians(candidate_deg)
                    escape_x_far = me_x + 1000.0 * math.cos(candidate_rads)
                    escape_y_far = me_y + 1000.0 * math.sin(candidate_rads)
                    goal_trap = navmesh.find_trapezoid_id_by_coord((escape_x_far, escape_y_far))
                    if goal_trap:
                        if navmesh.has_line_of_sight((me_x, me_y), (escape_x_far, escape_y_far)):
                            escape_radians = candidate_rads
                            escape_x = me_x + dist * math.cos(escape_radians)
                            escape_y = me_y + dist * math.sin(escape_radians)
                            escape_pos = (escape_x, escape_y)
                            found = True
                            break
                if found:
                    break

    return escape_pos


def nearest_from(array, origin_x: float, origin_y: float, max_dist: float = 0) -> int:
    """Closest agent in `array` to the origin, optionally within max_dist."""
    best_id = 0
    best_dist = float("inf")
    for eid in array:
        ex, ey = Agent.GetXY(eid)
        d = distance_between(origin_x, origin_y, ex, ey)
        if max_dist != 0 and d > max_dist:
            continue
        if d < best_dist:
            best_dist = d
            best_id = eid
    return best_id


class KeiranThackerayEOTN(BldMgrBT):
    def __init__(self, fsm=None, debug_fn: Optional[Callable[[], bool]] = None, match_only: bool = False):
        super().__init__(
            name="Keiran HeroAI Build",
            is_combat_automator_compatible=False,
        )
        if match_only:
            return

        self.debug_fn: Callable[[], bool] = debug_fn if debug_fn is not None else (lambda: False)
        self.hero_ai_handler = HeroAIBTEngine(standalone_fallback=True)

        self.natures_blessing = GLOBAL_CACHE.Skill.GetID("Natures_Blessing")
        self.relentless_assault = GLOBAL_CACHE.Skill.GetID("Relentless_Assault")
        self.keiran_sniper_shot = GLOBAL_CACHE.Skill.GetID("Keirans_Sniper_Shot_Hearts_of_the_North")
        self.terminal_velocity = GLOBAL_CACHE.Skill.GetID("Terminal_Velocity")
        self.gravestone_marker = GLOBAL_CACHE.Skill.GetID("Gravestone_Marker")
        self.rain_of_arrows = GLOBAL_CACHE.Skill.GetID("Rain_of_Arrows")
        self.find_their_weakness = GLOBAL_CACHE.Skill.GetID("Find_Their_Weakness_Thackeray")
        self.theres_nothing_to_fear = GLOBAL_CACHE.Skill.GetID("Theres_Nothing_To_Fear_Thackeray")

        # Priority-target state
        self.last_target_check = 0.0
        self.locked_target_id = 0
        self.locked_priority = len(PRIORITY_TARGET_MODELS)

        # Movement / combat-AI state
        self.last_movement_run = 0.0
        self.miku_idle = False
        self.player_combat = False
        self.miku_lazy_at = 0.0
        self.miku_reset_at = 0.0
        self.miku_reset_active = False
        self.miku_retrace_issued = False

        # Miku retrace path-following state
        self.retrace_ph = None
        self.return_ph = None
        self.miku_follow = None
        self.retrace_phase = ''

        # LoS / combat approach
        self.aoe_caster_id = 0
        self.aoe_caster_pos = (0.0, 0.0)
        self.aoe_sidestep_at = 0.0
        self.last_cast_at = 0.0
        self.combat_approach_at = 0.0
        self.los_fail_since = 0.0
        self.los_debug_at = 0.0

        # FSM pause/resume support
        self.fsm = fsm
        self.pause_reasons: set = set()
        self.ai_paused_fsm = False

    @property
    def debug(self) -> bool:
        return self.debug_fn()

    def sync_hero_ai_fallback_skill_blocks(self) -> None:
        blocked_fallback_skill_ids = [GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot) for slot in range(2, 8)]
        self.hero_ai_handler.ApplyBlockedSkillIDs(blocked_fallback_skill_ids)

    def set_pause(self, reason: str) -> None:
        self.pause_reasons.add(reason)
        if self.fsm is not None and not self.fsm.is_paused():
            self.fsm.pause()
            self.ai_paused_fsm = True

    def clear_pause(self, reason: str) -> None:
        self.pause_reasons.discard(reason)
        if self.fsm is not None and not self.pause_reasons and self.ai_paused_fsm and self.fsm.is_paused():
            self.fsm.resume()
            self.ai_paused_fsm = False

    def escort_routine(self):
        """Movement/combat AI on top, skill ladder below. Preserved verbatim."""
        player_id = Player.GetAgentID()
        if not Agent.IsValid(player_id) or Agent.IsDead(player_id):
            yield
            return
        player_x, player_y = Agent.GetXY(player_id)
        raw_enemies = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByCondition(
            raw_enemies, lambda eid: Agent.IsValid(eid) and not Agent.IsDead(eid) and Agent.GetHealth(eid) > 0.0
        )
        now = time.time()

        # Empathy / Spirit Shackles detection — needed early by the LoS block.
        empathy_id = GLOBAL_CACHE.Skill.GetID("Empathy")
        spirit_shackles_id = GLOBAL_CACHE.Skill.GetID("Spirit_Shackles")
        has_empathy = Routines.Checks.Agents.HasEffect(player_id, empathy_id) or Routines.Checks.Agents.HasEffect(
            player_id, spirit_shackles_id
        )

        # ================= MOVEMENT / COMBAT-AI =================
        player_health = Agent.GetHealth(player_id)
        enemies_close = AgentArray.Filter.ByCondition(
            enemy_array, lambda eid: distance_between(player_x, player_y, *Agent.GetXY(eid)) <= 300
        )
        enemies_agro = AgentArray.Filter.ByCondition(
            enemy_array, lambda eid: distance_between(player_x, player_y, *Agent.GetXY(eid)) <= 1500
        )
        enemies_far = AgentArray.Filter.ByCondition(
            enemy_array, lambda eid: distance_between(player_x, player_y, *Agent.GetXY(eid)) <= 2000
        )
        if Agent.IsInCombatStance(player_id) and len(enemies_far) > 0:
            self.player_combat = True
        else:
            self.player_combat = False

        # ---- Miku tracking ----
        miku_id = Routines.Agents.GetAgentIDByModelID(MIKU_MODEL_ID)
        miku_dead = miku_id != 0 and Agent.IsDead(miku_id)
        miku_reset = miku_id == 0 and Map.GetMapID() == 849

        if miku_id != 0 and not miku_dead:
            mk_x, mk_y = Agent.GetXY(miku_id)
            enemies_near_miku = AgentArray.Filter.ByCondition(
                enemy_array, lambda eid: distance_between(mk_x, mk_y, *Agent.GetXY(eid)) <= 1000
            )
            self.miku_idle = (
                Agent.IsIdle(miku_id) and not Agent.IsInCombatStance(miku_id) and len(enemies_near_miku) == 0
            )
        else:
            # Clear idle so the lazy-Miku trigger cannot fire on stale state.
            self.miku_idle = False

        if miku_dead and self.player_combat and len(enemies_far) > 1 and now - self.last_movement_run >= 1.0:
            if self.debug:
                PySystem.Console.Log(
                    "Avoidance", "Miku Dead Trigger -- retreating", PySystem.Console.MessageType.Warning
                )
            avg_x = sum(Agent.GetXY(eid)[0] for eid in enemies_far) / len(enemies_far)
            avg_y = sum(Agent.GetXY(eid)[1] for eid in enemies_far) / len(enemies_far)
            ex_x, ex_y = escape_point(player_x, player_y, avg_x, avg_y, 300, debug_fn=self.debug_fn)
            ActionQueueManager().ResetAllQueues()
            self.last_movement_run = now
            self.combat_approach_at = 0.0
            self.los_fail_since = 0.0
            Player.Move(ex_x, ex_y)
            yield from Routines.Yield.wait(500)
            return
        elif miku_dead and not self.player_combat:
            self.set_pause("miku_dead")
        else:
            self.clear_pause("miku_dead")

        # Miku fell through the world: activate reset, issue backtrack once after 5 s.
        if miku_reset:
            PySystem.Console.Log("Miku Model ID", f"{MIKU_MODEL_ID}", PySystem.Console.MessageType.Warning)
            PySystem.Console.Log("Miku ID", f"{miku_id}", PySystem.Console.MessageType.Warning)
            self.miku_reset_active = True
            if self.miku_reset_at == 0.0:
                self.miku_reset_at = now
            elif now - self.miku_reset_at >= 5.0 and not self.miku_retrace_issued:
                if self.debug:
                    PySystem.Console.Log(
                        "Avoidance", "Miku Reset - retracing path", PySystem.Console.MessageType.Warning
                    )
                nearest_idx = min(
                    range(len(MIKU_PATH)), key=lambda i: distance_between(player_x, player_y, *MIKU_PATH[i])
                )
                start_idx = nearest_idx - 1 if nearest_idx > 0 else 0
                retrace_coords = list(reversed(MIKU_PATH[: start_idx + 1]))
                return_coords = list(MIKU_PATH[: nearest_idx + 1])

                self.retrace_ph = Routines.Movement.PathHandler(retrace_coords)
                self.return_ph = Routines.Movement.PathHandler(return_coords)
                self.miku_follow = Routines.Movement.FollowXY(tolerance=150)
                self.retrace_phase = 'retrace'

                self.miku_retrace_issued = True
                self.set_pause("miku_reset")

        # ---- Drive the active retrace/return leg one frame at a time ----
        if self.retrace_phase:
            path_handler = self.retrace_ph if self.retrace_phase == 'retrace' else self.return_ph
            Routines.Movement.FollowPath(path_handler, self.miku_follow)
            if Routines.Movement.IsFollowPathFinished(path_handler, self.miku_follow):
                if self.retrace_phase == 'retrace':
                    self.miku_follow = Routines.Movement.FollowXY(tolerance=150)
                    self.retrace_phase = 'return'
                else:
                    self.miku_reset_active = False
                    self.miku_reset_at = 0.0
                    self.miku_retrace_issued = False
                    self.retrace_ph = None
                    self.return_ph = None
                    self.miku_follow = None
                    self.retrace_phase = ''
                    self.clear_pause("miku_reset")
            yield
            return

        if self.miku_reset_active:
            self.set_pause("miku_reset")
            yield
            return

        # ---- Spirit avoidance ----
        spirit_id = 0
        sp_x = sp_y = 0.0
        for eid in enemy_array:
            model = Agent.GetModelID(eid)
            if model == SHADOWSONG_ID or model in SOS_SPIRIT_IDS:
                ex, ey = Agent.GetXY(eid)
                if distance_between(player_x, player_y, ex, ey) < SPIRIT_FLEE_DIST:
                    spirit_id = eid
                    sp_x, sp_y = ex, ey
                    break

        if spirit_id != 0:
            self.set_pause("spirit")
        else:
            self.clear_pause("spirit")

        if Routines.Checks.Player.CanAct():
            if self.player_combat and self.combat_approach_at == 0.0:
                self.combat_approach_at = now + 3.0
            elif not self.player_combat:
                # Combat ended — reset per-encounter state so fight N does not
                # bleed into fight N+1.
                self.combat_approach_at = 0.0
                self.los_fail_since = 0.0
                self.locked_target_id = 0
                self.locked_priority = len(PRIORITY_TARGET_MODELS)
                self.miku_lazy_at = 0.0
                self.aoe_caster_id = 0
                self.aoe_caster_pos = (0.0, 0.0)

            if spirit_id != 0 and len(enemies_far) > 4 and now - self.last_movement_run >= 1.0:
                if self.debug:
                    PySystem.Console.Log(
                        "Avoidance",
                        f"Spirit Trigger - {len(enemies_far)} Enemies",
                        PySystem.Console.MessageType.Warning,
                    )
                ex_x, ex_y = escape_point(player_x, player_y, sp_x, sp_y, 500, debug_fn=self.debug_fn)
                ActionQueueManager().ResetAllQueues()
                self.last_movement_run = now
                self.combat_approach_at = 0.0
                self.los_fail_since = 0.0
                Player.Move(ex_x, ex_y)
                yield from Routines.Yield.wait(500)
                return

            if spirit_id == 0 and len(enemies_agro) > 4 and now - self.last_movement_run >= 1.0:
                if self.debug:
                    PySystem.Console.Log(
                        "Avoidance",
                        f"Overwhelmed Trigger - {len(enemies_agro)} Enemies",
                        PySystem.Console.MessageType.Warning,
                    )
                avg_x = sum(Agent.GetXY(eid)[0] for eid in enemies_far) / len(enemies_far)
                avg_y = sum(Agent.GetXY(eid)[1] for eid in enemies_far) / len(enemies_far)
                ex_x, ex_y = escape_point(player_x, player_y, avg_x, avg_y, 300, debug_fn=self.debug_fn)
                ActionQueueManager().ResetAllQueues()
                self.last_movement_run = now
                self.combat_approach_at = 0.0
                self.los_fail_since = 0.0
                Player.Move(ex_x, ex_y)
                yield from Routines.Yield.wait(500)
                return

            if player_health < 0.5 and len(enemies_far) > 0 and now - self.last_movement_run >= 1.0:
                if self.debug:
                    PySystem.Console.Log(
                        "Avoidance",
                        f"Critical HP Trigger - {player_health:.0%} HP",
                        PySystem.Console.MessageType.Warning,
                    )
                avg_x = sum(Agent.GetXY(eid)[0] for eid in enemies_far) / len(enemies_far)
                avg_y = sum(Agent.GetXY(eid)[1] for eid in enemies_far) / len(enemies_far)
                ex_x, ex_y = escape_point(player_x, player_y, avg_x, avg_y, 300, debug_fn=self.debug_fn)
                ActionQueueManager().ResetAllQueues()
                self.last_movement_run = now
                self.combat_approach_at = 0.0
                self.los_fail_since = 0.0
                Player.Move(ex_x, ex_y)
                yield from Routines.Yield.wait(500)
                return

            # ---- Hit detection ----
            # DEAD CODE: los_recent is hardcoded empty because the CombatEvents
            # import is commented out in the legacy module. damage_dealt and
            # damage_received are therefore ALWAYS False. See module header for
            # which triggers this disables. Preserved exactly.
            LOS_WINDOW_MS = 4000
            tick_now = ctypes.windll.kernel32.GetTickCount()
            los_recent = []  # CombatEvents.GetRecentDamage(count=100)
            damage_dealt = any(
                src == player_id and (tick_now - ts) < LOS_WINDOW_MS for ts, tgt, src, _dmg, _skill, _crit in los_recent
            )
            damage_received = any(
                tgt == player_id and (tick_now - ts) < LOS_WINDOW_MS for ts, tgt, src, _dmg, _skill, _crit in los_recent
            )

            # Lazy Miku: pull enemies toward her. Requires damage_dealt, so this
            # never fires while CombatEvents is disabled.
            if self.player_combat and self.miku_idle and damage_dealt and self.los_fail_since == 0.0:
                if self.miku_lazy_at == 0.0:
                    self.miku_lazy_at = now
                elif now - self.miku_lazy_at >= 3.0 and now - self.last_movement_run >= 1.0:
                    if self.debug:
                        PySystem.Console.Log("Avoidance", "Lazy Miku Trigger", PySystem.Console.MessageType.Warning)
                    nearest_enemy = nearest_from(enemy_array, player_x, player_y, 1500)
                    if nearest_enemy != 0:
                        ne_x, ne_y = Agent.GetXY(nearest_enemy)
                        if len(enemies_far) > 1:
                            ex_x, ex_y = escape_point(player_x, player_y, ne_x, ne_y, 300, debug_fn=self.debug_fn)
                        else:
                            ex_x, ex_y = escape_point(
                                player_x, player_y, ne_x, ne_y, 300, rotation=180, debug_fn=self.debug_fn
                            )
                        ActionQueueManager().ResetAllQueues()
                        self.last_movement_run = now
                        self.combat_approach_at = 0.0
                        self.miku_lazy_at = 0.0
                        Player.Move(ex_x, ex_y)
                        self.set_pause("miku_lazy")
                        yield from Routines.Yield.wait(500)
                        self.clear_pause("miku_lazy")
            else:
                self.miku_lazy_at = 0.0

            # Kite when two or more enemies are in melee range.
            if enemies_agro and len(enemies_close) > 1 and now - self.last_movement_run >= 1.0:
                if self.debug:
                    PySystem.Console.Log("Avoidance", "Melee Swarm Trigger", PySystem.Console.MessageType.Warning)
                avg_x = sum(Agent.GetXY(eid)[0] for eid in enemies_agro) / len(enemies_agro)
                avg_y = sum(Agent.GetXY(eid)[1] for eid in enemies_agro) / len(enemies_agro)
                ex_x, ex_y = escape_point(player_x, player_y, avg_x, avg_y, 300, debug_fn=self.debug_fn)
                ActionQueueManager().ResetAllQueues()
                self.last_movement_run = now
                self.combat_approach_at = 0.0
                Player.Move(ex_x, ex_y)
                yield from Routines.Yield.wait(500)
                return

            # ---- LoS gap-close ----
            if (
                self.player_combat
                and self.combat_approach_at != 0.0
                and now >= self.combat_approach_at
                and now - self.last_movement_run >= 1.0
                and not has_empathy
            ):

                priority_valid = (
                    self.locked_target_id != 0
                    and Agent.IsValid(self.locked_target_id)
                    and not Agent.IsDead(self.locked_target_id)
                )

                if priority_valid:
                    if damage_dealt:
                        self.los_fail_since = 0.0
                    else:
                        if self.los_fail_since == 0.0:
                            self.los_fail_since = now
                        pl_x, pl_y = Agent.GetXY(self.locked_target_id)
                        if self.debug:
                            PySystem.Console.Log(
                                "LoS",
                                "Not hitting priority target -- closing gap",
                                PySystem.Console.MessageType.Warning,
                            )
                        ActionQueueManager().ResetAllQueues()
                        ep_x, ep_y = escape_point(
                            player_x, player_y, pl_x, pl_y, 300, rotation=180, debug_fn=self.debug_fn
                        )
                        self.last_movement_run = now
                        self.combat_approach_at = 0.0
                        Player.Move(ep_x, ep_y)
                        yield from Routines.Yield.wait(500)
                        return
                else:
                    if damage_dealt or damage_received:
                        self.los_fail_since = 0.0
                    else:
                        if self.los_fail_since == 0.0:
                            self.los_fail_since = now
                        move_target = nearest_from(enemies_agro, player_x, player_y)
                        if move_target != 0:
                            if self.debug:
                                PySystem.Console.Log(
                                    "LoS",
                                    "No damage dealt or received -- moving toward group",
                                    PySystem.Console.MessageType.Warning,
                                )
                            ne_x, ne_y = Agent.GetXY(move_target)
                            ep_x, ep_y = escape_point(
                                player_x, player_y, ne_x, ne_y, 300, rotation=180, debug_fn=self.debug_fn
                            )
                            self.last_movement_run = now
                            self.combat_approach_at = 0.0
                            Player.Move(ep_x, ep_y)
                            yield from Routines.Yield.wait(500)
                            return

            # ---- AoE sidestep ----
            if self.aoe_caster_id != 0 and now >= self.aoe_sidestep_at:
                if Agent.IsValid(self.aoe_caster_id) and not Agent.IsDead(self.aoe_caster_id):
                    self.aoe_caster_pos = Agent.GetXY(self.aoe_caster_id)
                tx, ty = self.aoe_caster_pos
                if tx != 0.0 or ty != 0.0:
                    if self.debug:
                        PySystem.Console.Log("Avoidance", "AoE Sidestep", PySystem.Console.MessageType.Warning)
                    sx, sy = escape_point(
                        player_x, player_y, tx, ty, AOE_SIDESTEP_DIST, rotation=90, debug_fn=self.debug_fn
                    )
                    ActionQueueManager().ResetAllQueues()
                    Player.Move(sx, sy)
                    yield from Routines.Yield.wait(500)
                    self.last_movement_run = now
                self.aoe_caster_id = 0
                self.aoe_caster_pos = (0.0, 0.0)
                return  # skip skill casting this frame after a sidestep
            elif self.aoe_caster_id == 0:
                for eid in enemy_array:
                    skill = Agent.GetCastingSkillID(eid)
                    if skill in AOE_SKILLS:
                        self.aoe_sidestep_at = now + AOE_SKILLS[skill] / 1000.0
                        self.aoe_caster_id = eid
                        self.aoe_caster_pos = Agent.GetXY(eid)
                        break

        # ================= SKILL CASTING =================

        if has_empathy:
            ActionQueueManager().ResetAllQueues()
            Player.ChangeTarget(player_id)  # clear target to cancel auto-attack
            self.los_fail_since = 0.0

        # Priority target selection. Suppressed under Empathy so the target
        # drop above is not immediately undone.
        if not has_empathy and now - self.last_target_check >= TARGET_SWITCH_INTERVAL:
            self.last_target_check = now

            if self.locked_target_id != 0:
                pl_x, pl_y = Agent.GetXY(self.locked_target_id)
                if (
                    not Agent.IsValid(self.locked_target_id)
                    or Agent.IsDead(self.locked_target_id)
                    or distance_between(player_x, player_y, pl_x, pl_y) > PRIORITY_TARGET_RANGE
                ):
                    self.locked_target_id = 0
                    self.locked_priority = len(PRIORITY_TARGET_MODELS)
                    self.los_fail_since = 0.0

            best_id = 0
            best_priority = len(PRIORITY_TARGET_MODELS)
            for eid in enemy_array:
                ex, ey = Agent.GetXY(eid)
                if distance_between(player_x, player_y, ex, ey) > PRIORITY_TARGET_RANGE:
                    continue
                model = Agent.GetModelID(eid)
                if model in PRIORITY_TARGET_MODELS:
                    prio = PRIORITY_TARGET_MODELS.index(model)
                    if prio < best_priority:
                        best_priority = prio
                        best_id = eid

            if best_id != 0 and best_priority < self.locked_priority:
                self.locked_target_id = best_id
                self.locked_priority = best_priority
                self.los_fail_since = 0.0

            if self.locked_target_id != 0 and Player.GetTargetID() != self.locked_target_id:
                Player.ChangeTarget(self.locked_target_id)

        # ---- Nature's Blessing: heal Keiran or Miku ----
        health_threshold = 0.80
        miku_health_threshold = 0.50
        miku_low_health = False
        miku_in_earshot = False

        if miku_id != 0 and not miku_dead:
            mk_x_h, mk_y_h = Agent.GetXY(miku_id)
            if Agent.GetHealth(miku_id) < miku_health_threshold:
                miku_low_health = True
                miku_in_earshot = distance_between(player_x, player_y, mk_x_h, mk_y_h) <= Range.Earshot.value

        if (yield from Routines.Yield.Skills.IsSkillIDUsable(self.natures_blessing)):
            if player_health < health_threshold or has_empathy or (miku_low_health and miku_in_earshot):
                ActionQueueManager().ResetAllQueues()
                yield from Routines.Yield.Skills.CastSkillID(self.natures_blessing, aftercast_delay=100)
                return

        if not (
            Routines.Checks.Map.IsExplorable() and Routines.Checks.Player.CanAct() and Routines.Checks.Skills.CanCast()
        ):
            ActionQueueManager().ResetAllQueues()
            yield from Routines.Yield.wait(1000)
            return

        # Skip attacks during aftercast; healing and avoidance still fire each frame.
        if now - self.last_cast_at < 0.750:
            yield
            return

        def cast_at(target, skill_id):
            if Routines.Checks.Map.IsExplorable():
                yield from Routines.Yield.Agents.ChangeTarget(target)
                yield from Routines.Yield.Skills.CastSkillID(skill_id, aftercast_delay=0)
            yield

        in_danger = Routines.Checks.Agents.InDanger(aggro_area=WEAPON_RANGE)
        keiran_sniper_shot_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.keiran_sniper_shot)
        relentless_assault_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.relentless_assault)
        terminal_velocity_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.terminal_velocity)
        gravestone_marker_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.gravestone_marker)
        rain_of_arrows_ready = yield from Routines.Yield.Skills.IsSkillIDUsable(self.rain_of_arrows)
        theres_nothing_to_fear_ready = self.theres_nothing_to_fear != 0 and (
            yield from Routines.Yield.Skills.IsSkillIDUsable(self.theres_nothing_to_fear)
        )
        find_their_weakness_ready = self.find_their_weakness != 0 and (
            yield from Routines.Yield.Skills.IsSkillIDUsable(self.find_their_weakness)
        )

        if in_danger:
            # Keiran's Sniper Shot — finish a hexed enemy
            if keiran_sniper_shot_ready:
                hexed_enemy = Routines.Targeting.GetEnemyHexed(2000)
                if hexed_enemy != 0 and not has_empathy:
                    ActionQueueManager().ResetAllQueues()
                    self.last_cast_at = now
                    yield from cast_at(hexed_enemy, self.keiran_sniper_shot)
                    return

            # Relentless Assault — cleanse a condition
            if relentless_assault_ready:
                player_conditioned = (
                    Agent.IsDegenHexed(player_id)
                    or Agent.IsBleeding(player_id)
                    or Agent.IsPoisoned(player_id)
                    or Routines.Checks.Agents.HasEffect(player_id, GLOBAL_CACHE.Skill.GetID("Blind"))
                    or Routines.Checks.Agents.HasEffect(player_id, GLOBAL_CACHE.Skill.GetID("Deep_Wound"))
                    or Routines.Checks.Agents.HasEffect(player_id, GLOBAL_CACHE.Skill.GetID("Cracked_Armor"))
                    or Routines.Checks.Agents.HasEffect(player_id, GLOBAL_CACHE.Skill.GetID("Burning"))
                )
                if player_conditioned and not has_empathy:
                    target = self.locked_target_id or Routines.Targeting.GetEnemyInjured(WEAPON_RANGE.value)
                    if target != 0:
                        self.last_cast_at = now
                        yield from cast_at(target, self.relentless_assault)
                        return

            # There is Nothing to Fear! — buff-checked so it does not double-cast
            if theres_nothing_to_fear_ready:
                has_tntf = Routines.Checks.Agents.HasEffect(player_id, self.theres_nothing_to_fear)
                if not has_empathy and self.player_combat and not has_tntf:
                    ActionQueueManager().ResetAllQueues()
                    self.last_cast_at = now
                    yield from self.CastSkillID(self.theres_nothing_to_fear, aftercast_delay=0)
                    return

            # Terminal Velocity — interrupt a caster or apply to a bleeding enemy
            if terminal_velocity_ready:
                if not has_empathy:
                    target = (
                        self.locked_target_id
                        or Routines.Targeting.GetEnemyCasting(WEAPON_RANGE.value)
                        or Routines.Targeting.GetEnemyBleeding(WEAPON_RANGE.value)
                    )
                    if target != 0:
                        self.last_cast_at = now
                        yield from cast_at(target, self.terminal_velocity)
                        return

            # Find Their Weakness! — ally shout targeting Miku
            if find_their_weakness_ready:
                if not has_empathy and self.player_combat and miku_id != 0:
                    mk_x_f, mk_y_f = Agent.GetXY(miku_id)
                    if distance_between(player_x, player_y, mk_x_f, mk_y_f) <= Range.Earshot.value:
                        self.last_cast_at = now
                        yield from cast_at(miku_id, self.find_their_weakness)
                        return

            # Gravestone Marker — spirits first, then healthy enemies
            if gravestone_marker_ready:
                if not has_empathy:
                    target = (
                        self.locked_target_id
                        or Routines.Targeting.GetNearestSpirit(WEAPON_RANGE.value)
                        or Routines.Targeting.GetEnemyHealthy(WEAPON_RANGE.value)
                    )
                    if target != 0:
                        self.last_cast_at = now
                        yield from cast_at(target, self.gravestone_marker)
                        return

            # Rain of Arrows — spirits first, then clustered enemies
            if rain_of_arrows_ready:
                if not has_empathy:
                    target = (
                        self.locked_target_id
                        or Routines.Targeting.GetNearestSpirit(WEAPON_RANGE.value)
                        or Routines.Targeting.TargetClusteredEnemy(WEAPON_RANGE.value)
                    )
                    if target != 0:
                        self.last_cast_at = now
                        yield from cast_at(target, self.rain_of_arrows)
                        return

        # Explicit hand-off to HeroAI for anything unbound, with slots 2-7 masked.
        # This is NOT the BldMgrBT fallback chain — kept as a direct delegation.
        if not has_empathy:
            self.sync_hero_ai_fallback_skill_blocks()
            yield from self.hero_ai_handler.ProcessSkillCasting()
        else:
            yield  # do not let HeroAI re-target while Empathy/Spirit Shackles is up

    def build_rotation_tree(self) -> BehaviorTree:
        """Single hosted node — see module header for why this is not a tree."""
        return rotation_tree(
            "KeiranThackerayEOTN",
            [],
            [cast(self, "KeiranEscortRoutine", lambda: self.escort_routine())],
        )
