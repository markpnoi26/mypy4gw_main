---
name: fight-clear-detection
description: Detect "combat done" for a farm loop without ending early or hanging. Covers Range enum choice, first-enemy-seen latch, clear-hold debounce, enemy blacklist by name and encoded string, and model-ID drift.
---

# Fight-clear detection

Reference: `Scripts/py4gw-marks-corner/scripts/DervCOFFarmBT.py::WaitForAreaClearOrDeath` and `Core/Builds/Dervish/D_A/DervBoneFarmer.py::is_blacklisted_enemy`.

## The naive approach breaks in three ways

```python
# BROKEN
def tick():
    if no non-blacklisted enemies within Range.X:
        return SUCCESS
    return RUNNING
```

Three failure modes:
1. **Ends before combat starts** — when you arrive at the spot, enemies haven't reached you yet → SUCCESS immediately → skip past kill phase entirely.
2. **Ends mid-fight** — during knockbacks or spacing, enemies briefly stray outside range → transient "no enemies" → premature SUCCESS.
3. **Hangs forever** — chest farm / empty spawn, no enemies appear, never returns.

## The correct pattern: engage-latch + clear-hold + timeout

```python
def WaitForAreaClearOrDeath(
    engage_range: float = Range.Earshot.value,   # enemies must arrive within this
    clear_range: float = Range.Earshot.value,    # count anything within this as "still fighting"
    no_enemy_timeout_ms: int = 15_000,           # bail if nothing ever shows
    clear_hold_ms: int = 1500,                   # require this much continuous clear
):
    state = {"engaged": False, "started_at": 0.0, "clear_since": 0.0}

    def tick(node):
        if Agent.IsDead(Player.GetAgentID()):
            reset_state(); return FAILURE

        if state["started_at"] == 0.0:
            state["started_at"] = time.monotonic()

        # Phase 1: wait for first enemy to appear (or timeout)
        if not state["engaged"]:
            for eid in GetFilteredEnemyArray(px, py, engage_range):
                if not is_blacklisted_enemy(eid):
                    state["engaged"] = True; break
            else:
                if elapsed >= no_enemy_timeout_ms:
                    reset_state(); return SUCCESS   # safety valve
                return RUNNING

        # Phase 2: wait for continuous clear
        for eid in GetFilteredEnemyArray(px, py, clear_range):
            if not is_blacklisted_enemy(eid):
                state["clear_since"] = 0.0
                return RUNNING
        if state["clear_since"] == 0.0:
            state["clear_since"] = now
            return RUNNING
        if (now - state["clear_since"]) * 1000 < clear_hold_ms:
            return RUNNING

        reset_state()
        return SUCCESS
```

## Range enum values (know them cold)

| Range | Distance | Typical use |
|---|---|---|
| `Adjacent` | 166 | Touching / melee only |
| `Nearby` | 252 | Close melee AoE |
| `Area` | 322 | PBAoE range |
| `Earshot` | 1012 | Aggro / ranged range |
| `Spellcast` | 1248 | Spell range |
| `Spirit` | 2500 | Spirit-of / long checks |
| `Compass` | 5000 | Whole map area |

- **`Area` (322)** — too tight for scythe combat; enemies stray → false clear.
- **`Earshot` (1012)** — natural aggro range; things that could hit you.
- **`Spellcast` / `Spirit`** — chase stragglers; slows farm.

Default to `Earshot` for both engage and clear ranges.

## Blacklist enemies you deliberately skip

Two tiers of matching, both live in `DervBoneFarmer.py`:

```python
ENEMY_BLACKLIST_NAMES = {"blood song", "destruction", "charr axemaster"}  # case-insensitive substring
ENEMY_BLACKLIST_ENC_STRINGS: set[str] = set()                              # exact enc-string

def is_blacklisted_enemy(agent_id):
    enc = Agent.GetEncNameStrByID(agent_id, literal=False)
    if enc and enc in ENEMY_BLACKLIST_ENC_STRINGS:
        return True
    name = Agent.GetNameByID(agent_id)
    if not name:
        return False    # unnamed → engage, safer default
    return any(needle in name.lower() for needle in ENEMY_BLACKLIST_NAMES)
```

### Encoded-string vs name-substring

| Approach | Pros | Cons |
|---|---|---|
| **Name substring** (`"blood song"`) | Readable, easy to edit | Async — `GetNameByID` returns `""` on first tick after enemy appears (decode runs in background thread). Locale-dependent in theory. Watch spacing: "Blood Song" vs "Bloodsong" — substring match is exact-character. |
| **Encoded string** (`"\\x1234\\x5678"`) | Available immediately (no async decode). Locale-independent. Exact match — no substring collisions. | Not human-readable. Have to log-and-copy the enc string once per enemy type. |

**Get enc strings via the diagnostic**: `WaitForAreaClearOrDeath` logs `{'aid', 'name', 'enc', 'model_id'}` for every remaining enemy when it declares clear. Copy `enc` values you want to blacklist permanently into `ENEMY_BLACKLIST_ENC_STRINGS`.

## Do NOT blacklist by model ID

Two reasons:
1. Model IDs can drift between game updates.
2. Same model ID may be reused across enemy variants in different areas.

The old code used `{SpiritModelID.BLOODSONG, SpiritModelID.DESTRUCTION, AgentModelID.CHARR_AXEMASTER}`. During this project we hit exactly this drift — the model IDs in the enum didn't match what was in COF. Migrated to names + enc strings.

## Name resolution is async

`decode_raw` (the function backing `Agent.GetNameByID`) does a table lookup in `gw.dat`. First call for a given encoded string: `""` returned, decode dispatched to a background thread. Next tick: cache hit, real string returned.

Impact on the blacklist: for the first ~1 tick after an enemy appears, name-substring match won't work (`name == ""` → returns False → treated as engageable). Encoded-string check does work in that same tick. That's why we check enc first.

## Diagnostic to log

Keep this in your fight-clear tick when debugging:

```python
if area_declared_clear:
    survivors = [
        {"aid": aid, "name": Agent.GetNameByID(aid),
         "enc": Agent.GetEncNameStrByID(aid, literal=False),
         "model_id": Agent.GetModelID(aid)}
        for aid in GetFilteredEnemyArray(px, py, Range.Compass.value)
        if not Agent.IsDead(aid)
    ]
    if survivors:
        ConsoleLog("...", f"Declaring clear. Survivors within compass: {survivors}.")
```

You get name + enc + model_id in one line — enough to grow the blacklist correctly.
