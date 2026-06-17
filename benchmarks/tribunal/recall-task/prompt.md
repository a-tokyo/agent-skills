# Task

You are reviewing `taskq`, an in-memory job scheduler / task-queue library written in TypeScript.

The normative specification is in `spec.md`. The implementation is the set of `.ts` files in
`artifact/` (`backoff.ts`, `queue.ts`, `deps.ts`, `events.ts`, `worker-pool.ts`, `scheduler.ts`).
The code compiles cleanly under `tsc`.

**Verify this codebase against its spec and deliver a verdict.**

Your verdict must be exactly one of:

- **PASS** — the implementation satisfies the spec; ship it.
- **ITERATE** — there are issues worth fixing, but nothing that should block shipping.
- **BLOCK** — there are issues serious enough that the code should not ship as-is.

Report your **findings**. For each finding, give:

- the location as `file:line` (a line or short range),
- a short description of the problem,
- the spec clause(s) it relates to, and
- why it is wrong (a concrete example or trace where useful).

Be precise: a finding that misidentifies behavior, or flags code that actually meets the spec,
counts against you. Ground every finding in the actual code and the actual spec text.
