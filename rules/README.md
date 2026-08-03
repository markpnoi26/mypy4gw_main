# rules/

**Our standing decisions. These outrank `docs/`.**

`docs/` is reference: upstream's material, plus our own handovers and plans from
work already finished. It is not deleted and not corrected — a plan that was
accurate when written stays as written. When something in `docs/` disagrees with
something here, this wins, and `docs/` is simply out of date.

Nothing in here is a suggestion. A rule with an `RS-` number is enforced by the
manifest, by a gate, or by both, and the number appears in the code that enforces
it — grep for it.

## What is in here

| file | written by | what it is |
|---|---|---|
| [RESTRUCTURE.md](RESTRUCTURE.md) | hand | every deliberate divergence from upstream, numbered `RS-nnn`. Start here. |
| [TIER_MAP.md](TIER_MAP.md) | hand | which tier a file belongs to and what changing it costs. Read before adding code. |
| [DEPRECATED.md](DEPRECATED.md) | generated | leaves we decided not to keep. Tracked, not deleted. |
| [BREAKAGE.md](BREAKAGE.md) | generated | what does not load, why, and which pile it falls in. |
| [DIVERGENCE.md](DIVERGENCE.md) | generated | how far `main` has drifted, one row per sync. |
| [SCRIPT_MIGRATION_LIST.md](SCRIPT_MIGRATION_LIST.md) | hand | the widget→script backlog that RS-003 works through. |
| `upstream-verdicts.tsv` | generated | does upstream's own copy of each broken file load? Committed so the ours/inherited split survives without re-running the check. |

Generated files say so at the top. **Do not hand-edit them** — change the rule
that produces them, or the manifest, and regenerate:

```bash
python test/breakage.py --vs-upstream     # BREAKAGE, DEPRECATED, upstream-verdicts
python tools/reforge/divergence.py      # DIVERGENCE
```

## Why this is not in docs/

Two reasons, and the second is the real one.

`docs/` is 70-odd files, most of them upstream's, and a standing rule buried in
that pile is a rule nobody finds. More importantly: `docs/` gets rewritten by the
transform and by upstream. Anything we must be able to trust needs to live where
upstream has no vote, which is why `rules/**` has its own `keep` entry at the top
of `tools/reforge/layout.toml`.

## Adding a rule

Give it the next `RS-` number, write it in `RESTRUCTURE.md` in the existing
shape — decision, why, how it is enforced, cost — and put the number in the
`note` of whatever manifest entry or gate implements it. A rule with no
enforcement is a note, not a rule; say so explicitly and mark it **OPEN**.
