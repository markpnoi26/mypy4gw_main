---
name: persistence-jail
description: Reading or writing anything to disk — config, JSON, saved state, cross-account data. Covers the Settings/JsonFactory contract, scopes, what is forbidden and why, and what to do when the sanctioned classes lack a primitive you need. Load before adding any persistence, or when auditing code that touches files.
---

# Persistence jail

The rule itself is in `.claude/context/hard-rules.md` (always loaded). This is
the how and the why. Full audit: `docs/persistence_jail/`.

## The two classes

| Data | Class | Backend |
|---|---|---|
| INI / flat config | `Settings` | native `PySettings` |
| JSON / structured | `JsonFactory` | native `PyJson` |

Both are **self-throttled, self-persisting singletons keyed by `(name, scope)`**.
You do not save; you do not flush; you do not lock. Writing through them is the
entire contract.

## Scopes

- `"account"` — jailed under `settings/` or `json/`, per-account.
- `"global"` — jailed the same way, shared, with cross-process locking on the
  native side.
- `"root"` — **does not exist, raises.** The single project-root file,
  `Py4GW.ini`, is reached only through the hardcoded path-less accessor
  `Settings.py4gw_ini()`.

Cross-account data goes in `global` scope. Never construct a path to another
account's file — that is the bug this jail exists to prevent.

## Why the bans are absolute

Each forbidden mechanism reintroduces a problem the native layer already solved:

- `open()` / `json.dump` / `configparser` — non-atomic, so a crash mid-write
  truncates user config.
- hand-rolled lock files — the native side already locks `global` scope
  cross-process; a second lock deadlocks against it rather than helping.
- `pickle` — executes on load.
- file-based IPC — races against the injected process's frame loop. Use
  `GLOBAL_CACHE.ShMem`.
- directory enumeration for discovery — the scope jail owns the layout;
  enumerating it hardcodes an internal detail that then can't change.

## When a primitive is genuinely missing

**Stop and tell the user.** Propose the specific method on `Settings` /
`JsonFactory`, or the primitive in `Py4GW_Reforged_Native`. Do not fall back to
a raw handler "just for this one case" — that is how the jail erodes.

Known open gap: reading **bundled read-only catalogs** shipped in the source
tree. Needs either a Native "read bundled file" primitive or a `json/Defaults/`
seed template. If a task requires that, name it rather than working around it.

## Sanctioned exceptions — do not extend this list

- `Core/database_src/DBMgr.py` — sqlite, scheduled for rework.
- Processes that physically cannot load the embedded modules: the external
  launcher, `bridge_daemon.py`, `bridge_cli.py`, `py4gw_mcp_server.py`.

Injected code has no exception. Widgets, bots, builds, and core lib all go
through the two classes.

## Auditing

Grep for the ban list, then confirm each hit is one of the sanctioned files:

```sh
grep -rn "json\.load\|json\.dump\|configparser\|\bpickle\b\|write_text\|read_text" \
  --include="*.py" Core/ Widgets/ HeroAI/ Bots/ Scripts/
```

Migration history, if you need precedent for a conversion:
`docs/Configparser_To_Settings_Migration_Plan.md`,
`docs/IniManager_Migration_Plan.md`, `docs/ini_manager_behavior_and_usage_guide.md`.
