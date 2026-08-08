# mypy4gw

A **generated** reorganization of upstream Py4GW_Reforged. `vendor` mirrors
upstream and is never edited; `layout` is produced from it by
`tools/reforge/apply.py`; `main` is `layout` plus your work.

Before changing a file, know which of those three you are on, and read
`AGENTS.md` §2 — where you may edit.

Sibling repos on this machine — including the separate `Py4GW-Unchained` fork
line, whose code is **not** portable into this tree — are mapped in
`docs/related-repos.md`. The Reforged upstream is a supplier only: nothing is
ever sent back to it (RS-008). Contributions go to the Unchained line.

@.claude/context/hard-rules.md
@.claude/context/code-style.md
@.claude/context/layout-rules.md
@.claude/context/runtime-behaviour.md
