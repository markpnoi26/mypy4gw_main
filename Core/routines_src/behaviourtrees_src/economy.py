"""
BT routines file notes
======================

Economy and logistics routines: merchants, traders, crafters, collectors,
Xunlai storage and gold. Authoring and discovery conventions are the ones
documented at the top of `items.py` — `PascalCase` for public routines,
`snake_case` for helpers, and a single-line `Meta:` block per public routine.

Nodes here are transaction-shaped: each fires one request and completes on the
observable the game actually writes, never on a fixed delay.
"""

from __future__ import annotations

from ...GlobalCache import GLOBAL_CACHE
from ...Packet import Packet
from ...Py4GWcorelib import Console, ConsoleLog
from ...UIManager import CollectorWindow, CrafterWindow, MerchantWindow, TraderWindow
from ...enums_src.Item_enums import Bags
from ...py4gwcorelib_src.BehaviorTree import BehaviorTree
from ...py4gwcorelib_src import item_snapshot
from ...py4gwcorelib_src.item_catalog import storage

TRANSACTION_TIMEOUT_MS = 5000
DEFAULT_AFTERCAST_MS = 500

INVENTORY_BAGS = [Bags.Backpack, Bags.BeltPouch, Bags.Bag1, Bags.Bag2]


def log_step(source: str, message: str, *, log: bool = False, message_type=Console.MessageType.Info) -> None:
    ConsoleLog(source, message, message_type, log=log)


def log_failure(source: str, message: str, message_type=Console.MessageType.Warning) -> None:
    ConsoleLog(source, message, message_type, log=True)


def resolve_model_id(identifier) -> int:
    from ...py4gwcorelib_src.item_identifier import model_id_of

    return model_id_of(identifier)


def first_matching_item_id(identifier) -> int:
    """Find an inventory item answering to identifier, honouring a (model, type) pair."""
    model_id = resolve_model_id(identifier)
    if model_id:
        return GLOBAL_CACHE.Inventory.GetFirstModelID(model_id)
    for snapshot in item_snapshot.read_bags(INVENTORY_BAGS):
        if snapshot.matches(identifier):
            return snapshot.item_id
    return 0


def stack_sale_value(item_id: int) -> int:
    """Merchant sales must carry the real price — the game ignores a zero-priced sale."""
    snapshot = item_snapshot.read(item_id)
    if snapshot is None:
        return 0
    return snapshot.value * snapshot.quantity


class BTEconomy:
    """
    Public BT helper group for buying, selling, storage, crafting and gold.

    Meta:
      Expose: true
      Audience: advanced
      Display: Economy
      Purpose: Group public BT routines for merchant, trader, crafter, collector, storage and gold flows.
      UserDescription: Built-in BT helper group for economy and logistics routines.
      Notes: Public `PascalCase` methods in this class are discovery candidates when marked exposed.
    """

    class Merchant:
        """
        Merchant buying and selling.

        Meta:
          Expose: true
          Audience: intermediate
          Display: Merchant
          Purpose: Group merchant transaction routines.
          UserDescription: Buy from and sell to a standard merchant.
          Notes: Every routine requires the merchant window to be open.
        """

        @staticmethod
        def BuyItem(item_id: int, cost: int, timeout_ms: int = TRANSACTION_TIMEOUT_MS, log: bool = False):
            """
            Build a tree that buys one offered item from an open merchant.

            Meta:
              Expose: true
              Audience: beginner
              Display: Buy From Merchant
              Purpose: Buy a single offered item from the merchant for a known price.
              UserDescription: Use this when the merchant window is open and you know the item and its cost.
              Notes: Completes when the transaction is confirmed; fails if the window is shut or gold is short.
            """
            return build_transaction(
                name=f"Merchant.BuyItem({item_id})",
                is_ready=MerchantWindow.IsOpen,
                fire=lambda: GLOBAL_CACHE.Trading.Merchant.BuyItem(item_id, cost),
                can_afford=lambda: GLOBAL_CACHE.Inventory.GetGoldOnCharacter() >= cost,
                timeout_ms=timeout_ms,
                log=log,
            )

        @staticmethod
        def SellItem(item_id: int, cost: int = 0, timeout_ms: int = TRANSACTION_TIMEOUT_MS, log: bool = False):
            """
            Build a tree that sells one inventory item to an open merchant.

            Meta:
              Expose: true
              Audience: beginner
              Display: Sell To Merchant
              Purpose: Sell a single inventory item to the merchant.
              UserDescription: Use this when the merchant window is open and you want to sell one item.
              Notes: A cost of zero is priced at quantity times item value at fire time — the game ignores zero-priced sales.
            """
            return build_transaction(
                name=f"Merchant.SellItem({item_id})",
                is_ready=MerchantWindow.IsOpen,
                fire=lambda: GLOBAL_CACHE.Trading.Merchant.SellItem(item_id, cost or stack_sale_value(item_id)),
                timeout_ms=timeout_ms,
                log=log,
            )

        @staticmethod
        def SellItems(identifier, timeout_ms: int = TRANSACTION_TIMEOUT_MS, log: bool = False):
            """
            Build a tree that sells every inventory item matching an identifier.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Sell Items To Merchant
              Purpose: Sell all inventory items matching a model id, name or (model, type) pair.
              UserDescription: Use this to clear a whole stack or item kind at the merchant.
              Notes: Each sale is priced at quantity times item value and confirmed on the stack shrinking; a sale the game ignores is skipped after the timeout. A streamed stock list counts as an open window, because the frame check misses some merchant windows.
            """
            name = f"Merchant.SellItems({identifier!r})"
            state: dict = {"item_id": 0, "quantity": 0, "cost": 0, "skipped": set()}

            def find_next() -> BehaviorTree.NodeState:
                # Stock first, frame check second — the order is load-bearing. Evaluating
                # MerchantWindow.IsOpen() while a merchant window is open has been observed
                # to hard-block the tick loop, so it may only run when no stock streamed
                # (window shut, where the frame check short-circuits harmlessly on exists).
                if not GLOBAL_CACHE.Trading.Merchant.GetOfferedItems() and not MerchantWindow.IsOpen():
                    log_failure(name, "Merchant window is not open.")
                    return BehaviorTree.NodeState.FAILURE
                for snapshot in item_snapshot.read_bags(INVENTORY_BAGS):
                    if snapshot.item_id in state["skipped"]:
                        continue
                    if snapshot.matches(identifier):
                        state["item_id"] = snapshot.item_id
                        state["quantity"] = snapshot.quantity
                        state["cost"] = snapshot.value * snapshot.quantity
                        return BehaviorTree.NodeState.SUCCESS
                log_step(name, f"Nothing left matching {identifier!r}.", log=log)
                return BehaviorTree.NodeState.FAILURE

            def fire_sale() -> BehaviorTree.NodeState:
                log_step(name, f"Selling {state['quantity']} of item {state['item_id']} for {state['cost']}g.", log=log)
                GLOBAL_CACHE.Trading.Merchant.SellItem(state["item_id"], state["cost"])
                return BehaviorTree.NodeState.SUCCESS

            def stack_shrunk() -> BehaviorTree.NodeState:
                for snapshot in item_snapshot.read_bags(INVENTORY_BAGS):
                    if snapshot.item_id == state["item_id"]:
                        if snapshot.quantity < state["quantity"]:
                            return BehaviorTree.NodeState.SUCCESS
                        return BehaviorTree.NodeState.RUNNING
                return BehaviorTree.NodeState.SUCCESS

            def skip_stuck() -> BehaviorTree.NodeState:
                state["skipped"].add(state["item_id"])
                log_failure(name, f"Sale of item {state['item_id']} was not observed; skipping it.")
                return BehaviorTree.NodeState.SUCCESS

            one_sale = BehaviorTree.SequenceNode(
                name=f"{name}.Sale",
                children=[
                    BehaviorTree.ActionNode(name=f"{name}.FindNext", action_fn=find_next),
                    BehaviorTree.ActionNode(name=f"{name}.Sell", action_fn=fire_sale),
                    BehaviorTree.SelectorNode(
                        name=f"{name}.Confirm",
                        children=[
                            BehaviorTree.WaitUntilNode(
                                condition_fn=stack_shrunk, timeout_ms=timeout_ms, name=f"{name}.AwaitShrink"
                            ),
                            BehaviorTree.ActionNode(name=f"{name}.Skip", action_fn=skip_stuck),
                        ],
                    ),
                ],
            )
            return BehaviorTree(BehaviorTree.RepeaterUntilFailureNode(child=one_sale, name=name))

    class Trader:
        """
        Rare-material and rune trader flows, which quote before they trade.

        Meta:
          Expose: true
          Audience: intermediate
          Display: Trader
          Purpose: Group trader quote-then-buy routines.
          UserDescription: Buy from and sell to a trader.
          Notes: A trader transaction is two steps — request a quote, then act on the quoted price.
        """

        @staticmethod
        def BuyItem(item_id: int, timeout_ms: int = TRANSACTION_TIMEOUT_MS, log: bool = False):
            """
            Build a tree that quotes an item at an open trader and buys it at the quoted price.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Buy From Trader
              Purpose: Request a quote for an offered item and buy it once the price arrives.
              UserDescription: Use this at a rare material or rune trader; the price is read, never guessed.
              Notes: Fails when the quote does not arrive in time or the quoted price exceeds gold on hand.
            """
            name = f"Trader.BuyItem({item_id})"

            def request_quote() -> BehaviorTree.NodeState:
                if not TraderWindow.IsOpen():
                    log_failure(name, "Trader window is not open.")
                    return BehaviorTree.NodeState.FAILURE
                GLOBAL_CACHE.Trading.Trader.RequestQuote(item_id)
                return BehaviorTree.NodeState.SUCCESS

            def quote_arrived() -> BehaviorTree.NodeState:
                if GLOBAL_CACHE.Trading.Trader.GetQuotedItemID() != item_id:
                    return BehaviorTree.NodeState.RUNNING
                if GLOBAL_CACHE.Trading.Trader.GetQuotedValue() <= 0:
                    return BehaviorTree.NodeState.RUNNING
                return BehaviorTree.NodeState.SUCCESS

            def buy_at_quote() -> BehaviorTree.NodeState:
                cost = GLOBAL_CACHE.Trading.Trader.GetQuotedValue()
                if GLOBAL_CACHE.Inventory.GetGoldOnCharacter() < cost:
                    log_failure(name, f"Quoted {cost}g, more than gold on hand.")
                    return BehaviorTree.NodeState.FAILURE
                log_step(name, f"Buying {item_id} at quoted {cost}g.", log=log)
                GLOBAL_CACHE.Trading.Trader.BuyItem(item_id, cost)
                return BehaviorTree.NodeState.SUCCESS

            def confirmed() -> BehaviorTree.NodeState:
                if GLOBAL_CACHE.Trading.IsTransactionComplete():
                    return BehaviorTree.NodeState.SUCCESS
                return BehaviorTree.NodeState.RUNNING

            return BehaviorTree(
                BehaviorTree.SequenceNode(
                    name=name,
                    children=[
                        BehaviorTree.ActionNode(name=f"{name}.RequestQuote", action_fn=request_quote),
                        BehaviorTree.WaitUntilNode(
                            condition_fn=quote_arrived, timeout_ms=timeout_ms, name=f"{name}.AwaitQuote"
                        ),
                        BehaviorTree.ActionNode(name=f"{name}.Buy", action_fn=buy_at_quote),
                        BehaviorTree.WaitUntilNode(
                            condition_fn=confirmed, timeout_ms=timeout_ms, name=f"{name}.Confirm"
                        ),
                    ],
                )
            )

        @staticmethod
        def SellItems(identifier, min_quantity: int = 1, timeout_ms: int = TRANSACTION_TIMEOUT_MS, log: bool = False):
            """
            Build a tree that quote-sells every matching inventory stack at an open trader.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Sell Items To Trader
              Purpose: Quote and sell all inventory items matching a model id, name or (model, type) pair.
              UserDescription: Use this at a material or rune trader to sell off everything of one kind.
              Notes: Material traders trade in lots, so min_quantity skips stacks too small to sell; a zero quote marks the stack unsellable and moves on.
            """
            name = f"Trader.SellItems({identifier!r})"
            state: dict = {"item_id": 0, "fired": False, "unsellable": set()}

            def find_next() -> BehaviorTree.NodeState:
                if not TraderWindow.IsOpen():
                    log_failure(name, "Trader window is not open.")
                    return BehaviorTree.NodeState.FAILURE
                state["fired"] = False
                for snapshot in item_snapshot.read_bags(INVENTORY_BAGS):
                    if snapshot.item_id in state["unsellable"] or snapshot.quantity < min_quantity:
                        continue
                    if snapshot.matches(identifier):
                        state["item_id"] = snapshot.item_id
                        return BehaviorTree.NodeState.SUCCESS
                log_step(name, f"Nothing left matching {identifier!r} to quote.", log=log)
                return BehaviorTree.NodeState.FAILURE

            def request_quote() -> BehaviorTree.NodeState:
                GLOBAL_CACHE.Trading.Trader.RequestSellQuote(state["item_id"])
                return BehaviorTree.NodeState.SUCCESS

            def quote_arrived() -> BehaviorTree.NodeState:
                if GLOBAL_CACHE.Trading.Trader.GetQuotedItemID() != state["item_id"]:
                    return BehaviorTree.NodeState.RUNNING
                if GLOBAL_CACHE.Trading.Trader.GetQuotedValue() < 0:
                    return BehaviorTree.NodeState.RUNNING
                return BehaviorTree.NodeState.SUCCESS

            def sell_at_quote() -> BehaviorTree.NodeState:
                value = GLOBAL_CACHE.Trading.Trader.GetQuotedValue()
                if value <= 0:
                    state["unsellable"].add(state["item_id"])
                    log_step(name, f"Trader quoted nothing for item {state['item_id']}; skipping it.", log=log)
                    return BehaviorTree.NodeState.SUCCESS
                log_step(name, f"Selling item {state['item_id']} at quoted {value}g.", log=log)
                GLOBAL_CACHE.Trading.Trader.SellItem(state["item_id"], value)
                state["fired"] = True
                return BehaviorTree.NodeState.SUCCESS

            def confirmed() -> BehaviorTree.NodeState:
                if not state["fired"]:
                    return BehaviorTree.NodeState.SUCCESS
                if GLOBAL_CACHE.Trading.IsTransactionComplete():
                    return BehaviorTree.NodeState.SUCCESS
                return BehaviorTree.NodeState.RUNNING

            one_sale = BehaviorTree.SequenceNode(
                name=f"{name}.Sale",
                children=[
                    BehaviorTree.ActionNode(name=f"{name}.FindNext", action_fn=find_next),
                    BehaviorTree.ActionNode(name=f"{name}.RequestQuote", action_fn=request_quote),
                    BehaviorTree.WaitUntilNode(
                        condition_fn=quote_arrived, timeout_ms=timeout_ms, name=f"{name}.AwaitQuote"
                    ),
                    BehaviorTree.ActionNode(name=f"{name}.Sell", action_fn=sell_at_quote),
                    BehaviorTree.WaitUntilNode(condition_fn=confirmed, timeout_ms=timeout_ms, name=f"{name}.Confirm"),
                ],
            )
            return BehaviorTree(BehaviorTree.RepeaterUntilFailureNode(child=one_sale, name=name))

    class Inventory:
        """
        Gold, equipment and weapon sets.

        Meta:
          Expose: true
          Audience: intermediate
          Display: Inventory
          Purpose: Group gold movement, equipping and weapon-set routines.
          UserDescription: Manage gold, equip items and switch weapon sets.
          Notes: Gold routines require the Xunlai storage window to be open.
        """

        @staticmethod
        def DepositGold(keep_on_character: int = 0, log: bool = False):
            """
            Build a tree that deposits gold, optionally keeping a float on the character.

            Meta:
              Expose: true
              Audience: beginner
              Display: Deposit Gold
              Purpose: Move gold from the character into Xunlai storage.
              UserDescription: Use this to bank gold, keeping only what you want to carry.
              Notes: Succeeds without acting when the character already carries no more than the float.
            """

            def deposit() -> BehaviorTree.NodeState:
                if not GLOBAL_CACHE.Inventory.IsStorageOpen():
                    log_failure("Economy.DepositGold", "Xunlai storage is not open.")
                    return BehaviorTree.NodeState.FAILURE
                on_character = GLOBAL_CACHE.Inventory.GetGoldOnCharacter()
                amount = on_character - max(0, keep_on_character)
                if amount <= 0:
                    return BehaviorTree.NodeState.SUCCESS
                log_step("Economy.DepositGold", f"Depositing {amount}g.", log=log)
                GLOBAL_CACHE.Inventory.DepositGold(amount)
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"Inventory.DepositGold(keep={keep_on_character})",
                    action_fn=deposit,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

        @staticmethod
        def WithdrawGold(amount: int, log: bool = False):
            """
            Build a tree that withdraws gold from Xunlai storage.

            Meta:
              Expose: true
              Audience: beginner
              Display: Withdraw Gold
              Purpose: Move gold from Xunlai storage onto the character.
              UserDescription: Use this before a purchase that needs more gold than you are carrying.
              Notes: Fails when storage holds less than the requested amount.
            """

            def withdraw() -> BehaviorTree.NodeState:
                if not GLOBAL_CACHE.Inventory.IsStorageOpen():
                    log_failure("Economy.WithdrawGold", "Xunlai storage is not open.")
                    return BehaviorTree.NodeState.FAILURE
                if amount <= 0:
                    return BehaviorTree.NodeState.SUCCESS
                if GLOBAL_CACHE.Inventory.GetGoldInStorage() < amount:
                    log_failure("Economy.WithdrawGold", f"Storage holds less than {amount}g.")
                    return BehaviorTree.NodeState.FAILURE
                log_step("Economy.WithdrawGold", f"Withdrawing {amount}g.", log=log)
                GLOBAL_CACHE.Inventory.WithdrawGold(amount)
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"Inventory.WithdrawGold({amount})",
                    action_fn=withdraw,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

        @staticmethod
        def EquipItem(identifier, log: bool = False):
            """
            Build a tree that equips an inventory item by model id, name or (model, type) pair.

            Meta:
              Expose: true
              Audience: beginner
              Display: Equip Item
              Purpose: Equip a matching item from the inventory.
              UserDescription: Use this to put on a weapon, shield or armour piece you are carrying.
              Notes: Fails when nothing in the inventory answers to the identifier.
            """

            def equip() -> BehaviorTree.NodeState:
                from ...Player import Player

                item_id = first_matching_item_id(identifier)
                if item_id == 0:
                    log_failure("Economy.EquipItem", f"No inventory item matching {identifier!r}.")
                    return BehaviorTree.NodeState.FAILURE
                log_step("Economy.EquipItem", f"Equipping item {item_id}.", log=log)
                GLOBAL_CACHE.Inventory.EquipItem(item_id, Player.GetAgentID())
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree(
                BehaviorTree.ActionNode(name=f"Inventory.EquipItem({identifier!r})", action_fn=equip, aftercast_ms=750)
            )

        @staticmethod
        def ChangeWeaponSet(set_number: int, log: bool = False):
            """
            Build a tree that switches to weapon set 1 through 4.

            Meta:
              Expose: true
              Audience: beginner
              Display: Change Weapon Set
              Purpose: Switch the active weapon set.
              UserDescription: Use this to swap weapons mid-run, for example onto a shield set.
              Notes: Sends the equip-set packet directly, so it works without the weapon bar being visible.
            """

            def change() -> BehaviorTree.NodeState:
                if not Packet.ChangeWeaponSet(set_number):
                    log_failure("Economy.ChangeWeaponSet", f"Weapon set {set_number} is out of range 1-4.")
                    return BehaviorTree.NodeState.FAILURE
                log_step("Economy.ChangeWeaponSet", f"Switched to weapon set {set_number}.", log=log)
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"Inventory.ChangeWeaponSet({set_number})",
                    action_fn=change,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

        @staticmethod
        def Restock(identifier, target_quantity: int, log: bool = False):
            """
            Build a tree that tops an item up to a target quantity from Xunlai storage.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Restock From Storage
              Purpose: Withdraw enough of an item to reach a target inventory quantity.
              UserDescription: Use this to refill consumables or kits before a run.
              Notes: Succeeds without acting when the inventory already holds the target.
            """

            def restock() -> BehaviorTree.NodeState:
                if not GLOBAL_CACHE.Inventory.IsStorageOpen():
                    log_failure("Economy.Restock", "Xunlai storage is not open.")
                    return BehaviorTree.NodeState.FAILURE
                model_id = resolve_model_id(identifier)
                if not model_id:
                    log_failure("Economy.Restock", f"{identifier!r} does not resolve to a model id.")
                    return BehaviorTree.NodeState.FAILURE
                held = GLOBAL_CACHE.Inventory.GetModelCount(model_id)
                missing = target_quantity - held
                if missing <= 0:
                    return BehaviorTree.NodeState.SUCCESS
                if GLOBAL_CACHE.Inventory.GetModelCountInStorage(model_id) <= 0:
                    log_failure("Economy.Restock", f"Storage holds no model {model_id}.")
                    return BehaviorTree.NodeState.FAILURE
                log_step("Economy.Restock", f"Withdrawing {missing} of model {model_id}.", log=log)
                GLOBAL_CACHE.Inventory.WithdrawItemFromStorageByModelID(model_id, missing)
                return BehaviorTree.NodeState.RUNNING

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"Inventory.Restock({identifier!r}, {target_quantity})",
                    action_fn=restock,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

    class Storage:
        """
        Xunlai storage transfers.

        Meta:
          Expose: true
          Audience: intermediate
          Display: Storage
          Purpose: Group Xunlai deposit and withdraw routines.
          UserDescription: Move items between the inventory and Xunlai storage.
          Notes: Every routine requires the Xunlai storage window to be open.
        """

        @staticmethod
        def DepositItems(identifier, log: bool = False):
            """
            Build a tree that deposits every inventory item matching an identifier.

            Meta:
              Expose: true
              Audience: beginner
              Display: Deposit Items
              Purpose: Move all matching inventory items into Xunlai storage.
              UserDescription: Use this to bank a stack, a material or an item kind.
              Notes: Deposits one item per tick so each transfer settles before the next.
            """

            def deposit_one() -> BehaviorTree.NodeState:
                if not GLOBAL_CACHE.Inventory.IsStorageOpen():
                    log_failure("Economy.DepositItems", "Xunlai storage is not open.")
                    return BehaviorTree.NodeState.FAILURE
                item_id = first_matching_item_id(identifier)
                if item_id == 0:
                    return BehaviorTree.NodeState.SUCCESS
                log_step("Economy.DepositItems", f"Depositing item {item_id}.", log=log)
                GLOBAL_CACHE.Inventory.DepositItemToStorage(item_id)
                return BehaviorTree.NodeState.RUNNING

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"Storage.DepositItems({identifier!r})",
                    action_fn=deposit_one,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

        @staticmethod
        def WithdrawItems(identifier, quantity: int = -1, log: bool = False):
            """
            Build a tree that withdraws an item from Xunlai storage.

            Meta:
              Expose: true
              Audience: beginner
              Display: Withdraw Items
              Purpose: Move a stored item into the inventory.
              UserDescription: Use this to pull materials or consumables out of storage.
              Notes: A quantity of -1 withdraws the whole stack.
            """

            def withdraw() -> BehaviorTree.NodeState:
                if not GLOBAL_CACHE.Inventory.IsStorageOpen():
                    log_failure("Economy.WithdrawItems", "Xunlai storage is not open.")
                    return BehaviorTree.NodeState.FAILURE
                model_id = resolve_model_id(identifier)
                if not model_id:
                    log_failure("Economy.WithdrawItems", f"{identifier!r} does not resolve to a model id.")
                    return BehaviorTree.NodeState.FAILURE
                if GLOBAL_CACHE.Inventory.GetModelCountInStorage(model_id) <= 0:
                    log_failure("Economy.WithdrawItems", f"Storage holds no model {model_id}.")
                    return BehaviorTree.NodeState.FAILURE
                log_step("Economy.WithdrawItems", f"Withdrawing model {model_id}.", log=log)
                GLOBAL_CACHE.Inventory.WithdrawItemFromStorageByModelID(model_id, quantity)
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name=f"Storage.WithdrawItems({identifier!r})",
                    action_fn=withdraw,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

        @staticmethod
        def FillMaterialStorage(log: bool = False):
            """
            Build a tree that deposits every crafting material carried in the inventory.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Fill Material Storage
              Purpose: Move all carried crafting materials into material storage.
              UserDescription: Use this after a farm run to bank everything salvage produced.
              Notes: Materials are recognised from the shipped storage-pane map, one deposit per tick.
            """

            def deposit_next_material() -> BehaviorTree.NodeState:
                if not GLOBAL_CACHE.Inventory.IsStorageOpen():
                    log_failure("Economy.FillMaterialStorage", "Xunlai storage is not open.")
                    return BehaviorTree.NodeState.FAILURE
                for snapshot in item_snapshot.read_bags(INVENTORY_BAGS):
                    if storage.is_material(snapshot.model_id):
                        log_step(
                            "Economy.FillMaterialStorage",
                            f"Depositing {snapshot.name or snapshot.model_id}.",
                            log=log,
                        )
                        GLOBAL_CACHE.Inventory.DepositItemToStorage(snapshot.item_id)
                        return BehaviorTree.NodeState.RUNNING
                return BehaviorTree.NodeState.SUCCESS

            return BehaviorTree(
                BehaviorTree.ActionNode(
                    name="Storage.FillMaterialStorage",
                    action_fn=deposit_next_material,
                    aftercast_ms=DEFAULT_AFTERCAST_MS,
                )
            )

    class Crafting:
        """
        Crafting and collector exchanges, which both trade ingredients for an output.

        Meta:
          Expose: true
          Audience: intermediate
          Display: Crafting
          Purpose: Group crafter and collector exchange routines.
          UserDescription: Craft items and hand trophies to collectors.
          Notes: Ingredient lists are passed straight through to the game's trade call.
        """

        @staticmethod
        def CraftItem(
            item_id: int,
            cost: int,
            trade_item_ids: list[int],
            trade_quantities: list[int],
            timeout_ms: int = TRANSACTION_TIMEOUT_MS,
            log: bool = False,
        ):
            """
            Build a tree that crafts an item at an open crafter.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Craft Item
              Purpose: Trade gold and materials to a crafter for a crafted item.
              UserDescription: Use this at an armour or weapon crafter once you have the materials.
              Notes: Fails when the crafter window is shut or gold on hand is short of the cost.
            """
            return build_transaction(
                name=f"Crafting.CraftItem({item_id})",
                is_ready=CrafterWindow.IsOpen,
                fire=lambda: GLOBAL_CACHE.Trading.Crafter.CraftItem(item_id, cost, trade_item_ids, trade_quantities),
                can_afford=lambda: GLOBAL_CACHE.Inventory.GetGoldOnCharacter() >= cost,
                timeout_ms=timeout_ms,
                log=log,
            )

        @staticmethod
        def ExchangeCollectorItem(
            item_id: int,
            cost: int = 0,
            trade_item_ids: list[int] | None = None,
            trade_quantities: list[int] | None = None,
            timeout_ms: int = TRANSACTION_TIMEOUT_MS,
            log: bool = False,
        ):
            """
            Build a tree that exchanges trophies with an open collector.

            Meta:
              Expose: true
              Audience: intermediate
              Display: Exchange With Collector
              Purpose: Hand a collector the required trophies and take the offered item.
              UserDescription: Use this at a collector once you carry the trophies it asks for.
              Notes: Fails when the collector window is shut.
            """
            return build_transaction(
                name=f"Crafting.ExchangeCollectorItem({item_id})",
                is_ready=CollectorWindow.IsOpen,
                fire=lambda: GLOBAL_CACHE.Trading.Collector.ExchangeItem(
                    item_id, cost, trade_item_ids or [], trade_quantities or []
                ),
                timeout_ms=timeout_ms,
                log=log,
            )


def build_transaction(name, is_ready, fire, timeout_ms, can_afford=None, log=False):
    """Fire a trade once, then wait on the game's own transaction flag.

    The flag is the one signal the client writes for every trade kind, so
    completion latches on it rather than on a delay. The wait is a
    WaitUntilNode, which pins the sequence to this rung until the flag is
    observed, and the pending state returns RUNNING — returning False there
    would read as "the trade failed" and stop the wait immediately.
    """

    def fire_once() -> BehaviorTree.NodeState:
        if not is_ready():
            log_failure(name, "The trade window is not open.")
            return BehaviorTree.NodeState.FAILURE
        if can_afford is not None and not can_afford():
            log_failure(name, "Not enough gold on hand.")
            return BehaviorTree.NodeState.FAILURE
        log_step(name, "Firing transaction.", log=log)
        fire()
        return BehaviorTree.NodeState.SUCCESS

    def confirmed() -> BehaviorTree.NodeState:
        if GLOBAL_CACHE.Trading.IsTransactionComplete():
            log_step(name, "Transaction confirmed.", log=log)
            return BehaviorTree.NodeState.SUCCESS
        return BehaviorTree.NodeState.RUNNING

    return BehaviorTree(
        BehaviorTree.SequenceNode(
            name=name,
            children=[
                BehaviorTree.ActionNode(name=f"{name}.Fire", action_fn=fire_once),
                BehaviorTree.WaitUntilNode(condition_fn=confirmed, timeout_ms=timeout_ms, name=f"{name}.Confirm"),
            ],
        )
    )
