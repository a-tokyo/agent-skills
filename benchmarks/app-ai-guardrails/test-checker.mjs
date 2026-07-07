#!/usr/bin/env node
// test-checker.mjs — scorer self-test. MUST pass before any scored benchmark run (ticket 10 gate).
//
// Asserts, against the two Next fixtures (.gen/next-golden, .gen/next-bare — build them first with
// fixtures/make-golden.sh next && fixtures/make-bare.sh next):
//   1. golden scores >= 95 (the golden fixture is the acceptance oracle — hand-built canon per DESIGN)
//   2. bare scores in a LOW band (< 50; actual value recorded in output for baselining)
//   3. determinism: scoring the same repo twice yields byte-identical METRIC output
//   4. teeth probes restore cleanly: `git status --porcelain` on the ORIGINAL repo is empty after scoring
//
// Exit 0 = all pass. Exit 1 = an assertion failed. Exit 3 = harness/internal error.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = path.join(HERE, '.gen', 'next-golden');
const BARE = path.join(HERE, '.gen', 'next-bare');

function die(msg, code = 3) { console.error(`FATAL: ${msg}`); process.exit(code); }

for (const [name, dir] of [['golden', GOLDEN], ['bare', BARE]]) {
  if (!fs.existsSync(dir)) die(`${name} fixture missing at ${dir} — run fixtures/make-${name === 'golden' ? 'golden' : 'bare'}.sh next first`);
}

function evaluate(repo) {
  const res = spawnSync('bash', [path.join(HERE, 'evaluate.sh'), repo], { encoding: 'utf8', timeout: 20 * 60 * 1000 });
  if (res.status !== 0 && res.status !== null) {
    console.error(res.stderr);
    die(`evaluate.sh exited ${res.status} for ${repo}`);
  }
  const metrics = res.stdout.split('\n').filter((l) => l.startsWith('METRIC '));
  const map = {};
  for (const l of metrics) {
    const m = l.match(/^METRIC (\S+)=(\S+)$/);
    if (m) map[m[1]] = Number(m[2]);
  }
  return { metricLines: metrics.join('\n'), map };
}

function gitPorcelain(dir) {
  const res = spawnSync('git', ['status', '--porcelain'], { cwd: dir, encoding: 'utf8' });
  return res.status === 0 ? res.stdout.trim() : null;
}

let failures = 0;
function assert(cond, label, detail) {
  if (cond) console.log(`PASS  ${label}${detail ? ` — ${detail}` : ''}`);
  else { console.log(`FAIL  ${label}${detail ? ` — ${detail}` : ''}`); failures++; }
}

console.log('# test-checker.mjs — scorer self-test\n');

// --- 1+3+4 golden: score, determinism, clean restore ---
console.log('## golden (run 1)');
const g1 = evaluate(GOLDEN);
console.log('## golden (run 2, determinism check)');
const g2 = evaluate(GOLDEN);

assert(g1.map.guardrail_score >= 95, 'golden guardrail_score >= 95', `actual=${g1.map.guardrail_score}`);
assert(g1.map.all_gates_pass === 1, 'golden all_gates_pass == 1', `actual=${g1.map.all_gates_pass}`);
assert(g1.metricLines === g2.metricLines, 'golden scored twice -> byte-identical METRIC output');
const gPorcelain = gitPorcelain(GOLDEN);
assert(gPorcelain === '', 'golden repo unchanged after scoring (git status --porcelain empty)', gPorcelain === '' ? '' : `dirty: ${gPorcelain.split('\n').length} entries`);

// --- 2+3+4 bare: low band, determinism, clean restore ---
console.log('\n## bare (run 1)');
const b1 = evaluate(BARE);
console.log('## bare (run 2, determinism check)');
const b2 = evaluate(BARE);

assert(b1.map.guardrail_score < 50, 'bare guardrail_score in low band (< 50)', `actual=${b1.map.guardrail_score}`);
assert(b1.map.all_gates_pass === 0, 'bare all_gates_pass == 0', `actual=${b1.map.all_gates_pass}`);
assert(b1.metricLines === b2.metricLines, 'bare scored twice -> byte-identical METRIC output');
const bPorcelain = gitPorcelain(BARE);
assert(bPorcelain === '', 'bare repo unchanged after scoring (git status --porcelain empty)', bPorcelain === '' ? '' : `dirty: ${bPorcelain.split('\n').length} entries`);

// --- separation sanity ---
assert(g1.map.guardrail_score - b1.map.guardrail_score >= 40, 'golden/bare separation >= 40 points', `golden=${g1.map.guardrail_score} bare=${b1.map.guardrail_score}`);

console.log(`\n# recorded bands: golden=${g1.map.guardrail_score} bare=${b1.map.guardrail_score}`);
console.log(failures === 0 ? '\nALL CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
