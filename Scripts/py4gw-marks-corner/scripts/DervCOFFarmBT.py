"""COF Farmer — BottingTree edition."""

from __future__ import annotations

__script__ = {
    "name": "COF Farmer BT",
    "function": "farmer",
    "tags": ["bone", "dungeon", "eotn", "dervish"],
    "claims": ["character", "inventory"],
}

import os
from typing import Callable
from typing import Iterator

import PySystem

from Bots.marks_coding_corner.utils.loot_utils import VIABLE_LOOT
from Bots.marks_coding_corner.utils.loot_utils import get_valid_loot_array
from Bots.marks_coding_corner.utils.loot_utils import move_all_crafting_materials_to_storage
from Bots.marks_coding_corner.utils.loot_utils import set_autoloot_options_for_custom_bots
from Bots.marks_coding_corner.utils.merch_utils import buy_id_kits
from Bots.marks_coding_corner.utils.merch_utils import buy_salvage_kits
from Bots.marks_coding_corner.utils.merch_utils import sell_non_essential_mats
from Bots.marks_coding_corner.utils.merch_utils import withdraw_gold
from Bots.marks_coding_corner.utils.town_utils import return_to_outpost
from Core import GLOBAL_CACHE
from Core import Agent
from Core import ModelID
from Core import Player
from Core import Range
from Core import Routines
from Core.BottingTree import BottingTree
from Core.BTBuilds.Dervish.D_A.DervBoneFarmer import ENEMY_BLACKLIST_ENC_STRINGS
from Core.BTBuilds.Dervish.D_A.DervBoneFarmer import ENEMY_BLACKLIST_NAMES
from Core.BTBuilds.Dervish.D_A.DervBoneFarmer import DervBoneFarmer
from Core.BTBuilds.Dervish.D_A.DervBoneFarmer import DervBuildFarmStatus
from Core.BTBuilds.Dervish.D_A.DervBoneFarmer import is_blacklisted_enemy
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Settings import Settings
from Core.routines_src.BehaviourTrees import BT

MODULE_NAME = "COF Farmer BT"
INI_PATH = "Widgets/Automation/Bots/COF Farmer BT"
INI_FILENAME = "COF_Farmer_BT.ini"

DOOMLORE_SHRINE_ID = 648
COF_LEVEL_1_ID = 560

MERCHANT_XY = (-19166.00, 17980.00)
MERCHANT_MOVE_XY = (-18815.00, 17923.00)
COF_ENTRANCE_MOVE_XY = (-18295.50, -8614.49)
COF_ENTRANCE_GADGET_XY = (-18250.00, -8595.00)
COF_PREP_SPOT = (-16623.00, -8989.00)
COF_ATTACK_SPOT_1 = (-15525.00, -8923.00)
COF_ATTACK_SPOT_2 = (-15737.00, -9093.00)
SETUP_RESIGN_SPOT = (-19665.00, -8045.00)

MERCHANT_DIALOG = 0x7F
COF_QUEST_DIALOG = 0x832101
COF_ENTER_DIALOG = 0x88
COF_ENTRANCE_GADGET_DIALOG = 0x84

VIABLE_LOOT |= {ModelID.Golden_Rin_Relic, ModelID.Diessa_Chalice}

botting_tree: BottingTree | None = None
derv_build: DervBoneFarmer | None = None
initialized = False
ini_key = ""


def get_derv_build() -> DervBoneFarmer:
    global derv_build
    if derv_build is None:
        derv_build = DervBoneFarmer()
    return derv_build


def run_generator(gen_factory: Callable[[], Iterator], name: str = "RunGenerator") -> BehaviorTree:
    state = {"gen": None}

    def tick_next(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if state["gen"] is None:
            state["gen"] = gen_factory()  # type: ignore
        try:
            next(state["gen"])
            return BehaviorTree.NodeState.RUNNING
        except StopIteration:
            state["gen"] = None
            return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=tick_next, aftercast_ms=0))


def set_phase(phase: str) -> BehaviorTree:
    def apply_phase(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        get_derv_build().status = phase
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=f"SetPhase({phase})", action_fn=apply_phase, aftercast_ms=0))


def wait_for_area_clear_or_death(
    engage_range: float = Range.Earshot.value,
    clear_range: float = Range.Earshot.value,
    no_enemy_timeout_ms: int = 15_000,
    clear_hold_ms: int = 1500,
) -> BehaviorTree:
    """Wait for combat to complete.

    Latches on first-enemy-seen (within engage_range) so we don't declare "clear"
    while enemies are still running toward us. Once engaged, requires the area
    (within clear_range) to be continuously clear for clear_hold_ms before
    declaring SUCCESS — filters out transient flickers from knockbacks / spacing.
    no_enemy_timeout_ms bails out of the engage wait if nothing ever appears.
    """
    import time

    state = {"engaged": False, "started_at": 0.0, "clear_since": 0.0}

    def reset_state():
        state["engaged"] = False
        state["started_at"] = 0.0
        state["clear_since"] = 0.0

    def tick_check(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        if Agent.IsDead(Player.GetAgentID()):
            reset_state()
            return BehaviorTree.NodeState.FAILURE

        if state["started_at"] == 0.0:
            state["started_at"] = time.monotonic()

        px, py = Player.GetXY()

        if not state["engaged"]:
            engage_pool = Routines.Agents.GetFilteredEnemyArray(px, py, engage_range)
            for enemy_id in engage_pool:
                if not is_blacklisted_enemy(enemy_id):
                    state["engaged"] = True
                    break
            else:
                if (time.monotonic() - state["started_at"]) * 1000 >= no_enemy_timeout_ms:
                    PySystem.Console.Log(
                        "WaitForAreaClearOrDeath",
                        f"No enemies appeared within {engage_range} in {no_enemy_timeout_ms}ms; giving up.",
                        PySystem.Console.MessageType.Warning,
                    )
                    reset_state()
                    return BehaviorTree.NodeState.SUCCESS
                return BehaviorTree.NodeState.RUNNING

        clear_pool = Routines.Agents.GetFilteredEnemyArray(px, py, clear_range)
        for enemy_id in clear_pool:
            if not is_blacklisted_enemy(enemy_id):
                state["clear_since"] = 0.0
                return BehaviorTree.NodeState.RUNNING

        now = time.monotonic()
        if state["clear_since"] == 0.0:
            state["clear_since"] = now
            return BehaviorTree.NodeState.RUNNING
        if (now - state["clear_since"]) * 1000 < clear_hold_ms:
            return BehaviorTree.NodeState.RUNNING

        all_enemies = Routines.Agents.GetFilteredEnemyArray(px, py, Range.Compass.value)
        remaining = [
            {
                "aid": aid,
                "name": Agent.GetNameByID(aid),
                "enc": Agent.GetEncNameStrByID(aid, literal=False),
                "model_id": Agent.GetModelID(aid),
            }
            for aid in all_enemies
            if not Agent.IsDead(aid)
        ]
        if remaining:
            PySystem.Console.Log(
                "WaitForAreaClearOrDeath",
                f"Declaring area clear. Enemies still alive within compass: {remaining}. "
                f"Name blacklist={ENEMY_BLACKLIST_NAMES}. Enc blacklist={ENEMY_BLACKLIST_ENC_STRINGS}.",
                PySystem.Console.MessageType.Warning,
            )
        reset_state()
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name="WaitForAreaClearOrDeath", action_fn=tick_check, aftercast_ms=0))


def loot_filtered_items() -> BehaviorTree:
    def loot_gen():
        yield from Routines.Yield.wait(500)
        filtered = get_valid_loot_array(viable_loot=VIABLE_LOOT, loot_salvagables=True)
        yield from Routines.Yield.Items.LootItemsWithMaxAttempts(filtered, log=False)

    return run_generator(loot_gen, name="LootFilteredItems")


def inventory_is_ready() -> bool:
    salv = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Salvage_Kit)
    id_kits = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Identification_Kit)
    sup_id = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Superior_Identification_Kit)
    free = GLOBAL_CACHE.Inventory.GetFreeSlotCount()
    return (id_kits + sup_id) > 0 and salv >= 3 and free >= 4


def choose_recovery_step_name() -> str:
    return "Farm Loop" if inventory_is_ready() else "Prepare Outpost"


def initialize_bot() -> BehaviorTree:
    bot = ensure_botting_tree()
    set_autoloot_options_for_custom_bots(salvage_golds=True, module_active=False)
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Initialize Bot",
            children=[
                bot.Config.Pacifist(
                    auto_loot=False,
                    pause_on_danger=False,
                    resurrection_scroll=False,
                    multi_account=False,
                ),
                set_phase(DervBuildFarmStatus.Wait),
            ],
        )
    )


def prepare_outpost() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Prepare Outpost",
            children=[
                BT.Map.TravelToOutpost(outpost_id=DOOMLORE_SHRINE_ID, log=True, timeout=30_000),
                BT.Skills.LoadSkillbar("OgCjkqqLrSYiihdftXjhOXhX0kA", log=True),
                BT.Player.Wait(1_500),
                set_phase(DervBuildFarmStatus.Setup),
                BT.Player.Move(x=MERCHANT_MOVE_XY[0], y=MERCHANT_MOVE_XY[1], log=True),
                dialog_at(MERCHANT_XY, MERCHANT_DIALOG, "Open Merchant"),
                run_generator(withdraw_gold, name="WithdrawGold"),
                run_generator(sell_non_essential_mats, name="SellNonEssentialMats"),
                run_generator(buy_id_kits, name="BuyIDKits"),
                run_generator(lambda: buy_salvage_kits(custom_amount=5), name="BuySalvageKits"),
                BT.Items.IdentifyInventoryItems(log=False),
                BT.Items.SalvageInventoryItems(log=False),
                run_generator(move_all_crafting_materials_to_storage, name="StoreCraftingMats"),
                dialog_at(MERCHANT_XY, COF_QUEST_DIALOG, "Take COF quest"),
                dialog_at(MERCHANT_XY, COF_ENTER_DIALOG, "Enter COF Level 1"),
                BT.Player.Wait(2_000),
                BT.Map.WaitforMapLoad(map_id=COF_LEVEL_1_ID, log=True, timeout=60_000),
                BT.Player.Move(x=SETUP_RESIGN_SPOT[0], y=SETUP_RESIGN_SPOT[1], log=True),
                BT.Party.Resign(log=True),
                BT.Map.WaitforMapLoad(map_id=DOOMLORE_SHRINE_ID, log=True, timeout=60_000),
            ],
        )
    )


def farm_loop() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Farm Loop",
            children=[
                run_generator(return_to_outpost, name="EnsureAtOutpost"),
                BT.Map.WaitforMapLoad(map_id=DOOMLORE_SHRINE_ID, log=True, timeout=60_000),
                dialog_at(MERCHANT_XY, COF_QUEST_DIALOG, "Take COF quest"),
                dialog_at(MERCHANT_XY, COF_ENTER_DIALOG, "Enter COF Level 1"),
                BT.Map.WaitforMapLoad(map_id=COF_LEVEL_1_ID, log=True, timeout=60_000),
                BT.Player.Wait(2_000),
                BT.Player.Move(x=COF_ENTRANCE_MOVE_XY[0], y=COF_ENTRANCE_MOVE_XY[1], log=True),
                dialog_at(COF_ENTRANCE_GADGET_XY, COF_ENTRANCE_GADGET_DIALOG, "Open COF gadget"),
                BT.Player.Move(x=COF_PREP_SPOT[0], y=COF_PREP_SPOT[1], log=True),
                set_phase(DervBuildFarmStatus.Prepare),
                BT.Player.Wait(3_000),
                BT.Player.Move(x=COF_ATTACK_SPOT_1[0], y=COF_ATTACK_SPOT_1[1], log=True),
                BT.Player.Move(x=COF_ATTACK_SPOT_2[0], y=COF_ATTACK_SPOT_2[1], log=True),
                set_phase(DervBuildFarmStatus.Kill),
                wait_for_area_clear_or_death(),
                set_phase(DervBuildFarmStatus.Loot),
                BT.Player.Wait(500),
                loot_filtered_items(),
                BT.Player.Wait(500),
                BT.Items.IdentifyInventoryItems(log=False),
                BT.Items.SalvageInventoryItems(log=False),
                set_phase(DervBuildFarmStatus.Wait),
                BT.Party.Resign(log=True),
                BT.Map.WaitforMapLoad(map_id=DOOMLORE_SHRINE_ID, log=True, timeout=60_000),
            ],
        )
    )


def dialog_at(xy: tuple[float, float], dialog_id: int, label: str) -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"DialogAt:{label}",
            children=[
                BT.Movement.MoveTargetInteractAndDialog(
                    x=xy[0],
                    y=xy[1],
                    dialog_id=dialog_id,
                    pause_on_combat=False,
                    log=True,
                ),
                BT.Player.Wait(500),
            ],
        )
    )


def get_execution_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    return [
        ("Initialize Bot", initialize_bot),
        ("Prepare Outpost", prepare_outpost),
        ("Farm Loop", farm_loop),
    ]


def ensure_botting_tree() -> BottingTree:
    global botting_tree
    if botting_tree is None:
        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name="COFFarmSequence",
            repeat=True,
            reset=False,
            auto_start=False,
            multi_account=False,
            isolation_enabled=True,
        )
        botting_tree.AddBuild(get_derv_build())
        botting_tree.EnsurePartyWipeRecoveryService(
            default_step_name=choose_recovery_step_name,
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
        "Bots",
        "marks_coding_corner",
        "textures",
        "cof_art.png",
    )
    tree.UI.draw_window(icon_path=texture)


if __name__ == "__main__":
    main()
