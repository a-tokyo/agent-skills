Staged diff summary:
- Revert of commit 9f2c1ab, which introduced request coalescing in the api cache layer and caused stale reads under load
- api/cache/coalesce.ts (deleted): removes the request-coalescing wrapper
- api/cache/index.ts (modified): restores direct cache reads
