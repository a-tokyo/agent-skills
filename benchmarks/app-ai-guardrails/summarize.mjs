#!/usr/bin/env node
// summarize.mjs — aggregate results/scores.tsv into per-cell medians for the published README table.
// Only rows with status === "scored" count; every other status (scored_prefix, scored_invalid_*,
// env_failure, evaluate_error) is listed separately so honest negatives and audit rows stay visible.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const tsv = fs.readFileSync(path.join(HERE, 'results/scores.tsv'), 'utf8').trim().split('\n');
const header = tsv.shift().split('\t');
const rows = tsv.map((l) => Object.fromEntries(l.split('\t').map((v, i) => [header[i], v])));

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// Ledger contract: scores.tsv is APPEND-ONLY — a run_id may appear more than once when a
// scoring was superseded (old row keeps an audit status like scored_prescorerfix). For the
// `scored` status the LAST row per run_id wins; tools must never assume raw uniqueness.
const lastScored = new Map();
for (const r of rows) if (r.status === 'scored') lastScored.set(r.run_id, r);
const cells = new Map();
const excluded = [];
for (const r of rows) {
  if (r.status !== 'scored') { excluded.push(r); continue; }
  if (lastScored.get(r.run_id) !== r) { excluded.push({ ...r, status: 'superseded_by_rescoring' }); continue; }
  const key = `${r.arm}|${r.model}|${r.stack}|${r.pm}`;
  if (!cells.has(key)) cells.set(key, []);
  cells.get(key).push({ score: Number(r.guardrail_score), gates: Number(r.all_gates_pass) });
}

console.log('cell\tn\tmedian_guardrail_score\tall_gates_pass_rate\tscores');
for (const [key, runs] of [...cells.entries()].sort()) {
  const scores = runs.map((r) => r.score);
  const gates = runs.filter((r) => r.gates === 1).length;
  console.log(`${key.replaceAll('|', '\t')}\tn${runs.length}\t${median(scores)}\t${gates}/${runs.length}\t[${scores.join(',')}]`);
}
if (excluded.length) {
  console.log(`\n# excluded rows (${excluded.length}): status breakdown`);
  const byStatus = {};
  for (const r of excluded) byStatus[r.status] = (byStatus[r.status] || 0) + 1;
  for (const [s, n] of Object.entries(byStatus)) console.log(`#   ${s}: ${n}`);
}
