#!/usr/bin/env node
// Parity scorer for the database-docs skill build.
// Compares a candidate Canonical Schema Model (the skill's schema.json) against the ground-truth CSM
// (from the deterministic extractor) and emits METRIC lines. Structural parity is scored against the
// oracle -- NOT against an LLM's opinion. Every CSM field the oracle emits is routed into a scored class,
// so exact_parity=1 means "the docs are provably the schema". Headline metric is balanced F1 (beta=1, the
// dominant failure is incompleteness); hallucinations are separately hard-gated via hallucinated_objects==0.
//
// Usage: node score.mjs <truth.json> <candidate.json>

import fs from 'node:fs';

const [truthPath, candPath] = process.argv.slice(2);
if (!truthPath || !candPath) { console.error('usage: score.mjs <truth.json> <candidate.json>'); process.exit(2); }
const truth = JSON.parse(fs.readFileSync(truthPath, 'utf8'));
const cand = JSON.parse(fs.readFileSync(candPath, 'utf8'));
const ENGINE = (truth.meta && truth.meta.engine) || 'postgres';

// ---------- normalizers (the crux of cross-rendering fairness) ----------
function normType(t) {
  if (t == null) return '';
  let s = String(t).toLowerCase().trim().replace(/\s+/g, ' ');
  // alias collapse (preserve length/precision in parens)
  s = s.replace(/\bcharacter varying\b/g, 'varchar')
       .replace(/\bcharacter\b/g, 'char')
       .replace(/\btimestamp without time zone\b/g, 'timestamp')
       .replace(/\btimestamp with time zone\b/g, 'timestamptz')
       .replace(/\btime without time zone\b/g, 'time')
       .replace(/\btime with time zone\b/g, 'timetz')
       .replace(/\bdouble precision\b/g, 'float8')
       .replace(/\bboolean\b/g, 'bool')
       .replace(/\binteger\b/g, 'int4').replace(/\bint\b/g, 'int4')
       .replace(/\bbigint\b/g, 'int8').replace(/\bsmallint\b/g, 'int2')
       .replace(/\bnumeric\b/g, 'decimal')
       .replace(/\[\]$/, ' array');
  s = s.replace(/\s+/g, '');
  // SQL Server: a missing fractional-seconds scale means the default (7). Canonicalize so
  // `datetimeoffset` == `datetimeoffset(7)` etc., but a non-default scale still differs.
  if (ENGINE === 'mssql') s = s.replace(/^(datetimeoffset|datetime2|time)$/, '$1(7)');
  return s;
}
function stripWrapParens(s) { // MSSQL wraps defaults: ((1)) -> 1, (newid()) -> newid()
  let t = s.trim();
  while (t.startsWith('(') && t.endsWith(')')) {
    let depth = 0, wraps = true;
    for (let i = 0; i < t.length; i++) { if (t[i] === '(') depth++; else if (t[i] === ')') { depth--; if (depth === 0 && i < t.length - 1) { wraps = false; break; } } }
    if (wraps) t = t.slice(1, -1).trim(); else break;
  }
  return t;
}
function normDefault(d) {
  if (d == null) return '';
  let s = stripWrapParens(String(d).toLowerCase().trim());
  s = s.replace(/::[a-z0-9_ ."\[\]]+/g, '');          // strip ::type casts (pg)
  s = s.replace(/\bcurrent_timestamp\b/g, 'getdate()').replace(/\bnow\(\)/g, 'getdate()'); // equate now/current_timestamp/getdate
  s = s.replace(/^nextval\(.*\)$/, 'nextval()');       // sequence defaults differ only by name
  s = s.replace(/['"\s]/g, '');
  return s;
}
// normalize a constraint/check expression: drop the optional leading CHECK keyword, whitespace, quotes,
// casts, and outer wrapping parens, so `CHECK ([x]='a')` == `([x]='a')` == `[x]='a'`.
function normExpr(e) {
  if (e == null) return '';
  let s = String(e).toLowerCase().replace(/\s+/g, '').replace(/['"]/g, '').replace(/::[a-z0-9_]+/g, '');
  s = s.replace(/^check/, '');
  s = stripWrapParens(s);
  return s;
}
// Identifier matching is case-insensitive (MSSQL identifiers are CI; PG arms use the catalog's exact case
// anyway) but UNDERSCORE-SENSITIVE — so a doc using the wrong physical name (`client_id` for a `clientId`
// column) is correctly flagged, and two genuinely-distinct identifiers (`action_plan` vs `actionPlan`)
// never collide into one key.
const normId = (s) => String(s).toLowerCase();
const colset = (arr) => (arr || []).map(normId).join(',');
// uniqueness is set-based: (a,b) UNIQUE == (b,a) UNIQUE. Use order-insensitive colset for unique
// constraints (and the backing-index equivalence check). Indexes/PKs/FKs stay order-sensitive.
const sortedColset = (arr) => (arr || []).map(normId).sort().join(',');
// Split a parenthesised column/expression list on TOP-LEVEL commas (so `coalesce(a, b)` stays intact).
function splitTopLevel(s) {
  const out = []; let depth = 0, cur = '';
  for (const ch of s) {
    if (ch === '(') depth++; else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { out.push(cur); cur = ''; } else cur += ch;
  }
  if (cur.trim()) out.push(cur);
  return out;
}
function normIndexItem(item) {
  let s = item.trim()
    .replace(/\s+(ASC|DESC)\b/gi, '')
    .replace(/\s+NULLS\s+(FIRST|LAST)\b/gi, '')
    .replace(/\s+\w+_ops\b/gi, '');                 // strip operator class (gin_trgm_ops, text_pattern_ops…)
  s = s.replace(/"([^"]*)"/g, '$1');                // drop identifier quotes
  return s.toLowerCase().replace(/\s+/g, '');       // collapse whitespace inside expressions
}
// Canonical index column/expression list. Parse the CREATE INDEX def (both oracle and candidate carry it,
// from pg_get_indexdef) so expression/partial indexes compare on identical footing; fall back to columns[].
function indexCols(ix) {
  const def = ix.def || '';
  const m = def.match(/USING\s+\w+\s*\(/i);
  if (m) {
    const start = m.index + m[0].length - 1; // at the '('
    let depth = 0, end = -1;
    for (let i = start; i < def.length; i++) { if (def[i] === '(') depth++; else if (def[i] === ')') { depth--; if (depth === 0) { end = i; break; } } }
    if (end > start) return splitTopLevel(def.slice(start + 1, end)).map(normIndexItem).filter(Boolean);
  }
  return (ix.columns || []).map((c) => normIndexItem(String(c)));
}
const tkey = (s, n) => `${normId(s || '')}.${normId(n)}`;
// body normalizer (trigger/view defs): strip SQL comments (a leading `-- desc` differs cosmetically only),
// then lowercase + collapse whitespace. Catches a wrong body without flagging comment/format differences.
const normDef = (d) => (d == null ? '' : String(d).replace(/--[^\n]*/g, ' ').replace(/\/\*[\s\S]*?\*\//g, ' ')
  .toLowerCase().replace(/\s+/g, ' ').trim());
const fkAction = (a) => (a || 'NO ACTION').toUpperCase().replace(/[\s_]/g, ''); // NO ACTION==NO_ACTION==NOACTION

// ---------- build keyed object lists for each class ----------
function collect(model) {
  const tables = model.tables || [];
  const kindOf = (t) => String(t.kind || 'table').toLowerCase();
  const rel = tables.filter((t) => ['table', 'partitioned', 'foreign'].includes(kindOf(t)));
  const views = tables.filter((t) => ['view', 'materialized_view'].includes(kindOf(t)));
  const C = {
    tables: new Map(), columns: new Map(), column_types: new Map(), column_nullability: new Map(),
    column_defaults: new Map(), column_comments: new Map(), column_is_identity: new Map(),
    column_is_generated: new Map(), primary_keys: new Map(),
    foreign_keys: new Map(), fk_on_delete: new Map(), fk_on_update: new Map(), unique_constraints: new Map(),
    check_constraints: new Map(), indexes: new Map(), enums: new Map(), enum_values: new Map(),
    domains: new Map(), domain_base: new Map(), views: new Map(), view_defs: new Map(),
    triggers: new Map(), trigger_defs: new Map(), routines: new Map(), sequences: new Map(),
  };
  for (const t of rel) {
    const tk = tkey(t.schema, t.name);
    C.tables.set(tk, true);
    if (t.primary_key) C.primary_keys.set(tk, colset(t.primary_key.columns));
    for (const c of t.columns || []) {
      const ck = `${tk}/${normId(c.name)}`;
      C.columns.set(ck, true);
      C.column_types.set(ck, normType(c.type));
      C.column_nullability.set(ck, !!c.nullable);
      C.column_defaults.set(ck, normDefault(c.default));
      C.column_is_identity.set(ck, !!c.is_identity);
      C.column_is_generated.set(ck, !!c.is_generated);
      if (c.comment) C.column_comments.set(ck, String(c.comment).trim());
    }
    for (const fk of t.foreign_keys || []) {
      const k = `${tk}/(${colset(fk.columns)})->${normId(fk.ref_table || '')}`;
      C.foreign_keys.set(k, colset(fk.ref_columns));
      C.fk_on_delete.set(k, fkAction(fk.on_delete));
      C.fk_on_update.set(k, fkAction(fk.on_update));
    }
    // colsets covered by a PK or unique constraint -> a unique index over the same cols is just its backing
    // index (same schema fact), so it must not count as a separate/extra index on either side.
    const constraintColsets = new Set(); // order-insensitive, for backing-index equivalence
    if (t.primary_key) constraintColsets.add(sortedColset(t.primary_key.columns));
    for (const u of t.unique_constraints || []) {
      C.unique_constraints.set(`${tk}/(${sortedColset(u.columns)})`, true);
      constraintColsets.add(sortedColset(u.columns));
    }
    for (const ch of t.check_constraints || []) C.check_constraints.set(`${tk}/${normExpr(ch.def || ch.expression)}`, true);
    for (const ix of t.indexes || []) {
      const cols = indexCols(ix);
      if (ix.unique && constraintColsets.has(sortedColset(cols))) continue; // backing index of a PK/unique constraint
      // key carries method + partial-predicate so a full index != a partial index over the same cols,
      // and a btree != gin over the same cols.
      const method = String(ix.method || 'btree').toLowerCase();
      C.indexes.set(`${tk}/(${colset(cols)})/${ix.unique ? 'u' : 'n'}/${method}/${normExpr(ix.where)}`, true);
    }
    for (const tg of t.triggers || []) {
      const trk = `${tk}/${normId(tg.name)}`;
      C.triggers.set(trk, true);
      C.trigger_defs.set(trk, normDef(tg.def));
    }
  }
  for (const v of views) {
    const vk = tkey(v.schema, v.name);
    C.views.set(vk, true);
    C.view_defs.set(vk, normDef(v.view_definition));
  }
  for (const e of model.enums || []) {
    const ek = tkey(e.schema, e.name); C.enums.set(ek, true);
    // include ordinal so value ORDER is part of the identity (PG enum sort order is semantic)
    (e.values || []).forEach((val, i) => C.enum_values.set(`${ek}/${i}/${String(val).toLowerCase()}`, true));
  }
  for (const d of model.domains || []) {
    const dk = tkey(d.schema, d.name); C.domains.set(dk, true);
    C.domain_base.set(dk, normType(d.base_type));
  }
  for (const r of model.routines || []) {
    // include normalized arguments so overloads f(int) vs f(int,text) are distinct objects
    C.routines.set(`${tkey(r.schema, r.name)}(${normId(r.arguments || '')})`, (r.kind || '').toLowerCase());
  }
  for (const s of model.sequences || []) C.sequences.set(tkey(s.schema, s.name), true);
  return C;
}

// ---------- scoring ----------
// presence classes: TP if key in both. attribute classes: among keys-in-both, TP if value matches.
const ATTR_CLASSES = new Set(['column_types', 'column_nullability', 'column_defaults', 'column_comments',
  'column_is_identity', 'column_is_generated', 'primary_keys', 'foreign_keys',
  'fk_on_delete', 'fk_on_update', 'routines', 'domain_base', 'view_defs', 'trigger_defs']);
const WEIGHTS = {
  tables: 1, columns: 1, column_types: 1, primary_keys: 1, foreign_keys: 1,
  fk_on_delete: 0.6, fk_on_update: 0.6, column_nullability: 0.6, enums: 0.6, enum_values: 0.6,
  unique_constraints: 0.6, check_constraints: 0.6, indexes: 0.6, domains: 0.6,
  column_defaults: 0.3, views: 0.3, view_defs: 0.3, triggers: 0.3, trigger_defs: 0.3, routines: 0.3,
  sequences: 0.3, domain_base: 0.3, column_is_identity: 0.3, column_is_generated: 0.3,
  column_comments: 0.1,
};
// classes counting toward hallucinated/missing OBJECT tallies (presence of a real object)
const OBJECT_CLASSES = new Set(['tables', 'columns', 'foreign_keys', 'enums', 'indexes',
  'unique_constraints', 'check_constraints', 'views', 'triggers', 'routines', 'sequences', 'domains']);
// Goal is 100% parity and the dominant failure is INCOMPLETENESS, so use balanced F1 (beta=1) -- it only
// approaches 1.0 when BOTH recall and precision do. Hallucinations are handled separately by the hard
// `hallucinated_objects==0` ship gate, so the F-metric is free to be recall-sensitive. Override via PARITY_BETA.
const BETA = Number(process.env.PARITY_BETA || '1');
const BETA2 = BETA * BETA;

const T = collect(truth), G = collect(cand);
const classes = Object.keys(WEIGHTS);
let hallucinated = 0, missing = 0, wF = 0, wSum = 0, gTP = 0, gFP = 0, gFN = 0;
const perClass = {};

for (const cls of classes) {
  const tm = T[cls], gm = G[cls];
  const nTruth = tm.size;
  let tp = 0, fp = 0, fn = 0;
  if (ATTR_CLASSES.has(cls)) {
    // score only over keys present in both (presence handled by parent class); value must match
    for (const [k, v] of tm) { if (gm.has(k)) { if (gm.get(k) === v) tp++; else { fp++; fn++; } } }
    // no overlap => the parent objects weren't documented; omission already counted by presence
    // classes. Drop this conditional class from the rollup rather than scoring it 1.0 vacuously.
    if (tp + fp === 0) { perClass[cls] = { f: null, tp, fp, fn, n: nTruth }; continue; }
  } else {
    for (const k of tm.keys()) { if (gm.has(k)) tp++; else fn++; }
    for (const k of gm.keys()) { if (!tm.has(k)) fp++; }
  }
  if (nTruth === 0 && gm.size === 0) { perClass[cls] = { f: null, tp, fp, fn, n: 0 }; continue; }
  const P = tp + fp === 0 ? 1 : tp / (tp + fp);
  const R = tp + fn === 0 ? 1 : tp / (tp + fn);
  const F = P + R === 0 ? 0 : ((1 + BETA2) * P * R) / (BETA2 * P + R);
  perClass[cls] = { f: F, tp, fp, fn, n: nTruth, P, R };
  wF += WEIGHTS[cls] * F; wSum += WEIGHTS[cls];
  gTP += tp; gFP += fp; gFN += fn;
  if (OBJECT_CLASSES.has(cls)) { hallucinated += fp; missing += fn; }
}

const overall = wSum ? wF / wSum : 0;
const precision = gTP + gFP === 0 ? 1 : gTP / (gTP + gFP);
const recall = gTP + gFN === 0 ? 1 : gTP / (gTP + gFN);
const f = (x) => (x == null ? 'NA' : x.toFixed(4));
// The north star is EXACT 100% parity. total_defects = every disagreement across all classes (missing +
// hallucinated objects + attribute mismatches). exact_parity is the binary ship signal: 1 only when the
// docs are provably the schema (0 defects). The benchmark proves bare models do NOT reach exact_parity=1.
const total_defects = gFP + gFN;
const exact = total_defects === 0 ? 1 : 0;

console.log(`METRIC overall_parity=${f(overall)}`);
console.log(`METRIC exact_parity=${exact}`);
console.log(`METRIC total_defects=${total_defects}`);
console.log(`METRIC precision_overall=${f(precision)}`);
console.log(`METRIC recall_overall=${f(recall)}`);
console.log(`METRIC hallucinated_objects=${hallucinated}`);
console.log(`METRIC missing_objects=${missing}`);
for (const cls of classes) console.log(`METRIC f_${cls}=${f(perClass[cls].f)}`);

// SHOW_DIFF=1 dumps the exact offending objects per class (to stderr) so every defect can be driven to 0.
if (process.env.SHOW_DIFF === '1') {
  console.error('\n# DEFECT DIFF (truth\\candidate = missing/FN ; candidate\\truth = hallucinated/FP):');
  for (const cls of classes) {
    const tm = T[cls], gm = G[cls];
    const fn = [], fp = [];
    if (ATTR_CLASSES.has(cls)) {
      for (const [k, v] of tm) if (gm.has(k) && gm.get(k) !== v) fn.push(`${k} [truth=${v} cand=${gm.get(k)}]`);
    } else {
      for (const k of tm.keys()) if (!gm.has(k)) fn.push(k);
      for (const k of gm.keys()) if (!tm.has(k)) fp.push(k);
    }
    if (fn.length || fp.length) {
      console.error(`  ${cls}: ${fn.length} missing, ${fp.length} hallucinated`);
      for (const k of fn.slice(0, 25)) console.error(`    - MISSING:     ${k}`);
      for (const k of fp.slice(0, 25)) console.error(`    + HALLUCINATED: ${k}`);
    }
  }
}
// human-readable detail to stderr (not parsed)
console.error('\n# per-class detail (tp/fp/fn, n=truth):');
for (const cls of classes) {
  const c = perClass[cls];
  console.error(`  ${cls.padEnd(20)} f=${f(c.f)}  tp=${c.tp} fp=${c.fp} fn=${c.fn}  n=${c.n}`);
}
