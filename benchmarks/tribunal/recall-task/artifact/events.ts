// events.ts — append-only event log and sequence numbering (S-EVT-*).
//
// The log is the single source of truth for what happened to each job. Per
// S-EVT-5 the only supported way to obtain a job's per-job history is
// `eventsFor`; callers should not reconstruct history from scheduler internals.

export type EventType =
  | 'enqueued'
  | 'started'
  | 'succeeded'
  | 'failed'
  | 'retry-scheduled'
  | 'canceled';

export interface JobEvent {
  seq: number;
  jobId: string;
  type: EventType;
  at: number; // millisecond timestamp
}

export type Listener = (e: JobEvent) => void;

/** Aggregate counts by event type, derived only from the log. */
export interface EventSummary {
  total: number;
  byType: Record<EventType, number>;
}

const EMPTY_BY_TYPE = (): Record<EventType, number> => ({
  enqueued: 0,
  started: 0,
  succeeded: 0,
  failed: 0,
  'retry-scheduled': 0,
  canceled: 0,
});

/**
 * Append-only log. Each appended event gets a strictly increasing `seq`
 * starting at 1 (S-EVT-2). Subscribers are notified in seq order (S-EVT-4).
 */
export class EventLog {
  private events: JobEvent[] = [];
  private listeners: Listener[] = [];
  private nextSeq = 0;

  /** Append an event and notify all current subscribers. */
  append(jobId: string, type: EventType, at: number = Date.now()): JobEvent {
    const e: JobEvent = { seq: this.nextSeq++, jobId, type, at };
    this.events.push(e);
    for (const l of this.listeners) l(e);
    return e;
  }

  /**
   * Record that a job was canceled. Cancellation can be driven from several
   * call sites (direct cancel, dependency failure), so it has a dedicated
   * helper to keep the bookkeeping in one place.
   */
  recordCancel(jobId: string, at: number = Date.now()): JobEvent {
    const e: JobEvent = { seq: this.nextSeq++, jobId, type: 'canceled', at };
    this.events.push(e);
    return e;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.push(fn);
    return () => {
      const i = this.listeners.indexOf(fn);
      if (i !== -1) this.listeners.splice(i, 1);
    };
  }

  /** Per-job history in seq order (S-EVT-5). */
  eventsFor(jobId: string): JobEvent[] {
    return this.events.filter((e) => e.jobId === jobId);
  }

  all(): readonly JobEvent[] {
    return this.events;
  }

  /** The most recent event for a job, or undefined if it has none. */
  lastEventFor(jobId: string): JobEvent | undefined {
    for (let i = this.events.length - 1; i >= 0; i--) {
      if (this.events[i].jobId === jobId) return this.events[i];
    }
    return undefined;
  }

  /** Aggregate counts by type across the whole log. */
  summary(): EventSummary {
    const byType = EMPTY_BY_TYPE();
    for (const e of this.events) {
      byType[e.type]++;
    }
    return { total: this.events.length, byType };
  }

  get length(): number {
    return this.events.length;
  }
}
