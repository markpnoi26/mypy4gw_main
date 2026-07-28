# Divergence from upstream

How far `main` has drifted, recorded once per sync. Appended, never rewritten —
the trend is the point.

**conflict surface** is the number we care about: files upstream also owns that
we have *modified*. Those are the only ones that can ever conflict. Added files
in our own namespaces are free.

| date | vendor | ours | added | modified | deleted | conflict surface | where |
|---|---|---|---|---|---|---|---|
| 2026-07-28 | `55ec88a6` | 135 | 112 | 22 | 1 | 19 | Core 106, HeroAI 12, docs 9, Scripts 4 |
