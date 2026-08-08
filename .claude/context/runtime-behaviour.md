# Runtime behaviour — always loaded

Two things about the Guild Wars client that are not visible from the code, and
that an offline harness will happily agree with you about while being wrong.
Both have already cost a client crash and a dead character.

## Client state lags the action that causes it

After an action fires, the client keeps reporting the **previous** value for
several frames. Logic that re-reads on the next tick sees stale data and fires
again.

Fixed delays do not fix this — a longer sleep only narrows the race, because the
latency varies with ping and load.

**Latch on the pre-action value and require an observed change before allowing
the action again.** Always pair the latch with a timeout: an interrupted action
never produces the change, and without an escape the latch blocks forever. Make
the timeout a named constant, never a literal.

**Never make the timeout load-bearing.** A deadline you had to guess must not
decide whether the action counted. Measured live: shrinking cast windows to
`activation + 500` made *every* confirm fail — Vow of Piety confirms fine at
~1200 ms and never at 600 ms — and because expiry was a hard FAILURE that dropped
the rung, the character died. Observability lag is dominated by ACTION-queue
throttle plus server round trip, not by the skill table's activation. Windows
must be generous: `activation + aftercast + 750`.

Structure the wait so expiry means *"could not observe"*, not *"did not
happen"* — put the confirm in a Selector with a fallback that asks a second,
always-readable question. For casts that is **recharge started**: the server ack,
readable for every skill, so a buff confirm should release on `buff OR recharge`
rather than being hostage to whether that effect is legible. For adrenaline
attacks there is no recharge, so expiry settles to SUCCESS. Log the observed
values on expiry (`buff=… recharge=A->B adrenaline=A->B`) — a wrong observable
then reports itself instead of silently wedging a branch.

When one latch guards several mutually exclusive actions, make it **shared, not
per-action** — unless the wait is a node returning RUNNING. `WaitUntilNode`
(`Core/py4gwcorelib_src/BehaviorTree.py`) pins both SequenceNode and SelectorNode
to that rung, so the ladder cannot fall through to the sibling; the structure
replaces the shared latch.

**Verify the action actually writes what you confirm on.** `Player.Interact` /
`InteractAgent` does *not* set `player.target_id` — framework routines pair it
with `Player.ChangeTarget` first. Confirming on `GetTargetID()` after a bare
`Interact` never passes, and inside a Sequence that FAILURE aborts the branch.
Guard casts with `Routines.Checks.Skills.CanCast()`, which covers IsCasting,
IsDead, IsKnockedDown and `SkillBar.GetCasting() != 0`.

> The worked BT form of this (`cast_verified`) sits in the sibling fork's
> archive, on `HEROAI_MIGRATION` — importable with `forwardport.py` if wanted.
> Do not cite it as present in this tree.

## Identify UI frames by tree pattern, not by hash or name

Match `template_type` plus `child_offset_id` across parent *and* children. Never
anchor on `frame_hash`, a `frame_aliases.json` label, or a hardcoded child-offset
path.

Dynamically created frames — popups, confirm dialogs — have `frame_hash == 0` and
no native label, so hash and name lookups both return nothing. Their child offset
is a *creation-order slot*, not a stable id: the salvage materials confirm dialog
appears at 98/100/109/110/111/113 in different places in this repo, varying by
session state rather than by GW build. Alias names are keyed by path, so resolving
a name resolves the same fragile path with one extra indirection.

`template_type` is undocumented — nothing in `docs/RE/` describes it ("template"
there means 9-slice image templates and C++ `TCtlInstance<T>`, unrelated). Read
values empirically from `dev/harness/frame_viewer.py`, which shows Frame Hash,
Runtime Path, Child Offset ID and Template Type per frame.

Match every level before acting, and verify siblings too — for the salvage
confirm that means icon + No + Yes all matching offset and template before
clicking Yes. Worked example: `find_material_confirm_yes_frame()` in
`Core/Inventory.py`.

Bias the match **loose rather than strict** when a false negative costs more than
a false positive: a missed dialog fires the next salvage into an open modal and
crashes the client, while a spurious match only aborts the run.

Mark prefers this over the native-binding shortcut
(`PyInventory.AcceptSalvageWindow()`) and over hash anchoring, even where those
are available.
