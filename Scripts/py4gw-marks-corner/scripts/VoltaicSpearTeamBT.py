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
from dataclasses import dataclass
from typing import Callable

import PySystem

from Core import Range
from Core import SharedCommandType
from Core.BottingTree import BottingTree
from Core.enums_src.Model_enums import GadgetModelID
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Settings import Settings
from Core.routines_src.BehaviourTrees import BT
from Sources.marks_sources import gadget_interact
from Sources.marks_sources import team_bt

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

# Wider than the 200 this used before. Once the gadget id is doing the matching a
# decoy cannot win the scan, so the only thing a tight radius still buys is a
# missed chest when the walk lands short.
CHEST_SCAN_RADIUS = 400.0

botting_tree: BottingTree | None = None
initialized = False
ini_key = ""


def optional(subtree: BehaviorTree, name: str) -> BehaviorTree:
    return team_bt.optional(subtree, name, MODULE_NAME)


def walk(x: float, y: float) -> BehaviorTree:
    return team_bt.walk(x, y)


def walk_path(points: list[tuple[float, float]], label: str) -> BehaviorTree:
    return team_bt.walk_path(points, label)


def walk_and_exit(x: float, y: float, target_map_id: int, label: str) -> BehaviorTree:
    return team_bt.walk_and_exit(x, y, target_map_id, label)


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
                                    key=team_bt.chest_target_key(chest),
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
                optional(team_bt.team_opens_chest_in_turn(chest, MODULE_NAME), f"Team chest turns ({chest})"),
                # Looting is not a mutex, so the sweep for anything left on the
                # floor can go out to everyone at once.
                BT.Shared.SendCommand(command=SharedCommandType.PickUpLoot, log=True),
                optional(
                    BT.Shared.WaitCommandDispatch(
                        command=SharedCommandType.PickUpLoot,
                        timeout_ms=team_bt.TEAM_ACK_TIMEOUT_MS,
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
                # From here the wipe-restart service owns the run: outpost
                # loads, planner restarts from Initialize. Waiting on the map
                # load here races the restart — measured live in SoO as the bot
                # standing in the outpost restarting its own reset step.
                team_bt.hold_for_restart("Reset Run: waiting for restart"),
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
        botting_tree.AddServiceTree("FightGate", team_bt.fight_gate)
        botting_tree.AddServiceTree(
            "WipeRestart", lambda: team_bt.wipe_restart_service("Initialize", MODULE_NAME)
        )
    return botting_tree


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
    tree.UI.draw_window(icon_path=texture, extra_tabs=[("Fight", team_bt.draw_fight_tab)])


if __name__ == "__main__":
    main()
