// Self-test for the engineering-rigor probes (score.v2.algo): naive FAILS, proper PASSES.
const s = require('./score.js');
let f = 0; const T = (n, c) => { if (!c) f++; console.log((c ? '  ok  ' : 'FAIL  ') + n); };
const blk = (...l) => '```python\n' + l.join('\n') + '\n```';
const A = (code, task) => s.algo(code, { vars: { task } }).optimal;

T('two-sum naive (nested loop) FAILS', A(blk('def f(a,t):', '  for i in range(len(a)):', '    for j in range(i+1,len(a)):', '      if a[i]+a[j]==t: return True', '  return False'), 'two distinct elements sum to target') === false);
T('two-sum set PASSES', A(blk('def f(a,t):', '  seen=set()', '  for x in a:', '    if t-x in seen: return True', '    seen.add(x)', '  return False'), 'two distinct elements sum to target') === true);

T('fib naive recursion FAILS', A(blk('def fib(n):', '  if n<2: return n', '  return fib(n-1)+fib(n-2)'), 'nth fibonacci') === false);
T('fib iterative PASSES', A(blk('def fib(n):', '  a,b=0,1', '  for _ in range(n): a,b=b,a+b', '  return a'), 'nth fibonacci') === true);

T('top-k full-sort FAILS (heap expected)', A(blk('def f(a,k): return sorted(a)[-k:]'), 'k largest numbers') === false);
T('top-k heap PASSES', A(blk('import heapq', 'def f(a,k): return heapq.nlargest(k,a)'), 'k largest numbers') === true);

T('N+1 per-id loop FAILS', A(blk('def load(ids):', '  return [(repo.get_user(i), repo.get_orders_for_user(i)) for i in ids]'), 'each user with orders repo.get_user') === false);
T('N+1 batched PASSES', A(blk('def load(ids):', '  return zip(repo.get_users(ids), repo.get_orders_for_users(ids))'), 'each user with orders repo.get_users') === true);

T('SQL f-string interpolation FAILS', A(blk('def find(name):', '  cur.execute(f"SELECT * FROM users WHERE name = \'{name}\'")', '  return cur.fetchall()'), 'search users by name in sql database') === false);
T('SQL parameterized PASSES', A(blk('def find(name):', '  cur.execute("SELECT * FROM users WHERE name = %s", (name,))', '  return cur.fetchall()'), 'search users by name in sql database') === true);

T('money float FAILS', A(blk('def total(items, tax, disc):', '  s=float(sum(items))', '  return s*(1+tax)*(1-disc)'), 'cart total price with tax and discount') === false);
T('money Decimal PASSES', A(blk('from decimal import Decimal', 'def total(items, tax, disc):', '  s=sum(Decimal(str(i)) for i in items)', '  return s*(1+tax)*(1-disc)'), 'cart total price with tax and discount') === true);

T('datetime naive utcnow FAILS', A(blk('from datetime import datetime', 'def expired(ts): return (datetime.utcnow()-ts).total_seconds()>86400'), 'was it more than 24 hours ago timestamp') === false);
T('datetime tz-aware PASSES', A(blk('from datetime import datetime, timezone', 'def expired(ts): return (datetime.now(timezone.utc)-ts).total_seconds()>86400'), 'was it more than 24 hours ago timestamp') === true);

console.log(f ? `\n${f} FAIL` : '\nALL RIGOR PROBE TESTS PASS');
process.exit(f ? 1 : 0);
