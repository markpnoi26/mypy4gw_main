# Fight Zone — upstream PR dossier

Formation-based combat positioning for HeroAI parties. The party stops piling
onto the leader in a fight and instead holds an authored front/mid/back
formation anchored ahead of them, gives ground when the enemy mass presses in,
and closes when it drifts out of reach.

This document is written for someone applying the work to
`apoguita/Py4GW_Reforged`, not for someone reading it here. Everything below is
stated in **upstream's** naming unless explicitly marked otherwise.

---

## 1. Read this first: the diff is not the change

`git diff upstream/main` against this repo will show a very large, very
misleading changeset. This tree is a generated reorganisation of upstream:

- `Py4GWCoreLib/` is renamed to `Core/` throughout ([RS-001](../rules/RESTRUCTURE.md#rs-001)).
- Every file has been run through Black at 120 columns and isort with
  `force_single_line`.

Both are this repo's conventions, not the feature's. `HeroAI/globals.py` is the
clearest illustration: its diff against upstream is 41 added and 9 removed
lines, of which **exactly three** belong to this feature. The rest is the rename
and the reformat.

**Do not hand-port from a raw diff.** Use `tools/reforge/backport.py`, which
inverts the manifest for paths and rewrites `Core` back to `Py4GWCoreLib` in
imports and quoted module strings only — never as a bare token, because `Core`
is an ordinary English word and a blind reverse swap corrupts prose and
unrelated identifiers.

---

## 2. Entry point

The whole system hangs off **one object**, constructed lazily, behind a feature
flag that is **off by default**.

### Where it plugs in

`HeroAI/follow/leader_publish.py`, class `FollowFormationPublisher`. That class
already owns the "leader decides where everyone stands and writes it to shared
memory" loop. The fight zone is a branch inside it, nothing more.

Two call sites:

```python
# 1. Before the per-member publish loop — tick the zone, get its slots.
fight_plan = self._resolve_fight_plan(
    all_accounts, leader_index, leader_account, leader_options,
    leader_x, leader_y, leader_navmesh_sane,
)

# 2. Inside the per-member loop — a fight slot wins over the follow slot.
if fight_plan is not None:
    fight_slot = fight_plan.slots.get(party_pos)
    if fight_slot is not None:
        self._publish_fight_slot(
            options, fight_slot, leader_zplane,
            fight_plan.zone.anchor(), bypass_validation=bypass_validation,
        )
        continue
```

When the flag is off, `_resolve_fight_plan` returns `None` on its first branch
and the loop behaves exactly as it does today. **The off path is the unmodified
upstream path**, which is what makes this safe to merge before it is trusted.

### Why the import is lazy

```python
def _get_fight_publisher(self):
    if self.fight_publisher is None:
        from HeroAI.fight.publisher import FightZonePublisher
        self.fight_publisher = FightZonePublisher()
    return self.fight_publisher
```

`Core/GlobalCache/SharedMemory.py` imports `HeroAI.follow.leader_publish`
directly and is startup-sensitive; `HeroAI.fight` pulls `Core.AgentArray`. A
module-level import here creates a startup cycle. Keep it lazy.

For the same reason `HeroAI/fight/__init__.py` **exports nothing on purpose**,
mirroring the existing `HeroAI/follow/__init__.py` rule. Import exact submodules
(`HeroAI.fight.publisher`), never the package root.

### Who owns the party flag

`_apply_fight_all_flag` writes the zone anchor into `leader_options.AllFlag` and
sets `fight_owns_all_flag`. That flag is the whole handshake: it distinguishes
"we placed this flag" from "the user placed it", so the system only ever clears
a flag it put there itself. A manual flag suppresses the zone entirely.

---

## 3. File inventory

### New files — zero conflict surface

Upstream has never created any of these paths, so they cannot conflict on merge
no matter what upstream does elsewhere.

| File | Lines | Role |
|---|---:|---|
| `HeroAI/fight/__init__.py` | 6 | Deliberately empty; see above |
| `HeroAI/fight/zone.py` | 1084 | Zone state machine, trigger rings, give-ground |
| `HeroAI/fight/publisher.py` | 467 | Ticks the zone, builds slots, publishes the debug snapshot |
| `HeroAI/fight/formation.py` | 202 | Authored formation shape, depth budget |
| `HeroAI/fight/escape.py` | 313 | Plots the withdrawal route |
| `HeroAI/fight/lines.py` | 153 | Front/mid/back line model |
| `HeroAI/fight/breadcrumbs.py` | 152 | Walked-ground history for long retreats |
| `HeroAI/fight/engagement.py` | 127 | Blob clustering and engagement selection |
| `HeroAI/fight/assignment.py` | 86 | Member-to-pin assignment |
| `HeroAI/fight/safespot.py` | 76 | Last provably quiet position |
| `HeroAI/fight/report.py` | 61 | Human-readable state dump |
| `HeroAI/follow/placement.py` | 98 | Navmesh standable/reachable resolution |

**2825 lines, all additive.**

`HeroAI/follow/placement.py` is worth calling out separately: it is not
fight-specific. It answers "can a body stand here **and** walk to it" — two
distinct navmesh questions, because a ledge across a chasm passes the first and
fails the second. The existing follow publisher benefits from it too.

### Touched files — the real conflict surface

Four files, and they are the only thing that can conflict.

| File | What was added |
|---|---|
| `HeroAI/follow/leader_publish.py` | The two call sites above, plus `_resolve_fight_plan`, `_publish_fight_slot`, `_apply_fight_all_flag`, `fight_terrain_probe`, `_get_fight_publisher` |
| `HeroAI/ui_base.py` | Two isolated regions: a "Fight Lines" tab and `DrawFightZone3DOverlay` |
| `HeroAI/globals.py` | Three module-level flags |
| `HeroAI/utils.py` | `FIGHT_ZONE_FLAG_COLOR` and `is_fight_zone_flag` |

`HeroAI/globals.py` in full — this is the entire footprint in that file:

```python
show_fight_zone_overlay = False
fight_zone_overlay_circles_only = False
fight_zone_debug_snapshot: dict | None = None
```

---

## 4. Keeping the merge surface small

The design deliberately pushes weight into files upstream has never created:
2825 lines in new files, against a few hundred spread across the four touched
ones. Every addition in those four is a **whole new function** rather than an
edit threaded through existing logic — so a merge conflict there is a hunk that
moved, not a behaviour that has to be re-reasoned.

Three rules that keep it that way, and which a future change should not break:

1. **New behaviour goes in `HeroAI/fight/`.** If a change needs another hook in
   `leader_publish.py`, prefer widening what `_resolve_fight_plan` already
   receives over adding a second call site.
2. **The overlay reads a snapshot, never the live objects.**
   `hero_globals.fight_zone_debug_snapshot` is a plain dict published once per
   tick. That is why `ui_base.py` needs no import from `HeroAI.fight` at all,
   and why the UI can lag or be absent without affecting behaviour.
3. **Config lives in an INI section nothing else uses** (§5).

If upstream restructures `HeroAI/` wholesale, the new files move as a unit and
only the four touched files need re-grafting.

---

## 5. Configuration contract

Section `FightRuntime`, reached through `Settings` — not `configparser`, not
`open()`.

| Key | Default | Meaning |
|---|---|---|
| `fight_zone_enabled` | `False` | Master switch. Off means stock behaviour. |
| `show_fight_zone_overlay` | `False` | Draw the 3D debug overlay |
| `fight_zone_overlay_circles_only` | `False` | Draw only armed rings |
| `engage_depth_u` | `538.0` | Forward reach of the frontline ring |

**A saved `engage_depth_u` wins over the source default.** Anyone testing a new
default must move the "Engage reach" slider, or they will keep running their old
saved value and conclude the change did nothing.

---

## 6. Behaviour, in one page

The formation is pinned ahead of the party and faces the enemy blob. Three
elliptical trigger rings sit in the formation's local frame; each is tested
against the **engagement blob's centre of mass**.

| Ring | Centred on | Trips when | Response |
|---|---|---|---|
| Backline | Rear rank | Blob centre enters | Emergency step back, ignores the dwell |
| Midline | Mid rank | Blob centre enters | Soft step back after the dwell |
| Frontline | Ahead of the pin | Blob centre **leaves** | Step forward, flat 4s cadence |

Ellipses rather than flat depth planes because facing is re-aimed on a gate that
can stretch to 24 seconds for a lone enemy. With a stale facing, a 1-D
projection onto the facing axis under-reads a blob that has slid off-axis. The
lateral radius is what closes that blind spot, and it is why narrowing it is the
wrong instinct.

Escalation ordering is enforced in code, not by configuration: `backline_ring()`
clamps its own forward tip to stay behind the midline's. On a compressed
formation the fixed backline radius would otherwise put the emergency trip
*ahead* of the soft one, and the party would panic before it ever stepped back
calmly.

Retreat follows a plotted escape route — a radial navmesh scan for "where can we
go from here", plus the party's own breadcrumb trail for the long way out, which
is walkable by construction and correct about doglegs.

---

## 7. Known gaps

Stated plainly because a PR that hides them is worse than one that does not.

1. **The zone anchor is not navmesh-validated.** Member pins are, via
   `resolve_placement`. The anchor written to `AllFlag` is not, and neither is
   the forward advance step. All the terrain machinery currently looks backwards
   along the escape route; nothing looks forwards.
2. **The overlay draws wanted positions, not resolved ones.** Resolution happens
   downstream in `leader_publish` and its answer only reaches shared memory, so
   a pin drawn inside a wall says nothing about where the follower actually ends
   up. `Scripts/py4gw-marks-corner/scripts/PlacementProbe.py` exists to make the
   difference visible by hand.
3. **`follower_runtime.py` guards casts on `Agent.IsCasting()` only**, not
   `SkillBar.GetCasting()`. `HeroAI/bt/conditions.py` checks both, and so does
   `Routines.Checks.Skills.CanCast`. Client state lags, so a move issued in that
   window can clip the start of a cast.
4. **Retrace is mislabelled.** Popping retreat steps leaves `giving_ground=True`
   and `closing=False`, so the tab reads "BACKING OFF" while the pin moves
   forward. Cosmetic, but confusing while testing.
5. **`has_line_of_sight` samples interior points only.** Any hop shorter than
   the step distance has no samples at all and returns `True` unconditionally.
   Short-range reachability results are not evidence.

---

## 8. Verification — and the one thing blocking a credible PR

There is no pytest config in this repo and no global test command. This system
is verified by **eight targeted suites** covering ring geometry, escalation
ordering, control flow, jitter, long retreats, facing, escape plotting, forced
re-aim, and placement.

**Those suites currently live in a session scratchpad, not in the repo.** They
are not committed, not discoverable, and will not survive. For a PR that anyone
can re-run, they need a home — `HeroAI/fight/test_zone.py` and siblings would
match the pattern `Core/py4gwcorelib_src/script_manager/` already sets, which is
stdlib-only `unittest` loaded by file path so it runs outside the game client.

This is the single highest-value thing to do before opening the PR. A 2825-line
behavioural addition with no runnable tests attached is a hard sell to a
maintainer, and the suites already exist — they just need moving and a small
harness so they import without a live client.

---

## 9. Suggested PR shape

1. **Foundation** — `HeroAI/follow/placement.py` alone. Standable-and-reachable
   resolution, wired into the existing follow publisher. Useful on its own,
   independently reviewable, no fight zone in sight.
2. **The engine** — `HeroAI/fight/**` plus the tests. Entirely new files, no
   call sites. Nothing executes it yet.
3. **The graft** — the four touched files. Small, and reviewable as "does the
   off path still behave exactly as before".

Splitting it this way means a maintainer can accept 1 and 2 while still arguing
about 3, instead of facing one 3000-line take-it-or-leave-it.

---

## 10. Before you send it

- Run `tools/reforge/backport.py` — do not hand-port.
- Confirm `fight_zone_enabled` defaults to `False` in the ported tree.
- Confirm `HeroAI/fight/__init__.py` is still empty after any tooling pass.
- Check that upstream has not moved `HeroAI/follow/leader_publish.py`; if it
  has, the four graft points move with it and everything else is unaffected.
- Do not include this file, `rules/`, `tools/`, or anything under
  `Scripts/py4gw-marks-corner/` in the PR. They are this repo's, not upstream's.
