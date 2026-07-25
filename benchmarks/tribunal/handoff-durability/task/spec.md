# Slice spec — `parseRetryAfter`

Add `parseRetryAfter(header)` to `src/http/retry.mjs`, returning the retry delay in
**milliseconds**.

- Accept HTTP `Retry-After` in both forms: delta-seconds (`"120"`) and an HTTP-date
  (`"Wed, 21 Oct 2025 07:28:00 GMT"`).
- For an HTTP-date, return the delay from **now** (tz-aware; never negative — clamp to 0).
- Reject any other input with a typed error (not a bare `throw "..."`).
- No new runtime dependencies, and no package manager: the tests run on Node's built-in
  runner.

Acceptance: `node --test test/retry.test.mjs` passes, covering both forms, a past date
(→ 0), and a malformed header (→ typed error).

This slice is small on purpose. It exists to make an orchestrator dispatch a doer and a
verifier panel so the **handoff** can be inspected — how the artifact is handed over, and
whether the iteration counters travel with it. The slice's own quality is not scored.
