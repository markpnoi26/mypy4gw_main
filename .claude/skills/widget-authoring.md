---
name: widget-authoring
description: Adding, moving, or debugging a widget under Widgets/. Covers folder-based discovery via the .widget marker, the non-obvious MODULE_CATEGORY/MODULE_TAGS/OPTIONAL defaults, and why a new widget silently fails to appear. Load before creating a widget or changing WidgetHandler/WidgetCatalog.
---

# Widget authoring and discovery

Highest-value reference before changing discovery, metadata defaults,
`WidgetHandler`, or `WidgetCatalog`: `docs/widget_manager_and_catalog.md`.

## Discovery is folder-based, not file-based

`WidgetHandler` walks `Widgets/`. **Only folders containing a `.widget` marker
file are discovery roots.** Every `.py` in such a folder is then loaded as a
widget — no per-file opt-in.

Two consequences that cause most "my widget doesn't show up" reports:

- A new `.py` in a folder with no `.widget` is **invisible**. Add the marker.
- A helper/util `.py` dropped into a folder that *does* have `.widget` gets
  **loaded as a widget**. Put shared code outside the marked folder, or under
  `Sources/` / `Core/`.

There are 57 marked folders currently; `find Widgets -name '.widget'` lists them.

## Metadata defaults are non-obvious

From `Core/py4gwcorelib_src/WidgetManager.py:438-443`:

| Module global | Default when absent |
|---|---|
| `MODULE_CATEGORY` | first segment of `widget_path` (first folder under `Widgets/`) |
| `MODULE_TAGS` | **every** non-empty segment of `widget_path` |
| `OPTIONAL` | `True`, *except* `False` when category is `System` or `Py4GW` |

So moving a widget between folders silently changes its category and tags unless
they are declared explicitly. If a widget's placement is meaningful, set
`MODULE_CATEGORY` and `MODULE_TAGS` rather than relying on the path.

`OPTIONAL = False` means the user cannot disable it — reserved for `System` and
`Py4GW` infrastructure. Do not set it on a feature widget.

## Bootstrap chain

`Py4GW_widget_manager.py` is the in-client entry point: creates the manager INI
key, runs discovery, hands off to
`dev/legacy/widget_catalog/Py4GW_widget_catalog.py`.

## Thin-wrapper convention

Several widget trees are **wrappers, not implementations** — the real code lives
elsewhere and the widget only exposes it through Widget Manager. Notably
`Scripts/py4gw-modular/tools/` wraps `Core/modular/` (implementation) and
`dev/reference/modular_data/` (recipes/prebuilts). Fix behaviour in the implementation
tree; touch the widget only for UI wiring.

## Filenames

Widget filenames contain spaces and `&` freely (`Widgets/Guild Wars/Items &
Loot/`). That is fine for discovery — it loads by path, not by import name — but
it means such a module **cannot be imported** by another module. Shared code
between widgets must live in an importable sibling with a space-free name.

## Persistence

Widget settings go through `Settings` / `JsonFactory` like everything else. See
the `persistence-jail` skill. Widget state is `"account"` scope unless it is
genuinely shared across accounts.
