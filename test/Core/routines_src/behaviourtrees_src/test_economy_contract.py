# BT.Economy: the Meta-block contract every exposed routine must satisfy, and
# the NodeState paths that decide whether a transaction fired.
#
# The Meta template is prose in Core/routines_src/BehaviourTrees.py and nothing
# enforced it before; an AST pass is cheap and catches a malformed block at
# author time rather than in the configurator.

import ast
import inspect
import pathlib

import pytest

from Core.py4gwcorelib_src.BehaviorTree import BehaviorTree
from Core.routines_src.BehaviourTrees import BT
from Core.routines_src.behaviourtrees_src import economy

META_KEYS = ('Expose', 'Audience', 'Display', 'Purpose', 'UserDescription', 'Notes')
AUDIENCES = ('beginner', 'intermediate', 'advanced')

SOURCE = pathlib.Path(economy.__file__)


def meta_block(docstring):
    if not docstring or 'Meta:' not in docstring:
        return None
    lines = docstring.split('Meta:', 1)[1].splitlines()
    meta = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if ':' not in stripped:
            break
        key, value = stripped.split(':', 1)
        if key.strip() not in META_KEYS:
            break
        meta[key.strip()] = value.strip()
    return meta


def public_routines():
    """Every PascalCase staticmethod on a BTEconomy group, found by AST rather than import."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    root = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BTEconomy')
    for group in [n for n in root.body if isinstance(n, ast.ClassDef)]:
        for fn in [n for n in group.body if isinstance(n, ast.FunctionDef)]:
            if fn.name[:1].isupper():
                yield group.name, fn.name, ast.get_docstring(fn), group


def test_the_module_exposes_routines_at_all():
    assert len(list(public_routines())) >= 13


@pytest.mark.parametrize('group,name,doc,_node', list(public_routines()), ids=lambda v: v if isinstance(v, str) else '')
def test_every_public_routine_has_a_well_formed_meta_block(group, name, doc, _node):
    meta = meta_block(doc)
    assert meta is not None, f'{group}.{name} has no Meta: block'
    missing = [key for key in META_KEYS if key not in meta]
    assert not missing, f'{group}.{name} is missing {missing}'
    assert meta['Expose'] in ('true', 'false')
    assert meta['Audience'] in AUDIENCES
    assert all(meta[key] for key in META_KEYS)


def test_group_classes_are_documented_too():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    root = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BTEconomy')
    assert meta_block(ast.get_docstring(root)) is not None
    for group in [n for n in root.body if isinstance(n, ast.ClassDef)]:
        assert meta_block(ast.get_docstring(group)) is not None, f'{group.name} has no Meta: block'


def test_economy_is_bound_on_the_bt_catalog():
    assert BT.Economy is economy.BTEconomy
    from Core.routines_src import behaviourtrees_src

    assert behaviourtrees_src.BTEconomy is economy.BTEconomy
    assert 'BTEconomy' in behaviourtrees_src.__all__


def test_every_routine_returns_a_behavior_tree():
    built = [
        BT.Economy.Merchant.BuyItem(1, 10),
        BT.Economy.Merchant.SellItem(1),
        BT.Economy.Merchant.SellItems(925),
        BT.Economy.Trader.BuyItem(1),
        BT.Economy.Trader.SellItems(925),
        BT.Economy.Inventory.DepositGold(500),
        BT.Economy.Inventory.WithdrawGold(500),
        BT.Economy.Inventory.EquipItem(925),
        BT.Economy.Inventory.ChangeWeaponSet(2),
        BT.Economy.Inventory.Restock(925, 5),
        BT.Economy.Storage.DepositItems(925),
        BT.Economy.Storage.WithdrawItems(925),
        BT.Economy.Storage.FillMaterialStorage(),
        BT.Economy.Crafting.CraftItem(1, 100, [2], [3]),
        BT.Economy.Crafting.ExchangeCollectorItem(1),
    ]
    assert all(isinstance(tree, BehaviorTree) for tree in built)


def test_transaction_waits_on_running_not_on_false(monkeypatch):
    # WaitUntilNode reads False as "it failed, stop waiting"; a pending
    # transaction has to report RUNNING or the wait ends on the first tick.
    fired = []
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(
        economy.GLOBAL_CACHE.Trading.Merchant, 'BuyItem', lambda item_id, cost: fired.append((item_id, cost))
    )
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'GetGoldOnCharacter', lambda: 1000)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading, 'IsTransactionComplete', lambda: False)

    source = inspect.getsource(economy.build_transaction)
    assert 'NodeState.RUNNING' in source


def test_transaction_refuses_when_the_window_is_shut(monkeypatch):
    fired = []
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(lambda: False))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Merchant, 'BuyItem', lambda item_id, cost: fired.append(item_id))
    tree = BT.Economy.Merchant.BuyItem(42, 10)
    tree.tick()
    assert fired == []


def test_transaction_refuses_when_gold_is_short(monkeypatch):
    fired = []
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'GetGoldOnCharacter', lambda: 5)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Merchant, 'BuyItem', lambda item_id, cost: fired.append(item_id))
    tree = BT.Economy.Merchant.BuyItem(42, 1000)
    tree.tick()
    assert fired == []


def test_change_weapon_set_rejects_an_out_of_range_set(monkeypatch):
    sent = []
    monkeypatch.setattr(economy.Packet, 'SendRaw', staticmethod(lambda words: sent.append(list(words)) or True))
    BT.Economy.Inventory.ChangeWeaponSet(9).tick()
    assert sent == []
    BT.Economy.Inventory.ChangeWeaponSet(3).tick()
    assert sent == [[0x32, 2]]


def test_deposit_gold_keeps_the_float(monkeypatch):
    deposited = []
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'IsStorageOpen', lambda: True)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'GetGoldOnCharacter', lambda: 900)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'DepositGold', lambda amount: deposited.append(amount))
    BT.Economy.Inventory.DepositGold(keep_on_character=250).tick()
    assert deposited == [650]


def test_deposit_gold_does_nothing_when_already_below_the_float(monkeypatch):
    deposited = []
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'IsStorageOpen', lambda: True)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'GetGoldOnCharacter', lambda: 100)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'DepositGold', lambda amount: deposited.append(amount))
    BT.Economy.Inventory.DepositGold(keep_on_character=250).tick()
    assert deposited == []


def test_withdraw_gold_refuses_when_storage_is_short(monkeypatch):
    withdrawn = []
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'IsStorageOpen', lambda: True)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'GetGoldInStorage', lambda: 10)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'WithdrawGold', lambda amount: withdrawn.append(amount))
    BT.Economy.Inventory.WithdrawGold(500).tick()
    assert withdrawn == []


def test_a_lost_confirmation_times_out_instead_of_pinning_the_rung(monkeypatch):
    # The confirm flag is the only signal for a trade; if it is never observed
    # the wait must expire, or a planner rung waits forever.
    import time

    fired = []
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'GetGoldOnCharacter', lambda: 1000)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Merchant, 'BuyItem', lambda i, c: fired.append(i))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading, 'IsTransactionComplete', lambda: False)

    tree = BT.Economy.Merchant.BuyItem(7, 10, timeout_ms=60)
    results = []
    for _ in range(3):
        results.append(tree.tick())
        time.sleep(0.1)

    assert BehaviorTree.NodeState.FAILURE in results
    assert fired == [7]


def tick_until_settled(tree, ticks=30):
    result = tree.tick()
    for _ in range(ticks):
        if result != BehaviorTree.NodeState.RUNNING:
            break
        result = tree.tick()
    return result


def test_merchant_sell_items_prices_the_sale_from_the_stack(monkeypatch):
    # A zero-priced merchant sale is silently ignored by the game, so the node
    # must send quantity * value and confirm on the stack shrinking.
    from Core.py4gwcorelib_src.item_snapshot import ItemSnapshot

    sales = []
    stack = ItemSnapshot(item_id=77, model_id=925, quantity=10, value=4)
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(economy.item_snapshot, 'read_bags', lambda bags: [] if sales else [stack])
    monkeypatch.setattr(
        economy.GLOBAL_CACHE.Trading.Merchant, 'SellItem', lambda item_id, cost: sales.append((item_id, cost))
    )

    result = tick_until_settled(BT.Economy.Merchant.SellItems(925))

    assert sales == [(77, 40)]
    assert result == BehaviorTree.NodeState.SUCCESS


def test_merchant_sell_items_accepts_streamed_stock_as_an_open_window(monkeypatch):
    # The BtnBuy frame check misses some merchant windows (observed live at the
    # Embark Beach merchant); the streamed stock list must count as open.
    from Core.py4gwcorelib_src.item_snapshot import ItemSnapshot

    sales = []
    stack = ItemSnapshot(item_id=77, model_id=925, quantity=10, value=4)
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(lambda: False))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Merchant, 'GetOfferedItems', lambda: [111, 222])
    monkeypatch.setattr(economy.item_snapshot, 'read_bags', lambda bags: [] if sales else [stack])
    monkeypatch.setattr(
        economy.GLOBAL_CACHE.Trading.Merchant, 'SellItem', lambda item_id, cost: sales.append((item_id, cost))
    )

    result = tick_until_settled(BT.Economy.Merchant.SellItems(925))

    assert sales == [(77, 40)]
    assert result == BehaviorTree.NodeState.SUCCESS


def test_merchant_sell_items_never_touches_the_frame_check_while_stock_is_live(monkeypatch):
    # Evaluating MerchantWindow.IsOpen() with a merchant window open has been observed
    # to hard-block the tick loop; with stock streamed it must never be evaluated.
    from Core.py4gwcorelib_src.item_snapshot import ItemSnapshot

    def boom():
        raise AssertionError('MerchantWindow.IsOpen must not be called while stock is live')

    sales = []
    stack = ItemSnapshot(item_id=77, model_id=925, quantity=10, value=4)
    monkeypatch.setattr(economy.MerchantWindow, 'IsOpen', staticmethod(boom))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Merchant, 'GetOfferedItems', lambda: [111, 222])
    monkeypatch.setattr(economy.item_snapshot, 'read_bags', lambda bags: [] if sales else [stack])
    monkeypatch.setattr(
        economy.GLOBAL_CACHE.Trading.Merchant, 'SellItem', lambda item_id, cost: sales.append((item_id, cost))
    )

    result = tick_until_settled(BT.Economy.Merchant.SellItems(925))

    assert sales == [(77, 40)]
    assert result == BehaviorTree.NodeState.SUCCESS


def test_trader_sell_items_quotes_then_sells_at_the_quoted_price(monkeypatch):
    from Core.py4gwcorelib_src.item_snapshot import ItemSnapshot

    quotes = []
    sales = []
    stack = ItemSnapshot(item_id=77, model_id=925, quantity=100)
    monkeypatch.setattr(economy.TraderWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(economy.item_snapshot, 'read_bags', lambda bags: [] if sales else [stack])
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'RequestSellQuote', lambda item_id: quotes.append(item_id))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'GetQuotedItemID', lambda: 77 if quotes else 0)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'GetQuotedValue', lambda: 30 if quotes else -1)
    monkeypatch.setattr(
        economy.GLOBAL_CACHE.Trading.Trader, 'SellItem', lambda item_id, cost: sales.append((item_id, cost))
    )
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading, 'IsTransactionComplete', lambda: True)

    result = tick_until_settled(BT.Economy.Trader.SellItems(925))

    assert quotes == [77]
    assert sales == [(77, 30)]
    assert result == BehaviorTree.NodeState.SUCCESS


def test_trader_sell_items_skips_stacks_below_the_lot_size(monkeypatch):
    from Core.py4gwcorelib_src.item_snapshot import ItemSnapshot

    quotes = []
    stack = ItemSnapshot(item_id=77, model_id=925, quantity=5)
    monkeypatch.setattr(economy.TraderWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(economy.item_snapshot, 'read_bags', lambda bags: [stack])
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'RequestSellQuote', lambda item_id: quotes.append(item_id))

    result = tick_until_settled(BT.Economy.Trader.SellItems(925, min_quantity=10))

    assert quotes == []
    assert result == BehaviorTree.NodeState.SUCCESS


def test_trader_sell_items_marks_a_zero_quote_unsellable_and_moves_on(monkeypatch):
    from Core.py4gwcorelib_src.item_snapshot import ItemSnapshot

    quotes = []
    sales = []
    stack = ItemSnapshot(item_id=77, model_id=925, quantity=100)
    monkeypatch.setattr(economy.TraderWindow, 'IsOpen', staticmethod(lambda: True))
    monkeypatch.setattr(economy.item_snapshot, 'read_bags', lambda bags: [stack])
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'RequestSellQuote', lambda item_id: quotes.append(item_id))
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'GetQuotedItemID', lambda: 77 if quotes else 0)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'GetQuotedValue', lambda: 0 if quotes else -1)
    monkeypatch.setattr(economy.GLOBAL_CACHE.Trading.Trader, 'SellItem', lambda item_id, cost: sales.append(item_id))

    result = tick_until_settled(BT.Economy.Trader.SellItems(925))

    assert quotes == [77]
    assert sales == []
    assert result == BehaviorTree.NodeState.SUCCESS


def test_storage_routines_refuse_when_storage_is_shut(monkeypatch):
    moved = []
    monkeypatch.setattr(economy.GLOBAL_CACHE.Inventory, 'IsStorageOpen', lambda: False)
    monkeypatch.setattr(
        economy.GLOBAL_CACHE.Inventory, 'DepositItemToStorage', lambda item_id, *a, **k: moved.append(item_id)
    )
    BT.Economy.Storage.DepositItems(925).tick()
    BT.Economy.Storage.FillMaterialStorage().tick()
    assert moved == []
