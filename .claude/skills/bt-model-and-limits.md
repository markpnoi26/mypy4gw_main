---
name: bt-model-and-limits
description: What the BehaviorTree framework actually is and what it structurally cannot do. Load before deciding whether a piece of work belongs in a BT at all, when a tree misbehaves after reset/restart, when a guard condition "isn't firing", or when designing anything that needs to interrupt a running action.
---

# BT — the mental model and the hard limits

Framework: `Core/py4gwcorelib_src/BehaviorTree.py` (2086 ln).

## The reframe

**BT here is a cooperative frame scheduler, not an AI framework.** The game gives one
callback per frame — no threads, no sleeping, no blocking. Anything spanning more than one
frame needs something to hold its place across frames. BT is that something.

That is why everything looks like BT (builds, salvage, identify, buy, travel, planner
sequencing). Not because those are decision problems — because they take more than one frame.

## What is actually under a leaf

`BT.Items.SalvageInventoryItems()` is not a tree of salvage logic. It is one `ActionNode`
pumping a generator (`routines_src/behaviourtrees_src/items.py:1963`):

```python
state = {"gen": None}                      # items.py:1981 — closure, NOT blackboard

def tick(node):
    if state["gen"] is None:
        state["gen"] = YieldItems.SalvageItemsAndVerify(item_ids, ...)
    try:
        next(state["gen"]);  return RUNNING
    except StopIteration:
        state["gen"] = None; return SUCCESS
```

Identify, buy, craft, travel, restock — same shape. The real work lives in
`routines_src/yield_src/`. **Generators do the work; BT sequences them and makes them
inspectable.** Any generator can become a leaf via this pump.

## What BT actually buys

| Capability | Where |
|---|---|
| Multi-frame sequencing with named steps + restart-from-step | `botting_tree_src/planner.py:136-195` |
| Concurrent services beside the planner (HeroAI, upkeep, wipe recovery) | `planner.py:40-77` |
| Retry / fallback / priority structure | Selector, RepeaterUntilSuccess, timeouts |
| Per-node timing, `draw()`, `print()`, `BT_TRACE` | `BehaviorTree.py:149-201`, `:342` |
| Data-driven construction from JSON / configurator | `modular/json_bt_compiler.py`, `dev/bot_factory/discovery.py` |

Introspection is the real payoff over raw generators. If you don't need it and don't need
branching, a bare generator is the simpler tool.

## The five hard limits

### 1. It is not reactive

`SequenceNode` and `SelectorNode` have **memory** — `_current_child_index` survives across
ticks (`:609,:626` / `:687,:705`). A condition to the left of a RUNNING action is **never
re-evaluated** while that action runs.

```python
sequence("Walk", condition("HealthOK", ...), action("MoveTo", ...))   # WRONG
```
`HealthOK` is checked once, then `MoveTo` returns RUNNING for 400 frames and the guard never
runs again.

Fixes: `ChoiceNode` (re-evaluates from the top every tick, `:764`), or put the check **inside
the leaf** so it can return FAILURE itself.

### 2. No cancellation / preemption

When `ChoiceNode` lets a higher-priority child win, the lower-priority child that was RUNNING
is **not reset** (`:781-784`). It keeps its `_current_child_index` and its half-consumed
generator, and resumes mid-flight later. Textbook BTs reset the abandoned branch; this one
does not. If preemption must abort work, reset that branch yourself.

### 3. Leaf state hides in closures, and `reset()` does not clear it

`ActionNode.reset()` clears only `_action_done` / `_action_result` / `_start_time` (`:488`).
The `state = {"gen": ...}` dict lives in the routine's closure and survives every reset. A
tree reset mid-salvage **resumes the old generator over a stale item list**.

This only works in practice because planner steps are declared as **callables**:

```python
# CORRECT — SubtreeNode.reset() drops the subtree (:1311), factory rebuilds a fresh closure
[('Salvage', lambda: BT.Items.SalvageInventoryItems())]
[('Initialize Bot', InitializeBot)]

# BROKEN — same instance forever, stale generator after any restart
[('Salvage', BT.Items.SalvageInventoryItems())]
```

Same reason a built subtree instance **cannot be reused in two places**.

### 4. `ParallelNode` is fail-fast and is not parallel

Any child FAILURE fails the node and resets **every sibling mid-flight** (`:1252`). Children
tick sequentially inside one frame; concurrency exists only across frames.

`BottingTree` sidesteps this by wrapping every root branch in `RepeaterForeverNode`, which
discards child results entirely (`:1183`). Consequence: services cannot report failure upward
— they signal through the blackboard (`ticks.py` writes `COMBAT_ACTIVE`, `PLANNER_STATUS`, …).

### 5. The blackboard is one flat global, re-walked every tick

`_propagate_blackboard` recurses the whole tree from the root on **every** `BehaviorTree.tick()`
(`:2045,:2059`), and `_ensure_blackboard_data` calls `Player.GetAgentID()` +
`Agent.GetProfessionNames()` on every tick of every tree (`:2032-2043`). BottingTree pays this
twice per frame (root tree + `planner_tree.tick()` at `ticks.py:212`).

No scoping, no namespacing — key collisions are silent. Treat it as a global.

## Also true

- **You own the frame.** A leaf that loops 200 items in one call stalls the client. That is
  what `per_item_delay_ms` is for in the item routines.
- **Nothing crosses accounts.** Blackboard is per-tree, per-process. Multibox coordination
  goes through ShMem / Whiteboard; nodes just read and write it.
- **Boolean algebra does not want to be a tree.** Per-skill trees for 3433 skills would be
  predicates in node costumes, paying allocation + propagation for zero scheduling benefit.
  Structure in the tree, conditions as data tables — see `heroai-bt-engine`.
- **A leaf returning `None` raises TypeError** (`:164-168`), and any exception in a node kills
  the whole frame's tick.

## Decision rule

| Shape of the work | Use |
|---|---|
| Single frame, no waiting | plain function — not a node |
| Linear multi-frame, no branching | one generator in one `ActionNode` (what the library itself does) |
| Branching, retry, priority, or must run beside something else | BT earns its keep |
| Must interrupt a running action | BT fights you — put the check inside the leaf |

## Where BT is correctly absent

ImGui drawing, packet listeners / event hooks, pure computation (pathing, targeting
selection, item DB), and the legacy `BottingClass` coroutine framework
(`Core/Botting.py`, still valid — see `migrate-bot-to-bottingtree`).

## Sibling skills

- `bt-rotation-authoring` — node primitives, `optional()`, aftercast, service reset
- `heroai-bt-engine` — the HeroAI combat engine built on this
- `migrate-bot-to-bottingtree` — porting FSM bots onto the planner
