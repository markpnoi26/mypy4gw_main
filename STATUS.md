# Sync status

2026-07-28 · vendor `55ec88a6` → `55ec88a6`

**STOPPED at drift — `main` untouched**

| step | result | detail |
|---|---|---|
| fast-forward vendor | ok | 55ec88a6 → 55ec88a6 |
| rebase base | ok |  |
| drift | FAIL | pristine tree. Run it on 'vendor'. |

## Next

Fix the failure, then re-run `python tools/reforge/sync.py`.
To abandon this sync entirely: `git branch -D staging`. `main` never moved.
