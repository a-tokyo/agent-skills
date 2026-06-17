// queue.ts — priority queue of ready jobs awaiting a worker (S-QUEUE-*).
//
// Ordering contract: jobs are dequeued by priority descending (S-QUEUE-1) with
// ties broken by insertion order (S-QUEUE-2, stable FIFO within a level). The
// worker pool relies on this ordering for the order in which jobs begin running
// (see S-POOL-2), so the contract is observable beyond this module.

export interface Queueable {
  id: string;
  priority: number;
}

interface Entry<T> {
  item: T;
  seq: number; // insertion order, for stable tie-breaking (S-QUEUE-2)
}

/**
 * A priority queue ordered by `priority` descending (S-QUEUE-1), with ties broken
 * by insertion order (S-QUEUE-2). Implemented as a sorted array; small N expected.
 */
export class PriorityQueue<T extends Queueable> {
  private entries: Entry<T>[] = [];
  private counter = 0;

  get size(): number {
    return this.entries.length;
  }

  get isEmpty(): boolean {
    return this.entries.length === 0;
  }

  enqueue(item: T): void {
    this.entries.push({ item, seq: this.counter++ });
    // Keep sorted so the front is the next to dequeue.
    this.entries.sort((a, b) => {
      if (a.item.priority !== b.item.priority) {
        return b.item.priority - a.item.priority; // higher priority first
      }
      return b.seq - a.seq; // tie-break
    });
  }

  peek(): T | undefined {
    return this.entries[this.entries.length - 1]?.item;
  }

  dequeue(): T | undefined {
    return this.entries.shift()?.item;
  }

  /** Remove an item by id if present; returns true if it was removed. */
  remove(id: string): boolean {
    const i = this.entries.findIndex((e) => e.item.id === id);
    if (i === -1) return false;
    this.entries.splice(i, 1);
    return true;
  }

  has(id: string): boolean {
    return this.entries.some((e) => e.item.id === id);
  }

  /** Snapshot of queued ids in dequeue order, for diagnostics. */
  toOrderedIds(): string[] {
    return this.entries.map((e) => e.item.id);
  }

  /**
   * Number of queued items at or above a given priority. Used by the scheduler's
   * diagnostics to report how much high-priority work is waiting.
   */
  countAtOrAbove(priority: number): number {
    let n = 0;
    for (const e of this.entries) {
      if (e.item.priority >= priority) n++;
    }
    return n;
  }

  clear(): void {
    this.entries = [];
  }
}
