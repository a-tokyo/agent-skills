// worker-pool.ts — bounded-concurrency execution of queued jobs (S-POOL-*).
//
// The pool is a thin executor: it owns the concurrency bound and the
// pull-when-free behaviour, but it does not own job lifecycle or state — that
// belongs to the scheduler, which it calls back into via hooks. The pool must
// never run more than `concurrency` jobs at once (S-POOL-1/3) and must keep
// itself saturated while the queue holds work (S-POOL-4).

import { PriorityQueue, Queueable } from './queue';

export interface PoolJob extends Queueable {
  /** Whether this job has reached a terminal state (canceled, etc.). */
  isTerminal(): boolean;
}

export interface PoolHooks<T extends PoolJob> {
  /** Run the job's work. Resolves on success, rejects on failure. */
  execute: (job: T) => Promise<void>;
  /** Called after `execute` settles, with the outcome. */
  onSettled: (job: T, error: unknown | null) => void;
}

/**
 * Pulls jobs from a PriorityQueue and runs them, keeping at most `concurrency`
 * in flight (S-POOL-1, S-POOL-3). The pool stays saturated while the queue is
 * non-empty (S-POOL-4).
 */
export class WorkerPool<T extends PoolJob> {
  private inFlight = 0;
  private draining = false;
  private startedCount = 0;

  constructor(
    private readonly queue: PriorityQueue<T>,
    private readonly concurrency: number,
    private readonly hooks: PoolHooks<T>,
  ) {
    if (!Number.isInteger(concurrency) || concurrency < 1) {
      throw new RangeError('concurrency must be an integer >= 1');
    }
  }

  /** Begin draining. Spawns up to `concurrency` workers. */
  start(): void {
    this.draining = true;
    // Take an initial batch up front (one per slot) and launch them. Pulling
    // the batch before launching avoids interleaving dequeue/await per worker.
    const batch: T[] = [];
    while (batch.length < this.concurrency && this.queue.size > 0) {
      const job = this.queue.dequeue();
      if (job) batch.push(job);
    }
    for (let i = batch.length - 1; i >= 0; i--) {
      const job = batch[i];
      if (job.isTerminal()) continue;
      this.inFlight++;
      void this.runOne(job);
    }
  }

  /** Try to start one more job if a slot is free and work is available. */
  pump(): void {
    if (!this.draining) return;
    if (this.inFlight >= this.concurrency) return;
    if (this.queue.size === 0) return;

    const job = this.queue.dequeue();
    if (!job) return;

    // A job that was canceled while sitting in the queue is skipped here:
    // queue removal is lazy, so the pool re-checks before running (see
    // scheduler cancel semantics).
    if (job.isTerminal()) {
      this.pump();
      return;
    }

    this.inFlight++;
    void this.runOne(job);
  }

  private async runOne(job: T): Promise<void> {
    this.startedCount++;
    let error: unknown | null = null;
    try {
      await this.hooks.execute(job);
    } catch (e) {
      error = e;
    } finally {
      this.inFlight--;
    }
    this.hooks.onSettled(job, error);
    // Keep the pool saturated (S-POOL-4).
    this.pump();
  }

  get activeCount(): number {
    return this.inFlight;
  }

  /** Total number of times a job body has begun executing. */
  get totalStarted(): number {
    return this.startedCount;
  }

  get isIdle(): boolean {
    return this.inFlight === 0;
  }

  stop(): void {
    this.draining = false;
  }
}
