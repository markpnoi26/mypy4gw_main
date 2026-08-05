"""Outpost Runner — BottingTree edition, with the route steered round the mobs.

The FSM bot walked its recorded routes and handed casting to a BuildMgr whose
`ProcessCombat` resets the process-wide ACTION queue on every CanCast failure.
Under a planner that reset stomps the planner's own movement mid-tick, so the
rotation here is a `BldMgrBT` running as a service tree: its casts go straight
to the skillbar and never touch that queue.

Three things stand between the character and the next outpost, and they are not
the same thing:

  Terrain      solved before a leg starts — the mover autopaths, so every
               waypoint it hands out is on walkable ground.
  Bodies on    a path is stale the moment a foe steps onto it. `avoidance_gate`
  the path     watches the mover's own current waypoint and steps the runner
               round whatever has parked on it, checking the step against the
               navmesh so avoidance never aims into a wall.
  Body blocks  a foe touching the character stops it dead while the mover
               happily re-issues the same command into the obstruction. The gate
               escalates sidestep -> mirrored sidestep -> back off, and the
               build's own Heart of Shadow / Death's Charge escape covers the
               rest.

Routes are the aC run files under `dev/reference/aC_Scripts/OutpostRunner/maps`,
unchanged — this bot only reads them.
"""

from __future__ import annotations

__script__ = {
    "name": "Outpost Runner BT",
    "function": "runner",
    "tags": ["running", "outpost", "shadow form", "dervish", "avoidance"],
    "claims": ["character"],
}

import importlib.util
import os
import re
import time
from typing import Callable
from typing import Iterator

import PyImGui
import PySystem

from Core import Agent
from Core import Map
from Core import ModelID
from Core import Player
from Core import Range
from Core import Routines
from Core.BottingTree import BottingTree
from Core.BTBuilds.FarmBuilds.Dervish.D_A.SF_Derv_Runner import SF_Derv_Runner
from Core.Pathing import AutoPathing
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Settings import Settings
from Core.routines_src.BehaviourTrees import BT
from Sources.marks_sources import route_avoidance

MODULE_NAME = "Outpost Runner BT"
INI_PATH = "Widgets/Automation/Bots/Outpost Runner BT"
INI_FILENAME = "Outpost_Runner_BT.ini"

RUNS_DIR = os.path.join(PySystem.Console.get_projects_path(), "dev", "reference", "aC_Scripts", "OutpostRunner", "maps")
TEXTURE = os.path.join(PySystem.Console.get_projects_path(), "Textures", "Skill_Icons", "[1543] - Pious Haste.jpg")

# The movement nodes read ONE pause key, and PAUSE_MOVEMENT is not available:
# the HeroAI branch rewrites it at the top of every tick, before the planner
# reads it, so a service write to that key is clobbered before it can act.
AVOID_HOLD_KEY = "AVOID_HOLD"

PREPARE_STEP = "Prepare"
FINISH_STEP = "All runs finished"

# Player.Move goes through the same ACTION queue the rotation casts from, and
# re-issuing a move to the same point is idempotent — so this wants rate
# limiting rather than a latch on an observed change.
REPOSITION_INTERVAL_MS = 900.0
# Expiry means "could not clear it", never "the leg failed". The gate hands the
# leg back to the mover, which re-autopaths from wherever the runner ended up.
MAX_HOLD_MS = 8000.0
# After giving up, the mover gets an uninterrupted run at the leg — long enough
# for its own strafe recovery and its waypoint timeout to actually fire.
HOLD_COOLDOWN_MS = 25_000.0
# How long a character pinned against a body has to stay put before the gate
# treats it as blocked rather than merely slow.
BODY_BLOCK_MS = 2500.0
BLOCKER_SCAN_RANGE = 1100.0
NAVMESH_MARGIN = 100.0
NAVMESH_STEP = 200.0

OUTPOST_TRAVEL_TIMEOUT_MS = 60_000
PARTY_LOAD_TIMEOUT_MS = 20_000
MAP_LOAD_TIMEOUT_MS = 90_000
# Per autopath waypoint, not per leg — they are 750 units apart at most, and the
# mover triples the budget after any pause, including this gate's holds.
WAYPOINT_TIMEOUT_MS = 20_000
LEG_TOLERANCE = 150.0
OUTPOST_SETTLE_MS = 1500

AVOIDANCE = route_avoidance.AvoidanceConfig(
    lookahead=700.0,
    clearance=float(Range.Touch.value),
    min_detour=90.0,
    max_detour=float(Range.Area.value) * 1.5,
    retreat=float(Range.Area.value),
    dwell_radius=60.0,
)
BLOCKER_RADIUS = float(Range.Touch.value) * 0.5

CONSUMABLE_RESTOCK = (
    (int(ModelID.Birthday_Cupcake.value), 5),
    (int(ModelID.War_Supplies.value), 5),
)
CONSUMABLE_UPKEEP = (int(ModelID.Birthday_Cupcake.value), int(ModelID.War_Supplies.value))

botting_tree: BottingTree | None = None
runner_build: SF_Derv_Runner | None = None
initialized = False
ini_key = ""

gate_status: dict[str, object] = {"reason": "idle", "blockers": 0, "dwell": 0.0, "navmesh": False, "holding": False}


# =============================================================================
# region RUN QUEUE
# =============================================================================
class QueuedRun:
    """Plain class, deliberately not a dataclass.

    The native side runs a script by compiling its source and exec'ing it under
    a name that is not a real module. `dataclasses` resolves a string annotation
    by looking the class's module up in sys.modules, which is None there, so a
    module-scope dataclass plus `from __future__ import annotations` raises on
    load. test/test_imports.py is the gate that catches it.
    """

    def __init__(self, region: str, name: str, outpost_id: int, outpost_path: list, segments: list):
        self.region = region
        self.name = name
        self.outpost_id = outpost_id
        self.outpost_path = outpost_path
        self.segments = segments

    @property
    def display(self) -> str:
        return f"[{self.region}] {self.name}"


queued_runs: list[QueuedRun] = []
run_tries: list[int] = []
queue_version = 0
planner_version = -1
current_run_index = 0


def attribute_ending_with(module, suffix: str, default):
    """The run files prefix every name with the run's own slug, so a suffix is
    the only stable handle on them."""
    lowered = suffix.lower()
    for name in dir(module):
        if name.lower().endswith(lowered):
            return getattr(module, name)
    return default


def load_run(region_dir: str, run_name: str) -> QueuedRun:
    spec = importlib.util.spec_from_file_location(run_name, os.path.join(region_dir, run_name) + ".py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ids = attribute_ending_with(module, "_ids", {}) or {}
    return QueuedRun(
        region=os.path.basename(region_dir),
        name=run_name,
        outpost_id=int(ids.get("outpost_id", 0)),
        outpost_path=list(attribute_ending_with(module, "_outpost_path", []) or []),
        segments=list(attribute_ending_with(module, "_segments", []) or []),
    )


def step_name(index: int) -> str:
    """Unique per queue position, so the same run queued twice still restarts at
    the copy that was actually running."""
    return f"Run {index + 1}: {queued_runs[index].name}"


# endregion


# =============================================================================
# region BT HELPERS
# =============================================================================
def optional(subtree: BehaviorTree, name: str) -> BehaviorTree:
    """Let a step expire or miss without dropping the rung.

    A FAILURE anywhere in the planner Sequence stops the whole bot. That is the
    right answer for a wrong map and the wrong one for a leg that merely could
    not confirm what it was watching for — the next leg autopaths from wherever
    the runner got to, so carrying on usually recovers.

    The fallback LOGS. A leg that quietly gave up is the hardest kind of run to
    read afterwards, because the bot carries on looking healthy.
    """

    def carry_on(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        PySystem.Console.Log(
            MODULE_NAME,
            f"{name} did not complete - continuing anyway.",
            PySystem.Console.MessageType.Warning,
        )
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.SelectorNode(
            name=name,
            children=[
                subtree,
                BehaviorTree.ActionNode(name=f"{name}: continue anyway", action_fn=carry_on, aftercast_ms=0),
            ],
        )
    )


def run_generator(factory: Callable[[], Iterator], name: str) -> BehaviorTree:
    state = {"generator": None}

    def advance(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if state["generator"] is None:
            state["generator"] = factory()
        try:
            next(state["generator"])
            return BehaviorTree.NodeState.RUNNING
        except StopIteration:
            state["generator"] = None
            return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=advance, aftercast_ms=0))


def walk(x: float, y: float) -> BehaviorTree:
    """One autopathed leg.

    `pause_on_combat=True` looks wrong for a runner and is not: with headless
    HeroAI off, COMBAT_ACTIVE is never set, so nothing ever pauses for combat.
    What the flag actually guards inside the mover is the per-waypoint timeout
    AND the strafe-on-stall recovery, both of which are switched off wholesale
    when it is False. A leg that can never fail can never be abandoned, and a
    runner wedged in a doorway would sit there for the rest of the session.
    """
    return BT.Movement.Move(
        x=float(x),
        y=float(y),
        tolerance=LEG_TOLERANCE,
        timeout_ms=WAYPOINT_TIMEOUT_MS,
        pause_on_combat=True,
        pause_flag_key=AVOID_HOLD_KEY,
    )


def walk_path(points: list, label: str) -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[
                optional(walk(point[0], point[1]), f"{label} leg {index + 1}") for index, point in enumerate(points)
            ],
        )
    )


def set_rotation_running(running: bool) -> BehaviorTree:
    def apply(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        get_build().SetRoutineFinished(not running)
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.ActionNode(
            name=f"Rotation({'running' if running else 'idle'})",
            action_fn=apply,
            aftercast_ms=0,
        )
    )


def mark_attempt(index: int) -> BehaviorTree:
    def apply(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        global current_run_index
        current_run_index = index
        while len(run_tries) <= index:
            run_tries.append(0)
        run_tries[index] += 1
        PySystem.Console.Log(
            MODULE_NAME,
            f"Starting run {index + 1} - {queued_runs[index].display} (attempt {run_tries[index]}).",
            PySystem.Console.MessageType.Info,
        )
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=f"MarkAttempt({index + 1})", action_fn=apply, aftercast_ms=0))


def abandon_run(index: int, note: str) -> BehaviorTree:
    """Skip to the next queued run rather than failing the planner.

    A FAILURE here would stop the bot and lose every run still queued behind
    this one, which is a much larger loss than the run that went wrong.
    """
    state = {"requested": False}

    def request(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if not state["requested"]:
            target = step_name(index + 1) if index + 1 < len(queued_runs) else FINISH_STEP
            PySystem.Console.Log(
                MODULE_NAME,
                f"Run {index + 1} abandoned ({note}). Jumping to '{target}'.",
                PySystem.Console.MessageType.Warning,
            )
            node.blackboard["restart_step_name_request"] = target
            state["requested"] = True
        # Pin here: the planner swaps this tree out at the end of the tick, and
        # letting the Sequence advance meanwhile would walk the rest of a route
        # we have already given up on.
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name=f"AbandonRun({index + 1})", action_fn=request, aftercast_ms=0))


def reach_map(index: int, map_id: int, label: str) -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SelectorNode(
            name=label,
            children=[
                BT.Map.WaitforMapLoad(map_id=map_id, log=True, timeout=MAP_LOAD_TIMEOUT_MS),
                abandon_run(index, f"never reached map {map_id}"),
            ],
        )
    )


# endregion


# =============================================================================
# region AVOIDANCE
# =============================================================================
def read_blockers(origin: tuple[float, float]) -> list:
    blockers = []
    for enemy in Routines.Agents.GetFilteredEnemyArray(origin[0], origin[1], BLOCKER_SCAN_RANGE):
        if Agent.IsDead(enemy):
            continue
        x, y = Agent.GetXY(enemy)
        blockers.append(route_avoidance.Blocker(float(x), float(y), BLOCKER_RADIUS))
    return blockers


def avoidance_gate() -> BehaviorTree:
    """Hold the mover and step round whatever is standing on its next waypoint.

    A service rather than a movement node of its own, because BT.Movement.Move
    already owns the parts that are hard — autopathing, per-waypoint timeouts,
    map-transition detection — and reimplementing those to bolt steering on
    would trade a bug class we do not have for one we would. It reads the
    mover's published waypoint and writes back the one pause key the legs watch.

    Services tick AFTER the planner, so the hold lands one frame later than it
    is decided. That is fine for steering and is why the rate limit exists.
    """
    state = {
        "primer": None,
        "primed_map": 0,
        "last_move_ms": 0.0,
        "hold_since_ms": 0.0,
        "cooldown_until_ms": 0.0,
        "stall_left": True,
        "dwell": route_avoidance.Dwell(),
    }

    def prime_navmesh() -> None:
        """`contains` and `has_line_of_sight` answer nothing until the navmesh
        for this map has been built; the native planner the mover prefers never
        builds one. Without this the walkability check silently passes anything.
        """
        map_id = int(Map.GetMapID() or 0)
        if not map_id or state["primed_map"] == map_id:
            return
        if state["primer"] is None:
            state["primer"] = AutoPathing().load_pathing_maps()
        try:
            next(state["primer"])
        except StopIteration:
            state["primer"] = None
            if AutoPathing().get_navmesh() is not None:
                state["primed_map"] = map_id

    def walkable(origin: tuple[float, float], point: tuple[float, float]) -> bool:
        navmesh = AutoPathing().get_navmesh()
        if navmesh is None:
            return True
        if not navmesh.contains(point[0], point[1], NAVMESH_MARGIN):
            return False
        return navmesh.has_line_of_sight(origin, point, NAVMESH_MARGIN, NAVMESH_STEP)

    def pick_aim(origin, waypoint, plan):
        """Sidestep, then the same step mirrored, then back off — first one the
        navmesh will accept."""
        candidates = []
        if plan is not None:
            candidates.append(((plan.x, plan.y), plan.reason))
            flipped = route_avoidance.mirror(origin, waypoint, (plan.x, plan.y))
            if flipped is not None:
                candidates.append((flipped, f"{plan.reason}-mirrored"))
        else:
            side = "left" if state["stall_left"] else "right"
            step = route_avoidance.sidestep(
                origin, waypoint, AVOIDANCE.max_detour / 2.0, state["stall_left"], AVOIDANCE
            )
            if step is not None:
                candidates.append((step, f"unpin-{side}"))
        back = route_avoidance.retreat_point(origin, waypoint, AVOIDANCE)
        if back is not None:
            candidates.append((back, "back off"))
        for point, reason in candidates:
            if walkable(origin, point):
                return point, reason
        return None, "no lane"

    def release(blackboard: dict) -> BehaviorTree.NodeState:
        blackboard[AVOID_HOLD_KEY] = False
        state["hold_since_ms"] = 0.0
        gate_status["holding"] = False
        return BehaviorTree.NodeState.RUNNING

    def tick(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        blackboard = node.blackboard
        prime_navmesh()
        gate_status["navmesh"] = AutoPathing().get_navmesh() is not None

        waypoint = blackboard.get("move_current_waypoint")
        move_state = str(blackboard.get("move_state", "") or "")
        pause_reason = str(blackboard.get("move_current_pause_reason", "") or "")

        route_active = (
            waypoint is not None
            and move_state in ("running", "paused")
            and Routines.Checks.Map.MapValid()
            and not Routines.Checks.Player.IsDead()
        )
        if not route_active:
            # Only here is the dwell latch worth throwing away: a new leg, a new
            # map or a corpse all mean the spot it was anchored on is history.
            state["dwell"].anchor = None
            gate_status["reason"] = "route not running"
            gate_status["blockers"] = 0
            gate_status["dwell"] = 0.0
            blackboard["BODY_BLOCKED"] = False
            return release(blackboard)

        # "external_pause" is this gate's own hold. Any other pause reason belongs
        # to somebody else and steering through it would be a deadlock. The dwell
        # latch SURVIVES that: the Shadow Form rotation casts constantly, and
        # resetting on every cast would mean a body block is never detected.
        if pause_reason not in ("", "external_pause"):
            gate_status["reason"] = f"mover paused - {pause_reason}"
            return release(blackboard)

        now = time.monotonic() * 1000.0
        if now < float(state["cooldown_until_ms"]):
            gate_status["reason"] = "stood down - mover has the leg"
            return release(blackboard)

        origin = Player.GetXY()
        target = (float(waypoint[0]), float(waypoint[1]))
        dwell = route_avoidance.dwell_ms(state["dwell"], origin, now, AVOIDANCE)
        blockers = read_blockers(origin)
        plan = route_avoidance.detour(origin, target, blockers, AVOIDANCE)

        # Pinned against a body, not merely slow. Scenery stalls stay the mover's
        # problem — it re-autopaths on its own timeout, and a gate that fired on
        # every pause would sidestep the runner off the path while it waited.
        pinned = not route_avoidance.is_clear(origin, blockers, AVOIDANCE)
        body_blocked = pinned and dwell >= BODY_BLOCK_MS

        gate_status["blockers"] = len(blockers)
        gate_status["dwell"] = dwell
        blackboard["BODY_BLOCKED"] = body_blocked

        if plan is None and not body_blocked:
            gate_status["reason"] = route_avoidance.describe(None, dwell)
            return release(blackboard)

        aim, reason = pick_aim(origin, target, plan)
        if aim is None:
            # Nowhere walkable to step onto. Holding would only stop the mover
            # from trying, so let it.
            gate_status["reason"] = "no walkable lane - mover has it"
            return release(blackboard)

        if state["hold_since_ms"] == 0.0:
            state["hold_since_ms"] = now
        elif now - float(state["hold_since_ms"]) >= MAX_HOLD_MS:
            # Standing down has to mean standing down. Every hold resets the
            # mover's waypoint timer, so re-holding on the next tick would keep
            # the leg alive forever and the run could never be abandoned.
            state["cooldown_until_ms"] = now + HOLD_COOLDOWN_MS
            PySystem.Console.Log(
                MODULE_NAME,
                f"Steering gave up after {MAX_HOLD_MS:.0f}ms: reason={reason} blockers={len(blockers)} "
                f"dwell={dwell:.0f}ms pinned={pinned} navmesh={gate_status['navmesh']}",
                PySystem.Console.MessageType.Warning,
            )
            return release(blackboard)

        blackboard[AVOID_HOLD_KEY] = True
        gate_status["holding"] = True
        gate_status["reason"] = f"{reason} - {len(blockers)} near, still {dwell / 1000.0:.1f}s"

        # The hold outranks "casting" inside the mover's own pause ladder, so
        # without this the sidestep would interrupt the cast — and the cast being
        # interrupted is Shadow Form, on a character that is by definition
        # standing in a mob. Hold the route, wait the cast out, step after.
        if Routines.Checks.Player.IsCasting():
            gate_status["reason"] = f"{reason} - waiting out a cast"
            return BehaviorTree.NodeState.RUNNING

        if now - float(state["last_move_ms"]) >= REPOSITION_INTERVAL_MS:
            Player.Move(aim[0], aim[1])
            state["last_move_ms"] = now
            # Alternate sides only once a step has actually been TAKEN. Flipping
            # per tick would leave whichever side the rate limiter happened to
            # land on, which is a coin toss rather than an escalation.
            if reason.startswith("unpin"):
                state["stall_left"] = not state["stall_left"]
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name="AvoidanceGate", action_fn=tick, aftercast_ms=0))


# endregion


# =============================================================================
# region PLANNER
# =============================================================================
def get_build() -> SF_Derv_Runner:
    global runner_build
    if runner_build is None:
        runner_build = SF_Derv_Runner()
    return runner_build


def prepare() -> BehaviorTree:
    tree = ensure_botting_tree()
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=PREPARE_STEP,
            children=[
                # HeroAI off: the Shadow Form rotation is the only thing allowed
                # to touch this skillbar, and two owners means neither wins.
                tree.Config.Pacifist(
                    auto_loot=False,
                    resurrection_scroll=False,
                    multi_account=False,
                    pause_on_danger=False,
                ),
                set_rotation_running(False),
                optional(run_generator(lambda: get_build().LoadSkillBar(), "LoadSkillBar"), "Load skillbar"),
            ],
        )
    )


def run_step(index: int) -> BehaviorTree:
    run = queued_runs[index]
    label = f"Run {index + 1}"
    children: list = [
        mark_attempt(index),
        set_rotation_running(False),
        BT.Map.TravelToOutpost(outpost_id=run.outpost_id, log=True, timeout=OUTPOST_TRAVEL_TIMEOUT_MS),
        optional(BT.Party.WaitForPartyLoaded(timeout_ms=PARTY_LOAD_TIMEOUT_MS), f"{label} party"),
        optional(BT.Map.SetHardMode(False), f"{label} normal mode"),
        optional(
            BT.Items.RestockItemsFromList(items=list(CONSUMABLE_RESTOCK), allow_missing=True),
            f"{label} restock",
        ),
        BT.Player.Wait(OUTPOST_SETTLE_MS),
        set_rotation_running(True),
    ]

    if run.outpost_path:
        children.append(walk_path(run.outpost_path, f"{label} leave outpost"))

    first_map_id = int(run.segments[0].get("map_id", 0)) if run.segments else 0
    if first_map_id:
        children.append(reach_map(index, first_map_id, f"{label} enter {first_map_id}"))

    for position, segment in enumerate(run.segments):
        path = list(segment.get("path", []) or [])
        if not path:
            continue
        following = run.segments[position + 1] if position + 1 < len(run.segments) else segment
        next_map_id = int(following.get("map_id", 0))
        children.append(walk_path(path, f"{label} segment {position + 1}"))
        if next_map_id:
            children.append(reach_map(index, next_map_id, f"{label} enter {next_map_id}"))

    children.append(set_rotation_running(False))
    return BehaviorTree(BehaviorTree.SequenceNode(name=label, children=children))


def finish() -> BehaviorTree:
    def apply(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        PySystem.Console.Log(MODULE_NAME, "All runs finished.", PySystem.Console.MessageType.Success)
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=FINISH_STEP, action_fn=apply, aftercast_ms=0))


def get_execution_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    steps: list[tuple[str, Callable[[], BehaviorTree]]] = [(PREPARE_STEP, prepare)]
    for index in range(len(queued_runs)):
        steps.append((step_name(index), lambda index=index: run_step(index)))
    steps.append((FINISH_STEP, finish))
    return steps


def ensure_botting_tree() -> BottingTree:
    global botting_tree
    if botting_tree is None:
        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name="OutpostRunSequence",
            repeat=False,
            reset=False,
            auto_start=False,
            multi_account=False,
            isolation_enabled=True,
        )
        # ConfigureUpkeep calls SetUpkeepTrees, which REPLACES the service list
        # rather than appending to it. Anything added below this line survives;
        # anything added above it vanishes silently.
        botting_tree.Config.ConfigureUpkeep(
            looting_enabled=False,
            resurrection_scroll=False,
            consumable_upkeeps=list(CONSUMABLE_UPKEEP),
        )
        botting_tree.AddBuild(get_build())
        botting_tree.AddServiceTree("AvoidanceGate", avoidance_gate)
    return botting_tree


def sync_planner() -> None:
    global planner_version
    if planner_version == queue_version:
        return
    tree = ensure_botting_tree()
    tree.Stop()
    tree.SetMainRoutine(get_execution_steps(), name="OutpostRunSequence", repeat=False)
    planner_version = queue_version


# endregion


# =============================================================================
# region UI
# =============================================================================
region_index = 0
run_index = 0
listing_cache: dict[str, list[str]] = {}


def run_sort_key(name: str) -> int:
    found = re.search(r"_(\d+)_", name)
    return int(found.group(1)) if found else 0


def list_regions() -> list[str]:
    """Cached: this is redrawn every frame the settings tab is open, and the
    route files do not appear mid-session."""
    if RUNS_DIR not in listing_cache:
        listing_cache[RUNS_DIR] = sorted(
            entry for entry in os.listdir(RUNS_DIR) if os.path.isdir(os.path.join(RUNS_DIR, entry))
        )
    return listing_cache[RUNS_DIR]


def list_runs(region_dir: str) -> list[str]:
    if region_dir not in listing_cache:
        listing_cache[region_dir] = sorted(
            (entry[:-3] for entry in os.listdir(region_dir) if entry.endswith(".py")), key=run_sort_key
        )
    return listing_cache[region_dir]


def clamped_combo(label: str, index: int, values: list[str]) -> int:
    """A stale index survives a rescan and a removed folder; the raw combo does
    not survive being handed one."""
    limit = len(values) - 1
    return min(max(PyImGui.combo(label, min(max(index, 0), limit), values), 0), limit)


def draw_settings() -> None:
    global region_index, run_index, queue_version

    PyImGui.text("Region & Run Selection")
    PyImGui.separator()
    regions = list_regions()
    if not regions:
        PyImGui.text(f"No run data under {RUNS_DIR}")
        return
    region_index = clamped_combo("##Region", region_index, regions)
    region_dir = os.path.join(RUNS_DIR, regions[region_index])

    runs = list_runs(region_dir)
    if not runs:
        PyImGui.text("No runs in this region.")
        return
    run_index = clamped_combo("##Run", run_index, runs)

    if PyImGui.button("Add Region", 120, 25):
        for name in runs:
            queued_runs.append(load_run(region_dir, name))
        queue_version += 1
    PyImGui.same_line(0, 10)
    if PyImGui.button("Add Run", 120, 25):
        queued_runs.append(load_run(region_dir, runs[run_index]))
        queue_version += 1
    PyImGui.same_line(0, 10)
    if PyImGui.button("Clear Runs", 120, 25):
        queued_runs.clear()
        run_tries.clear()
        queue_version += 1

    PyImGui.separator()
    PyImGui.text(f"Queued runs: {len(queued_runs)}")
    to_remove = None
    for index, run in enumerate(queued_runs):
        marker = " <-- CURRENT" if index == current_run_index and ensure_botting_tree().IsStarted() else ""
        tries = f" (tries: {run_tries[index]})" if index < len(run_tries) and run_tries[index] else ""
        PyImGui.text(f"  {index + 1}. {run.display}{marker}{tries}")
        PyImGui.same_line(0, 10)
        if PyImGui.button(f"X##{index}", 20, 20):
            to_remove = index
    if to_remove is not None:
        queued_runs.pop(to_remove)
        if to_remove < len(run_tries):
            run_tries.pop(to_remove)
        queue_version += 1


def draw_avoidance_tab() -> None:
    PyImGui.text(f"State: {gate_status['reason']}")
    PyImGui.separator()
    PyImGui.text(f"Holding route: {'yes' if gate_status['holding'] else 'no'}")
    PyImGui.text(f"Bodies in scan range: {gate_status['blockers']}")
    PyImGui.text(f"Time on one spot: {float(gate_status['dwell']) / 1000.0:.1f}s")
    PyImGui.separator()
    if gate_status["navmesh"]:
        PyImGui.text("Navmesh loaded - sidesteps are checked against terrain.")
    else:
        PyImGui.text("No navmesh yet - sidesteps are NOT terrain checked.")
    PyImGui.separator()
    PyImGui.text(f"Clearance {AVOIDANCE.clearance:.0f}, detour {AVOIDANCE.min_detour:.0f}-{AVOIDANCE.max_detour:.0f}")
    PyImGui.text(f"Lookahead {AVOIDANCE.lookahead:.0f}, back off {AVOIDANCE.retreat:.0f}")


def draw_help() -> None:
    PyImGui.text("Equipment")
    PyImGui.bullet_text("+5e +20% enchant duration weapon")
    PyImGui.bullet_text("+45hp -2dmg while enchanted shield")
    PyImGui.bullet_text("x5 Windwalker insignias")
    PyImGui.bullet_text("+1 head +1 Mysticism Rune")
    PyImGui.bullet_text("Major Vigor Rune")
    PyImGui.bullet_text("x3 Attunement Rune")
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.spacing()
    PyImGui.text("Routes: aC's OutpostRunner map files, read as-is.")
    PyImGui.text("Credits to: Aura, and aC's original script.")


# endregion


# =============================================================================
# region MAIN
# =============================================================================
def main() -> None:
    global initialized, ini_key

    if not Routines.Checks.Map.MapValid() or not Player.IsPlayerLoaded():
        return

    if not initialized:
        if not ini_key:
            ini_key = Settings(f"{INI_PATH}/{INI_FILENAME}", "account").name
            if not ini_key:
                return
        tree = ensure_botting_tree()
        tree.UI.override_draw_config(draw_settings)
        tree.UI.override_draw_help(draw_help)
        initialized = True

    sync_planner()
    tree = ensure_botting_tree()
    tree.tick()
    tree.UI.draw_window(icon_path=TEXTURE, extra_tabs=[("Avoidance", draw_avoidance_tab)])


if __name__ == "__main__":
    main()
# endregion
