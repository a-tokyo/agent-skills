Staged diff summary:
- Revert of commit 4e8d21c, which added retry-with-backoff to webhook delivery in the api worker and caused duplicate deliveries
- api/workers/webhooks.ts (modified): removes the retry-with-backoff wrapper and restores single-attempt delivery
