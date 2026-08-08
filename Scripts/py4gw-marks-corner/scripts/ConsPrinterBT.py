"""Cons printer — batch-crafts consets at Embark Beach. BT conversion of the FSM ConsPrinter."""

__script__ = {
    "name": "Cons Printer BT",
    "function": "crafter",
    "tags": ["conset", "crafting", "economy", "embark-beach"],
    "claims": ["character", "inventory"],
}

import math
import os
import time
from typing import Callable

import PyImGui
import PyInventory
import PySystem

from Core import GLOBAL_CACHE
from Core import Agent
from Core import Player
from Core import Range
from Core.AgentArray import AgentArray
from Core.BottingTree import BottingTree
from Core.enums_src.Item_enums import STORAGE_BAGS
from Core.enums_src.Item_enums import Bags
from Core.enums_src.Model_enums import ModelID
from Core.py4gwcorelib_src import item_snapshot
from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.py4gwcorelib_src.Settings import Settings
from Core.routines_src.BehaviourTrees import BT
from Core.UIManager import CrafterWindow
from Core.UIManager import TraderWindow
from Core.UIManager import XunlaiStorageWindow

MODULE_NAME = "Cons Printer BT"
INI_PATH = "Widgets/Automation/Bots/Cons Printer BT"
INI_FILENAME = "Cons_Printer_BT.ini"

EMBARK_BEACH = "Embark Beach"

MATERIAL_TRADER_XY = (2997.0, -2271.0)
MERCHANT_XY = (2158.0, -2006.0)

TRADER_SELLABLE = (ModelID.Scale, ModelID.Granite_Slab)
MERCHANT_SELLABLE = (
    ModelID.Wood_Plank,
    ModelID.Scale,
    ModelID.Tanned_Hide_Square,
    ModelID.Bolt_Of_Cloth,
    ModelID.Granite_Slab,
    ModelID.Chitin_Fragment,
)

PER_CONSET = {
    ModelID.Iron_Ingot: 100,
    ModelID.Pile_Of_Glittering_Dust: 100,
    ModelID.Bone: 50,
    ModelID.Feather: 50,
}
GOLD_PER_CONSET = 750
SKILL_POINTS_PER_CONSET = 3
CRAFT_COST_GOLD = 250

CONSET_RECIPES: tuple[tuple[ModelID, dict[ModelID, int], tuple[float, float]], ...] = (
    (ModelID.Armor_Of_Salvation, {ModelID.Iron_Ingot: 50, ModelID.Bone: 50}, (3743.0, -106.0)),
    (ModelID.Essence_Of_Celerity, {ModelID.Feather: 50, ModelID.Pile_Of_Glittering_Dust: 50}, (3666.0, 90.0)),
    (ModelID.Grail_Of_Might, {ModelID.Iron_Ingot: 50, ModelID.Pile_Of_Glittering_Dust: 50}, (3414.0, 644.0)),
)
CONSET_MODELS = tuple(model for model, per_craft, xy in CONSET_RECIPES)

INVENTORY_BAGS = [Bags.Backpack, Bags.BeltPouch, Bags.Bag1, Bags.Bag2]
STACK_SIZE = 250
BUFFER_SLOTS = 4
MAX_WITHDRAW_STACKS = 20
# The common material trader deals in lots of ten; smaller stacks cannot be quoted.
MATERIAL_TRADER_LOT = 10

# Kits are what a general merchant stocks and traders/crafters never do — the
# streamed stock proves which window opened. Same identity check as InventoryLite.
KIT_MODELS = (
    int(ModelID.Salvage_Kit.value),
    int(ModelID.Identification_Kit.value),
    int(ModelID.Superior_Identification_Kit.value),
)
MERCHANT_PROBE_RANGE = 1500.0
MERCHANT_PROBE_TIMEOUT_MS = 5_000
MERCHANT_PROBE_LIMIT = 4

PLAN_KEY = "cons_plan"
# The scan anchors on the NPC's own coordinates, so tight is what disambiguates:
# every other NPC in the crowd is further from the anchor than the one standing on it.
NPC_SCAN_RADIUS = 100.0
WINDOW_TIMEOUT_MS = 10_000
# Inventory counts lag the transaction-complete flag by a few frames; the verify
# waits them out rather than reading one stale frame.
VERIFY_TIMEOUT_MS = 5_000
# Expiry means "could not observe the transfer", never "it did not happen" — on
# expiry the action is re-permitted or the node settles, it does not FAIL.
OBSERVE_TIMEOUT_MS = 5_000
WITHDRAW_RETRY_LIMIT = 3

botting_tree: BottingTree | None = None
initialized = False
ini_key = ""


def now_ms() -> float:
    return time.monotonic() * 1000.0


def log_info(message: str) -> None:
    PySystem.Console.Log(MODULE_NAME, message, PySystem.Console.MessageType.Info)


def log_warning(message: str) -> None:
    PySystem.Console.Log(MODULE_NAME, message, PySystem.Console.MessageType.Warning)


def craft_target_key(model: ModelID) -> str:
    return f"craft_target:{model.name}"


def bag_items() -> list[item_snapshot.ItemSnapshot]:
    return item_snapshot.read_bags(INVENTORY_BAGS)


def material_pane_stacks(model: ModelID) -> list:
    try:
        entries = PyInventory.Bag(Bags.MaterialStorage.value, Bags.MaterialStorage.name).GetItems()
    except Exception:
        return []
    return [entry for entry in entries if int(entry.model_id) == model and int(entry.quantity) > 0]  # type: ignore


def stored_material_count(model: ModelID) -> int:
    """Bank tabs plus the material pane — the tab-only count misses where materials usually live."""
    in_pane = sum(int(entry.quantity) for entry in material_pane_stacks(model))
    return GLOBAL_CACHE.Inventory.GetModelCountInStorage(model) + in_pane


def landing_target(model: ModelID, quantity: int, bags: list) -> tuple[int, int, int] | None:
    """(bag_id, slot, amount) where a moved stack can land — partial stack first."""
    first_empty = None
    for bag_enum in bags:
        try:
            bag = PyInventory.Bag(bag_enum.value, bag_enum.name)
            entries = bag.GetItems()
            size = bag.GetSize()
        except Exception:
            continue
        occupied = {}
        for entry in entries:
            occupied[int(entry.slot)] = (int(entry.model_id), int(entry.quantity))  # type: ignore
        for slot, (slot_model, slot_quantity) in occupied.items():
            room = STACK_SIZE - slot_quantity
            if slot_model == model and room > 0:
                return bag_enum.value, slot, min(room, quantity)
        if first_empty is None:
            for slot in range(size):
                if slot not in occupied:
                    first_empty = (bag_enum.value, slot, min(quantity, STACK_SIZE))
                    break
    return first_empty


def pull_from_storage(model: ModelID, amount: int, name: str) -> bool:
    """Fire one withdraw — bank tabs first, then the material pane. True when a move was queued.

    Withdrawing FROM the pane is an ordinary item move into a bag slot; only deposits
    need the client's own button, because MoveItem cannot target the pane."""
    in_tabs = GLOBAL_CACHE.Inventory.GetModelCountInStorage(model)
    if in_tabs > 0:
        if GLOBAL_CACHE.Inventory.WithdrawItemFromStorageByModelID(model, min(amount, in_tabs)):
            return True
        log_warning(f"{name}: tab withdraw found nothing to move despite the storage count.")
    for entry in material_pane_stacks(model):
        landing = landing_target(model, min(amount, int(entry.quantity)), INVENTORY_BAGS)
        if landing is None:
            log_warning(f"{name}: no bag slot free to land a material pane withdrawal.")
            return False
        bag_id, slot, landed = landing
        GLOBAL_CACHE.Inventory.MoveItem(int(entry.item_id), bag_id, slot, landed)
        return True
    return False


def optional(subtree: BehaviorTree, name: str) -> BehaviorTree:
    """Let a step miss without dropping the run — the planner stops on FAILURE."""

    def carry_on() -> BehaviorTree.NodeState:
        log_warning(f"{name} did not complete - continuing anyway.")
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


def talk_to_npc(npc_xy: tuple[float, float], label: str) -> BehaviorTree:
    """Stop at touch range, then target the NPC standing on these exact coordinates.

    The walk cannot end ON the coordinates — the NPC's collision circle keeps the
    mover outside the default tolerance — and nearest-to-player targeting picks a
    bystander from wherever the walk happened to stop, so the target scan anchors
    on the NPC's own position instead.
    """
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=f"Talk to {label}",
            children=[
                BT.Movement.Move(x=npc_xy[0], y=npc_xy[1], tolerance=Range.Touch.value, log=True),
                BT.Agents.TargetNearestNPCXY(npc_xy[0], npc_xy[1], NPC_SCAN_RADIUS, log=True),
                BT.Player.InteractTarget(log=True),
            ],
        )
    )


def window_open_wait(
    is_open_fn: Callable[[], bool], label: str, stock_fn: Callable[[], list] | None = None
) -> BehaviorTree:
    """The frame existing is not proof the trade session is live — transactions fired
    before the stock list streams in are dropped. Where a stock_fn is given, the wait
    holds until the offered list arrives, the way InventoryLite's merchant flow does."""

    def check() -> BehaviorTree.NodeState:
        if not is_open_fn():
            return BehaviorTree.NodeState.RUNNING
        if stock_fn is not None and not stock_fn():
            return BehaviorTree.NodeState.RUNNING
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(
        BehaviorTree.WaitUntilNode(condition_fn=check, timeout_ms=WINDOW_TIMEOUT_MS, name=f"Await {label}")
    )


def open_merchant_by_stock() -> BehaviorTree:
    """InventoryLite's lesson, BT-ified: identity is decided by what the window turns
    out to sell, never by the NPC we walked to. Probe the NPCs around the merchant
    spot until one streams a stock list with kits in it — the fixed coordinates only
    say where to search. The client walks the last gap itself on interact."""
    state: dict = {"candidates": None, "index": -1, "since": 0.0}

    def reset() -> None:
        state["candidates"] = None
        state["index"] = -1
        state["since"] = 0.0

    def rank_candidates() -> list[int]:
        ranked = []
        for agent_id in AgentArray.GetNPCMinipetArray():
            try:
                x, y = Agent.GetXY(agent_id)
            except Exception:
                continue
            distance = math.hypot(x - MERCHANT_XY[0], y - MERCHANT_XY[1])
            if distance > MERCHANT_PROBE_RANGE:
                continue
            # Names load lazily from gw.dat; empty is the normal case, not an error.
            try:
                name = (Agent.GetNameByID(agent_id) or "").strip().lower()
            except Exception:
                name = ""
            rank = 0 if name == "merchant" else 1 if "merchant" in name else 2
            ranked.append((rank, distance, agent_id))
        ranked.sort()
        return [agent_id for rank, distance, agent_id in ranked[:MERCHANT_PROBE_LIMIT]]

    def stocks_kits(offered) -> bool:
        for item_id in offered:
            try:
                if int(GLOBAL_CACHE.Item.GetModelID(item_id)) in KIT_MODELS:
                    return True
            except Exception:
                continue
        return False

    def probe(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        offered = list(GLOBAL_CACHE.Trading.Merchant.GetOfferedItems() or [])
        if offered and stocks_kits(offered):
            log_info(f"Merchant window proven by stock ({len(offered)} items).")
            reset()
            return BehaviorTree.NodeState.SUCCESS
        if state["candidates"] is None:
            state["candidates"] = rank_candidates()
            if not state["candidates"]:
                log_warning("Merchant probe: no NPC within range of the merchant spot.")
                reset()
                return BehaviorTree.NodeState.FAILURE
            log_info(f"Merchant probe: trying {len(state['candidates'])} nearby NPC(s).")
        if state["index"] < 0 or now_ms() - state["since"] >= MERCHANT_PROBE_TIMEOUT_MS:
            state["index"] += 1
            if state["index"] >= len(state["candidates"]):
                log_warning("Merchant probe: no candidate streamed a merchant stock list.")
                reset()
                return BehaviorTree.NodeState.FAILURE
            agent_id = state["candidates"][state["index"]]
            log_info(f"Merchant probe: interacting with agent {agent_id}.")
            Player.ChangeTarget(agent_id)
            Player.Interact(agent_id, False)
            state["since"] = now_ms()
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name="Open merchant by stock", action_fn=probe, aftercast_ms=250))


def merch_sell_items(model: ModelID) -> BehaviorTree:
    """InventoryLite's sell mechanism, verbatim: fire SellItem at value * quantity and
    confirm on the stack leaving the bags. Deliberately built ONLY from calls proven in
    this bot's own runs — no frame checks, no stock reads, no snapshot layer; each of
    those has frozen the tick loop around the merchant window."""
    name = f"Merch {model.name}"
    state: dict = {"item_id": 0, "quantity": 0, "since": 0.0, "skipped": set()}

    def sell_next() -> BehaviorTree.NodeState:
        if state["item_id"]:
            held = GLOBAL_CACHE.Inventory.GetItemCount(state["item_id"])
            if held <= 0 or held < state["quantity"]:
                state["item_id"] = 0
            elif now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
                return BehaviorTree.NodeState.RUNNING
            else:
                log_warning(f"{name}: sale of item {state['item_id']} not observed; skipping it.")
                state["skipped"].add(state["item_id"])
                state["item_id"] = 0
        for item_id in GLOBAL_CACHE.Inventory.GetAllItemIdsByModelID(model):
            if item_id in state["skipped"]:
                continue
            quantity = GLOBAL_CACHE.Inventory.GetItemCount(item_id)
            if quantity <= 0:
                continue
            value = int(GLOBAL_CACHE.Item.Properties.GetValue(item_id) or 0)
            if value <= 0:
                log_warning(f"{name}: item {item_id} has no merchant value; skipping it.")
                state["skipped"].add(item_id)
                continue
            log_info(f"{name}: selling {quantity} for {value * quantity}g (item {item_id}).")
            GLOBAL_CACHE.Trading.Merchant.SellItem(item_id, value * quantity)
            state["item_id"] = item_id
            state["quantity"] = quantity
            state["since"] = now_ms()
            return BehaviorTree.NodeState.RUNNING
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=sell_next, aftercast_ms=250))


def deposit_to_bank(models: tuple, name: str) -> BehaviorTree:
    """InventoryLite's deposit mechanism: an explicit (bag, slot, amount) move into
    the bank tabs, partial stacks first, confirmed on the stack leaving the bags.
    Proven calls only, same reasoning as the merchant sell. Needs storage open."""
    state: dict = {"item_id": 0, "quantity": 0, "since": 0.0, "skipped": set()}

    def deposit_next() -> BehaviorTree.NodeState:
        if state["item_id"]:
            held = GLOBAL_CACHE.Inventory.GetItemCount(state["item_id"])
            if held <= 0 or held < state["quantity"]:
                state["item_id"] = 0
            elif now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
                return BehaviorTree.NodeState.RUNNING
            else:
                log_warning(f"{name}: deposit of item {state['item_id']} not observed; leaving it in the bags.")
                state["skipped"].add(state["item_id"])
                state["item_id"] = 0
        for model in models:
            for item_id in GLOBAL_CACHE.Inventory.GetAllItemIdsByModelID(model):
                if item_id in state["skipped"]:
                    continue
                quantity = GLOBAL_CACHE.Inventory.GetItemCount(item_id)
                if quantity <= 0:
                    continue
                target = landing_target(model, quantity, STORAGE_BAGS)
                if target is None:
                    log_warning(f"{name}: bank tabs are full; leaving item {item_id} in the bags.")
                    state["skipped"].add(item_id)
                    continue
                bag_id, slot, amount = target
                log_info(f"{name}: depositing {amount} of {model.name} (item {item_id}).")
                GLOBAL_CACHE.Inventory.MoveItem(item_id, bag_id, slot, amount)
                state["item_id"] = item_id
                state["quantity"] = quantity
                state["since"] = now_ms()
                return BehaviorTree.NodeState.RUNNING
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=deposit_next, aftercast_ms=250))


def ensure_storage_open() -> BehaviorTree:
    """Storage reads work with the window shut; storage MOVES are silently ignored
    without it. OpenXunlaiWindow opens it from anywhere in an outpost."""

    def request_open() -> BehaviorTree.NodeState:
        GLOBAL_CACHE.Inventory.OpenXunlaiWindow()
        return BehaviorTree.NodeState.SUCCESS

    def open_observed() -> BehaviorTree.NodeState:
        if GLOBAL_CACHE.Inventory.IsStorageOpen():
            return BehaviorTree.NodeState.SUCCESS
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Open Xunlai storage",
            children=[
                BehaviorTree.ActionNode(name="Request Xunlai open", action_fn=request_open, aftercast_ms=250),
                BehaviorTree.WaitUntilNode(
                    condition_fn=open_observed, timeout_ms=WINDOW_TIMEOUT_MS, name="Await Xunlai storage"
                ),
            ],
        )
    )


def sweep_materials_to_pane() -> BehaviorTree:
    """The client's own deposit-all-materials button — the ONE mover that reaches the
    material pane; MoveItem and DepositItemToStorage only target the bank tabs.
    Same approach as InventoryLite."""
    state: dict = {"clicked": False, "stacks": 0, "since": 0.0}

    def reset() -> None:
        state["clicked"] = False
        state["stacks"] = 0
        state["since"] = 0.0

    def sweep() -> BehaviorTree.NodeState:
        stacks = sum(1 for snapshot in bag_items() if snapshot.is_material)
        if not state["clicked"]:
            if stacks == 0:
                reset()
                return BehaviorTree.NodeState.SUCCESS
            if not XunlaiStorageWindow.ClickDepositAllMaterials():
                log_warning("Sweep materials: could not reach the deposit-all-materials button.")
                reset()
                return BehaviorTree.NodeState.SUCCESS
            state["clicked"] = True
            state["stacks"] = stacks
            state["since"] = now_ms()
            return BehaviorTree.NodeState.RUNNING
        if stacks == 0:
            reset()
            return BehaviorTree.NodeState.SUCCESS
        if stacks != state["stacks"]:
            state["stacks"] = stacks
            state["since"] = now_ms()
            return BehaviorTree.NodeState.RUNNING
        if now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
            return BehaviorTree.NodeState.RUNNING
        log_warning(f"Sweep materials: {stacks} stack(s) stayed in the bags - the material pane may be full.")
        reset()
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name="Sweep materials to pane", action_fn=sweep, aftercast_ms=250))


def deposit_items(matches_fn: Callable[[item_snapshot.ItemSnapshot], bool], name: str) -> BehaviorTree:
    """Deposit matching items one per fire, waiting for each to leave the bags before the next."""
    state: dict = {"item_id": 0, "since": 0.0, "skipped": set()}

    def deposit_next() -> BehaviorTree.NodeState:
        items = bag_items()
        if state["item_id"]:
            if any(snapshot.item_id == state["item_id"] for snapshot in items):
                if now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
                    return BehaviorTree.NodeState.RUNNING
                log_warning(f"{name}: deposit of item {state['item_id']} not observed; leaving it in the bags.")
                state["skipped"].add(state["item_id"])
            state["item_id"] = 0
        for snapshot in items:
            if snapshot.item_id in state["skipped"]:
                continue
            if matches_fn(snapshot):
                GLOBAL_CACHE.Inventory.DepositItemToStorage(snapshot.item_id)
                state["item_id"] = snapshot.item_id
                state["since"] = now_ms()
                return BehaviorTree.NodeState.RUNNING
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=deposit_next, aftercast_ms=250))


def withdraw_stacks(model: ModelID, name: str) -> BehaviorTree:
    """Pull every stored stack of the model into the bags, one withdraw per observed change."""
    state: dict = {"held": None, "since": 0.0, "stacks": 0}

    def withdraw_next() -> BehaviorTree.NodeState:
        held = GLOBAL_CACHE.Inventory.GetModelCount(model)
        if state["held"] is not None:
            if held != state["held"]:
                state["held"] = None
            elif now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
                return BehaviorTree.NodeState.RUNNING
            else:
                log_warning(f"{name}: withdraw not observed (held {held}); stopping at {state['stacks']} stacks.")
                return BehaviorTree.NodeState.SUCCESS
        if stored_material_count(model) <= 0:
            return BehaviorTree.NodeState.SUCCESS
        if state["stacks"] >= MAX_WITHDRAW_STACKS or GLOBAL_CACHE.Inventory.GetFreeSlotCount() <= 0:
            return BehaviorTree.NodeState.SUCCESS
        if not pull_from_storage(model, STACK_SIZE, name):
            return BehaviorTree.NodeState.SUCCESS
        state["held"] = held
        state["since"] = now_ms()
        state["stacks"] += 1
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=withdraw_next, aftercast_ms=250))


def withdraw_planned_material(model: ModelID) -> BehaviorTree:
    name = f"Withdraw {model.name}"
    state: dict = {"held": None, "since": 0.0, "retries": 0}

    def withdraw_next(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target = PER_CONSET[model] * int(node.blackboard.get(PLAN_KEY, 0))
        held = GLOBAL_CACHE.Inventory.GetModelCount(model)
        if held >= target:
            state["held"] = None
            state["retries"] = 0
            return BehaviorTree.NodeState.SUCCESS
        if state["held"] is not None:
            if held != state["held"]:
                state["held"] = None
                state["retries"] = 0
            elif now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
                return BehaviorTree.NodeState.RUNNING
            else:
                state["held"] = None
                state["retries"] += 1
                if state["retries"] >= WITHDRAW_RETRY_LIMIT:
                    log_warning(f"{name}: withdraws not observed; settling at {held}/{target}.")
                    state["retries"] = 0
                    return BehaviorTree.NodeState.SUCCESS
                log_warning(f"{name}: withdraw not observed (held {held}/{target}); refiring.")
        if stored_material_count(model) <= 0:
            log_warning(f"{name}: storage dry at {held}/{target}.")
            return BehaviorTree.NodeState.SUCCESS
        if not pull_from_storage(model, min(STACK_SIZE, target - held), name):
            log_warning(f"{name}: could not fire a withdraw; settling at {held}/{target}.")
            return BehaviorTree.NodeState.SUCCESS
        state["held"] = held
        state["since"] = now_ms()
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=withdraw_next, aftercast_ms=250))


def withdraw_planned_gold() -> BehaviorTree:
    name = "Withdraw gold"
    state: dict = {"gold": None, "since": 0.0, "retries": 0}

    def withdraw_next(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        needed = GOLD_PER_CONSET * int(node.blackboard.get(PLAN_KEY, 0))
        on_character = GLOBAL_CACHE.Inventory.GetGoldOnCharacter()
        if on_character >= needed:
            state["gold"] = None
            state["retries"] = 0
            return BehaviorTree.NodeState.SUCCESS
        if state["gold"] is not None:
            if on_character != state["gold"]:
                state["gold"] = None
                state["retries"] = 0
            elif now_ms() - state["since"] < OBSERVE_TIMEOUT_MS:
                return BehaviorTree.NodeState.RUNNING
            else:
                state["gold"] = None
                state["retries"] += 1
                if state["retries"] >= WITHDRAW_RETRY_LIMIT:
                    log_warning(f"{name}: withdraws not observed; settling at {on_character}/{needed}g.")
                    state["retries"] = 0
                    return BehaviorTree.NodeState.SUCCESS
                log_warning(f"{name}: withdraw not observed ({on_character}/{needed}g); refiring.")
        in_storage = GLOBAL_CACHE.Inventory.GetGoldInStorage()
        if in_storage <= 0:
            log_warning(f"{name}: no gold left in storage at {on_character}/{needed}g.")
            return BehaviorTree.NodeState.SUCCESS
        GLOBAL_CACHE.Inventory.WithdrawGold(min(needed - on_character, in_storage))
        state["gold"] = on_character
        state["since"] = now_ms()
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=withdraw_next, aftercast_ms=250))


def slots_fit(conset_count: int, free_slots: int) -> bool:
    slots_needed = 0
    for model, per_conset in PER_CONSET.items():
        remaining = max(0, per_conset * conset_count - GLOBAL_CACHE.Inventory.GetModelCount(model))
        slots_needed += (remaining + STACK_SIZE - 1) // STACK_SIZE
    return slots_needed + BUFFER_SLOTS <= free_slots


def plan_consets() -> BehaviorTree:
    """FAILURE when nothing can be printed — that is the print loop's exit condition."""

    def plan(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        node.blackboard[PLAN_KEY] = 0
        free_slots = GLOBAL_CACHE.Inventory.GetFreeSlotCount()
        if free_slots < len(PER_CONSET) + BUFFER_SLOTS:
            log_warning(f"Not enough free bag slots ({free_slots}) to print consets.")
            return BehaviorTree.NodeState.FAILURE
        possible = min(
            (stored_material_count(model) + GLOBAL_CACHE.Inventory.GetModelCount(model)) // amount
            for model, amount in PER_CONSET.items()
        )
        gold = GLOBAL_CACHE.Inventory.GetGoldInStorage() + GLOBAL_CACHE.Inventory.GetGoldOnCharacter()
        possible = min(possible, gold // GOLD_PER_CONSET)
        skill_points, _ = Player.GetSkillPointData()
        possible = min(possible, skill_points // SKILL_POINTS_PER_CONSET)
        while possible > 0 and not slots_fit(possible, free_slots):
            possible -= 1
        if possible <= 0:
            totals = ", ".join(
                f"{model.name} {stored_material_count(model) + GLOBAL_CACHE.Inventory.GetModelCount(model)}/{amount}"
                for model, amount in PER_CONSET.items()
            )
            log_info(
                f"Nothing left to print. Per conset: {totals}; gold {gold}/{GOLD_PER_CONSET}, "
                f"skill points {skill_points}/{SKILL_POINTS_PER_CONSET}, free slots {free_slots}."
            )
            return BehaviorTree.NodeState.FAILURE
        node.blackboard[PLAN_KEY] = possible
        for model in CONSET_MODELS:
            node.blackboard[craft_target_key(model)] = GLOBAL_CACHE.Inventory.GetModelCount(model) + possible
        log_info(
            f"Printing {possible} conset(s): {possible * SKILL_POINTS_PER_CONSET} skill points, "
            f"{possible * GOLD_PER_CONSET}g."
        )
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name="Plan Consets", action_fn=plan, aftercast_ms=0))


def gather_ingredients(per_craft: dict[ModelID, int]) -> tuple[list[int], list[int]] | None:
    trade_items: list[int] = []
    trade_quantities: list[int] = []
    for model, needed in per_craft.items():
        remaining = needed
        for item_id in GLOBAL_CACHE.Inventory.GetAllItemIdsByModelID(model):
            if remaining <= 0:
                break
            take = min(GLOBAL_CACHE.Inventory.GetItemCount(item_id), remaining)
            if take <= 0:
                continue
            trade_items.append(item_id)
            trade_quantities.append(take)
            remaining -= take
        if remaining > 0:
            return None
    return trade_items, trade_quantities


def craft_step(target: ModelID, per_craft: dict[ModelID, int], crafter_xy: tuple[float, float]) -> BehaviorTree:
    name = f"Craft {target.name}"

    def more_to_craft(node: BehaviorTree.Node) -> bool:
        return GLOBAL_CACHE.Inventory.GetModelCount(target) < int(node.blackboard.get(craft_target_key(target), 0))

    def fail_step(message: str) -> BehaviorTree:
        def report() -> BehaviorTree.NodeState:
            log_warning(f"{name}: {message}")
            return BehaviorTree.NodeState.FAILURE

        return BehaviorTree(BehaviorTree.ActionNode(name=f"{name}: unavailable", action_fn=report, aftercast_ms=0))

    # Built lazily per craft so item ids and stacks are read while the window is open.
    def single_craft(node: BehaviorTree.Node) -> BehaviorTree:
        offered = GLOBAL_CACHE.Trading.Crafter.GetOfferedItems()
        item_id = next((offer for offer in offered if GLOBAL_CACHE.Item.GetModelID(offer) == target), 0)
        if not item_id:
            return fail_step("crafter does not offer it")
        ingredients = gather_ingredients(per_craft)
        if ingredients is None:
            return fail_step("out of ingredients")
        trade_items, trade_quantities = ingredients
        return BT.Economy.Crafting.CraftItem(item_id, CRAFT_COST_GOLD, trade_items, trade_quantities, log=True)

    def count_reached(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target_count = int(node.blackboard.get(craft_target_key(target), 0))
        held = GLOBAL_CACHE.Inventory.GetModelCount(target)
        if held >= target_count:
            log_info(f"{name}: verified - {held}/{target_count} in the bags.")
            return BehaviorTree.NodeState.SUCCESS
        return BehaviorTree.NodeState.RUNNING

    def report_shortfall(node: BehaviorTree.Node) -> BehaviorTree.NodeState:
        target_count = int(node.blackboard.get(craft_target_key(target), 0))
        held = GLOBAL_CACHE.Inventory.GetModelCount(target)
        log_warning(f"{name}: verified only {held}/{target_count} in the bags - stopping the print loop.")
        return BehaviorTree.NodeState.FAILURE

    # The craft loop can end for two reasons: target reached, or a craft that could
    # not fire. The verify tells them apart on the observable the craft writes — the
    # inventory count — so a wrong window or dead crafter ends the print loop loudly
    # instead of re-planning against untouched materials forever.
    verify = BehaviorTree.SelectorNode(
        name=f"{name}: verify",
        children=[
            BehaviorTree.WaitUntilNode(
                condition_fn=count_reached, timeout_ms=VERIFY_TIMEOUT_MS, name=f"{name}: count check"
            ),
            BehaviorTree.ActionNode(name=f"{name}: shortfall", action_fn=report_shortfall, aftercast_ms=0),
        ],
    )

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=name,
            children=[
                talk_to_npc(crafter_xy, f"{target.name} crafter"),
                window_open_wait(
                    CrafterWindow.IsOpen,
                    f"{target.name} crafter",
                    stock_fn=lambda: GLOBAL_CACHE.Trading.Crafter.GetOfferedItems(),
                ),
                BehaviorTree.RepeaterUntilFailureNode(
                    name=f"{name} until done",
                    child=BehaviorTree.SequenceNode(
                        name=f"{name} once",
                        children=[
                            BehaviorTree.ConditionNode(condition_fn=more_to_craft, name=f"{name}: more wanted"),
                            BehaviorTree.SubtreeNode(subtree_fn=single_craft, name=f"{name}: one craft"),
                        ],
                    ),
                ),
                verify,
            ],
        )
    )


def prepare_outpost() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Prepare Outpost",
            children=[
                BT.Map.TravelToOutpost(outpost_name=EMBARK_BEACH, log=True, timeout=60_000),
                BT.Player.Wait(1_000),
            ],
        )
    )


def bank_materials() -> BehaviorTree:
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Bank Materials",
            children=[
                ensure_storage_open(),
                sweep_materials_to_pane(),
                deposit_items(lambda snapshot: snapshot.is_material, "Bank overflow materials"),
            ],
        )
    )


def sell_at_material_trader() -> BehaviorTree:
    children: list = [ensure_storage_open()]
    children += [withdraw_stacks(model, f"Withdraw {model.name} to sell") for model in TRADER_SELLABLE]
    children += [
        talk_to_npc(MATERIAL_TRADER_XY, "material trader"),
        window_open_wait(TraderWindow.IsOpen, "material trader"),
    ]
    children += [
        BT.Economy.Trader.SellItems(model, min_quantity=MATERIAL_TRADER_LOT, log=True) for model in TRADER_SELLABLE
    ]
    children += [
        ensure_storage_open(),
        deposit_items(lambda snapshot: snapshot.model_id in TRADER_SELLABLE, "Bank unsold trader materials"),
    ]
    return optional(
        BehaviorTree(BehaviorTree.SequenceNode(name="Material Trader", children=children)),
        "Material trader selling",
    )


def sell_at_merchant() -> BehaviorTree:
    children: list = [ensure_storage_open()]
    children += [withdraw_stacks(model, f"Withdraw {model.name} to merch") for model in MERCHANT_SELLABLE]
    children += [
        # No talk_to_npc and no MerchantWindow.IsOpen here: the FSM-era coordinates
        # point at an NPC that never streams merchant stock, and the BtnBuy frame
        # check never matches (both observed live). Walk to the spot, then let the
        # stock list prove which nearby NPC is the merchant.
        BT.Movement.Move(x=MERCHANT_XY[0], y=MERCHANT_XY[1], tolerance=Range.Touch.value, log=True),
        open_merchant_by_stock(),
    ]
    children += [merch_sell_items(model) for model in MERCHANT_SELLABLE]
    children += [
        ensure_storage_open(),
        deposit_items(lambda snapshot: snapshot.model_id in MERCHANT_SELLABLE, "Bank unsold merchant materials"),
    ]
    return optional(
        BehaviorTree(BehaviorTree.SequenceNode(name="Merchant", children=children)),
        "Merchant selling",
    )


def print_consets() -> BehaviorTree:
    cycle = BehaviorTree.SequenceNode(
        name="Print Cycle",
        children=[
            plan_consets(),
            ensure_storage_open(),
            *[withdraw_planned_material(model) for model in PER_CONSET],
            withdraw_planned_gold(),
            *[craft_step(model, per_craft, xy) for model, per_craft, xy in CONSET_RECIPES],
        ],
    )
    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name="Print Consets",
            children=[
                BehaviorTree.RepeaterUntilFailureNode(name="Print until dry", child=cycle),
                ensure_storage_open(),
                deposit_to_bank(CONSET_MODELS, "Bank printed consets"),
                sweep_materials_to_pane(),
                deposit_to_bank(tuple(PER_CONSET), "Bank leftover conset materials"),
            ],
        )
    )


def material_balance() -> dict:
    per_material = {model: stored_material_count(model) // amount for model, amount in PER_CONSET.items()}
    gold_consets = GLOBAL_CACHE.Inventory.GetGoldInStorage() // GOLD_PER_CONSET
    printable = min(min(per_material.values()), gold_consets)
    next_cost = {}
    for model, amount in PER_CONSET.items():
        have = stored_material_count(model)
        next_cost[model] = max(0, amount * (printable + 1) - have)
    candidates = {model: cost for model, cost in next_cost.items() if cost > 0}
    best = min(candidates.items(), key=lambda entry: entry[1])[0] if candidates else None
    return {
        "per_material": per_material,
        "gold_consets": gold_consets,
        "printable": printable,
        "next_cost": next_cost,
        "best": best,
    }


def material_report() -> BehaviorTree:
    def report() -> BehaviorTree.NodeState:
        balance = material_balance()
        bottleneck = min(balance["per_material"].values())
        log_info(f"Storage can print {balance['printable']} more conset(s); gold covers {balance['gold_consets']}.")
        for model, count in balance["per_material"].items():
            marker = " (BOTTLENECK)" if count == bottleneck else ""
            log_info(f"- {model.name}: enough for {count}{marker}")
        best = balance["best"]
        if best is not None:
            log_info(f"==> Farm next: {best.name} (+{balance['next_cost'][best]} for one more conset).")
        else:
            log_info("==> Materials and gold are balanced; no farming needed.")
        return BehaviorTree.NodeState.SUCCESS

    return BehaviorTree(BehaviorTree.ActionNode(name="Material Report", action_fn=report, aftercast_ms=0))


def get_execution_steps() -> list[tuple[str, Callable[[], BehaviorTree]]]:
    return [
        ("Prepare Outpost", prepare_outpost),
        ("Bank Materials", bank_materials),
        ("Material Trader", sell_at_material_trader),
        ("Merchant", sell_at_merchant),
        ("Print Consets", print_consets),
        ("Material Report", material_report),
    ]


def ensure_botting_tree() -> BottingTree:
    global botting_tree
    if botting_tree is None:
        botting_tree = BottingTree.Create(
            MODULE_NAME,
            main_routine=get_execution_steps(),
            routine_name="ConsPrinterSequence",
            repeat=False,
            reset=False,
            auto_start=False,
            multi_account=False,
            auto_loot=False,
            isolation_enabled=False,
        )
    return botting_tree


def draw_cons_tab() -> None:
    plan = 0
    if botting_tree is not None:
        plan = int(botting_tree.tree.blackboard.get(PLAN_KEY, 0) or 0)
    PyImGui.text(f"Current print plan: {plan} conset(s)")
    PyImGui.separator()
    balance = material_balance()
    bottleneck = min(balance["per_material"].values())
    PyImGui.text(f"Storage can print: {balance['printable']} (gold covers {balance['gold_consets']})")
    for model, count in balance["per_material"].items():
        marker = "  <- bottleneck" if count == bottleneck else ""
        PyImGui.text(f"{model.name}: enough for {count}{marker}")
    PyImGui.separator()
    best = balance["best"]
    if best is not None:
        PyImGui.text(f"Farm next: {best.name} (+{balance['next_cost'][best]} for one more conset)")
    else:
        PyImGui.text("Materials and gold are balanced.")


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
        "conset_printer.png",
    )
    tree.UI.draw_window(icon_path=texture, extra_tabs=[("Cons", draw_cons_tab)])


if __name__ == "__main__":
    main()
