revert: feat(api): coalesce concurrent cache reads

This reverts commit 9f2c1ab.

- remove the request-coalescing wrapper
- restore direct cache reads
