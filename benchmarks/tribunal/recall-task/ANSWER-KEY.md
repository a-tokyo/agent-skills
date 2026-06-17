# ANSWER KEY — `taskq` v3 fixture

Authored blind from `spec.md`. **13 defects** (3 Tier-1, 4 Tier-2, 6 Tier-3 of which **5 are
cross-file**), **1 spec contradiction**, **3 traps**.

Toolchain: TypeScript 5.9.3. Compilation verified with:

```
npx tsc -p artifact/tsconfig.json      # exits 0, no diagnostics — PASS
```

Defect scenarios were either **executed** (a throwaway `tsx` harness that imports the artifact
and asserts the observed value) or **hand-traced**; each entry says which. Line numbers refer to
the files under `artifact/`.

Legend: cross-file means the *cause* lives in one file and the *wrong observable behavior* is in
another (or the violated contract is owned by another module).

---

## Tier-1 defects (single-site; a careful linear reader should catch)

### D1 — backoff exponent off-by-one
- **Tier:** 1. **File:** `backoff.ts:50` (inside `computeBackoff`). **Cross-file:** no.
- **Clause:** S-BACK-2 — "`base = baseDelayMs * 2 ** (attempt - 1)` … the delay after the first
  failure (`attempt = 1`) is `baseDelayMs`."
- **Defect:** code computes `opts.baseDelayMs * 2 ** attempt` (exponent `attempt`, not
  `attempt - 1`), doubling every delay.
- **Scenario (executed):** `computeBackoff(1, {baseDelayMs:100, maxDelayMs:1e9, rng:()=>1})`
  returns **200**; S-BACK-2 requires **100**. (`maxBackoffFor(1, …)` correctly returns 100,
  confirming the intended value.)

### D2 — `peek()` disagrees with `dequeue()`
- **Tier:** 1. **File:** `queue.ts:46` (`peek`). **Cross-file:** no.
- **Clause:** S-QUEUE-4 — "`peek()` returns the job that `dequeue()` would return, without
  removing it."
- **Defect:** `peek` returns `this.entries[this.entries.length - 1]` (the *last* / lowest-priority
  entry) while `dequeue` returns `entries.shift()` (the *first* / highest). They disagree.
- **Scenario (executed):** enqueue `{hi,pri9}`,`{lo,pri1}`. `peek()` → `"lo"`; `dequeue()` →
  `"hi"`. They must match.

### D3 — event `seq` starts at 0, not 1
- **Tier:** 1. **File:** `events.ts:46,50` (`nextSeq = 0`; `seq: this.nextSeq++`). **Cross-file:** no.
- **Clause:** S-EVT-2 — "a strictly increasing integer `seq` starting at 1 for the first event."
- **Defect:** `nextSeq` initialized to 0, so the first appended event has `seq = 0`.
- **Scenario (executed):** `new EventLog().append('j','enqueued',0).seq` → **0**; S-EVT-2 requires
  **1**. (Monotonicity/gap-free properties still hold; only the start value is wrong.)

---

## Tier-2 defects (cross-reference 2 clauses or 2 sites in the SAME file)

### D4 — backoff clamps AFTER jitter instead of before
- **Tier:** 2. **File:** `backoff.ts:50-52` (`computeBackoff`). **Cross-file:** no (two clauses, one site).
- **Clauses:** S-BACK-3 ("clamped to `maxDelayMs` **before** jitter: `clamped = min(base, maxDelayMs)`")
  + S-BACK-4 ("returned delay is uniform in `[0, clamped)` … therefore always `< maxDelayMs`").
- **Defect:** code does `jittered = base * rng()` then `clamped = min(jittered, maxDelayMs)` —
  jitter then clamp. This (a) skews the distribution toward `maxDelayMs` (a large fraction of
  draws pile up at the cap) and (b) makes the result able to *equal* `maxDelayMs`, violating the
  strict `< maxDelayMs` guarantee in S-BACK-4.
- **Scenario (executed):** `computeBackoff(10,{baseDelayMs:100,maxDelayMs:500,rng:()=>0.01})`.
  Spec order: `base=100*2**9=51200` (using correct exponent) → `clamped=min(51200,500)=500` →
  jitter `0.01*500=5`. Code returns **500** (because `jittered=51200*0.01=512` → `min(512,500)=500`).
  Observed 500 vs spec ≈5, and 500 == maxDelayMs (violates strict `<`).
- *Note:* D4 and D1 are independent (exponent vs clamp order); both must be fixed.

### D5 — `recordCancel` appends without notifying subscribers
- **Tier:** 2. **File:** `events.ts:61-64` (`recordCancel`) vs `events.ts:48-54` (`append`). **Cross-file:** no (two sites, same file).
- **Clauses:** S-EVT-1 ("Every state transition appends exactly one event") + S-EVT-4 ("Listeners
  registered before an event is appended MUST all observe that event").
- **Defect:** `append` runs `for (const l of this.listeners) l(e)`; `recordCancel` pushes the
  event but omits the notification loop. Cancellation events are therefore invisible to
  subscribers, even though they are appended to the log.
- **Scenario (executed):** subscribe a collector; `append('j','started')` then `recordCancel('j')`.
  Collector sees `["started"]` only; the log contains `["started","canceled"]`. The `canceled`
  transition was never delivered to the listener.

### D6 — readiness uses "ANY dependency succeeded" instead of "ALL"
- **Tier:** 2. **File:** `deps.ts:62-68` (`isReady`). **Cross-file:** no (manifests in deps; surfaces in scheduler, but the bug is self-evident in deps).
- **Clause:** S-DEP-3 — "A job becomes eligible … only when **all** of its dependencies are in the
  `succeeded` state."
- **Defect:** the loop returns `true` as soon as one dependency is succeeded
  (`if (isSucceeded(d)) return true`). Correct logic returns `false` if *any* dependency is not
  yet succeeded.
- **Scenario (executed):** `X` depends on `A,B`; only `A` succeeded.
  `isReady('X', d => d==='A')` → **true**; S-DEP-3 requires **false**. (Downstream this lets the
  scheduler run `X` before `B` finishes.)

### D7 — FIFO tie-break reversed (LIFO within a priority level)
- **Tier:** 2. **File:** `queue.ts:38-43` (`enqueue` comparator). **Cross-file:** no (two clauses).
- **Clauses:** S-QUEUE-1 (priority desc) + S-QUEUE-2 ("Ties … MUST be broken by **insertion
  order** … the one enqueued earliest is dequeued first").
- **Defect:** comparator tie-break returns `b.seq - a.seq` (descending seq), so among equal
  priority the *latest*-inserted is dequeued first — LIFO, not FIFO. Priority ordering itself
  (`b.priority - a.priority`) is correct, which is why this needs both clauses to spot.
- **Scenario (executed):** enqueue `A,B,C` all priority 5; dequeue order is **C,B,A**; S-QUEUE-2
  requires **A,B,C**.

---

## Tier-3 defects (subtle; 5 of 6 cross-file)

### D8 — backoff unit mismatch: ms treated as seconds
- **Tier:** 3. **Cross-file:** YES — cause `scheduler.ts:216,224`; violated contract owned by `backoff.ts` (S-BACK-5).
- **Clause:** S-BACK-5 — "`baseDelayMs` and `maxDelayMs` are expressed in **milliseconds** and
  this is the unit used everywhere a delay crosses a module boundary." (Also S-BACK-1: the return
  is "a delay in milliseconds".)
- **Defect:** `computeBackoff` returns **ms**, but the scheduler names it `delaySec` and schedules
  `setTimeout(fn, delaySec * 1000)`, treating the value as seconds. Every retry waits **1000×**
  too long.
- **Scenario (executed):** `baseDelayMs:10, maxDelayMs:1000, rng:()=>1`, a job with
  `maxAttempts:2` that always throws. `computeBackoff(1,…)=10*2**1=20` (per code). Scheduler waits
  `20*1000 = 20000 ms`. Within a 400 ms observation window the retry **never fires** (observed: the
  2nd attempt had not run). Correct behavior: a sub-second retry.
- **Single-pass catch estimate:** ~35%. The `* 1000` and the `delaySec` name are local and look
  intentional; catching it requires recalling that `computeBackoff` is documented in ms in a
  *different* file. Most linear readers accept "delay × 1000" as benign.

### D9 — initial worker fill launches in reverse priority order
- **Tier:** 3. **Cross-file:** YES — cause `worker-pool.ts:44-58` (`start`); violated contract owned by `queue.ts` (S-QUEUE-1/2) via S-POOL-2.
- **Clauses:** S-POOL-2 — "The order in which jobs begin running MUST … follow the queue's
  ordering contract (S-QUEUE-1/2)" + S-QUEUE-1 (priority desc).
- **Defect:** `start()` dequeues an initial batch in *correct* order, then launches it with
  `for (let i = batch.length - 1; i >= 0; i--)` — reversed. With `concurrency > 1` the
  highest-priority jobs (front of the batch) start **last**. Refills via `pump()` are in-order, so
  only the initial fill is wrong — easy to miss.
- **Scenario (executed):** `concurrency:3`, jobs `P9,P5,P1` (priorities 9,5,1). Observed start
  order: **P1, P5, P9**; S-POOL-2 requires **P9, P5, P1**.
- **Single-pass catch estimate:** ~30%. The batch-then-launch code reads as a deliberate
  optimization with a plausible comment; the reversed loop bound is one character of intent. The
  ordering contract it breaks lives in `queue.ts`/`spec`, so a reader focused on `worker-pool.ts`
  alone sees nothing obviously wrong.

### D10 — retry comparison off-by-one exceeds `maxAttempts`
- **Tier:** 3. **Cross-file:** YES — cause `scheduler.ts:214` (comparison in `onSettled`); the extra
  `run()` is actually invoked by `worker-pool.ts` (`runOne` → `execute` → `job.run`).
- **Clauses:** S-SCHED-5 ("MUST NOT invoke `run()` more than `maxAttempts` times") + S-LIFE-3
  (same count, attempts = invocations) + S-LIFE-4 (failed only after attempt `maxAttempts`).
- **Defect:** retry guard is `if (job.attempts <= job.maxAttempts)` (should be `<`). Since
  `attempts` is incremented *before* the body runs, `<=` permits one extra invocation.
- **Scenario (executed):** `maxAttempts:2`, always-throwing job. Trace/observed: attempt1
  (attempts=1, `1<=2` retry), attempt2 (attempts=2, `2<=2` retry — BUG), attempt3 (attempts=3,
  `3<=2` false → failed). `run()` invoked **3 times**; `attemptsOf('flaky')` = **3**; S-SCHED-5
  caps it at 2. Event log shows three `started` events.
- **Single-pass catch estimate:** ~40%. `<=` vs `<` is a classic off-by-one, but the
  incrementing happens in `execute` (a different method/file boundary) and the comparison reads
  naturally; the reader must connect "attempts++ before run" in one place with "attempts <= max"
  in another to see the extra run.

### D11 — cancellation propagation is not transitive
- **Tier:** 3. **Cross-file:** YES — cause `scheduler.ts:245-256` (`propagateCancel`); relies on
  `deps.ts` graph and the available-but-unused `deps.ts:transitiveDependentsOf`.
- **Clause:** S-DEP-4 — "If any dependency … reaches a terminal `failed` or `canceled` state, the
  dependent job is **canceled** … This cancellation propagates **transitively** to that job's
  dependents."
- **Defect:** `propagateCancel` cancels only **direct** dependents via `graph.dependentsOf(id)`
  and does not recurse (nor does it call the existing `graph.transitiveDependentsOf`). A
  grand-dependent is never canceled. (It marks the direct dependent canceled and calls
  `onTerminal`, but does not re-run propagation for that newly-canceled node.)
- **Scenario (executed):** chain `C → B → A` (C depends on B, B on A). `A` fails. Observed: `B`
  state = `canceled`; `C` state = **`pending`** (never canceled). S-DEP-4 requires `C` canceled.
  Consequence: `C` waits forever and `drain()` never resolves (also implicates S-SCHED-8).
- **Single-pass catch estimate:** ~30%. `propagateCancel` looks complete in isolation (it handles
  running vs queued dependents). The missing transitivity only shows up when you cross-reference
  S-DEP-4's "transitively" against the single non-recursive loop — and the correct helper sits
  unused in `deps.ts`, which is itself a clue most linear readers won't connect.

### D12 — cycle detection only catches self-edges
- **Tier:** 3. **Cross-file:** YES — cause `deps.ts:107-114` (`wouldCycle`); the deadlock manifests in
  `scheduler.ts` (jobs stuck `pending`, `drain()` hangs — S-SCHED-8).
- **Clause:** S-DEP-2 — "Adding a job whose dependencies would introduce a cycle MUST be rejected …
  Cycle detection MUST consider dependencies on jobs already registered."
- **Defect:** `wouldCycle` reduced to a self-edge check (`for (const d of dependencyIds) if (d===id)
  return true`). It no longer traverses existing edges, so transitive cycles are admitted. The
  comment ("Indirect back-references are resolved as the graph is built") rationalizes it but is
  false.
- **Scenario (executed):** `addNode('A',['B'])`, `addNode('B',['C'])`, then `addNode('C',['A'])`
  creates `A→B→C→A`. Observed: the third add **does not throw** (cycle admitted); S-DEP-2 requires
  it to throw. In the scheduler these three jobs would never become ready and `drain()` would hang.
- **Single-pass catch estimate:** ~25%. The function compiles, has a confident comment, and
  self-edge detection looks "good enough." Recognizing it requires knowing graph-cycle detection
  needs traversal AND constructing a 3-node example — unlikely in one linear pass, especially since
  the visible failure (deadlock) is in a different file.

### D13 — budget rejection is mishandled as a retryable failure
- **Tier:** 3. **Cross-file:** YES — cause `scheduler.ts:185-187` (`execute` throws `BudgetError`
  before incrementing `attempts`); the throw is caught by `worker-pool.ts:runOne`'s generic
  `catch` and routed back to `scheduler.ts:onSettled`, which treats it as a normal failure.
- **Clauses:** S-EVT-1 ("Every state transition appends exactly one event" — here events are
  emitted with no real transition) + S-SCHED-3 (retry is for a failed `run()`, after a backoff
  wait) + S-LIFE-4 ("`failed` only after `run()` has thrown on attempt `maxAttempts`").
- **Defect:** when the global budget is exhausted, `execute` throws *before* `job.attempts++` and
  before `job.run` is ever called. `runOne` catches it like any rejection; `onSettled` sees
  `attempts` unchanged (e.g. 0) → `0 <= maxAttempts` true → it schedules `retry-scheduled` and
  re-enqueues. The job loops forever emitting `retry-scheduled` for a body that never runs, never
  reaching a terminal state.
- **Scenario (executed):** `globalAttemptBudget:1`, two independent jobs. One job consumes the
  budget and succeeds; the other observed with events
  `["enqueued","retry-scheduled","retry-scheduled", …]` (unbounded), state stuck at `ready`,
  never terminal → `drain()` never resolves.
- **Single-pass catch estimate:** ~20%. Requires mentally executing a throw in `execute`
  (`scheduler.ts`) propagating through the pool's `catch` (`worker-pool.ts`) and back into the
  retry branch (`scheduler.ts`) — a three-hop, two-file control-flow trace that a linear read of
  any single file will not reveal.

---

## Spec contradiction (flag/escalate, do NOT silently pick a side)

### C1 — global attempt budget vs guaranteed first execution
- **Clauses in conflict:**
  - S-SCHED-6: "the *total* number of `run()` invocations across *all* jobs MUST NOT exceed
    `globalAttemptBudget` … the scheduler MUST refuse to start further attempts."
  - S-SCHED-7: "if `globalAttemptBudget` is configured, it MUST be large enough that **every**
    registered job receives at least one `run()` invocation — the scheduler guarantees
    first-attempt execution for all admitted jobs."
- **Why unsatisfiable:** S-SCHED-6 caps total invocations at `globalAttemptBudget`; S-SCHED-7
  demands ≥ `N` invocations where `N` is the number of admitted jobs (one per job minimum). If a
  caller configures `globalAttemptBudget < N` (e.g. budget 1 with 5 independent jobs), the two
  clauses give contradictory mandates: refuse further attempts vs guarantee every job runs once.
  Both cannot hold. This is reachable in normal operation — nothing in the spec constrains
  `globalAttemptBudget` relative to job count, and a small budget is a natural operator choice.
- **Correct review behavior:** surface/escalate the contradiction (the spec must decide whether
  S-SCHED-7 is conditional on a sufficiently large budget, or whether admission should be
  rejected when the budget is too small). Do NOT silently assume one wins.
- **Relationship to D13:** D13 is the *code* defect in how budget exhaustion is handled; C1 is
  the *spec* impossibility that the situation even reaches. They are independent — fixing D13
  (e.g. marking budget-rejected jobs terminal) still leaves the spec self-contradictory about
  whether those jobs were *allowed* to be rejected.

---

## Traps (look wrong, but are spec-compliant — do NOT report as defects)

### T1 — worker pool re-checks `isTerminal()` after dequeue (`worker-pool.ts:73`)
- **Why it looks wrong:** the queue is described as holding *ready* jobs, so re-checking
  terminality on a freshly dequeued job seems redundant/dead.
- **Why it is required:** S-SCHED-4 cancels a `pending`/`ready` job by marking it `canceled`
  **in place**; `scheduler.cancel` deliberately does NOT scan/remove it from the queue (lazy
  removal — see the comment at `scheduler.ts:168-169`). A canceled job therefore remains in the
  queue and *will* be dequeued; the pool MUST skip it. Removing the check would run a canceled
  job's `run()`, violating S-LIFE-5.
- **Verified (executed):** a low-priority job canceled while queued behind a running job is
  observed to *not* execute (`victim ran? false`) and ends in state `canceled`. The re-check is
  load-bearing.

### T2 — `promoteDependents` guard `dep.state !== 'pending'` (`scheduler.ts:238`)
- **Why it looks wrong:** when a dependency just succeeded, one might assume every dependent is
  still `pending`, making the guard a no-op.
- **Why it is required:** with multiple dependencies (S-DEP-3) and cancellation propagation
  (S-DEP-4), a dependent can already be `canceled` (a *different* dependency failed first) or
  already `ready`/`running` (promoted via another satisfied path). Without the guard, the
  scheduler would re-enqueue a canceled or in-flight job, breaking S-LIFE-5 ("a terminal job MUST
  NOT be executed again") and risking duplicate execution. The guard correctly restricts
  promotion to jobs still genuinely `pending`.

### T3 — full-jitter interval `[0, clamped)` can return near-zero delays (`backoff.ts`, `computeBackoff`)
- **Why it looks wrong:** the delay can come out very small (≈0), making retries appear to fire
  almost immediately — looks like a missing minimum-delay floor.
- **Why it is correct:** S-BACK-4 *mandates* full jitter, "a uniformly random value in the
  half-open interval `[0, clamped)`," explicitly allowing 0 and small values. There is no
  spec-required minimum delay; adding a floor would itself violate S-BACK-4. (Note: the *clamp
  ordering* in this function is a real defect — D4 — but the `[0, clamped)` lower bound and
  small-delay behavior are compliant. A reviewer must not conflate the two.)

---

## Expected verdict

**BLOCK.** Rationale: multiple Tier-3 cross-file defects cause non-recoverable behavior in normal
operation — D11 and D12 deadlock `drain()` (jobs never terminal), D13 produces an unbounded
event loop with no terminal state, and D10 violates the hard `maxAttempts`/`run()` safety cap.
These are correctness/liveness failures, not cosmetic; shipping is unsafe. Additionally the spec
contains a genuine contradiction (C1) that must be resolved before the budget feature can be
implemented correctly at all. A verdict of ITERATE would be defensible *only* if a reviewer caught
just the milder Tier-1/Tier-2 issues; given the liveness defects, BLOCK is correct.

## Calibration summary (single-pass catch probability)

| Defect | Tier | Cross-file | Est. single-pass catch |
|--------|------|-----------|------------------------|
| D1 | 1 | no  | ~80% |
| D2 | 1 | no  | ~75% |
| D3 | 1 | no  | ~80% |
| D4 | 2 | no  | ~55% |
| D5 | 2 | no  | ~50% |
| D6 | 2 | no  | ~55% |
| D7 | 2 | no  | ~50% |
| D8 | 3 | yes | ~35% |
| D9 | 3 | yes | ~30% |
| D10| 3 | yes | ~40% |
| D11| 3 | yes | ~30% |
| D12| 3 | yes | ~25% |
| D13| 3 | yes | ~20% |
| C1 (contradiction) | — | — | ~20% (requires reading S-SCHED-6 and S-SCHED-7 *together*) |

All six Tier-3 defects are estimated below 50% single-pass catch; the four most cross-file
(D9, D11, D12, D13) are at or below 30%. This is the gap a triangulating, multi-reader process
is expected to close versus one linear pass.
