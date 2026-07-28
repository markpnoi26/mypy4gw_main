# Divergence from upstream

How far `main` has drifted, recorded once per sync. Appended, never rewritten —
the trend is the point.

**conflict surface** is the number we care about: files upstream also owns that
we have *modified*. Those are the only ones that can ever conflict. Added files
in our own namespaces are free.

| date | vendor | ours | added | modified | deleted | conflict surface | where |
|---|---|---|---|---|---|---|---|
| 2026-07-28 | `55ec88a6` | 246 | 111 | 133 | 2 | 20 | Core 107, dev 78, Scripts 28, HeroAI 12 |
