import { Scheduler } from './scheduler';
import { computeBackoff } from './backoff';

async function testBasic() {
  console.log('=== Test 1: Basic execution ===');
  const s = new Scheduler({
    concurrency: 2,
    baseDelayMs: 100,
    maxDelayMs: 1000,
  });

  let jobAExecuted = 0;
  s.addJob({
    id: 'A',
    run: async () => { jobAExecuted++; },
  });

  s.start();
  await s.drain();
  console.log('Job A executed', jobAExecuted, 'times');
}

async function testBackoffBug() {
  console.log('\n=== Test 2: Backoff calculation bug ===');
  const delay1 = computeBackoff(1, { baseDelayMs: 100, maxDelayMs: 1000, rng: () => 1 });
  console.log('First failure backoff (expect 100):', delay1);
  
  const delay2 = computeBackoff(2, { baseDelayMs: 100, maxDelayMs: 1000, rng: () => 1 });
  console.log('Second failure backoff (expect 200):', delay2);
}

async function testQueuePeek() {
  console.log('\n=== Test 3: Queue peek correctness ===');
  const { PriorityQueue } = await import('./queue');
  const q = new PriorityQueue<{id: string; priority: number}>();
  q.enqueue({id: 'a', priority: 5});
  q.enqueue({id: 'b', priority: 10});
  
  console.log('Peek (should be b with priority 10):', q.peek());
  const d = q.dequeue();
  console.log('Dequeued:', d?.id, 'with priority', d?.priority);
}

(async () => {
  try {
    await testBasic();
    await testBackoffBug();
    await testQueuePeek();
  } catch (e) {
    console.error('Test error:', (e as any)?.message || e);
  }
})();
