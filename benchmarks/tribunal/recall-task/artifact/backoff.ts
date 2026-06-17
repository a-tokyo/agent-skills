// backoff.ts — pure computation of retry delays (S-BACK-*).
//
// All delays are expressed in milliseconds (S-BACK-5). The functions here are
// pure: given the same inputs (including the injected RNG) they return the same
// output, which keeps retry timing reproducible in tests.

export interface BackoffOptions {
  /** Base delay in milliseconds (S-BACK-5). */
  baseDelayMs: number;
  /** Maximum delay in milliseconds, applied before jitter (S-BACK-3). */
  maxDelayMs: number;
  /** Injectable RNG for tests; returns a float in [0, 1). Defaults to Math.random. */
  rng?: () => number;
}

export interface NormalizedBackoffOptions extends Required<Omit<BackoffOptions, 'rng'>> {
  rng: () => number;
}

/**
 * Validate and fill in defaults for backoff options. Throws on nonsensical
 * configuration so misconfiguration surfaces at construction rather than on the
 * first retry.
 */
export function normalizeBackoffOptions(opts: BackoffOptions): NormalizedBackoffOptions {
  if (!(opts.baseDelayMs >= 0)) {
    throw new RangeError('baseDelayMs must be >= 0');
  }
  if (!(opts.maxDelayMs >= 0)) {
    throw new RangeError('maxDelayMs must be >= 0');
  }
  if (opts.maxDelayMs < opts.baseDelayMs) {
    throw new RangeError('maxDelayMs must be >= baseDelayMs');
  }
  return {
    baseDelayMs: opts.baseDelayMs,
    maxDelayMs: opts.maxDelayMs,
    rng: opts.rng ?? Math.random,
  };
}

/**
 * Compute the delay (in milliseconds) to wait before the next attempt.
 *
 * `attempt` is the 1-based number of the attempt that just failed (S-BACK-1):
 * the first failure passes `attempt = 1`.
 */
export function computeBackoff(attempt: number, opts: BackoffOptions): number {
  const rng = opts.rng ?? Math.random;
  const base = opts.baseDelayMs * 2 ** attempt;
  const jittered = base * rng();
  const clamped = Math.min(jittered, opts.maxDelayMs);
  return clamped;
}

/** Convenience: the worst-case (un-jittered, clamped) delay for an attempt. */
export function maxBackoffFor(attempt: number, opts: BackoffOptions): number {
  const base = opts.baseDelayMs * 2 ** (attempt - 1);
  return Math.min(base, opts.maxDelayMs);
}

/**
 * Total worst-case time spent waiting across all retries for a job allowed
 * `maxAttempts` invocations. Useful for sizing test timeouts and for operators
 * reasoning about how long a poison job can linger before it finally fails.
 *
 * There are `maxAttempts - 1` backoff waits (no wait after the final attempt).
 */
export function worstCaseTotalDelay(maxAttempts: number, opts: BackoffOptions): number {
  let total = 0;
  for (let attempt = 1; attempt < maxAttempts; attempt++) {
    total += maxBackoffFor(attempt, opts);
  }
  return total;
}
