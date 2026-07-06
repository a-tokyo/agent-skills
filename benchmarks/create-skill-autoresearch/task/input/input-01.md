Staged diff summary:
- api/routes/exports.ts (new): adds GET /v1/exports endpoint streaming account data as CSV
- api/services/export-service.ts (new): builds the CSV stream with cursor pagination
- api/routes/index.ts (modified): registers the new exports route
