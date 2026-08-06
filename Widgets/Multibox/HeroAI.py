# region Imports
import math
import os
import sys
import traceback
import Py4GW
import PyImGui

from HeroAI.engine import create_heroai_engine

MODULE_ALIASES = ['Automation/Multiboxing/HeroAI.py']
MODULE_NAME = "HeroAI"
MODULE_ICON = "Textures/Module_Icons/HeroAI.png"

from Core.Map import Map
from Core.Player import Player
from Core.routines_src.BehaviourTrees import BehaviorTree

from HeroAI.cache_data import CacheData
from HeroAI.follow.follower_runtime import (
    FollowExecutionState,
    execute_follower_follow,
    get_follow_destination_distance,
    is_follow_recovery_active,
)
from HeroAI.fight.report import CombatLineReporter
from HeroAI import enemy_party
from HeroAI import resurrection_scroll

from HeroAI.windows import (
    HeroAI_FloatingWindows,
    HeroAI_Windows,
)
from HeroAI.ui_base import HeroAI_BaseUI
from HeroAI.ui import draw_configure_window, draw_skip_cutscene_overlay
from HeroAI import team_viewer_broadcast
from Core import GLOBAL_CACHE, Agent, Range, Routines, ThrottledTimer, SharedCommandType, Utils

# region GLOBALS
LOOT_THROTTLE_CHECK = ThrottledTimer(250)

cached_data = CacheData()
heroai_build = create_heroai_engine(cached_data)
map_quads: list[Map.Pathing.Quad] = []
build_contract_map_signature: tuple[int, int, int, int] | None = None


# region Looting
def LootingNode(cached_data: CacheData) -> BehaviorTree.NodeState:
    options = cached_data.account_options
    if not options or not options.Looting:
        return BehaviorTree.NodeState.FAILURE

    if is_follow_recovery_active(cached_data, follow_execution_state):
        return BehaviorTree.NodeState.FAILURE

    if cached_data.data.in_aggro:
        return BehaviorTree.NodeState.FAILURE

    account_email = Player.GetAccountEmail()
    index, message = GLOBAL_CACHE.ShMem.PreviewNextMessage(account_email)

    if index != -1 and message and message.Command == SharedCommandType.PickUpLoot:
        if LOOT_THROTTLE_CHECK.IsExpired():
            return BehaviorTree.NodeState.FAILURE
        return BehaviorTree.NodeState.RUNNING

    if GLOBAL_CACHE.Inventory.GetFreeSlotCount() <= 1:
        return BehaviorTree.NodeState.FAILURE

    from Core.py4gwcorelib_src.loot_filters import LootFilters

    loot_array = LootFilters().GetLootArray(Range.Earshot.value)

    if len(loot_array) == 0:
        return BehaviorTree.NodeState.FAILURE

    self_account = GLOBAL_CACHE.ShMem.GetAccountDataFromEmail(account_email)
    if self_account:
        GLOBAL_CACHE.ShMem.SendMessage(
            self_account.AccountEmail,
            self_account.AccountEmail,
            SharedCommandType.PickUpLoot,
            (0, 0, 0, 0),
        )
        LOOT_THROTTLE_CHECK.Reset()
        # Return RUNNING so the tree knows the task started
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree.NodeState.FAILURE


# region Combat
def HandleOutOfCombat(cached_data: CacheData):
    options = cached_data.account_options

    if not options or not options.Combat:  # halt operation if combat is disabled
        return False

    if cached_data.data.in_aggro:
        return False

    if is_follow_recovery_active(cached_data, follow_execution_state):
        return False

    player_agent_id = Player.GetAgentID()
    if cached_data.combat_handler.InCastingRoutine() or Agent.IsCasting(player_agent_id):
        return False

    heroai_build.set_cached_data(cached_data)
    next(heroai_build.ProcessOOC(), None)
    return heroai_build.DidTickSucceed()


def HandleCombat(cached_data: CacheData):
    options = cached_data.account_options

    if not options or not options.Combat:  # halt operation if combat is disabled
        return False

    if is_follow_recovery_active(cached_data, follow_execution_state):
        return False

    if not cached_data.data.in_aggro:
        return False

    heroai_build.set_cached_data(cached_data)
    next(heroai_build.ProcessCombat(), None)
    return heroai_build.DidTickSucceed()


# region Following
following_flag = False
follow_execution_state = FollowExecutionState()
FOLLOW_INI_FILENAMES = (
    "FollowModule_Formations.ini",
    "FollowModule_Settings.ini",
)
printed_widget_list = False


def _follow_ini_paths() -> list[str]:
    base_path = os.path.join(
        PySystem.Console.get_projects_path(),
        "Settings",
        "Global",
        "HeroAI",
    )
    return [os.path.join(base_path, filename) for filename in FOLLOW_INI_FILENAMES]


def _follow_ini_ready() -> bool:
    return all(os.path.exists(path) for path in _follow_ini_paths())


def EnsureFollowModuleIni() -> None:
    if _follow_ini_ready():
        return

    try:
        from HeroAI.follow.editor import _init_once

        _init_once()
    except Exception as e:
        PySystem.Console.Log(
            MODULE_NAME, f"Follow formation INI bootstrap failed: {e}", PySystem.Console.MessageType.Error
        )


combat_line_reporter = CombatLineReporter()


def holding_station(cached_data: CacheData) -> bool:
    """True when the leader has published a party flag — a fight zone or a
    hand-placed one. Following and holding station are different jobs even
    though they share a mover, so they get different BT nodes."""
    options = GLOBAL_CACHE.ShMem.GetHeroAIOptionsByPartyNumber(0)
    if options is None or not bool(getattr(options, "IsFlagged", False)):
        return False
    return abs(float(options.AllFlag.x)) > 0.001 or abs(float(options.AllFlag.y)) > 0.001


def fight_zone(cached_data: CacheData) -> BehaviorTree.NodeState:
    if cached_data.data.is_leader:
        return BehaviorTree.NodeState.FAILURE
    try:
        combat_line_reporter.tick(
            heroai_build.GetBuildContract(),
            heroai_build.GetBuildContractName() if hasattr(heroai_build, "GetBuildContractName") else "",
        )
    except Exception:
        pass
    if not holding_station(cached_data):
        return BehaviorTree.NodeState.FAILURE
    # Same mover, different intent: it walks to the published fight slot rather
    # than to a slot behind the leader.
    return execute_follower_follow(cached_data, follow_execution_state)


def Follow(cached_data: CacheData) -> BehaviorTree.NodeState:
    if cached_data.data.is_leader:
        return BehaviorTree.NodeState.FAILURE  # leader doesn't follow anyone
    if holding_station(cached_data):
        return BehaviorTree.NodeState.FAILURE  # FightZone owns movement while engaged
    return execute_follower_follow(cached_data, follow_execution_state)


def handle_UI(cached_data: CacheData):
    global HeroAI_BT
    # Flag placement reads live mouse state, so it has to stay on the draw loop.
    # It is leader-only and self-gates, so followers pay nothing for it here.
    if Routines.Checks.Map.MapValid():
        HeroAI_BaseUI._process_flagging_runtime(cached_data)
    if not cached_data.ui_state_data.show_classic_controls:
        HeroAI_BaseUI.DrawEmbeddedWindow(cached_data)
    else:
        HeroAI_BaseUI.DrawControlPanelWindow(cached_data)
        if HeroAI_FloatingWindows.settings.ShowPartyPanelUI:
            HeroAI_BaseUI.DrawFollowerUI(cached_data)

    if HeroAI_BaseUI.show_debug:
        HeroAI_BaseUI.draw_debug_window(HeroAI_BT)

    HeroAI_FloatingWindows.show_ui(cached_data)
    if Map.IsExplorable() and cached_data.data.is_leader and enemy_party.is_enabled():
        enemy_party.ui_main()
    HeroAI_BaseUI.DrawBuildMatchesWindow(cached_data)
    HeroAI_BaseUI.DrawFollowFormationsQuickWindow(cached_data)


def initialize(cached_data: CacheData) -> bool:
    global build_contract_map_signature

    if not Routines.Checks.Map.MapValid():
        heroai_build.ClearBuildContract()
        build_contract_map_signature = None
        return False

    if not GLOBAL_CACHE.Party.IsPartyLoaded():
        return False

    if not Map.IsExplorable():  # halt operation if not in explorable area
        heroai_build.ClearBuildContract()
        build_contract_map_signature = None
        return False

    if Map.IsInCinematic():  # halt operation during cinematic
        return False

    # HeroAI_FloatingWindows.draw_Targeting_floating_buttons(cached_data)
    heroai_build.set_cached_data(cached_data)
    map_signature = (
        int(Map.GetMapID()),
        int(Map.GetRegion()[0]),
        int(Map.GetDistrict()),
        int(Map.GetLanguage()[0]),
    )
    if build_contract_map_signature != map_signature:
        heroai_build.EnsureBuildContract(cached_data)
        build_contract_map_signature = map_signature
    cached_data.UpdateCombat()
    return True


# region main
# DEPRECATED FOR BEHAVIOUR TREE IMPLEMENTATION
# KEPT FOR REFERENCE
"""def UpdateStatus(cached_data: CacheData) -> bool:
    
    if (
            not Agent.IsAlive(Player.GetAgentID())
            or (HeroAI_FloatingWindows.DistanceToDestination(cached_data) >= Range.SafeCompass.value)
            or Agent.IsKnockedDown(Player.GetAgentID())
            or cached_data.combat_handler.InCastingRoutine()
            or Agent.IsCasting(Player.GetAgentID())
        ):
            return False

    
    if LootingRoutineActive():
        return True

    if HandleOutOfCombat(cached_data):
        return True

    if Agent.IsMoving(Player.GetAgentID()):
        return False

    if Loot(cached_data):
        return True

    if Follow(cached_data):
        cached_data.follow_throttle_timer.Reset()
        return True

    if HandleCombat(cached_data):
        cached_data.auto_attack_timer.Reset()
        return True

    return False"""


def IsUserInterrupting() -> bool:
    from Core.enums_src.IO_enums import Key

    io = PyImGui.get_io()

    if io.want_capture_keyboard or io.want_capture_mouse:
        return False

    movement_keys = [
        Key.W.value,
        Key.A.value,
        Key.S.value,
        Key.D.value,
        Key.Q.value,
        Key.E.value,
        Key.Z.value,
        Key.R.value,
        Key.UpArrow.value,
        Key.DownArrow.value,
        Key.LeftArrow.value,
        Key.RightArrow.value,
    ]

    for vk in movement_keys:
        if PyImGui.is_key_down(vk):
            return True

    if (PyImGui.is_mouse_down(0) and PyImGui.is_mouse_down(1)) or PyImGui.is_mouse_down(2):
        return True

    return False


GlobalGuardNode = BehaviorTree.SequenceNode(
    name="GlobalGuard",
    children=[
        BehaviorTree.ConditionNode(name="IsAlive", condition_fn=lambda: Agent.IsAlive(Player.GetAgentID())),
        BehaviorTree.ConditionNode(
            name="DistanceSafe",
            condition_fn=lambda: get_follow_destination_distance(cached_data) < Range.SafeCompass.value
            or is_follow_recovery_active(cached_data, follow_execution_state),
        ),
        BehaviorTree.ConditionNode(
            name="NotKnockedDown", condition_fn=lambda: not Agent.IsKnockedDown(Player.GetAgentID())
        ),
    ],
)

CastingBlockNode = BehaviorTree.ConditionNode(
    name="IsCasting",
    condition_fn=lambda: (
        BehaviorTree.NodeState.RUNNING
        if (
            (
                cached_data.combat_handler.InCastingRoutine()
                and not is_follow_recovery_active(cached_data, follow_execution_state)
            )
            or Agent.IsCasting(Player.GetAgentID())
        )
        else BehaviorTree.NodeState.SUCCESS
    ),
)


def movement_interrupt() -> BehaviorTree.NodeState:
    # During a smart unstuck detour, BT.Move must be ticked at full HeroAI
    # BT rate so it can steer the engine target continuously
    if follow_execution_state.stuck.mode != "idle":
        return BehaviorTree.NodeState.FAILURE  # let Follow run every tick during detour
    if Agent.IsMoving(Player.GetAgentID()):
        return BehaviorTree.NodeState.SUCCESS  # block lower-priority automation for this tick
    return BehaviorTree.NodeState.FAILURE  # allow next branch


def user_interrupt() -> BehaviorTree.NodeState:
    # if IsUserInterrupting():
    #    return BehaviorTree.NodeState.SUCCESS   # block lower-priority automation for this tick
    return BehaviorTree.NodeState.FAILURE  # allow next branch


HeroAI_BT = BehaviorTree.SequenceNode(
    name="HeroAI_Main_BT",
    children=[
        # ---------- GLOBAL HARD GUARD ----------
        GlobalGuardNode,
        CastingBlockNode,
        # ---------- PRIORITY SELECTOR ----------
        BehaviorTree.SelectorNode(
            name="UpdateStatusSelector",
            children=[
                # Looting routine already active (allowed anytime)
                BehaviorTree.ActionNode(
                    name="LootingRoutine",
                    action_fn=lambda: LootingNode(cached_data),
                ),
                # Out-of-combat behavior (allowed while moving)
                BehaviorTree.ActionNode(
                    name="HandleOutOfCombat",
                    action_fn=lambda: (
                        BehaviorTree.NodeState.SUCCESS
                        if HandleOutOfCombat(cached_data)
                        else BehaviorTree.NodeState.FAILURE
                    ),
                ),
                # User / external movement override (blocks below)
                BehaviorTree.ActionNode(
                    name="UserInterrupt",
                    action_fn=lambda: user_interrupt(),
                ),
                # Holding station in a fight. Mutually exclusive with Follow:
                # whichever one owns movement, the other returns FAILURE, so the
                # debug window shows which job is actually running.
                BehaviorTree.ActionNode(
                    name="FightZone",
                    action_fn=lambda: fight_zone(cached_data),
                ),
                # Travelling behind the leader
                BehaviorTree.ActionNode(
                    name="Follow",
                    action_fn=lambda: Follow(cached_data),
                ),
                BehaviorTree.ActionNode(
                    name="MovementInterrupt",
                    action_fn=lambda: movement_interrupt(),
                ),
                # Combat
                BehaviorTree.ActionNode(
                    name="HandleCombat",
                    action_fn=lambda: (
                        cached_data.auto_attack_timer.Reset() or BehaviorTree.NodeState.SUCCESS
                        if HandleCombat(cached_data)
                        else BehaviorTree.NodeState.FAILURE
                    ),
                ),
            ],
        ),
    ],
)


# region real_main
def configure():
    draw_configure_window(MODULE_NAME, HeroAI_FloatingWindows.configure_window)


def tooltip():
    import PyImGui
    from Core.py4gwcorelib_src.Color import Color
    from Core.ImGui import ImGui

    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("HeroAI: Multibox Combat Engine", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()

    # Description
    PyImGui.text("An advanced multi-account synchronization and combat AI system.")
    PyImGui.text("This widget transforms extra game instances into intelligent,")
    PyImGui.text("automated party members that behave like high-performance heroes.")
    PyImGui.spacing()

    # Features
    PyImGui.text_colored("Features:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Multibox Logic: Synchronizes actions across multiple game clients")
    PyImGui.bullet_text("Advanced AI: Replaces standard hero behavior with custom combat routines")
    PyImGui.bullet_text("Intelligent interrupt logic, hex removal, enemy tracking, and more")
    PyImGui.bullet_text("Formation Control: Dynamic follower distancing and tactical positioning")
    PyImGui.bullet_text("Automation Suite: Integrated auto-looting, salvaging, and cutscene skipping")
    PyImGui.bullet_text("Behavior Trees: Complex decision-making for combat and out-of-combat states")
    PyImGui.bullet_text("Shared Memory: Seamless data exchange via the Shared Memory Manager (SMM)")

    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.spacing()

    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Apo")
    PyImGui.bullet_text("Contributors: Mark, frenkey, Dharmantrix, aC, Greg-76, ")
    PyImGui.bullet_text("Sloppynacho, Wick-Divinus, LLYANL, Zilvereyes, valkogw")

    PyImGui.end_tooltip()


# The update loop runs at ~100 Hz; the tree used to tick every other draw frame.
# This keeps roughly the old cadence, and matches what follower steering wants
# during a detour.
TREE_TICK_INTERVAL_MS = 33

tree_timer = ThrottledTimer(TREE_TICK_INTERVAL_MS)


def report_error(kind: str, error: Exception):
    PySystem.Console.Log(MODULE_NAME, f"{kind} encountered: {str(error)}", PySystem.Console.MessageType.Error)
    PySystem.Console.Log(MODULE_NAME, f"Stack trace: {traceback.format_exc()}", PySystem.Console.MessageType.Error)


def tick_logic():
    """Everything that is not drawing.

    Driven from exactly one loop at a time - see update()/draw(). Never both:
    cached_data, map_quads and the tree are shared mutable state, and letting the
    draw loop read them while this writes tore them apart on map transitions.
    """
    global cached_data, map_quads

    if not tree_timer.IsExpired():
        return
    tree_timer.Reset()

    try:
        cached_data.Update()

        log_aggro_probe()

        EnsureFollowModuleIni()
        HeroAI_FloatingWindows.update()
        team_viewer_broadcast.tick()
        resurrection_scroll.tick()

        if initialize(cached_data):
            HeroAI_BT.tick()
        else:
            map_quads.clear()
            HeroAI_BT.reset()

    except ImportError as e:
        report_error("ImportError", e)
    except ValueError as e:
        report_error("ValueError", e)
    except TypeError as e:
        report_error("TypeError", e)
    except Exception as e:
        report_error("Unexpected error", e)


AGGRO_PROBE_ENABLED = True
AGGRO_PROBE_MAX_LINES = 50

# MC = minimised combat. Account scope, so every client writes its own file and a
# minimised follower can be read without restoring its window. JsonFactory rather
# than a raw file because all disk access goes through the persistence jail.
MC_DOCUMENT = "MC.json"

aggro_probe_timer = ThrottledTimer(1000)
aggro_probe_lines = 0
mc_lines: list[str] = []
mc_path_announced = False


def record_mc_line(line: str) -> None:
    global mc_path_announced

    import PySystem
    from Core.py4gwcorelib_src.JsonFactory import JsonFactory

    mc_lines.append(line)
    if len(mc_lines) > AGGRO_PROBE_MAX_LINES:
        del mc_lines[0 : len(mc_lines) - AGGRO_PROBE_MAX_LINES]

    document = JsonFactory(MC_DOCUMENT)
    document.set_json("lines", list(mc_lines))
    document.save()

    if not mc_path_announced:
        mc_path_announced = True
        PySystem.Console.Log("MC", "writing to %s" % document.resolved_path(), PySystem.Console.MessageType.Info)


def log_aggro_probe() -> None:
    """TEMPORARY. Reports which gate is holding the combat branch shut.

    Logs only while in aggro, capped per engagement — a follower that heals but
    never attacks has failed one of these gates, and the interesting window is the
    fight itself. The counter resets when combat ends, so each fight gets a fresh
    budget instead of one long fight burning it for the session.
    """
    global aggro_probe_lines

    if not AGGRO_PROBE_ENABLED or not aggro_probe_timer.IsExpired():
        return
    aggro_probe_timer.Reset()

    # MC only: minimised AND in combat. A visible client records nothing.
    if not (Utils.IsDrawLoopStalled() and cached_data.data.in_aggro):
        aggro_probe_lines = 0
        return
    if aggro_probe_lines >= AGGRO_PROBE_MAX_LINES:
        return
    aggro_probe_lines += 1

    import PySystem

    try:
        import time

        from Core.AgentArray import AgentArray
        from Core import ActionQueueManager

        data = cached_data.data
        enemies = AgentArray.GetEnemyArray()
        me = Player.GetAgentID()
        target = Player.GetTargetID()
        queue = ActionQueueManager()

        line = (
            "%s pos=%d enemies=%d effective=%d | target=%d attacking=%d casting=%d moving=%d idle=%d"
            " | weapon=%d holding=%d qACTION=%s next=%s"
            % (
                time.strftime("%H:%M:%S"),
                int(data.party_position),
                len(enemies or []),
                int(bool(data.in_aggro)),
                int(target or 0),
                int(bool(Agent.IsAttacking(me))),
                int(bool(Agent.IsCasting(me))),
                int(bool(Agent.IsMoving(me))),
                int(bool(Agent.IsIdle(me))),
                int(data.weapon_type or 0),
                int(bool(Agent.IsHoldingItem(me))),
                "empty" if queue.IsEmpty("ACTION") else "BUSY",
                queue.GetNextActionName("ACTION") or "-",
            )
        )

        PySystem.Console.Log("MC", line, PySystem.Console.MessageType.Info)
        record_mc_line(line)
    except Exception as error:
        PySystem.Console.Log("MC", "probe failed: %s" % error, PySystem.Console.MessageType.Warning)


def update():
    # Only while minimised. A visible client has a live draw loop and drives the
    # same work from there, so exactly one thread ever touches the shared state.
    if Utils.IsDrawLoopStalled():
        tick_logic()


def draw():
    if not Utils.IsDrawLoopStalled():
        tick_logic()
    try:
        handle_UI(cached_data)
    except Exception as e:
        report_error("Unexpected error", e)


def minimal():
    draw_skip_cutscene_overlay()


def on_enable():
    HeroAI_FloatingWindows.settings.reset()
    HeroAI_FloatingWindows.SETTINGS_THROTTLE.SetThrottleTime(50)


__all__ = ['update', 'draw', 'configure', 'on_enable']
