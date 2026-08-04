# Fight zone — health retreat

**IMPLEMENTED.** The shipped behaviour is specified in `FIGHT_ZONE_BEHAVIOR.md`
§9, §12, §13 and §14; where the two disagree, that one is current. This is kept
for the reasoning — why a level trigger cannot work here, and what the last
attempt actually got wrong.

One thing changed during implementation. `assess()` as proposed in §3 was a
single call that charged the budget as it answered. That is a live bug: the
ground controller sits behind a 5-18s dwell and evaluates every frame in between,
so the budget emptied in three frames and the party got one 250u step for the
whole fight. It shipped as three calls — `observe` / `verdict` / `spend` — with
the publisher charging only on a change in `zone.health_steps`.

Adds the one input the ground controller deliberately does not have: the party's
own health. Today `adjust_ground()` is purely geometric, and its docstring says
why — a health threshold "has no such feedback and simply ratchets". That
sentence is an accurate description of the last attempt, which backed up forever.
So the whole design below is about giving health the feedback it lacks.

---

## 1. What already exists

Everything the retreat needs to *move* is built and shipped.

| Piece | File | State |
|---|---|---|
| Escape route, radial + breadcrumb backtrack | `HeroAI/fight/escape.py` | Done |
| One step along the route, normalised across a bend | `zone.retreat_step_vector()` | Done |
| Retreat as a **stack**, so coming back retraces the dogleg | `zone.retreat_steps` | Done |
| Distance ceiling (`max_given_ground` 1400, route − margin) | `zone.ground_ceiling()` | Done |
| Dwell after any move, tiered by blob size | `zone.recover_hold_tiers_ms` | Done |
| Retrace on recovery — pops the stack one step per advance dwell | `adjust_ground()` branch 4 | Done |
| Mean party health, published to the overlay | `publisher.mean_party_health()` | Done, read-only |

The health retreat is therefore **not** a new movement system. It is a new
*reason* to call machinery that already works, plus the discipline that stops it.

---

## 2. Why the last attempt never stopped

Three separate mechanisms, all of which have to be answered.

**a. A level trigger has no release condition.** `mean < 0.60` stays true after a
step, because stepping back does not heal anyone. The midline ring releases
itself — one step moves the ring off the mob — but health does not move when the
party does. So the trigger re-fires every dwell until `max_given_ground` runs
out, and then the party sits at maximum retreat being chewed on.

**b. Dead members pin the average below the threshold permanently.**
`party_health[pos] = Current/Max`, and a corpse reads `0.0` and stays there.
Two dead in an eight-man party caps the mean at **0.75 even with the survivors at
full health**. Against a 60% arm threshold the living six would have to average
80% to disarm. This alone guarantees a permanent retreat for the rest of the
fight, and it is invisible in the current readout, which shows only the mean.

**c. Backing up does not reduce damage against a mob that follows.** Enemies run
at the party's own speed. A continuous slide costs the party its own attacks and
gains nothing — the exact "backing up forever while killing nothing" symptom.
Distance only pays when it is spent in a *burst* and then held long enough for
melee to re-close and heals to land, which is why the existing dwell exists.

---

## 3. Design

Health gets a **finite, spendable budget** rather than a condition. This is the
whole idea; everything else is detail.

### The three verdicts

A new module `HeroAI/fight/health_retreat.py` answers one question per tick:

| Verdict | Meaning | Effect on the pin |
|---|---|---|
| `CLEAR` | Health has no opinion | Nothing — today's behaviour exactly |
| `WITHDRAW` | Losing, budget remaining | One step back along the escape route |
| `HOLD` | Losing, budget spent | Advance is vetoed; **nothing moves** |

`HOLD` is what replaces the old infinite retreat. Once the budget is gone, the
party stands and fights wherever it got to. It does not creep back into a fight
it is losing, and it does not keep running.

### The decision

```
mean    = mean health over LIVING members only
deaths  = members who died since the last tick

if deaths:                        arm
elif not armed and mean >= arm_fraction:      return CLEAR

if not deaths and mean >= release_fraction:   reset budget; return CLEAR
if steps_used >= max_steps:                                   return HOLD
if not deaths:
    if mean >= arm_fraction:                                  return HOLD
    if mean > health_at_last_step + recover_margin:           return HOLD

steps_used += 1;  health_at_last_step = mean;  return WITHDRAW
```

Four gates, each answering one of §2:

1. **Budget** (`max_steps`, default 3 = 750u). Answers (a) and (c). Termination
   is structural, not a timer.
2. **Living-only mean.** Answers (b) — a corpse can no longer hold the trigger
   down forever.
3. **Death as an event, not a level.** Living-only means a death *raises* the
   mean, which would perversely disarm at the worst moment: monk spiked, everyone
   else at 90%. So a fresh death spends a step regardless of the mean. Same shape
   as `engagement.party_under_fire`, which already watches change rather than
   level.
4. **Trend gate** (`recover_margin`). Latch the mean at the moment of the step;
   if it has climbed since, the step is working — hold and keep the budget. This
   is the pre-action latch from `.claude/context/runtime-behaviour.md`, and it is
   deliberately **not** load-bearing: an unobservable reading falls through to the
   budget, which stops things on its own.

### Hysteresis — the release

The budget refills only on an **observed recovery above a strictly higher
threshold**: arm at 60%, release at 75%. No timer anywhere. A timer is precisely
what would rebuild the infinite retreat, one refill at a time.

### Why "we're still proceeding forward" is guaranteed

Six independent bounds, and the first three are new:

1. Health can move the pin at most `max_steps × give_ground_step` = **750u** per
   episode.
2. An episode ends only on observed recovery past 75% — never on elapsed time.
3. A spent budget gives health **zero** authority; geometry runs exactly as today.
4. Health never blocks the geometric retreat — breach and overrun still outrank it.
5. `max_given_ground` (1400u) and route length − margin still cap the total.
6. **The retrace is free.** After 750u of withdrawal the blob is outside the
   frontline ring, so the existing advance branch pops the health steps back one
   per `advance_hold_ms`, through the same dogleg they were taken along. The
   overlay reads `CLOSING` while it does. No new code, and it is visible.

Worst case in the money scenario — blob correctly positioned, party at 55%,
nothing recovering — is: three steps back over ~15s totalling 750u, then a hold,
then the party fights it out or walks back in as health returns.

---

## 4. Where it plugs in

### `HeroAI/fight/health_retreat.py` — new

`HealthRetreatConfig`, `HealthRetreatState`, `HealthVerdict`, and the pure
functions `living_mean()`, `newly_dead()`, `assess()`. Config + state + pure
functions is the shape `engagement.py`, `escape.py` and `safespot.py` already
use, and it is what makes the suite in §6 possible without a client.

State lives on `FightZonePublisher` beside `EscapeState` and `EngagementState`,
and is cleared where `self.escape.clear()` already is — so a fight ending resets
the budget with no new lifecycle code.

### `HeroAI/fight/zone.py`

`ZoneInputs` gains `health_verdict: HealthVerdict = HealthVerdict.CLEAR`. The
default is what keeps every existing caller and test untouched.

`adjust_ground()` precedence becomes:

```
1. breached = backline_breached(...)
2. if not breached and now < hold_until_ms:  return
3. if breached or overrun(...):              RETREAT   (geometry)
4. elif verdict == WITHDRAW:                 RETREAT   (health)      <- new
5. elif verdict == HOLD:                     pass      (veto advance)<- new
6. elif not frontline_reached(...):          ADVANCE
7. else:                                     HOLD
```

Health sits *below* both rings and *above* the advance. It can add a step and it
can veto closing; it can never delay an emergency.

Branches 3 and 4 are the same six lines of step machinery, so they get extracted
into one `give_ground(zone, cfg, inputs, hold_ms)` rather than duplicated. The
health step reuses `recover_hold_tiers_ms` — one dwell for "gave ground",
whatever the reason, and the existing Recover-dwell slider keeps covering it.

### `HeroAI/fight/publisher.py`

`mean_party_health()` becomes living-only (§2b). `tick()` calls `assess()` once
and hands the verdict to `ZoneInputs`. New INI keys under `FightRuntime`, read on
the existing 1s reload timer:

| Key | Default | Meaning |
|---|---:|---|
| `health_retreat_enabled` | `False` | The opt-in. Off = today's behaviour |
| `health_retreat_arm` | `0.60` | Below this, start giving ground |
| `health_retreat_release` | `0.75` | Above this, the budget refills |
| `health_retreat_steps` | `3` | Steps per episode — the hard bound |

Disabled means the verdict is computed and **published but not applied** — the
same dry-run the zone itself shipped with, so the controller can be watched for a
few runs before it drives anything.

---

## 5. The visual aid

### Fight Lines tab — always on, even with the feature off

```
Party HP  ██████░░░░  64%     6 alive, 2 dead
Health retreat: ARMED — 2 of 3 steps used, 500u given, releases at 75%
```

`ImGui.progress_bar` (already imported in `ui_base`), coloured by verdict: grey
clear, gold armed, red spent, blue recovering. The **alive/dead split is the
important half** — it is the number that made the old version un-diagnosable, and
the current readout does not show it.

With the feature disabled the second line reads `would arm`, matching the
existing dry-run wording.

### 3D overlay — the one that matters mid-fight

`HP 64%` beside the pin, same colour ramp. Drawn in `FULL` always; in
`CIRCLES`/`MINIMAL` only when armed or spent — a warning, treated like
`depth_clamped`, since those modes exist for fighting rather than reading.

### Snapshot keys

`health_verdict`, `health_steps_used`, `health_max_steps`, `health_arm`,
`health_release`, `health_retreat_enabled`, `party_alive`, `party_dead`.
`party_health` keeps its name and becomes the living-only mean.

---

## 6. Tests

`test/HeroAI/fight/test_health_retreat.py` — new:

1. A healthy party has no opinion.
2. **A flat line cannot spend more than the budget** — the anti-rout test, and
   the headline one. Tick 50 times at 40%, assert exactly `max_steps` withdrawals.
3. A spent budget holds and never withdraws again.
4. The budget refills only on observed recovery — 70% does not, 76% does.
5. **Dead members do not permanently arm the retreat** — 2 of 8 dead, survivors
   full, must read `CLEAR`. This is §2b, and it fails on the current `mean`.
6. A fresh death spends a step even at full health — the monk-spike case.
7. A death counts once; a member still dead does not re-spend.
8. Climbing health below the threshold holds instead of spending.
9. An absent reading never argues for retreat.

`test/HeroAI/fight/test_zone.py` — added:

10. Geometry outranks the verdict: breach + `WITHDRAW` takes **one** step on the
    breach dwell, not two.
11. `HOLD` vetoes the advance — blob far outside the frontline ring, pin does not
    move.
12. A health withdrawal is retraced by the ordinary advance branch.
13. The verdict defaults to `CLEAR`, so every existing path is unchanged.

Per `.claude/skills/test-harness.md`, each invariant gets a mutation probe in the
scratchpad before the suite is called done. Baseline to hold:
`1 failed, 778 passed, 282 deselected, 1 xfailed`.

---

## 7. Known behaviour, stated rather than hidden

- **A death near full health arms for one step, then clears on the next tick**
  once the living mean reads above release. The step is taken, and if it goes
  badly the mean drops and it re-arms with a full budget. Self-correcting, but it
  is not "retreat until the monk is up".
- **Health never overrides the escape route's own limits.** Boxed in with no
  route means no step and a `HOLD`, which is correct — there is nowhere to go —
  but it means the feature silently does nothing in a dead end. The tab says
  `Escape: NO ROUTE` already.
- **`giving_ground` still reports "BACKING OFF" while retracing** — existing
  cosmetic bug, deviation 4 in `FIGHT_ZONE_BEHAVIOR.md`. A health withdrawal will
  hit it too.

## 8. Increments

1. `health_retreat.py` + its suite. No wiring, nothing moves. Verifiable alone.
2. Living-only mean + alive/dead in the snapshot + the tab readout and the
   overlay label. **Read-only — this is the visual aid on its own**, and it makes
   the next step watchable.
3. `ZoneInputs.health_verdict`, the `give_ground` extraction, branches 4 and 5,
   the zone tests.
4. INI keys, the tab checkbox and sliders, dry-run publishing.

Stop after 2 if the numbers look wrong live — steps 3 and 4 are what actually
move the party.

## 9. Two stale things found on the way, not fixed

- `docs/FIGHT_ZONE_BEHAVIOR.md` §9 still documents the advance dwell as 4000ms
  and "22% of run speed"; the code is 1500ms and ~58% since the close-faster
  commit.
- `ui_base.py:1893` branches on a `released` / `remaining` snapshot key that
  `publisher.publish_debug_snapshot` never writes — a dead mop-up readout.
