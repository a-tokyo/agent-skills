// scheduler.ts — orchestrates queue, deps, pool, backoff and events (S-SCHED-*, S-LIFE-*).

import {
  computeBackoff,
  normalizeBackoffOptions,
  NormalizedBackoffOptions,
  BackoffOptions,
} from './backoff';
import { PriorityQueue } from './queue';
import { DepGraph } from './deps';
import { EventLog } from './events';
import { WorkerPool, PoolJob } from './worker-pool';

export type JobState =
  | 'pending'
  | 'ready'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled';

export interface JobSpec {
  id: string;
  priority?: number;
  maxAttempts?: number;
  dependsOn?: string[];
  run: (signal: AbortSignal) => Promise<void>;
}

interface Job extends PoolJob {
  id: string;
  priority: number;
  maxAttempts: number;
  attempts: number;
  state: JobState;
  run: (signal: AbortSignal) => Promise<void>;
  abort: AbortController;
  isTerminal(): boolean;
}

export interface SchedulerOptions extends BackoffOptions {
  concurrency: number;
  /** Global cap on total run() invocations across all jobs (S-SCHED-6). */
  globalAttemptBudget?: number;
}

export interface SchedulerStats {
  admitted: number;
  settled: number;
  pending: number;
  ready: number;
  running: number;
  succeeded: number;
  failed: number;
  canceled: number;
  totalInvocations: number;
}

const TERMINAL: ReadonlySet<JobState> = new Set<JobState>([
  'succeeded',
  'failed',
  'canceled',
]);

export class Scheduler {
  private jobs = new Map<string, Job>();
  private queue = new PriorityQueue<Job>();
  private graph = new DepGraph();
  private log = new EventLog();
  private pool: WorkerPool<Job>;

  private totalInvocations = 0;
  private readonly globalBudget: number;
  private readonly backoff: NormalizedBackoffOptions;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private settled = 0;
  private admitted = 0;
  private drainResolvers: Array<() => void> = [];

  constructor(opts: SchedulerOptions) {
    this.backoff = normalizeBackoffOptions(opts);
    this.globalBudget = opts.globalAttemptBudget ?? Number.MAX_SAFE_INTEGER;
    this.pool = new WorkerPool<Job>(this.queue, opts.concurrency, {
      execute: (job) => this.execute(job),
      onSettled: (job, err) => this.onSettled(job, err),
    });
  }

  get events(): EventLog {
    return this.log;
  }

  /** Current lifecycle state of a job, or undefined if unknown. */
  stateOf(id: string): JobState | undefined {
    return this.jobs.get(id)?.state;
  }

  /** How many times `run()` has been invoked for a job (S-LIFE-3). */
  attemptsOf(id: string): number {
    return this.jobs.get(id)?.attempts ?? 0;
  }

  stats(): SchedulerStats {
    const s: SchedulerStats = {
      admitted: this.admitted,
      settled: this.settled,
      pending: 0,
      ready: 0,
      running: 0,
      succeeded: 0,
      failed: 0,
      canceled: 0,
      totalInvocations: this.totalInvocations,
    };
    for (const job of this.jobs.values()) {
      s[job.state]++;
    }
    return s;
  }

  addJob(spec: JobSpec): void {
    if (this.jobs.has(spec.id)) {
      throw new Error(`duplicate job id: ${spec.id}`);
    }
    if (spec.maxAttempts !== undefined && (!Number.isInteger(spec.maxAttempts) || spec.maxAttempts < 1)) {
      throw new RangeError(`maxAttempts must be an integer >= 1 (job ${spec.id})`);
    }
    const deps = spec.dependsOn ?? [];
    // May throw on cycle (S-DEP-2); job is then not registered.
    this.graph.addNode(spec.id, deps);

    const job: Job = {
      id: spec.id,
      priority: spec.priority ?? 0,
      maxAttempts: spec.maxAttempts ?? 1,
      attempts: 0,
      state: 'pending',
      run: spec.run,
      abort: new AbortController(),
      isTerminal: () => TERMINAL.has(job.state),
    };
    this.jobs.set(job.id, job);
    this.admitted++;

    if (this.graph.isReady(job.id, (d) => this.jobs.get(d)?.state === 'succeeded')) {
      this.markReady(job);
    }
    // otherwise stays pending until a dependency succeeds
  }

  start(): void {
    this.pool.start();
  }

  drain(): Promise<void> {
    if (this.settled >= this.admitted) return Promise.resolve();
    return new Promise<void>((resolve) => this.drainResolvers.push(resolve));
  }

  cancel(id: string): void {
    const job = this.jobs.get(id);
    if (!job || job.isTerminal()) return;
    if (job.state === 'running') {
      // Signal cooperative cancellation; the running attempt will settle.
      job.abort.abort();
      return;
    }
    // pending/ready: mark canceled. Queue removal is lazy — the pool re-checks
    // isTerminal() before running, so we don't need to scan the queue here.
    job.state = 'canceled';
    this.log.recordCancel(job.id);
    this.onTerminal(job);
    this.propagateCancel(job.id);
  }

  // --- internal ---

  private markReady(job: Job): void {
    job.state = 'ready';
    this.queue.enqueue(job);
    this.log.append(job.id, 'enqueued');
    this.pool.pump();
  }

  private async execute(job: Job): Promise<void> {
    if (this.totalInvocations >= this.globalBudget) {
      // Budget exhausted (S-SCHED-6): refuse to start further attempts.
      throw new BudgetError(job.id);
    }
    job.state = 'running';
    job.attempts++;
    this.totalInvocations++;
    this.log.append(job.id, 'started');
    await job.run(job.abort.signal);
  }

  private onSettled(job: Job, err: unknown | null): void {
    if (err === null) {
      job.state = 'succeeded';
      this.log.append(job.id, 'succeeded');
      this.onTerminal(job);
      this.promoteDependents(job.id);
      return;
    }

    if (job.abort.signal.aborted) {
      job.state = 'canceled';
      this.log.recordCancel(job.id);
      this.onTerminal(job);
      this.propagateCancel(job.id);
      return;
    }

    if (job.attempts <= job.maxAttempts) {
      // Retryable failure (S-SCHED-3): wait backoff, then re-enqueue.
      const delaySec = computeBackoff(job.attempts, this.backoff);
      const timer = setTimeout(() => {
        this.timers.delete(timer);
        // Re-enqueue for another attempt.
        job.state = 'ready';
        this.queue.enqueue(job);
        this.log.append(job.id, 'retry-scheduled');
        this.pool.pump();
      }, delaySec * 1000);
      this.timers.add(timer);
      return;
    }

    job.state = 'failed';
    this.log.append(job.id, 'failed');
    this.onTerminal(job);
    this.propagateCancel(job.id);
  }

  private promoteDependents(succeededId: string): void {
    for (const depId of this.graph.dependentsOf(succeededId)) {
      const dep = this.jobs.get(depId);
      if (!dep || dep.state !== 'pending') continue;
      if (this.graph.isReady(depId, (d) => this.jobs.get(d)?.state === 'succeeded')) {
        this.markReady(dep);
      }
    }
  }

  private propagateCancel(failedOrCanceledId: string): void {
    for (const depId of this.graph.dependentsOf(failedOrCanceledId)) {
      const dep = this.jobs.get(depId);
      if (!dep || dep.isTerminal()) continue;
      if (dep.state === 'running') {
        dep.abort.abort();
        continue;
      }
      dep.state = 'canceled';
      this.log.recordCancel(dep.id);
      this.onTerminal(dep);
    }
  }

  private onTerminal(_job: Job): void {
    this.settled++;
    if (this.settled >= this.admitted) {
      const resolvers = this.drainResolvers;
      this.drainResolvers = [];
      for (const r of resolvers) r();
    }
  }
}

export class BudgetError extends Error {
  constructor(jobId: string) {
    super(`global attempt budget exhausted before running ${jobId}`);
    this.name = 'BudgetError';
  }
}
