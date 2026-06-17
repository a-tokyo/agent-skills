# Specification — `taskq`: an in-memory job scheduler / task-queue library

`taskq` is a single-process, in-memory library for scheduling and running asynchronous
jobs with priorities, retries, dependencies, cancellation, a bounded worker pool, and an
append-only event log. This document is the **normative** specification. Every clause has a
stable ID of the form `S-<area>-<n>`. Implementations MUST satisfy every clause unless the
clause says MAY/SHOULD.

The library is organized into six modules:

| Module           | Responsibility                                              |
|------------------|-------------------------------------------------------------|
| `backoff.ts`     | Pure computation of retry delays.                           |
| `queue.ts`       | Priority queue holding *ready* jobs awaiting a worker.      |
| `deps.ts`        | Dependency graph (DAG) bookkeeping and cycle detection.     |
| `events.ts`      | Append-only event log and sequence numbering.               |
| `worker-pool.ts` | Bounded-concurrency execution of jobs pulled from the queue.|
| `scheduler.ts`   | Orchestrates the above; owns job lifecycle and state.       |

---

## 1. Job model and lifecycle (`S-LIFE-*`)

- **S-LIFE-1.** A job has a unique string `id`, an integer `priority` (higher value = more
  urgent), a `maxAttempts` (integer ≥ 1), a set of dependency ids, and a user-supplied
  async `run()` function.

- **S-LIFE-2.** A job moves through exactly these states: `pending` → `ready` → `running` →
  (`succeeded` | `failed` | `canceled`). A job is `pending` while it has unsatisfied
  dependencies; it becomes `ready` once all dependencies have `succeeded`; it is `running`
  while a worker executes its `run()`; it ends in a terminal state.

- **S-LIFE-3.** `attempts` counts the number of times `run()` has been **invoked** for a job.
  Before the first invocation `attempts` is 0. A job is permitted at most `maxAttempts`
  invocations of `run()` in total (the first try plus retries).

- **S-LIFE-4.** A job transitions to `failed` only after its `run()` has thrown on attempt
  number `maxAttempts` (i.e. retries are exhausted). If an earlier attempt throws and
  attempts remain, the job is scheduled for retry and returns to `ready` (after its backoff
  delay) rather than `failed`.

- **S-LIFE-5.** A terminal job (`succeeded`/`failed`/`canceled`) MUST NOT be executed again
  and MUST NOT change state.

---

## 2. Backoff (`S-BACK-*`)

- **S-BACK-1.** Retry delay is computed by `computeBackoff(attempt, opts)` where `attempt`
  is the **1-based number of the attempt that just failed** (so the first failure passes
  `attempt = 1`). The function returns a delay in **milliseconds** to wait before the next
  attempt.

- **S-BACK-2.** The base (un-jittered) delay grows exponentially:
  `base = baseDelayMs * 2 ** (attempt - 1)`. Thus the delay after the first failure
  (`attempt = 1`) is `baseDelayMs * 2 ** 0 = baseDelayMs`.

- **S-BACK-3.** The base delay MUST be clamped to `maxDelayMs` **before** jitter is applied:
  `clamped = min(base, maxDelayMs)`.

- **S-BACK-4.** Jitter is "full jitter": the returned delay is a uniformly random value in
  the half-open interval `[0, clamped)`. The returned value is therefore always
  `< maxDelayMs` and `>= 0`.

- **S-BACK-5.** `baseDelayMs` and `maxDelayMs` are expressed in **milliseconds** and this is
  the unit used everywhere a delay crosses a module boundary.

---

## 3. Priority queue (`S-QUEUE-*`)

- **S-QUEUE-1.** The ready queue orders jobs by `priority` descending (higher priority
  dequeued first).

- **S-QUEUE-2.** Ties (equal priority) MUST be broken by **insertion order**: among jobs of
  equal priority, the one enqueued earliest is dequeued first (stable FIFO within a priority
  level).

- **S-QUEUE-3.** `dequeue()` removes and returns the highest-priority job per S-QUEUE-1/2, or
  `undefined` if the queue is empty.

- **S-QUEUE-4.** `size` reflects the number of jobs currently in the queue. `peek()` returns
  the job that `dequeue()` would return, without removing it.

---

## 4. Dependencies (`S-DEP-*`)

- **S-DEP-1.** Dependencies form a directed acyclic graph (DAG): an edge `A → B` means "A
  depends on B" (B must succeed before A may run).

- **S-DEP-2.** Adding a job whose dependencies would introduce a cycle MUST be rejected:
  `addJob` throws and the job is not registered. Cycle detection MUST consider dependencies
  on jobs already registered.

- **S-DEP-3.** A job becomes eligible to move from `pending` to `ready` only when **all** of
  its dependencies are in the `succeeded` state.

- **S-DEP-4.** If any dependency of a job reaches a terminal `failed` or `canceled` state,
  the dependent job is **canceled** (it can never satisfy S-DEP-3). This cancellation
  propagates transitively to that job's dependents.

- **S-DEP-5.** `dependentsOf(id)` returns the ids of jobs that directly depend on `id`.

---

## 5. Events (`S-EVT-*`)

- **S-EVT-1.** Every state transition appends exactly one event to the log. Events are
  immutable once appended.

- **S-EVT-2.** Each event carries a strictly increasing integer `seq` starting at 1 for the
  first event. No two events share a `seq`; `seq` values are gap-free and monotonic in
  append order.

- **S-EVT-3.** An event records `{ seq, jobId, type, at }` where `type` is one of
  `enqueued | started | succeeded | failed | retry-scheduled | canceled` and `at` is a
  millisecond timestamp.

- **S-EVT-4.** `subscribe(fn)` registers a listener invoked for every subsequently appended
  event, in `seq` order. Listeners registered before an event is appended MUST all observe
  that event.

- **S-EVT-5.** The log supports `eventsFor(jobId)` returning that job's events in `seq`
  order, and this is the only supported way to obtain a job's per-job history.

---

## 6. Worker pool (`S-POOL-*`)

- **S-POOL-1.** The pool runs at most `concurrency` jobs simultaneously, where `concurrency`
  is an integer ≥ 1 fixed at construction.

- **S-POOL-2.** Whenever a worker becomes free and the ready queue is non-empty, the worker
  pulls the next job via `queue.dequeue()` and executes it. The order in which jobs begin
  running MUST therefore follow the queue's ordering contract (S-QUEUE-1/2).

- **S-POOL-3.** The number of in-flight jobs MUST never exceed `concurrency`, including
  during the window between dequeuing a job and awaiting its `run()`.

- **S-POOL-4.** When a job finishes (success or failure), the worker MUST attempt to pull
  more work so the pool stays saturated while the queue is non-empty.

---

## 7. Scheduler orchestration (`S-SCHED-*`)

- **S-SCHED-1.** `addJob(spec)` registers a job. If it has no dependencies it is enqueued as
  `ready`; otherwise it is held `pending` until S-DEP-3 is satisfied.

- **S-SCHED-2.** On a job's success, the scheduler MUST re-evaluate every job that directly
  depends on it and enqueue any that have now become `ready` (all deps succeeded).

- **S-SCHED-3.** On a retryable failure (attempts remain per S-LIFE-3/4), the scheduler waits
  `computeBackoff(attemptJustFailed, opts)` milliseconds and then re-enqueues the job as
  `ready`. The waiting job does NOT occupy a worker slot.

- **S-SCHED-4.** `cancel(id)` cancels a non-terminal job. A `running` job's `run()` is
  signaled via its `AbortSignal`; a `pending`/`ready` job is removed and marked `canceled`.
  Cancellation propagates per S-DEP-4.

- **S-SCHED-5.** **Retry cap.** Across its entire lifetime a single job MUST NOT invoke
  `run()` more than `maxAttempts` times (this is the same count as S-LIFE-3).

- **S-SCHED-6.** **Global retry budget.** To bound runaway workloads, the scheduler enforces
  a global cap: the *total* number of `run()` invocations across *all* jobs MUST NOT exceed
  `globalAttemptBudget` (a constructor option). When the budget would be exceeded, the
  scheduler MUST refuse to start further attempts.

- **S-SCHED-7.** Every job that is `addJob`-ed and whose dependencies all eventually succeed
  MUST be given the opportunity to run to completion: the scheduler MUST NOT drop a ready job
  while workers or budget remain. Specifically, if `globalAttemptBudget` is configured, it
  MUST be large enough that **every** registered job receives at least one `run()`
  invocation — the scheduler guarantees first-attempt execution for all admitted jobs.

- **S-SCHED-8.** `start()` begins draining the queue; `drain()` resolves once every admitted
  job has reached a terminal state.
