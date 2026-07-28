"""Purpose-specific BT builds that are NOT general combat builds.

Anything under this package is structurally excluded from contract matching:
`BuildRegistry._iter_matchable_builds` drops it, so neither the HeroAI BT combat
engine nor the legacy engine will ever auto-select it for an account. Exclusion
is by location, not by flag — there is no `is_combat_automator_compatible=False`
to forget.

These builds are reached only by explicit instantiation (a farm script) or by
`bot.AddBuild(...)` on a BottingTree.

Put a build here when it exists for one scenario — a specific farm route, a
specific boss, a specific chest run — rather than as a rotation HeroAI should
pick up when it sees a matching skillbar.
"""
