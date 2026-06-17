import { PriorityQueue } from './queue';

// Test tie-breaking
const q = new PriorityQueue<{ id: string; priority: number }>();
q.enqueue({ id: 'A', priority: 5 });
q.enqueue({ id: 'B', priority: 5 });
q.enqueue({ id: 'C', priority: 5 });

console.log('peek:', q.peek()?.id, '(expect A)');
console.log('dequeue order:', [q.dequeue()?.id, q.dequeue()?.id, q.dequeue()?.id]);
