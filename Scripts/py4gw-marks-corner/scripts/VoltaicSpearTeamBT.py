"""Voltaic Spear team farm — Justiciar Thommis, driven from the party leader.

The FSM version of this bot flagged the party by hand through CombatPrep. This
one hands the fight to HeroAI's fight zone and its only job around combat is to
stay out of the way — which is harder than it sounds, because the zone places
every party member EXCEPT the leader. The leader's route is therefore the one
thing that can still walk the party into a mob it is busy backing away from.
`fight_gate` is what stops that: it holds the route while a zone is driving, and
walks the leader back onto the anchor when the formation gives ground.
"""

# NO `from __future__ import annotations` here. It makes every annotation a
# string, which sends @dataclass through `sys.modules[cls.__module__].__dict__`
# to resolve them — and the native side runs a script by exec'ing its source
# under a name that is not a real module, so that lookup returns None and the
# script dies at load. Every working dataclass script in this tree omits it.
__script__ = {
    "name": "Voltaic Spear Team BT",
    "function": "farmer",
    "tags": ["voltaic", "dungeon", "eotn", "slavers", "multibox", "heroai"],
    "claims": ["character", "inventory", "sharedmem"],
}

import os
import time
from dataclasses import dataclass
from typing import Callable

import PyImGui
import PySystem

from Core import GLOBAL_CACHE
from Core import Player
from Core import Range
from Core import SharedCommandType
from Core.BottingTree import BottingTree
from Core.enums_src.Model_enums import GadgetModelID
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Settings import Settings
from Core.routines_src.BehaviourTrees import BT
from Sources.marks_sources import fight_awareness
from Sources.marks_sources import gadget_interact
from Sources.marks_sources import team_turns

MODULE_NAME = "Voltaic Spear Team BT"
INI_PATH = "Widgets/Automation/Bots/Voltaic Spear Team BT"
INI_FILENAME = "Voltaic_Spear_Team_BT.ini"

UMBRAL_GROTTO_ID = 639
VERDANT_CASCADES_ID = 566
SLAVERS_EXILE_ID = 577
JUSTICIAR_THOMMIS_ROOM_ID = 620

# The route is carried over verbatim from the FSM bot, which ran it for months.
# Treat the numbers as measured, not derived.
UMBRAL_GROTTO_EXIT = (-22735.0, 6339.0)
VERDANT_CASCADES_EXIT = (25729.0, -9360.0)
SLAVERS_EXILE_EXIT = (-18300.0, 12527.0)

VERDANT_CASCADES_PATH: list[tuple[float, float]] = [
    (-19887.0, 6074.0),
    (-10273.0, 3251.0),
    (-6878.0, -329.0),
    (-3041.0, -3446.0),
    (3571.0, -9501.0),
    (4721.0, -10626.0),
    (10764.0, -6448.0),
    (13063.0, -4396.0),
    (18054.0, -3275.0),
    (20966.0, -6476.0),
    (25298.0, -9456.0),
]

DUNGEON_ENTRANCE_PATH: list[tuple[float, float]] = [
    (-16797.0, 9251.0),
    (-17835.0, 12524.0),
]

THOMMIS_PRE_PATH_1 = (-12590.0, -17740.0)
THOMMIS_PATH_1: list[tuple[float, float]] = [
    (-13480.0, -16570.0),
    (-13500.0, -15750.0),
    (-12500.0, -15000.0),
    (-10400.0, -14800.0),
    (-10837.0, -13823.0),
    (-11500.0, -13300.0),
    (-12175.0, -12211.0),
    (-13400.0, -11500.0),
    (-13700.0, -9550.0),
    (-14100.0, -8600.0),
    (-15000.0, -7500.0),
    (-16000.0, -7112.0),
    (-17347.0, -7438.0),
]

THOMMIS_PRE_PATH_2 = (-18781.0, -8064.0)
THOMMIS_PATH_2: list[tuple[float, float]] = [
    (-19083.0, -10150.0),
    (-18500.0, -11500.0),
    (-17700.0, -12500.0),
    (-17663.0, -13497.0),
]

REWARD_CHEST_XY = (-17461.0, -14258.0)


@dataclass(frozen=True)
class ChestRoute:
    """A clear that ends at one chest. The room holds more than one, and each is
    its own mutex, so they are separate routes rather than one longer path."""

    name: str
    path: tuple[tuple[float, float], ...]
    chest: tuple[float, float]
    # Coordinates alone pick the nearest gadget of ANY kind, so a door or a
    # signpost standing closer to the scan point wins and gets interacted with
    # perfectly. The id is what makes the target the chest.
    chest_id: int


ROUTES: tuple[ChestRoute, ...] = (
    ChestRoute(
        name="Thommis",
        path=(THOMMIS_PRE_PATH_1, *THOMMIS_PATH_1, THOMMIS_PRE_PATH_2, *THOMMIS_PATH_2),
        chest=REWARD_CHEST_XY,
        chest_id=GadgetModelID.CHEST_DUNGEON_SLAVERS_EXILE_JUSTICIAR_THOMMIS_ROOM.value,
    ),
    # Second chest: add a ChestRoute here once its path is mapped. Steps are
    # generated per route and the chest turn-taker is keyed by route name, so
    # nothing below this line needs editing.
)

# Movement nodes read ONE pause key, and the HeroAI branch owns PAUSE_MOVEMENT.
FIGHT_HOLD_KEY = "FIGHT_HOLD"
# Player.Move goes through the ACTION queue the party's skills are also using.
REPOSITION_INTERVAL_MS = 1500.0
CHEST_TURNS_KEY = "chest_turns"
# Wider than the 200 this used before. Once the gadget id is doing the matching a
# decoy cannot win the scan, so the only thing a tight radius still buys is a
# missed chest when the walk lands short.
CHEST_SCAN_RADIUS = 400.0
# Generous on purpose: expiry here means "could not confirm", and every wait that
# uses it is wrapped so it cannot decide whether the step counted.
TEAM_ACK_TIMEOUT_MS = 90_000

TURN_CFG = team_turns.TurnConfig()

botting_tree: BottingTree | None = None
initialized = False
ini_key = ""


def fight_gate() -> BehaviorTree:
    """Hold the route while the party is fighting; follow it back when it withdraws.

    Writes its own pause key rather than PAUSE_MOVEMENT because services tick
    AFTER the planner, and the HeroAI branch rewrites PAUSE_MOVEMENT at the top of
    every tick — before the planner reads it. A service write to that key is
    clobbered before anything can act on it, so FIGHT_HOLD carries it forward
    instead, one frame behind.
    """
    state = {"last_move_ms": 0.0}

    def tick_gate(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        snapshot = fight_awareness.read()
        stance = fight_awareness.stance(snapshot)
        blackboard = node.blackboard
        blackboard["FIGHT_STANCE"] = stance.name
        blackboard["FIGHT_REASON"] = fight_awareness.describe(snapshot)
        blackboard[FIGHT_HOLD_KEY] = stance is not fight_awareness.Stance.CLEAR or bool(
            blackboard.get("PAUSE_MOVEMENT", False)
        )

        target = fight_awareness.reposition_target(snapshot, Player.GetXY())
        if target is None:
            return BehaviorTree.NodeState.RUNNING

        # Re-issuing a move to the same point is idempotent, so this wants rate
        # limiting rather than a latch on an observed change.
        now = time.monotonic() * 1000.0
        if now - float(state["last_move_ms"]) < REPOSITION_INTERVAL_MS:
            return BehaviorTree.NodeState.RUNNING
        Player.Move(target[0], target[1])
        state["last_move_ms"] = now
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name="FightGate", action_fn=tick_gate, aftercast_ms=0))


def optional(subtree: BehaviorTree, name: str) -> BehaviorTree:
    """Let a step expire or miss without dropping the rung.

    A FAILURE anywhere in the planner Sequence stops the whole bot — the planner
    sets `started = False` on it. That is the right answer for a wrong map and
    the wrong one for a wait that merely could not confirm what it was watching
    for. The Selector still pins on RUNNING, so this shortens nothing.

    The fallback LOGS. A wait that quietly gave up is the hardest kind of run to
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
                BehaviorTree.ActionNode(
                    name=f"{name}: continue anyway",
                    action_fn=carry_on,
                    aftercast_ms=0,
                ),
            ],
        )
    )


def walk(x: float, y: float) -> BehaviorTree:
    return BT.Movement.Move(x=x, y=y, pause_flag_key=FIGHT_HOLD_KEY)


def walk_path(points: list[tuple[float, float]], label: str) -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[walk(x, y) for x, y in points],
        )
    )


def walk_and_exit(x: float, y: float, target_map_id: int, label: str) -> BehaviorTree:
    """Composed here rather than using MoveAndExitMap, which offers no pause key."""
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=label,
            children=[
                walk(x, y),
                BT.Map.WaitforMapLoad(map_id=target_map_id, log=True, timeout=60_000),
            ],
        )
    )


def chest_target_key(chest: str) -> str:
    return f"chest_target_id:{chest}"


def chest_started_key(chest: str) -> str:
    return f"chest_turns_started:{chest}"


def follower_emails(sender_email: str) -> list[str]:
    return [
        email
        for email in (
            str(getattr(account, "AccountEmail", "") or "") for account in GLOBAL_CACHE.ShMem.GetAllAccountData() or []
        )
        if email and email != sender_email
    ]


def account_is_busy(email: str) -> bool:
    """Any active message for that account — the order itself, and the loot pass
    HeroAI queues behind it. Waiting on the interact alone releases the chest
    while the account is still picking up."""
    index, message = GLOBAL_CACHE.ShMem.PreviewNextMessage(email)
    return index != -1 and message is not None


def team_opens_chest_in_turn(chest: str) -> BehaviorTree:
    """One account at a time. The chest is a mutex — a second account interacting
    while the first still has it open gets nothing, and reports success anyway.

    Keyed by chest name so a run with more than one of them cannot have the
    second step read the first one's target or its already-started flag.

    The receiver already snapshots and disables HeroAI options around its walk
    and interact, then restores them — see Widgets/Panels/Messaging.py. Do not
    add a combat toggle here; it would fight that snapshot.
    """
    state = team_turns.TurnState()

    def tick_turns(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target = int(node.blackboard.get(chest_target_key(chest), 0) or 0)
        sender_email = str(Player.GetAccountEmail() or "")
        if not target or not sender_email:
            return BehaviorTree.NodeState.FAILURE

        started_key = chest_started_key(chest)
        if not node.blackboard.get(started_key):
            team_turns.begin(state, follower_emails(sender_email))
            node.blackboard[started_key] = True

        busy = account_is_busy(state.current) if state.current else False
        verdict = team_turns.next_turn(state, TURN_CFG, int(time.monotonic() * 1000.0), busy)
        node.blackboard[CHEST_TURNS_KEY] = f"{chest}: {team_turns.summary(state)}"

        if verdict is team_turns.Turn.START:
            GLOBAL_CACHE.ShMem.SendMessage(
                sender_email,
                state.current,
                SharedCommandType.InteractWithTarget,
                (float(target), 0.0, 0.0, 0.0),
            )
            PySystem.Console.Log(
                MODULE_NAME,
                f"{chest} turn: {state.current} ({team_turns.remaining(state)} left).",
                PySystem.Console.MessageType.Info,
            )
            return BehaviorTree.NodeState.RUNNING

        if verdict is team_turns.Turn.FINISHED:
            node.blackboard[started_key] = False
            PySystem.Console.Log(
                MODULE_NAME,
                f"{chest} turns complete: {team_turns.summary(state)}.",
                PySystem.Console.MessageType.Success,
            )
            return BehaviorTree.NodeState.SUCCESS

        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.ActionNode(name=f"TeamOpensChestInTurn({chest})", action_fn=tick_turns, aftercast_ms=0)
    )


def initialize() -> BehaviorTree:
    tree = ensure_botting_tree()
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Initialize",
            children=[
                tree.Config.Multibox_Aggressive(auto_loot=True, resurrection_scroll=False),
                BT.Map.SetHardMode(True),
            ],
        )
    )


def prepare_outpost() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Prepare Outpost",
            children=[
                BT.Map.TravelToOutpost(outpost_id=UMBRAL_GROTTO_ID, log=True, timeout=60_000),
                optional(BT.Party.WaitForPartyLoaded(timeout_ms=30_000), "Wait for party"),
                BT.Player.Wait(2_000),
            ],
        )
    )


def cross_verdant_cascades() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Cross Verdant Cascades",
            children=[
                walk_and_exit(*UMBRAL_GROTTO_EXIT, VERDANT_CASCADES_ID, "Leave Umbral Grotto"),
                BT.Player.Wait(2_000),
                walk_path(VERDANT_CASCADES_PATH, "Verdant Cascades route"),
                walk_and_exit(*VERDANT_CASCADES_EXIT, SLAVERS_EXILE_ID, "Enter Slavers Exile"),
            ],
        )
    )


def enter_thommis_room() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Enter Thommis Room",
            children=[
                BT.Player.Wait(2_000),
                walk_path(DUNGEON_ENTRANCE_PATH, "Dungeon entrance route"),
                walk_and_exit(*SLAVERS_EXILE_EXIT, JUSTICIAR_THOMMIS_ROOM_ID, "Enter Thommis room"),
                BT.Player.Wait(3_000),
            ],
        )
    )


def claim_chest(route: ChestRoute) -> BehaviorTree:
    """Takes the whole route so a room with more than one chest is a second entry
    in ROUTES rather than a second copy of this."""
    chest = route.name
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"Claim Chest ({chest})",
            children=[
                BT.Player.Wait(5_000),
                walk(*route.chest),
                # The leader takes the FIRST turn and finishes it — opens, then
                # loots — before any follower is ordered in. Nothing below may
                # run while the leader still has the chest window up.
                # A missed chest costs one run's loot. Failing here would cost
                # every run after it, because the planner stops on FAILURE.
                optional(
                    BehaviorTree(
                        BehaviorTree.SequenceNode(
                            name=f"Leader opens {chest}",
                            children=[
                                gadget_interact.interact_gadget(
                                    route.chest[0],
                                    route.chest[1],
                                    key=chest_target_key(chest),
                                    radius=CHEST_SCAN_RADIUS,
                                    wanted_ids=(route.chest_id,),
                                ),
                                BT.Player.Wait(3_000),
                                BT.Items.LootItems(distance=float(Range.Spellcast.value), timeout_ms=20_000),
                            ],
                        )
                    ),
                    f"Leader rewards ({chest})",
                ),
                # Then the followers, strictly one at a time. Each turn ends on
                # that account going quiet, not on a timer — how long it takes is
                # geometry, since it walks in from wherever the formation left it.
                optional(team_opens_chest_in_turn(chest), f"Team chest turns ({chest})"),
                # Looting is not a mutex, so the sweep for anything left on the
                # floor can go out to everyone at once.
                BT.Shared.SendCommand(command=SharedCommandType.PickUpLoot, log=True),
                optional(
                    BT.Shared.WaitCommandDispatch(
                        command=SharedCommandType.PickUpLoot,
                        timeout_ms=TEAM_ACK_TIMEOUT_MS,
                        log=True,
                    ),
                    f"Team loot ({chest})",
                ),
            ],
        )
    )


def clear_route(route: ChestRoute) -> BehaviorTree:
    return walk_path(list(route.path), f"Clear {route.name}")


def reset_run() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Reset Run",
            children=[
                BT.Shared.ResignAllAccounts(log=True),
                BT.Party.Resign(log=True),
                BT.Map.WaitforMapLoad(map_id=UMBRAL_GROTTO_ID, log=True, timeout=90_000),
                optional(BT.Party.WaitForPartyLoaded(timeout_ms=30_000), "Wait for party"),
            ],
        )
    )


def get_execution_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    """Clear-then-chest per route, then one resign for the whole run. Step names
    carry the route name because they are also the jump targets wipe recovery
    restarts from — two steps called "Claim Chest" would send it to the wrong one.
    """
    steps: list[tuple[str, Callable[[], BehaviorTree]]] = [
        ("Initialize", initialize),
        ("Prepare Outpost", prepare_outpost),
        ("Cross Verdant Cascades", cross_verdant_cascades),
        ("Enter Thommis Room", enter_thommis_room),
    ]
    for route in ROUTES:
        steps.append((f"Clear {route.name}", lambda route=route: clear_route(route)))
        steps.append((f"Chest {route.name}", lambda route=route: claim_chest(route)))
    steps.append(("Reset Run", reset_run))
    return steps


def ensure_botting_tree() -> BottingTree:
    global botting_tree
    if botting_tree is None:
        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name="VoltaicSpearSequence",
            repeat=True,
            reset=False,
            auto_start=False,
            multi_account=True,
            isolation_enabled=False,
        )
        # Never add ConfigureUpkeep above this line: it calls SetUpkeepTrees,
        # which REPLACES the service list rather than appending to it, and the
        # gate would vanish silently.
        botting_tree.AddServiceTree("FightGate", fight_gate)
    return botting_tree


def draw_fight_tab() -> None:
    snapshot = fight_awareness.read()
    stance = fight_awareness.stance(snapshot)
    PyImGui.text(f"Stance: {stance.name}")
    PyImGui.text(fight_awareness.describe(snapshot))
    PyImGui.separator()

    if snapshot is None:
        PyImGui.text("No fight zone publishing. HeroAI leader-side only.")
        return

    PyImGui.progress_bar(fight_awareness.party_health(snapshot), -1.0, f"{fight_awareness.party_health(snapshot):.0%}")
    PyImGui.text(f"Zone state: {snapshot.get('state', '?')}")
    PyImGui.text(f"Alive {snapshot.get('party_alive', '?')} / dead {snapshot.get('party_dead', '?')}")
    PyImGui.text(f"Health steps: {snapshot.get('health_steps_used', 0)}/{snapshot.get('health_max_steps', 0)}")
    PyImGui.text(f"Given ground: {float(snapshot.get('given_ground', 0.0)):.0f}u")
    anchor = fight_awareness.anchor(snapshot)
    if anchor is not None:
        PyImGui.text(f"Anchor: {anchor[0]:.0f}, {anchor[1]:.0f}")

    PyImGui.separator()
    # The escape route is a HARD GATE on withdrawing, not one input among
    # several: no route, or one shorter than the give-ground margin, and the
    # party cannot back up at all however bad its health gets. This is the line
    # to read first when a retreat does not happen.
    for line in fight_awareness.retreat_blockers(snapshot):
        PyImGui.text(line)

    if stance is fight_awareness.Stance.CLEAR:
        return
    PyImGui.separator()
    PyImGui.text("Route is HELD - the leader is not advancing.")


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
        "voltaic_spear.png",
    )
    tree.UI.draw_window(icon_path=texture, extra_tabs=[("Fight", draw_fight_tab)])


if __name__ == "__main__":
    main()
