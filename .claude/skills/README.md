# Skills

Auto-registered by Claude Code. Filename becomes the skill name
(`persistence-jail.md` → `/persistence-jail`); frontmatter `description` is the
hook it reads to decide whether to auto-invoke. Presence costs nothing —
invocation is what lands content in context.

**These are tracked here**, unlike in the sibling repo where `.claude/` is
excluded via `.git/info/exclude` and one `git clean` from gone. Ported
2026-07-28 with every path translated through `tools/reforge/layout.toml`, so
they name *this* tree.

## Index

**Repo-wide** — load early, cheap, applies to any task

- `repo-navigation` — LINE_INDEX symbol lookup, tree ownership, docs hierarchy, entry points
- `pr-workflow` — origin/upstream fork split, branch scratchpads, files never to commit
- `python-naming-conventions` — snake_case vs the framework APIs we don't own

**Subsystems**

- `persistence-jail` — Settings / JsonFactory, the disk-access ban list
- `reforged-native-bindings` — `Py*` modules, ctypes context path, GWCA→Reforged renames
- `widget-authoring` — `.widget` folder discovery, MODULE_CATEGORY/TAGS/OPTIONAL defaults
- `re-workflow` — WASM-first reverse engineering, Ghidra MCP, offset JSON
- `reforged-vs-frenkey-primitives` — what frenkeyLib pretends still exists
- `reforge-layout` — regenerate the Core/Widgets/Scripts layout from upstream

**Combat / BT stack**

- `heroai-bt-engine` — the BT combat engine, BldMgrBT builds, the legacy toggle
- `bt-model-and-limits` — what a BehaviorTree structurally cannot do
- `bt-rotation-authoring` — Selector/Sequence semantics, aftercast, `optional()`
- `migrate-bot-to-bottingtree` — FSM `Botting()` → `BottingTree` port

**Bot-building**

- `inventory-actions` — identify / salvage / loot primitives and the fire-then-verify pattern
- `fight-clear-detection` — "combat done" without ending early or hanging
- `log-spam-suppression` — the per-layer `log=` gates

## Reading these against a sibling repo

Two siblings exist, both at `C:\cygwin64\home\Mark\code\`:

| repo | layout | what it is |
|---|---|---|
| `Py4GW_Reforged` | `Py4GWCoreLib/` + `HeroAI/` | the fork — PR staging, and `forwardport.py --source` |
| `MyPy4GW` | `Py4GWCoreLib/` only | symlink-overlay runtime; no `HeroAI/`, so BT skills do not apply |

> `Py4GW_Reforged` also contains a stray `Core/` — 11 entries against
> `Py4GWCoreLib`'s 69, untracked and gitignored at `.gitignore:253`. It is
> residue from an early transform run, **not** a second layout. Do not read it as
> evidence that repo has been reorganised; translate paths anyway.

There is no `Py4GW` directory. `AGENTS.md` §0 mentions it as the retired
pre-Reforged project and a memory scope still exists for it, but the working tree
is gone.

Both siblings use upstream's layout, so translate in reverse when exploring them:

| here | sibling repos |
|---|---|
| `Core/` | `Py4GWCoreLib/` |
| `Scripts/py4gw-marks-corner/scripts/` | `Bots/marks_coding_corner/` |
| `Scripts/py4gw-marks-corner/lib/` | `Bots/marks_coding_corner/utils/` |
| `Scripts/py4gw-community-bots/legacy/` | `Bots/` |
| `Scripts/py4gw-community-bots/scripts/` | `Widgets/Automation/Bots/` |
| `Scripts/py4gw-devtools/scripts/` | `Widgets/Coding/` |
| `Scripts/py4gw-examples/` | `Examples/` |
| `Scripts/py4gw-modular/tools/` | `Widgets/Automation/modular/` |
| `dev/reference/` | `Sources/` |
| `dev/bot_factory/` | `bot_factory/` |
| `dev/legacy/widget_catalog/` | `Widgets/WidgetCatalog/` |
| `dev/bridge/runtime/` | `BridgeRuntime/` |
| `Widgets/Panels/Messaging.py` | `Widgets/System/Messaging.py` |

`Widgets/` otherwise re-nests by category — check `layout.toml` rather than
guessing. The authority is always the manifest, never this table.

## Known-stale references

Found by resolving every path in these skills against the real tree. **These are
not port damage** — they were already wrong in the sibling repo:

| skill | reference | status |
|---|---|---|
| `migrate-bot-to-bottingtree` | `Core/BTBuildMgr.py` | in no tree, ours or upstream's. Probably meant `Core/BldMgrBT.py`. |
| `reforge-layout` | `Core/debug_hatch.py` | exists only in the fork's working copy, never committed upstream — not ported here |
| `migrate-bot-to-bottingtree` | `…/DervCOFFarm.py` | the pre-BT version; in vendor but not in this tree |
| `re-workflow` | three `docs/*.md` RE guides | never existed under those names; see `docs/RE/` |
| `repo-navigation` | `dev/reference/modular_bot`, `Widgets/Config` | data dirs, gitignored, absent from vendor |

86 other path references resolve cleanly.

## Adjacent

`CLAUDE.md` is loaded every turn and @-includes four context files
(`hard-rules`, `code-style`, `layout-rules`, `runtime-behaviour`) — durable rules
go there, on-demand knowledge goes here. `AGENTS.md` is the operating manual for
other agentic tools and for humans; Claude Code does not auto-load it.

There is no `.claude/hooks/` in this repo — the sibling's `py_syntax_check.py`
`PostToolUse` hook was not ported, and neither was its `settings.local.json`.
