"""Shards of Orr BDS team farm — Fendi Nin, driven from the party leader.

Same shape as the Voltaic Spear team bot: the leader walks the route, HeroAI's
fight zone drives everyone else, and the end chest goes one account at a time
through `team_turns`. What SoO adds on top of Slavers is the torch — a bundle
the leader carries through two dark levels, dropped before fights and re-lit at
braziers when its buff runs out — and a two-phase boss that must stay dead for a
while before the room counts as clear.

Route numbers are carried over verbatim from the community SoO bot
(Scripts/py4gw-community-bots/.../Dungeons/SoO.py). Treat them as measured,
not derived.
"""

# NO `from __future__ import annotations` here. It makes every annotation a
# string, which sends @dataclass through `sys.modules[cls.__module__].__dict__`
# to resolve them — and the native side runs a script by exec'ing its source
# under a name that is not a real module, so that lookup returns None and the
# script dies at load. Every working dataclass script in this tree omits it.
__script__ = {
    "name": "SoO Team BT",
    "function": "farmer",
    "tags": ["soo", "bds", "dungeon", "eotn", "fendi", "multibox", "heroai"],
    "claims": ["character", "inventory", "sharedmem"],
}

import math
import os
import time
from typing import Callable

import PyImGui
import PyInventory
import PySystem

from Core import Effects
from Core import Item
from Core import Map
from Core import Player
from Core import Quest
from Core import Range
from Core import SharedCommandType
from Core.Agent import Agent
from Core.AgentArray import AgentArray
from Core.BottingTree import BottingTree
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Settings import Settings
from Core.routines_src.BehaviourTrees import BT
from Sources.marks_sources import brazier_route
from Sources.marks_sources import gadget_choice
from Sources.marks_sources import gadget_interact
from Sources.marks_sources import team_bt

MODULE_NAME = "SoO Team BT"
INI_PATH = "Widgets/Automation/Bots/SoO Team BT"
INI_FILENAME = "SoO_Team_BT.ini"

VLOXS_FALLS_ID = 624
ARBOR_BAY_ID = 485
SOO_L1_ID = 581
SOO_L2_ID = 582
SOO_L3_ID = 583

DWARVEN_BLESSING_DIALOG = 0x84
LOST_SOULS_QUEST_ID = 0x324
SHANDRA_TAKE_DIALOG = 0x832401
SHANDRA_REWARD_DIALOG = 0x832407
# Crewmember Shandra. Identified by her ENCODED NAME — readable synchronously
# from agent memory (captured live at her spot), unlike display names, which
# need a request round trip and read empty right after arriving. She stands in
# ONE spot, by the dungeon gate — confirmed live. (The community bot carries a
# `SHANDRA_POSITION` of (14067, -17253); it is assigned and never used, and
# she is not there.) The walk stops at Nearby range because her spot cannot be
# stood ON. With the enc string doing the matching, the radius is only a
# search area — wide costs nothing, a bystander cannot win the scan.
SHANDRA_XY = (12056.0, -17882.0)
SHANDRA_ENC = '\\x8102\\x0F53\\xE569\\xC6E5\\x5CE2'
SHANDRA_SCAN_RADIUS = 2500.0
SHANDRA_GHOST_SCAN_RADIUS = 2500.0

TORCH_MODEL_IDS = (22341, 22342)
TORCH_BUFF_SKILL_ID = 2545
TORCH_SCAN_RADIUS = 5000.0
TORCH_PICKUP_DIST = 180.0
TORCH_TRY_INTERVAL_MS = 700.0
# Walking distance plus a fight the party may be finishing. Expiry means "could
# not confirm the pickup" and the step is wrapped so it cannot end the run.
TORCH_PICKUP_TIMEOUT_MS = 90_000

BRAZIER_INTERACT_RADIUS = 250.0
BRAZIER_ARRIVE_DIST = 200.0
# One interact lights the brazier but does not build enough flame to carry — the
# torch buff dies en route and the leader walks all the way back to relight.
# Interact a SECOND time, a beat later, to top the flame up before leaving.
BRAZIER_INTERACTS = 2
BRAZIER_REINTERACT_MS = 1000.0
# The buff window trails the interact by a server round trip; reading it early
# scores a lit brazier as failed and sends the leader on a pointless relight.
BRAZIER_LIGHT_SETTLE_MS = 1500.0
BRAZIER_ROUTE_TIMEOUT_MS = 600_000

FENDI_MODEL_IDS = (7064, 7065)  # Fendi Nin and the Soul that respawns him
FENDI_ANCHOR_XY = (-16022.9, 17889.9)
FENDI_ANCHOR_RADIUS = 750.0
# The Soul re-summons Fendi if it lives; a short quiet spell between phases must
# not read as victory. Community-measured window.
FENDI_CLEAR_CONFIRM_MS = 20_000.0
FENDI_TICK_MS = 500
FENDI_TIMEOUT_MS = 1_800_000

FENDI_CHEST_XY = (-15800.98, 16901.23)
FENDI_CHEST_GADGET_ID = 8934
CHEST_SCAN_RADIUS = 700.0

DUNGEON_RETURN_TIMEOUT_MS = 300_000

ARBOR_EXIT_XY = (15505.38, 12460.59)
ARBOR_BLESSING_XY = (16327.0, 11607.0)
ARBOR_TO_SHANDRA_PATH: list[tuple[float, float]] = [
    (13455.43, 10678.00),
    (9850.00, 5025.00),
    (11207.11, 1872.32),
    (10452.02, 178.50),
    (10782.86, -3321.00),
    (8360.94, -6550.00),
    (10382.85, -12342.00),
    (10080.30, -13995.00),
    (10667.00, -16116.00),
    (10747.49, -17546.00),
    (11156.00, -17802.00),
]
DUNGEON_ENTRY_PATH: list[tuple[float, float]] = [
    (11177.0, -17683.0),
    (10218.0, -18864.0),
    (9519.0, -19968.0),
]
DUNGEON_GATE_XY = (9240.07, -20260.95)

L1_BLESSING_XY = (-11686.0, 10427.0)
L1_PATH_BRIDGANT: list[tuple[float, float]] = [
    (-11685.5, 10475.5),
    (-10682.6, 9841.2),
    (-9670.9, 9744.2),
    (-8661.9, 9975.7),
    (-7653.5, 10063.4),
    (-6652.0, 10156.2),
    (-5646.1, 10717.7),
    (-4642.3, 11376.3),
    (-3640.8, 11984.6),
    (-2634.2, 12702.1),
    (-1630.8, 13315.2),
    (-628.5, 14075.6),
    (379.8, 14700.8),
    (1384.7, 15324.0),
    (2394.5, 15950.3),
    (3409.5, 15710.4),
    (4157.9, 14705.9),
    (5089.4, 13698.1),
    (6090.8, 13172.6),
    (7091.1, 13482.8),
    (8093.3, 13148.6),
    (8503.9, 12143.5),
    (7496.9, 11676.0),
    (6494.3, 10739.2),
]
L1_PATH_BEFORE_DOOR: list[tuple[float, float]] = [
    (9196.0, 11484.4),
    (10196.0, 12469.4),
    (11198.7, 13401.8),
    (12201.3, 14284.4),
    (13202.8, 15176.3),
    (14207.0, 16116.2),
    (15208.8, 16871.6),
    (16213.2, 16417.3),
    (16643.4, 15416.6),
    (16994.9, 14410.6),
    (17115.6, 13405.6),
    (16689.2, 12400.4),
]
L1_DOOR_APPROACH_XY = (15953.0, 11902.0)
L1_PATH_BEFORE_DOOR2: list[tuple[float, float]] = [
    (15927.4, 11684.7),
    (16037.8, 10679.9),
    (15761.1, 9679.7),
    (15289.5, 8672.6),
    (14447.3, 7672.0),
    (14526.2, 6664.2),
    (14951.6, 5657.9),
]
L1_DOOR_XY = (15100.0, 5443.0)
L1_PATH_AFTER_DOOR: list[tuple[float, float]] = [
    (15364.9, 4858.7),
    (15689.5, 3857.7),
    (16026.7, 2857.1),
    (17030.7, 2262.6),
    (18035.7, 1888.8),
    (19037.1, 1384.6),
    (19679.2, 1009.5),
    (20181.6, 1203.7),
]
L1_EXIT_XY = (20400.5, 1300.0)

L2_BLESSING_XY = (-14076.0, -19457.0)
L2_PATH_TO_TORCH: list[tuple[float, float]] = [
    (-14977.9, -16480.2),
    (-15985.6, -16838.1),
    (-16985.9, -16929.4),
]
L2_TORCH_CHEST_XY = (-14709.0, -16548.0)
L2_BRAZIERS_1: list[tuple[float, float]] = [
    (-11303.0, -14596.0),
    (-11019.0, -11550.0),
    (-9028.0, -9021.0),
    (-6805.0, -11511.0),
    (-8984.0, -13842.0),
]
L2_CLEANING_PATH: list[tuple[float, float]] = [
    (-7506.89, -12236.26),
    (-7435.12, -10649.25),
    (-9013.61, -9772.06),
    (-10324.58, -10434.43),
    (-10371.20, -12510.16),
    (-8836.63, -11471.01),
]
L2_PATH_ROOM2: list[tuple[float, float]] = [
    (-11013.7, -6381.7),
    (-11081.9, -5378.8),
    (-10071.6, -4396.5),
    (-9069.4, -4301.1),
    (-8066.1, -4222.4),
    (-7058.8, -4191.0),
]
L2_BRAZIERS_2: list[tuple[float, float]] = [
    (-3717.0, -4254.0),
    (-8251.0, -3240.0),
    (-8278.0, -1670.0),
]
L2_PATH_TO_DOOR: list[tuple[float, float]] = [
    (-9069.4, -4301.1),
    (-10071.6, -4396.5),
    (-11106.6, -4747.1),
    (-10970.9, -5754.5),
    (-11033.4, -6755.6),
    (-11318.0, -7767.2),
    (-12320.7, -8417.1),
    (-13324.0, -8649.0),
    (-14326.3, -8773.0),
    (-15331.0, -8905.6),
    (-16335.1, -9004.5),
]
L2_DOOR_XY = (-18725.0, -9171.0)
L2_EXIT_XY = (-19571.61, -8459.0)

L3_BLESSING_XY = (17544.0, 18810.0)
L3_PATH_MAIN: list[tuple[float, float]] = [
    (17544.5, 18530.2),
    (16370.86, 15686.98),
    (16140.35, 18052.51),
    (13998.4, 18866.7),
    (12990.9, 19299.5),
    (11988.8, 19353.2),
    (10986.4, 19188.9),
    (9985.7, 18719.2),
    (9402.1, 17715.6),
    (9076.9, 17383.4),
    (9133.0, 16373.0),
    (8496.5, 15367.3),
    (7978.0, 14357.9),
    (7105.7, 13350.9),
    (6236.1, 12349.0),
    (5524.4, 11344.1),
    (4813.8, 10340.7),
    (4095.0, 9332.7),
    (3091.4, 8424.8),
    (2078.2, 8286.5),
    (1926.0, 5848.0),
    (1069.7, 8045.3),
    (619.8, 7044.0),
    (-385.8, 6478.3),
    (-1123.5, 7481.9),
]
L3_PATH_WEST: list[tuple[float, float]] = [
    (-2964.1, 7302.1),
    (-3139.7, 7022.7),
    (-4152.0, 6469.6),
    (-5154.0, 5969.0),
    (-5837.7, 4968.0),
    (-5832.1, 3954.0),
    (-6838.3, 3495.2),
    (-7845.7, 4397.5),
    (-8049.0, 5403.5),
    (-9049.9, 5289.2),
    (-10051.1, 4604.6),
    (-11057.4, 4039.1),
    (-10381.7, 3037.7),
]
L3_PATH_TO_TORCH: list[tuple[float, float]] = [
    (-4723.00, 6703.00),
    (-1280.00, 7880.00),
    (3089.73, 8511.00),
    (4963.00, 9974.00),
    (9918.64, 19108.00),
    (14709.00, 19526.00),
    (16111.00, 17556.00),
]
L3_TORCH_CHEST_XY = (16111.0, 17556.0)
L3_BRAZIERS: list[tuple[float, float]] = [
    (15692.0, 17111.0),
    (12969.0, 19842.0),
    (8236.0, 16950.0),
    (5549.0, 9920.0),
    (-536.0, 6109.0),
    (-3814.0, 5599.0),
    (-4959.0, 7558.0),
    (-7532.0, 4536.0),
    (-10984.0, 486.0),
    (-12621.0, 2948.0),
]
L3_PRE_BOSS_XY_1 = (-11878.79, 2166.51)
L3_PRE_BOSS_XY_2 = (-9686.32, 2632.0)
L3_BOSS_DOOR_XY = (-9252.32, 6396.40)
L3_PATH_TO_FENDI: list[tuple[float, float]] = [
    (-8871.19, 6152.95),
    (-9326.33, 6862.55),
    (-10044.56, 7921.78),
    (-8408.54, 9475.41),
    (-10049.41, 11259.31),
    (-11381.15, 12387.01),
    (-12304.50, 13319.24),
    (-14736.33, 15054.21),
    (-15000.0, 16850.0),
]

# Everything the leader eats at the dungeon door. Names index the upkeeper
# preset table, so model ids and effect names live in ONE place; the node skips
# whatever is missing from inventory or already running.
LEADER_CONSUMABLES = (
    "armor_of_salvation",
    "essence_of_celerity",
    "grail_of_might",
    "slice_of_pumpkin_pie",
    "birthday_cupcake",
    "golden_egg",
    "candy_corn",
    "candy_apple",
    "drake_kabob",
    "bowl_of_skalefin_soup",
    "pahnai_salad",
    "war_supplies",
)
CONSUMABLE_AFTERCAST_MS = 750

BRAZIER_STATUS_KEY = "brazier_status"
FENDI_STATUS_KEY = "fendi_status"

botting_tree: BottingTree | None = None
initialized = False
ini_key = ""


def now_ms() -> float:
    return time.monotonic() * 1000.0


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def optional(subtree: BehaviorTree, name: str) -> BehaviorTree:
    return team_bt.optional(subtree, name, MODULE_NAME)


def walk(x: float, y: float, tolerance: float = 50.0) -> BehaviorTree:
    return team_bt.walk(x, y, tolerance=tolerance)


def walk_path(points: list[tuple[float, float]], label: str) -> BehaviorTree:
    return team_bt.walk_path(points, label)


def walk_and_exit(x: float, y: float, target_map_id: int, label: str) -> BehaviorTree:
    return team_bt.walk_and_exit(x, y, target_map_id, label)


def when(condition_fn: Callable[[], bool], subtree: BehaviorTree, name: str) -> BehaviorTree:
    """Run the subtree when the condition holds, skip silently when it does not.

    `optional` is the wrong wrapper for a branch that is USUALLY skipped — its
    fallback warns, and a warning that fires every healthy run trains the log
    reader to ignore warnings.
    """

    def skipped(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.SelectorNode(
            name=name,
            children=[
                BehaviorTree.SequenceNode(
                    name=f"{name}: gated",
                    children=[
                        BehaviorTree.ConditionNode(condition_fn, name=f"{name}: check"),
                        optional(subtree, f"{name}: run"),
                    ],
                ),
                BehaviorTree.ActionNode(name=f"{name}: skipped", action_fn=skipped, aftercast_ms=0),
            ],
        )
    )


def blessing(label: str, x: float, y: float) -> BehaviorTree:
    """Walk to the blessing NPC, take the dialog, then order the whole team to.
    Costs gold, buys kill speed — a miss is not worth ending the run over.
    Touch tolerance because the walk target IS the NPC and its collision circle
    keeps the mover outside the default 50."""
    return optional(
        BehaviorTree(
            BehaviorTree.SequenceNode(
                name=label,
                children=[
                    walk(x, y, tolerance=float(Range.Touch.value)),
                    team_bt.team_takes_dialog(label, x, y, DWARVEN_BLESSING_DIALOG, MODULE_NAME),
                ],
            )
        ),
        label,
    )


def open_gadget(label: str, x: float, y: float) -> BehaviorTree:
    """Doors and torch chests: nearest gadget wins, there is nothing to match
    against. A confirm here means the approach worked, no more."""
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[
                walk(x, y),
                optional(
                    gadget_interact.interact_gadget(x, y, key=f"gadget:{label}", radius=BRAZIER_INTERACT_RADIUS),
                    f"{label}: interact",
                ),
                BT.Player.Wait(1_000),
            ],
        )
    )


def drop_bundle() -> BehaviorTree:
    # Twice, like the community bot: the first press can land while the client
    # is still resolving a move and get eaten.
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="DropBundle",
            children=[
                BT.Party.DropBundle(log=False),
                BT.Player.Wait(250),
                BT.Party.DropBundle(log=False),
                BT.Player.Wait(250),
            ],
        )
    )


def find_torch_on_ground() -> tuple[int, int]:
    me = int(Player.GetAgentID())
    items = AgentArray.Filter.ByDistance(AgentArray.GetItemArray(), Player.GetXY(), TORCH_SCAN_RADIUS)
    items = AgentArray.Sort.ByDistance(items, Player.GetXY())
    for agent_id in items or []:
        agent_id = int(agent_id)
        item_agent = Agent.GetItemAgentByID(agent_id)
        if not item_agent:
            continue
        try:
            owner = int(item_agent.owner)
            if owner not in (0, me):
                continue
        except Exception:
            pass
        try:
            item_id = int(Agent.GetItemAgentItemID(agent_id))
        except Exception:
            continue
        try:
            model_id = int(Item.GetModelID(item_id))
        except Exception:
            continue
        if model_id in TORCH_MODEL_IDS:
            return agent_id, item_id
    return 0, 0


def pickup_torch(label: str) -> BehaviorTree:
    """Walk to the nearest torch on the ground and pick it up.

    The confirm is the item agent disappearing — an observed change, not the
    pickup call returning. The alternating agent-id / item-id calls are carried
    over from the community bot, which needed both across client builds.
    """
    state = {"target": 0, "item_id": 0, "last_move_ms": 0.0, "last_try_ms": 0.0, "tries": 0}

    def arm(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        state["target"] = 0
        state["item_id"] = 0
        state["last_move_ms"] = 0.0
        state["last_try_ms"] = 0.0
        state["tries"] = 0
        return BehaviorTree.NodeState.SUCCESS

    def tick(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if state["target"] and not Agent.GetItemAgentByID(state["target"]):
            PySystem.Console.Log(MODULE_NAME, f"{label}: torch picked up.", PySystem.Console.MessageType.Info)
            return BehaviorTree.NodeState.SUCCESS

        if not state["target"]:
            state["target"], state["item_id"] = find_torch_on_ground()
            if not state["target"]:
                return BehaviorTree.NodeState.RUNNING

        tx, ty = Agent.GetXY(state["target"])
        px, py = Player.GetXY()
        now = now_ms()

        if dist(px, py, tx, ty) > TORCH_PICKUP_DIST:
            if now - state["last_move_ms"] >= team_bt.REPOSITION_INTERVAL_MS:
                Player.Move(tx, ty)
                state["last_move_ms"] = now
            return BehaviorTree.NodeState.RUNNING

        if now - state["last_try_ms"] >= TORCH_TRY_INTERVAL_MS:
            state["last_try_ms"] = now
            state["tries"] += 1
            Player.ChangeTarget(state["target"])
            # Native interact IS the game's own pickup — it walks the last gap
            # itself. The inventory-API calls stay as an every-third fallback in
            # case an interact packet whiffs silently.
            if state["tries"] % 3:
                Player.Interact(state["target"], False)
            else:
                inventory = PyInventory.PyInventory()
                if (state["tries"] // 3) % 2:
                    inventory.PickUpItem(state["item_id"], True)
                else:
                    inventory.PickUpItem(state["target"], True)
        return BehaviorTree.NodeState.RUNNING

    def report(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        PySystem.Console.Log(
            MODULE_NAME,
            f"{label}: no torch confirmed in hand (target={state['target']} tries={state['tries']}). "
            "The brazier run will try in the dark.",
            PySystem.Console.MessageType.Warning,
        )
        return BehaviorTree.NodeState.FAILURE

    return optional(
        BehaviorTree(
            BehaviorTree.SequenceNode(
                name=label,
                children=[
                    BehaviorTree.ActionNode(name=f"{label}: arm", action_fn=arm, aftercast_ms=0),
                    BehaviorTree.SelectorNode(
                        name=f"{label}: reach",
                        children=[
                            BehaviorTree.WaitUntilNode(
                                condition_fn=tick,
                                throttle_interval_ms=150,
                                timeout_ms=TORCH_PICKUP_TIMEOUT_MS,
                                name=f"{label}: wait",
                            ),
                            BehaviorTree.ActionNode(name=f"{label}: report", action_fn=report, aftercast_ms=0),
                        ],
                    ),
                ],
            )
        ),
        label,
    )


def torch_buff_active() -> bool:
    return bool(Effects.HasEffect(Player.GetAgentID(), TORCH_BUFF_SKILL_ID))


def brazier_run(points: list[tuple[float, float]], label: str) -> BehaviorTree:
    """Drive the pure brazier sequencer with live movement and interacts.

    Moves are direct and rate-limited, NOT routed through the fight-hold pause
    key: the torch buff is on a timer, and holding the route for every skirmish
    burns it — the relight detour recovers from a fight that could not be
    avoided, and the community route runs these rooms the same way.
    """
    state = brazier_route.RouteState()
    cfg = brazier_route.RouteConfig()
    runtime = {"last_move_ms": 0.0, "light_issued_ms": 0.0, "light_target": 0, "light_count": 0}

    def reset_light() -> None:
        runtime["light_issued_ms"] = 0.0
        runtime["light_target"] = 0
        runtime["light_count"] = 0

    def arm(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        brazier_route.begin(state, points)
        runtime["last_move_ms"] = 0.0
        reset_light()
        return BehaviorTree.NodeState.SUCCESS

    def tick(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        # The finished check must come first: goal() has no answer once the last
        # advance pushed past the end — reading it there crashed a live run.
        if brazier_route.finished(state):
            level = (
                PySystem.Console.MessageType.Warning if state.failed else PySystem.Console.MessageType.Success
            )
            PySystem.Console.Log(MODULE_NAME, f"{label} complete: {brazier_route.summary(state)}.", level)
            return BehaviorTree.NodeState.SUCCESS

        buff = torch_buff_active()
        gx, gy = brazier_route.goal(state)
        px, py = Player.GetXY()
        arrived = dist(px, py, gx, gy) <= BRAZIER_ARRIVE_DIST
        step = brazier_route.next_step(state, arrived=arrived, buff_active=buff)
        node.blackboard[BRAZIER_STATUS_KEY] = f"{label}: {brazier_route.summary(state)}"

        if step is brazier_route.Step.WALK:
            reset_light()
            now = now_ms()
            if now - runtime["last_move_ms"] >= team_bt.REPOSITION_INTERVAL_MS:
                gx, gy = brazier_route.goal(state)
                Player.Move(gx, gy)
                runtime["last_move_ms"] = now
            return BehaviorTree.NodeState.RUNNING

        now = now_ms()
        # First interact: resolve the brazier and light it.
        if runtime["light_count"] == 0:
            agent_id, reason = gadget_choice.pick(
                gadget_interact.candidates_near(gx, gy, BRAZIER_INTERACT_RADIUS), (gx, gy)
            )
            if not agent_id:
                PySystem.Console.Log(MODULE_NAME, f"{label}: {reason}", PySystem.Console.MessageType.Warning)
                brazier_route.report_light(state, cfg, found_gadget=False, buff_active=buff)
                return BehaviorTree.NodeState.RUNNING
            runtime["light_target"] = agent_id
            Player.ChangeTarget(agent_id)
            Player.Interact(agent_id, False)
            runtime["light_issued_ms"] = now
            runtime["light_count"] = 1
            return BehaviorTree.NodeState.RUNNING

        # Follow-up interacts on the SAME brazier, one per beat, to build flame.
        if runtime["light_count"] < BRAZIER_INTERACTS:
            if now - runtime["light_issued_ms"] >= BRAZIER_REINTERACT_MS:
                Player.ChangeTarget(runtime["light_target"])
                Player.Interact(runtime["light_target"], False)
                runtime["light_issued_ms"] = now
                runtime["light_count"] += 1
            return BehaviorTree.NodeState.RUNNING

        # Let the last interact settle before reading the buff to score it.
        if now - runtime["light_issued_ms"] < BRAZIER_LIGHT_SETTLE_MS:
            return BehaviorTree.NodeState.RUNNING

        reset_light()
        brazier_route.report_light(state, cfg, found_gadget=True, buff_active=torch_buff_active())
        return BehaviorTree.NodeState.RUNNING

    def report(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        PySystem.Console.Log(
            MODULE_NAME,
            f"{label} expired at {brazier_route.summary(state)}.",
            PySystem.Console.MessageType.Warning,
        )
        return BehaviorTree.NodeState.FAILURE

    return optional(
        BehaviorTree(
            BehaviorTree.SequenceNode(
                name=label,
                children=[
                    BehaviorTree.ActionNode(name=f"{label}: arm", action_fn=arm, aftercast_ms=0),
                    BehaviorTree.SelectorNode(
                        name=f"{label}: run",
                        children=[
                            BehaviorTree.WaitUntilNode(
                                condition_fn=tick,
                                throttle_interval_ms=100,
                                timeout_ms=BRAZIER_ROUTE_TIMEOUT_MS,
                                name=f"{label}: wait",
                            ),
                            BehaviorTree.ActionNode(name=f"{label}: report", action_fn=report, aftercast_ms=0),
                        ],
                    ),
                ],
            )
        ),
        label,
    )


def lost_souls_missing() -> bool:
    return LOST_SOULS_QUEST_ID not in (Quest.GetQuestLogIds() or [])


def lost_souls_reward_ready() -> bool:
    if lost_souls_missing():
        return False
    return bool(Quest.IsQuestCompleted(LOST_SOULS_QUEST_ID))


def shandra_dialog(kind: str, dialog_id: int, x: float, y: float, radius: float, walk_first: bool = False) -> BehaviorTree:
    """Blessing-shaped when `walk_first` is set: walk into range, scan, talk —
    the shrine flow that works every time, pointed at a quest NPC. Nearby
    tolerance because her spot cannot be stood ON (collision circle, measured
    live as a 15s jitter dance at tolerance 50). The ghost branch skips the
    walk — the party is already standing at the chest when it runs."""
    key = f"npc_target:shandra_{kind}"
    label = f"Shandra {kind}"
    children = [walk(x, y, tolerance=float(Range.Nearby.value))] if walk_first else []
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=children
            + [
                team_bt.target_npc(x, y, key, radius=radius, source=MODULE_NAME, wanted_enc=SHANDRA_ENC),
                team_bt.leader_takes_dialog(key, dialog_id, source=MODULE_NAME),
                team_bt.broadcast_dialog_to_team(key, dialog_id, source=MODULE_NAME),
                optional(
                    BT.Shared.WaitCommandDispatch(
                        command=SharedCommandType.SendDialogToTarget,
                        timeout_ms=team_bt.TEAM_ACK_TIMEOUT_MS,
                        log=True,
                    ),
                    f"{label}: team ack",
                ),
            ],
        )
    )


def handle_shandra_outside() -> BehaviorTree:
    """Reward first when one is waiting, then take the quest fresh — the same
    visit can need both, because collecting the reward removes it from the log."""
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Shandra Quest",
            children=[
                when(
                    lost_souls_reward_ready,
                    shandra_dialog("reward", SHANDRA_REWARD_DIALOG, *SHANDRA_XY, SHANDRA_SCAN_RADIUS, walk_first=True),
                    "Shandra reward due",
                ),
                BT.Player.Wait(2_000),
                when(
                    lost_souls_missing,
                    shandra_dialog("take", SHANDRA_TAKE_DIALOG, *SHANDRA_XY, SHANDRA_SCAN_RADIUS, walk_first=True),
                    "Shandra take due",
                ),
            ],
        )
    )


def report_quest_state() -> BehaviorTree:
    """The dungeon only pays out with Lost Souls active. Report-only: the
    community bot walks back out to fix it, this one tells you instead."""

    def check(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if lost_souls_missing():
            state = "MISSING"
        elif lost_souls_reward_ready():
            state = "COMPLETE (reward not taken)"
        else:
            state = "active"
        level = (
            PySystem.Console.MessageType.Info
            if state == "active"
            else PySystem.Console.MessageType.Warning
        )
        PySystem.Console.Log(MODULE_NAME, f"Lost Souls in dungeon: {state}.", level)
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name="ReportQuestState", action_fn=check, aftercast_ms=0))


def fendi_fight() -> BehaviorTree:
    """Hold the anchor and wait the boss out.

    No Interact on enemies here: headless HeroAI owns the leader's combat and
    the fight zone drives the party — this node only keeps the party parked in
    the room, nudges toward stragglers, and refuses to call the fight won until
    nothing (and no Fendi phase) has shown for the confirm window.
    """
    state = {"clear_since_ms": 0.0, "last_move_ms": 0.0, "last_target_ms": 0.0}

    def arm(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        state["clear_since_ms"] = 0.0
        state["last_move_ms"] = 0.0
        state["last_target_ms"] = 0.0
        return BehaviorTree.NodeState.SUCCESS

    def move_rate_limited(x: float, y: float) -> None:
        now = now_ms()
        if now - state["last_move_ms"] >= team_bt.REPOSITION_INTERVAL_MS:
            Player.Move(x, y)
            state["last_move_ms"] = now

    def tick(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if Map.GetMapID() != SOO_L3_ID:
            PySystem.Console.Log(
                MODULE_NAME, "Fendi fight: not on SoO level 3 - stopping.", PySystem.Console.MessageType.Error
            )
            return BehaviorTree.NodeState.FAILURE

        px, py = Player.GetXY()
        ax, ay = FENDI_ANCHOR_XY

        nearest_id = 0
        nearest_d = float("inf")
        boss_present = False
        compass = float(Range.Compass.value)
        for agent_id in AgentArray.GetEnemyArray() or []:
            agent_id = int(agent_id)
            if not Agent.IsAlive(agent_id):
                continue
            ex, ey = Agent.GetXY(agent_id)
            if dist(ex, ey, ax, ay) > compass:
                continue
            if Agent.GetModelID(agent_id) in FENDI_MODEL_IDS:
                boss_present = True
            d = dist(ex, ey, px, py)
            if d < nearest_d:
                nearest_d = d
                nearest_id = agent_id

        now = now_ms()
        if nearest_id:
            state["clear_since_ms"] = 0.0
            if now - state["last_target_ms"] >= 3_000.0:
                Player.ChangeTarget(nearest_id)
                state["last_target_ms"] = now
            if nearest_d > float(Range.Earshot.value):
                ex, ey = Agent.GetXY(nearest_id)
                move_rate_limited(ex, ey)
            elif dist(px, py, ax, ay) > FENDI_ANCHOR_RADIUS:
                move_rate_limited(ax, ay)
        else:
            if boss_present:
                state["clear_since_ms"] = 0.0
            elif state["clear_since_ms"] == 0.0:
                state["clear_since_ms"] = now
            elif now - state["clear_since_ms"] >= FENDI_CLEAR_CONFIRM_MS:
                PySystem.Console.Log(
                    MODULE_NAME,
                    f"Fendi room clear for {FENDI_CLEAR_CONFIRM_MS / 1000:.0f}s - moving to the chest.",
                    PySystem.Console.MessageType.Success,
                )
                return BehaviorTree.NodeState.SUCCESS
            if dist(px, py, ax, ay) > FENDI_ANCHOR_RADIUS:
                move_rate_limited(ax, ay)

        clear_for = (now - state["clear_since_ms"]) / 1000.0 if state["clear_since_ms"] else 0.0
        node.blackboard[FENDI_STATUS_KEY] = (
            f"boss={'yes' if boss_present else 'no'} enemies={'yes' if nearest_id else 'no'} clear_for={clear_for:.0f}s"
        )
        return BehaviorTree.NodeState.RUNNING

    def report(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        # Deliberately FAILURE, not carry-on: walking eight accounts onto the
        # chest with a live boss is how wipes happen. Expiry here stops the run.
        PySystem.Console.Log(
            MODULE_NAME,
            f"Fendi fight never resolved ({node.blackboard.get(FENDI_STATUS_KEY, 'no reading')}).",
            PySystem.Console.MessageType.Error,
        )
        return BehaviorTree.NodeState.FAILURE

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Fendi Fight",
            children=[
                BehaviorTree.ActionNode(name="Fendi: arm", action_fn=arm, aftercast_ms=0),
                BehaviorTree.SelectorNode(
                    name="Fendi: resolve",
                    children=[
                        BehaviorTree.WaitUntilNode(
                            condition_fn=tick,
                            throttle_interval_ms=FENDI_TICK_MS,
                            timeout_ms=FENDI_TIMEOUT_MS,
                            name="Fendi: wait",
                        ),
                        BehaviorTree.ActionNode(name="Fendi: report", action_fn=report, aftercast_ms=0),
                    ],
                ),
            ],
        )
    )


def use_leader_consumables() -> BehaviorTree:
    presets = BT.Upkeepers.CONSUMABLE_UPKEEP_PRESETS
    items = [(presets[name]["model_id"], presets[name]["effect_name"]) for name in LEADER_CONSUMABLES]
    return optional(
        BT.Items.UseConsumables(items, aftercast_ms=CONSUMABLE_AFTERCAST_MS),
        "Leader consumables",
    )


def initialize() -> BehaviorTree:
    tree = ensure_botting_tree()
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Initialize",
            children=[
                tree.Config.Multibox_Aggressive(auto_loot=True, resurrection_scroll=True),
                BT.Map.SetHardMode(True),
            ],
        )
    )


def prepare_outpost() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Prepare Outpost",
            children=[
                BT.Map.TravelToOutpost(outpost_id=VLOXS_FALLS_ID, log=True, timeout=60_000),
                optional(BT.Party.WaitForPartyLoaded(timeout_ms=30_000), "Wait for party"),
                BT.Player.Wait(2_000),
            ],
        )
    )


def seq(name: str, *children) -> BehaviorTree:
    return BehaviorTree(BehaviorTree.SequenceNode(name=name, children=list(children)))


def leave_vloxs_falls() -> BehaviorTree:
    return seq(
        "Leave Vlox's Falls",
        walk_and_exit(*ARBOR_EXIT_XY, ARBOR_BAY_ID, "Leave Vlox's Falls"),
        BT.Player.Wait(2_000),
    )


def arbor_blessing() -> BehaviorTree:
    return blessing("Arbor Bay blessing", *ARBOR_BLESSING_XY)


def arbor_route() -> BehaviorTree:
    return walk_path(ARBOR_TO_SHANDRA_PATH, "Arbor Bay route")


def walk_to_shandra() -> BehaviorTree:
    return walk(*SHANDRA_XY, tolerance=float(Range.Nearby.value))


def shandra_quest() -> BehaviorTree:
    return optional(handle_shandra_outside(), "Shandra quest")


def enter_dungeon() -> BehaviorTree:
    return seq(
        "Enter Dungeon",
        walk_path(DUNGEON_ENTRY_PATH, "Dungeon approach"),
        walk_and_exit(*DUNGEON_GATE_XY, SOO_L1_ID, "Enter Shards of Orr"),
        BT.Player.Wait(2_000),
        report_quest_state(),
    )


def l1_blessing() -> BehaviorTree:
    return seq(
        "L1 Blessing",
        blessing("L1 blessing", *L1_BLESSING_XY),
        use_leader_consumables(),
    )


def l1_route_1() -> BehaviorTree:
    return walk_path(L1_PATH_BRIDGANT, "L1 route 1")


def l1_route_2() -> BehaviorTree:
    return seq(
        "L1 Route 2",
        walk_path(L1_PATH_BEFORE_DOOR, "L1 route 2"),
        walk(*L1_DOOR_APPROACH_XY),
    )


def l1_route_3() -> BehaviorTree:
    return walk_path(L1_PATH_BEFORE_DOOR2, "L1 route 3")


def l1_door() -> BehaviorTree:
    return open_gadget("L1 door", *L1_DOOR_XY)


def enter_level_2() -> BehaviorTree:
    return seq(
        "Enter Level 2",
        walk_path(L1_PATH_AFTER_DOOR, "L1 route 4"),
        walk_and_exit(*L1_EXIT_XY, SOO_L2_ID, "Enter level 2"),
        BT.Player.Wait(2_000),
    )


def l2_blessing() -> BehaviorTree:
    return seq(
        "L2 Blessing",
        blessing("L2 blessing", *L2_BLESSING_XY),
        use_leader_consumables(),
    )


def l2_first_torch() -> BehaviorTree:
    return seq(
        "L2 First Torch",
        walk_path(L2_PATH_TO_TORCH, "L2 route to torch"),
        open_gadget("L2 torch chest", *L2_TORCH_CHEST_XY),
        pickup_torch("L2 torch 1"),
    )


def l2_braziers_1() -> BehaviorTree:
    return seq(
        "L2 Braziers 1",
        walk(-11002.0, -17001.0),
        drop_bundle(),
        walk(-9259.0, -17322.0),
        walk(-9971.23, -17633.08),
        walk(-11136.85, -17201.66),
        pickup_torch("L2 torch 2"),
        walk(-11030.3, -17474.0),
        walk(-11303.0, -14596.0),
        brazier_run(L2_BRAZIERS_1, "L2 braziers 1"),
    )


def l2_room_sweep() -> BehaviorTree:
    return seq(
        "L2 Room Sweep",
        drop_bundle(),
        walk_path(L2_CLEANING_PATH, "L2 room sweep"),
        pickup_torch("L2 torch 3"),
    )


def l2_move_up() -> BehaviorTree:
    return seq(
        "L2 Move Up",
        walk(-11061.1, -7578.5),
        drop_bundle(),
        walk(-10958.2, -4529.5),
        walk(-11690.64, -3802.55),
        walk(-10958.2, -4529.5),
        walk(-11032.11, -5389.71),
        walk(-11090.10, -6890.14),
        pickup_torch("L2 torch 4"),
    )


def l2_braziers_2() -> BehaviorTree:
    return seq(
        "L2 Braziers 2",
        walk_path(L2_PATH_ROOM2, "L2 room 2"),
        drop_bundle(),
        walk(-4245.2, -2101.0),
        pickup_torch("L2 torch 5"),
        brazier_run(L2_BRAZIERS_2, "L2 braziers 2"),
        drop_bundle(),
        walk(-6798.8, -2436.4),
    )


def enter_level_3() -> BehaviorTree:
    return seq(
        "Enter Level 3",
        walk_path(L2_PATH_TO_DOOR, "L2 route to door"),
        open_gadget("L2 door", *L2_DOOR_XY),
        walk(-18610.0, -8636.0),
        walk_and_exit(*L2_EXIT_XY, SOO_L3_ID, "Enter level 3"),
        BT.Player.Wait(2_000),
    )


def l3_blessing() -> BehaviorTree:
    return seq(
        "L3 Blessing",
        blessing("L3 blessing", *L3_BLESSING_XY),
        use_leader_consumables(),
    )


def l3_main_route() -> BehaviorTree:
    return walk_path(L3_PATH_MAIN, "L3 main route")


def l3_west_route() -> BehaviorTree:
    return walk_path(L3_PATH_WEST, "L3 west route")


def l3_torch() -> BehaviorTree:
    return seq(
        "L3 Torch",
        walk_path(L3_PATH_TO_TORCH, "L3 route to torch"),
        open_gadget("L3 torch chest", *L3_TORCH_CHEST_XY),
        pickup_torch("L3 torch"),
    )


def l3_braziers() -> BehaviorTree:
    return seq(
        "L3 Braziers",
        brazier_run(L3_BRAZIERS, "L3 braziers"),
        drop_bundle(),
    )


def l3_boss_door() -> BehaviorTree:
    return seq(
        "L3 Boss Door",
        walk(*L3_PRE_BOSS_XY_1),
        walk(*L3_PRE_BOSS_XY_2),
        open_gadget("L3 boss door", *L3_BOSS_DOOR_XY),
    )


def l3_route_to_fendi() -> BehaviorTree:
    return walk_path(L3_PATH_TO_FENDI, "L3 route to Fendi")


def claim_fendi_chest() -> BehaviorTree:
    chest = "Fendi"
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Claim Fendi Chest",
            children=[
                BT.Player.Wait(5_000),
                # No walk to the chest: targeting scans around the chest's own
                # coordinates and the interact is what walks the leader in —
                # native interact IS the approach. A separate Move just paths
                # the leader through the room a second time for nothing.
                # The leader takes the FIRST turn and finishes it — opens, then
                # loots — before any follower is ordered in. Nothing below may
                # run while the leader still has the chest window up.
                optional(
                    BehaviorTree(
                        BehaviorTree.SequenceNode(
                            name="Leader opens Fendi chest",
                            children=[
                                gadget_interact.interact_gadget(
                                    FENDI_CHEST_XY[0],
                                    FENDI_CHEST_XY[1],
                                    key=team_bt.chest_target_key(chest),
                                    radius=CHEST_SCAN_RADIUS,
                                    wanted_ids=(FENDI_CHEST_GADGET_ID,),
                                ),
                                BT.Player.Wait(3_000),
                                BT.Items.LootItems(distance=float(Range.Spellcast.value), timeout_ms=20_000),
                            ],
                        )
                    ),
                    "Leader rewards (Fendi)",
                ),
                # Then the followers, strictly one at a time. Each turn ends on
                # that account going quiet, not on a timer.
                optional(team_bt.team_opens_chest_in_turn(chest, MODULE_NAME), "Team chest turns (Fendi)"),
                # Looting is not a mutex, so the floor sweep goes to everyone.
                BT.Shared.SendCommand(command=SharedCommandType.PickUpLoot, log=True),
                optional(
                    BT.Shared.WaitCommandDispatch(
                        command=SharedCommandType.PickUpLoot,
                        timeout_ms=team_bt.TEAM_ACK_TIMEOUT_MS,
                        log=True,
                    ),
                    "Team loot (Fendi)",
                ),
            ],
        )
    )


def dungeon_reward() -> BehaviorTree:
    # Lost Souls completes with Fendi; Shandra's ghost spawns by the chest. No
    # approach walk — the party is already standing there, and the interact
    # itself closes whatever gap is left.
    return optional(
        shandra_dialog("dungeon reward", SHANDRA_REWARD_DIALOG, *FENDI_CHEST_XY, SHANDRA_GHOST_SCAN_RADIUS),
        "Shandra dungeon reward",
    )


def reset_run() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Reset Run",
            children=[
                # The dungeon-complete teleport back to Arbor Bay. Expiry falls
                # through to the resign, which recovers from either map.
                optional(
                    BT.Map.WaitforMapLoad(map_id=ARBOR_BAY_ID, log=True, timeout=DUNGEON_RETURN_TIMEOUT_MS),
                    "Wait for dungeon return",
                ),
                BT.Player.Wait(5_000),
                BT.Shared.ResignAllAccounts(log=True),
                BT.Party.Resign(log=True),
                # From here the wipe-restart service owns the run: outpost loads,
                # planner restarts from Initialize. Nothing after the resign may
                # wait on map loads itself — that race against the restart is
                # exactly what stuck the first looped run.
                team_bt.hold_for_restart("Reset Run: waiting for restart"),
            ],
        )
    )


def get_execution_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    """One step per resume point. Every step opens with a walk to an absolute
    position (or is position-independent), so restarting the planner AT any
    step works from wherever the leader is standing — these names are the jump
    targets used to pick a run back up after a stop."""
    return [
        ("Initialize", initialize),
        ("Prepare Outpost", prepare_outpost),
        ("Leave Vlox's Falls", leave_vloxs_falls),
        ("Arbor Bay Blessing", arbor_blessing),
        ("Arbor Route", arbor_route),
        ("Walk to Shandra", walk_to_shandra),
        ("Shandra Quest", shandra_quest),
        ("Enter Dungeon", enter_dungeon),
        ("L1 Blessing", l1_blessing),
        ("L1 Route 1", l1_route_1),
        ("L1 Route 2", l1_route_2),
        ("L1 Route 3", l1_route_3),
        ("L1 Door", l1_door),
        ("Enter Level 2", enter_level_2),
        ("L2 Blessing", l2_blessing),
        ("L2 First Torch", l2_first_torch),
        ("L2 Braziers 1", l2_braziers_1),
        ("L2 Room Sweep", l2_room_sweep),
        ("L2 Move Up", l2_move_up),
        ("L2 Braziers 2", l2_braziers_2),
        ("Enter Level 3", enter_level_3),
        ("L3 Blessing", l3_blessing),
        ("L3 Main Route", l3_main_route),
        ("L3 West Route", l3_west_route),
        ("L3 Torch", l3_torch),
        ("L3 Braziers", l3_braziers),
        ("L3 Boss Door", l3_boss_door),
        ("L3 Route to Fendi", l3_route_to_fendi),
        ("Fendi Fight", fendi_fight),
        ("Fendi Chest", claim_fendi_chest),
        ("Dungeon Reward", dungeon_reward),
        ("Reset Run", reset_run),
    ]


def ensure_botting_tree() -> BottingTree:
    global botting_tree
    if botting_tree is None:
        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name="SoOSequence",
            repeat=True,
            reset=False,
            auto_start=False,
            multi_account=True,
            isolation_enabled=False,
        )
        # Never add ConfigureUpkeep above this line: it calls SetUpkeepTrees,
        # which REPLACES the service list rather than appending to it, and the
        # gate would vanish silently.
        botting_tree.AddServiceTree("FightGate", team_bt.fight_gate)
        botting_tree.AddServiceTree(
            "WipeRestart", lambda: team_bt.wipe_restart_service("Initialize", MODULE_NAME)
        )
    return botting_tree


def draw_run_tab() -> None:
    tree = botting_tree
    if tree is None:
        PyImGui.text("Not initialized.")
        return
    blackboard = tree.tree.blackboard
    PyImGui.text(str(blackboard.get(BRAZIER_STATUS_KEY, "Braziers: not started")))
    PyImGui.text(str(blackboard.get(FENDI_STATUS_KEY, "Fendi: not started")))
    PyImGui.text(str(blackboard.get(team_bt.CHEST_TURNS_KEY, "Chest turns: not started")))


def main() -> None:
    global initialized, ini_key

    if not initialized:
        if not ini_key:
            ini_key = Settings(f"{INI_PATH}/{INI_FILENAME}", "account").name
            if not ini_key:
                return
        ensure_botting_tree()
        initialized = True

    tree = ensure_botting_tree()
    tree.tick()
    texture = os.path.join(
        PySystem.Console.get_projects_path(),
        "Scripts",
        "py4gw-marks-corner",
        "scripts",
        "textures",
        "soo.png",
    )
    tree.UI.draw_window(icon_path=texture, extra_tabs=[("Fight", team_bt.draw_fight_tab), ("Run", draw_run_tab)])


if __name__ == "__main__":
    main()
