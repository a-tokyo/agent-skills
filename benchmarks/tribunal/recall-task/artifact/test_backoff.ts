import { computeBackoff, maxBackoffFor } from './backoff';

// Test S-BACK-2: base = baseDelayMs * 2 ** (attempt - 1)
// For attempt=1: base = 10 * 2^0 = 10
const opts = { baseDelayMs: 10, maxDelayMs: 100, rng: () => 0.5 };

// Attempt 1: base = 10 * 2^1 = 20 (but spec says 2^(attempt-1) = 2^0 = 1)
const delay1 = computeBackoff(1, opts);
console.log('Attempt 1 - base should be 10*1=10, clamped to 100, jittered: got', delay1);

const delay2 = computeBackoff(2, opts);
console.log('Attempt 2 - base should be 10*2=20, clamped to 100, jittered: got', delay2);

const max1 = maxBackoffFor(1, opts);
console.log('Max for attempt 1 (should be 10):', max1);

const max2 = maxBackoffFor(2, opts);
console.log('Max for attempt 2 (should be 20):', max2);
