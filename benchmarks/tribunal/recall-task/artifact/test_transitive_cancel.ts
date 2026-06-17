import { Scheduler } from './scheduler';

const scheduler = new Scheduler({
  concurrency: 2,
  baseDelayMs: 10,
  maxDelayMs: 100,
});

// Build a chain: A -> B -> C -> D
// When B fails, C and D should transitively cancel
scheduler.addJob({
  id: 'A',
  run: async () => { console.log('A ran'); },
});

scheduler.addJob({
  id: 'B',
  dependsOn: ['A'],
  maxAttempts: 1,
  run: async () => { throw new Error('B fails'); },
});

scheduler.addJob({
  id: 'C',
  dependsOn: ['B'],
  run: async () => { console.log('C ran'); },
});

scheduler.addJob({
  id: 'D',
  dependsOn: ['C'],
  run: async () => { console.log('D ran'); },
});

scheduler.start();

(async () => {
  await scheduler.drain();
  
  console.log('\n=== Final States ===');
  console.log('A:', scheduler.stateOf('A'));
  console.log('B:', scheduler.stateOf('B'));
  console.log('C:', scheduler.stateOf('C'));
  console.log('D:', scheduler.stateOf('D'));
})();
