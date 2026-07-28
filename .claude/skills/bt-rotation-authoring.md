---
name: bt-rotation-authoring
description: Authoring combat rotations as BehaviorTrees under `BTBuildMgr`. Covers Selector/Sequence semantics, aftercast timing, the `optional()` wrapper, RepeaterForever footguns, and framework state reset.
---

# BT rotation authoring

Reference: `Core/Builds/Dervish/D_A/DervBoneFarmer.py`.

## The base contract

```python
class MyBuild(BTBuildMgr):
    def build_rotation_tree(self) -> BehaviorTree:
        return selector("MyRotation",
            self.outpost_guard(),
            self.non_combat_guard(),
            self.combat_branch(),
            succeeder("Idle"),
        )
```

- One `Selector` at the root — first branch that returns SUCCESS wins.
- A trailing `succeeder("Idle")` so we never bubble FAILURE.
- Casts go through `BT.Skills.CastSkillID(skill_id, aftercast_delay=ms, log=False)` — that dispatches `GLOBAL_CACHE.SkillBar.UseSkill` directly and does NOT touch `ActionQueueManager`.

## Node primitive semantics

| Node | Behavior |
|---|---|
| `SequenceNode` | Ticks children in order. First `FAILURE` short-circuits and returns FAILURE. First `RUNNING` returns RUNNING (resumes at that child next tick). Only all-SUCCESS → SUCCESS. |
| `SelectorNode` | Ticks children in order. First `SUCCESS` short-circuits. First `RUNNING` returns RUNNING. Only all-FAILURE → FAILURE. |
| `ActionNode(fn, aftercast_ms)` | Calls `fn`, holds RUNNING for `aftercast_ms` after it returns SUCCESS/FAILURE, then propagates the original result. Self-resets on final. |
| `ConditionNode(fn)` | Returns SUCCESS/FAILURE based on `fn()`. Never RUNNING. |
| `SucceederNode` | Leaf, always SUCCESS. Handy trailing fallback. |
| `RepeaterForeverNode(child)` | Ticks child, discards its result, always returns RUNNING. See footgun below. |

## The `optional()` wrapper — absorb FAILURE from a Sequence child

Every `SwapToWeapon` sub-sequence is `Condition(NeedsSwap) → Action(PressKey)`. When we don't need the swap, the Condition returns FAILURE, which would abort the parent Sequence and skip everything after. Wrap in a Selector-with-succeeder to convert FAILURE to SUCCESS:

```python
def optional(cast_tree, name="Optional"):
    return selector(name, cast_tree, succeeder(f"{name}:Skip"))

# Usage inside an Engagement Sequence:
optional(self.swap_to_scythe(), name="OptionalScytheSwap"),
action("InteractNearest", interact_nearest, aftercast_ms=100),
```

Without the wrap, "already on scythe" causes the whole engagement to abort before we ever call `Interact`.

## Aftercast_ms — what it actually does

The `aftercast_ms` on an ActionNode makes the node stay RUNNING for that duration after the action returns. **It doesn't wait for the game to process the action** — it's a BT-side pause. Two knobs matter:

1. `BT.Skills.CastSkillID(aftercast_delay=ms)` → this passes into `SkillBar.UseSkill(..., aftercast_delay=ms)` — the game-engine hint.
2. The `aftercast_ms` on the containing ActionNode (used automatically by `CastSkillID`) — pure BT pause.

Cast animations vary. 100 ms is fine for stances/signets; spells/attack skills typically need 200–500 ms. Too tight and you queue up packets faster than the game processes them (see `fire-then-verify-pattern`).

## The RepeaterForever footgun

Tempting fix for the framework log `"Upkeep tree returned SUCCESS"` spam: wrap the whole rotation in `RepeaterForeverNode` so the outer tree always returns RUNNING. **Don't.** It changes state semantics:

- Without wrap: framework calls `service_tree.reset()` after every SUCCESS. Every ActionNode's aftercast state gets wiped every tick. Old rotations implicitly depend on this.
- With wrap: framework never resets. Inner state persists across ticks. Correct BT semantics, but exposes latent bugs (F1 press timing races, weapon-swap thrash) that were masked by the constant reset.

**Fix the log spam at the source instead** — comment out the log block in `Core/botting_tree_src/ticks.py:243-248`. See `log-spam-suppression`.

## Phase gating pattern

The planner writes `self.status`; the rotation gates each branch on it:

```python
NON_COMBAT_PHASES = {"setup", "loot", "wait"}

def non_combat_guard(self):
    return sequence("NonCombatGuard",
        condition("InNonCombatPhase", lambda: self.status in NON_COMBAT_PHASES),
        optional(self.swap_to_shield_set(), name="OptionalShieldSwap"),
    )
```

Also gate on `Routines.Checks.Map.IsExplorable()` — no rotation logic should fire in outposts.

## Small module-local helpers keep the tree readable

```python
def sequence(name, *children): return BehaviorTree(BehaviorTree.SequenceNode(name=name, children=list(children)))
def selector(name, *children): return BehaviorTree(BehaviorTree.SelectorNode(name=name, children=list(children)))
def condition(name, fn):       return BehaviorTree.ConditionNode(name=name, condition_fn=fn)
def action(name, fn, aftercast_ms=0): return BehaviorTree.ActionNode(name=name, action_fn=fn, aftercast_ms=aftercast_ms)
def succeeder(name):           return BehaviorTree.SucceederNode(name=name)
```

Put these at module level in the build file. Naming them `sequence`/`selector` (not `_sequence` etc.) matches the project style rule.

## Framework state reset

When a service tree returns SUCCESS or FAILURE, `_tick_service_tree` in `botting_tree_src/ticks.py` calls `service_tree.reset()`. That propagates through and clears every child's transient state (`_action_done`, `_start_time`, Sequence/Selector `_current_child_index`). Design your rotation assuming it starts fresh on every tick where it completed.

## Cast log noise

`BT.Skills.CastSkillID(..., log=True)` fires a `[CastSkillID] Cast X` line every dispatch. In a rotation that's dozens per second. Default your helpers to `log=False`:

```python
def cast_gated(self, name, skill_id, gate_fn, aftercast_ms):
    return sequence(f"Cast:{name}",
        condition(f"Gate:{name}", gate_fn),
        BT.Skills.CastSkillID(skill_id, aftercast_delay=aftercast_ms, log=False),
    )
```
