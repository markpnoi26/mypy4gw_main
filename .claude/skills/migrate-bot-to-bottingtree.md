---
name: migrate-bot-to-bottingtree
description: Port an old FSM `Botting()`-based bot to the `BottingTree` planner + `BTBuildMgr` rotation stack. Reference the DervCOFFarm → DervCOFFarmBT migration as the canonical example.
---

# Migrating an old FSM `Botting()` bot to `BottingTree`

Canonical example: `Scripts/py4gw-marks-corner/scripts/DervCOFFarmBT.py` (BT) replaces the deleted `Scripts/py4gw-marks-corner/scripts/DervCOFFarm.py` (FSM). Diff visible under commit `5627e8a3`.

## What the old FSM bot looked like

```python
bot = Botting(
    "COF Farmer",
    custom_build=DervBoneFarmer(),          # standard BuildMgr subclass
    ...
)
# Long chain of bot.AddStep(...), bot.Movement.Move(...), bot.Dialogs.AtXY(...) etc.
```

- Global module state (`is_farming`, `looted_areas`, timers).
- Combat rotation was a `BuildMgr` generator that queued casts through `ActionQueueManager`.
- Phases tracked by `bot.config.build_handler.status = ...`.

## Why the FSM path breaks under a planner

`BuildMgr.ProcessCombat` calls `ActionQueueManager().ResetAllQueues()` on every `CanAct`/`CanCast` failure. That queue is **process-wide** — the same singleton the `BottingTree` planner uses for movement, dialog, and interact. Under a planner, the reset stomps everything mid-tick.

## The four migrations

### 1. `BuildMgr` → `BTBuildMgr`

Custom rotation subclasses `BTBuildMgr` and overrides `build_rotation_tree() -> BehaviorTree`. Casts go through `BT.Skills.CastSkillID` → `GLOBAL_CACHE.SkillBar.UseSkill`, **bypassing ActionQueueManager entirely**. See `Core/BTBuildMgr.py` and `Core/Builds/Dervish/D_A/DervBoneFarmer.py`.

### 2. `Botting(...)` → `BottingTree.Create(...)`

```python
botting_tree = BottingTree.Create(
    MODULE_NAME,
    main_routine=[("Initialize Bot", InitializeBot),
                  ("Prepare Outpost", PrepareOutpost),
                  ("Farm Loop", FarmLoop)],
    routine_name="COFFarmSequence",
    repeat=True,
    reset=False,
    auto_start=False,
    multi_account=False,
    isolation_enabled=True,
)
botting_tree.AddBuild(get_derv_build())               # rotation as service
botting_tree.EnsurePartyWipeRecoveryService(...)      # opt-in wipe handler
```

Each planner step is a `() -> BehaviorTree` factory. Steps are Sequences of BT nodes (see below).

### 3. Turn HeroAI OFF so it doesn't fight the custom rotation

```python
bot.Config.Pacifist(auto_loot=False, pause_on_danger=False,
                    resurrection_scroll=False, multi_account=False)
```

Put it as the first child of `Initialize Bot`. Without this, HeroAI's own combat will collide with your rotation.

### 4. Yield-based utility generators stay useful — wrap them

Old util modules (`loot_utils.py`, `merch_utils.py`, `town_utils.py`) are still yield-based generators. Drop them into the planner via a small adapter:

```python
def RunGenerator(gen_factory, name="RunGenerator") -> BehaviorTree:
    state = {"gen": None}
    def tick_next(node):
        if state["gen"] is None:
            state["gen"] = gen_factory()
        try:
            next(state["gen"])
            return BehaviorTree.NodeState.RUNNING
        except StopIteration:
            state["gen"] = None
            return BehaviorTree.NodeState.SUCCESS
    return BehaviorTree(BehaviorTree.ActionNode(name=name, action_fn=tick_next, aftercast_ms=0))

# Usage: RunGenerator(withdraw_gold, name="WithdrawGold")
```

## Phase passing between planner and rotation

Old FSM: `bot.config.build_handler.status = DervBuildFarmStatus.Kill`.
BT: same — the planner writes `get_build().status = "kill"` via a small `SetPhase` node; the rotation's condition nodes read `self.status in {...}` every tick.

```python
def SetPhase(phase) -> BehaviorTree:
    def set_phase(node):
        get_derv_build().status = phase
        return BehaviorTree.NodeState.SUCCESS
    return BehaviorTree(BehaviorTree.ActionNode(name=f"SetPhase({phase})",
                                                action_fn=set_phase, aftercast_ms=0))
```

## What else you'll trip on

- **Log spam**: see `log-spam-suppression`. Every framework layer has its own `log=` knob.
- **Item ID/salvage**: don't use `AutoInventoryHandler().IDAndSalvageItems()` — see `inventory-actions`.
- **Fight-end detection**: `WaitForAreaClearOrDeath` needs an engage latch + clear hold — see `fight-clear-detection`.
- **BT primitives available in `BT.Items`, `BT.Skills`, `BT.Map`, `BT.Player`, `BT.Movement`, `BT.Party`, `BT.Skills`**. Look at `Core/routines_src/behaviourtrees_src` for the catalog.

## Migration checklist for a new bot

1. Read the old FSM file end-to-end and inventory: dialogs, moves, phase transitions, kit purchases, wipe recovery.
2. Copy the `DervCOFFarmBT.py` planner scaffold. Replace constants (map IDs, coords, dialog IDs).
3. Port the rotation from `BuildMgr` to `BTBuildMgr` if it uses one. If it uses HeroAI, keep HeroAI on and skip.
4. Turn the planner sequence into `SequenceNode` children.
5. Replace `bot.Dialogs.AtXY(x, y, dialog_id)` with `MoveTargetInteractAndDialog(...)` in a small `dialog_at()` helper (see DervCOFFarmBT).
6. Test the loop. Watch for the specific issues listed in the sibling skills.
