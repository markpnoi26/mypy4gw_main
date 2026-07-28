---
name: log-spam-suppression
description: Where per-tick log spam comes from in a BottingTree + BTBuildMgr bot, and the exact knob to silence each source. Framework layers each ship their own `log=` gates.
---

# Silencing log spam in a BottingTree bot

The framework has multiple layers, each with its own log emission. Silence them one-by-one at their source rather than trying a global mute.

## Sources and knobs (in order of noise)

### Cast logs (per skill dispatch, dozens/sec during combat)

`BT.Skills.CastSkillID(skill_id, aftercast_delay=ms, log=True)` fires `[CastSkillID] Cast X` on every dispatch. Default your rotation helpers to `log=False`:

```python
def cast_gated(self, name, skill_id, gate_fn, aftercast_ms):
    return sequence(f"Cast:{name}",
        condition(f"Gate:{name}", gate_fn),
        BT.Skills.CastSkillID(skill_id, aftercast_delay=aftercast_ms, log=False),
    )
```

### "Upkeep tree returned SUCCESS" (once per frame per service)

Framework logs every service-tree tick that resolves to SUCCESS/FAILURE. For a rotation that always resolves (matches a branch or falls through to `Idle`), that's ~60 lines/sec.

**Silence at source** — comment out the log block in `Core/botting_tree_src/ticks.py`, roughly lines 243-248:

```python
# if service_result in (BehaviorTree.NodeState.SUCCESS, BehaviorTree.NodeState.FAILURE):
#     PySystem.Console.Log('BottingTree', f"Upkeep tree '{service_name}' returned ...", ...)
if service_result in (BehaviorTree.NodeState.SUCCESS, BehaviorTree.NodeState.FAILURE):
    service_tree.reset()   # keep this — tree state MUST reset
```

**Do NOT wrap the rotation in `RepeaterForeverNode`** to fix this. That kills the framework reset and breaks aftercast semantics — see `bt-rotation-authoring`.

### Loot pickup log (per item picked up)

`Routines.Yield.Items.LootItemsWithMaxAttempts(items, log=True)` prints per-item lines. In our `LootFilteredItems()` wrapper set `log=False`:

```python
yield from Routines.Yield.Items.LootItemsWithMaxAttempts(filtered, log=False)
```

### Planner step logs (once per step, generally fine)

`BT.Map.TravelToOutpost(..., log=True)`, `BT.Player.Move(..., log=True)`, `BT.Party.Resign(log=True)`, `BT.Map.WaitforMapLoad(..., log=True)`, `_dialog_at(..., log=True)` — one line per step. Useful for tracing planner progress; leave `log=True` unless you're debugging noise from a specific step.

### Identify/salvage diagnostics I added while debugging

`BT.Items.IdentifyInventoryItems` and `SalvageInventoryItems` currently emit:
- `Auto-collected N unidentified/salvageable items ...`
- `Generator created; starting first tick.`
- `Generator exhausted; returning SUCCESS.`

Plus per-item `Firing salvage item_id=X ...` and `Skip item_id=X: not present.` from `SalvageItemsAndVerify`. Useful for debugging; strip once the pipeline is stable — the log block sits at the top of `tick(node)` in each wrapper (in `Core/routines_src/behaviourtrees_src/items.py`).

### Framework "Botting tree reset" / "Headless HeroAI is disabled" (once per state change / periodic)

Baked into the framework's `_tick_heroai` / lifecycle. Fire only on state transitions (Pacifist keeps HeroAI disabled → periodic "disabled" logs). Suppress the "disabled" spam by editing `_tick_heroai` if you really need to, but it's typically ~1 line every 5s — livable.

## Style rule reminder

New `log=` defaults you introduce should follow the repo style — `log: bool = False`, not `log: bool = True`. Only flip to `True` when the caller opts in for a specific debug pass. See `CLAUDE.md` at the repo root.
