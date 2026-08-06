#!/usr/bin/env node
// Offline self-test for score.mjs. No database, no Docker, no credentials, no network — it feeds the
// scorer synthetic Canonical Schema Models and asserts the probes discriminate.
//
// Why this exists: a scorer nobody has tested invalidates every number it produces. A benchmark is only
// as trustworthy as the evidence that its probes fire, so this must print SELFTEST PASS before any
// headline parity figure is believed.
//
//   node evaluation/selftest.mjs
//
// Note the distinction from the *oracle* determinism gate in evaluate.sh, which extracts twice and
// requires byte-identical output. That one needs a live database; this one does not, and they check
// different things: the oracle being stable vs. the scorer being able to tell good from bad.

import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCORER = path.join(HERE, 'score.mjs');

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'dbdoc-selftest-'));
process.on('exit', () => fs.rmSync(work, { recursive: true, force: true }));

let failures = 0;

function score(truth, candidate) {
  const t = path.join(work, 'truth.json');
  const c = path.join(work, 'cand.json');
  fs.writeFileSync(t, JSON.stringify(truth));
  fs.writeFileSync(c, JSON.stringify(candidate));
  const out = execFileSync('node', [SCORER, t, c], { encoding: 'utf8' });
  const metrics = {};
  for (const line of out.split('\n')) {
    const m = line.match(/^METRIC ([a-z_0-9]+)=(.+)$/);
    if (m) metrics[m[1]] = Number(m[2]);
  }
  return metrics;
}

// SHOW_DIFF=1 writes the offending objects to stderr. Captured separately because the scoring path
// above only reads stdout.
function diffLines(truth, candidate) {
  const t = path.join(work, 'truth.json');
  const c = path.join(work, 'cand.json');
  fs.writeFileSync(t, JSON.stringify(truth));
  fs.writeFileSync(c, JSON.stringify(candidate));
  // spawnSync, not execFileSync: the latter returns stdout, and the diff is written to stderr.
  const res = spawnSync('node', [SCORER, t, c], {
    encoding: 'utf8', env: { ...process.env, SHOW_DIFF: '1' }
  });
  return String(res.stderr || '').split('\n');
}

function check(label, condition, detail) {
  if (condition) {
    console.log(`  ok    ${label}`);
  } else {
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ''}`);
    failures++;
  }
}


// A small but structurally varied oracle: two schemas, a PK, an FK, a unique constraint, a check,
// an index, a view and an enum. Enough that a scorer which silently ignores a whole class shows up.
const ORACLE = {
  meta: { engine: 'postgres' },
  tables: [
    {
      schema: 'public',
      name: 'customer',
      columns: [
        { name: 'id', type: 'bigint', nullable: false },
        { name: 'email', type: 'text', nullable: false },
        { name: 'tier', type: 'customer_tier', nullable: true }
      ],
      primary_key: { columns: ['id'] },
      unique_constraints: [{ columns: ['email'] }],
      check_constraints: [{ def: 'char_length(email) > 3' }],
      indexes: [{ name: 'customer_email_idx', columns: ['email'], unique: true }]
    },
    {
      schema: 'public',
      name: 'order',
      columns: [
        { name: 'id', type: 'bigint', nullable: false },
        { name: 'customer_id', type: 'bigint', nullable: false },
        { name: 'total_cents', type: 'integer', nullable: false }
      ],
      primary_key: { columns: ['id'] },
      foreign_keys: [
        { columns: ['customer_id'], ref_schema: 'public', ref_table: 'customer', ref_columns: ['id'] }
      ]
    },
    {
      schema: 'billing',
      name: 'invoice',
      columns: [
        { name: 'id', type: 'bigint', nullable: false },
        { name: 'order_id', type: 'bigint', nullable: false }
      ],
      primary_key: { columns: ['id'] }
    }
  ],
  enums: [{ schema: 'public', name: 'customer_tier', values: ['free', 'pro'] }]
};

// Views live inside `tables` with kind: 'view' — the collector reads `model.tables` and partitions on
// kind, so a top-level `views` array is silently ignored and scores nothing. Getting this wrong makes a
// fixture look like a scorer bug; it cost me one before I checked collect().
ORACLE.tables.push({
  schema: 'public',
  name: 'active_customer',
  kind: 'view',
  columns: [{ name: 'id', type: 'bigint', nullable: true }],
  view_definition: 'SELECT id FROM customer'
});

const clone = (o) => JSON.parse(JSON.stringify(o));

console.log('score.mjs self-test\n');

// 1. The oracle round-tripped as its own candidate must score a perfect parity. If this fails, the
//    normalizers are lossy and every real score is understated.
{
  const m = score(ORACLE, clone(ORACLE));
  check('oracle vs itself => overall_parity 1.0', m.overall_parity === 1, `got ${m.overall_parity}`);
  check('oracle vs itself => exact_parity 1', m.exact_parity === 1, `got ${m.exact_parity}`);
  check('oracle vs itself => 0 defects', m.total_defects === 0, `got ${m.total_defects}`);
  check('oracle vs itself => 0 hallucinations', m.hallucinated_objects === 0, `got ${m.hallucinated_objects}`);
  check('oracle vs itself => 0 missing', m.missing_objects === 0, `got ${m.missing_objects}`);
}

// 2. Empty documentation must score zero, not "nearly right because it invented nothing".
{
  const m = score(ORACLE, { meta: { engine: 'postgres' }, tables: [] });
  check('empty candidate => overall_parity 0.0', m.overall_parity === 0, `got ${m.overall_parity}`);
  check('empty candidate => not exact', m.exact_parity === 0, `got ${m.exact_parity}`);
  check('empty candidate => missing objects reported', m.missing_objects > 0, `got ${m.missing_objects}`);
  check('empty candidate => recall 0', m.recall_overall === 0, `got ${m.recall_overall}`);
}

// 3. Omission must be caught — the dominant real failure is incompleteness.
{
  const partial = clone(ORACLE);
  partial.tables = partial.tables.filter((t) => t.name !== 'invoice');
  const m = score(ORACLE, partial);
  check('dropped table => parity < 1', m.overall_parity < 1, `got ${m.overall_parity}`);
  check('dropped table => missing_objects > 0', m.missing_objects > 0, `got ${m.missing_objects}`);
  check('dropped table => recall < 1', m.recall_overall < 1, `got ${m.recall_overall}`);
  check('dropped table => no false hallucination', m.hallucinated_objects === 0, `got ${m.hallucinated_objects}`);
}

// 4. Invention must be caught, and separately from omission — a hallucinated table is a hard gate,
//    not something an F1 average is allowed to absorb.
{
  const invented = clone(ORACLE);
  invented.tables.push({
    schema: 'public',
    name: 'totally_made_up',
    columns: [{ name: 'id', type: 'bigint', nullable: false }],
    primary_key: { columns: ['id'] }
  });
  const m = score(ORACLE, invented);
  check('invented table => hallucinated_objects > 0', m.hallucinated_objects > 0, `got ${m.hallucinated_objects}`);
  check('invented table => precision < 1', m.precision_overall < 1, `got ${m.precision_overall}`);
  check('invented table => not exact', m.exact_parity === 0, `got ${m.exact_parity}`);
  check('invented table => recall still 1 (nothing omitted)', m.recall_overall === 1, `got ${m.recall_overall}`);
}

// 5. Sub-table objects must count. `foreign_keys` and `primary_keys` are hybrid classes — the key
//    identifies an object, the value carries an attribute — and they used to be scored as pure
//    attributes, which skipped any key the candidate never mentioned. That silently forgave omitting
//    every foreign key and every primary key in the database. These are the regression tests.
{
  const noFk = clone(ORACLE);
  for (const t of noFk.tables) delete t.foreign_keys;
  const m = score(ORACLE, noFk);
  check('dropped foreign key => parity < 1', m.overall_parity < 1, `got ${m.overall_parity}`);
  check('dropped foreign key => missing_objects > 0', m.missing_objects > 0, `got ${m.missing_objects}`);
}
{
  const noPk = clone(ORACLE);
  for (const t of noPk.tables) delete t.primary_key;
  const m = score(ORACLE, noPk);
  check('dropped primary key => parity < 1', m.overall_parity < 1, `got ${m.overall_parity}`);
  check('dropped primary key => counted as a defect', m.total_defects > 0, `got ${m.total_defects}`);
}
{
  // An FK the docs claim that the schema does not have is a false statement, not an omission.
  const inventedFk = clone(ORACLE);
  const t = inventedFk.tables.find((x) => x.name === 'customer');
  t.foreign_keys = [{ columns: ['id'], ref_schema: 'public', ref_table: 'order', ref_columns: ['id'] }];
  const m = score(ORACLE, inventedFk);
  check('invented foreign key => hallucinated_objects > 0', m.hallucinated_objects > 0, `got ${m.hallucinated_objects}`);
  check('invented foreign key => precision < 1', m.precision_overall < 1, `got ${m.precision_overall}`);
}
{
  // The guard that makes the above safe: when a whole table is undocumented, its PK and FK vanish with
  // it. Those must NOT be charged again — the missing table already accounts for them, and one error
  // should cost one defect. `order` has 3 columns, 1 PK and 1 FK; dropping it must cost 4 (itself plus
  // its columns), not 6.
  const noTable = clone(ORACLE);
  noTable.tables = noTable.tables.filter((t) => t.name !== 'order');
  const m = score(ORACLE, noTable);
  check('dropped table does not double-charge its PK and FK', m.total_defects === 4,
    `expected 4 (1 table + 3 columns), got ${m.total_defects}`);
}

// 5c. SHOW_DIFF must report what the score counted. A diff that disagrees with the score is worse than
//     no diff — you see the number move and cannot find out why.
{
  const noFk = clone(ORACLE);
  for (const t of noFk.tables) delete t.foreign_keys;
  const lines = diffLines(ORACLE, noFk);
  check('SHOW_DIFF names the omitted foreign key',
    lines.some((l) => l.includes('MISSING') && l.includes('->')),
    'no MISSING line for the dropped FK');

  // ...and stays silent about a PK/FK that vanished with its table, matching the scoring guard.
  const noTable = clone(ORACLE);
  noTable.tables = noTable.tables.filter((t) => t.name !== 'order');
  const dropped = diffLines(ORACLE, noTable);
  const classesListed = dropped.filter((l) => /^ {2}[a-z_]+: /.test(l)).map((l) => l.trim().split(':')[0]);
  check('SHOW_DIFF does not list the FK/PK of a dropped table',
    !classesListed.includes('foreign_keys') && !classesListed.includes('primary_keys'),
    `listed: ${classesListed.join(', ')}`);
}

// 5b. The control for the above: an omitted VIEW *is* caught. This is what a correctly-scored object
//     class looks like, and it is why the defect above is specific rather than systemic.
{
  const noView = clone(ORACLE);
  noView.tables = noView.tables.filter((t) => t.kind !== 'view');
  const m = score(ORACLE, noView);
  check('dropped view => parity < 1', m.overall_parity < 1, `got ${m.overall_parity}`);
  check('dropped view => missing_objects > 0', m.missing_objects > 0, `got ${m.missing_objects}`);
}

// 6. Cosmetic rendering differences must NOT be penalised, or the benchmark measures formatting
//    instead of correctness. Type aliases and identifier case are the usual offenders.
{
  const restyled = clone(ORACLE);
  restyled.tables[0].columns[0].type = 'int8';        // documented alias of bigint
  restyled.tables[0].name = 'Customer';               // case difference
  const m = score(ORACLE, restyled);
  check('type alias + case difference => still exact parity', m.exact_parity === 1,
    `got exact_parity=${m.exact_parity}, parity=${m.overall_parity} (normalizers may be too strict)`);
}

console.log('');
if (failures > 0) {
  console.log(`SELFTEST FAIL — ${failures} assertion(s) failed. Do not trust a parity number until this passes.`);
  process.exit(1);
}
console.log('SELFTEST PASS');
