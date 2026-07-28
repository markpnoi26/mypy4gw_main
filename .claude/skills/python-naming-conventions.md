---
name: python-naming-conventions
description: snake_case for every function, method, and variable in code we own — PascalCase is reserved for classes and for framework APIs we don't own. Read before writing or editing any Python in `Bots/`, `Widgets/`, or `Core/Builds/`.
---

# Python naming conventions

Pairs with the no-underscore / no-comment-spam rules in `CLAUDE.md`. Those cover *decoration*; this covers *casing*.

## The rule

| Kind | Casing | Example |
|---|---|---|
| Function (module-level, nested, closure) | `snake_case` | `wait_for_area_clear_or_death` |
| Method on a class we define | `snake_case` | `def build_rotation_tree(self)` |
| Local / module variable | `snake_case` | `engage_pool`, `botting_tree` |
| Module-level constant | `UPPER_SNAKE` | `COF_ATTACK_SPOT_1`, `MERCHANT_DIALOG` |
| Class we define | `PascalCase` | `class DervBoneFarmer` |
| Framework API we call | *whatever it already is* | `BT.Map.TravelToOutpost`, `GLOBAL_CACHE.Inventory.GetModelCount` |

The distinction is **ownership**, not location. A `snake_case` function in `Scripts/py4gw-marks-corner/scripts/` that returns a `BehaviorTree` is ours → snake_case, even though every node it constructs is PascalCase.

## Factories that return BehaviorTrees are still functions

The tempting mistake: a helper returns a `BehaviorTree` and gets called alongside `BT.Player.Wait(500)`, so it grows a PascalCase name to "match" the framework. Don't. It's a Python function we wrote.

```python
# wrong — reads like a framework primitive, isn't one
def WaitForAreaClearOrDeath(...) -> BehaviorTree: ...
def SetPhase(phase) -> BehaviorTree: ...

# right
def wait_for_area_clear_or_death(...) -> BehaviorTree: ...
def set_phase(phase) -> BehaviorTree: ...
```

Mixed call sites are fine and expected — see `Scripts/py4gw-marks-corner/scripts/DervCOFFarmBT.py`:

```python
children=[
    BT.Player.Move(x=COF_ATTACK_SPOT_1[0], y=COF_ATTACK_SPOT_1[1], log=True),
    set_phase(DervBuildFarmStatus.Kill),
    wait_for_area_clear_or_death(),
    BT.Items.SalvageInventoryItems(log=False),
    loot_filtered_items(),
]
```

## Strings are not identifiers — leave them alone

`name=` arguments on BT nodes, log source labels, and step names are **display data**, not code. They stay as-authored:

```python
BehaviorTree.ActionNode(name=f"SetPhase({phase})", action_fn=apply_phase, aftercast_ms=0)
```

The function is `set_phase`; the node label stays `"SetPhase(...)"`. Renaming these breaks UI tree labels and log greppability for no benefit.

**Watch out:** step-name strings in `get_execution_steps()` are load-bearing — `choose_recovery_step_name()` returns `"Farm Loop"` / `"Prepare Outpost"` and the party-wipe recovery service matches on them. Rename the callable, never the string:

```python
("Farm Loop", farm_loop),   # left side is a lookup key, right side is ours
```

## Name collisions when renaming

Converting `SetPhase` → `set_phase` collides with an inner `def set_phase`. Rename the *inner* one to something concrete — don't reach for `_set_phase`:

```python
def set_phase(phase: str) -> BehaviorTree:
    def apply_phase(node): ...
```

## Aliasing framework imports

Allowed, but only when it earns its keep — a genuinely awkward name, or a collision. Don't alias wholesale; it costs greppability against the library source.

```python
from Core.SomeModule import AwkwardlyNamedThing as awkward_thing
```

Class imports (`BottingTree`, `DervBoneFarmer`, `Settings`, `ModelID`) stay PascalCase — that's correct Python for classes, ours or not. Attribute-chain calls like `BT.Map.WaitforMapLoad` can't be aliased at import anyway; leave them.

## Checking a file

```powershell
& ".venv\Scripts\python.exe" -c "import ast,sys; [print(n.name) for n in ast.walk(ast.parse(open(sys.argv[1],encoding='utf-8').read())) if isinstance(n,ast.FunctionDef) and n.name[0].isupper()]" <file.py>
```

Prints every PascalCase `def` in the file. Anything listed is ours and should be renamed; nothing should print.

## Upkeep

When editing a file in `Bots/` or `Core/Builds/`, fix PascalCase `def`s you touch, and their call sites in the same file. Before renaming anything with cross-file reach, grep the module name first — `DervCOFFarmBT` had no importers, so its rename was contained; a build class under `Core/Builds/` usually won't be. Don't sweep unrelated files as a drive-by.
