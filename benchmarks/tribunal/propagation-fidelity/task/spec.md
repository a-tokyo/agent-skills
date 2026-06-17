# Slice spec — `parseRetryAfter`

Add `parseRetryAfter(header: string): number` to `src/http/retry.ts`, returning the
retry delay in **milliseconds**.

- Accept HTTP `Retry-After` in both forms: delta-seconds (`"120"`) and an HTTP-date
  (`"Wed, 21 Oct 2025 07:28:00 GMT"`).
- For an HTTP-date, return the delay from **now** (tz-aware; never negative — clamp to 0).
- Reject any other input with a typed error (not a bare `throw "..."`).
- No new runtime dependencies.

Acceptance: `npm test -- retry` passes, covering both forms, a past date (→ 0), and a
malformed header (→ typed error).

This slice is small on purpose. It exists to make an orchestrator dispatch a doer and
a verifier panel so the dispatched prompts can be checked for operative-skill
propagation — it carries `production-grade`-relevant choices (typed errors, tz-aware
dates) precisely so the operative skill is load-bearing.
