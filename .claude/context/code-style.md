# Project code style — non-negotiable

Three hard rules for all code written or edited in this repo.

## 1. No `_underscore` prefix on functions, methods, or variables

The **only** exception: `_` (or `_name`) to mark a deliberately unused variable,
e.g. `for _ in range(n):` or `_, actual = pair`.

Everything else — helper functions, "private" methods, module-locals, closures,
state dicts — gets a plain name. If you feel the urge to write `_helper`, name it
something concrete like `apply_handler_settings` or `dispatch_cast` instead.

When wrapping existing framework code, you have to call the underscore names that
already exist (e.g. `AutoInventoryHandler()._get_inventory_items`) — that's fine.
Just don't *introduce* new underscore-prefixed names.

## 2. Almost no comments

Default to zero comments. Only keep a comment when it explains a non-obvious
*why* — a hidden constraint, a workaround for a specific bug, an invariant a
reader would miss.

Never write comments that restate what the code does. If names aren't clear
enough, **rename** — don't paper over with prose.

Bad:
```python
handler.salvage_whites = True  # enable white salvage
```

Good — just delete the comment. The name says it.

Keep comments only for surprising behavior. Example that survives:
```python
optional(swap_to_scythe(), name="OptionalScytheSwap"),  # Sequence FAILs when already on scythe; wrap so parent Sequence keeps going
```

## 3. Docstrings stay minimal

Module-level docstrings: one short line, or none at all. No multi-paragraph
explainers at the top of every file.

Function docstrings: fine when they document intent that isn't obvious from the
signature. Keep them tight — one paragraph max. No `Meta:` blocks, no template
noise, no restating parameter types Python already declares.
